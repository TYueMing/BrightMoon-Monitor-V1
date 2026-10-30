// Provisioning runs on the serial owner. Network verification runs on its worker.
#include <Arduino.h>
#include <ArduinoJson.h>
#include <WiFi.h>
#include <esp_mac.h>
#include "usb_wifi.h"
#include "config_portal.h"
#include "net_client.h"
static char board[13],request[33];
static bool scanning=false,configuring=false,reported_ip=false;
static uint32_t started=0;
static IslandConfig previous,candidate;
const char *usb_wifi_board(){
 if(!board[0]){uint8_t mac[6];esp_read_mac(mac,ESP_MAC_WIFI_STA);snprintf(board,sizeof(board),"%02x%02x%02x%02x%02x%02x",mac[0],mac[1],mac[2],mac[3],mac[4],mac[5]);}
 return board;
}
void usb_wifi_caps(){Serial.printf("SOLO3 CAPS {\"board\":\"%s\",\"wifi\":true,\"version\":1}\n",usb_wifi_board());}
static void report(const char *state){Serial.printf("SOLO3 WIFI {\"id\":\"%s\",\"state\":\"%s\"}\n",request,state);}
static bool field(JsonDocument &doc,const char *key,char *out,size_t capacity){
 if(!doc[key].is<const char*>())return false;const char *s=doc[key];if(strlen(s)>=capacity)return false;strlcpy(out,s,capacity);return true;
}
void usb_wifi_handle(const char *line){
 const char *raw=strchr(line+6,' ');if(!raw)return;
 JsonDocument doc;if(deserializeJson(doc,raw+1)!=DeserializationError::Ok)return;
 const char *id=doc["id"] | "";if(strlen(id)!=32)return;for(int i=0;i<32;i++)if(!isxdigit(id[i]))return;
 if(scanning||configuring)return;strlcpy(request,id,sizeof(request));
 if(!strncmp(line,"SOLO3 SCAN ",11)){
  WiFi.mode(WIFI_STA);WiFi.scanDelete();started=millis();
  int status=WiFi.scanNetworks(true,true);scanning=status==WIFI_SCAN_RUNNING||status>=0;
  if(!scanning)report("error");return;
 }
 if(strncmp(line,"SOLO3 CONFIG ",13))return;
 memset(&candidate,0,sizeof(candidate));
 bool ok=field(doc,"ssid",candidate.ssid,sizeof(candidate.ssid))&&field(doc,"pass",candidate.pass,sizeof(candidate.pass))&&
  field(doc,"host",candidate.host,sizeof(candidate.host))&&field(doc,"token",candidate.token,sizeof(candidate.token))&&
  field(doc,"ca",candidate.ca_pem,sizeof(candidate.ca_pem))&&field(doc,"server_id",candidate.server_id,sizeof(candidate.server_id));
 int port=doc["port"] | 0;
 size_t passlen=strlen(candidate.pass);
 ok=ok&&candidate.ssid[0]&&strlen(candidate.token)==64&&strlen(candidate.server_id)==32&&strstr(candidate.ca_pem,"BEGIN CERTIFICATE")&&port>=1024&&port<=65535&&doc["tls"]==true;
 ok=ok&&(passlen==0||(passlen>=8&&passlen<=64));
 IPAddress ip;ok=ok&&ip.fromString(candidate.host);
 for(size_t i=0;i<strlen(candidate.token);i++)ok=ok&&isxdigit(candidate.token[i]);
 for(size_t i=0;i<strlen(candidate.server_id);i++)ok=ok&&isxdigit(candidate.server_id[i]);
 if(passlen==64)for(size_t i=0;i<passlen;i++)ok=ok&&isxdigit(candidate.pass[i]);
 if(!ok){report("error");memset(&candidate,0,sizeof(candidate));return;}
 candidate.port=port;candidate.tls=candidate.configured=true;
 config_load(&previous);config_stage_usb(&candidate);net_begin();
 configuring=true;reported_ip=false;started=millis();report("connecting");
}
void usb_wifi_poll(){
 if(scanning){
  int n=WiFi.scanComplete();
  if(n>=0){
   JsonDocument doc;doc["id"]=request;JsonArray networks=doc["networks"].to<JsonArray>();
   for(int i=0;i<n&&networks.size()<20;i++){
    if(!WiFi.SSID(i).length())continue;
    JsonObject entry=networks.add<JsonObject>();entry["ssid"]=WiFi.SSID(i);entry["rssi"]=WiFi.RSSI(i);
    entry["open"]=WiFi.encryptionType(i)==WIFI_AUTH_OPEN;
    wifi_auth_mode_t auth=WiFi.encryptionType(i);
    entry["supported"]=auth==WIFI_AUTH_OPEN||auth==WIFI_AUTH_WPA_PSK||auth==WIFI_AUTH_WPA2_PSK||auth==WIFI_AUTH_WPA_WPA2_PSK||auth==WIFI_AUTH_WPA3_PSK||auth==WIFI_AUTH_WPA2_WPA3_PSK;
   }
   Serial.print("SOLO3 SCAN ");serializeJson(doc,Serial);Serial.println();WiFi.scanDelete();scanning=false;
  }else if(n==WIFI_SCAN_FAILED||millis()-started>15000){scanning=false;report("error");}
 }
 if(configuring){
  if(WiFi.status()==WL_CONNECTED&&!reported_ip){reported_ip=true;report("verifying");}
  // net_begin clears online immediately; only a fresh authenticated response succeeds.
  if(net_online()&&net_last_sync_seconds()>=0&&net_last_sync_seconds()<3){
   if(config_commit_usb()){report("saved");configuring=false;memset(&previous,0,sizeof(previous));memset(&candidate,0,sizeof(candidate));return;}
   started=millis()-60000;
  }
  if(millis()-started>55000){
   configuring=false;config_stage_usb(&previous);net_begin();report("error");
   memset(&previous,0,sizeof(previous));memset(&candidate,0,sizeof(candidate));
  }
 }
}
