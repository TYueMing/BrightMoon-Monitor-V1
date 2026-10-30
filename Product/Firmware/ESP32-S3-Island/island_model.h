/*
 * Wire contract between the desktop bridge and the ESP32 renderer.
 *
 * The desktop (monitor/esp32/compact.py) already resolves every visual
 * decision: status text, colours, elapsed seconds, progress and attention.
 * The firmware only decodes this struct and draws it, so the screen can
 * never invent a percentage or leak a field the desktop did not send.
 */
#ifndef ISLAND_MODEL_H
#define ISLAND_MODEL_H

#include <stdbool.h>
#include <stdint.h>

#define ISLAND_MAX_TASKS 5
#define ISLAND_MAX_ACTIONS 3
#define ISLAND_TITLE_MAX 96
#define ISLAND_TEXT_MAX 220
#define ISLAND_LABEL_MAX 32
#define ISLAND_SOURCE_MAX 24
#define ISLAND_ACTION_ID_MAX 40

/* One whitelisted desktop action attached to a task over the USB serial
 * transport (HTTP polling leaves action_count at 0). The id is an opaque
 * desktop-side HMAC; the firmware only echoes it back. */
typedef struct {
  char id[ISLAND_ACTION_ID_MAX];   /* 32 lowercase hex, opaque */
  char kind[16];                   /* approve / deny / reject */
  char label[40];                  /* localized button label, UTF-8 */
} IslandAction;

typedef struct {
  char key[24];
  char app[20];
  char name[ISLAND_SOURCE_MAX];
  char dev[ISLAND_SOURCE_MAX];     /* device label: "本机" or a peer name */
  char device_id[ISLAND_SOURCE_MAX]; /* stable grouping key, names can collide */
  char glyph[4];                   /* 1-2 ASCII chars drawn in the task dot */
  char status[28];                 /* raw status, for debugging only */
  char label[ISLAND_LABEL_MAX];    /* localized status label from desktop */
  char color[8];                   /* "#RRGGBB" status colour */
  char title[ISLAND_TITLE_MAX];
  char summary[ISLAND_TEXT_MAX];
  char plan_label[ISLAND_LABEL_MAX];
  char step[ISLAND_TITLE_MAX];
  bool error;
  bool final;
  bool remote;
  bool has_attention;
  char attention_title[ISLAND_LABEL_MAX];
  char attention_reason[ISLAND_TEXT_MAX];
  int32_t elapsed_s;               /* running seconds reported by desktop */
  int32_t plan_done;               /* -1 when the source did not report */
  int32_t plan_total;
  int32_t action_count;            /* 0..ISLAND_MAX_ACTIONS, serial only */
  IslandAction actions[ISLAND_MAX_ACTIONS];
  char control[40];               /* Request-bound SOLO2 proposal handle. */
} IslandTask;

typedef struct { char id[24]; char name[24]; bool online; } IslandDevice;
typedef struct { char key[24]; char title[96]; char summary[220]; char dev[24]; char at[24]; char control[40]; bool active; } IslandReminder;
typedef struct {
  char id[40]; char cmd[40]; char status[24]; char text[384]; char kind[16];
  int32_t page, total; uint8_t actions; /* approve=1 deny=2 snooze=4 acknowledge=8 */
  bool detail;
} IslandReply;

typedef struct {
  bool valid;                      /* at least one successful reply */
  int32_t version;
  uint32_t rev;
  char epoch[16];
  char clock[8];                   /* "14:32" desktop local time */
  char date[24];                   /* "10-29 周三" */
  bool desktop_visible;
  bool attention;
  char privacy[16];                /* "summary" / "status_only" */
  char control_mode[16];           /* enabled / disabled / privacy; absent on older hosts */
  char mode[8];                    /* "active" / "idle" */
  char focus[24];

  /* Theme resolved by the desktop, matching the phone viewer. */
  char bg[8];
  char fg[8];
  char muted[8];
  char panel[8];
  int32_t panel_alpha;             /* 0-100 */

  bool idle_preview;
  int32_t idle_count;
  char idle_title[ISLAND_TITLE_MAX];
  char idle_at[24];
  char idle_body[220];
  int32_t device_count;
  IslandDevice devices[6];
  int32_t reminder_count, reminder_total, task_total;
  IslandReminder reminders[5];

  int32_t task_count;
  IslandTask tasks[ISLAND_MAX_TASKS];
} IslandState;

#endif
