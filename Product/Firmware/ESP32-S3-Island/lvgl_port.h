#ifndef LVGL_PORT_H
#define LVGL_PORT_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Initialise QSPI panel, touch, LVGL, and build the mirror UI. */
void lvgl_port_init(void);
/* Battery latch must be raised before lengthy configuration/network waits. */
void lvgl_port_power_init(void);
/* Caller holds the LVGL lock. Battery off; USB power requires deep sleep. */
void lvgl_port_power_off(void);

/* LVGL is touched from the Arduino loop and the LVGL task; always hold this. */
bool lvgl_port_lock(int32_t timeout_ms);
void lvgl_port_unlock(void);

/* Rotate the rendered landscape frame 180 degrees for upside-down mounting. */
void lvgl_port_set_flip(bool flip);

#ifdef __cplusplus
}
#endif

#endif
