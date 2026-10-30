#ifndef ISLAND_UI_H
#define ISLAND_UI_H

#include <stdbool.h>
#include "island_model.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Called once from lvgl_port_init() with the LVGL mutex already held. */
void island_ui_build(void);

/* Called from the Arduino loop while holding lvgl_port_lock(). */
void island_ui_apply(const IslandState *state);

/* Link/refresh state, also called under lvgl_port_lock(). */
void island_ui_set_link(bool online, int32_t seconds_since_sync);

/* Override the top-strip link text (e.g. "USB 已连接"); NULL/"" restores the
 * default net_status_text() used by the WiFi transport. */
void island_ui_set_link_text(const char *text);

/* Show the asynchronous result of an approval/action command for a few
 * seconds (green success / red failure). Called under lvgl_port_lock(). */
void island_ui_show_result(const char *message, bool ok);
void island_ui_control_reply(const IslandReply *reply);
/* Physical PWR flow; all access under the LVGL mutex. */
void island_ui_show_power_dialog(void);
bool island_ui_take_power_shutdown(void);

#ifdef __cplusplus
}
#endif

#endif
