"""Render only fixture windows. No live agents, personal data or desktop capture."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import tkinter as tk
from unittest.mock import Mock
import shutil

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from monitor.app import MonitorApp
from monitor.settings import SettingsStore
from monitor.tasks.store import TaskStore
from monitor.UI.display import enable_dpi_awareness
from render_wallpaper_preview import capture, artwork
from test_rope_personalization import settle


def main():
    enable_dpi_awareness()
    output=Path(__file__).resolve().parents[1]/"preview"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="solo-tasks-preview-") as tmp:
        root_path=Path(tmp)
        store=SettingsStore(root_path/"data")
        photo=root_path/"landscape.png"
        artwork(photo)
        store.import_photo(photo)
        store.update(task_sound_enabled=False,monitor_sound_enabled=False,panel_transparency=15)
        tasks=TaskStore(store.root)
        soon=datetime.now()+timedelta(minutes=4)
        tasks.save(dict(title="项目评审",body="整理本周进度，准备演示文件和设计稿。\n核对问题清单，与团队确认下一轮交付时间。"*4,
                        start_date=soon.date().isoformat(),time_of_day=soon.strftime("%H:%M"),
                        weekdays=[soon.weekday()],lead_minutes=5,enabled=True))
        for index,(title,body) in enumerate((("起身活动","离开屏幕，喝一杯水。"),("阅读时间","阅读并记录今天的想法。"),("检查备份","确认工作文件已保存。"))):
            later=soon+timedelta(hours=index+1)
            tasks.save(dict(title=title,body=body,start_date=later.date().isoformat(),time_of_day=later.strftime("%H:%M"),
                            weekdays=[0,1,2,3,4] if index==0 else [],lead_minutes=5,enabled=True))
        tasks.close()
        state={"backend_id":"preview","runs":[],"actions":[],"source_states":{}}
        hub=Mock()
        hub.read.side_effect=lambda:state
        root=tk.Tk()
        errors=[]
        root.report_callback_exception=lambda *e:errors.append(str(e[1]))
        app=MonitorApp(root,store,hub)
        try:
            root.deiconify()
            root.update()
            app.panel.select_tab("tasks")
            capture(root).save(output/"solo-tasks-list.png")
            app.panel.controls["task-view"].callback()
            capture(root).save(output/"solo-tasks-grid.png")
            app.panel.controls["task-view"].callback()
            edit=app.tasks.store.list()[0]
            edit=app.tasks.store.save({**edit,"weekdays":[],"interval_hours":2},edit["id"])
            app.task_dialog(edit)
            capture(app.task_window).save(output/"solo-task-editor.png")
            app.task_window.close()
            app.store.update(panel_color="#172233",panel_transparency=10)
            app.task_dialog(edit)
            capture(app.task_window).save(output/"solo-task-editor-dark.png")
            app.task_window.close()
            app.store.update(panel_color="#ffffff",panel_transparency=15)
            island=app.island
            settle(island)
            capture(island.root).save(output/"solo-task-capsule.png")
            island._toggle()
            settle(island)
            capture(island.root).save(output/"solo-task-progress.png")
            island._toggle()
            settle(island)
            capture(island.root).save(output/"solo-task-details.png")
            stamp=datetime.now(timezone.utc).isoformat()
            state["runs"]=[{"app_id":"codex","run_id":f"codex:preview-{i}","turn_id":"one","status":"running",
                           "started_at":stamp,"updated_at":stamp,"steps":[],"tools":[]} for i in range(5)]
            island._poll()
            settle(island)
            capture(island.root).save(output/"solo-task-mixed-five.png")
            app.panel.select_tab("personalization")
            app.panel.select_personalization("sounds")
            capture(root).save(output/"solo-task-sounds.png")
            app.panel.select_personalization("appearance")
            capture(root).save(output/"solo-appearance-refined.png")
            app.panel.select_tab("monitor")
            capture(root).save(output/"solo-task-monitor-toggle.png")
            asset=Path(__file__).resolve().parents[1]/"packaging/assets/rope/gpt.png"
            (store.root/"rope").mkdir()
            shutil.copyfile(asset,store.root/"rope/gpt.png")
            app.panel.select_tab("personalization")
            app.panel.select_personalization("rope")
            app.toggle_rope(True)
            for position,label in ((0,"left"),(50,"center"),(100,"right")):
                app.save_rope_position(position)
                capture(root).save(output/f"solo-rope-position-{label}.png")
            assert not errors,errors
            hub.send.assert_not_called()
            print("Saved 14 fixture-only previews to",output)
        finally:
            app.close()


if __name__=="__main__":
    main()
