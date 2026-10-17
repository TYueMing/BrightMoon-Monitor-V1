"""Native task editor, shared theme and reminder-only island actions."""
from datetime import datetime, timedelta
from unittest.mock import Mock

from PIL import Image

from monitor.tasks.store import TaskStore
from monitor.src.app_sources import app_source
from monitor.UI.settings_panel import CARD_WIDTH, CARD_HEIGHT
from test_wallpapers import native_app
from test_rope_personalization import settle


def imminent(**changes):
    due=datetime.now()+timedelta(minutes=4)
    return {"title":"准备项目评审材料", "body":"整理迭代结果、演示文件与待确认的问题。"*20,
            "start_date":due.date().isoformat(),"time_of_day":due.strftime("%H:%M"),
            "weekdays":[],"lead_minutes":5,"enabled":True,**changes}


def silence(app):
    app.tasks.player.close()
    app.tasks.player=Mock()
    app.store.update(task_sound_enabled=False)


def test_navigation_editor_validation_and_persisted_task(native_app):
    app,tmp=native_app
    silence(app)
    app.panel.select_tab("tasks")
    assert app.panel.controls["tab:tasks"].rect[1]==182
    assert app.panel.controls["tab:personalization"].rect[1]==530
    assert "task-new" in app.panel.controls
    app.panel.controls["task-new"].callback()
    editor=app.task_window
    assert not editor.save()  # Empty title; leave the editor open.
    assert editor.canvas.itemcget(editor.error,"text")
    task=imminent()
    for key in editor.values:
        editor.values[key].set(str(task[key]))
    editor.body.insert("1.0",task["body"])
    editor.weekdays[0].set(True)
    editor.weekdays[6].set(True)
    assert editor.save()
    assert app.task_window is None
    reopened=TaskStore(app.store.root)
    saved=reopened.list()[0]
    assert saved["weekdays"]==[0,6] and saved["body"]==task["body"]
    assert saved["lead_minutes"]==5
    reopened.close()
    app.panel.select_tab("tasks")
    assert "task-edit:"+saved["id"] in app.panel.controls
    app.task_toggle(saved,False)
    assert not app.tasks.store.get(saved["id"])["enabled"]
    app.hub.send.assert_not_called()


def test_editor_background_tracks_wallpaper_and_panel_color(native_app):
    app,_=native_app
    silence(app)
    app.photo=Image.new("RGB",(500,600),"#335577")
    app.store.update(panel_color="#112233",panel_transparency=0)
    app.task_dialog()
    editor=app.task_window
    assert editor.background.getpixel((0,0))==(51,85,119)
    assert editor.background.getpixel((app.s(28),app.s(100)))==(17,34,51)
    assert editor.ink=="#f4f7fc"
    editor.close()


def test_solo_reminder_three_levels_and_local_acknowledgement(native_app):
    app,_=native_app
    silence(app)
    state=app.hub.read()
    state["runs"]=[]
    app.hub.read.side_effect=lambda:state
    task=app.tasks.store.save(imminent())
    app.tasks_changed()
    island=app.island
    settle(island)
    texts=[island.canvas.itemcget(i,"text") for i in island.canvas.find_all() if island.canvas.type(i)=="text"]
    assert texts==[task["title"][:5]]
    assert len(island.canvas.find_withtag("app_logo"))==1
    assert len(island.canvas.find_withtag("task_ring_track"))==1
    capsule_logo=island.canvas.bbox(island.canvas.find_withtag("app_logo")[0])
    island._toggle()
    settle(island)
    assert island.expanded and not island.details
    keys={b["key"] for b in island._buttons}
    assert {"panel:reminder-snooze","panel:reminder-acknowledge"}<=keys
    assert len(island._summary_lines())<=3
    assert len(island.canvas.find_withtag("app_logo"))==1
    expanded_logo=island.canvas.bbox(island.canvas.find_withtag("app_logo")[0])
    assert expanded_logo==capsule_logo
    assert len(island.canvas.find_withtag("task_ring_track"))==1
    assert len(island.canvas.find_withtag("reminder_detail_logo"))==1
    assert app_source("solo_task").icon.name=="LOGO_EXE.png"
    island._toggle()
    settle(island)
    assert island.details
    text=" ".join(island.canvas.itemcget(i,"text") for i in island.canvas.find_all() if island.canvas.type(i)=="text")
    assert "重复：仅一次" in text and "下次计划" in text
    island._activate(next(b for b in island._buttons if b["key"]=="panel:reminder-acknowledge"))
    assert not island.selection.runs and island._hidden
    app.hub.send.assert_not_called()
    assert app.tasks.store.state_for(task["id"])["state"]=="acknowledged"


def test_mixed_five_slots_no_ellipsis_and_snooze_does_not_send_agent_command(native_app):
    app,_=native_app
    silence(app)
    state=app.hub.read()
    agent=state["runs"][0]
    state["runs"]=[{**agent,"run_id":f"codex:{i}"} for i in range(5)]
    app.hub.read.side_effect=lambda:state
    task=app.tasks.store.save(imminent())
    app.tasks_changed()
    island=app.island
    settle(island)
    assert len(island.selection.runs)==5 and island.selection.runs[0]["app_id"]=="solo_task"
    assert len([b for b in island._buttons if b["key"].startswith("task:")])==5
    assert not any(b["key"]=="task_more" for b in island._buttons)
    island.select_task(island.selection.runs[0]["run_id"])
    settle(island)
    island._activate(next(b for b in island._buttons if b["key"]=="panel:reminder-snooze"))
    assert app.tasks.store.state_for(task["id"])["state"]=="snoozed"
    assert all(r["app_id"]=="codex" for r in island.selection.runs)
    app.hub.send.assert_not_called()


def test_task_list_pagination_and_independent_sound_controls(native_app):
    app,_=native_app
    silence(app)
    for i in range(6):
        app.tasks.store.save(imminent(title=f"任务{i}"+"宽字符W"*9,weekdays=list(range(7))))
    app.panel.select_tab("tasks")
    assert len([k for k in app.panel.controls if k.startswith("task-edit:")])==4
    titles=[i for i in app.panel.find_all() if app.panel.type(i)=="text"
            and app.panel.itemcget(i,"text").startswith("任务") and len(app.panel.itemcget(i,"text"))>3]
    assert len(titles)==4
    assert all(app.panel.bbox(i)[2]<app.panel.point(588,0)[0] for i in titles)
    app.panel.controls["task-next"].callback()
    assert len([k for k in app.panel.controls if k.startswith("task-edit:")])==2
    for control in app.panel.controls.values():
        x1,y1,x2,y2=control.rect
        assert 0<=x1<x2<=CARD_WIDTH and 0<=y1<y2<=CARD_HEIGHT
    app.panel.select_tab("personalization")
    app.panel.select_personalization("sounds")
    app.panel.controls["task-sound"].callback()
    assert app.store.snapshot()["task_sound_enabled"]
    assert not app.store.snapshot()["monitor_sound_enabled"]
    app.panel.controls["task-sounds"].callback()
    assert app.sound_choice.get()==app.store.snapshot()["task_reminder_sound"]
    app.close_sounds()


def test_agent_reader_failure_does_not_disable_local_reminders(native_app):
    app,_=native_app
    silence(app)
    app.tasks.store.save(imminent())
    app.hub.read.side_effect=OSError("Agent reader unavailable")
    app.island._poll()
    assert any(r["app_id"]=="solo_task" for r in app.island.selection.runs)
    assert not app.island._hidden
