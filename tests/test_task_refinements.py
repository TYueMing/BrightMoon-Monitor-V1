"""Interval migration, monitor gating, two task views and live pendant placement."""
from datetime import datetime
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from monitor.tasks.store import TaskStore
from monitor.tasks.service import TaskService
from monitor.tasks.schedule import next_due, repeat_label
from monitor.settings import SettingsStore
from monitor.UI.rope_pendant import horizontal_center
from monitor.UI.panel_palette import contrast
from test_tasks import BASE, definition
from test_tasks_ui import imminent, silence
from test_wallpapers import native_app
from test_rope_personalization import png, settle


def test_interval_same_day_restart_ack_and_snooze_keep_original_anchor(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(interval_hours=2),now=BASE-60)
    first=store.poll(BASE)[0]
    assert repeat_label(task)=="每隔 2 小时"
    store.act(first["id"],0,"snooze",now=BASE+100)
    store.close()
    store=TaskStore(tmp_path)
    later=store.poll(BASE+400)[0]
    assert later["id"]==first["id"] and later["cycle"]==1
    store.act(later["id"],1,"acknowledge",now=BASE+401)
    assert store.poll(BASE+402)==[]
    assert next_due(task,BASE+401)==BASE+7500
    second=store.poll(BASE+7200)[0]
    assert second["id"]!=first["id"] and second["due_at"]==BASE+7500
    assert second["due_date"]=="interval:1"
    assert store.claim_sound(second["id"],0)
    store.close()
    store=TaskStore(tmp_path)
    assert store.poll(BASE+7201)[0]["id"]==second["id"]
    assert not store.claim_sound(second["id"],0)
    store.close()


def test_interval_cross_midnight_skips_missed_cycles_and_edit_revokes_old(tmp_path):
    store=TaskStore(tmp_path)
    task=store.save(definition(time_of_day="23:05",interval_hours=2),now=BASE)
    first_at=datetime(2030,1,7,23).timestamp()
    row=store.poll(first_at)[0]
    next_row=store.poll(first_at+7200)[0]
    assert next_row["due_date"]=="interval:1" and next_row["id"]!=row["id"]
    late=store.poll(first_at+20*86400+7200)
    assert len(late)==1 and late[0]["due_date"]=="interval:241"
    changed=store.save({**task,"interval_hours":0,"weekdays":[0]},task["id"],now=first_at+20*86400+7201)
    assert changed["interval_hours"]==0 and changed["revision"]==2
    with pytest.raises(ValueError):
        store.act(late[0]["id"],0,"acknowledge",now=first_at+20*86400+7202)
    store.close()


@pytest.mark.parametrize("interval,weekdays",[(-1,[]),(8761,[]),(True,[]),(1.5,[]),("oops",[]),(2,[0])])
def test_invalid_or_conflicting_intervals_preserve_existing_task(tmp_path,interval,weekdays):
    store=TaskStore(tmp_path)
    task=store.save(definition(),now=BASE-60)
    with pytest.raises(ValueError):
        store.save({**task,"interval_hours":interval,"weekdays":weekdays},task["id"],now=BASE-60)
    assert store.get(task["id"])==task
    store.close()


def test_schema_one_upgrade_preserves_acknowledgements_and_sound_claims(tmp_path):
    # Build the shipped v1 schema directly, rather than generating it through
    # the new store (which would hide migration regressions).
    db=sqlite3.connect(tmp_path/"tasks.sqlite3")
    db.executescript("""
        CREATE TABLE tasks (id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL,
            start_date TEXT NOT NULL,time_of_day TEXT NOT NULL,weekdays TEXT NOT NULL,
            lead_minutes INTEGER NOT NULL,enabled INTEGER NOT NULL,revision INTEGER NOT NULL,
            created_at REAL NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE occurrences (id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            revision INTEGER NOT NULL,due_date TEXT NOT NULL,due_at REAL NOT NULL,remind_at REAL NOT NULL,
            expires_at REAL NOT NULL,state TEXT NOT NULL,cycle INTEGER NOT NULL DEFAULT 0,
            sound_cycle INTEGER NOT NULL DEFAULT -1,UNIQUE(task_id,revision,due_date));
        PRAGMA user_version=1;
    """)
    task=definition()
    db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",("old",task["title"],task["body"],
        task["start_date"],task["time_of_day"],json.dumps([]),5,1,1,BASE-60,BASE-60))
    db.execute("INSERT INTO occurrences VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("old:1:2030-01-07","old",1,"2030-01-07",BASE+300,BASE,BASE+300,"acknowledged",0,0))
    db.commit()
    db.close()
    store=TaskStore(tmp_path)
    assert store.get("old")["interval_hours"]==0
    assert store._db().execute("PRAGMA user_version").fetchone()[0]==2
    assert not store.poll(BASE+1)
    assert store.state_for("old")["state"]=="acknowledged"
    assert not store.claim_sound("old:1:2030-01-07",0)
    store.close()


def test_monitor_switch_hides_without_ack_or_sound_claim_and_survives_restart(tmp_path):
    prefs=SettingsStore(tmp_path)
    assert prefs.snapshot()["task_monitor_enabled"] is True
    prefs.update(task_monitor_enabled=False)
    player=Mock()
    service=TaskService(tmp_path,prefs.snapshot,player=player)
    task=service.store.save(definition(),now=BASE-60)
    assert service.read(BASE)==[]
    player.play.assert_not_called()
    player.retain.assert_called_with(set())
    row=service.store.poll(BASE)[0]
    with pytest.raises(ValueError,match="监控已关闭"):
        service.send("todo:"+row["id"],{"action":"acknowledge","cycle":0})
    service.close()
    prefs=SettingsStore(tmp_path)
    assert not prefs.snapshot()["task_monitor_enabled"]
    prefs.update(task_monitor_enabled=True)
    service=TaskService(tmp_path,prefs.snapshot,player=player)
    assert service.read(BASE+10)[0]["reminder"]["task"]["id"]==task["id"]
    assert player.play.call_count==1
    prefs.update(task_monitor_enabled=False)
    assert not service.read(BASE+11)
    player.retain.assert_called_with(set())
    prefs.update(task_monitor_enabled=True)
    service.read(BASE+12)
    assert player.play.call_count==1  # Toggle must not rearm already claimed sound.
    assert service.read(BASE+301)==[]
    service.close()


@pytest.mark.parametrize("change",[{"rope_position":-1},{"rope_position":101},{"rope_position":True},
    {"rope_position":3.5},{"task_monitor_enabled":"false"},{"task_view":"unknown"}])
def test_new_preferences_validate_without_partial_save(tmp_path,change):
    store=SettingsStore(tmp_path)
    assert store.snapshot()["rope_position"]==50
    store.update(task_view="grid",rope_position=75,task_monitor_enabled=False)
    expected=store.snapshot()
    with pytest.raises(ValueError):
        store.update(**change)
    assert SettingsStore(tmp_path).snapshot()==expected


def test_interval_editor_switches_exclusively_and_round_buttons_are_keyboard_usable(native_app):
    app,_=native_app
    silence(app)
    task=app.tasks.store.save(imminent(weekdays=[0,2,4]))
    app.task_dialog(task)
    editor=app.task_window
    assert set(editor.buttons)=={"删除任务","取消","保存"}
    editor.interval_check.invoke()
    assert editor.interval_enabled.get() and not any(v.get() for v in editor.weekdays)
    editor.weekday_checks[2].invoke()
    assert editor.weekdays[2].get() and not editor.interval_enabled.get()
    assert str(editor.interval_input.cget("state"))=="disabled"
    editor.interval_check.invoke()
    editor.interval_hours.set("2")
    save=editor.buttons["保存"]
    assert contrast(save.fill,save.ink)>=4.5
    assert max(abs(a-b) for a,b in zip(save.rendered.getpixel((0,0)),save.backdrop.getpixel((0,0))))<=2
    assert save.rendered.getpixel((save.rendered.width//2,2))!=save.backdrop.getpixel((save.rendered.width//2,2))
    save.focus_force()
    editor.update()
    save.event_generate("<Return>")
    saved=app.tasks.store.get(task["id"])
    assert saved["interval_hours"]==2 and saved["weekdays"]==[]
    app.task_dialog(saved)
    assert app.task_window.interval_enabled.get() and app.task_window.interval_hours.get()=="2"
    app.task_window.buttons["取消"].invoke()
    assert app.task_window is None


def test_grid_toggle_paginates_keeps_edit_and_enable_and_persists(native_app):
    app,_=native_app
    silence(app)
    for index in range(8):
        app.tasks.store.save(imminent(title=f"测试任务{index}"+"宽标题"*8))
    panel=app.panel
    panel.select_tab("tasks")
    assert len([k for k in panel.controls if k.startswith("task-edit:")])==4
    assert next(iter(panel._task_view_icons))[0]=="grid.png"
    panel.controls["task-view"].callback()
    assert SettingsStore(app.store.root).snapshot()["task_view"]=="grid"
    assert next(iter(panel._task_view_icons))[0]=="navigation.png"
    assert len([k for k in panel.controls if k.startswith("task-edit:")])==6
    panel.controls["task-next"].callback()
    assert len([k for k in panel.controls if k.startswith("task-edit:")])==2
    key=next(k for k in panel.controls if k.startswith("task-enable:"))
    panel.controls[key].callback()
    assert not app.tasks.store.get(key.split(":",1)[1])["enabled"]
    edit=next(k for k in panel.controls if k.startswith("task-edit:"))
    panel.controls[edit].callback()
    assert app.task_window.task["id"]==edit.split(":",1)[1]
    app.task_window.close()
    panel.controls["task-view"].callback()
    assert panel.task_page==0 and app.store.snapshot()["task_view"]=="list"
    app.hub.send.assert_not_called()


def test_monitor_toggle_immediately_withdraws_local_cards_without_changing_agents(native_app):
    app,_=native_app
    silence(app)
    app.tasks.store.save(imminent())
    app.tasks_changed()
    assert any(r["app_id"]=="solo_task" for r in app.island.selection.runs)
    app.panel.select_tab("monitor")
    app.panel.controls["task-monitor"].callback()
    assert all(r["app_id"]!="solo_task" for r in app.island.selection.runs)
    assert app.tasks.store.list()[0]["enabled"]
    app.tasks.player.retain.assert_called_with(set())
    app.panel.controls["task-monitor"].callback()
    assert any(r["app_id"]=="solo_task" for r in app.island.selection.runs)
    app.hub.send.assert_not_called()
    app.panel.select_tab("personalization")
    app.panel.select_personalization("appearance")
    assert "sounds" not in app.panel.controls and "sound" not in app.panel.controls
    app.panel.select_personalization("sounds")
    assert {"agent-sounds","task-sounds"}<=app.panel.controls.keys()


def test_rope_slider_moves_preview_and_native_pendant_with_twenty_pixel_edges(native_app,monkeypatch):
    app,_=native_app
    png(app.store.root/"rope"/"position.png")
    clock=[100.]
    monkeypatch.setattr("monitor.UI.rope_pendant.time.monotonic",lambda:clock[0])
    app.panel.select_tab("personalization")
    app.panel.select_personalization("rope")
    app.toggle_rope(True)
    island=app.island
    settle(island)
    clock[0]=101.
    island.pendant._paint()
    previous=None
    for value in (0,50,100):
        app.panel.controls["rope-position"].callback(value)
        island.root.update_idletasks()
        pendant=island.pendant
        left=island._position[0]+5*island.scale
        width=(island._size[0]-10)*island.scale
        assert pendant.anchor[0]==pytest.approx(horizontal_center(left,width,pendant.size[0],island.scale,value))
        if value==0:
            assert pendant.window.winfo_x()-left==pytest.approx(20*island.scale,abs=1)
        if value==100:
            assert left+width-pendant.window.winfo_x()-pendant.size[0]==pytest.approx(20*island.scale,abs=1)
        point=app.panel.coords(app.panel.find_withtag("rope_position_preview")[0])[0]
        if previous is not None:
            assert point>previous
        previous=point
        assert SettingsStore(app.store.root).snapshot()["rope_position"]==value
    app.panel.focus_key="rope-position"
    app.panel.key(SimpleNamespace(keysym="Home"))
    assert app.store.snapshot()["rope_position"]==0
