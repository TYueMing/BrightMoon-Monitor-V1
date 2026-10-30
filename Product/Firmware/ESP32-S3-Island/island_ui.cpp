/* Fixed-height ESP32 island, native LVGL 8.4. All calls hold the LVGL lock.
 * Objects are allocated once. Task identity and command identity are never
 * inferred from a display index when a snapshot or a remote reply arrives. */
#include "lvgl.h"
#include "island_ui.h"
#include "island_icons.h"
#include "serial_client.h"
#include <cstdio>
#include <cstring>
#include <algorithm>
LV_FONT_DECLARE(island_cjk_16);
static const lv_font_t *font=&island_cjk_16;
static lv_font_t ui_font;
// The bundled GB2312 font has U+30FB but not U+00B7. Alias the visually
// equivalent separator without substituting any approval parameter text.
static bool glyph_dsc(const lv_font_t *,lv_font_glyph_dsc_t *d,uint32_t c,uint32_t next){return island_cjk_16.get_glyph_dsc(&island_cjk_16,d,c==0xb7?0x30fb:c,next==0xb7?0x30fb:next);}
static const uint8_t *glyph_bitmap(const lv_font_t *,uint32_t c){return island_cjk_16.get_glyph_bitmap(&island_cjk_16,c==0xb7?0x30fb:c);}
static const uint32_t BG=0xedf1f5, FG=0x263449, MUTED=0x63758b, BLUE=0x3778d1, AMBER=0xc98a27, GREEN=0x269579, RED=0xd45a63;
static IslandState state{};
static bool online=false, built=false, detailed=false, power_confirmed=false;
static char link_text[48]="USB 等待";
struct Group {char id[24];char name[24];char selected[24];bool online;};
static Group groups[6]{};
static int group_count=1, focus_group=0, other_group=1;
static char selected_device[24]="local";
static lv_obj_t *screen,*header,*connection,*local_pill,*remote_pill,*content,*small_content,*small_label,*small_status;
static lv_obj_t *title,*summary,*status_label,*timer_label,*brand,*rings[3],*ring_images[3],*ring_marks[3],*focus_dots[3];
static lv_obj_t *plan_nodes[5],*plan_lines[4];
static lv_obj_t *reminder_button,*reminder_label,*counter_button,*counter_label;
static lv_obj_t *overlay,*overlay_title,*overlay_body,*body_label,*buttons[5],*button_labels[5],*cards[3],*card_names[3],*card_states[3],*card_icons[3];
static lv_obj_t *power_dialog=nullptr,*power_title,*power_confirm,*power_cancel;
static int ring_tasks[3]={-1,-1,-1};
static int layout_target=-1,layout_value=0;
static bool switching=false;
static lv_timer_t *tick_timer;
static int last_pulse=-1;
static uint32_t theme_bg=BG,theme_panel=0xffffff,theme_fg=FG,theme_muted=MUTED;
static uint32_t sent_at=0;
enum Modal {NONE, TASKS, REMINDERS, DEVICES, DOCUMENT, CONFIRM, PENDING, RESULT};
static Modal modal=NONE;
static int list_page=0,list_count=0;
static char list_keys[6][24]{};
static char request_id[40]="",command_id[40]="",decision[16]="",request_kind[16]="",request_title[128]="";
static int doc_page=0,doc_total=0,requested_page=0;
static bool visited[120]{},waiting_page=false;
static uint8_t actions=0;
static char result_status[24]="",document_text[384]="";
static bool result_query=false;
static void render();
static void render_modal();
static void modal_button(lv_event_t *event);
static void pill_click(lv_event_t *event);
static void ring_click(lv_event_t *event);
static void card_click(lv_event_t *event);
static void power_cancel_cb(lv_event_t *);
static void power_confirm_cb(lv_event_t *);

static void copy(char *dst,size_t n,const char *src){if(!src)src="";size_t len=strlen(src);if(len>=n){len=n-1;while(len&&((unsigned char)src[len]&0xc0)==0x80)--len;}memcpy(dst,src,len);dst[len]=0;}
static void text(lv_obj_t *obj,const char *value){if(strcmp(lv_label_get_text(obj),value))lv_label_set_text(obj,value);}
static uint32_t rgb(const char *value,uint32_t fallback){unsigned parsed=0;return value&&strlen(value)==7&&value[0]=='#'&&sscanf(value+1,"%x",&parsed)==1?parsed:fallback;}
static void apply_theme(){
 uint32_t bg=rgb(state.bg,BG),panel=rgb(state.panel,0xffffff);int alpha=state.panel[0]?std::max(0,std::min(100,int(state.panel_alpha))):100;
 uint32_t mixed=0;for(int shift=0;shift<=16;shift+=8)mixed|=((((panel>>shift)&255)*alpha+((bg>>shift)&255)*(100-alpha))/100)<<shift;
 bool light=(((mixed>>16)&255)*212+((mixed>>8)&255)*715+(mixed&255)*73)>150000;
 theme_bg=bg;theme_panel=mixed;theme_fg=light?FG:0xeff4ff;theme_muted=light?MUTED:0xacbdd2;
 lv_obj_set_style_bg_color(screen,lv_color_hex(bg),0);
 for(auto *o:{local_pill,remote_pill,content,small_content})lv_obj_set_style_bg_color(o,lv_color_hex(mixed),0);
 lv_obj_set_style_text_color(title,lv_color_hex(theme_fg),0);
 for(auto *o:{summary,timer_label,small_label})lv_obj_set_style_text_color(o,lv_color_hex(theme_muted),0);
 bool bg_light=(((bg>>16)&255)*212+((bg>>8)&255)*715+(bg&255)*73)>150000;
 for(auto *o:{header,connection,counter_label})lv_obj_set_style_text_color(o,lv_color_hex(bg_light?MUTED:0xacbdd2),0);
}
static void hide(lv_obj_t *obj,bool hidden){if(hidden)lv_obj_add_flag(obj,LV_OBJ_FLAG_HIDDEN);else lv_obj_clear_flag(obj,LV_OBJ_FLAG_HIDDEN);}
static lv_obj_t *box(lv_obj_t *parent,int x,int y,int w,int h,int radius=0,uint32_t color=0xffffff){
 lv_obj_t *o=lv_obj_create(parent);lv_obj_remove_style_all(o);lv_obj_set_pos(o,x,y);lv_obj_set_size(o,w,h);
 lv_obj_clear_flag(o,LV_OBJ_FLAG_SCROLLABLE);lv_obj_clear_flag(o,LV_OBJ_FLAG_CLICKABLE);
 lv_obj_set_style_radius(o,radius,0);lv_obj_set_style_bg_color(o,lv_color_hex(color),0);lv_obj_set_style_bg_opa(o,255,0);return o;
}
static lv_obj_t *label(lv_obj_t *parent,int x,int y,int w,const char *value,uint32_t color=FG,const lv_font_t *f=nullptr){
 lv_obj_t *o=lv_label_create(parent);lv_obj_set_pos(o,x,y);lv_obj_set_width(o,w);lv_obj_set_style_text_font(o,f?f:font,0);
 lv_obj_set_style_text_color(o,lv_color_hex(color),0);lv_label_set_long_mode(o,LV_LABEL_LONG_DOT);lv_label_set_text(o,value);return o;
}
static lv_obj_t *button(lv_obj_t *parent,int x,int y,int w,int h,lv_event_cb_t cb,intptr_t id=0){
 auto *o=box(parent,x,y,w,h,h/2);lv_obj_add_flag(o,LV_OBJ_FLAG_CLICKABLE);lv_obj_add_event_cb(o,cb,LV_EVENT_CLICKED,(void*)id);return o;
}
static int task_index(const char *key){for(int i=0;i<state.task_count;i++)if(!strcmp(state.tasks[i].key,key))return i;return -1;}
static int reminder_index(const char *key){for(int i=0;i<state.reminder_count;i++)if(!strcmp(state.reminders[i].key,key))return i;return -1;}
static bool in_group(int i,int group){return !strcmp(state.tasks[i].device_id,groups[group].id);}
static int group_tasks(int group,int *out){int n=0;for(int i=0;i<state.task_count;i++)if(in_group(i,group))out[n++]=i;return n;}
static int selected_task(){int i=task_index(groups[focus_group].selected);return i>=0&&in_group(i,focus_group)?i:-1;}
static uint32_t task_color(const IslandTask &t){if(t.error||!online)return MUTED;if(!strcmp(t.status,"completed"))return GREEN;if(!strcmp(t.status,"failed"))return RED;if(t.final)return MUTED;return t.has_attention?AMBER:BLUE;}
static void update_groups(){
 Group previous[6];memcpy(previous,groups,sizeof(groups));int old_count=group_count;
 memset(groups,0,sizeof(groups));group_count=0;
 auto add=[&](const char *id,const char *name,bool connected){
  for(int i=0;i<group_count;i++)if(!strcmp(groups[i].id,id))return;
  if(group_count>=6)return;auto &g=groups[group_count++];copy(g.id,sizeof(g.id),id);copy(g.name,sizeof(g.name),name);g.online=connected;
  for(int i=0;i<old_count;i++)if(!strcmp(previous[i].id,id))copy(g.selected,sizeof(g.selected),previous[i].selected);
 };
 add("local","本机",true);
 for(int i=0;i<state.device_count;i++)add(state.devices[i].id,state.devices[i].name,state.devices[i].online);
 for(int i=0;i<state.task_count;i++){auto &t=state.tasks[i];if(!t.device_id[0])copy(t.device_id,sizeof(t.device_id),t.remote?t.dev:"local");add(t.device_id,t.dev,!t.error);}
 focus_group=0;for(int i=0;i<group_count;i++){
  if(!strcmp(groups[i].id,selected_device))focus_group=i;
  int indices[5];int n=group_tasks(i,indices);int selected=task_index(groups[i].selected);
  if(selected<0||!in_group(selected,i))copy(groups[i].selected,24,n?state.tasks[indices[0]].key:"");
 }
 copy(selected_device,sizeof(selected_device),groups[focus_group].id);
 other_group=focus_group==0?std::min(other_group,group_count-1):focus_group;
 if(other_group<1&&group_count>1)other_group=1;
}
static void layout_exec(void *,int32_t value){
 layout_value=value;
 if(group_count<=1){lv_obj_set_pos(local_pill,16,34);lv_obj_set_size(local_pill,608,98);return;}
 int local_width=498-(400*value/1000);
 lv_obj_set_pos(local_pill,16,34);lv_obj_set_size(local_pill,local_width,98);
 lv_obj_set_pos(remote_pill,16+local_width+12,34);lv_obj_set_size(remote_pill,608-12-local_width,98);
}
static void layout_done(lv_anim_t *){switching=false;lv_obj_set_style_opa(content,255,0);}
static void set_layout(bool animate){
 int target=focus_group?1000:0;bool changed=target!=layout_target;layout_target=target;
 hide(remote_pill,group_count<2);
 auto *big=focus_group?remote_pill:local_pill;
 auto *small=focus_group?local_pill:remote_pill;
 if(lv_obj_get_parent(content)!=big)lv_obj_set_parent(content,big);
 if(lv_obj_get_parent(small_content)!=small)lv_obj_set_parent(small_content,small);
 hide(small_content,group_count<2);
 int w=group_count>1?498:608;lv_obj_set_size(content,w,98);
 if(animate&&changed&&group_count>1){
  lv_anim_del(nullptr,layout_exec);switching=true;lv_obj_set_style_opa(content,0,0);
  lv_anim_t a;lv_anim_init(&a);lv_anim_set_exec_cb(&a,layout_exec);lv_anim_set_values(&a,layout_value,target);
  lv_anim_set_time(&a,460);lv_anim_set_path_cb(&a,lv_anim_path_ease_in_out);lv_anim_set_ready_cb(&a,layout_done);lv_anim_start(&a);
 }else if(!switching)layout_exec(nullptr,target);
}
static void request_page(int page){
 if(!online){copy(document_text,sizeof(document_text),"连接中断，无法读取请求");modal=RESULT;render_modal();return;}
 requested_page=page;waiting_page=true;sent_at=lv_tick_get();
 if(!serial_client_request("GET",request_id,"",page,"")){waiting_page=false;copy(document_text,sizeof(document_text),"发送队列繁忙，请返回重试");modal=RESULT;}
 render_modal();
}
static void open_request(const char *token,const char *heading,const char *kind){
 copy(request_id,sizeof(request_id),token);copy(request_title,sizeof(request_title),heading);copy(request_kind,sizeof(request_kind),kind);
 command_id[0]=0;decision[0]=0;memset(visited,0,sizeof(visited));doc_total=doc_page=0;actions=0;result_query=false;modal=DOCUMENT;
 request_page(0);
}
static bool all_read(){if(doc_total<1||doc_total>120)return false;for(int i=0;i<doc_total;i++)if(!visited[i])return false;return true;}
static void open_list(Modal type){
 modal=type;list_page=0;list_count=0;
 if(type==TASKS){int indices[5];int n=group_tasks(focus_group,indices);for(int i=0;i<n;i++)copy(list_keys[list_count++],24,state.tasks[indices[i]].key);}
 if(type==REMINDERS)for(int i=0;i<state.reminder_count;i++)copy(list_keys[list_count++],24,state.reminders[i].key);
 if(type==DEVICES)for(int i=0;i<group_count;i++)copy(list_keys[list_count++],24,groups[i].id);
 render_modal();
}
static void show_reminder(int i){
 const auto &r=state.reminders[i];char heading[128];snprintf(heading,sizeof(heading),"%s · 待办 · %s",r.dev,r.at);
 if(r.control[0]&&r.active&&online)open_request(r.control,heading,"reminder");
 else{modal=RESULT;result_query=false;request_id[0]=0;copy(request_title,sizeof(request_title),heading);snprintf(document_text,sizeof(document_text),"%s\n%s\n此提醒当前仅可查看",r.title,r.summary);render_modal();}
}
// Viewing an attention state must not depend on permission to submit it.
// All three entry points share this path, including a single local task.
static bool show_attention(int i){
 if(i<0)return false;const auto &t=state.tasks[i];
 if(!t.has_attention&&!t.control[0])return false;
 char heading[128];snprintf(heading,sizeof(heading),"%s · %s · %s",groups[focus_group].name,t.name,t.label);
 if(online&&!t.error&&t.control[0]){open_request(t.control,heading,"approval");return true;}
 modal=RESULT;waiting_page=false;result_query=false;request_id[0]=command_id[0]=0;actions=0;
 copy(request_title,sizeof(request_title),heading);
 const char *message;
 if(!online||t.error)message="来源或连接已中断。\n恢复连接后重新打开请求。";
 else if(!strcmp(state.privacy,"status_only")||!strcmp(state.control_mode,"privacy"))message="当前仅显示状态，详情已隐藏。\n电脑：远程连接 / ESP32\n关闭仅显示状态，保存并连接。";
 else if(!strcmp(state.control_mode,"disabled"))message="USB 屏幕审核尚未开启。\n电脑：远程连接 / ESP32\n勾选允许审核，保存并连接。";
 else if(!strcmp(t.status,"waiting_input"))message="此请求需要填写回答。\n请在来源应用输入后继续。";
 else if(!strcmp(state.control_mode,"enabled"))message="来源未提供可安全提交的完整请求。\n请在电脑来源应用处理。";
 else message="当前连接未提供审核能力。\n请检查桌面 ESP32 设置及固件版本。";
 copy(document_text,sizeof(document_text),message);render_modal();return true;
}
static void pill_click(lv_event_t *event){
 if(modal!=NONE||switching)return;
 int action=(intptr_t)lv_event_get_user_data(event);
 if(action==1){if(group_count<2)return;focus_group=focus_group?0:other_group;copy(selected_device,24,groups[focus_group].id);detailed=false;set_layout(true);render();return;}
 if(action==2){if(state.reminder_count>1)open_list(REMINDERS);else if(state.reminder_count==1)show_reminder(0);else{modal=RESULT;result_query=false;request_id[0]=0;copy(request_title,sizeof(request_title),"下一次提醒 · 仅预告");snprintf(document_text,sizeof(document_text),"%s %s\n%s",state.idle_at,state.idle_title,state.idle_body);render_modal();}return;}
 if(action==3){open_list(TASKS);return;}
 if(show_attention(selected_task()))return;
 detailed=!detailed;render();
}
static void ring_click(lv_event_t *event){
 if(modal!=NONE||switching)return;
 int slot=(intptr_t)lv_event_get_user_data(event),i=ring_tasks[slot];
 if(i==-2){open_list(TASKS);return;}
 if(i<0)return;copy(groups[focus_group].selected,24,state.tasks[i].key);detailed=true;render();
 show_attention(i);
}
static void card_click(lv_event_t *event){
 int j=list_page*3+(intptr_t)lv_event_get_user_data(event);if(j>=list_count)return;
 if(modal==TASKS){int i=task_index(list_keys[j]);if(i<0)return;copy(groups[focus_group].selected,24,state.tasks[i].key);modal=NONE;detailed=true;render();show_attention(i);}
 else if(modal==REMINDERS){int i=reminder_index(list_keys[j]);if(i>=0)show_reminder(i);}
 else if(modal==DEVICES){for(int i=0;i<group_count;i++)if(!strcmp(groups[i].id,list_keys[j])){focus_group=i;if(i)other_group=i;copy(selected_device,24,groups[i].id);break;}modal=NONE;set_layout(true);render();}
}
static void set_button(int i,int x,int w,const char *caption,bool enabled=true,uint32_t color=0xffffff){
 hide(buttons[i],false);lv_obj_set_pos(buttons[i],x,120);lv_obj_set_size(buttons[i],w,44);
 text(button_labels[i],caption);lv_obj_set_style_bg_color(buttons[i],lv_color_hex(color),0);
 lv_obj_set_style_text_color(button_labels[i],lv_color_hex(color==BLUE?0xffffff:FG),0);
 if(enabled)lv_obj_clear_state(buttons[i],LV_STATE_DISABLED);else lv_obj_add_state(buttons[i],LV_STATE_DISABLED);
 lv_obj_set_style_opa(buttons[i],enabled?255:100,0);lv_obj_center(button_labels[i]);
}
static void render_modal(){
 hide(overlay,modal==NONE);if(modal==NONE)return;
 for(int i=0;i<5;i++)hide(buttons[i],true);
 for(int i=0;i<3;i++)hide(cards[i],true);
 hide(overlay_body,false);
 if(modal==TASKS||modal==REMINDERS||modal==DEVICES){
  char b[128];snprintf(b,sizeof(b),"%s · %s · 第 %d/%d 页",groups[focus_group].name,modal==TASKS?"全部任务":modal==REMINDERS?"待办提醒":"选择设备",list_page+1,std::max(1,(list_count+2)/3));text(overlay_title,b);hide(overlay_body,true);
  for(int j=0;j<3;j++){
   int n=list_page*3+j;if(n>=list_count)break;hide(cards[j],false);const char *name="任务已退出",*st="已失效",*app="unknown";bool attention=false;
   if(modal==TASKS){int i=task_index(list_keys[n]);if(i>=0){name=state.tasks[i].title;st=state.tasks[i].label;app=state.tasks[i].app;attention=state.tasks[i].has_attention;}}
   else if(modal==REMINDERS){int i=reminder_index(list_keys[n]);if(i>=0){name=state.reminders[i].title;st=state.reminders[i].dev;app="solo_task";attention=true;}}
   else{for(int i=0;i<group_count;i++)if(!strcmp(groups[i].id,list_keys[n])){name=groups[i].name;st=groups[i].online?"在线":"离线";}}
   text(card_names[j],name);text(card_states[j],st);lv_img_set_src(card_icons[j],island_icon(app));lv_obj_set_style_bg_color(cards[j],lv_color_hex(attention?0xfff3de:0xffffff),0);
  }
  set_button(0,16,100,"返回");if(list_count>3)set_button(2,494,130,"下一页");return;
 }
 text(overlay_title,request_title);
 text(body_label,waiting_page?"正在读取完整内容…":document_text);
 if(modal==DOCUMENT){
  set_button(0,16,84,"返回");
  if(waiting_page)return;
  if(doc_page>0)set_button(1,108,84,"上一页");
  if(doc_page+1<doc_total)set_button(2,520,104,"下一页");
  if(all_read()&&doc_page==doc_total-1){
   if(!strcmp(request_kind,"reminder")){set_button(3,310,160,"5分钟后提醒",actions&4);set_button(4,482,142,"已知晓",actions&8,BLUE);}
   else{set_button(3,418,94,"拒绝",actions&2,0xf5e2df);set_button(4,524,100,"同意本次",actions&1,BLUE);}
  }
 }else if(modal==CONFIRM){set_button(0,16,120,"返回检查");set_button(4,474,150,"确认提交",online,!strcmp(decision,"deny")?0xf5e2df:BLUE);}
 else if(modal==PENDING){set_button(0,16,150,"处理中…",false);}
 else{set_button(0,16,104,"返回任务");if(result_query&&command_id[0])set_button(4,434,190,"查询原请求状态",online);}
}
static void submit(){
 if(!online||!request_id[0])return;
 serial_client_new_id(command_id);modal=PENDING;sent_at=lv_tick_get();waiting_page=false;
 copy(document_text,sizeof(document_text),"已发送，等待来源设备确认。\n请勿重复提交。");
 if(!serial_client_request("DO",request_id,decision,0,command_id)){modal=RESULT;result_query=false;copy(document_text,sizeof(document_text),"发送失败，未提交。请返回重新读取请求。");}
 render_modal();
}
static void modal_button(lv_event_t *event){
 int id=(intptr_t)lv_event_get_user_data(event);
 if(id==0){if(modal==PENDING)return;if(modal==CONFIRM){modal=DOCUMENT;request_page(doc_page);return;}modal=NONE;waiting_page=false;render();return;}
 if(modal==TASKS||modal==REMINDERS||modal==DEVICES){list_page=(list_page+1)%std::max(1,(list_count+2)/3);render_modal();return;}
 if(modal==DOCUMENT){
  if(waiting_page)return;
  if(id==1&&doc_page>0){request_page(doc_page-1);return;}
  if(id==2&&doc_page+1<doc_total){request_page(doc_page+1);return;}
  if((id==3||id==4)&&all_read()){
   const bool reminder=!strcmp(request_kind,"reminder");const char *op=reminder?(id==3?"snooze":"acknowledge"):(id==3?"deny":"approve");
   uint8_t bit=reminder?(id==3?4:8):(id==3?2:1);if(!(actions&bit))return;
   copy(decision,sizeof(decision),op);
   if(reminder){submit();return;}
   modal=CONFIRM;snprintf(document_text,sizeof(document_text),"确认%s这一次请求？\n%s\n只作用于刚刚完整阅读的请求，不永久授权。",id==3?"拒绝":"同意",request_title);render_modal();
  }
 }else if(modal==CONFIRM&&id==4)submit();
 else if(modal==RESULT&&id==4&&result_query){modal=PENDING;sent_at=lv_tick_get();copy(document_text,sizeof(document_text),"正在查询原请求，不重新执行操作…");if(!serial_client_request("QUERY",request_id,decision,0,command_id)){modal=RESULT;}render_modal();}
}
static void render(){
 if(!built)return;
 update_groups();set_layout(false);
 apply_theme();
 char b[256];snprintf(b,sizeof(b),"Bright Moon / %s",groups[focus_group].name);text(header,b);text(connection,online?link_text:"连接中断 · 数据已冻结");
 int ids[5],n=group_tasks(focus_group,ids),i=selected_task();
 int show[3]={-1,-1,-1};if(n<=3){for(int j=0;j<n;j++)show[j]=ids[j];}else{show[0]=i;show[1]=ids[0]==i?ids[1]:ids[0];if(show[0]>show[1])std::swap(show[0],show[1]);show[2]=-2;}
 bool hidden_attention=false;for(int j=0;j<n;j++)if(ids[j]!=show[0]&&ids[j]!=show[1]&&state.tasks[ids[j]].has_attention)hidden_attention=true;
 for(int j=0;j<3;j++){
  ring_tasks[j]=show[j];hide(rings[j],show[j]==-1);if(show[j]==-1){hide(focus_dots[j],true);continue;}
  bool more=show[j]==-2;auto *t=more?nullptr:&state.tasks[show[j]];uint32_t c=more?(hidden_attention?AMBER:MUTED):task_color(*t);
  lv_obj_set_style_border_color(rings[j],lv_color_hex(c),0);hide(ring_images[j],more||(t&&(!strcmp(t->status,"completed")||!strcmp(t->status,"failed"))));
  hide(ring_marks[j],!lv_obj_has_flag(ring_images[j],LV_OBJ_FLAG_HIDDEN));
  if(more)text(ring_marks[j],hidden_attention?"...!":"...");else if(!strcmp(t->status,"completed"))text(ring_marks[j],LV_SYMBOL_OK);else if(!strcmp(t->status,"failed"))text(ring_marks[j],"!");else lv_img_set_src(ring_images[j],island_icon(t->app));
  lv_obj_set_style_text_color(ring_marks[j],lv_color_hex(c),0);lv_obj_center(ring_marks[j]);hide(focus_dots[j],more||show[j]!=i);lv_obj_set_style_bg_color(focus_dots[j],lv_color_hex(c),0);
  lv_obj_set_style_shadow_color(rings[j],lv_color_hex(c),0);lv_obj_set_style_shadow_width(rings[j],(!more&&show[j]==i)?8:0,0);
 }
 hide(brand,n>0);int w=group_count>1?498:608;int x=n?23+std::min(3,n)*52+10:96;
 lv_obj_set_pos(title,x,12);lv_obj_set_width(title,w-x-96);lv_obj_set_pos(timer_label,w-86,15);lv_obj_set_width(timer_label,64);
 lv_obj_set_pos(summary,x,39);lv_obj_set_width(summary,w-x-22);lv_obj_set_pos(status_label,x,65);lv_obj_set_width(status_label,w-x-20);
 if(i>=0){auto &t=state.tasks[i];text(title,t.title);text(summary,detailed?(t.summary[0]?t.summary:"来源未提供摘要"):(t.step[0]?t.step:t.label));
  if(t.has_attention)text(summary,t.control[0]?"点击查看请求并审核":"点击查看待处理说明");
  if(detailed&&t.plan_total>0&&t.plan_done>=0)snprintf(b,sizeof(b),"步骤 %ld / %ld · %s",(long)t.plan_done,(long)t.plan_total,t.label);else snprintf(b,sizeof(b),"%s · %s",t.name,t.error?"来源失联":t.label);
  text(status_label,b);snprintf(b,sizeof(b),"%02ld:%02ld",(long)(t.elapsed_s/60),(long)(t.elapsed_s%60));text(timer_label,b);
 }else{text(title,"Bright Moon");text(summary,!groups[focus_group].online?"设备离线，等待自动重连":detailed?"留一点时间，给自己。":"暂无任务，安心专注。");text(status_label,groups[focus_group].online?"保持在线 · 空闲":"绑定仍保留");text(timer_label,state.clock);}
 bool show_plan=i>=0&&detailed&&state.tasks[i].plan_total>0&&state.tasks[i].plan_done>=0&&!state.tasks[i].has_attention;
 for(int j=0;j<5;j++){
  int count=show_plan?std::min(5,int(state.tasks[i].plan_total)):0;hide(plan_nodes[j],j>=count);
  if(j<4){hide(plan_lines[j],j>=count-1);lv_obj_set_pos(plan_lines[j],x+5+j*14,74);}
  if(j<count){lv_obj_set_pos(plan_nodes[j],x+j*14,72);lv_obj_set_style_bg_color(plan_nodes[j],lv_color_hex(j<state.tasks[i].plan_done?BLUE:0xd4deea),0);}
 }
 if(show_plan){int count=std::min(5,int(state.tasks[i].plan_total));lv_obj_set_pos(status_label,x+count*14+4,65);lv_obj_set_width(status_label,w-x-count*14-24);}
 if(group_count>1){int g=focus_group?0:other_group;int indices[5];int count=group_tasks(g,indices);bool att=false;for(int j=0;j<count;j++)att|=state.tasks[indices[j]].has_attention;
  text(small_label,groups[g].name);snprintf(b,sizeof(b),att?"待审核":!groups[g].online?"已离线":count?"%d 个任务":"空闲",count);text(small_status,b);lv_obj_set_style_text_color(small_status,lv_color_hex(att?AMBER:count?BLUE:GREEN),0);
 }
 if(state.reminder_count){snprintf(b,sizeof(b),"提醒 %s · %s%s",state.reminders[0].at,state.reminders[0].title,state.reminder_count>1?" · 更多":"");}
 else if(state.idle_title[0])snprintf(b,sizeof(b),"%s · %s",state.idle_at,state.idle_title);else copy(b,sizeof(b),"未来 1 小时暂无提醒");text(reminder_label,b);
 int ordinal=0;for(int j=0;j<n;j++)if(ids[j]==i)ordinal=j+1;
 if(state.task_total>state.task_count)snprintf(b,sizeof(b),"已同步 %ld/%ld",(long)state.task_count,(long)state.task_total);else snprintf(b,sizeof(b),n?"%d/%d 任务%s":"",ordinal,n,n>3?" ...":"");text(counter_label,b);
 // A newer proposal must invalidate a review, never silently replace its text.
 if(modal==DOCUMENT||modal==CONFIRM){bool found=false;for(int j=0;j<state.task_count;j++)found|=!strcmp(request_id,state.tasks[j].control);for(int j=0;j<state.reminder_count;j++)found|=!strcmp(request_id,state.reminders[j].control);
  if(!online||!found){modal=RESULT;waiting_page=false;result_query=false;copy(document_text,sizeof(document_text),"请求已变化或连接中断。\n请返回查看最新任务。");}}
 render_modal();
}
static void tick(lv_timer_t *){
 if((waiting_page||modal==PENDING)&&lv_tick_elaps(sent_at)>10000){waiting_page=false;result_query=command_id[0];modal=RESULT;copy(document_text,sizeof(document_text),result_query?"结果暂未确认。\n请查询原请求，不要重复提交。":"读取超时，请返回重新打开。");render_modal();}
 int pulse=(lv_tick_get()/100)%26;if(pulse==last_pulse)return;last_pulse=pulse;
 for(int j=0;j<3;j++){int i=ring_tasks[j];bool active=i>=0&&i==selected_task()&&online&&!state.tasks[i].final&&!state.tasks[i].error;
  lv_obj_set_style_shadow_opa(rings[j],active?30+(pulse<13?pulse:26-pulse)*6:0,0);}
}
static void device_long(lv_event_t *){if(modal==NONE&&group_count>2)open_list(DEVICES);}
void island_ui_build(void){
 ui_font=island_cjk_16;ui_font.get_glyph_dsc=glyph_dsc;ui_font.get_glyph_bitmap=glyph_bitmap;font=&ui_font;
 screen=lv_scr_act();lv_obj_remove_style_all(screen);lv_obj_set_style_bg_color(screen,lv_color_hex(BG),0);lv_obj_set_style_bg_opa(screen,255,0);lv_obj_clear_flag(screen,LV_OBJ_FLAG_SCROLLABLE);
 header=label(screen,22,8,370,"Bright Moon / 本机",MUTED);connection=label(screen,395,8,223,"USB 等待",MUTED);lv_obj_set_style_text_align(connection,LV_TEXT_ALIGN_RIGHT,0);
 local_pill=box(screen,16,34,608,98,49);remote_pill=box(screen,526,34,98,98,49);
 content=button(local_pill,0,0,608,98,pill_click,0);small_content=button(remote_pill,0,0,98,98,pill_click,1);lv_obj_add_event_cb(small_content,device_long,LV_EVENT_LONG_PRESSED,nullptr);
 auto *device_icon=box(small_content,35,12,28,20,3);lv_obj_set_style_border_width(device_icon,2,0);lv_obj_set_style_border_color(device_icon,lv_color_hex(MUTED),0);
 box(small_content,48,32,2,5,0,MUTED);box(small_content,41,37,16,2,1,MUTED);
 small_label=label(small_content,7,39,84,"工作站",MUTED);small_status=label(small_content,7,64,84,"空闲",GREEN);lv_obj_set_style_text_align(small_label,LV_TEXT_ALIGN_CENTER,0);lv_obj_set_style_text_align(small_status,LV_TEXT_ALIGN_CENTER,0);
 brand=lv_img_create(content);lv_img_set_src(brand,island_icon("unknown"));lv_img_set_zoom(brand,384);lv_obj_set_pos(brand,38,31);
 for(int j=0;j<3;j++){
  rings[j]=button(content,23+j*52,26,46,46,ring_click,j);lv_obj_set_style_border_width(rings[j],3,0);
  ring_images[j]=lv_img_create(rings[j]);lv_img_set_src(ring_images[j],island_icon("codex"));lv_obj_center(ring_images[j]);
  ring_marks[j]=label(rings[j],0,0,40,"",BLUE,&lv_font_montserrat_20);lv_obj_set_style_text_align(ring_marks[j],LV_TEXT_ALIGN_CENTER,0);lv_obj_center(ring_marks[j]);
  focus_dots[j]=box(content,44+j*52,80,4,4,2,BLUE);
 }
 title=label(content,96,12,400,"Bright Moon");timer_label=label(content,520,15,66,"00:00",MUTED,&lv_font_montserrat_16);lv_obj_set_style_text_align(timer_label,LV_TEXT_ALIGN_RIGHT,0);
 summary=label(content,96,39,430,"暂无任务",MUTED);status_label=label(content,96,65,430,"空闲",BLUE);
 for(int j=0;j<5;j++){if(j<4)plan_lines[j]=box(content,0,74,9,1,0,0xc4d5eb);plan_nodes[j]=box(content,0,72,5,5,3,BLUE);}
 reminder_button=button(screen,16,137,414,34,pill_click,2);lv_obj_set_style_bg_opa(reminder_button,0,0);reminder_label=label(reminder_button,6,4,402,"未来 1 小时暂无提醒",AMBER);
 counter_button=button(screen,440,137,184,34,pill_click,3);lv_obj_set_style_bg_opa(counter_button,0,0);counter_label=label(counter_button,0,4,178,"",MUTED);lv_obj_set_style_text_align(counter_label,LV_TEXT_ALIGN_RIGHT,0);
 overlay=box(screen,0,0,640,172,0,BG);lv_obj_add_flag(overlay,LV_OBJ_FLAG_CLICKABLE);overlay_title=label(overlay,16,5,608,"审核",AMBER);
 overlay_body=box(overlay,16,30,608,84,16);body_label=label(overlay_body,12,7,584,"");lv_label_set_long_mode(body_label,LV_LABEL_LONG_WRAP);lv_obj_set_style_text_line_space(body_label,2,0);lv_obj_set_height(body_label,70);
 for(int j=0;j<5;j++){buttons[j]=button(overlay,16,120,100,44,modal_button,j);button_labels[j]=label(buttons[j],0,0,LV_SIZE_CONTENT,"");lv_obj_center(button_labels[j]);}
 for(int j=0;j<3;j++){cards[j]=button(overlay,16+j*205,30,198,84,card_click,j);lv_obj_set_style_radius(cards[j],16,0);card_icons[j]=lv_img_create(cards[j]);lv_obj_set_pos(card_icons[j],10,24);lv_img_set_src(card_icons[j],island_icon("unknown"));card_names[j]=label(cards[j],54,14,134,"");card_states[j]=label(cards[j],54,48,134,"",MUTED);}
 tick_timer=lv_timer_create(tick,100,nullptr);built=true;render();
}
void island_ui_apply(const IslandState *value){if(!value)return;state=*value;state.task_count=std::max(0,std::min(5,int(state.task_count)));state.reminder_count=std::max(0,std::min(5,int(state.reminder_count)));state.device_count=std::max(0,std::min(6,int(state.device_count)));render();}
void island_ui_set_link(bool connected,int32_t){if(online!=connected){online=connected;render();}}
void island_ui_set_link_text(const char *value){copy(link_text,sizeof(link_text),value&&value[0]?value:"USB 等待");if(built)text(connection,online?link_text:"连接中断 · 数据已冻结");}
void island_ui_show_result(const char *message,bool){if(modal==NONE){modal=RESULT;result_query=false;request_id[0]=0;copy(request_title,sizeof(request_title),"操作结果");copy(document_text,sizeof(document_text),message);render_modal();}}
void island_ui_control_reply(const IslandReply *reply){
 if(!reply||strcmp(reply->id,request_id))return;
 if(reply->detail){
  if(modal!=DOCUMENT||!waiting_page||reply->page!=requested_page||reply->total<1||reply->total>120||reply->page<0||reply->page>=reply->total)return;
  if(doc_total&&reply->total!=doc_total){modal=RESULT;waiting_page=false;copy(document_text,sizeof(document_text),"请求内容发生变化，请重新读取");render_modal();return;}
  doc_page=reply->page;doc_total=reply->total;visited[doc_page]=true;actions=reply->actions;waiting_page=false;copy(document_text,sizeof(document_text),reply->text);copy(request_kind,sizeof(request_kind),reply->kind);render_modal();return;
 }
 if(reply->cmd[0]&&strcmp(reply->cmd,command_id))return;
 if(modal!=PENDING&&modal!=DOCUMENT&&modal!=CONFIRM&&modal!=RESULT)return;
 waiting_page=false;copy(result_status,sizeof(result_status),reply->status);copy(document_text,sizeof(document_text),reply->text);
 if(!strcmp(reply->status,"pending"))modal=PENDING;else{modal=RESULT;result_query=!strcmp(reply->status,"outcome_unknown");}
 render_modal();
}
static void power_cancel_cb(lv_event_t *){hide(power_dialog,true);}
static void power_confirm_cb(lv_event_t *){power_confirmed=true;text(power_title,"正在关闭，请松开电源键");lv_obj_add_state(power_confirm,LV_STATE_DISABLED);lv_obj_add_state(power_cancel,LV_STATE_DISABLED);}
void island_ui_show_power_dialog(void){
 if(!power_dialog){power_dialog=box(lv_layer_top(),0,0,640,172,0,0x101927);lv_obj_set_style_bg_opa(power_dialog,210,0);lv_obj_add_flag(power_dialog,LV_OBJ_FLAG_CLICKABLE);
 auto *card=box(power_dialog,65,8,510,156,24,0xf4f7fc);power_title=label(card,24,17,460,"关闭这台桌面伴侣？");label(card,24,49,460,"电脑任务会继续，USB 供电时进入休眠",MUTED);
 power_cancel=button(card,24,99,218,44,power_cancel_cb);power_confirm=button(card,254,99,232,44,power_confirm_cb);lv_obj_set_style_bg_color(power_confirm,lv_color_hex(BLUE),0);
 auto *a=label(power_cancel,0,0,LV_SIZE_CONTENT,"继续陪伴");lv_obj_center(a);auto *b=label(power_confirm,0,0,LV_SIZE_CONTENT,"确认关机",0xffffff);lv_obj_center(b);}
 hide(power_dialog,false);lv_obj_move_foreground(power_dialog);
}
bool island_ui_take_power_shutdown(void){bool value=power_confirmed;power_confirmed=false;return value;}
