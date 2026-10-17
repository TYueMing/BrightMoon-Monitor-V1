"""Resident UI previews from temporary fixture windows, never real sessions."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import shutil
import tempfile
import tkinter as tk
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.UI.display import enable_dpi_awareness
from monitor.UI.resident import MARQUEE_ENTER, MARQUEE_CYCLE
from test_rope_personalization import settle
from render_wallpaper_preview import capture, artwork


def main():
    enable_dpi_awareness()
    output=Path(__file__).resolve().parents[1]/"preview"
    with tempfile.TemporaryDirectory(prefix="solo-resident-preview-") as tmp:
        store=SettingsStore(Path(tmp)/"data")
        folder=store.root/"cartoon"
        folder.mkdir(parents=True)
        for source in (output.parent/"data/cartoon").glob("*.png"):
            shutil.copyfile(source,folder/source.name)
        store.update(island_resident=True,task_sound_enabled=False,monitor_sound_enabled=False)
        store.update(cartoon_image="cartoon/月光星尘.png")
        photo=Path(tmp)/"scene.png"
        artwork(photo)
        store.import_photo(photo)
        hub=Mock()
        hub.read.return_value={"backend_id":"resident-preview","runs":[],"actions":[],"source_states":{}}
        root=tk.Tk()
        errors=[]
        root.report_callback_exception=lambda *args:errors.append(str(args[1]))
        app=MonitorApp(root,store,hub)
        try:
            root.deiconify()
            root.update()
            island=app.island
            settle(island)
            capture(root).save(output/"solo-resident-settings.png")
            clock=island.resident_view.since
            with patch("monitor.UI.resident.time.monotonic",return_value=clock+MARQUEE_ENTER+.5):
                island.draw()
                capture(island.root).save(output/"solo-resident-capsule.png")
                island._toggle()
                settle(island)
                capture(island.root).save(output/"solo-resident-empty.png")
                island._toggle()
                settle(island)
                capture(island.root).save(output/"solo-resident-details.png")
            app.panel.select_tab("personalization")
            app.panel.select_personalization("cartoon")
            capture(root).save(output/"solo-cartoon-picker.png")
            app.select_cartoon("cartoon/Jay.png")
            island.details=False
            settle(island)
            capture(island.root).save(output/"solo-cartoon-jay-empty.png")
            future=datetime.now()+timedelta(minutes=40)
            app.tasks.store.save(dict(title="给自己留一点阅读时间",body="放下手边的事情，读几页喜欢的书。\n不必赶进度，记住一个新想法就好。",
                start_date=future.date().isoformat(),time_of_day=future.strftime("%H:%M"),
                weekdays=[],lead_minutes=5,enabled=True))
            later=future+timedelta(minutes=10)
            app.tasks.store.save(dict(title="整理明天的计划",body="留三分钟记下最重要的一件事。",
                start_date=later.date().isoformat(),time_of_day=later.strftime("%H:%M"),
                weekdays=[],lead_minutes=5,enabled=True))
            island._poll()
            island.details=False
            settle(island)
            clock=island.resident_view.since
            with patch("monitor.UI.resident.time.monotonic",return_value=clock+MARQUEE_ENTER+.5):
                island.draw()
                capture(island.root).save(output/"solo-resident-next-reminder.png")
            island.expanded=False
            settle(island)
            with patch("monitor.UI.resident.time.monotonic",return_value=clock+MARQUEE_ENTER+.5):
                island.draw()
                capture(island.root).save(output/"solo-hour-preview-capsule.png")
            frames=[]
            for index in range(round(MARQUEE_CYCLE/.1)):
                with patch("monitor.UI.resident.time.monotonic",return_value=clock+index*.1):
                    island.draw()
                    frames.append(capture(island.root))
            frames[0].save(output/"solo-hour-preview-marquee.gif",save_all=True,append_images=frames[1:],duration=100,loop=0)
            app.store.update(monitor_background_color="#f1f5f9")
            island._poll()
            settle(island)
            with patch("monitor.UI.resident.time.monotonic",return_value=clock+MARQUEE_ENTER+.5):
                island.draw()
                capture(island.root).save(output/"solo-resident-light.png")
            app.panel.select_tab("tasks")
            capture(root).save(output/"solo-tasks-icon-only.png")
            from test_tasks_ui import imminent
            app.tasks.store.save(imminent(title="喝一杯水"))
            app.tasks_changed()
            settle(island)
            capture(island.root).save(output/"solo-reminder-ring-mini.png")
            island._toggle()
            settle(island)
            capture(island.root).save(output/"solo-reminder-ring-expanded.png")
            assert not errors,errors
            assert len(island.selection.runs)==1
            hub.send.assert_not_called()
            print("Saved 12 PNG previews and the one-hour preview marquee GIF to",output)
        finally:
            app.close()


if __name__=="__main__":
    main()
