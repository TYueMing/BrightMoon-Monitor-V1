"""Embedded Remote Connection page. Widgets and confirmation remain on Tk."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import shutil
from .config import normalize
from .settings_surface import SettingsSurface


class PhoneSettings:
    def __init__(self, app, parent=None):
        self.app = app
        self.scale = getattr(app, "scale", 1)
        self.window = tk.Frame(parent if parent is not None else app.root, bg="#f5f7fc")
        self.theme = None
        self.style = ttk.Style(self.window)
        self.style.configure("Phone.TButton", font=("Microsoft YaHei UI", -round(11*self.scale)), padding=4)
        self.job = None
        self.closed = False
        self.interfaces = []
        self.discovery_results = queue.Queue(maxsize=1)
        self.scanning = False
        # Paint text on the parent's composited wallpaper. Native input widgets
        # remain independent so keyboard focus survives status/theme refreshes.
        self.surface = SettingsSurface(self.window, self.scale)
        self.canvas = self.surface.canvas
        self.wheel_binding = app.root.bind("<MouseWheel>", self.wheel, add="+")
        self.surface.label(10, 8, "远程连接", size=20, bold=True)
        self.surface.label(10, 42, "统一设备服务 · 手机网页 / 原生客户端 · 设备权限单独管理")
        self.surface.label(10, 68, "HTTPS 默认开启。首次访问需在手机信任本机 CA；不会自动修改系统。", size=11)
        config = app.store.snapshot()["phone"]
        self.interface = ttk.Combobox(self.canvas, state="readonly")
        self.surface.widget(self.interface, 10, 104, 365)
        self.surface.button(385, 104, 108, "刷新网卡", self.scan)
        self.surface.label(10, 150, "端口")
        self.port = tk.StringVar(value=str(config["port"]))
        self.surface.widget(ttk.Entry(self.canvas, textvariable=self.port), 46, 142, 80)
        self.privacy = ttk.Combobox(self.canvas, state="readonly", values=["状态与摘要", "仅状态（隐藏正文）"])
        self.privacy.current(0 if config["privacy_mode"] == "summary" else 1)
        self.surface.widget(self.privacy, 142, 142, 351)
        self.images = tk.BooleanVar(value=config["share_personalization_images"])
        self.surface.checkbox(10, 190, "共享当前壁纸、挂件和空闲角色图片", self.images)
        self.tls = tk.BooleanVar(value=config.get('tls_enabled',True))
        self.surface.checkbox(10, 220, 'HTTPS 加密（关闭后仅兼容原只读查看）', self.tls)
        self.surface.button(10, 260, 130, "保存并开启", self.start)
        self.surface.button(152, 260, 108, "关闭访问", self.stop)
        self.surface.button(274, 260, 118, '导出证书 / 指纹', self.export_certificate)
        self.surface.button(404, 260, 89, '证书设置', self.certificate_settings)
        self.surface.label(10, 302, "更改设置需先关闭访问；HTTPS 首次需重新配对，旧 HTTP 凭据不升级。", size=11)
        self.message = self.surface.label(10, 331, "", size=12)
        self.address = tk.StringVar()
        self.surface.widget(ttk.Entry(self.canvas, textvariable=self.address, state="readonly"), 10, 388, 365)
        self.surface.button(385, 388, 108, "复制网址", self.copy)
        self.surface.button(10, 432, 174, "连接新手机 / 换码", self.pair)
        self.code = self.surface.label(196, 437, "", size=18, bold=True, width=300)
        self.surface.label(10, 476, "手机浏览器打开网址输入验证码；长期保存后可单独授予编辑、授权权限。", size=11)
        self.device_key = None
        self.device_buttons = []
        self.apply_theme("#f5f7fc", "#26364b")
        self.scan()
        self.refresh()

    def apply_theme(self, background, foreground):
        if self.theme == (background, foreground):
            return
        self.theme = background, foreground
        self.window.configure(bg=background)
        self.surface.set_theme(background, foreground)
        # Native Windows buttons retain their light surface even on dark wallpaper.
        self.style.configure("Phone.TButton", foreground="#243349", padding=4)

    def layout(self, x, y, width, height, background, foreground, backdrop=None):
        self.apply_theme(background, foreground)
        self.window.place(x=round(x), y=round(y), width=round(width), height=round(height))
        self.surface.set_backdrop(backdrop, (round(x), round(y)), (round(width), round(height)))

    def draw_devices(self, devices):
        self.surface.clear_group("devices")
        self.device_buttons = []
        self.surface.label(10, 516, f"配对设备 {len(devices)}/3 · 长期保存 {sum(bool(s.get('remembered')) for s in devices)}/3", bold=True, group="devices")
        y = 548
        for item in devices:
            saved = item.get("remembered", False)
            label = ("长期" if saved else "临时") + (" · 在线" if item["online"] else " · 离线")
            if saved and item["online"] and not item.get("cookie_confirmed"):
                label += " · 等待手机保存"
            self.surface.label(10, y+5, item["name"][:16]+" · "+label, width=275, group="devices")
            self.device_buttons.append(self.surface.button(425, y, 68, "删除" if saved else "撤销",
                lambda d=item["id"]: self.revoke(d), group="devices"))
            if not saved:
                self.device_buttons.append(self.surface.button(319, y, 96, "长期保存",
                    lambda d=item["id"]: self.remember(d), group="devices"))
            else:
                self.device_buttons.append(self.surface.button(319,y,96,'设备权限',lambda d=dict(item):self.permissions(d),group='devices'))
            y += 60
        self.surface.label(10, y+10, "无法访问：检查两端网络互通、访客隔离和 Windows 私人网络防火墙。\n不能选到网卡时，不会自动放宽系统网络策略。", size=11, group="devices")
        self.surface.set_height(y+80)

    def wheel(self, event):
        if not self.closed and self.window.winfo_ismapped():
            self.canvas.yview_scroll(-int(event.delta/120), "units")

    def scan(self):
        if self.scanning:
            return
        self.scanning = True
        self.message.configure(text="正在读取私人网络网卡…")
        discovery, results = self.app.phone.discovery, self.discovery_results
        def work():
            try:
                result = discovery()
            except Exception:
                result = []
            # Do not retain the Tk panel until a network worker exits.
            results.put(result)
        threading.Thread(target=work, name="solo-phone-discovery", daemon=True).start()

    def start(self):
        try:
            index = self.interface.current()
            if index < 0:
                raise ValueError("请选择私人网络网卡。")
            config = {**self.app.store.snapshot()['phone'], "enabled": True, "interface_id": self.interfaces[index].id, "port": int(self.port.get()),
                      "privacy_mode": "summary" if self.privacy.current() == 0 else "status_only",
                      "share_personalization_images": self.images.get(), 'tls_enabled':self.tls.get()}
            if self.app.phone.thread and self.app.phone.thread.is_alive():
                raise ValueError("请先关闭访问，等待关闭后再应用设置。")
            config = normalize(config, strict=True)
            self.app.store.update(phone=config)
            self.app.phone.start(config)
        except (ValueError, OSError) as error:
            self.message.configure(text=str(error))
            self.local_error = str(error)
        else:
            self.local_error = ""

    def stop(self):
        self.local_error = ""
        try:
            self.app.store.update(phone={**self.app.store.snapshot()["phone"], "enabled": False})
        except (ValueError, OSError) as error:
            self.local_error = str(error)
        finally:
            self.app.phone.stop()

    def pair(self):
        try:
            self.app.phone.command("pair")
            self.local_error = ""
        except (ValueError, OSError) as error:
            self.local_error = str(error)

    def remember(self, device):
        if not messagebox.askyesno("长期保存此设备", "允许此设备以后免验证码连接？\n默认只读。保存后可单独授予任务编辑和 Agent 操作权限。\n请保持页面打开直到保存完成，可随时撤销。", parent=self.app.root):
            return
        try:
            self.app.phone.command("remember", device)
            self.local_error = ""
        except (ValueError, OSError) as error:
            self.local_error = str(error)

    def revoke(self, device):
        try:
            self.app.phone.command("revoke", device)
            self.local_error = ""
        except (ValueError, OSError) as error:
            self.local_error = str(error)

    def copy(self):
        if self.address.get():
            self.window.clipboard_clear()
            self.window.clipboard_append(self.address.get())

    def export_certificate(self):
        status=self.app.phone.snapshot_status()
        source=status.get('certificate')
        if not source:
            messagebox.showinfo('信任证书','请先开启 HTTPS 服务，生成本机证书。',parent=self.app.root)
            return
        filename=filedialog.asksaveasfilename(parent=self.app.root,title='导出本机公开 CA（不含私钥）',
                    initialfile='SOLO-device-CA.crt',defaultextension='.crt',filetypes=[('证书','*.crt')])
        if filename:
            try: shutil.copyfile(source,filename)
            except (OSError,shutil.SameFileError) as error:
                messagebox.showerror('导出失败',str(error),parent=self.app.root);return
        messagebox.showinfo('证书指纹核对','SHA-256：\n'+status.get('fingerprint','')+
            '\n\n本地 CA：通过可信方式传到手机并安装信任。\n已导入站点证书：由组织部署对应的 CA 信任或使用公开可信证书。\n仅导出公开证书，不含私钥。不要忽略浏览器证书警告。',parent=self.app.root)

    def certificate_settings(self):
        if self.app.phone.thread and self.app.phone.thread.is_alive():
            messagebox.showinfo('请先停止访问','证书和访问地址只在服务停止后修改。',parent=self.app.root);return
        config=self.app.store.snapshot()['phone'];dialog=tk.Toplevel(self.app.root)
        dialog.title('HTTPS 证书与访问地址');dialog.transient(self.app.root)
        body=ttk.Frame(dialog,padding=20);body.pack(fill='both',expand=True)
        mode=tk.StringVar(value=config['certificate_mode']);host=tk.StringVar(value=config['tls_host'])
        ttk.Radiobutton(body,text='本机生成 CA（首次需在手机建立信任）',variable=mode,value='local').pack(anchor='w',pady=5)
        ttk.Radiobutton(body,text='使用已导入的可信站点证书（PEM）',variable=mode,value='imported').pack(anchor='w',pady=5)
        ttk.Label(body,text='访问主机名（可留空使用网卡 IP；DNS 需事先指向此电脑）').pack(anchor='w',pady=8)
        ttk.Entry(body,textvariable=host,width=54).pack(fill='x')
        note=ttk.Label(body,text='支持精确 SAN 名称。不会修改系统证书、DNS 或防火墙。');note.pack(anchor='w',pady=10)
        def load():
            cert=filedialog.askopenfilename(parent=dialog,title='选择站点 PEM 证书链',filetypes=[('PEM 证书','*.pem *.crt'),('所有文件','*')])
            if not cert:return
            key=filedialog.askopenfilename(parent=dialog,title='选择匹配的 PEM 私钥',filetypes=[('PEM 私钥','*.pem *.key'),('所有文件','*')])
            if not key:return
            try:
                from ..remote.tls import import_certificate
                import_certificate(self.app.store.root,cert,key)
            except Exception:
                messagebox.showerror('导入失败','请检查 PEM 格式、证书与私钥是否匹配，以及私钥是否带口令。',parent=dialog);return
            mode.set('imported');note.configure(text='已验证匹配并通过 Windows DPAPI 加密保存私钥副本。')
        def save():
            try:self.app.store.update(phone=normalize({**config,'tls_host':host.get().strip(),'certificate_mode':mode.get()},strict=True))
            except (ValueError,OSError) as error:messagebox.showerror('设置无效',str(error),parent=dialog);return
            dialog.destroy()
        ttk.Button(body,text='导入证书与私钥',command=load).pack(side='left',pady=8)
        ttk.Button(body,text='保存',command=save).pack(side='right',pady=8);dialog.grab_set()

    def permissions(self, device):
        if not self.app.phone.secure:
            messagebox.showinfo('需要 HTTPS','HTTP 模式不开放操作权限。',parent=self.app.root);return
        from ..remote.permissions import normalize
        dialog=tk.Toplevel(self.app.root);dialog.title('设备权限 · '+device['name']);dialog.transient(self.app.root)
        body=ttk.Frame(dialog,padding=20);body.pack(fill='both',expand=True)
        ttk.Label(body,text='仅授予此设备需要的能力。来源不支持时不会出现操作按钮。').pack(anchor='w',pady=8)
        titles=dict(view_tasks='查看完整待办（含正文）',edit_tasks='新建、编辑和启停待办',delete_tasks='删除待办',
                    reminder_act='知晓和暂缓提醒',agent_approve='同意 / 拒绝 Agent 单次授权',agent_answer='回答 Agent 问题',agent_stop='停止 Agent 执行')
        grants=normalize(device.get('grants'));values={}
        for key,title in titles.items():
            values[key]=tk.BooleanVar(value=grants[key])
            ttk.Checkbutton(body,text=title,variable=values[key]).pack(anchor='w',pady=5)
        def save():
            try:self.app.phone.command('grants',{'id':device['id'],'grants':{k:v.get() for k,v in values.items()}})
            except ValueError as error:messagebox.showerror('保存失败',str(error),parent=dialog);return
            dialog.destroy()
        ttk.Label(body,text='全局“仅状态”模式会屏蔽这些权限。撤销立即阻止后续操作。').pack(anchor='w',pady=8)
        ttk.Button(body,text='保存权限',command=save).pack(anchor='e');dialog.grab_set()

    def refresh(self):
        if self.closed:
            return
        try:
            self.interfaces = self.discovery_results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.scanning = False
            self.interface.configure(values=[f"{i.name} · {i.address} · 私人网络" for i in self.interfaces])
            wanted = self.app.store.snapshot()["phone"]["interface_id"]
            if self.interfaces:
                self.interface.current(next((n for n, i in enumerate(self.interfaces) if i.id == wanted), 0))
        status = self.app.phone.snapshot_status()
        self.message.configure(text=getattr(self, "local_error", "") or status.get("command_error", "") or ("正在读取网卡…" if self.scanning else status["message"]))
        self.address.set(status.get("address", "") if status["state"] == "running" else "")
        code = status.get("code", "")
        self.code.configure(text=f"{code[:4]} {code[4:]}  {status.get('code_seconds', 0)}s" if code else "未生成 / 已使用")
        devices = status.get("devices", [])
        key = tuple((s["id"], s["name"], s["online"], s.get("remembered"), s.get("cookie_confirmed"),str(s.get('grants'))) for s in devices)
        if key != self.device_key:
            self.device_key = key
            self.draw_devices(devices)
        self.job = self.window.after(300, self.refresh)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.job:
            self.window.after_cancel(self.job)
        self.app.root.unbind("<MouseWheel>", self.wheel_binding)
        self.window.destroy()
        self.app.phone_dialog = None
