/*
 * QMI8658 accelerometer orientation helper.
 *
 * Register sequence (per the QMI8658 datasheet, as used by the Waveshare demo
 * and lewisxhe/SensorLib - see THIRD_PARTY_NOTICES.md):
 *   0x00 WHO_AM_I      == 0x05
 *   0x02 CTRL1         bit6 = address auto-increment (needed for the 6-byte
 *                      accel burst starting at 0x35)
 *   0x08 CTRL7         bit0 = accelerometer enable (clear while configuring)
 *   0x03 CTRL2         0x15 = +/-4 g, 250 Hz
 *   0x06 CTRL5         0x00 = low-pass filter off
 *   0x35..0x3A         AX_L/AX_H/AY_L/AY_H/AZ_L/AZ_H, little-endian int16
 *
 * The sensor axes relative to the board are not documented/needed: the
 * orientation logic is baseline-relative (see imu_bsp.h).
 */
#include <Arduino.h>
#include <math.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

#include "i2c_bsp.h"
#include "imu_bsp.h"

namespace {

/* QMI8658 register map (subset). */
constexpr uint8_t REG_WHO_AM_I = 0x00;
constexpr uint8_t REG_CTRL1 = 0x02;
constexpr uint8_t REG_CTRL2 = 0x03;
constexpr uint8_t REG_CTRL5 = 0x06;
constexpr uint8_t REG_CTRL7 = 0x08;
constexpr uint8_t REG_AX_L = 0x35;
constexpr uint8_t WHO_AM_I_VALUE = 0x05;

constexpr uint8_t CTRL1_AUTO_INC = 0x40; /* bit6 */
constexpr uint8_t CTRL7_ACC_EN = 0x01;   /* bit0 */
constexpr uint8_t CTRL2_4G_250HZ = 0x15;

/* 4 g full scale over a signed 16-bit range. */
constexpr float ACCEL_SCALE_G = 4.0f / 32768.0f;

constexpr uint32_t SAMPLE_INTERVAL_MS = 20;
constexpr uint32_t DEBOUNCE_MS = 400;
constexpr uint32_t BOOT_RECALIBRATE_MS = 2000;

/* Run direction thresholds against gravity (cosine of the angle to the
 * calibrated baseline), with a hysteresis band kept from the current state. */
constexpr float DOT_UP = 0.75f;
constexpr float DOT_DOWN = -0.75f;
constexpr float DOT_FLAT = 0.55f;

constexpr int CALIBRATION_SAMPLES = 24;
constexpr uint32_t CALIBRATION_GAP_MS = 10;

bool s_ready = false;
ImuOrientation s_orientation = IMU_UNKNOWN;
float s_baseline[3] = {0.0f, 0.0f, 1.0f};
bool s_baseline_set = false;
float s_filtered[3] = {0.0f, 0.0f, 0.0f};
bool s_filter_init = false;
uint32_t s_last_read_ms = 0;
ImuOrientation s_candidate = IMU_UNKNOWN;
uint32_t s_candidate_since = 0;
bool s_boot_prev = false;
uint32_t s_boot_since = 0;
bool s_boot_recal_armed = false;
bool s_manual_flip = false;
uint32_t s_i2c_errors = 0;

bool reg_write(uint8_t reg, uint8_t value) {
  return i2c_write_buff(imu_dev_handle, static_cast<int>(reg), &value, 1) == 0;
}

bool reg_read(uint8_t reg, uint8_t *value) {
  return i2c_read_buff(imu_dev_handle, static_cast<int>(reg), value, 1) == 0;
}

/* Read the 6-byte accel burst (auto-increment set in CTRL1). */
bool read_accel_g(float g[3]) {
  uint8_t raw[6] = {0};
  if (i2c_read_buff(imu_dev_handle, REG_AX_L, raw, sizeof(raw)) != 0) {
    ++s_i2c_errors;
    if ((s_i2c_errors % 50u) == 1u) {
      Serial.printf("[imu] I2C read failed (%u)\n", static_cast<unsigned>(s_i2c_errors));
    }
    return false;
  }
  s_i2c_errors = 0;
  const auto le16 = [](const uint8_t *p) -> int16_t {
    return static_cast<int16_t>(static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8));
  };
  g[0] = static_cast<float>(le16(&raw[0])) * ACCEL_SCALE_G;
  g[1] = static_cast<float>(le16(&raw[2])) * ACCEL_SCALE_G;
  g[2] = static_cast<float>(le16(&raw[4])) * ACCEL_SCALE_G;
  return true;
}

void update_boot_button(uint32_t now) {
  const bool down = digitalRead(0) == LOW;
  if (down && !s_boot_prev) {
    s_boot_since = now;
    s_boot_recal_armed = false;
  }
  if (down && !s_boot_recal_armed && (now - s_boot_since) >= BOOT_RECALIBRATE_MS) {
    s_boot_recal_armed = true;
    /* The absolute "up" direction of an axis-unknown IMU cannot be known, so
     * BOOT lets the user invert the landscape interpretation by 180 degrees.
     * The baseline is still captured at boot; reboot after remounting. */
    s_manual_flip = !s_manual_flip;
    Serial.printf("[imu] BOOT long-press -> manual flip=%d\n", s_manual_flip ? 1 : 0);
  }
  if (!down) {
    s_boot_recal_armed = false;
  }
  s_boot_prev = down;
}

}  // namespace

bool imu_begin(void) {
  s_ready = false;
  s_orientation = IMU_UNKNOWN;
  s_candidate = IMU_UNKNOWN;
  s_baseline_set = false;
  s_filter_init = false;
  s_i2c_errors = 0;
  s_boot_prev = false;
  s_boot_recal_armed = false;
  s_manual_flip = false;

  if (imu_dev_handle == nullptr) {
    Serial.println("[imu] no I2C device handle");
    return false;
  }

  uint8_t who_am_i = 0;
  if (!reg_read(REG_WHO_AM_I, &who_am_i) || who_am_i != WHO_AM_I_VALUE) {
    Serial.printf("[imu] QMI8658 not found (WHO_AM_I=0x%02X, expected 0x%02X)\n", who_am_i, WHO_AM_I_VALUE);
    return false;
  }

  uint8_t ctrl1 = 0;
  if (!reg_read(REG_CTRL1, &ctrl1)) {
    ctrl1 = 0;
  }
  if (!reg_write(REG_CTRL1, static_cast<uint8_t>(ctrl1 | CTRL1_AUTO_INC))) {
    Serial.println("[imu] CTRL1 write failed");
    return false;
  }

  uint8_t ctrl7 = 0;
  if (!reg_read(REG_CTRL7, &ctrl7)) {
    ctrl7 = 0;
  }
  /* Disable the accelerometer while reconfiguring it. */
  if (!reg_write(REG_CTRL7, static_cast<uint8_t>(ctrl7 & ~CTRL7_ACC_EN))) {
    Serial.println("[imu] CTRL7 disable failed");
    return false;
  }
  delay(10);

  if (!reg_write(REG_CTRL2, CTRL2_4G_250HZ)) {
    Serial.println("[imu] CTRL2 write failed");
    return false;
  }
  if (!reg_write(REG_CTRL5, 0x00)) { /* low-pass filter off */
    Serial.println("[imu] CTRL5 write failed");
    return false;
  }
  if (!reg_write(REG_CTRL7, static_cast<uint8_t>((ctrl7 & ~CTRL7_ACC_EN) | CTRL7_ACC_EN))) {
    Serial.println("[imu] CTRL7 enable failed");
    return false;
  }
  delay(60); /* let the first samples settle */

  /* BOOT (GPIO0) doubles as the runtime recalibration button. The boot-time
   * portal gate has already run by the time imu_begin() is called. */
  pinMode(0, INPUT_PULLUP);

  s_ready = true;
  Serial.println("[imu] QMI8658 ready (4g / 250Hz, address auto-increment)");
  return true;
}

void imu_calibrate(void) {
  if (!s_ready) {
    return;
  }
  float sum[3] = {0.0f, 0.0f, 0.0f};
  int samples = 0;
  for (int i = 0; i < CALIBRATION_SAMPLES; ++i) {
    float g[3] = {0.0f, 0.0f, 0.0f};
    if (read_accel_g(g)) {
      sum[0] += g[0];
      sum[1] += g[1];
      sum[2] += g[2];
      ++samples;
    }
    delay(CALIBRATION_GAP_MS);
  }
  if (samples < CALIBRATION_SAMPLES / 2) {
    Serial.println("[imu] calibration failed: not enough samples");
    return;
  }

  const float mean[3] = {sum[0] / samples, sum[1] / samples, sum[2] / samples};
  const float magnitude = sqrtf(mean[0] * mean[0] + mean[1] * mean[1] + mean[2] * mean[2]);
  if (magnitude < 0.60f || magnitude > 1.40f) {
    Serial.printf("[imu] calibration skipped: not at rest (|g|=%.2f g)\n", magnitude);
    return;
  }
  s_baseline[0] = mean[0] / magnitude;
  s_baseline[1] = mean[1] / magnitude;
  s_baseline[2] = mean[2] / magnitude;
  s_baseline_set = true;
  s_filter_init = false;
  s_orientation = IMU_LANDSCAPE;
  s_candidate = IMU_LANDSCAPE;
  s_candidate_since = millis();
  Serial.printf("[imu] calibrated: baseline=(%.2f, %.2f, %.2f) -> landscape\n", s_baseline[0], s_baseline[1],
                s_baseline[2]);
}

void imu_poll(void) {
  if (!s_ready) {
    return;
  }
  const uint32_t now = millis();

  /* Runtime BOOT long-press recalibration (never portals; that gate only runs
   * before the display is brought up). */
  update_boot_button(now);

  if ((now - s_last_read_ms) < SAMPLE_INTERVAL_MS) {
    return;
  }
  s_last_read_ms = now;

  float g[3] = {0.0f, 0.0f, 0.0f};
  if (!read_accel_g(g)) {
    return;
  }

  /* Light exponential filter to reject hand vibration. */
  if (!s_filter_init) {
    s_filtered[0] = g[0];
    s_filtered[1] = g[1];
    s_filtered[2] = g[2];
    s_filter_init = true;
  } else {
    for (int i = 0; i < 3; ++i) {
      s_filtered[i] = (s_filtered[i] * 0.75f) + (g[i] * 0.25f);
    }
  }

  const float magnitude =
      sqrtf(s_filtered[0] * s_filtered[0] + s_filtered[1] * s_filtered[1] + s_filtered[2] * s_filtered[2]);
  if (magnitude < 0.50f || magnitude > 1.60f) {
    return; /* free fall or a shake: keep the last stable orientation */
  }

  if (!s_baseline_set) {
    imu_calibrate(); /* fallback when the sketch did not calibrate at boot */
    return;
  }

  const float unit[3] = {s_filtered[0] / magnitude, s_filtered[1] / magnitude, s_filtered[2] / magnitude};
  const float dot = (unit[0] * s_baseline[0]) + (unit[1] * s_baseline[1]) + (unit[2] * s_baseline[2]);

  ImuOrientation candidate = s_orientation;
  if (dot > DOT_UP) {
    candidate = IMU_LANDSCAPE;
  } else if (dot < DOT_DOWN) {
    candidate = IMU_LANDSCAPE_FLIPPED;
  } else if (fabsf(dot) < DOT_FLAT) {
    candidate = IMU_PORTRAIT;
  }
  /* Otherwise (between DOT_FLAT and +/-DOT_UP) keep the current orientation:
   * that band is the hysteresis against tilt wobble. */
  if (s_manual_flip) {
    if (candidate == IMU_LANDSCAPE) {
      candidate = IMU_LANDSCAPE_FLIPPED;
    } else if (candidate == IMU_LANDSCAPE_FLIPPED) {
      candidate = IMU_LANDSCAPE;
    }
  }

  if (candidate == s_orientation) {
    s_candidate = s_orientation;
    return;
  }
  if (candidate != s_candidate) {
    s_candidate = candidate;
    s_candidate_since = now;
    return;
  }
  if ((now - s_candidate_since) >= DEBOUNCE_MS) {
    /* The sketch logs the change and applies the 180-degree flush flip. */
    s_orientation = candidate;
  }
}

ImuOrientation imu_orientation(void) { return s_orientation; }

const char *imu_orientation_name(ImuOrientation orientation) {
  switch (orientation) {
    case IMU_LANDSCAPE: return "landscape";
    case IMU_LANDSCAPE_FLIPPED: return "landscape flipped";
    case IMU_PORTRAIT: return "portrait";
    case IMU_UNKNOWN:
    default: return "unknown";
  }
}
