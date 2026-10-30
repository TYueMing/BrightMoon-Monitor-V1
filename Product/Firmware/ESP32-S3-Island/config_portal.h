/*
 * Provisioning portal and configuration store for the Solo Island mirror.
 *
 * The device has no keyboard, so the first boot (and a BOOT-button hold at
 * reset) raises an open SoftAP with a small configuration page. Everything the
 * runtime network client needs - WiFi credentials, desktop address/port, access
 * token, HTTPS flag and an optional pinned CA - lives in NVS and is shared
 * through IslandConfig.
 *
 * Optional read-only SD provisioning: a FAT card with
 *   /solo-island/config.json   {"ssid","pass","host","port","token","tls"}
 *   /solo-island/ca.pem        optional PEM root/intermediate CA
 * overrides legacy NVS for that boot (SD is never written by the firmware).
 * A verified USB-provisioned NVS record takes precedence over both. If no
 * valid SD config is present the NVS configuration is used; if neither is
 * valid the sketch opens this portal.
 */
#ifndef CONFIG_PORTAL_H
#define CONFIG_PORTAL_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* NVS namespace and defaults. Keys stay <= 15 chars (NVS limit). */
#define ISLAND_NVS_NAMESPACE "solo-island"
#define ISLAND_DEFAULT_PORT 18765

#define ISLAND_CONFIG_SSID_MAX 33
#define ISLAND_CONFIG_PASS_MAX 65
#define ISLAND_CONFIG_HOST_MAX 64
#define ISLAND_CONFIG_TOKEN_MAX 96
/* NVS strings are capped at 4000 bytes; a local CA PEM is normally ~1.5-2 KB. */
#define ISLAND_CONFIG_CA_MAX 3000

typedef struct {
  char ssid[ISLAND_CONFIG_SSID_MAX];
  char pass[ISLAND_CONFIG_PASS_MAX];
  char host[ISLAND_CONFIG_HOST_MAX]; /* IPv4 address or host name */
  char token[ISLAND_CONFIG_TOKEN_MAX];
  char ca_pem[ISLAND_CONFIG_CA_MAX]; /* pinned CA; empty = TLS without chain check */
  char server_id[33];              /* USB-bound SOLO identity; enables authenticated discovery */
  uint16_t port;
  bool tls;        /* talk https:// to the desktop */
  bool configured; /* all required fields present */
} IslandConfig;

/*
 * Load once at boot: verified USB record, then read-only SD, then legacy NVS.
 * Safe to call more than once; later calls return the
 * cached result. Returns true when ssid, host and token are present.
 */
bool config_bootstrap(void);

/* True when this boot is using the SD card configuration (diagnostics). */
bool config_from_sd(void);

/*
 * Fill *out from the effective (cached) configuration. Returns true only when
 * ssid, host and token are all present. Missing keys yield empty strings / the
 * default port.
 */
bool config_load(IslandConfig *out);
void config_stage_usb(const IslandConfig *value);
bool config_commit_usb(void);

/* Convenience: true when the saved configuration is complete. */
bool config_configured(void);

/* USB-only preference: set when the desktop serial bridge says HELLO while the
 * board is still unconfigured/provisioning.  The next boots skip the WiFi
 * portal and run the display in USB-only mode until BOOT is held or WiFi is
 * configured. */
bool config_usb_preferred(void);
void config_set_usb_preferred(bool value);

/*
 * Return true when GPIO0 (BOOT, active low) is held at boot for >= 3 s.
 * Never blocks when the button is not pressed; a press that is released early
 * returns false.
 */
bool portal_boot_button_held(void);

/*
 * Start the SoftAP + WebServer portal and block forever. Only called at boot
 * (before LVGL init) or when the first STA connect attempt fails at runtime.
 * Saves settings to NVS and restarts the device on POST /save.
 */
void portal_start(void);

/* True once portal_start() has been reached (useful for diagnostics). */
bool portal_active(void);

/* SoftAP SSID chosen for this unit, e.g. "Solo-Island-3F2A". */
const char *portal_ap_ssid(void);

#ifdef __cplusplus
}
#endif

#endif /* CONFIG_PORTAL_H */
