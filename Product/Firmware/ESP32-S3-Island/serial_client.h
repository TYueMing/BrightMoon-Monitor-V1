/*
 * Bidirectional USB serial transport for the Solo Island mirror.
 *
 * The ESP32-S3 dev board exposes its native USB CDC as a plain serial line at
 * 115200 8N1. When a desktop bridge is reachable over that cable, it can push
 * the same compact state document that the HTTP transport serves, and the user
 * can approve/deny whitelisted desktop actions with the on-screen buttons.
 *
 * Wire protocol (line based, UTF-8, every frame starts with "SOLO1 "):
 *   desktop -> board   SOLO1 HELLO
 *                      SOLO1 STATE <compact JSON>
 *                      SOLO1 RESULT {"id":"<token>","ok":true,"message":"..."}
 *   board -> desktop   SOLO1 READY v1                (boot + ~5 s heartbeat)
 *                      SOLO1 ACK <rev> <age_ms>      (after a STATE applied)
 *                      SOLO1 CMD {"id":"<token>"}    (UI action button tapped)
 *
 * All other serial lines (firmware logs, terminal echo, ...) are ignored, so a
 * normal serial monitor can be attached without confusing either side.
 *
 * Threading: serial_client_poll() runs on the Arduino loop task and owns the
 * RX line parser and the TX writes. serial_client_send_action() is safe to
 * call from the LVGL task: it only enqueues a command, which serial_client_poll
 * flushes. serial_client_take_result() is called from the loop task.
 */
#ifndef SERIAL_CLIENT_H
#define SERIAL_CLIENT_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "island_model.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Called right after Serial.begin(115200); sends the first READY frame. */
void serial_client_begin(void);

/*
 * USB-only boot probe, called before the config portal gate when no WiFi
 * configuration is stored: sends READY every 500 ms and waits for HELLO.
 * Returns true when a desktop answered (the sketch then boots USB-only), false
 * on timeout. Never blocks longer than timeout_ms.
 */
bool serial_client_boot_handshake(uint32_t timeout_ms);

/* Non-blocking line reader + READY heartbeat + queued CMD flush. */
void serial_client_poll(void);

/* True when a state arrived within max_age_ms (USB transport is live). */
bool serial_client_fresh(uint32_t max_age_ms);

/* Mutex-protected snapshot of the last valid USB state. */
bool serial_client_copy_state(IslandState *out);

/* Revision of the last USB state (0 when none yet). */
uint32_t serial_client_rev(void);

/* Seconds since the last USB state, or -1 when none has arrived. */
int32_t serial_client_age_seconds(void);

/* "USB 已连接" (HELLO/state seen) / "USB 等待" / "USB 未启用". */
const char *serial_client_status_text(void);

/* True when this boot is running USB-only (handshake succeeded, no WiFi). */
bool serial_client_usb_only(void);

/* Queue "SOLO1 CMD" for one desktop action id; safe from the LVGL task. */
void serial_client_send_action(const char *action_id);

/* One-shot RESULT drain for the UI toast. Returns false when nothing pending. */
bool serial_client_take_result(char *message, size_t size, bool *ok);
bool serial_client_request(const char *verb, const char *id, const char *op, int page, const char *cmd);
void serial_client_new_id(char *out);
bool serial_client_take_reply(IslandReply *reply);

#ifdef __cplusplus
}
#endif

#endif /* SERIAL_CLIENT_H */
