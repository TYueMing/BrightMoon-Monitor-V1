"""Source/frozen diagnostics with explicit temporary data and offline sources."""
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
import time
import traceback
from uuid import uuid4


def run_check(report_path):
    report = {"status":"failed","checks":[]}
    destination = Path(report_path).resolve()
    app = startup = None
    try:
        import tkinter as tk
        import sqlite3
        import zstandard
        import websocket
        from .app import MonitorApp, attach_desktop
        from .settings import SettingsStore
        from .src.paths import initialize_installed_assets, PROJECT_ROOT
        from .src.startup import StartupManager
        from .src.desktop import notify_existing
        from .UI.display import enable_dpi_awareness
        from .music.play.catalog import sound_files
        enable_dpi_awareness()
        report["checks"].append("runtime_imports")
        # ExitStack closes Tk, databases and registry probes before the temp
        # directory is removed, including on assertion/construction failures.
        with tempfile.TemporaryDirectory(prefix="MonitorSolo-release-check-") as temporary, ExitStack() as cleanup:
            data_directory = Path(temporary)/"data"
            initialize_installed_assets(data_directory)
            store = SettingsStore(data_directory)
            store.update(island_resident=True,monitor_sound_enabled=False,task_sound_enabled=False,
                         phone={"enabled":False},peers={"enabled":False},task_monitor_enabled=False)
            assert SettingsStore(store.root).snapshot()["island_resident"]
            assert len(sound_files()) >= 4
            assert (PROJECT_ROOT/"monitor/icon_asset/MonitorSolo.ico").is_file()
            assert (PROJECT_ROOT/"monitor/icon_asset/LOGO.png").is_file()
            report["checks"].append("bundled_assets_and_persistence")
            class OfflineHub:
                def read(self):
                    return {"backend_id":"release-check","runs":[],"source_states":{},"actions":[]}
                def send(self,*args):
                    raise AssertionError("Diagnostics must never send an agent command")
                def close(self):
                    pass
            root = tk.Tk()
            def destroy_root():
                try:
                    root.destroy()
                except tk.TclError:
                    pass  # Normal app.close() already destroyed this interpreter.
            cleanup.callback(destroy_root)
            app = MonitorApp(root,store,OfflineHub())
            cleanup.callback(lambda: app.close() if not app.closed else None)
            assert app.phone.thread is None and app.peers.thread is None
            report["checks"].append("isolated_data_and_disabled_remote_services")
            errors=[]
            root.report_callback_exception=lambda *args:errors.append(str(args[1]))
            attach_desktop(app)
            def pump():
                deadline=time.monotonic()+.25
                while time.monotonic()<deadline and not app.closed:
                    root.update()
                    time.sleep(.01)
            app.show_control_panel()
            pump()
            assert app.desktop.available and root.state()=="normal"
            app.panel.select_tab("personalization")
            app.panel.select_personalization("general")
            assert "startup-enabled" in app.panel.controls
            root.tk.call(root.protocol("WM_DELETE_WINDOW"))
            pump()
            assert root.state()=="withdrawn" and not app.closed and not app.island._hidden
            report["checks"].append("native_tray_close_keeps_island")
            assert notify_existing(root=store.root)
            pump()
            assert root.state()=="normal"
            report["checks"].append("native_instance_activation")
            startup=StartupManager(value_name="MonitorSoloSelfTest-"+uuid4().hex)
            cleanup.callback(startup.set_enabled,False)
            assert not startup.status()[0]
            startup.set_enabled(True)
            assert startup.status()[0]
            startup.set_enabled(False)
            assert not startup.status()[0]
            report["checks"].append("startup_register_and_remove")
            assert notify_existing(quit=True,root=store.root)
            pump()
            assert app.closed and not app.desktop.available and not errors,errors
            report["checks"].append("native_graceful_exit")
            report["status"]="passed"
    except Exception:
        report["error"]=traceback.format_exc()
    finally:
        destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return 0 if report["status"]=="passed" else 1
