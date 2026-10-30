"""Embedded desktop peer controls, sharing the phone page wallpaper surface."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk
from ...phone.settings_panel import PhoneSettings
from ...UI.theme import blend
from .surface import PeerSurface
from ...esp32.serial_settings import ESP32Settings

class PeerSettings:
    def __init__(self,app,parent):
        self.app=app;self.window=tk.Frame(parent);self.surface=PeerSurface(self.window,app.scale)
        self.closed=False;self.job=None;self.interfaces=[];self.scan_result=queue.Queue();self.key=None;self.show_tasks=False;self.task_page=0;self.pending_action=None
        self.form=None;self.form_value=None;self.status_note=''
        self.scroll_to_form=False
        self.wheel_binding=app.root.bind('<MouseWheel>',self.wheel,add='+')
        c=self.surface.canvas;s=self.surface;cfg=app.store.snapshot()['peers']
        s.label(12,8,'电脑协同',22,True)
        s.label(12,43,'把另一台电脑的进展，带到眼前。绑定后自动重连。',11)
        s.card(8,78,480,190)
        s.label(24,94,'本机连接',14,True)
        self.name=tk.StringVar(value=cfg['name']);self.port=tk.StringVar(value=str(cfg['port']))
        s.label(24,124,'设备名称',10);s.label(314,124,'端口',10)
        s.widget(ttk.Entry(c,textvariable=self.name,style='Peer.TEntry'),24,144,270)
        s.widget(ttk.Entry(c,textvariable=self.port,style='Peer.TEntry'),314,144,150)
        self.interface=ttk.Combobox(c,state='readonly',style='Peer.TCombobox')
        s.widget(self.interface,24,188,310);s.button(350,188,114,'刷新网卡',self.scan)
        s.button(24,228,125,'保存并开启',self.start,accent=True)
        s.button(165,228,110,'关闭协同',self.stop)
        self.message=s.label(12,282,'正在读取连接状态…',11,width=475)
        s.label(12,342,'添加一台电脑',15,True)
        s.label(12,370,'在一台电脑生成邀请，在另一台粘贴；核对后确认一次。',11)
        s.button(12,400,140,'生成邀请',lambda:self.call('invite'),accent=True)
        s.button(170,400,140,'复制邀请',self.copy)
        s.button(328,400,150,'绑定电脑',self.join,accent=True)
        self.form_y=452;self.devices_y=452
        self.scan();self.refresh()
    def layout(self,x,y,width,height,background,foreground,backdrop=None):
        self.window.configure(bg=background);self.surface.set_theme(background,foreground)
        self.window.place(x=round(x),y=round(y),width=round(width),height=round(height))
        self.surface.set_backdrop(backdrop,(round(x),round(y)),(round(width),round(height)))
    def call(self,kind,value=None):
        if kind in ('invite','join') and self.app.peers.snapshot_status().get('state')!='running':
            if self.start():self.pending_action=(kind,value)
            return
        self.status_note=''
        try:self.app.peers.command(kind,value)
        except (ValueError,OSError) as e:self.status_note=str(e)
    def scan(self):
        discovery,results=self.app.peers.discovery,self.scan_result
        def work():
            try:results.put(discovery())
            except Exception:results.put([])
        threading.Thread(target=work,daemon=True).start()
    def wheel(self,event):
        if not self.closed and self.window.winfo_ismapped():
            self.surface.canvas.yview_scroll(-int(event.delta/120),'units')
    def start(self):
        self.status_note=''
        try:
            index=self.interface.current()
            if index<0:raise ValueError('请选择私人网络网卡')
            cfg=dict(enabled=True,show_remote=True,interface_id=self.interfaces[index].id,port=int(self.port.get()),name=self.name.get())
            self.app.peers.start(cfg);self.app.store.update(peers=cfg)
            return True
        except (ValueError,OSError) as e:self.status_note=str(e)
    def stop(self):
        self.pending_action=None
        self.app.store.update(peers={**self.app.store.snapshot()['peers'],'enabled':False});self.app.peers.stop()
    def toggle_tasks(self):self.show_tasks=not self.show_tasks;self.task_page=0;self.key=None
    def page(self,direction):self.task_page=max(0,self.task_page+direction);self.key=None
    def copy(self):
        value=self.app.peers.snapshot_status().get('invite')
        if value:
            self.window.clipboard_clear();self.window.clipboard_append(value);self.status_note='邀请已复制，5 分钟内在另一台电脑粘贴。'
        else:self.status_note='请先生成邀请。'
    def open_form(self,title,body,submit,label,initial=None):
        self.surface.clear_group('form');s=self.surface;c=s.canvas;y=self.form_y
        self.form=submit;self.form_value=None;self.devices_y=y+238
        s.card(8,y,480,218,group='form');s.label(24,y+16,title,15,True,group='form')
        s.label(24,y+47,body,11,width=440,group='form')
        if initial is not None:
            self.form_value=tk.Text(c,wrap='word',height=3,relief='flat',borderwidth=0,
                                   font=('Microsoft YaHei UI',-round(11*self.app.scale)))
            self.form_value.insert('1.0',initial)
            s.widget(self.form_value,24,y+96,440,group='form')
            c.itemconfigure(c.find_withtag('form')[-1],height=round(66*self.app.scale))
            self.form_value.configure(bg=s.bg,fg=s.foreground,insertbackground=s.foreground)
        s.button(240,y+174,100,'取消',self.close_form,group='form')
        s.button(354,y+174,110,label,self.submit_form,group='form',accent=True)
        self.key=None;self.scroll_to_form=True
        if self.form_value:self.form_value.focus_set()
    def close_form(self):
        self.surface.clear_group('form');self.form=None;self.form_value=None
        self.devices_y=self.form_y;self.key=None
    def submit_form(self):
        callback=self.form
        value=self.form_value.get('1.0','end').strip() if self.form_value else ''
        if self.form_value and not value:self.status_note='请填写完整内容。';return
        self.close_form()
        if callback:callback(value)
    def join(self):
        self.open_form('绑定另一台电脑','粘贴完整邀请。绑定后共享任务详情，可处理来源支持的授权与提醒。',
                       lambda value:self.call('join',value),'开始绑定',initial='')
    def confirm_binding(self,item):
        self.open_form('确认绑定 · '+item['name'],'双方核对码：'+item['code']+
                       '\n确认后自动双向协同，删除绑定可撤销。仅绑定自己的或信任的电脑。',
                       lambda _:self.call('approve',dict(id=item['id'])),'确认绑定')
    def update_address(self,item):
        self.open_form('更新地址 · '+item['name'],'填写对方当前的 HTTPS 地址。保留原设备身份与绑定，无需重新配对。',
                       lambda value:self.call('address',dict(id=item['id'],address=value)),
                       '保存地址',initial=item['address'])
    def remove(self,item):
        self.open_form('删除绑定 · '+item['name'],'删除后将停止共享。对方重启或网络恢复不会重新建立此绑定。',
                       lambda _:self.call('remove',item['id']),'删除绑定')
    def refresh(self):
        if self.closed:return
        try:self.interfaces=self.scan_result.get_nowait()
        except queue.Empty:pass
        else:
            self.interface.configure(values=[i.name+' · '+i.address for i in self.interfaces])
            if self.interfaces:self.interface.current(next((n for n,i in enumerate(self.interfaces) if i.id==self.app.store.snapshot()['peers']['interface_id']),0))
        state=self.app.peers.snapshot_status();self.message.configure(text=(self.status_note or state.get('message',''))+'\n'+state.get('address',''))
        if self.pending_action and state.get('state')=='running':
            action=self.pending_action;self.pending_action=None;self.call(*action)
        tasks=self.app.peers.catalog_snapshot() if self.show_tasks else []
        key=json.dumps([state.get('peers'),state.get('pending'),self.show_tasks,self.task_page,self.devices_y,[(r['run_id'],r['remote_entity']['revision'],r['connection_error']) for r in tasks]],sort_keys=True)
        if key!=self.key:
            self.key=key;s=self.surface;s.clear_group('devices');y=self.devices_y
            s.label(12,y,'当前任务' if self.show_tasks else '已绑定设备',15,True,group='devices')
            s.button(310,y-5,168,'返回设备' if self.show_tasks else '查看全部任务',self.toggle_tasks,group='devices');y+=48
            for item in ([] if self.show_tasks else state.get('pending',[])):
                s.label(10,y,item['name']+' · 核对码 '+item['code'],group='devices',width=300)
                if item['direction']=='incoming':
                    s.button(320,y,90,'确认绑定',lambda i=item:self.confirm_binding(i),group='devices')
                else:s.label(320,y,'等待对方确认',size=11,group='devices')
                s.button(420,y,65,'拒绝',lambda i=item:self.call('reject',i['id']),group='devices');y+=55
            for item in ([] if self.show_tasks else state.get('peers',[])):
                s.card(8,y-8,480,100,group='devices')
                suffix=f" · {item['omitted_count']} 项超过共享上限" if item.get('omitted_count') else ''
                s.label(10,y,item['name']+' · '+('在线' if item['online'] else '离线')+suffix,group='devices',width=190)
                s.button(275,y,100,'更新地址',lambda i=item:self.update_address(i),group='devices')
                s.button(395,y,90,'删除绑定',lambda i=item:self.remove(i),group='devices');y+=30
                s.label(10,y,item.get('connection_message') or '已绑定 · 自动重连，无需再次授权',size=11,group='devices',width=450);y+=75
            if self.show_tasks:
                s.button(12,y,90,'上一页',lambda:self.page(-1),group='devices')
                s.button(118,y,90,'下一页',lambda:self.page(1),group='devices');y+=44
                self.task_page=min(self.task_page,max(0,(len(tasks)-1)//20))
                for run in tasks[self.task_page*20:(self.task_page+1)*20]:
                    s.label(10,y,run['remote_name']+' · '+run['task_summary'],group='devices',width=330)
                    s.button(365,y,120,'在灵动岛查看',lambda r=run:self.app.island.inspect_remote(r),group='devices');y+=48
                s.label(10,y,f'共 {len(tasks)} 项 · 第 {self.task_page+1} 页 · 含已退场的最近来源记录',group='devices');y+=35
            if not self.show_tasks and not state.get('peers') and not state.get('pending'):
                s.label(24,y,'还没有绑定电脑。完成上方邀请后，它会出现在这里。',11,width=440,group='devices');y+=48
            s.label(10,y,'最多绑定 5 台电脑；重启后自动恢复。两端需运行新版 SOLO 并开启协同。',size=11,group='devices');s.set_height(y+70)
        if self.scroll_to_form:
            self.scroll_to_form=False
            self.surface.canvas.yview_moveto(max(0,self.form_y-16)/self.surface.height)
        self.job=self.window.after(400,self.refresh)
    def close(self):
        self.closed=True
        if self.job:self.window.after_cancel(self.job)
        self.app.root.unbind('<MouseWheel>',self.wheel_binding)
        self.window.destroy()

class RemoteSettings:
    def __init__(self,app,parent):
        self.app=app;self.window=tk.Frame(parent);self.parent=parent;self.mode='phone';self.children={}
        self.bar=tk.Canvas(self.window,highlightthickness=0,borderwidth=0);self.bar.pack(fill='both',expand=True)
        self.bar_image=self.bar.create_image(0,0,anchor='nw');self.bar_key=None;self.bar_photo=None
        self.tabs=[('设备服务','phone'),('电脑协同','peers'),('ESP32 屏幕','esp32')]
        self.tab_boxes=[];self.bar.configure(takefocus=True,cursor='hand2')
        self.bar.bind('<Button-1>',self._tab_click)
        for key in ('Left','Right','space','Return'):
            self.bar.bind('<'+key+'>',self._tab_key)
        self.geometry=None

    def _tab_click(self,event):
        self.bar.focus_set()
        for box,mode in self.tab_boxes:
            if box[0] <= event.x <= box[2] and box[1] <= event.y <= box[3]:
                self.select(mode);return

    def _tab_key(self,event):
        modes=[mode for _,mode in self.tabs]
        self.select(modes[(modes.index(self.mode)+(-1 if event.keysym=='Left' else 1))%len(modes)])
        return 'break'

    def _draw_bar(self,background,foreground):
        c=self.bar;s=self.app.scale;c.delete('peer_tab');self.tab_boxes=[]
        # A compact segmented control keeps the page on the same wallpaper
        # surface as the settings card; native ttk buttons otherwise paint a
        # second opaque gray/white strip over the wallpaper.
        for i,(name,mode) in enumerate(self.tabs):
            x=10+i*122;y=3;w=116;h=29
            fill=blend(background,foreground,.16 if mode==self.mode else .07)
            c.create_polygon(*[v*s for p in ((x+10,y),(x+w-10,y),(x+w,y+10),(x+w,y+h-10),
                                               (x+w-10,y+h),(x+10,y+h),(x,y+h-10),(x,y+10)) for v in p],
                             smooth=True,fill=fill,outline='',tags='peer_tab')
            c.create_text((x+w/2)*s,(y+h/2)*s,text=name,fill=foreground,
                          font=('Microsoft YaHei UI',-round(10*s),'bold' if mode==self.mode else 'normal'),
                          tags='peer_tab')
            self.tab_boxes.append(((x*s,y*s,(x+w)*s,(y+h)*s),mode))
    def select(self,mode):
        self.mode=mode
        if self.geometry:self.layout(*self.geometry)
    def layout(self,x,y,width,height,background,foreground,backdrop=None):
        self.geometry=(x,y,width,height,background,foreground,backdrop)
        self.window.place(x=round(x),y=round(y),width=round(width),height=round(35*self.app.scale))
        self.window.configure(bg=background);self.bar.configure(bg=background)
        key=(id(backdrop),round(x),round(y),round(width),round(35*self.app.scale))
        if key!=self.bar_key:
            self.bar_key=key
            if backdrop is not None:
                self.bar_photo=ImageTk.PhotoImage(backdrop.crop((key[1],key[2],key[1]+key[3],key[2]+key[4])),master=self.bar)
                self.bar.itemconfigure(self.bar_image,image=self.bar_photo)
            else:self.bar.itemconfigure(self.bar_image,image='');self.bar_photo=None
        self._draw_bar(background,foreground)
        if self.mode not in self.children:self.children[self.mode]={'phone':PhoneSettings,'peers':PeerSettings,'esp32':ESP32Settings}[self.mode](self.app,self.parent)
        for mode,child in self.children.items():
            if mode!=self.mode:child.window.place_forget()
        self.children[self.mode].layout(x,y+38*self.app.scale,width,height-38*self.app.scale,background,foreground,backdrop)
    def hide(self):
        self.window.place_forget()
        for child in self.children.values():child.window.place_forget()
    def close(self):
        for child in self.children.values():child.close()
        self.window.destroy();self.app.phone_dialog=None
