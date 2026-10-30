/*
 * WiFi/HTTP client for the Solo Island ESP32-S3 mirror.
 *
 * The firmware is a passive renderer: it polls the desktop bridge for a
 * pre-resolved compact state document and never decides anything visual on its
 * own. This module owns the connection, the ETag cache and the mutex-protected
 * IslandState copy that the UI thread reads.
 *
 * Threading: net_begin()/net_poll()/net_copy_state() are called from the
 * Arduino loop task. The LVGL task only touches the state through
 * net_copy_state(), which copies under a mutex.
 */
#ifndef NET_CLIENT_H
#define NET_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "island_model.h"

#ifdef __cplusplus
extern "C" {
#endif

/*
 * Decode one compact state JSON document into *out. This is the single wire
 * decoder shared by the HTTP poller and the USB serial transport; it returns
 * false on malformed JSON and never sets out->valid (the transport does that).
 */
bool net_decode_compact(const char *json, size_t length, IslandState *out);

/*
 * Read the saved configuration from NVS and start the non-blocking WiFi STA
 * connection (hostname "solo-island"). Must be called after the boot-time
 * portal gate, and after the display is up so a dead network cannot delay the
 * first frame.
 */
void net_begin(void);

/*
 * Drive the connection state machine. Call from loop(): it is non-blocking
 * except for one HTTP request of at most ~1.5 s when a poll is due.
 */
void net_poll(void);

/* Mutex-protected snapshot of the last valid state. */
bool net_copy_state(IslandState *out);

/* True when the last poll succeeded (HTTP 200 or 304). */
bool net_online(void);

/* Seconds since the last successful sync, or -1 when never synced. */
int32_t net_last_sync_seconds(void);

/* Alias kept for the integration notes that name the age explicitly. */
int32_t net_last_sync_age_seconds(void);

/* Short Chinese status for the UI: 连接中… / 等待电脑 / 令牌无效 / 已连接. */
const char *net_status_text(void);

/* Revision of the last successfully parsed state (UI uses it to skip no-ops). */
uint32_t net_rev(void);

/* ETag from the last 200 response, echoed back as If-None-Match. */
const char *net_etag(void);

/* "host:port" of the configured desktop bridge (diagnostics). */
const char *net_server_address(void);

/*
 * Set when the first STA connect attempt did not succeed within the 20 s boot
 * window. The sketch hands the device to the config portal once and then
 * blocks there. Never set again after the first successful connection.
 */
bool net_portal_requested(void);
bool net_queue_control(const char *line);
bool net_take_reply(char *out, size_t capacity);

#ifdef __cplusplus
}
#endif

#endif /* NET_CLIENT_H */
