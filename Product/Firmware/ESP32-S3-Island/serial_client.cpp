/*
 * Bidirectional USB serial transport implementation.
 *
 * The line parser is intentionally simple and allocation-free: bytes are
 * appended to one 8 KB static line buffer, CR/LF trimmed, and only frames
 * starting with "SOLO1 " are interpreted. The compact STATE JSON is decoded by
 * the shared net_decode_compact() decoder so USB and HTTP can never disagree
 * about the wire contract.
 *
 * RX/tx ownership: serial_client_poll() runs on the Arduino loop task and is
 * the only place that reads the port or writes READY/ACK/CMD frames (the boot
 * handshake also runs on that task). serial_client_send_action() may be called
 * from the LVGL task; it pushes the id into a small lock-protected ring and
 * returns immediately.
 */
#include <Arduino.h>
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>
#include <freertos/task.h>
#include <ArduinoJson.h>
#include <esp_system.h>

#include "island_model.h"
#include "net_client.h"
#include "serial_client.h"
#include "usb_wifi.h"

namespace {

/* One STATE line is "SOLO1 STATE " + <=8192-byte JSON + CR/LF, so the raw
 * line buffer must be larger than the desktop's 8 KB document hard cap. */
constexpr size_t RX_LINE_MAX = 9216;
constexpr uint32_t READY_INTERVAL_MS = 5000;
constexpr uint32_t BOOT_READY_INTERVAL_MS = 500;
constexpr size_t CMD_DEPTH = 4;
constexpr size_t CMD_ID_MAX = ISLAND_ACTION_ID_MAX; /* 40 including NUL */
constexpr uint32_t BAD_FRAME_LOG_INTERVAL_MS = 5000;
constexpr int RX_BUDGET_PER_POLL = 4096;
constexpr size_t MAX_STATE_JSON = 12288; /* protocol caps at 6144, headroom */

bool s_begun = false;
SemaphoreHandle_t s_state_mux = nullptr;

IslandState s_state;   /* protected by s_state_mux */
IslandState s_staged;  /* loop task only */
bool s_valid = false;  /* protected by s_state_mux */
uint32_t s_rev = 0;
uint32_t s_last_state_ms = 0;
bool s_hello_seen = false;
bool s_usb_only = false;
uint32_t s_last_ready_ms = 0;
uint32_t s_last_bad_log_ms = 0;

char s_line[RX_LINE_MAX];
size_t s_line_len = 0;
bool s_line_overflow = false;

/* CMD queue shared with the LVGL task (see serial_client_send_action). */
portMUX_TYPE s_cmd_mux = portMUX_INITIALIZER_UNLOCKED;
char s_cmd_ids[CMD_DEPTH][CMD_ID_MAX];
uint8_t s_cmd_head = 0;
uint8_t s_cmd_tail = 0;
uint8_t s_cmd_count = 0;
bool s_cmd_logged = false;

char s_result_message[ISLAND_TEXT_MAX];
bool s_result_ok = false;
bool s_result_pending = false;
char s_requests[4][256];
uint8_t s_request_head=0,s_request_count=0;
IslandReply s_replies[4];
uint8_t s_reply_head=0,s_reply_count=0;

void log_throttled(const char *text) {
  const uint32_t now = millis();
  if ((now - s_last_bad_log_ms) < BAD_FRAME_LOG_INTERVAL_MS) {
    return;
  }
  s_last_bad_log_ms = now;
  Serial.printf("[serial] %s\n", text);
}

void send_ready() { Serial.print("SOLO1 READY v1\nSOLO2 READY v2\n");usb_wifi_caps(); }

void send_ack(uint32_t rev, uint32_t age_ms) {
  Serial.printf("SOLO1 ACK %u %u\n", static_cast<unsigned>(rev), static_cast<unsigned>(age_ms));
}

/* Desktop action ids are opaque HMAC tokens (normally 32 lowercase hex). Only
 * accept a JSON-safe, bounded charset so the id can never break the CMD frame. */
bool action_id_is_safe(const char *id) {
  if (id == nullptr || id[0] == '\0') {
    return false;
  }
  for (size_t i = 0; id[i] != '\0'; ++i) {
    if (i + 1 >= CMD_ID_MAX) {
      return false;
    }
    const char c = id[i];
    const bool ok = (c >= '0' && c <= '9') || (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                    c == '-' || c == '_';
    if (!ok) {
      return false;
    }
  }
  return true;
}

void handle_hello() {
  if (!s_hello_seen) {
    Serial.println("[serial] HELLO received");
  }
  s_hello_seen = true;
  send_ready();
  s_last_ready_ms = millis();
}

void handle_state(const char *json, size_t length) {
  if (length == 0 || length > MAX_STATE_JSON) {
    log_throttled("STATE length out of range");
    return;
  }
  if (!net_decode_compact(json, length, &s_staged)) {
    log_throttled("STATE decode failed");
    return;
  }
  const uint32_t now = millis();
  bool committed = false;
  if (s_state_mux != nullptr && xSemaphoreTake(s_state_mux, pdMS_TO_TICKS(50)) == pdTRUE) {
    s_state = s_staged;
    s_state.valid = true;
    s_rev = s_staged.rev;
    s_valid = true;
    xSemaphoreGive(s_state_mux);
    committed = true;
  }
  if (!committed) {
    log_throttled("STATE dropped (mutex)");
    return;
  }
  s_last_state_ms = now;
  s_hello_seen = true;
  /* age_ms is 0: the desktop just pushed this snapshot and the board applied it
   * immediately (there is no transport timestamp in the frame). */
  send_ack(s_rev, 0);
}

void handle_result(const char *json) {
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok) {
    log_throttled("RESULT decode failed");
    return;
  }
  const bool ok = doc["ok"] | false;
  const char *message = doc["message"] | "";
  if (message == nullptr || message[0] == '\0') {
    message = ok ? "操作已完成" : "操作失败";
  }
  strlcpy(s_result_message, message, sizeof(s_result_message));
  s_result_ok = ok;
  s_result_pending = true;
}

void handle_line(char *line) {
  size_t length = strlen(line);
  while (length > 0) {
    const char c = line[length - 1];
    if (c == '\r' || c == '\n' || c == ' ' || c == '\t') {
      line[--length] = '\0';
    } else {
      break;
    }
  }
  if(!strncmp(line,"SOLO3 ",6)){usb_wifi_handle(line);return;}
  if (strncmp(line,"SOLO2 DETAIL ",13)==0 || strncmp(line,"SOLO2 RESULT ",13)==0) {
    JsonDocument doc;
    if(deserializeJson(doc,line+13)!=DeserializationError::Ok)return;
    IslandReply reply{}; reply.detail=strncmp(line,"SOLO2 DETAIL ",13)==0;
    strlcpy(reply.id,doc["id"] | "",sizeof(reply.id));strlcpy(reply.cmd,doc["cmd"] | "",sizeof(reply.cmd));
    strlcpy(reply.status,doc["status"] | "",sizeof(reply.status));strlcpy(reply.kind,doc["kind"] | "",sizeof(reply.kind));
    const char *text=reply.detail?(doc["text"] | ""):(doc["message"] | "");
    if(strlen(text)>=sizeof(reply.text))return; // never approve truncated pages
    strlcpy(reply.text,text,sizeof(reply.text));reply.page=doc["page"] | 0;reply.total=doc["total"] | 0;
    for(JsonVariantConst a:doc["actions"].as<JsonArrayConst>()) {
      const char *op=a | "";
      if(!strcmp(op,"approve"))reply.actions|=1;if(!strcmp(op,"deny"))reply.actions|=2;
      if(!strcmp(op,"snooze"))reply.actions|=4;if(!strcmp(op,"acknowledge"))reply.actions|=8;
    }
    // UI consumes on the same loop under LVGL lock; no worker owns this slot.
    if(s_reply_count<4){s_replies[(s_reply_head+s_reply_count)%4]=reply;s_reply_count++;}return;
  }
  if (length < 7 || strncmp(line, "SOLO1 ", 6) != 0) {
    return; /* firmware logs / terminal noise: ignored by design */
  }
  char *frame = line + 6;
  if (strcmp(frame, "HELLO") == 0) {
    handle_hello();
    return;
  }
  if (strncmp(frame, "STATE ", 6) == 0) {
    if (length > 12) {
      handle_state(frame + 6, length - 12);
    } else {
      log_throttled("STATE frame without JSON");
    }
    return;
  }
  if (strncmp(frame, "RESULT ", 7) == 0) {
    handle_result(frame + 7);
    return;
  }
  /* Unknown SOLO1 frames are ignored so the desktop can add fields later. */
}

void feed_char(char c) {
  if (c == '\n') {
    s_line[s_line_len] = '\0';
    if (!s_line_overflow) {
      handle_line(s_line);
    } else {
      log_throttled("RX line too long, dropped");
    }
    memset(s_line,0,s_line_len); // USB provisioning may contain Wi-Fi credentials.
    s_line_len = 0;
    s_line_overflow = false;
    return;
  }
  if (s_line_overflow) {
    return;
  }
  if (s_line_len >= (RX_LINE_MAX - 1)) {
    s_line_overflow = true;
    s_line_len = 0;
    return;
  }
  s_line[s_line_len++] = c;
}

void read_serial_available() {
  int budget = RX_BUDGET_PER_POLL;
  while (budget-- > 0) {
    const int c = Serial.read();
    if (c < 0) {
      break;
    }
    feed_char(static_cast<char>(c));
  }
}

void flush_cmd_queue() {
  for (;;) {
    char id[CMD_ID_MAX];
    bool have = false;
    taskENTER_CRITICAL(&s_cmd_mux);
    if (s_cmd_count > 0) {
      strlcpy(id, s_cmd_ids[s_cmd_head], sizeof(id));
      s_cmd_head = static_cast<uint8_t>((s_cmd_head + 1) % CMD_DEPTH);
      --s_cmd_count;
      have = true;
    }
    taskEXIT_CRITICAL(&s_cmd_mux);
    if (!have) {
      break;
    }
    Serial.printf("SOLO1 CMD {\"id\":\"%s\"}\n", id);
    if (!s_cmd_logged) {
      s_cmd_logged = true;
      Serial.println("[serial] first CMD forwarded to desktop");
    }
  }
}

}  // namespace

void serial_client_begin(void) {
  if (s_state_mux == nullptr) {
    s_state_mux = xSemaphoreCreateMutex();
    if (s_state_mux == nullptr) {
      Serial.println("[serial] state mutex allocation failed");
    }
  }
  memset(&s_state, 0, sizeof(s_state));
  memset(&s_staged, 0, sizeof(s_staged));
  s_valid = false;
  s_rev = 0;
  s_last_state_ms = 0;
  s_hello_seen = false;
  s_usb_only = false;
  s_line_len = 0;
  s_line_overflow = false;
  s_line[0] = '\0';
  s_result_message[0] = '\0';
  s_result_ok = false;
  s_result_pending = false;
  s_reply_head=s_reply_count=s_request_head=s_request_count=0;
  s_cmd_logged = false;
  s_last_bad_log_ms = 0;

  taskENTER_CRITICAL(&s_cmd_mux);
  s_cmd_head = 0;
  s_cmd_tail = 0;
  s_cmd_count = 0;
  taskEXIT_CRITICAL(&s_cmd_mux);

  s_begun = true;
  s_last_ready_ms = millis();
  send_ready();
  Serial.println("[serial] SOLO1 transport up (115200 8N1)");
}

bool serial_client_boot_handshake(uint32_t timeout_ms) {
  if (!s_begun) {
    return false;
  }
  s_hello_seen = false;
  s_usb_only = false;
  const uint32_t start = millis();
  uint32_t last_ready = millis();
  s_last_ready_ms = last_ready;
  send_ready();

  bool ok = false;
  while ((millis() - start) < timeout_ms) {
    read_serial_available();
    if (s_hello_seen) {
      ok = true;
      break;
    }
    if ((millis() - last_ready) >= BOOT_READY_INTERVAL_MS) {
      last_ready = millis();
      s_last_ready_ms = last_ready;
      send_ready();
    }
    delay(5);
  }

  if (ok) {
    s_usb_only = true;
    s_last_ready_ms = millis();
    send_ready();
    Serial.println("[serial] desktop handshake ok -> USB-only boot");
  } else {
    Serial.println("[serial] boot handshake timed out");
  }
  return ok;
}

void serial_client_poll(void) {
  if (!s_begun) {
    return;
  }
  read_serial_available();
  usb_wifi_poll();
  static char wireless_reply[1200];
  while(net_take_reply(wireless_reply,sizeof(wireless_reply)))handle_line(wireless_reply);
  const uint32_t now = millis();
  if ((now - s_last_ready_ms) >= READY_INTERVAL_MS) {
    s_last_ready_ms = now;
    send_ready();
  }
  flush_cmd_queue();
  for(;;){
    char request[256]; bool have=false;
    taskENTER_CRITICAL(&s_cmd_mux);
    if(s_request_count){strlcpy(request,s_requests[s_request_head],sizeof(request));s_request_head=(s_request_head+1)%4;s_request_count--;have=true;}
    taskEXIT_CRITICAL(&s_cmd_mux);
    if(!have)break;
    if(serial_client_fresh(5000))Serial.print(request);
    else if(!net_queue_control(request)){
      JsonDocument value;const char *raw=strchr(request+6,' ');
      if(raw&&deserializeJson(value,raw+1)==DeserializationError::Ok){
        JsonDocument failed;failed["id"]=value["id"];failed["cmd"]=value["cmd"];
        failed["status"]="rejected";failed["message"]="连接未就绪，请稍后重试";
        String body;serializeJson(failed,body);snprintf(wireless_reply,sizeof(wireless_reply),"SOLO2 RESULT %s",body.c_str());handle_line(wireless_reply);
      }
    }
  }
}

bool serial_client_request(const char *verb,const char *id,const char *op,int page,const char *cmd){
  if(!s_begun||!action_id_is_safe(id))return false;
  if(strcmp(verb,"GET")&&strcmp(verb,"DO")&&strcmp(verb,"QUERY"))return false;
  if(op[0]&&strcmp(op,"approve")&&strcmp(op,"deny")&&strcmp(op,"snooze")&&strcmp(op,"acknowledge"))return false;
  if(cmd[0]&&!action_id_is_safe(cmd))return false;
  char request[256];int n=snprintf(request,sizeof(request),"SOLO2 %s {\"id\":\"%s\",\"op\":\"%s\",\"page\":%d,\"cmd\":\"%s\"}\n",verb,id,op,page,cmd);
  if(n<=0||n>=int(sizeof(request)))return false;
  bool queued=false;taskENTER_CRITICAL(&s_cmd_mux);
  if(s_request_count<4){strlcpy(s_requests[(s_request_head+s_request_count)%4],request,sizeof(request));s_request_count++;queued=true;}
  taskEXIT_CRITICAL(&s_cmd_mux);return queued;
}
void serial_client_new_id(char *out){snprintf(out,33,"%08lx%08lx%08lx%08lx",(unsigned long)esp_random(),(unsigned long)esp_random(),(unsigned long)esp_random(),(unsigned long)esp_random());}
bool serial_client_take_reply(IslandReply *out){if(!s_reply_count)return false;*out=s_replies[s_reply_head];s_reply_head=(s_reply_head+1)%4;s_reply_count--;return true;}

bool serial_client_fresh(uint32_t max_age_ms) {
  if (!s_valid) {
    return false;
  }
  return (millis() - s_last_state_ms) <= max_age_ms;
}

bool serial_client_copy_state(IslandState *out) {
  if (out == nullptr || s_state_mux == nullptr) {
    return false;
  }
  bool copied = false;
  if (xSemaphoreTake(s_state_mux, pdMS_TO_TICKS(50)) == pdTRUE) {
    if (s_valid) {
      memcpy(out, &s_state, sizeof(IslandState));
      copied = true;
    }
    xSemaphoreGive(s_state_mux);
  }
  return copied;
}

uint32_t serial_client_rev(void) { return s_rev; }

int32_t serial_client_age_seconds(void) {
  if (!s_valid) {
    return -1;
  }
  return static_cast<int32_t>((millis() - s_last_state_ms) / 1000u);
}

const char *serial_client_status_text(void) {
  if (!s_begun) {
    return "USB 未启用";
  }
  return s_hello_seen ? "USB 已连接" : "USB 等待";
}

bool serial_client_usb_only(void) { return s_usb_only; }

void serial_client_send_action(const char *action_id) {
  if (!s_begun || !action_id_is_safe(action_id)) {
    log_throttled("CMD dropped: invalid action id");
    return;
  }
  bool queued = false;
  taskENTER_CRITICAL(&s_cmd_mux);
  if (s_cmd_count < CMD_DEPTH) {
    strlcpy(s_cmd_ids[s_cmd_tail], action_id, CMD_ID_MAX);
    s_cmd_tail = static_cast<uint8_t>((s_cmd_tail + 1) % CMD_DEPTH);
    ++s_cmd_count;
    queued = true;
  }
  taskEXIT_CRITICAL(&s_cmd_mux);
  if (!queued) {
    log_throttled("CMD queue full, action dropped");
  }
}

bool serial_client_take_result(char *message, size_t size, bool *ok) {
  if (!s_result_pending) {
    return false;
  }
  if (message != nullptr && size > 0) {
    strlcpy(message, s_result_message, size);
  }
  if (ok != nullptr) {
    *ok = s_result_ok;
  }
  s_result_pending = false;
  return true;
}
