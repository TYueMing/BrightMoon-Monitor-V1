#include <Arduino.h>
#include <atomic>
#include "esp_sleep.h"
#include "driver/rtc_io.h"
#include "user_config.h"
#include "lvgl_port.h"
#include "island_ui.h"
#include "power_hold.h"
#include "power_control.h"

static std::atomic<bool> s_shutdown{false};

static void power_task(void *) {
  PowerHold button;
  bool requested = false;
  uint32_t released_at = 0;
  bool released = false;
  for (;;) {
    const uint32_t now = millis();
    const bool pressed = digitalRead(EXAMPLE_PIN_NUM_SYS_OUT) == LOW;
    if (button.update(pressed, now)) requested = true;
    // Network timeouts and provisioning must not block the physical key.
    if (lvgl_port_lock(20)) {
      if (requested) { island_ui_show_power_dialog(); requested = false; }
      if (island_ui_take_power_shutdown()) s_shutdown.store(true);
      lvgl_port_unlock();
    }
    if (s_shutdown.load()) {
      if (pressed) released = false;
      else if (!released) { released = true; released_at = now; }
      if (released && now - released_at >= 100) {
        // Never arm a low-level wakeup while the user is still holding PWR.
        if (lvgl_port_lock(200)) {
          lvgl_port_power_off();
          lvgl_port_unlock();
          rtc_gpio_pullup_en(EXAMPLE_PIN_NUM_SYS_OUT);
          rtc_gpio_pulldown_dis(EXAMPLE_PIN_NUM_SYS_OUT);
          esp_sleep_enable_ext0_wakeup(EXAMPLE_PIN_NUM_SYS_OUT, 0);
          esp_deep_sleep_start(); // USB cannot be electrically switched off.
        }
      }
    }
    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

void power_control_begin() {
  pinMode(EXAMPLE_PIN_NUM_SYS_OUT, INPUT_PULLUP);
  if (xTaskCreate(power_task, "island-power", 4096, nullptr, 2, nullptr) != pdPASS)
    Serial.println("[power] unable to start key monitor");
}

bool power_control_shutting_down() { return s_shutdown.load(); }
