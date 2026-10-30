/*
 * BrightMoon Monitor - Solo Island ESP32-S3 mirror.
 *
 * Built for the Waveshare ESP32-S3-Touch-LCD-3.49 V2 (172x640 panel, driven as
 * a 640x172 landscape LVGL canvas via the QSPI rotation in lvgl_port.c).
 * The device renders the desktop Dynamic Island; the desktop resolves every
 * visual decision and publishes a compact JSON state which this firmware draws.
 *
 * Two transports carry that state:
 *   - USB serial (preferred when a desktop bridge is attached): line protocol
 *     "SOLO1 ..." + on-screen approval buttons that send CMD frames back.
 *   - WiFi HTTP(S): unchanged polling of /api/esp32/v1/state (works headless).
 * A fresh USB state always wins; the WiFi poller only runs when USB is stale,
 * so a USB-only device needs no network configuration at all.
 *
 * Boot order and rationale (see also config_portal.h / net_client.h /
 * serial_client.h / imu_bsp.h):
 *   1. Serial at 115200, then serial_client_begin() (sends the first READY).
 *   2. Verified vendor BSP bring-up order starts with the shared I2C bus.
 *   3. config_bootstrap(): optional read-only SD provisioning
 *      (/solo-island/config.json + ca.pem) overrides NVS; NVS is the fallback.
 *   4. If there is no network configuration, probe USB for a desktop bridge
 *      (serial_client_boot_handshake, 4 s). A desktop answer means USB-only
 *      boot; otherwise the SoftAP provisioning portal opens.
 *   5. Display bring-up (QSPI panel + touch + LVGL + mirror UI) - always
 *      completes before any network wait, so a dead network can never delay or
 *      prevent the first frame.
 *   6. IMU: QMI8658 orientation, optional; flipping the board 180 degrees flips
 *      the landscape frame (BOOT ~2 s toggles a manual 180-degree flip).
 *   7. net_begin() only when a WiFi configuration exists (non-blocking STA).
 *   8. loop(): serial_client_poll() + UI/toast, then either the fresh USB state
 *      or the WiFi poller; UI refreshed on rev/transport change or every 2 s
 *      under the LVGL mutex.
 *
 * The 20 s boot-time STA window is evaluated from loop(). It only falls back to
 * the provisioning portal when the USB transport is not fresh as well, so a
 * USB-connected board never gets hijacked into AP mode.
 */
#include <Arduino.h>
#include "user_config.h"
#include "i2c_bsp.h"
#include "lvgl_port.h"
#include "island_ui.h"
#include "island_model.h"
#include "config_portal.h"
#include "net_client.h"
#include "serial_client.h"
#include "imu_bsp.h"
#include "power_control.h"
#include "src/lcd_bl_bsp/lcd_bl_pwm_bsp.h"

/* Snapshot passed to the UI. File scope (~6 KB) instead of the loop-task
 * stack, which is only 8 KB and also hosts the HTTPClient request. */
static IslandState s_ui_state;

/* Boot-time decision: is a WiFi configuration available at all? */
static bool s_have_config = false;

void setup()
{
  /* A STATE line is 1-2 KB; the Espressif USB-CDC default RX queue is only
   * 256 bytes, so a single STATE write can overflow it while LVGL is busy and
   * the line is dropped (screen keeps "等待电脑").  Must be set before begin(). */
  Serial.setRxBufferSize(8192);
  Serial.begin(115200);
  delay(100);

  /* USB serial transport first: transmits SOLO1 READY v1. */
  serial_client_begin();

  Serial.println();
  Serial.println("[boot] BrightMoon Solo Island (ESP32-S3, landscape 640x172)");

  /* Verified vendor bring-up order: shared I2C bus first. */
  i2c_master_Init();
  lvgl_port_power_init();
  lvgl_port_init();
  lcd_bl_pwm_bsp_init(LCD_PWM_MODE_255);
  power_control_begin();

  /* Configuration: optional read-only SD card provisioning overrides NVS. */
  s_have_config = config_bootstrap();
  Serial.printf("[boot] config source=%s configured=%d\n", config_from_sd() ? "SD" : "NVS", s_have_config);

  /* USB-only preference: set when the desktop said HELLO while unconfigured.
   * Later boots skip the portal and wait for the USB bridge directly. */
  const bool usb_preferred = config_usb_preferred();

  /* No WiFi configuration? Try to boot as a USB-only display. */
  bool usb_boot = false;
  if (!s_have_config && !usb_preferred) {
    usb_boot = serial_client_boot_handshake(4000);
    Serial.printf("[boot] USB-only boot: %s\n", usb_boot ? "yes" : "no");
  } else if (usb_preferred) {
    Serial.println("[boot] USB-only preference remembered (hold BOOT to reconfigure WiFi)");
  }

  /* Provisioning portal: only when neither WiFi config nor a USB desktop is
   * available, or when the user holds BOOT for 3 s to force re-provisioning. */
  if ((!s_have_config && !usb_boot && !usb_preferred) || portal_boot_button_held()) {
    Serial.println("[boot] no transport configured -> configuration portal");
    portal_start(); /* SoftAP + WebServer, never returns */
  }


  /* Optional orientation sensor; the mirror still works when it is absent. */
  if (imu_begin()) {
    imu_calibrate();
  } else {
    Serial.println("[imu] orientation disabled (QMI8658 not available)");
  }

  /* WiFi is only started when a configuration exists; USB-only boot skips it. */
  if (s_have_config) {
    net_begin();
  } else {
    Serial.println("[boot] USB-only mode (WiFi disabled until configured)");
  }

  Serial.printf("[boot] free heap=%u free psram=%u\n", static_cast<unsigned>(ESP.getFreeHeap()),
                static_cast<unsigned>(ESP.getFreePsram()));
}

void loop()
{
  if (power_control_shutting_down()) { delay(30); return; }
  /* USB serial: RX frames, READY heartbeat, queued CMD flush. */
  serial_client_poll();
  s_have_config=config_configured();
  static IslandReply control_reply;
  if (lvgl_port_lock(20)) {
    while(serial_client_take_reply(&control_reply)) island_ui_control_reply(&control_reply);
    lvgl_port_unlock();
  }

  /* Approval/result toast from the desktop (one-shot). */
  char result_message[ISLAND_TEXT_MAX];
  bool result_ok = false;
  if (serial_client_take_result(result_message, sizeof(result_message), &result_ok)) {
    if (lvgl_port_lock(100)) {
      island_ui_show_result(result_message, result_ok);
      lvgl_port_unlock();
    }
    Serial.printf("[ui] action result: %s\n", result_ok ? "ok" : "failed");
  }

  const bool usb_fresh = serial_client_fresh(5000);

  /* WiFi fallback: only poll the desktop over HTTP when USB is not delivering
   * state. A USB-connected board is never interrupted by the WiFi state. */
  if (s_have_config && !usb_fresh) {
    net_poll();
    /* First-connect WiFi failure opens the portal only when USB is dead too. */
    if (net_portal_requested() && !serial_client_fresh(5000)) {
      portal_start(); /* never returns */
    }
  }

  /* IMU orientation: debounced inside imu_poll(); flip the landscape frame for
   * upside-down mounting and log only real changes. */
  imu_poll();
  static ImuOrientation s_last_orientation = IMU_UNKNOWN;
  const ImuOrientation orientation = imu_orientation();
  if (orientation != s_last_orientation) {
    s_last_orientation = orientation;
    if (lvgl_port_lock(50)) {
      lvgl_port_set_flip(orientation == IMU_LANDSCAPE_FLIPPED);
      lvgl_port_unlock();
    }
    Serial.printf("[imu] %s\n", imu_orientation_name(orientation));
  }

  /* Pick the winning transport and its state/rev/link metadata. */
  bool got_state = false;
  bool online = false;
  int32_t age_s = -1;
  uint32_t rev = 0;
  if (usb_fresh) {
    rev = serial_client_rev();
    online = true;
    age_s = serial_client_age_seconds();
    got_state = serial_client_copy_state(&s_ui_state);
  } else if (s_have_config) {
    rev = net_rev();
    online = net_online();
    age_s = net_last_sync_seconds();
    got_state = net_copy_state(&s_ui_state);
  } else {
    online = false;
    age_s = serial_client_age_seconds();
  }

  static uint32_t s_last_rev = 0;
  static bool s_have_applied = false;
  static bool s_last_usb = false;
  static uint32_t s_last_ui_ms = 0;

  const uint32_t now = millis();
  const bool transport_changed = (usb_fresh != s_last_usb);
  const bool changed = (!s_have_applied || rev != s_last_rev || transport_changed);

  if (changed || (now - s_last_ui_ms) >= 2000) {
    if (lvgl_port_lock(100)) {
      /* Link line: USB when live, "USB 等待" on a stale USB-only boot, and the
       * normal WiFi status text otherwise (NULL restores it). */
      if (usb_fresh) {
        island_ui_set_link_text("USB 已连接");
      } else if (!s_have_config) {
        island_ui_set_link_text("USB 等待");
      } else {
        island_ui_set_link_text(net_online()?"Wi-Fi 已连接":net_status_text());
      }
      if (got_state) {
        island_ui_apply(&s_ui_state);
        island_ui_set_link(online, age_s);
      } else {
        island_ui_set_link(false, age_s);
      }
      lvgl_port_unlock();
    }
    if (got_state) {
      s_last_rev = rev;
      s_have_applied = true;
    }
    if (transport_changed) {
      Serial.printf("[link] %s\n", usb_fresh ? "USB" : (s_have_config ? "WiFi" : "USB 等待"));
    }
    s_last_usb = usb_fresh;
    s_last_ui_ms = now;
  }

  delay(30);
}
