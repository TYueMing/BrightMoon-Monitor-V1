/*
 * WiFi/HTTP(S) state client for the Solo Island mirror.
 *
 * Protocol (desktop bridge, LAN):
 *   GET {http|https}://HOST:PORT/api/esp32/v1/state  every ~1000 ms
 *   headers: X-Solo-ESP32: <token>, If-None-Match: <etag> once known
 *   200 -> JSON body + ETag + X-Server-Time   (parse)
 *   304 -> unchanged                          (refresh last-ok only)
 *   401 -> token rejected                     ("令牌无效", slow retry)
 *   other / transport error -> offline        (exponential backoff)
 *
 * TLS trust model (see README/protocol docs):
 *   The ESP32 usually has no trustworthy wall clock and the desktop recreates
 *   its leaf certificate on every service start, so standard CA + hostname
 *   validation at handshake time is fragile. Instead we:
 *     1. WiFiClientSecure::setInsecure() so the handshake is not blocked by a
 *        missing/incorrect clock;
 *     2. after the handshake, manually verify the peer chain against the pinned
 *        CA PEM from the configuration, ignoring ONLY the validity-date flags
 *        (MBEDTLS_X509_BADCERT_EXPIRED / _FUTURE) and rejecting every other
 *        flag (signature, issuer, key usage, ...);
 *     3. if no CA is configured, keep the encrypted transport but log
 *        "TLS 未校验（仅加密）" on every new connection.
 *   The TLS socket is kept alive between polls. PinnedClient also validates
 *   HTTPClient's implicit reconnects before credentials are transmitted. HTTPClient is
 *   begun once per connection and reused for every GET. A new handshake +
 *   chain verification only happens after a transport error or server close.
 *
 * The parser is deliberately defensive: every string goes through a
 * compile-time bounded strlcpy (never an unbounded strcpy), task arrays are
 * clamped to ISLAND_MAX_TASKS, and unknown/missing fields keep safe defaults.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <mbedtls/x509_crt.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/queue.h>
#include <WiFiUdp.h>
#include <mbedtls/md.h>
#include <atomic>
#include "usb_wifi.h"

#include "config_portal.h"
#include "net_client.h"

namespace {

const char *const STATE_PATH = "/api/esp32/v1/state";

constexpr uint32_t POLL_INTERVAL_MS = 1000;
constexpr uint16_t HTTP_TIMEOUT_MS = 1500;
constexpr unsigned long TLS_HANDSHAKE_TIMEOUT_SECONDS = 4;
constexpr uint32_t BOOT_WIFI_TIMEOUT_MS = 20000;
constexpr uint32_t RECONNECT_RETRY_MS = 10000;
constexpr uint32_t BACKOFF_MIN_MS = 2000;
constexpr uint32_t BACKOFF_MAX_MS = 15000;
constexpr uint32_t AUTH_RETRY_MS = 15000;
constexpr size_t ETAG_MAX = 48;
constexpr size_t SERVER_ADDR_MAX = 96;
constexpr int MAX_BODY_BYTES = 12288; /* protocol caps at 6144, leave headroom */

enum NetStatus { NET_ST_CONNECTING = 0, NET_ST_WAITING, NET_ST_BAD_TOKEN, NET_ST_CONNECTED };

/* Configuration and connection state (owned by the network worker). */
IslandConfig s_cfg;
bool s_wifi_started = false;
bool s_ever_connected = false;
bool s_portal_req = false;
uint32_t s_connect_start_ms = 0;
uint32_t s_next_reconnect_ms = 0;
uint32_t s_next_poll_ms = 0;
uint32_t s_backoff_ms = 0;
char s_server_addr[SERVER_ADDR_MAX] = {0};
char s_etag[ETAG_MAX] = {0};

/* One persistent transport per scheme. HTTPClient is intentionally file-scope
 * and never destroyed: its destructor would stop() the client and kill the
 * keep-alive TLS connection. The request object is begun exactly once per
 * transport connection and reused for every GET on that socket; it is ended
 * only when the socket is closed, so the next poll reopens and re-verifies. */
WiFiClient s_plain_client;
WiFiClientSecure *s_tls_client = nullptr;
HTTPClient s_http;
bool s_http_begun = false;
bool s_http_etag_sent = false;
bool s_logged_tls_heap = false;

/* Pinned CA, parsed once per boot on the first successful TLS handshake. */
mbedtls_x509_crt s_pinned_ca;
bool s_pinned_ca_ready = false;
bool s_pinned_ca_inited = false;

/* Shared with the LVGL task through s_mux. */
SemaphoreHandle_t s_mux = nullptr;
IslandState s_state;   /* protected by s_mux */
bool s_valid = false;  /* protected by s_mux */
uint32_t s_rev = 0;
uint32_t s_last_ok_ms = 0;
bool s_has_sync = false;
volatile bool s_online = false;
volatile int s_status = NET_ST_CONNECTING;
std::atomic<uint32_t> requested_config{0},applied_config{0};
TaskHandle_t network_worker=nullptr;
QueueHandle_t control_queue=nullptr,reply_queue=nullptr;
WiFiUDP discovery;
char discovery_nonce[33]="";
uint32_t last_discovery=0;
char pending_query[256]="";
uint32_t query_due=0,query_started=0;
static void network_loop(void *);

/* Parse target: file scope so the ~4.5 KB struct never sits on the 8 KB
 * Arduino loop-task stack. Only the network worker touches it. */
IslandState s_staged_state;

const char *status_text(int status) {
  switch (status) {
    case NET_ST_WAITING: return "等待电脑";
    case NET_ST_BAD_TOKEN: return "令牌无效";
    case NET_ST_CONNECTED: return "已连接";
    case NET_ST_CONNECTING:
    default: return "连接中…";
  }
}

void set_status(int status) {
  if (s_status == status) {
    return;
  }
  s_status = status;
  Serial.printf("[net] %s\n", status_text(status));
}

bool time_reached(uint32_t now, uint32_t target) { return static_cast<int32_t>(now - target) >= 0; }

/*
 * Copy at most N-1 bytes plus the terminator, stopping at a UTF-8 boundary.
 * Both memory safety and Chinese text survive an oversized upstream field.
 */
template <size_t N>
void copy_field(char (&dst)[N], JsonVariantConst src) {
  const char *text = src.as<const char *>();
  if (text == nullptr) {
    dst[0] = '\0';
    return;
  }
  size_t size = strlen(text);
  if (size >= N) {
    size = N - 1;
    // Do not cut Chinese/emoji in the middle of a UTF-8 codepoint.
    while (size > 0 && (static_cast<unsigned char>(text[size]) & 0xc0) == 0x80) --size;
  }
  memcpy(dst, text, size);
  dst[size] = '\0';
}

int clamp_int(JsonVariantConst v, int fallback, int lo, int hi) {
  const int value = v | fallback;
  if (value < lo) {
    return lo;
  }
  if (value > hi) {
    return hi;
  }
  return value;
}

void parse_state(JsonDocument &doc, IslandState *st) {
  memset(st, 0, sizeof(*st));

  st->version = doc["v"] | 0;
  st->rev = doc["rev"] | 0u;
  copy_field(st->epoch, doc["epoch"]);
  copy_field(st->clock, doc["now"]);
  copy_field(st->date, doc["date"]);
  st->desktop_visible = doc["desktop"] | false;
  st->attention = doc["attention"] | false;
  copy_field(st->control_mode, doc["control_mode"]);
  copy_field(st->privacy, doc["privacy"]);
  copy_field(st->mode, doc["mode"]);
  copy_field(st->focus, doc["focus"]);

  /* Theme resolved by the desktop; v1 always sends it, but defaults are safe. */
  JsonVariantConst theme = doc["theme"];
  copy_field(st->bg, theme["bg"]);
  copy_field(st->fg, theme["fg"]);
  copy_field(st->muted, theme["muted"]);
  copy_field(st->panel, theme["panel"]);
  st->panel_alpha = clamp_int(theme["panel_a"], 0, 0, 100);

  JsonVariantConst idle = doc["idle"];
  st->idle_preview = idle["preview"] | false;
  st->idle_count = idle["count"] | 0;
  copy_field(st->idle_title, idle["title"]);
  copy_field(st->idle_at, idle["at"]);
  copy_field(st->idle_body, idle["body"]);
  st->task_total = doc["task_total"] | 0;
  st->reminder_total = doc["reminder_total"] | 0;
  for (JsonObjectConst device : doc["devices"].as<JsonArrayConst>()) {
    if(st->device_count>=6) break;
    IslandDevice *d=&st->devices[st->device_count++];
    copy_field(d->id,device["id"]);copy_field(d->name,device["name"]);d->online=device["online"] | false;
  }
  for (JsonObjectConst reminder : doc["reminders"].as<JsonArrayConst>()) {
    if(st->reminder_count>=5) break;
    IslandReminder *r=&st->reminders[st->reminder_count++];
    copy_field(r->key,reminder["k"]);copy_field(r->title,reminder["title"]);copy_field(r->summary,reminder["sum"]);
    copy_field(r->dev,reminder["dev"]);copy_field(r->at,reminder["at"]);copy_field(r->control,reminder["control"]);
    r->active=reminder["active"] | false;
  }

  JsonArrayConst tasks = doc["tasks"].as<JsonArrayConst>();
  int32_t count = 0;
  for (JsonObjectConst task : tasks) {
    if (count >= ISLAND_MAX_TASKS) {
      break; /* never write past tasks[ISLAND_MAX_TASKS - 1] */
    }
    IslandTask *dst = &st->tasks[count];

    copy_field(dst->key, task["k"]);
    copy_field(dst->app, task["app"]);
    copy_field(dst->name, task["name"]);
    copy_field(dst->glyph, task["glyph"]);
    copy_field(dst->status, task["status"]);
    copy_field(dst->label, task["label"]);
    copy_field(dst->color, task["color"]);
    copy_field(dst->title, task["title"]);
    copy_field(dst->summary, task["sum"]);
    copy_field(dst->control, task["control"]);
    /* Device label: serial peers send "dev"; the local desktop omits it and the
     * task belongs to this machine. */
    copy_field(dst->dev, task["dev"]);
    copy_field(dst->device_id, task["device"]);
    if (dst->dev[0] == '\0') {
      strlcpy(dst->dev, "本机", sizeof(dst->dev));
    }
    dst->error = task["err"] | false;
    dst->final = task["final"] | false;
    dst->remote = task["remote"] | false;
    dst->elapsed_s = task["elapsed"] | 0;

    /* plan may be null; missing/null fields intentionally stay at -1 so the UI
     * hides the progress bar instead of inventing a percentage. */
    dst->plan_done = task["plan"]["done"] | -1;
    dst->plan_total = task["plan"]["total"] | -1;
    copy_field(dst->plan_label, task["plan"]["label"]);
    copy_field(dst->step, task["plan"]["step"]);

    /* att may be null. Tolerate the object form the desktop bridge uses plus
     * string/true variants so a protocol tweak cannot silently drop the
     * attention badge. */
    JsonVariantConst att = task["att"];
    if (att.is<JsonObjectConst>()) {
      dst->has_attention = true;
      copy_field(dst->attention_title, att["title"]);
      copy_field(dst->attention_reason, att["reason"]);
    } else if (att.is<const char *>()) {
      const char *reason = att.as<const char *>();
      if (reason != nullptr && reason[0] != '\0') {
        dst->has_attention = true;
        copy_field(dst->attention_reason, att);
      }
    } else if (att.is<bool>() && att.as<bool>()) {
      dst->has_attention = true;
    }
    if (dst->has_attention) {
      if (dst->attention_title[0] == '\0') {
        copy_field(dst->attention_title, task["label"]);
      }
      if (dst->attention_reason[0] == '\0') {
        copy_field(dst->attention_reason, task["sum"]);
      }
    }

    /* Optional serial-only action buttons (the HTTP projection omits them).
     * Each id is an opaque desktop token that the firmware only echoes back
     * over USB when the user taps the button. */
    JsonArrayConst actions = task["actions"].as<JsonArrayConst>();
    int32_t action_count = 0;
    for (JsonObjectConst action : actions) {
      if (action_count >= ISLAND_MAX_ACTIONS) {
        break;
      }
      IslandAction *slot = &dst->actions[action_count];
      copy_field(slot->id, action["id"]);
      copy_field(slot->kind, action["kind"]);
      copy_field(slot->label, action["label"]);
      if (slot->id[0] != '\0') {
        ++action_count;
      }
    }
    dst->action_count = action_count;

    ++count;
  }
  st->task_count = count;
}

/* Tolerate a pasted "http://1.2.3.4:18765/" in the host field. */
void sanitize_host(char *host) {
  char *start = host;
  if (strncmp(start, "http://", 7) == 0) {
    start += 7;
  } else if (strncmp(start, "https://", 8) == 0) {
    start += 8;
  }
  if (start != host) {
    memmove(host, start, strlen(start) + 1);
  }
  char *slash = strchr(host, '/');
  if (slash != nullptr) {
    *slash = '\0';
  }
  /* The port has its own field; strip ":port" from a pasted address. IPv4 or a
   * LAN hostname is expected, not IPv6. */
  char *colon = strchr(host, ':');
  if (colon != nullptr) {
    *colon = '\0';
  }
}

uint32_t grow_backoff() {
  if (s_backoff_ms == 0) {
    s_backoff_ms = BACKOFF_MIN_MS;
  } else if (s_backoff_ms < BACKOFF_MAX_MS) {
    s_backoff_ms *= 2;
    if (s_backoff_ms > BACKOFF_MAX_MS) {
      s_backoff_ms = BACKOFF_MAX_MS;
    }
  }
  return s_backoff_ms;
}

/* Verify callback: clear only the calendar validity flags so a device without
 * a reliable clock can still validate the chain. Every other failure flag is
 * left in place and rejected by the caller. */
int ignore_calendar_flags(void * /*ctx*/, mbedtls_x509_crt * /*crt*/, int /*depth*/, uint32_t *flags) {
  *flags &= ~static_cast<uint32_t>(MBEDTLS_X509_BADCERT_EXPIRED | MBEDTLS_X509_BADCERT_FUTURE);
  return 0;
}

/* Manual chain verification of the just-completed (insecure) TLS handshake
 * against the pinned CA. The desktop sends its intermediate/CA in the TLS
 * chain, so verifying the peer chain against the configured trust anchor works
 * even though the leaf certificate is regenerated on every service start. */
bool tls_verify_peer() {
  if (s_cfg.ca_pem[0] == '\0') {
    return true; /* caller logs "TLS 未校验（仅加密）" */
  }
  if (s_tls_client == nullptr) {
    return false;
  }
  if (!s_pinned_ca_ready) {
    if (s_pinned_ca_inited) {
      mbedtls_x509_crt_free(&s_pinned_ca); /* release a previous failed parse */
    }
    mbedtls_x509_crt_init(&s_pinned_ca);
    s_pinned_ca_inited = true;
    const int parsed = mbedtls_x509_crt_parse(&s_pinned_ca,
                                              reinterpret_cast<const unsigned char *>(s_cfg.ca_pem),
                                              strlen(s_cfg.ca_pem) + 1);
    if (parsed != 0) {
      Serial.printf("[net] pinned CA parse failed: -0x%04x\n", -parsed);
      mbedtls_x509_crt_free(&s_pinned_ca);
      s_pinned_ca_inited = false;
      return false;
    }
    s_pinned_ca_ready = true;
    Serial.printf("[net] pinned CA parsed (%u bytes)\n", static_cast<unsigned>(strlen(s_cfg.ca_pem)));
  }

  const mbedtls_x509_crt *peer = s_tls_client->getPeerCertificate();
  if (peer == nullptr) {
    Serial.println("[net] TLS peer certificate unavailable");
    return false;
  }
  uint32_t flags = 0;
  const int result = mbedtls_x509_crt_verify(const_cast<mbedtls_x509_crt *>(peer), &s_pinned_ca, nullptr,
                                             nullptr, &flags, ignore_calendar_flags, nullptr);
  const uint32_t reject = flags & ~static_cast<uint32_t>(MBEDTLS_X509_BADCERT_EXPIRED | MBEDTLS_X509_BADCERT_FUTURE);
  if (result != 0 || reject != 0) {
    Serial.printf("[net] TLS chain rejected: ret=-0x%04x flags=0x%08x (clock-only flags ignored)\n", -result,
                  static_cast<unsigned>(flags));
    return false;
  }
  return true;
}

// HTTPClient may reconnect after the explicit preflight. Validate every new
// TLS connection before it can send headers containing the bearer credential.
class PinnedClient: public WiFiClientSecure {
 public:
 int connect(const char *host,uint16_t port,int32_t timeout) override {
  _timeout=timeout;
  // The base three-argument overload calls the virtual two-argument overload.
  // Call the explicit certificate overload to avoid recursive dispatch.
  int ok=WiFiClientSecure::connect(host,port,_CA_cert,_cert,_private_key);
  if(ok&&s_cfg.ca_pem[0]&&!tls_verify_peer()){stop();return 0;}return ok;
 }
 int connect(const char *host,uint16_t port) override {return connect(host,port,HTTP_TIMEOUT_MS);}
 int connect(IPAddress ip,uint16_t port,int32_t timeout) override {String host=ip.toString();return connect(host.c_str(),port,timeout);}
 int connect(IPAddress ip,uint16_t port) override {return connect(ip,port,HTTP_TIMEOUT_MS);}
};
NetworkClient &active_client() {
  if (s_cfg.tls) {
    if (s_tls_client == nullptr) {
      s_tls_client = new PinnedClient();
      if (s_tls_client == nullptr) {
        Serial.println("[net] TLS client allocation failed");
      }
    }
    return *s_tls_client;
  }
  return s_plain_client;
}

/* Open the transport if needed. PinnedClient verifies any implicit reconnect
 * as well; successful polls normally reuse the socket. */
bool connect_transport() {
  if (s_cfg.tls && s_tls_client == nullptr) {
    (void)active_client();
    if (s_tls_client == nullptr) {
      return false;
    }
  }
  NetworkClient &client = active_client();
  if (client.connected()) {
    return true;
  }

  const int attempts = s_cfg.tls ? 2 : 1; /* one delayed retry for TLS handshakes */
  int rc = 0;
  for (int attempt = 1; attempt <= attempts; ++attempt) {
    if (s_cfg.tls) {
      s_tls_client->setInsecure();
      s_tls_client->setHandshakeTimeout(TLS_HANDSHAKE_TIMEOUT_SECONDS);
    }
    client.setTimeout(HTTP_TIMEOUT_MS);
    Serial.printf("[net] %s connect %s:%u (heap=%u)\n", s_cfg.tls ? "TLS" : "TCP", s_cfg.host,
                  static_cast<unsigned>(s_cfg.port), static_cast<unsigned>(ESP.getFreeHeap()));
    rc = client.connect(s_cfg.host, s_cfg.port, HTTP_TIMEOUT_MS);
    if (rc == 1) {
      break;
    }
    Serial.printf("[net] %s connect attempt %d/%d failed (rc=%d heap=%u)\n", s_cfg.tls ? "TLS" : "TCP", attempt,
                  attempts, rc, static_cast<unsigned>(ESP.getFreeHeap()));
    if (attempt < attempts) {
      client.stop();
      delay(300);
    }
  }
  if (rc != 1) {
    return false;
  }

  if (s_cfg.tls) {
    if (s_cfg.ca_pem[0] != '\0') {
      if (!tls_verify_peer()) {
        s_tls_client->stop();
        return false;
      }
      Serial.printf("[net] TLS chain verified against pinned CA (heap=%u)\n",
                    static_cast<unsigned>(ESP.getFreeHeap()));
    } else {
      Serial.printf("[net] TLS 未校验（仅加密） heap=%u\n", static_cast<unsigned>(ESP.getFreeHeap()));
    }
  }
  return true;
}

/* Perform one GET on the already-verified transport.
 *
 * Keep-alive contract: connect_transport() performs an explicit preflight;
 * PinnedClient verifies every subsequent connection too. HTTPClient begins
 * once on that socket and is then
 * reused with GET() alone: sendRequest() reuses the connected client and does
 * not disconnect at the end, so no TLS handshake can happen per poll. Whenever
 * the socket is gone or HTTPClient reports a transport error we end() the
 * object and stop() the client, so the next poll reopens and re-verifies.
 * Returns true when an HTTP status code was received; *code is then >= 100. */
bool perform_request(int *code, String *etag, String *payload) {
  if (!connect_transport()) {
    return false;
  }
  NetworkClient &client = active_client();

  if (!s_http_begun) {
    String url = s_cfg.tls ? "https://" : "http://";
    url += s_cfg.host;
    url += ':';
    url += String(static_cast<unsigned>(s_cfg.port));
    url += STATE_PATH;

    s_http.setReuse(true);
    s_http.setConnectTimeout(HTTP_TIMEOUT_MS);
    if (!s_http.begin(client, url)) {
      Serial.println("[net] HTTPClient begin failed");
      s_http.end();
      client.stop();
      return false;
    }
    s_http.setTimeout(HTTP_TIMEOUT_MS);
    s_http.addHeader("X-Solo-ESP32", s_cfg.token);
    s_http.addHeader("X-Solo-Board", usb_wifi_board());
    s_http.addHeader("Accept", "application/json");
    static const char *header_keys[] = {"ETag", "X-Server-Time"};
    s_http.collectHeaders(header_keys, 2);
    s_http_etag_sent = false;
    s_http_begun = true;
  }

  /* Update the conditional header on the reusable object (replace=true keeps a
   * single If-None-Match entry). If an ETag was sent before but the server
   * stopped reporting one, drop the session instead of sending a stale value. */
  if (s_etag[0] != '\0') {
    s_http.addHeader("If-None-Match", s_etag, false, true);
    s_http_etag_sent = true;
  } else if (s_http_etag_sent) {
    s_http.end();
    s_http_begun = false;
    s_http_etag_sent = false;
    client.stop();
    return false;
  }

  const int http_code = s_http.GET();
  if (http_code <= 0) {
    const String error = s_http.errorToString(http_code);
    s_http.end();
    s_http_begun = false;
    s_http_etag_sent = false;
    client.stop(); /* force a fresh handshake + verification next poll */
    Serial.printf("[net] request error %d (%s)\n", http_code, error.c_str());
    return false;
  }

  *code = http_code;
  if (http_code == 200) {
    *etag = s_http.header("ETag");
    *payload = s_http.getString();
  } else if (http_code != 304) {
    /* Drain the body (401/403/5xx) so the keep-alive connection stays in sync. */
    s_http.getString();
  }
  /* No end() on success: the socket and the begun request object are reused for
   * the next poll. Only drop them when the server closed the connection. */
  if (!client.connected()) {
    s_http.end();
    s_http_begun = false;
    s_http_etag_sent = false;
    client.stop();
  }
  return true;
}

void note_failure(int status, uint32_t delay_ms) {
  s_online = false;
  set_status(status);
  s_next_poll_ms = millis() + delay_ms;
}

/* Close the reusable request/socket (used when the response could not be
 * consumed cleanly, so the next poll reconnects and re-verifies TLS). */
void drop_http_session() {
  if (s_http_begun) {
    s_http.end();
    s_http_begun = false;
    s_http_etag_sent = false;
  }
  active_client().stop();
}

void http_poll() {
  const bool tls = s_cfg.tls;
  int code = 0;
  String etag;
  String payload;
  if (!perform_request(&code, &etag, &payload)) {
    note_failure(NET_ST_WAITING, grow_backoff());
    return;
  }

  if (code == 200) {
    if (payload.length() == 0 || payload.length() > MAX_BODY_BYTES) {
      note_failure(NET_ST_WAITING, grow_backoff());
      drop_http_session(); /* the body is unusable: do not reuse the stream */
      Serial.printf("[net] bad payload size %u\n", static_cast<unsigned>(payload.length()));
      return;
    }
    /* Same decoder the USB serial transport uses: one wire contract, one
     * parser, so HTTP and USB can never drift apart. */
    if (!net_decode_compact(payload.c_str(), payload.length(), &s_staged_state)) {
      note_failure(NET_ST_WAITING, grow_backoff());
      drop_http_session();
      Serial.println("[net] JSON error");
      return;
    }

    if (xSemaphoreTake(s_mux, pdMS_TO_TICKS(100)) == pdTRUE) {
      memcpy(&s_state, &s_staged_state, sizeof(s_state));
      s_state.valid = true;
      s_rev = s_staged_state.rev;
      s_valid = true;
      xSemaphoreGive(s_mux);
    }
    if (etag.length() > 0) {
      strlcpy(s_etag, etag.c_str(), sizeof(s_etag));
    } else {
      s_etag[0] = '\0';
    }
    s_last_ok_ms = millis();
    s_has_sync = true;
    s_online = true;
    s_backoff_ms = 0;
    s_next_poll_ms = millis() + POLL_INTERVAL_MS;
    set_status(NET_ST_CONNECTED);
    if (tls && !s_logged_tls_heap) {
      s_logged_tls_heap = true;
      Serial.printf("[net] first TLS state ok, free heap=%u free psram=%u\n",
                    static_cast<unsigned>(ESP.getFreeHeap()), static_cast<unsigned>(ESP.getFreePsram()));
    }
    Serial.printf("[net] state rev=%u tasks=%d %s\n", static_cast<unsigned>(s_rev),
                  static_cast<int>(s_staged_state.task_count), s_staged_state.epoch);
    return;
  }

  if (code == 304) {
    s_last_ok_ms = millis();
    s_has_sync = true;
    s_online = true;
    s_backoff_ms = 0;
    s_next_poll_ms = millis() + POLL_INTERVAL_MS;
    set_status(NET_ST_CONNECTED);
    return;
  }

  if (code == 401) {
    /* Token rejected: keep showing 令牌无效 and retry slowly. */
    s_online = false;
    set_status(NET_ST_BAD_TOKEN);
    s_next_poll_ms = millis() + AUTH_RETRY_MS;
    return;
  }

  const uint32_t delay_ms = grow_backoff();
  note_failure(NET_ST_WAITING, delay_ms);
  Serial.printf("[net] HTTP %d, retry in %u ms\n", code, static_cast<unsigned>(delay_ms));
}

}  // namespace

static void discover_desktop(){
 if(!s_cfg.server_id[0]||WiFi.status()!=WL_CONNECTED)return;
 if(millis()-last_discovery>10000){
  last_discovery=millis();
  snprintf(discovery_nonce,sizeof(discovery_nonce),"%08lx%08lx%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random(),(unsigned long)esp_random(),(unsigned long)esp_random());
  JsonDocument doc;doc["server"]=s_cfg.server_id;doc["board"]=usb_wifi_board();doc["nonce"]=discovery_nonce;
  String request;serializeJson(doc,request);discovery.beginPacket(IPAddress(255,255,255,255),18770);discovery.print(request);discovery.endPacket();
 }
 int n=discovery.parsePacket();if(!n)return;
 char buffer[600];if(n>=int(sizeof(buffer))){while(discovery.available())discovery.read();return;}
 int received=discovery.read(buffer,sizeof(buffer)-1);if(received<=0)return;buffer[received]=0;
 JsonDocument doc;if(deserializeJson(doc,buffer)!=DeserializationError::Ok)return;
 if(strcmp(doc["server"] | "",s_cfg.server_id)||strcmp(doc["nonce"] | "",discovery_nonce))return;
 const char *host=doc["host"] | "";int port=doc["port"] | 0;
 if(discovery.remoteIP().toString()!=host||port!=18769)return;
 String message=String(s_cfg.server_id)+"|"+discovery_nonce+"|"+host+"|"+String(port);
 unsigned char key[32],proof[32];char expected[65];
 const mbedtls_md_info_t *info=mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
 mbedtls_md(info,(const unsigned char*)s_cfg.token,strlen(s_cfg.token),key);
 mbedtls_md_hmac(info,key,32,(const unsigned char*)message.c_str(),message.length(),proof);
 for(int i=0;i<32;i++)snprintf(expected+i*2,3,"%02x",proof[i]);
 const char *actual=doc["proof"] | "";if(strlen(actual)!=64)return;
 unsigned diff=0;for(int i=0;i<64;i++)diff|=actual[i]^expected[i];if(diff)return;
 if(strcmp(s_cfg.host,host)||s_cfg.port!=port){drop_http_session();strlcpy(s_cfg.host,host,sizeof(s_cfg.host));s_cfg.port=port;s_etag[0]=0;s_next_poll_ms=0;}
}

static void push_reply(const char *kind,JsonVariantConst value){
 if(!reply_queue)return;char line[1200];String body;serializeJson(value,body);
 if(body.length()+strlen(kind)+8>=sizeof(line))return;
 snprintf(line,sizeof(line),"SOLO2 %s %s",kind,body.c_str());xQueueSend(reply_queue,line,0);
}
static bool wireless_control(const char *line){
 const char *raw=strchr(line+6,' ');if(!raw)return false;
 JsonDocument value;if(deserializeJson(value,raw+1)!=DeserializationError::Ok)return false;
 String verb=String(line+6).substring(0,raw-(line+6));
 JsonDocument body;body["verb"]=verb;body["value"]=value.as<JsonVariantConst>();String encoded;serializeJson(body,encoded);
 JsonDocument result;
 auto failed=[&](){result["id"]=value["id"];result["cmd"]=value["cmd"];result["status"]=verb=="DO"?"outcome_unknown":"rejected";result["message"]="无线结果未确认，请查询原请求";push_reply("RESULT",result.as<JsonVariantConst>());};
 drop_http_session();
 if(!s_cfg.tls||!s_cfg.ca_pem[0]||!connect_transport()){failed();return false;}
 HTTPClient http;String url=String("https://")+s_cfg.host+":"+String(s_cfg.port)+"/api/esp32/v1/control";
 http.setConnectTimeout(HTTP_TIMEOUT_MS);http.setTimeout(HTTP_TIMEOUT_MS);http.setReuse(false);
 if(!http.begin(active_client(),url)){failed();return false;}
 http.addHeader("Content-Type","application/json");http.addHeader("X-Solo-ESP32",s_cfg.token);http.addHeader("X-Solo-Board",usb_wifi_board());
 int code=http.POST(encoded);String response=code==200?http.getString():"";http.end();active_client().stop();
 if(code!=200||response.length()>1500||deserializeJson(result,response)!=DeserializationError::Ok){failed();return false;}
 const char *kind=result["kind"] | "";
 if(strcmp(kind,"DETAIL")&&strcmp(kind,"RESULT")){failed();return false;}
 push_reply(kind,result["value"]);
 if(!strcmp(kind,"RESULT")){
  const char *status=result["value"]["status"] | "";
  if(!strcmp(status,"pending")){
   if(verb=="DO")query_started=millis();
   snprintf(pending_query,sizeof(pending_query),"SOLO2 QUERY %s",raw+1);query_due=millis()+700;
  }else pending_query[0]=0;
 }
 return true;
}

bool net_decode_compact(const char *json, size_t length, IslandState *out) {
  if (json == nullptr || out == nullptr || length == 0 || length > static_cast<size_t>(MAX_BODY_BYTES)) {
    return false;
  }
  JsonDocument doc;
  const DeserializationError err = deserializeJson(doc, json, length);
  if (err || !doc.is<JsonObject>()) {
    return false;
  }
  parse_state(doc, out);
  return true;
}

static void net_start_internal() {
  if(s_http_begun){s_http.end();s_http_begun=false;}
  if(s_tls_client)s_tls_client->stop();s_plain_client.stop();
  if(s_pinned_ca_inited){mbedtls_x509_crt_free(&s_pinned_ca);s_pinned_ca_inited=s_pinned_ca_ready=false;}
  discovery.stop();discovery.begin(0);last_discovery=0;
  s_wifi_started=false;
  if (s_mux == nullptr) {
    s_mux = xSemaphoreCreateMutex();
    if (s_mux == nullptr) {
      Serial.println("[net] mutex allocation failed");
    }
  }
  if(s_mux&&xSemaphoreTake(s_mux,portMAX_DELAY)==pdTRUE){
    memset(&s_state,0,sizeof(s_state));s_valid=false;xSemaphoreGive(s_mux);
  }
  memset(&s_staged_state, 0, sizeof(s_staged_state));
  s_rev = 0;
  s_has_sync = false;
  s_last_ok_ms = 0;
  s_online = false;
  s_status = NET_ST_CONNECTING;
  s_backoff_ms = 0;
  s_etag[0] = '\0';
  s_ever_connected = false;
  s_portal_req = false;
  s_logged_tls_heap = false;
  s_http_begun = false;
  s_http_etag_sent = false;

  const bool configured = config_load(&s_cfg);
  if (!configured) {
    /* The boot gate in the sketch normally redirects to the portal first. */
    s_portal_req = true;
    Serial.println("[net] no configuration, portal required");
    return;
  }
  /* Accept a pasted "http://..." / "https://..." in the host field too: the
   * scheme overrides the tls flag before the host is sanitised. */
  if (strncmp(s_cfg.host, "https://", 8) == 0) {
    s_cfg.tls = true;
  } else if (strncmp(s_cfg.host, "http://", 7) == 0) {
    s_cfg.tls = false;
  }
  sanitize_host(s_cfg.host);
  if (s_cfg.host[0] == '\0') {
    /* e.g. the user pasted "http://" with no address: treat as unconfigured. */
    s_portal_req = true;
    Serial.println("[net] desktop address invalid after sanitising, portal required");
    return;
  }
  snprintf(s_server_addr, sizeof(s_server_addr), "%s:%u", s_cfg.host, static_cast<unsigned>(s_cfg.port));
  Serial.printf("[net] transport %s://%s config=%s token_len=%u ca=%s\n", s_cfg.tls ? "https" : "http",
                s_server_addr, config_from_sd() ? "SD" : "NVS", static_cast<unsigned>(strlen(s_cfg.token)),
                s_cfg.ca_pem[0] ? "pinned" : "none");

  WiFi.persistent(false); /* we own the NVS configuration, avoid WiFi NVS churn */
  WiFi.mode(WIFI_STA);
  WiFi.setHostname("solo-island");
  WiFi.setAutoReconnect(true);

  if (s_cfg.pass[0] != '\0') {
    WiFi.begin(s_cfg.ssid, s_cfg.pass);
  } else {
    WiFi.begin(s_cfg.ssid);
  }
  s_wifi_started = true;
  s_connect_start_ms = millis();
  s_next_reconnect_ms = millis() + RECONNECT_RETRY_MS;
  Serial.printf("[net] connecting to \"%s\"\n", s_cfg.ssid);
}

static void net_poll_internal(void) {
  if (!s_wifi_started) {
    return;
  }
  const uint32_t now = millis();

  if (WiFi.status() != WL_CONNECTED) {
    if (s_online) {
      s_online = false;
    }
    set_status(NET_ST_CONNECTING);

    if (!s_cfg.server_id[0] && !s_ever_connected && !s_portal_req && (now - s_connect_start_ms) >= BOOT_WIFI_TIMEOUT_MS) {
      /* First boot-window connection failed: hand over to the config portal.
       * Later drops never portal (the display is already showing the last
       * state and a router reboot should not wipe the device into AP mode). */
      s_portal_req = true;
      Serial.println("[net] WiFi not connected within 20 s, portal required");
      return;
    }
    if (time_reached(now, s_next_reconnect_ms)) {
      s_next_reconnect_ms = now + RECONNECT_RETRY_MS;
      Serial.printf("[net] WiFi reconnect (status %d)\n", static_cast<int>(WiFi.status()));
      WiFi.reconnect();
    }
    return;
  }

  if (!s_ever_connected) {
    s_ever_connected = true;
    s_backoff_ms = 0;
    s_next_poll_ms = now;
    Serial.print("[net] WiFi connected, ip=");
    Serial.println(WiFi.localIP());
  }
  if(!s_online)discover_desktop();
  if (!time_reached(now, s_next_poll_ms)) {
    return;
  }
  http_poll();
}

bool net_copy_state(IslandState *out) {
  if (out == nullptr || s_mux == nullptr) {
    return false;
  }
  bool copied = false;
  if (xSemaphoreTake(s_mux, pdMS_TO_TICKS(100)) == pdTRUE) {
    if (s_valid) {
      memcpy(out, &s_state, sizeof(IslandState));
      copied = true;
    }
    xSemaphoreGive(s_mux);
  }
  return copied;
}

bool net_online(void) { return requested_config.load()==applied_config.load()&&s_online&&s_has_sync&&millis()-s_last_ok_ms<5000; }

int32_t net_last_sync_seconds(void) {
  if (!s_has_sync) {
    return -1;
  }
  return static_cast<int32_t>((millis() - s_last_ok_ms) / 1000u);
}

const char *net_status_text(void) { return status_text(s_status); }

int32_t net_last_sync_age_seconds(void) { return net_last_sync_seconds(); }

uint32_t net_rev(void) { return s_rev; }

const char *net_etag(void) { return s_etag; }

const char *net_server_address(void) { return s_server_addr; }

bool net_portal_requested(void) { return s_portal_req; }

// All blocking TLS/HTTP work stays off the serial owner and LVGL threads.
namespace {
static void network_loop(void *){
 char request[256];
 for(;;){
  uint32_t generation=requested_config.load();
  if(generation!=applied_config.load()){net_start_internal();applied_config.store(generation);}
  if(control_queue&&xQueueReceive(control_queue,request,0)==pdTRUE)wireless_control(request);
  else if(pending_query[0]&&time_reached(millis(),query_due)){
   if(millis()-query_started<12000){strlcpy(request,pending_query,sizeof(request));pending_query[0]=0;wireless_control(request);}
   else pending_query[0]=0;
  }
  net_poll_internal();vTaskDelay(pdMS_TO_TICKS(30));
 }
}
}
void net_begin(void){
 requested_config.fetch_add(1);s_online=false;
 if(!network_worker){
  control_queue=xQueueCreate(4,256);reply_queue=xQueueCreate(4,1200);
  if(!control_queue||!reply_queue)return;
  xTaskCreate(network_loop,"solo-network",16384,nullptr,1,&network_worker);
 }
}
void net_poll(void){}
bool net_queue_control(const char *line){
 if(!control_queue||strlen(line)>=256)return false;char request[256];strlcpy(request,line,sizeof(request));return xQueueSend(control_queue,request,0)==pdTRUE;
}
bool net_take_reply(char *out,size_t capacity){
 if(!reply_queue||capacity<1200)return false;return xQueueReceive(reply_queue,out,0)==pdTRUE;
}
