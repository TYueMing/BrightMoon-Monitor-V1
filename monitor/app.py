"""Small native settings window and the independently copied Dynamic Island."""
import tkinter as tk
import sqlite3
import argparse
import time
import queue
from concurrent.futures import ThreadPoolExecutor
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageTk

from .settings import SettingsStore
from .src.app_sources import app_source, ICON_ROOT
from .src.lifecycle import InstanceLock
from .src.paths import initialize_installed_assets, PROJECT_ROOT, is_installed
from .src.startup import StartupManager
from .src.desktop import DesktopHost, notify_existing, stop_existing
from .src.display_history import DisplayHistory
from .src.source_config import SOURCE_IDS
from .src.source_hub import SourceHub
from .UI.display import enable_dpi_awareness, window_scale
from .multi_diverse.UI.island import CollaborativeIsland as DynamicIsland
from .multi_diverse.service import PeerService
from .src.task_identity import task_identity
from .UI.settings_panel import SettingsPanel
from .UI.wallpaper_picker import WallpaperPicker
from .src.wallpapers import read_photo
from .src.rope_assets import RopeCatalog
from .src.cartoon_assets import CartoonCatalog
from .music.play.catalog import sound_files, sound_label
from .music.play.preview import SoundPreview
from .tasks.service import TaskService
from .phone.service import PhoneService

INK = "#1c2940"
MUTED = "#7b8597"
BLUE = "#426de7"


class MonitorApp:
    def __init__(self, root, store=None, hub=None):
        self.root = root
        self.store = store or SettingsStore()
        self.hub = hub or SourceHub(self.store.snapshot)
        self.phone = PhoneService(self.store.root)
        self.peers = PeerService(self.store.root)
        self.peer_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="solo-owner-command")
        self.peer_job = None
        self.phone_dialog = None
        self.tasks = TaskService(self.store.root, self.store.snapshot)
        self.task_window = None
        self.task_warning = ""
        self._agent_snapshot = {}
        self.closed = False
        self.desktop = None
        self.startup = StartupManager()
        self.startup_enabled,self.startup_status = self.startup.status()
        self.job = self.notice_job = self.preview_job = None
        self.source_status = {}
        self.photo = self.island_photo = None
        self.rope_catalog = RopeCatalog(self.store.root)
        self.rope_items = []
        self.rope_photo = None
        self.cartoon_catalog = CartoonCatalog(self.store.root)
        self.cartoon_items = []
        self.cartoon_photo = None
        self.wallpapers = []
        self.dialog = self.sounds_window = self.wallpaper_window = None
        self.preview = SoundPreview()
        self.notice_text = "设置自动保存 · × 收到托盘 · 右键托盘退出"
        self.notice_color = MUTED
        self.scale = min(window_scale(root), max(.7, min((root.winfo_screenheight()-110)/712,
                                                       (root.winfo_screenwidth()-80)/812)))
        self.s = lambda value: round(value*self.scale)
        root.title("Bright Moon Monitor")
        root.configure(bg="white")
        root.geometry(f"{self.s(812)}x{self.s(712)}")
        root.minsize(self.s(792), self.s(690))
        self.font = ("Microsoft YaHei UI", -self.s(13))
        self.small = ("Microsoft YaHei UI", -self.s(11))
        self.bold = ("Microsoft YaHei UI", -self.s(14), "bold")
        self.icon = ImageTk.PhotoImage(Image.open(ICON_ROOT / "MonitorSolo_logo.png"), master=root)
        root.iconphoto(True, self.icon)
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("Solo.TCombobox", font=self.font, padding=self.s(5), fieldbackground="white")
        root.option_add("*TCombobox*Listbox.font", self.font)
        self.panel = self.canvas = SettingsPanel(root,self)
        self.panel.pack(fill="both",expand=True)
        self.load_background()
        self.load_rope()
        self.load_cartoon()
        self.island_window = tk.Toplevel(root)
        self.island_window.title("Bright Moon Monitor · 灵动岛")
        self.display_history = DisplayHistory.for_data_root(self.store.root)
        self.history_warning = ""
        self.island = DynamicIsland(self.island_window,self.read_snapshot,self.send_command,
                                    display_history=self.display_history,
                                    background_reader=lambda:self.island_photo,
                                    rope_reader=lambda:self.rope_photo,
                                    cartoon_reader=lambda:self.cartoon_photo,
                                    view_publisher=self.publish_phone, phone_status=self.phone.snapshot_status,peer_catalog=self.peers.catalog_snapshot)
        root.protocol("WM_DELETE_WINDOW",self.hide_to_tray)
        self.refresh_status()
        if self.store.warning:
            self.notice(self.store.warning,error=True)
        if self.store.snapshot()["phone"]["enabled"]:
            self.phone.start(self.store.snapshot()["phone"])

        if self.store.snapshot()["peers"]["enabled"]:
            self.peers.start(self.store.snapshot()["peers"])
        self.drain_peer_commands()

    def publish_phone(self,snapshot,visible,focus,desktop_visible):
        # Peer re-sharing remains denied unless explicitly implemented/granted.
        local=[r for r in visible if not r.get("remote_origin")]
        self.phone.publish({**snapshot,"runs":[r for r in snapshot.get("runs",[]) if not r.get("remote_origin")]},local,focus,desktop_visible)

    def show_phone_settings(self):
        self.panel.select_tab("remote")

    def read_snapshot(self):
        try:
            snapshot = self.hub.read()
            if not isinstance(snapshot, dict):
                raise ValueError("Invalid monitor snapshot")
            self._agent_snapshot = snapshot
        except Exception:
            snapshot = {**self._agent_snapshot, "status":"error", "actions":[],
                        "runs":[{**r,"connection_error":True} for r in self._agent_snapshot.get("runs",[])]}
        reminders = self.tasks.read()
        # Do not mutate cached Agent snapshots, revisions, commands or identity.
        local = {**snapshot, "runs":[*reminders,*snapshot.get("runs",[])],
                "source_states":snapshot.get("source_states",{}), "preferences":self.store.snapshot(),
                "next_reminder":self.tasks.upcoming, "upcoming_reminder_count":self.tasks.upcoming_count,
                "task_data_error":bool(self.tasks.error)}
        self.peers.publish_local(local)
        imported=self.peers.snapshot() if self.store.snapshot()['peers']['show_remote'] else []
        return {**local,"runs":[*local["runs"],*imported]}

    def send_command(self, action, run_id, payload):
        if run_id.startswith("peer:"):
            run=next((r for r in self.peers.catalog_snapshot() if r["run_id"]==run_id),None)
            if not run or run.get("connection_error"):raise ValueError("远程来源已离线")
            return self.peers.submit(run,action,payload)
        if action == "local_reminder":
            return self.tasks.send(run_id,payload)
        return self.hub.send(action,run_id,payload)

    def drain_peer_commands(self):
        if self.closed:return
        while not self.peers.owner_commands.empty():
            cmd=self.peers.owner_commands.get_nowait()
            def finish(result,c=cmd):
                try:self.peers.command('result',dict(id=c['id'],peer=c['peer'],result=result))
                except ValueError:pass
            try:
                status=self.peers.snapshot_status()
                peer=next((p for p in status.get('peers',[]) if p['id']==cmd['peer']),None)
                from .multi_diverse.protocol import OPERATIONS
                if not self.peers.authorized(cmd):
                    raise ValueError('权限已撤销或命令过期')
                target=cmd['target'];payload=cmd['payload'];operation=cmd['operation']
                if not isinstance(payload,dict) or set(payload)-{'confirmation_id','expected_revision','cycle','action','answers','answer'}:
                    raise ValueError('操作参数无效')
                if operation=='local_reminder':
                    self.tasks.store.act(target['run_id'][5:],payload.get('cycle'),payload.get('action'),command_id=cmd['peer']+':'+cmd['id'])
                    finish(dict(status='succeeded',message='提醒操作已完成'))
                else:
                    def work(c=cmd,done=finish):
                        try:
                            if not self.peers.authorized(c):raise ValueError('权限已撤销或操作过期')
                            self.hub.send_target(c['operation'],c['target'],c['payload'])
                            done(dict(status='accepted',message='已提交来源，等待实际状态确认'))
                        except ValueError as error:
                            unknown=getattr(error,'code','') in ('DISCONNECTED','BRIDGE_ERROR')
                            done(dict(status='outcome_unknown' if unknown else 'rejected',message='结果待来源核对，请勿重复提交' if unknown else '来源请求已更新或已拒绝'))
                        except Exception:done(dict(status='outcome_unknown',message='结果待来源核对，请勿重复提交'))
                    self.peer_executor.submit(work)
            except (ValueError,OSError,sqlite3.Error):finish(dict(status='rejected',message='请求已过期、冲突或权限已撤销'))
        self.peer_job=self.root.after(100,self.drain_peer_commands)

    def task_dialog(self, task=None):
        if self.task_window and self.task_window.winfo_exists():
            self.task_window.lift()
            return
        from .UI.tasks.editor import TaskEditor
        self.task_window = TaskEditor(self,task)

    def task_toggle(self, task, enabled):
        try:
            self.tasks.store.save({**task,"enabled":enabled},task["id"],expected_revision=task["revision"])
            self.tasks_changed()
        except (OSError,ValueError,sqlite3.Error) as error:
            self.notice(str(error),error=True)

    def tasks_changed(self):
        self.island._poll()
        self.panel.draw()

    def toggle_task_monitor(self, enabled):
        if self.save(task_monitor_enabled=enabled):
            self.tasks_changed()  # Revoke cards and sound immediately.

    def toggle_resident(self, enabled):
        if self.save(island_resident=enabled):
            self.island._poll()

    def save_rope_position(self, value):
        if self.save(rope_position=value):
            self.island.snapshot["preferences"] = self.store.snapshot()
            self.island._sync_pendant()

    def label(self, parent, text, *, font=None, color=INK, **kwargs):
        return tk.Label(parent,text=text,font=font or self.font,fg=color,bg="white",**kwargs)

    def button(self, parent, text, command, *, accent=False):
        return tk.Button(parent,text=text,command=command,font=self.small,relief="flat",bd=0,
                         bg="#edf2ff" if accent else "#f4f5f8",fg=BLUE if accent else INK,
                         activebackground="#e5ebfa",activeforeground=INK,cursor="hand2",
                         padx=self.s(12),pady=self.s(6),highlightthickness=0)

    def notice(self,text,*,error=False):
        if self.closed:
            return
        self.notice_text,self.notice_color = text,"#c24c43" if error else BLUE
        if self.notice_job:
            self.root.after_cancel(self.notice_job)
        self.notice_job = self.root.after(6000,self.clear_notice)
        self.panel.draw()

    def clear_notice(self):
        self.notice_job = None
        warning = self.display_history.error
        self.notice_text,self.notice_color = (warning,"#c24c43") if warning else ("设置自动保存 · × 收到托盘 · 右键托盘退出",MUTED)
        self.panel.draw()

    def save(self, **changes):
        try:
            self.store.update(**changes)
            self.notice("已保存")
            return True
        except (OSError, ValueError) as exc:
            self.notice(f"未能保存：{exc}",error=True)
            return False

    def toggle_source(self, app_id, enabled):
        try:
            self.store.set_source(app_id,enabled=enabled)
        except (OSError,ValueError) as exc:
            self.notice(f"未能保存：{exc}",error=True)
            return
        self.hub.read()  # immediately revoke disabled sources and stale actions
        self.island._poll()
        self.notice("已开启监控" if enabled else "已关闭监控，Agent 任务继续运行")

    def toggle_sound(self,enabled):
        self.save(monitor_sound_enabled=enabled)
        self.panel.draw()

    def save_delay(self,minutes):
        if minutes != self.store.snapshot()["monitor_hide_minutes"]:
            self.save(monitor_hide_minutes=minutes)
        self.panel.draw()

    def save_transparency(self,percent):
        if percent != self.store.snapshot()["panel_transparency"]:
            self.save(panel_transparency=percent)
        self.panel.draw()

    def save_panel_color(self, color):
        self.save(panel_color=color)

    def choose_panel_color(self):
        _, color = colorchooser.askcolor(parent=self.root, title="控制面板颜色",
                                        initialcolor=self.store.snapshot()["panel_color"])
        if color:
            self.save_panel_color(color)

    def load_rope(self):
        self.rope_items = self.rope_catalog.list()
        prefs = self.store.snapshot()
        self.rope_photo = None
        if prefs["rope_enabled"] and prefs["rope_image"]:
            try:
                self.rope_photo = self.rope_catalog.read(prefs["rope_image"])
            except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
                self.notice("已选卡通无法读取，请在悬挂卡通中重新选择。", error=True)
        if hasattr(self, "island"):
            self.island._sync_pendant()
        self.panel.draw()

    def toggle_rope(self, enabled):
        # Pick the first readable PNG only when no previous selection exists.
        # A missing saved selection is retained so replacing its file restores it.
        self.rope_items = self.rope_catalog.list()
        reference = self.store.snapshot()["rope_image"]
        if enabled and not reference and self.rope_items:
            reference = self.rope_items[0]
        if self.save(rope_enabled=enabled, rope_image=reference):
            self.load_rope()

    def select_rope(self, reference):
        try:
            self.rope_catalog.read(reference)
        except (OSError, ValueError, RuntimeError, Image.DecompressionBombError):
            self.notice("这张卡通无法读取，请刷新列表后重试。", error=True)
            return False
        if self.save(rope_image=reference):
            self.load_rope()
            return True
        return False

    def select_sound(self,name):
        return self.save(monitor_completion_sound=name)

    def load_cartoon(self):
        self.cartoon_items = self.cartoon_catalog.list()
        prefs = self.store.snapshot()
        reference = prefs["cartoon_image"]
        # Only choose a default when there was no selection. Deleted selections
        # stay saved so a refresh never silently changes the user's character.
        if not reference and self.cartoon_items:
            reference = self.cartoon_items[0]
            if self.save(cartoon_image=reference):
                prefs = self.store.snapshot()
        self.cartoon_photo = None
        if prefs["cartoon_enabled"] and reference:
            try:
                self.cartoon_photo = self.cartoon_catalog.read(reference)
            except (OSError,ValueError,RuntimeError,Image.DecompressionBombError):
                self.notice("已选空闲角色不可用，请刷新列表或重新选择。",error=True)
        if hasattr(self,"island"):
            self.island._poll()
        self.panel.draw()

    def toggle_cartoon(self, enabled):
        if self.save(cartoon_enabled=enabled):
            self.load_cartoon()

    def select_cartoon(self, reference):
        try:
            self.cartoon_catalog.read(reference)
        except (OSError,ValueError,RuntimeError,Image.DecompressionBombError) as error:
            self.notice(str(error),error=True)
            return False
        if self.save(cartoon_image=reference):
            self.load_cartoon()
            return True
        return False

    def choose_cartoon(self):
        path = filedialog.askopenfilename(parent=self.root,title="选择已抠图的空闲角色",
            filetypes=[("透明背景图像","*.png *.webp")])
        if not path:
            return
        try:
            reference = self.cartoon_catalog.import_image(path)
            self.select_cartoon(reference)
        except (OSError,ValueError,RuntimeError,Image.DecompressionBombError) as error:
            self.notice(f"导入失败：{error}",error=True)

    def save_island_color(self,color):
        if self.save(monitor_background_color=color):
            self.island._poll()

    def choose_island_color(self):
        _,color = colorchooser.askcolor(parent=self.root,title="灵动岛颜色",
                                       initialcolor=self.store.snapshot()["monitor_background_color"])
        if color:
            self.save_island_color(color)

    def save_island_transparency(self,percent):
        if self.save(monitor_opacity=100-percent):
            self.island._poll()

    def center_dialog(self,dialog):
        dialog.update_idletasks()
        width,height = dialog.winfo_reqwidth(),dialog.winfo_reqheight()
        x = self.root.winfo_rootx()+(self.root.winfo_width()-width)//2
        y = self.root.winfo_rooty()+(self.root.winfo_height()-height)//2
        x = max(0,min(x,self.root.winfo_screenwidth()-width))
        y = max(0,min(y,self.root.winfo_screenheight()-height-40))
        dialog.geometry(f"+{x}+{y}")

    def sound_dialog(self, kind="agent"):
        if self.sounds_window and self.sounds_window.winfo_exists():
            self.sounds_window.lift()
            return
        dialog = self.sounds_window = tk.Toplevel(self.root)
        dialog.title("Bright Moon Monitor · 提示音")
        dialog.configure(bg="white")
        dialog.resizable(False,False)
        dialog.transient(self.root)
        body = tk.Frame(dialog,bg="white",padx=self.s(24),pady=self.s(24))
        body.pack()
        self.label(body,"待办任务提示音 · 每轮3遍" if kind=="task" else "Agent 完成提示音 · 1遍",font=self.bold).pack(anchor="w")
        self.label(body,"选中后自动保存；试听不会改变当前选择。",font=self.small,color=MUTED).pack(anchor="w",pady=self.s(12))
        field = "task_reminder_sound" if kind=="task" else "monitor_completion_sound"
        self.sound_choice = tk.StringVar(value=self.store.snapshot()[field])
        for name in sound_files():
            row = tk.Frame(body,bg="white")
            row.pack(fill="x",pady=self.s(6))
            def select(n=name):
                if not self.save(**{field:n}):
                    self.sound_choice.set(self.store.snapshot()[field])
            tk.Radiobutton(row,text=sound_label(name),variable=self.sound_choice,value=name,
                command=select,bg="white",activebackground="white",fg=INK,font=self.font,
                selectcolor="#eef3ff",highlightthickness=0).pack(side="left")
            self.button(row,"试听",lambda n=name:self.preview_sound(n)).pack(side="right",padx=(self.s(140),0))
        self.preview_label = self.label(body,"点击试听可播放，再次点击同一提示音可停止。",font=self.small,color=MUTED)
        self.preview_label.pack(anchor="w",pady=self.s(14))
        self.button(body,"完成",self.close_sounds,accent=True).pack(anchor="e")
        dialog.protocol("WM_DELETE_WINDOW",self.close_sounds)
        self.center_dialog(dialog)
        dialog.grab_set()
        self.watch_preview()

    def preview_sound(self,name):
        try:
            if self.preview.current == name:
                self.preview.stop()
            else:
                self.preview.play(name)
        except (OSError,ValueError) as exc:
            self.notice(str(exc),error=True)

    def watch_preview(self):
        self.preview_job = None
        if self.sounds_window and self.sounds_window.winfo_exists():
            name = self.preview.current
            text = (f"正在试听：{sound_label(name)}" if name in sound_files() else
                    self.preview.error or "点击试听可播放，再次点击同一提示音可停止。")
            self.preview_label.configure(text=text)
            self.preview_job = self.root.after(150,self.watch_preview)

    def close_sounds(self):
        self.preview.stop()
        if self.preview_job:
            self.root.after_cancel(self.preview_job)
            self.preview_job = None
        if self.sounds_window and self.sounds_window.winfo_exists():
            self.sounds_window.destroy()
        self.sounds_window = None

    def source_dialog(self, app_id):
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.lift()
            return
        if app_id == "claude":
            from .claude_monitor.settings_ui import open_settings
            return open_settings(self)
        app = app_source(app_id)
        dialog = self.dialog = tk.Toplevel(self.root)
        dialog.title(app.name + " · 监控设置")
        dialog.configure(bg="white")
        dialog.resizable(False,False)
        dialog.transient(self.root)
        body = tk.Frame(dialog,bg="white",padx=self.s(24),pady=self.s(24))
        body.pack()
        self.label(body,app.name,font=self.bold).pack(anchor="w")
        hints = {
            "codex":"默认自动发现本机会话和实时服务。未连接实时服务时，\n授权请在 Codex 中处理。",
            "deepseek_harness":"默认读取 ~/.dsh/sessions。授权与答题需要安装随项目\n提供的 Harness 交互桥，详见 README。",
            "opencode":"默认自动发现本机数据库和服务。连接实时服务后\n可在灵动岛处理授权与问题。",
        }
        self.label(body,hints[app_id],font=self.small,color=MUTED,justify="left").pack(anchor="w",pady=self.s(12))
        self.label(body,"数据目录（留空表示自动发现）").pack(anchor="w")
        config = self.store.snapshot()["monitor_sources"][app_id]
        path = tk.StringVar(value=config["data_path"])
        row = tk.Frame(body,bg="white")
        row.pack(fill="x",pady=self.s(8))
        entry = tk.Entry(row,textvariable=path,font=self.font,width=38,relief="solid",bd=1)
        entry.pack(side="left",ipady=self.s(6))
        def browse():
            chosen = filedialog.askdirectory(parent=dialog,title="选择数据目录")
            if chosen:
                path.set(chosen)
        self.button(row,"浏览…",browse).pack(side="left",padx=(self.s(8),0))
        mode, url = None, None
        modes = {"自动发现":"auto","只读日志":"logs","本机实时服务":"manual"}
        if app_id == "codex":
            self.label(body,"连接方式").pack(anchor="w",pady=(self.s(12),0))
            mode = tk.StringVar(value=next(k for k,v in modes.items() if v == config["connection_mode"]))
            ttk.Combobox(body,textvariable=mode,values=list(modes),state="readonly",style="Solo.TCombobox").pack(fill="x",pady=self.s(8))
            self.label(body,"本机实时地址（仅手动模式需要）",font=self.small,color=MUTED).pack(anchor="w")
            url = tk.StringVar(value=config["server_url"])
            tk.Entry(body,textvariable=url,font=self.font,relief="solid",bd=1).pack(fill="x",ipady=self.s(6),pady=self.s(8))
        def apply():
            changes = {"data_path":path.get().strip()}
            if mode is not None:
                changes.update(connection_mode=modes[mode.get()],server_url=url.get().strip())
                if changes["connection_mode"] == "manual" and not changes["server_url"]:
                    messagebox.showerror("地址未填写","请填写本机 ws://127.0.0.1:端口 地址",parent=dialog)
                    return
            try:
                self.store.set_source(app_id,**changes)
            except (OSError,ValueError) as exc:
                messagebox.showerror("未能保存",str(exc),parent=dialog)
                return
            self.hub.read()
            dialog.destroy()
            self.notice("来源设置已保存")
        self.button(body,"保存设置",apply,accent=True).pack(anchor="e",pady=(self.s(16),0))
        self.center_dialog(dialog)
        dialog.grab_set()

    def choose_photo(self, *, target="app", parent=None):
        path = filedialog.askopenfilename(parent=parent or self.root,title="选择灵动岛壁纸" if target == "island" else "选择背景照片",filetypes=[
            ("照片","*.jpg *.jpeg *.png *.webp *.bmp"),("所有文件","*.*")])
        if not path:
            return False
        try:
            self.store.import_photo(path, target=target)
            self.load_background()
            self.notice("照片已复制并保存")
            return True
        except (OSError,ValueError,Image.DecompressionBombError) as exc:
            messagebox.showerror("无法使用这张照片",str(exc),parent=parent or self.root)
            return False

    def select_background(self, relative, *, target="app"):
        """Activate a previously imported wallpaper inside Solo's data folder."""
        try:
            self.store.select_photo(relative, target=target)
            self.load_background()
            self.notice("已切换历史壁纸")
            return True
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            self.notice(f"无法切换壁纸：{exc}", error=True)
            return False

    def wallpaper_dialog(self, target="app"):
        """Show a local-only wallpaper picker; no external file is copied here."""
        if self.wallpaper_window and self.wallpaper_window.winfo_exists():
            self.wallpaper_window.lift()
            return
        self.wallpapers = self.store.list_backgrounds()
        self.panel.draw()
        self.wallpaper_window = WallpaperPicker(self, target)

    def close_wallpaper_dialog(self):
        if self.wallpaper_window and self.wallpaper_window.winfo_exists():
            self.wallpaper_window.grab_release()
            self.wallpaper_window.destroy()
        self.wallpaper_window = None

    def set_background(self, mode):
        if self.save(background_mode=mode):
            self.load_background()

    def set_island_background(self, mode):
        if self.save(monitor_background_mode=mode):
            self.load_background()

    def load_background(self):
        prefs = self.store.snapshot()
        errors = []
        for attribute, prefix, label in (("photo", "background", "应用背景"),
                                         ("island_photo", "monitor_background", "灵动岛壁纸")):
            image = None
            if prefs[prefix+"_mode"] == "photo":
                try:
                    image = read_photo(self.store.background_path(prefs[prefix+"_photo"]))
                except (OSError,ValueError,Image.DecompressionBombError):
                    errors.append(label+"无法读取，暂时显示纯色")
            setattr(self, attribute, image)
        self.wallpapers = self.store.list_backgrounds()
        self.panel.background_key = None
        self.panel.draw()
        if hasattr(self, "island"):
            self.island._poll()
        if errors:
            self.notice("；".join(errors), error=True)

    def refresh_status(self):
        if self.closed:
            return
        warning = self.display_history.error
        if warning != self.history_warning:
            self.history_warning = warning
            if warning:
                self.notice(warning,error=True)
            else:
                self.notice("任务显示记录已恢复保存")
        # The island owns business sampling. Status rendering must not claim
        # reminder sound receipts or reread SQLite independently.
        snapshot = self.island.snapshot
        if self.tasks.error != self.task_warning:
            self.task_warning = self.tasks.error
            self.notice(self.task_warning or "待办数据已恢复",error=bool(self.task_warning))
        prefs = self.store.snapshot()
        self.startup_enabled,self.startup_status = self.startup.status()
        # A source read may fail before the first complete snapshot arrives.
        # Render that state without sampling again (which would consume reminders).
        source_states = snapshot.get("source_states")
        source_states = source_states if isinstance(source_states, dict) else {}
        for key in SOURCE_IDS:
            if key == "codeagent":
                continue
            state = source_states.get(key)
            state = state if isinstance(state, dict) else {}
            fallback = "error" if snapshot.get("status") == "error" else "connecting"
            status = state.get("status", fallback)
            enabled = prefs["monitor_sources"][key]["enabled"]
            count = sum(r.get("app_id")==key for r in self.island.selection.runs)
            if not enabled:
                text,color = "未开启",MUTED
            elif status in {"error","unreadable","dependency_missing"}:
                text,color = "连接异常 · 请检查来源设置","#c24c43"
            elif status == "connecting":
                text,color = "正在连接…",BLUE
            elif status == "setup_required":
                text,color = "请点设置安装 Hook", "#b57822"
            elif count:
                text,color = f"监看中 · {count} 个任务",BLUE
            else:
                text,color = "已开启 · 等待任务",BLUE
            self.source_status[key] = (text,color)
        self.panel.draw()
        self.job = self.root.after(700,self.refresh_status)

    def toggle_startup(self, enabled):
        try:
            self.startup.set_enabled(enabled)
            self.notice("开机自启已开启" if enabled else "开机自启已关闭")
        except (OSError,ValueError) as error:
            self.notice(f"无法更改开机自启：{error}",error=True)
        self.startup_enabled,self.startup_status = self.startup.status()
        self.panel.draw()

    def show_control_panel(self):
        if not self.closed:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def hide_to_tray(self):
        if self.closed:
            return
        if not self.desktop or not self.desktop.available:
            # Never leave an inaccessible background process if Explorer/tray
            # creation failed. An ordinary taskbar button remains reachable.
            self.notice("系统托盘不可用，已最小化到任务栏；可在个性化 → 通用中退出。",error=True)
            self.root.iconify()
            return
        self.close_sounds()
        self.close_wallpaper_dialog()
        if self.task_window:
            self.task_window.close()
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()
        self.root.withdraw()

    def close(self):
        if self.closed:
            return
        self.phone.stop()
        self.peers.stop()
        self.peer_executor.shutdown(wait=False,cancel_futures=True)
        if self.peer_job:self.root.after_cancel(self.peer_job)
        if self.phone_dialog:
            self.phone_dialog.close()
        self.close_sounds()
        self.close_wallpaper_dialog()
        if self.task_window:
            self.task_window.close()
        self.closed = True
        for job in (self.job,self.notice_job):
            if job:
                self.root.after_cancel(job)
        self.island.close()
        self.hub.close()
        self.tasks.close()
        if self.desktop:
            self.desktop.close()
        self.root.destroy()


def attach_desktop(app):
    icon = (PROJECT_ROOT/"monitor/icon_asset/MonitorSolo.ico" if is_installed()
            else PROJECT_ROOT/"packaging/assets/MonitorSolo.ico")
    app.desktop = DesktopHost(app.root,icon,app.show_control_panel,app.close,data_directory=app.store.root)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bright Moon Monitor (Solo)")
    parser.add_argument("--background",action="store_true")
    parser.add_argument("--quit",action="store_true",help="Gracefully stop this user's instance")
    parser.add_argument("--self-test",metavar="REPORT",help="Run isolated installed-runtime diagnostics")
    args = parser.parse_args(argv)
    if args.self_test:
        from .release_check import run_check
        return run_check(args.self_test)
    if args.quit:
        return 0 if stop_existing() else 1
    enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    lock = InstanceLock("monitor-solo")
    app = None
    try:
        if not lock.acquire():
            if not args.background and not notify_existing():
                messagebox.showinfo("Bright Moon Monitor","Solo 正在启动，请稍后再次点击快捷方式。",parent=root)
            return 0
        initialize_installed_assets()
        app = MonitorApp(root)
        try:
            attach_desktop(app)
        except OSError as error:
            app.notice(f"托盘不可用：{error}；仍可在个性化 → 通用退出。",error=True)
        if not args.background or not app.desktop:
            app.show_control_panel()
        root.mainloop()
        return 0
    finally:
        if app and not app.closed:
            app.close()
        elif app is None:
            root.destroy()
        lock.close()
