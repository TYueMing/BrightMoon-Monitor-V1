"""One-hour resident preview: bounded, read-only and consistent with its header."""
from datetime import datetime, timedelta
from unittest.mock import Mock

from monitor.tasks.store import TaskStore
from monitor.tasks.service import TaskService
from monitor.UI.resident import marquee_label, PREVIEW_BLUE
from test_tasks import BASE, definition
from test_tasks_ui import imminent, silence
from test_resident_island import empty_source, texts
from test_rope_personalization import settle
from test_wallpapers import native_app


def test_hour_preview_boundaries_order_disabled_and_read_only(tmp_path):
    store=TaskStore(tmp_path)
    first=store.save(definition(title="先提醒",time_of_day="09:20",lead_minutes=10),now=BASE-60)
    store.save(definition(title="后提醒",time_of_day="09:19",lead_minutes=2),now=BASE-60)
    boundary=store.save(definition(title="恰好一小时",time_of_day="10:05"),now=BASE-60)
    store.save(definition(title="超过一小时",time_of_day="10:06"),now=BASE-60)
    store.save(definition(title="停用",time_of_day="09:15",enabled=False),now=BASE-60)
    rows=store.upcoming_reminders(BASE)
    assert len(rows)==3 and rows[0]["task"]["id"]==first["id"]
    assert rows[-1]["task"]["id"]==boundary["id"] and rows[-1]["remind_at"]==BASE+3600
    assert store._db().execute("SELECT count(*) FROM occurrences").fetchone()[0]==0
    assert len(store.upcoming_reminders(BASE+60))==4  # Moving window admits the next task.
    store.save({**first,"enabled":False},first["id"],now=BASE)
    assert len(store.upcoming_reminders(BASE))==2
    store.delete(boundary["id"])
    assert len(store.upcoming_reminders(BASE))==1
    store.close()


def test_snooze_and_next_interval_are_one_task_and_ack_does_not_replay(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(interval_hours=1),now=BASE-60)
    row=store.poll(BASE)[0]
    store.act(row["id"],0,"snooze",now=BASE+1)
    preview=store.upcoming_reminders(BASE+2)
    assert len(preview)==1 and preview[0]["snoozed"] and preview[0]["remind_at"]==BASE+301
    store.poll(BASE+301)
    store.act(row["id"],1,"acknowledge",now=BASE+302)
    preview=store.upcoming_reminders(BASE+303)
    assert len(preview)==1 and not preview[0]["snoozed"]
    assert preview[0]["remind_at"]==BASE+3600 and preview[0]["task"]["id"]==task["id"]
    store.close()


def test_hour_window_crosses_midnight(tmp_path):
    now=datetime(2030,1,7,23,30).timestamp()
    store=TaskStore(tmp_path)
    store.save(definition(start_date="2030-01-08",time_of_day="00:15"),now=now)
    store.save(definition(start_date="2030-01-08",time_of_day="00:36"),now=now)
    assert len(store.upcoming_reminders(now))==1
    store.close()


def test_service_clears_preview_on_off_error_and_active_handoff(tmp_path):
    prefs={"island_resident":True,"task_monitor_enabled":True,"task_sound_enabled":False}
    service=TaskService(tmp_path,lambda:prefs,player=Mock())
    service.store.save(definition(time_of_day="09:15"),now=BASE-60)
    assert service.read(BASE)==[]
    assert service.upcoming_count==1 and service.upcoming["remind_at"]==BASE+600
    prefs["task_monitor_enabled"]=False
    service.read(BASE)
    assert service.upcoming_count==0 and service.upcoming is None
    prefs["task_monitor_enabled"]=True
    assert len(service.read(BASE+600))==1
    assert service.upcoming_count==0 and service.upcoming is None
    service.store.upcoming_reminders=Mock(side_effect=ValueError("fixture error"))
    service.read(BASE)
    assert service.error and service.upcoming_count==0 and service.upcoming is None
    service.close()


def test_ui_one_detail_other_count_header_refresh_and_large_count(native_app):
    app,_=native_app
    silence(app)
    empty_source(app)
    saved=[]
    for minutes,title in ((20,"最近任务"),(30,"第二个任务"),(120,"远处的任务")):
        future=datetime.now()+timedelta(minutes=minutes)
        saved.append(app.tasks.store.save(imminent(title=title,start_date=future.date().isoformat(),
                                                   time_of_day=future.strftime("%H:%M"))))
    app.toggle_resident(True)
    island=app.island
    settle(island)
    assert marquee_label(island.snapshot)=="2项任务"
    assert island.resident_view._key[1]==PREVIEW_BLUE
    island._toggle()
    settle(island)
    content=texts(island)
    assert "最近任务" in content and "另有 1 项" in content
    assert "第二个任务" not in content and "远处的任务" not in content
    island.snapshot["upcoming_reminder_count"]=1000
    island.draw()
    assert island.resident_view._source.width<=round(68*island.scale)
    app.tasks.store.delete(saved[0]["id"])
    island._poll()
    assert marquee_label(island.snapshot)=="1项任务"
    assert "第二个任务" in texts(island) and "另有" not in texts(island)
    since=island.resident_view.since
    island._poll()
    assert island.resident_view.since==since
    app.tasks.store.delete(saved[1]["id"])
    island._poll()
    assert marquee_label(island.snapshot)=="无任务！"
    assert "未来 1 小时暂无提醒" in texts(island)
    app.toggle_task_monitor(False)
    assert marquee_label(island.snapshot)=="提醒关闭"
    app.tasks.store.upcoming_reminders=Mock(side_effect=ValueError("fixture error"))
    app.toggle_task_monitor(True)
    assert marquee_label(island.snapshot)=="读取失败"
    app.hub.send.assert_not_called()
