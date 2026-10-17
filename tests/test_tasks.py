"""Real SQLite scheduling, durable acknowledgements and isolated local routing."""
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import Mock, patch
import sqlite3

import pytest

from monitor.tasks.schedule import validate, next_due
from monitor.tasks.store import TaskStore
from monitor.tasks.service import TaskService
from monitor.settings import SettingsStore
from monitor.UI.task_selection import TaskSelection
from monitor.music.play.reminder import ReminderSoundPlayer


BASE = datetime(2030, 1, 7, 9, 0).timestamp()  # Monday, local time.


def definition(**changes):
    return {"title":"准备开会材料", "body":"检查议程和项目进度。", "start_date":"2030-01-07",
            "time_of_day":"09:05", "weekdays":[], "lead_minutes":5, "enabled":True, **changes}


@pytest.mark.parametrize("changes", [{"title":""},{"title":"a"*41},{"title":"a\nb"},
    {"body":"x"*4001},{"start_date":"2030-02-30"},{"time_of_day":"25:01"},
    {"weekdays":[7]},{"weekdays":[True]},{"lead_minutes":1},{"lead_minutes":11},
    {"lead_minutes":3.5},{"lead_minutes":True},{"enabled":"true"}])
def test_invalid_definition_rejected_without_changing_data(tmp_path, changes):
    store=TaskStore(tmp_path)
    saved=store.save(definition(),now=BASE-60)
    with pytest.raises(ValueError):
        store.save(definition(**changes),saved["id"],now=BASE-60)
    assert store.get(saved["id"])==saved
    store.close()


def test_once_window_boundaries_restart_ack_and_no_historical_replay(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(),now=BASE-60)
    assert store.poll(BASE-1)==[]
    row=store.poll(BASE)[0]
    assert row["due_at"]==BASE+300 and row["expires_at"]==BASE+300
    assert store.claim_sound(row["id"],0)
    store.close()
    restored=TaskStore(tmp_path)
    assert restored.poll(BASE+1)[0]["id"]==row["id"]
    assert not restored.claim_sound(row["id"],0)
    restored.act(row["id"],0,"acknowledge",now=BASE+20)
    assert restored.poll(BASE+21)==[]
    with pytest.raises(ValueError):
        restored.act(row["id"],0,"snooze",now=BASE+22)
    restored.close()
    final=TaskStore(tmp_path)
    assert final.poll(BASE+30)==[] and final.poll(BASE+10*86400)==[]
    assert final.get(task["id"])["enabled"]
    final.close()


def test_snooze_survives_restart_rearms_after_five_minutes_and_rejects_stale_click(tmp_path):
    store=TaskStore(tmp_path)
    store.save(definition(),now=BASE-60)
    row=store.poll(BASE)[0]
    assert store.claim_sound(row["id"],0)
    store.act(row["id"],0,"snooze",now=BASE+100)
    assert store.poll(BASE+399)==[]
    store.close()
    store=TaskStore(tmp_path)
    repeat=store.poll(BASE+400)[0]
    assert repeat["cycle"]==1 and repeat["expires_at"]==BASE+700
    with pytest.raises(ValueError):
        store.act(row["id"],0,"acknowledge",now=BASE+401)
    assert store.claim_sound(row["id"],1)
    assert not store.claim_sound(row["id"],1)
    assert store.poll(BASE+699)
    assert store.poll(BASE+700)==[]
    store.close()


def test_weekdays_midnight_next_week_and_new_revision(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(start_date="2030-01-08",time_of_day="00:03",weekdays=[1,3]),now=BASE)
    at=datetime(2030,1,7,23,58).timestamp()
    row=store.poll(at)[0]
    assert datetime.fromtimestamp(row["due_at"]).date().isoformat()=="2030-01-08"
    store.act(row["id"],0,"acknowledge",now=at)
    assert next_due(task,row["due_at"])==datetime(2030,1,10,0,3).timestamp()
    next_row=store.poll(datetime(2030,1,9,23,58).timestamp())[0]
    assert next_row["id"]!=row["id"]
    updated=store.save({**task,"title":"新标题"},task["id"],now=at+1,expected_revision=task["revision"])
    assert updated["revision"]==2
    with pytest.raises(ValueError):
        store.act(row["id"],0,"snooze",now=at+2)
    store.close()


def test_sleep_past_window_does_not_play_catchup_and_resume_inside_does(tmp_path):
    store=TaskStore(tmp_path)
    store.save(definition(weekdays=[0]),now=BASE-60)
    assert store.poll(BASE+301)==[]
    assert store.poll(BASE+7*86400+30)
    store.close()


def test_disable_and_delete_revoke_visible_cards_and_pending_commands(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(),now=BASE-60)
    row=store.poll(BASE)[0]
    store.save({**task,"enabled":False},task["id"],now=BASE)
    assert store.poll(BASE+1)==[]
    with pytest.raises(ValueError):
        store.act(row["id"],0,"acknowledge",now=BASE+2)
    store.delete(task["id"])
    assert not store.list()
    assert store._db().execute("SELECT count(*) FROM occurrences").fetchone()[0]==0
    store.close()


def test_corrupt_database_is_reported_without_replacing_it(tmp_path):
    path=tmp_path/"tasks.sqlite3"
    path.write_bytes(b"not a database")
    service=TaskService(tmp_path,lambda:{},player=Mock())
    assert service.read(BASE)==[] and service.error
    assert path.read_bytes()==b"not a database"
    service.close()


def test_service_audio_is_claimed_only_once_and_only_for_five_visible_reminders(tmp_path):
    player=Mock()
    prefs={"task_reminder_sound":"sound-2","task_sound_enabled":True}
    service=TaskService(tmp_path,lambda:prefs,player=player)
    for index in range(7):
        service.store.save(definition(title=f"任务{index}"),now=BASE-60)
    runs=service.read(BASE)
    assert len(runs)==7 and player.play.call_count==5
    assert all(c.args[1]=="sound-2" for c in player.play.call_args_list)
    service.read(BASE+1)
    assert player.play.call_count==5
    first=runs[0]["reminder"]
    service.store.act(first["id"],0,"acknowledge",now=BASE+2)
    service.read(BASE+3)
    assert player.play.call_count==6
    prefs["task_sound_enabled"]=False
    service.read(BASE+4)
    player.retain.assert_called_with(set())
    service.close()


def test_reminders_sort_before_five_agents_and_leave_authorization_focus_intact(tmp_path):
    service=TaskService(tmp_path,lambda:{"task_sound_enabled":False},player=Mock())
    service.store.save(definition(time_of_day="09:06"),now=BASE-60)
    service.store.save(definition(title="先提醒"),now=BASE-60)
    agents=[{"app_id":"codex","run_id":f"codex:{i}","turn_id":"one","status":"running",
             "started_at":BASE-100+i,"updated_at":BASE,"tools":[]} for i in range(5)]
    selection=TaskSelection()
    snapshot={"backend_id":"same","runs":agents,"preferences":{}}
    selection.update(snapshot,now=BASE)
    selection.select("codex:4")
    snapshot["runs"]=[*agents,*service.read(BASE+60)]
    selection.update(snapshot,expanded=True,now=BASE+60)
    assert len(selection.runs)==5
    assert [r["app_id"] for r in selection.runs[:2]]==["solo_task","solo_task"]
    assert selection.runs[0]["task_summary"]=="先提醒"
    assert selection.focus_id=="codex:4"
    assert len(selection.visible_runs())==5
    # Reminder expiry is independent of Agent completion-retention preferences.
    selection.update(snapshot,now=BASE+361)
    assert all(r["app_id"]=="codex" for r in selection.runs)
    service.close()


def test_reminder_audio_three_times_and_cancel(tmp_path):
    player=ReminderSoundPlayer()
    played=Event()
    calls=[]
    def play_one(path,cancel):
        calls.append(path.name)
        if len(calls)==3:
            played.set()
    with patch("monitor.music.play.reminder.SoundPreview._windows",side_effect=play_one):
        assert player.play("a","sound-1")
        assert not player.play("a","sound-1")
        assert played.wait(3)
    assert len(calls)==3
    player.close()
    assert not player.play("b","sound-1")
    second=ReminderSoundPlayer()
    entered=Event()
    def block(path,cancel):
        entered.set()
        assert cancel.wait(2)
    with patch("monitor.music.play.reminder.SoundPreview._windows",side_effect=block) as backend:
        second.play("c","sound-1")
        assert entered.wait(1)
        second.retain(set())
        second.close()
    assert backend.call_count==1


def test_task_sound_settings_survive_restart_independently(tmp_path):
    store=SettingsStore(tmp_path)
    store.update(task_reminder_sound="sound-4",task_sound_enabled=False,monitor_completion_sound="sound-2")
    prefs=SettingsStore(tmp_path).snapshot()
    assert prefs["task_reminder_sound"]=="sound-4" and not prefs["task_sound_enabled"]
    assert prefs["monitor_completion_sound"]=="sound-2"
    with pytest.raises(ValueError):
        store.update(task_sound_enabled="false")


def test_clock_rollback_does_not_resurrect_expired_occurrence(tmp_path):
    store=TaskStore(tmp_path)
    store.save(definition(),now=BASE-60)
    row=store.poll(BASE)[0]
    assert store.claim_sound(row["id"],0)
    assert store.poll(BASE+300)==[]
    store.close()
    restored=TaskStore(tmp_path)
    assert restored.poll(BASE+100)==[]
    assert not restored.claim_sound(row["id"],0)
    restored.close()


def test_unrepresentable_future_calendar_is_validation_error(tmp_path):
    store=TaskStore(tmp_path)
    # 9999-12-31 is Friday; the next selected weekday would overflow date.max.
    with pytest.raises(ValueError):
        store.save(definition(start_date="9999-12-31",weekdays=[0]),now=BASE)
    assert store.list()==[]
    store.close()


def test_locked_database_recovers_without_losing_acknowledgement(tmp_path):
    service=TaskService(tmp_path,lambda:{"task_sound_enabled":False},player=Mock())
    task=service.store.save(definition(),now=BASE-60)
    row=service.read(BASE)[0]["reminder"]
    other=sqlite3.connect(service.store.path)
    try:
        other.execute("BEGIN EXCLUSIVE")
        assert service.read(BASE+1)==[] and service.error
        with pytest.raises(sqlite3.OperationalError):
            service.store.act(row["id"],0,"acknowledge",now=BASE+2)
        other.rollback()
        assert service.read(BASE+3)[0]["reminder"]["id"]==row["id"]
        assert not service.error
        service.store.act(row["id"],0,"acknowledge",now=BASE+4)
        assert service.read(BASE+5)==[]
        assert service.store.state_for(task["id"])["state"]=="acknowledged"
    finally:
        other.close()
        service.close()
