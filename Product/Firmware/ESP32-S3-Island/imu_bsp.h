/*
 * QMI8658 orientation helper for the Solo Island mirror (Waveshare
 * ESP32-S3-Touch-LCD-3.49 V2, IMU on the shared system I2C bus at 0x6B).
 *
 * Mounting is arbitrary: the board can sit upright in "landscape" (the mirror
 * UI is 640x172), upside down, or be rotated. Rather than hard-coding sensor
 * axes, imu_calibrate() records the gravity vector of the current physical
 * position as "landscape upright"; afterwards the firmware compares live
 * gravity against that baseline:
 *   - same direction  -> IMU_LANDSCAPE
 *   - opposite        -> IMU_LANDSCAPE_FLIPPED (UI rotates 180 degrees)
 *   - in between      -> IMU_PORTRAIT / lying flat
 * Holding BOOT for ~2 s while the firmware runs re-captures the baseline.
 */
#ifndef IMU_BSP_H
#define IMU_BSP_H

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
  IMU_UNKNOWN = 0,
  IMU_LANDSCAPE,
  IMU_LANDSCAPE_FLIPPED,
  IMU_PORTRAIT,
} ImuOrientation;

/* Initialise the QMI8658 (WHO_AM_I check + 4 g / 250 Hz config). Returns false
 * when the sensor is missing so the caller can keep running without it. */
bool imu_begin(void);

/* Capture the current gravity direction as "landscape upright". Safe to call
 * again at runtime (BOOT long press). */
void imu_calibrate(void);

/* Read + filter + debounce, called from loop(). Also watches BOOT for the
 * long-press recalibration. */
void imu_poll(void);

/* Latest debounced orientation (IMU_UNKNOWN until the first calibration). */
ImuOrientation imu_orientation(void);

/* "landscape" / "landscape flipped" / "portrait" / "unknown" for serial logs. */
const char *imu_orientation_name(ImuOrientation orientation);

#ifdef __cplusplus
}
#endif

#endif /* IMU_BSP_H */
