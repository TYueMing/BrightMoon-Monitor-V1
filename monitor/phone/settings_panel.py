"""Embedded Remote Connection page. Widgets and confirmation remain on Tk."""
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
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
        self.surface.label(10, 42, "同一可互通 Wi-Fi / 局域网 · 网页只读，不执行 Agent 操作")
        self.surface.label(10, 68, "仅用于可信私人网络。HTTP 传输未加密；验证码不是加密连接。", size=11)
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
        self.surface.button(10, 224, 130, "保存并开启", self.start)
        self.surface.button(152, 224, 108, "关闭访问", self.stop)
        self.surface.label(10, 266, "更改设置需先关闭访问；长期保存的设备重启后仍可连接。", size=11)
        self.message = self.surface.label(10, 295, "", size=12)
        self.address = tk.StringVar()
        self.surface.widget(ttk.Entry(self.canvas, textvariable=self.address, state="readonly"), 10, 352, 365)
        self.surface.button(385, 352, 108, "复制网址", self.copy)
        self.surface.button(10, 396, 174, "连接新手机 / 换码", self.pair)
        self.code = self.surface.label(196, 401, "", size=18, bold=True, width=300)
        self.surface.label(10, 440, "手机浏览器打开上方网址，输入验证码；成功后验证码立即失效。", size=11)
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
        self.surface.label(10, 480, f"配对设备 {len(devices)}/3 · 长期保存 {sum(bool(s.get('remembered')) for s in devices)}/3", bold=True, group="devices")
        y = 512
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
        def work():
            try:
                result = self.app.phone.discovery()
            except Exception:
                result = []
            self.discovery_results.put(result)
        threading.Thread(target=work, name="solo-phone-discovery", daemon=True).start()

    def start(self):
        try:
            index = self.interface.current()
            if index < 0:
                raise ValueError("请选择私人网络网卡。")
            config = {"enabled": True, "interface_id": self.interfaces[index].id, "port": int(self.port.get()),
                      "privacy_mode": "summary" if self.privacy.current() == 0 else "status_only",
                      "share_personalization_images": self.images.get()}
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
        if not messagebox.askyesno("长期保存此设备", "允许此手机以后免验证码查看实况？\n仅保存你信任的设备；请保持手机页面打开直到保存完成。\n可随时在这里删除。HTTP 仍未加密。", parent=self.app.root):
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
        key = tuple((s["id"], s["name"], s["online"], s.get("remembered"), s.get("cookie_confirmed")) for s in devices)
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
