"""Render only a fake phone settings window; never open a LAN listener."""
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import tkinter as tk
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor.settings import SettingsStore
from monitor.phone.service import PhoneService
from monitor.phone.network import Interface
from monitor.phone.settings_panel import PhoneSettings
from monitor.UI.display import enable_dpi_awareness
from render_wallpaper_preview import capture, artwork
from PIL import Image

def main():
    enable_dpi_awareness()
    with tempfile.TemporaryDirectory(prefix="solo-phone-dialog-") as folder:
        from unittest.mock import Mock
        from monitor.app import MonitorApp
        root=tk.Tk()
        store=SettingsStore(folder)
        store.update(monitor_sound_enabled=False,task_sound_enabled=False)
        hub=Mock();hub.read.return_value={"runs":[],"actions":[],"source_states":{}}
        app=MonitorApp(root,store,hub)
        wallpaper=Path(folder)/"wallpaper.png"
        artwork(wallpaper)
        app.photo=Image.open(wallpaper).convert("RGB")
        store.update(panel_color="#d9eaff", panel_transparency=65)
        interface=Interface("fixture", "Wi-Fi（演示）", "192.168.1.23", 24, "Private")
        app.phone.discovery=lambda:[interface]
        app.phone.report(state="running",message="已开启 · 只读访问（界面演示）",address="http://192.168.1.23:18765/",
                         code="12345678",code_seconds=120,devices=[{"id":"fixture","name":"我的手机","online":True,"remembered":True,"cookie_confirmed":True}])
        try:
            app.show_phone_settings()
            root.update()
            panel=app.phone_dialog
            panel.window.after_cancel(panel.job)
            panel.refresh()
            target=Path(__file__).resolve().parents[2]/"preview/phone-settings.png"
            target.parent.mkdir(exist_ok=True)
            capture(root).save(target)
            panel.canvas.yview_moveto(1)
            capture(root).save(target.with_name("phone-devices.png"))
            store.update(panel_color="#18253a", panel_transparency=25)
            app.panel.draw()
            panel.canvas.yview_moveto(0)
            capture(root).save(target.with_name("phone-settings-dark.png"))
            print(target)
        finally:
            app.close()

if __name__=="__main__":main()
