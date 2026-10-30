/*
 * Provisioning portal + configuration store implementation.
 *
 * Design notes:
 *  - The portal is intentionally tiny: one HTML page (< 4 KB, no external
 *    assets) so it works on any phone browser connected to the open SoftAP.
 *  - Secrets (WiFi password, access token, CA certificate) are never echoed
 *    back into the page. Leaving those fields empty keeps the previously
 *    stored value, so a user can fix e.g. the desktop address without
 *    re-typing the token or re-pasting the CA.
 *  - Saving replies with a short confirmation, then reboots. The reboot is
 *    what leaves AP mode and re-enters STA mode with the new configuration.
 *  - Optional read-only SD provisioning runs once at boot in
 *    config_bootstrap(); it overrides legacy NVS, but a verified USB binding
 *    takes priority. The portal always writes NVS.
 */
#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Preferences.h>
#include <SD_MMC.h>
#include <ArduinoJson.h>
#include <esp_mac.h>

#include "config_portal.h"

namespace {

/* Board SD slot, wired as SDMMC 1-bit: CLK=41, CMD=39, D0=40. */
constexpr int SD_PIN_CLK = 41;
constexpr int SD_PIN_CMD = 39;
constexpr int SD_PIN_D0 = 40;
constexpr size_t SD_CONFIG_MAX = 2048;

const char *const SD_CONFIG_PATH = "/solo-island/config.json";
const char *const SD_CA_PATH = "/solo-island/ca.pem";

bool s_portal_active = false;
char s_ap_ssid[24] = {0};
WebServer s_server(80);

IslandConfig s_cache;
bool s_cache_ready = false;
bool s_from_sd = false;
portMUX_TYPE s_config_mux=portMUX_INITIALIZER_UNLOCKED;

/* The form uses {{AP}} / {{SSID}} / {{HOST}} / {{PORT}} / {{TLS_CHECKED}}
 * placeholders. A single flat page well under the 4 KB budget also keeps the
 * String replace cheap. */
const char PORTAL_PAGE[] PROGMEM =
    "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
    "<title>Solo Island 配网</title><style>"
    "body{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:#101827;color:#e8eefc;margin:0;padding:24px 18px}"
    "form{max-width:430px;margin:0 auto}h1{font-size:20px;margin:0 0 6px}"
    "p{color:#9fb0c9;font-size:13px;line-height:1.6;margin:6px 0}"
    "label{display:block;font-size:13px;color:#c7d3e6;margin:18px 0 6px}"
    "input,textarea{width:100%;box-sizing:border-box;padding:11px;border-radius:9px;border:1px solid #2b3a52;background:#182233;color:#e8eefc;font-size:15px}"
    "textarea{font-family:ui-monospace,Consolas,monospace;font-size:12px;resize:vertical}"
    "label.inline{display:flex;align-items:center;gap:9px;margin-top:16px}"
    "label.inline input{width:auto}"
    "button{width:100%;margin-top:26px;padding:13px;border:0;border-radius:9px;background:#649cf0;color:#08111e;font-size:16px;font-weight:600}"
    "small{color:#7d8ea6;font-size:12px;line-height:1.55;display:block;margin-top:18px}"
    "</style></head><body><form method=\"post\" action=\"/save\">"
    "<h1>Solo Island 配网</h1>"
    "<p>连接热点 {{AP}} 后填写电脑信息，保存后设备会自动重启。</p>"
    "<label>WiFi 名称（SSID）</label>"
    "<input name=\"ssid\" value=\"{{SSID}}\" maxlength=\"32\" required>"
    "<label>WiFi 密码</label>"
    "<input name=\"pass\" type=\"password\" maxlength=\"64\" placeholder=\"留空则保留原密码\">"
    "<label>电脑地址（IPv4 或主机名）</label>"
    "<input name=\"host\" value=\"{{HOST}}\" maxlength=\"63\" required placeholder=\"192.168.1.23\">"
    "<label>端口</label>"
    "<input name=\"port\" type=\"number\" min=\"1\" max=\"65535\" value=\"{{PORT}}\" required>"
    "<label class=\"inline\"><input name=\"tls\" type=\"checkbox\" value=\"1\" {{TLS_CHECKED}}>使用 HTTPS（桌面端需同时开启 HTTPS 加密）</label>"
    "<label>CA 证书（PEM，可留空 = 只加密不校验）</label>"
    "<textarea name=\"ca\" maxlength=\"2999\" rows=\"5\" placeholder=\"-----BEGIN CERTIFICATE-----\"></textarea>"
    "<label>访问令牌</label>"
    "<input name=\"token\" maxlength=\"95\" placeholder=\"留空则保留原令牌\">"
    "<button type=\"submit\">保存并重启</button>"
    "<small>令牌与电脑地址见 Solo 设置 → 远程连接 → ESP32 屏幕。已保存的 CA 不会回显，留空保留原值。<br>"
    "如插有 SD 卡（/solo-island/config.json），开机以 SD 配置为准（只读）；本页保存写入 NVS。</small>"
    "</form></body></html>";

String html_escape(const String &in) {
  String out;
  out.reserve(in.length() + 8);
  for (size_t i = 0; i < in.length(); ++i) {
    const char c = in[i];
    switch (c) {
      case '&': out += "&amp;"; break;
      case '<': out += "&lt;"; break;
      case '>': out += "&gt;"; break;
      case '"': out += "&quot;"; break;
      case '\'': out += "&#39;"; break;
      default: out += c; break;
    }
  }
  return out;
}

template <size_t N>
void copy_bounded(char (&dst)[N], const String &src) {
  strlcpy(dst, src.c_str(), N);
}

template <size_t N>
void copy_bounded(char (&dst)[N], const char *src) {
  strlcpy(dst, src != nullptr ? src : "", N);
}

uint16_t parse_port(const String &raw, uint16_t fallback) {
  if (raw.length() == 0) {
    return fallback;
  }
  char *end = nullptr;
  const long value = strtol(raw.c_str(), &end, 10);
  if (end == raw.c_str() || value < 1 || value > 65535) {
    return 0; /* caller treats 0 as invalid */
  }
  return static_cast<uint16_t>(value);
}

bool required_fields_present(const IslandConfig &cfg) {
  return cfg.ssid[0] != '\0' && cfg.host[0] != '\0' && cfg.token[0] != '\0';
}

/* Read a small text file from the mounted SD card. */
String read_sd_text(const char *path, size_t max_bytes) {
  File file = SD_MMC.open(path);
  if (!file || file.isDirectory()) {
    if (file) {
      file.close();
    }
    return String();
  }
  const size_t size = file.size();
  if (size == 0 || size > max_bytes) {
    file.close();
    return String();
  }
  String text;
  text.reserve(size + 1);
  while (file.available()) {
    text += static_cast<char>(file.read());
  }
  file.close();
  return text;
}

/* Try the optional read-only SD provisioning files. Returns true when a valid
 * config.json was found; SD values then override NVS. */
bool sd_load(IslandConfig *out) {
  if (!SD_MMC.setPins(SD_PIN_CLK, SD_PIN_CMD, SD_PIN_D0)) {
    return false;
  }
  if (!SD_MMC.begin("/sdcard", true)) { /* mode1bit = true */
    SD_MMC.end();
    return false;
  }

  bool ok = false;
  const String body = read_sd_text(SD_CONFIG_PATH, SD_CONFIG_MAX);
  if (body.length() > 0) {
    JsonDocument doc;
    const DeserializationError err = deserializeJson(doc, body);
    if (err || !doc.is<JsonObject>()) {
      Serial.printf("[config] SD config.json parse error: %s\n", err.c_str());
    } else {
      memset(out, 0, sizeof(*out));
      out->port = ISLAND_DEFAULT_PORT;
      copy_bounded(out->ssid, doc["ssid"] | "");
      copy_bounded(out->pass, doc["pass"] | "");
      copy_bounded(out->host, doc["host"] | "");
      copy_bounded(out->token, doc["token"] | "");
      out->tls = doc["tls"] | false;
      const int port = doc["port"] | ISLAND_DEFAULT_PORT;
      if (port >= 1 && port <= 65535) {
        out->port = static_cast<uint16_t>(port);
      }
      out->configured = required_fields_present(*out);
      ok = out->configured;
      if (!ok) {
        Serial.println("[config] SD config.json is missing ssid/host/token");
      }
    }
  } else {
    Serial.println("[config] SD card mounted but /solo-island/config.json is missing");
  }

  if (ok) {
    const String pem = read_sd_text(SD_CA_PATH, ISLAND_CONFIG_CA_MAX);
    if (pem.length() > 0) {
      copy_bounded(out->ca_pem, pem);
      Serial.printf("[config] SD ca.pem loaded (%u bytes)\n", static_cast<unsigned>(pem.length()));
    }
  }
  SD_MMC.end();
  return ok;
}

/* Load the NVS configuration (what the portal saves). */
bool nvs_load(IslandConfig *out) {
  memset(out, 0, sizeof(*out));
  out->port = ISLAND_DEFAULT_PORT;

  Preferences prefs;
  if (!prefs.begin(ISLAND_NVS_NAMESPACE, true)) {
    Serial.println("[config] NVS namespace open failed");
    return false;
  }
  String usb=prefs.getString("usb_config","");
  if(usb.length()){
    JsonDocument doc;
    if(deserializeJson(doc,usb)==DeserializationError::Ok){
      copy_bounded(out->ssid,doc["ssid"] | "");copy_bounded(out->pass,doc["pass"] | "");
      copy_bounded(out->host,doc["host"] | "");copy_bounded(out->token,doc["token"] | "");
      copy_bounded(out->ca_pem,doc["ca"] | "");copy_bounded(out->server_id,doc["server_id"] | "");
      out->port=doc["port"] | ISLAND_DEFAULT_PORT;out->tls=true;
      out->configured=required_fields_present(*out)&&strlen(out->server_id)==32&&out->ca_pem[0];
      if(out->configured){prefs.end();return true;}
    }
  }
  const String ssid = prefs.getString("wifi_ssid", "");
  const String pass = prefs.getString("wifi_pass", "");
  const String host = prefs.getString("host", "");
  const String token = prefs.getString("token", "");
  const String ca = prefs.getString("ca_pem", "");
  const uint16_t port = prefs.getUShort("port", ISLAND_DEFAULT_PORT);
  const bool tls = prefs.getBool("tls", false);
  const bool configured = prefs.getBool("configured", false);
  prefs.end();

  copy_bounded(out->ssid, ssid);
  copy_bounded(out->pass, pass);
  copy_bounded(out->host, host);
  copy_bounded(out->token, token);
  copy_bounded(out->ca_pem, ca);
  out->port = (port == 0) ? ISLAND_DEFAULT_PORT : port;
  out->tls = tls;
  out->configured = configured && required_fields_present(*out);
  return out->configured;
}

void send_plain(int code, const String &text) {
  s_server.send(code, "text/plain; charset=utf-8", text);
}

void send_page() {
  IslandConfig cfg;
  config_load(&cfg);

  String page(PORTAL_PAGE);
  page.replace("{{AP}}", html_escape(String(s_ap_ssid)));
  page.replace("{{SSID}}", html_escape(String(cfg.ssid)));
  page.replace("{{HOST}}", html_escape(String(cfg.host)));
  page.replace("{{PORT}}", String(cfg.port ? cfg.port : ISLAND_DEFAULT_PORT));
  page.replace("{{TLS_CHECKED}}", cfg.tls ? "checked" : "");

  s_server.sendHeader("Cache-Control", "no-store");
  s_server.send(200, "text/html; charset=utf-8", page);
}

void handle_root() { send_page(); }

void handle_save() {
  IslandConfig current;
  config_load(&current);

  String ssid = s_server.arg("ssid");
  String pass = s_server.arg("pass");
  String host = s_server.arg("host");
  String token = s_server.arg("token");
  String ca = s_server.arg("ca");
  ssid.trim();
  host.trim();
  token.trim();
  ca.trim();
  const bool tls = s_server.hasArg("tls");

  if (ssid.length() == 0) {
    send_plain(400, "请填写 WiFi 名称（SSID）");
    return;
  }
  if (host.length() == 0) {
    send_plain(400, "请填写电脑地址（IPv4 或主机名）");
    return;
  }
  const uint16_t port =
      parse_port(String(s_server.arg("port")), current.port ? current.port : ISLAND_DEFAULT_PORT);
  if (port == 0) {
    send_plain(400, "端口需在 1-65535 之间");
    return;
  }
  /* Empty secret fields keep the stored value (see file header). */
  if (token.length() == 0 && current.token[0] != '\0') {
    token = String(current.token);
  }
  if (token.length() == 0) {
    send_plain(400, "请填写访问令牌（Solo 设置 → 远程连接 → ESP32 屏幕）");
    return;
  }
  if (pass.length() == 0 && current.pass[0] != '\0') {
    pass = String(current.pass);
  }
  if (ca.length() == 0) {
    ca = String(current.ca_pem); /* keep the previously saved CA */
  } else {
    if (ca.length() >= ISLAND_CONFIG_CA_MAX) {
      send_plain(400, "CA 证书过长（上限约 3 KB）");
      return;
    }
    if (ca.indexOf("BEGIN CERTIFICATE") < 0) {
      send_plain(400, "CA 证书需为 PEM 格式（-----BEGIN CERTIFICATE-----）");
      return;
    }
    ca += '\n';
  }

  Preferences prefs;
  if (!prefs.begin(ISLAND_NVS_NAMESPACE, false)) {
    send_plain(500, "无法保存配置，请重启后重试");
    return;
  }
  prefs.putString("wifi_ssid", ssid);
  prefs.putString("wifi_pass", pass);
  prefs.putString("host", host);
  prefs.putUShort("port", port);
  prefs.putString("token", token);
  prefs.putString("ca_pem", ca);
  prefs.putBool("tls", tls);
  prefs.putBool("configured", true);
  prefs.putBool("usb_preferred", false); /* WiFi config wins over USB-only */
  prefs.remove("usb_config"); /* Explicit manual provisioning replaces the USB binding. */
  prefs.end();

  Serial.printf("[portal] saved: ssid=%s host=%s port=%u tls=%u token_len=%u ca_len=%u\n", ssid.c_str(),
                host.c_str(), static_cast<unsigned>(port), static_cast<unsigned>(tls),
                static_cast<unsigned>(token.length()), static_cast<unsigned>(ca.length()));
  send_plain(200, "已保存，正在重启…");
  delay(1200);
  ESP.restart();
}

void build_ap_ssid() {
  /* Read the factory MAC from eFuse: this works before WiFi is initialised,
   * unlike WiFi.macAddress() (which can return zeros in the portal path). */
  uint8_t mac[6] = {0};
  esp_read_mac(mac, ESP_MAC_WIFI_STA);
  snprintf(s_ap_ssid, sizeof(s_ap_ssid), "Solo-Island-%02X%02X", mac[4], mac[5]);
}

}  // namespace

bool config_bootstrap(void) {
  if (s_cache_ready) {
    return s_cache.configured;
  }
  memset(&s_cache, 0, sizeof(s_cache));
  s_cache.port = ISLAND_DEFAULT_PORT;
  s_cache_ready = true;

  // An explicitly verified USB binding takes precedence over legacy SD setup.
  if(nvs_load(&s_cache)&&s_cache.server_id[0])return true;
  if (sd_load(&s_cache)) {
    s_from_sd = true;
    Serial.println("[config] using SD card");
    return true;
  }
  memset(&s_cache, 0, sizeof(s_cache));
  s_cache.port = ISLAND_DEFAULT_PORT;
  const bool ok = nvs_load(&s_cache);
  Serial.printf("[config] using NVS (%s)\n", ok ? "configured" : "empty");
  return ok;
}

bool config_from_sd(void) { return s_from_sd; }

bool config_load(IslandConfig *out) {
  if (out == nullptr) {
    return false;
  }
  if (!s_cache_ready) {
    config_bootstrap();
  }
  taskENTER_CRITICAL(&s_config_mux);*out = s_cache;taskEXIT_CRITICAL(&s_config_mux);
  return out->configured;
}

void config_stage_usb(const IslandConfig *value){
  if(!value)return;
  taskENTER_CRITICAL(&s_config_mux);s_cache=*value;s_cache_ready=true;s_from_sd=false;taskEXIT_CRITICAL(&s_config_mux);
}
bool config_commit_usb(void){
  static IslandConfig value;config_load(&value);if(!value.configured||!value.server_id[0])return false;
  JsonDocument doc;doc["ssid"]=value.ssid;doc["pass"]=value.pass;doc["host"]=value.host;doc["port"]=value.port;
  doc["token"]=value.token;doc["ca"]=value.ca_pem;doc["server_id"]=value.server_id;
  String body;serializeJson(doc,body);if(body.length()>3900)return false;
  Preferences prefs;if(!prefs.begin(ISLAND_NVS_NAMESPACE,false))return false;
  bool ok=prefs.putString("usb_config",body)==body.length();prefs.end();return ok;
}

bool config_configured(void) {
  IslandConfig cfg;
  return config_load(&cfg);
}

bool config_usb_preferred(void) {
  Preferences prefs;
  if (!prefs.begin(ISLAND_NVS_NAMESPACE, true)) {
    return false;
  }
  const bool value = prefs.getBool("usb_preferred", false);
  prefs.end();
  return value;
}

void config_set_usb_preferred(bool value) {
  Preferences prefs;
  if (!prefs.begin(ISLAND_NVS_NAMESPACE, false)) {
    return;
  }
  prefs.putBool("usb_preferred", value);
  prefs.end();
}

bool portal_boot_button_held(void) {
  /* GPIO0 is the BOOT strap pin and is pulled up on the board. */
  pinMode(0, INPUT_PULLUP);
  delay(20);
  if (digitalRead(0) != LOW) {
    return false;
  }
  Serial.print("[portal] BOOT pressed, hold 3s to force configuration mode");
  const uint32_t start = millis();
  while (digitalRead(0) == LOW) {
    if (millis() - start >= 3000) {
      Serial.println(" ...");
      return true;
    }
    delay(20);
  }
  Serial.println(" (released early, normal boot)");
  return false;
}

void portal_start(void) {
  s_portal_active = true;
  /* A user-forced portal always clears the USB-only preference first. */
  config_set_usb_preferred(false);
  build_ap_ssid();

  WiFi.persistent(false);
  WiFi.mode(WIFI_AP);
  delay(200); /* let the AP interface come up */
  const bool ok = WiFi.softAP(s_ap_ssid);

  Serial.println();
  Serial.println("======================================");
  Serial.println("[portal] configuration mode");
  Serial.printf("[portal] AP SSID : %s (open)\n", s_ap_ssid);
  Serial.printf("[portal] SoftAP  : %s\n", ok ? "ok" : "FAILED");
  if (ok) {
    Serial.print("[portal] page URL: http://");
    Serial.println(WiFi.softAPIP());
  }
  Serial.println("[portal] 推荐在 SOLO 远程连接 → ESP32 屏幕中通过 USB 配置 WiFi");
  Serial.println("======================================");

  s_server.on("/", HTTP_GET, handle_root);
  s_server.on("/save", HTTP_POST, handle_save);
  s_server.onNotFound(handle_root); /* captive-portal probes land on the form */
  s_server.begin();

  /* A desktop USB serial bridge repeats SOLO1 HELLO until READY.  If the board
   * reached the portal without Wi-Fi configuration but the cable/desktop are
   * already active, reboot into the USB-only boot handshake instead of
   * requiring a manual reset.  BOOT-held forced reconfiguration still wins only
   * until the desktop starts HELLO; close the serial port to use the portal. */
  char hello_line[32] = {0};
  size_t hello_len = 0;
  for (;;) {
    s_server.handleClient();
    while (Serial.available()) {
      const char c = static_cast<char>(Serial.read());
      if (c == '\r' || c == '\n') {
        hello_line[hello_len] = '\0';
        if (strcmp(hello_line, "SOLO1 HELLO") == 0) {
          Serial.println("[portal] desktop SOLO1 HELLO -> reboot for USB mode");
          config_set_usb_preferred(true);
          delay(200);
          ESP.restart();
        }
        hello_len = 0;
      } else if (hello_len + 1 < sizeof(hello_line)) {
        hello_line[hello_len++] = c;
      } else {
        hello_len = 0; /* discard an overlong/unknown line */
      }
    }
    delay(2);
  }
}

bool portal_active(void) { return s_portal_active; }

const char *portal_ap_ssid(void) { return s_ap_ssid; }
