"""Resident visibility is independent from real task retirement and scheduling."""
from datetime import datetime, timedelta, timezone
import time
from unittest.mock import Mock

import pytest

from monitor.settings import SettingsStore
from monitor.tasks.store import TaskStore
from monitor.UI.resident import marquee_offset
from monitor.src.app_sources import app_source
from test_tasks import BASE, definition
from test_tasks_ui import imminent, silence
from test_rope_personalization import settle, png
from test_wallpapers import native_app


def empty_source(app):
    state={"backend_id":"resident-fixture","runs":[],"actions":[],"source_states":{}}
    app.hub.read.side_effect=lambda:state
    return state


def texts(island):
    return " ".join(island.canvas.itemcget(i,"text") for i in island.canvas.find_all()
                    if island.canvas.type(i)=="text")


def test_resident_default_validation_and_persistence(tmp_path):
    store=SettingsStore(tmp_path)
    assert store.snapshot()["island_resident"] is False
    store.update(island_resident=True)
    assert SettingsStore(tmp_path).snapshot()["island_resident"] is True
    with pytest.raises(ValueError):
        store.update(island_resident="true")
    assert SettingsStore(tmp_path).snapshot()["island_resident"] is True
    assert SettingsStore.normalize({"island_resident":None})["island_resident"] is False


def test_marquee_clips_then_holds_complete_text_for_one_second_and_repeats():
    width,text_width=68,52
    rest=(width-text_width)/2
    assert marquee_offset(0,width,text_width)==width
    assert marquee_offset(1.,width,text_width)==pytest.approx((width+rest)/2)
    for elapsed in (2.,2.2,2.5,2.999999):
        assert marquee_offset(elapsed,width,text_width)==pytest.approx(rest)
    assert marquee_offset(4.,width,text_width)<rest
    assert marquee_offset(5.2,width,text_width)==-text_width
    assert marquee_offset(5.3,width,text_width)==pytest.approx(width)
    assert marquee_offset(6.3,width,text_width)==pytest.approx(marquee_offset(1.,width,text_width))


def test_next_reminder_orders_by_notification_not_due_time_and_ignores_disabled(tmp_path):
    store=TaskStore(tmp_path)
    store.save(definition(title="更早到期却更晚提醒",time_of_day="09:10",lead_minutes=2),now=BASE-60)
    earliest=store.save(definition(title="提前十分钟",time_of_day="09:15",lead_minutes=10),now=BASE-60)
    store.save(definition(title="已暂停",time_of_day="09:06",enabled=False),now=BASE-60)
    row=store.next_reminder(BASE)
    assert row["task"]["id"]==earliest["id"] and row["remind_at"]==BASE+300
    assert row["due_at"]==BASE+900
    assert store._db().execute("SELECT count(*) FROM occurrences").fetchone()[0]==0
    store.close()


def test_next_reminder_respects_ack_snooze_weekly_and_interval(tmp_path):
    store=TaskStore(tmp_path)
    once=store.save(definition(),now=BASE-60)
    row=store.poll(BASE)[0]
    store.act(row["id"],0,"snooze",now=BASE+10)
    upcoming=store.next_reminder(BASE+11)
    assert upcoming["snoozed"] and upcoming["remind_at"]==BASE+310
    store.poll(BASE+310)
    store.act(row["id"],1,"acknowledge",now=BASE+311)
    assert store.next_reminder(BASE+312) is None
    weekly=store.save(definition(weekdays=[0]),now=BASE-60)
    row=store.poll(BASE)[0]
    store.act(row["id"],0,"acknowledge",now=BASE+1)
    assert store.next_reminder(BASE+2)["remind_at"]==BASE+7*86400
    interval=store.save(definition(interval_hours=2),now=BASE-60)
    row=store.poll(BASE)[0]
    store.act(row["id"],0,"acknowledge",now=BASE+1)
    upcoming=store.next_reminder(BASE+2)
    assert upcoming["task"]["id"]==interval["id"] and upcoming["remind_at"]==BASE+7200
    store.close()
    restored=TaskStore(tmp_path)
    assert restored.next_reminder(BASE+3)==upcoming
    restored.close()


def test_resident_empty_three_levels_no_fake_runs_and_disabled_hides(native_app,monkeypatch):
    app,_=native_app
    silence(app)
    empty_source(app)
    png(app.store.root/"cartoon"/"fixture.png")
    app.load_cartoon()
    app.island._poll()
    assert app.island._hidden
    app.panel.select_tab("monitor")
    app.panel.controls["island-resident"].callback()
    island=app.island
    settle(island)
    assert island._is_idle() and not island._hidden and island._state()=="idle"
    assert island.selection.runs==[] and island._run()=={}
    assert "Bright Moon" in texts(island)
    assert island._fonts["brand"].actual("slant")=="italic"
    assert app_source("solo_idle").icon.name=="MonitorSolo_logo.png"
    assert len(island.canvas.find_withtag("app_logo"))==1
    assert len(island.canvas.find_withtag("task_ring_halo"))==1
    assert len(island.canvas.find_withtag("resident_marquee"))==1
    since=island.resident_view.since
    monkeypatch.setattr("monitor.UI.resident.time.monotonic",lambda:since+1.)
    island.draw()
    outline=island.canvas.itemcget(island.canvas.find_withtag("task_ring_track")[0],"outline")
    monkeypatch.setattr("monitor.UI.resident.time.monotonic",lambda:since+1.6)
    island.draw()
    assert island.canvas.itemcget(island.canvas.find_withtag("task_ring_track")[0],"outline")!=outline
    island._toggle()
    settle(island)
    assert "未来 1 小时暂无提醒" in texts(island)
    assert island.canvas.find_withtag("resident_idle_character")
    island._poll()
    assert island.expanded and not island.details  # Empty polls cannot auto-collapse again.
    island._toggle()
    settle(island)
    assert island.details and not island.canvas.find_withtag("resident_footer_logo")
    characters=island.canvas.find_withtag("resident_idle_character")
    assert len(characters)==1
    bounds=island.canvas.bbox(characters[0])
    assert bounds[3]-bounds[1]==round(80*island.scale)
    assert bounds[0]>island.WIDTH/2*island.scale
    assert bounds[3]<island._size[1]*island.scale
    photo=island.resident_view._character_photo
    island.draw()
    assert island.resident_view._character_photo is photo
    from types import SimpleNamespace
    from monitor.UI.resident import ResidentView
    scaled=island.resident_view._character(SimpleNamespace(root=island.root,scale=1.5))
    assert scaled.height()==120
    assert scaled.width()/scaled.height()==pytest.approx(photo.width()/photo.height(),abs=.02)
    assert ResidentView()._character(island) is None
    assert all(b["key"]=="toggle" for b in island._buttons)
    app.toggle_resident(False)
    assert island._hidden and not island.expanded and not island.details
    assert not any("solo_idle" in key for key in app.display_history.records)
    app.hub.send.assert_not_called()


def test_idle_details_show_next_notification_and_control_off_error_states(native_app):
    app,_=native_app
    silence(app)
    empty_source(app)
    future=datetime.now()+timedelta(minutes=40)
    task=app.tasks.store.save(imminent(title="下一个计划",body="阅读一会儿，记下新的想法。"*80,
        start_date=future.date().isoformat(),time_of_day=future.strftime("%H:%M"),lead_minutes=10))
    app.toggle_resident(True)
    island=app.island
    settle(island)
    island._toggle()
    settle(island)
    assert "下一个计划" in texts(island) and "开始提醒" in texts(island)
    assert len(island._summary_lines())==2
    upcoming=island.snapshot["next_reminder"]
    assert upcoming["due_at"]-upcoming["remind_at"]==600
    assert app.tasks.player.play.call_count==0
    app.toggle_task_monitor(False)
    assert "任务提醒已关闭" in texts(island) and island.expanded
    app.toggle_task_monitor(True)
    app.tasks.store.upcoming_reminders=Mock(side_effect=ValueError("fixture broken data"))
    island._poll()
    assert "暂时无法读取提醒计划" in texts(island)
    assert island.snapshot["next_reminder"] is None


@pytest.mark.parametrize("level",[1,2,3])
def test_last_agent_retires_into_idle_capsule_and_cannot_replay(native_app,monkeypatch,level):
    app,_=native_app
    silence(app)
    now=time.time()
    state=empty_source(app)
    agent={"app_id":"codex","run_id":"codex:resident","turn_id":"one","status":"running",
           "started_at":datetime.fromtimestamp(now-30,timezone.utc).isoformat(),
           "updated_at":datetime.fromtimestamp(now,timezone.utc).isoformat(),"steps":[],"tools":[]}
    state["runs"]=[agent]
    app.toggle_resident(True)
    island=app.island
    settle(island)
    if level>1:
        island._toggle()
        settle(island)
    if level>2:
        island._toggle()
        settle(island)
    app.store.update(monitor_hide_minutes=2)
    agent.update(status="completed",ended_at=datetime.fromtimestamp(now,timezone.utc).isoformat())
    island._poll()
    assert not island._is_idle()
    monkeypatch.setattr("monitor.UI.island.time.time",lambda:now+119)
    island._poll()
    assert island.selection.runs
    monkeypatch.setattr("monitor.UI.island.time.time",lambda:now+121)
    island._poll()
    settle(island)
    assert not island._hidden and island._is_idle()
    assert not island.expanded and not island.details and not island.selection.runs
    assert "Bright Moon" in texts(island)
    agent["message"]="晚到的摘要不能重新弹出"
    island._poll()
    assert island._is_idle()
    agent.update(turn_id="two",status="running",ended_at=None,
                 updated_at=datetime.fromtimestamp(now+122,timezone.utc).isoformat())
    island._poll()
    assert not island._is_idle() and island._run()["turn_id"]=="two"
    settle(island)
    assert not island.canvas.find_withtag("resident_idle_character")
    app.hub.send.assert_not_called()


def test_one_agent_retirement_keeps_other_agent_and_then_idle(native_app,monkeypatch):
    app,_=native_app
    silence(app)
    now=time.time()
    stamp=datetime.fromtimestamp(now,timezone.utc).isoformat()
    state=empty_source(app)
    state["runs"]=[{"app_id":"codex","run_id":f"codex:parallel-{i}","turn_id":"one","status":"running",
                    "started_at":stamp,"updated_at":stamp,"tools":[],"steps":[]} for i in range(2)]
    app.toggle_resident(True)
    state["runs"][0].update(status="completed",ended_at=stamp)
    app.island._poll()
    monkeypatch.setattr("monitor.UI.island.time.time",lambda:now+61)
    app.island._poll()
    assert len(app.island.selection.runs)==1 and not app.island._is_idle()
    state["runs"][1].update(status="cancelled",ended_at=stamp)
    app.island._poll()
    assert app.island._is_idle() and not app.island._hidden


def test_local_reminder_ack_returns_to_idle_and_new_reminder_replaces_it(native_app):
    app,_=native_app
    silence(app)
    empty_source(app)
    app.toggle_resident(True)
    app.tasks.store.save(imminent())
    app.tasks_changed()
    island=app.island
    settle(island)
    assert not island._is_idle()
    island._toggle()
    settle(island)
    island._activate(next(b for b in island._buttons if b["key"]=="panel:reminder-acknowledge"))
    assert island._is_idle() and not island.expanded and not island._hidden
    app.tasks.store.save(imminent(title="新的提醒"))
    app.tasks_changed()
    assert not island._is_idle() and island._run()["task_summary"]=="新的提醒"


def test_resident_keeps_pendant_and_agent_error_does_not_revoke_switch(native_app,monkeypatch):
    app,_=native_app
    silence(app)
    empty_source(app)
    clock=[100.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    png(app.store.root/"rope"/"cat.png")
    app.toggle_rope(True)
    app.toggle_resident(True)
    settle(app.island)
    clock[0]=101.
    app.island.pendant._paint()
    assert app.island.pendant.alpha>0
    app.hub.read.side_effect=OSError("fixture unavailable")
    app.island._poll()
    assert app.island._is_idle() and not app.island._hidden
    app.toggle_resident(False)
    assert app.island._hidden and app.island.pendant.alpha==0


def test_task_view_switch_is_only_an_icon(native_app):
    app,_=native_app
    app.panel.select_tab("tasks")
    for _ in range(2):
        rect=app.panel.controls["task-view"].rect
        center=app.panel.point((rect[0]+rect[2])/2,(rect[1]+rect[3])/2)
        overlapping=app.panel.find_overlapping(center[0]-2,center[1]-2,center[0]+2,center[1]+2)
        assert any("task_view_icon" in app.panel.gettags(i) for i in overlapping)
        assert not any(app.panel.type(i) in {"polygon","rectangle","line"} for i in overlapping)
        app.panel.controls["task-view"].callback()
