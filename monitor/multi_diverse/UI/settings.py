"""Embedded desktop peer controls, sharing the phone page wallpaper surface."""
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk,messagebox,simpledialog
from PIL import ImageTk
from ...phone.settings_panel import PhoneSettings
from ...phone.settings_surface import SettingsSurface
from ..config import grants

class PeerSettings:
    def __init__(self,app,parent):
        self.app=app;self.window=tk.Frame(parent);self.surface=SettingsSurface(self.window,app.scale)
        self.closed=False;self.job=None;self.interfaces=[];self.scan_result=queue.Queue();self.key=None;self.show_tasks=False;self.task_page=0
        c=self.surface.canvas;s=self.surface;cfg=app.store.snapshot()['peers']
        s.label(10,6,'电脑协同',20,True)
        s.label(10,40,'双向查看，分别授权 · 不需要登录 · 仅私人局域网',11)
        self.name=tk.StringVar(value=cfg['name']);s.widget(ttk.Entry(c,textvariable=self.name),10,75,240)
        self.show_remote=tk.BooleanVar(value=cfg['show_remote'])
        self.port=tk.StringVar(value=str(cfg['port']));s.widget(ttk.Entry(c,textvariable=self.port),270,75,90)
        s.label(365,80,'设备名 / 端口',11)
        self.interface=ttk.Combobox(c,state='readonly');s.widget(self.interface,10,115,350)
        s.button(375,115,110,'刷新网卡',self.scan)
        s.button(10,160,130,'保存并开启',self.start);s.button(155,160,110,'关闭协同',self.stop)
        s.button(280,160,100,'生成邀请',lambda:self.call('invite'));s.button(390,160,100,'粘贴邀请',self.join)
        self.message=s.label(10,205,'',11,width=480)
        s.button(10,255,130,'复制当前邀请',self.copy)
        s.checkbox(155,259,'在本机显示远程任务',self.show_remote)
        self.show_remote.trace_add('write',lambda *_:self.toggle_visible())
        s.button(10,295,150,'全部当前任务 / 设备',self.toggle_tasks)
        s.button(180,295,90,'上一页',lambda:self.page(-1));s.button(285,295,90,'下一页',lambda:self.page(1))
        self.scan();self.refresh()
    def layout(self,x,y,width,height,background,foreground,backdrop=None):
        self.window.configure(bg=background);self.surface.set_theme(background,foreground)
        self.window.place(x=round(x),y=round(y),width=round(width),height=round(height))
        self.surface.set_backdrop(backdrop,(round(x),round(y)),(round(width),round(height)))
    def call(self,kind,value=None):
        try:self.app.peers.command(kind,value)
        except (ValueError,OSError) as e:messagebox.showerror('电脑协同',str(e),parent=self.window)
    def scan(self):
        def work():
            try:self.scan_result.put(self.app.peers.discovery())
            except Exception:self.scan_result.put([])
        threading.Thread(target=work,daemon=True).start()
    def start(self):
        try:
            index=self.interface.current()
            if index<0:raise ValueError('请选择私人网络网卡')
            cfg=dict(enabled=True,show_remote=self.show_remote.get(),interface_id=self.interfaces[index].id,port=int(self.port.get()),name=self.name.get())
            self.app.store.update(peers=cfg);self.app.peers.start(cfg)
        except (ValueError,OSError) as e:messagebox.showerror('电脑协同',str(e),parent=self.window)
    def stop(self):
        self.app.store.update(peers={**self.app.store.snapshot()['peers'],'enabled':False});self.app.peers.stop()
    def toggle_visible(self):
        self.app.store.update(peers={**self.app.store.snapshot()['peers'],'show_remote':self.show_remote.get()})
    def toggle_tasks(self):self.show_tasks=not self.show_tasks;self.task_page=0;self.key=None
    def page(self,direction):self.task_page=max(0,self.task_page+direction);self.key=None
    def copy(self):
        value=self.app.peers.snapshot_status().get('invite')
        if value:self.window.clipboard_clear();self.window.clipboard_append(value)
    def join(self):
        value=simpledialog.askstring('加入电脑','粘贴对方通过可信渠道提供的完整邀请：',parent=self.window)
        if value:self.call('join',value)
    def permissions(self,item,pending=False):
        win=tk.Toplevel(self.window);win.title('核对设备与共享权限');win.transient(self.app.root)
        tk.Label(win,text=item['name']+'\n'+('双方核对码：'+item['code'] if pending else '设备指纹：'+item['fingerprint']),wraplength=480).pack(padx=20,pady=15)
        labels={'view_status':'共享任务状态与标题','anonymous_titles':'隐藏任务标题（详情权限另行控制）','view_details':'共享摘要、步骤与工具结果','reminder_act':'允许处理已共享提醒','agent_approve':'允许同意 / 拒绝 Agent 授权','agent_answer':'允许回答支持的 Agent 问题','agent_stop':'允许停止 Agent'}
        variables={}
        for key,label in labels.items():
            v=tk.BooleanVar(value=item.get('grants',grants()).get(key,False));variables[key]=v
            ttk.Checkbutton(win,text=label,variable=v).pack(anchor='w',padx=20,pady=4)
        tk.Label(win,text='共享来源（只共享在这里勾选的本机来源）').pack(padx=20,pady=6)
        source_vars={};selected=item.get('grants',{}).get('sources')
        for key,label in [('codex','Codex'),('opencode','OpenCode'),('deepseek_harness','DeepSeek'),('claude','Claude Code'),('solo_task','待办提醒')]:
            v=tk.BooleanVar(value=selected is None or key in selected);source_vars[key]=v
            ttk.Checkbutton(win,text=label,variable=v).pack(anchor='w',padx=20)
        tk.Label(win,text='这些权限是本机授予对方的；对方授予本机的权限需在对方设置。',wraplength=440).pack(padx=20,pady=10)
        def save():
            self.call('approve' if pending else 'grants',dict(id=item['id'],grants={**{k:v.get() for k,v in variables.items()},'sources':[k for k,v in source_vars.items() if v.get()]}));win.destroy()
        ttk.Button(win,text='核对一致，确认授权' if pending else '保存权限',command=save).pack(pady=10)
    def refresh(self):
        if self.closed:return
        try:self.interfaces=self.scan_result.get_nowait()
        except queue.Empty:pass
        else:
            self.interface.configure(values=[i.name+' · '+i.address for i in self.interfaces])
            if self.interfaces:self.interface.current(next((n for n,i in enumerate(self.interfaces) if i.id==self.app.store.snapshot()['peers']['interface_id']),0))
        state=self.app.peers.snapshot_status();self.message.configure(text=state.get('message','')+'\n'+state.get('address',''))
        tasks=self.app.peers.catalog_snapshot() if self.show_tasks else []
        key=json.dumps([state.get('peers'),state.get('pending'),self.show_tasks,[(r['run_id'],r['remote_entity']['revision'],r['connection_error']) for r in tasks]],sort_keys=True)
        if key!=self.key:
            self.key=key;s=self.surface;s.clear_group('devices');y=340
            for item in ([] if self.show_tasks else state.get('pending',[])):
                s.label(10,y,item['name']+' · 核对码 '+item['code'],group='devices',width=300)
                s.button(320,y,90,'核对权限',lambda i=item:self.permissions(i,True),group='devices')
                s.button(420,y,65,'拒绝',lambda i=item:self.call('reject',i['id']),group='devices');y+=55
            for item in ([] if self.show_tasks else state.get('peers',[])):
                suffix=f" · {item['omitted_count']} 项超过共享上限" if item.get('omitted_count') else ''
                s.label(10,y,item['name']+' · '+('在线' if item['online'] else '离线')+suffix,group='devices',width=190)
                s.button(210,y,80,'管理权限',lambda i=item:self.permissions(i),group='devices')
                s.button(300,y,85,'恢复共享' if item.get('paused') else '暂停共享',lambda i=item:self.call('pause',i['id']),group='devices')
                s.button(395,y,90,'移除配对',lambda i=item:self.call('remove',i['id']),group='devices');y+=55
            if self.show_tasks:
                self.task_page=min(self.task_page,max(0,(len(tasks)-1)//20))
                for run in tasks[self.task_page*20:(self.task_page+1)*20]:
                    s.label(10,y,run['remote_name']+' · '+run['task_summary'],group='devices',width=330)
                    s.button(365,y,120,'在灵动岛查看',lambda r=run:self.app.island.inspect_remote(r),group='devices');y+=48
                s.label(10,y,f'共 {len(tasks)} 项 · 第 {self.task_page+1} 页 · 含已退场的最近来源记录',group='devices');y+=35
            s.label(10,y,'最多配对 5 台电脑；提醒由来源电脑调度，来源离线不自动接管。',size=11,group='devices');s.set_height(y+70)
        self.job=self.window.after(400,self.refresh)
    def close(self):
        self.closed=True
        if self.job:self.window.after_cancel(self.job)
        self.window.destroy()

class RemoteSettings:
    def __init__(self,app,parent):
        self.app=app;self.window=tk.Frame(parent);self.parent=parent;self.mode='phone';self.children={}
        self.bar=tk.Canvas(self.window,highlightthickness=0,borderwidth=0);self.bar.pack(fill='both',expand=True)
        self.bar_image=self.bar.create_image(0,0,anchor='nw');self.bar_key=None;self.bar_photo=None
        for i,(name,mode) in enumerate([('手机访问','phone'),('电脑协同','peers')]):
            button=ttk.Button(self.bar,text=name,command=lambda value=mode:self.select(value))
            self.bar.create_window(i*110*app.scale,0,anchor='nw',window=button,width=108*app.scale,height=30*app.scale)
        self.geometry=None
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
        if self.mode not in self.children:self.children[self.mode]=(PhoneSettings if self.mode=='phone' else PeerSettings)(self.app,self.parent)
        for mode,child in self.children.items():
            if mode!=self.mode:child.window.place_forget()
        self.children[self.mode].layout(x,y+38*self.app.scale,width,height-38*self.app.scale,background,foreground,backdrop)
    def hide(self):
        self.window.place_forget()
        for child in self.children.values():child.window.place_forget()
    def close(self):
        for child in self.children.values():child.close()
        self.window.destroy();self.app.phone_dialog=None
