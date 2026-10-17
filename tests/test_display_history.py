"""Standalone restart, replay and execution evidence regressions."""
from copy import deepcopy
import json
import sqlite3
from unittest.mock import Mock, patch

import pytest

from monitor.src.display_history import DisplayHistory
from monitor.src.task_identity import task_identity
from monitor.src.source_hub import SourceHub, SourceWorker
from monitor.UI.task_selection import TaskSelection


def run(status="stale", **changes):
    return {"run_id": "same", "app_id": "codex", "status": status,
            "started_at": 1, "updated_at": 100, **changes}


def snapshot(*runs, **changes):
    return {"backend_id": "one", "runs": list(runs), "preferences": {}, **changes}


def test_connection_flaps_do_not_rearm_but_new_execution_does():
    selector = TaskSelection()
    task = run("running", connection_error=True)
    selector.update(snapshot(task), now=100)
    for tick in (110, 120, 130, 140, 150, 160, 170, 180):
        task.update(connection_error=tick % 20 == 0, updated_at=tick)
        selector.update(snapshot(task), now=tick)
        assert bool(selector.runs) is (tick < 160)
    task.update(connection_error=False, tools=[{"id": "fresh-tool", "status": "running"}], updated_at=190)
    selector.update(snapshot(task), now=190)
    assert selector.runs
    # Normal active tasks must not inherit the old disconnect deadline.
    selector.update(snapshot(task), now=1000)
    assert selector.runs



def test_backlog_decoded_during_outage_and_old_progress_replay_are_not_recovery():
    selector = TaskSelection()
    task = run("running", connection_error=True)
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    task.update(tools=[{"id": "backlog", "status": "completed"}], updated_at=170)
    selector.update(snapshot(task), now=170)
    task.update(connection_error=False)
    selector.update(snapshot(task), now=180)
    assert not selector.runs
    # An older message can have different data; its event time rejects replay.
    task.update(tools=[{"id": "old", "status": "running"}], updated_at=10)
    selector.update(snapshot(task), now=190)
    assert not selector.runs



def test_pause_retires_but_resume_or_actionable_request_can_reopen():
    selector = TaskSelection()
    task = run("paused")
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    assert not selector.runs
    task.update(status="running", updated_at=50)
    selector.update(snapshot(task), now=165)
    assert not selector.runs
    task["updated_at"] = 170
    selector.update(snapshot(task), now=170)
    assert selector.runs
    task.update(status="paused", updated_at=200)
    selector.update(snapshot(task), now=200)
    selector.update(snapshot(task), now=260)
    assert not selector.runs
    task.update(status="waiting_confirmation", pending_confirmations=[{"id": "request", "status": "pending"}])
    selector.update(snapshot(task), now=270)
    selector.update(snapshot(task), now=10000)
    assert selector.runs



def test_same_request_reconnects_only_if_provider_still_offers_real_control():
    selector = TaskSelection()
    task = run("waiting_confirmation", connection_error=True,
               pending_confirmations=[{"id": "request", "status": "pending"}])
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    task["connection_error"] = False
    selector.update(snapshot(task), now=170)
    assert not selector.runs  # Old read-only approval log.
    selector.update(snapshot(task, actions=[{"type": "approve_tool", "run_id": "same", "confirmation_id": "request"}]), now=180)
    assert selector.runs



def test_late_terminal_outcome_is_shown_once_without_replaying_summary_changes():
    selector = TaskSelection()
    task = run()
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    task.update(status="completed", ended_at=200, updated_at=200)
    selector.update(snapshot(task), now=200)
    assert selector.runs
    selector.update(snapshot(task), now=260)
    assert not selector.runs
    task.update(summary_preview="new summary", tools=[{"id": "late-summary-metadata", "status": "completed"}])
    selector.update(snapshot(task), now=270)
    assert not selector.runs



@pytest.mark.parametrize("identity", ["started_at", "turn_id", "completion_id"])
def test_new_turn_with_reused_session_id_is_a_new_display_episode(identity):
    selector = TaskSelection()
    task = run("paused", **{identity: 1})
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    task[identity] = 2
    selector.update(snapshot(task), now=200)
    assert selector.runs



def test_identity_separates_sources_datasets_and_canonicalizes_timestamp():
    task = run(started_at="2026-10-16T08:00:00+08:00")
    assert task_identity(task) == task_identity({**task, "started_at": "2026-10-16T00:00:00Z"})
    assert len({task_identity({**task, "app_id": app, "monitor_scope": scope})
                for app in ("brightmoon", "codex", "opencode") for scope in ("a", "b")}) == 6



def test_final_generation_cannot_be_revived_by_partial_running_hydration():
    selector = TaskSelection()
    task = run("completed", ended_at=100)
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    task.update(status="running", updated_at=200, tools=[{"id": "hydrated", "status": "running"}])
    selector.update(snapshot(task), now=200)
    assert not selector.runs
    task.update(turn_id="new-turn", started_at=201)
    selector.update(snapshot(task), now=201)
    assert selector.runs



def test_codex_live_text_activity_wakes_but_subscription_status_and_hydration_do_not():
    from monitor.codex_monitor.projection import Projection
    projection = Projection("fixture")
    task = projection.ensure("thread", "turn", 1)
    projection.event("item/started", {"threadId": "thread", "turnId": "turn",
        "item": {"type": "commandExecution", "id": "original-call"}})
    task.update(connection_error=True, updated_at=100)
    selector = TaskSelection()
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    # A fresh connection hydrates a partial tool list, which is not new work.
    projection = Projection("fixture")
    task = projection.ensure("thread", "turn", 1)
    projection.event("thread/status/changed", {"threadId": "thread", "turnId": "turn", "status": {}})
    projection.item(task, {"type": "agentMessage", "id": "old", "text": "old hydrated output"}, True)
    projection.item(task, {"type": "commandExecution", "id": "hydrated-call"}, True)
    selector.update(snapshot(task), now=170)
    assert not selector.runs
    projection.event("item/agentMessage/delta", {"threadId": "thread", "turnId": "turn", "delta": "new output"})
    selector.update(snapshot(task), now=180)
    assert selector.runs



def test_codex_rollout_activity_markers_are_replay_stable():
    from monitor.codex_monitor.projection import RolloutProjection
    projection = RolloutProjection("fixture")
    projection.apply({"type": "session_meta", "payload": {"id": "thread"}})
    projection.apply({"type": "event_msg", "timestamp": 1, "payload": {"type": "task_started", "turn_id": "turn"}})
    event = {"type": "event_msg", "timestamp": 100, "payload": {"type": "agent_message", "message": "output"}}
    projection.apply(event)
    first = projection.snapshot()[0]["monitor_activity_id"]
    projection.apply(event)
    assert projection.snapshot()[0]["monitor_activity_id"] == first
    projection.apply({**event, "timestamp": 200})
    assert projection.snapshot()[0]["monitor_activity_id"] != first



def test_deepseek_real_events_wake_without_title_changes_or_transport_identity_reset():
    from monitor.deepseek_monitor.projection import SessionProjection
    from monitor.deepseek_monitor.interactions import merge_interactions
    model = SessionProjection({"id": "session", "createdAt": 1000}, "fixture")
    model.apply({"type": "turn/start", "data": {"turn": 1}, "time": 1000, "seq": 1})
    model.apply({"type": "assistant/message", "data": {"message": {"content": "before"}}, "time": 100000, "seq": 2})
    selector = TaskSelection()
    task = model.snapshot(now=100)
    task["connection_error"] = True
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    model.apply({"type": "session/title", "data": {"title": "edited"}, "time": 170000, "seq": 3})
    selector.update(snapshot(model.snapshot(now=170)), now=170)
    assert not selector.runs
    model.apply({"type": "assistant/message", "data": {"message": {"content": "after"}}, "time": 180000, "seq": 4})
    selector.update(snapshot(model.snapshot(now=180)), now=180)
    assert selector.runs
    # Live interaction arrives before logs; restarting the adapter must still
    # recognize the same logical task when the log's run ID replaces the alias.
    live = merge_interactions({"runs": []}, {"connected": True, "control_key": "fixture", "instance": "host",
        "generation": 1, "requests": [{"id": "ask", "kind": "question", "session_id": "session", "turn": 1}]}, "fixture")
    assert live["runs"][0]["run_id"] != task["run_id"]
    assert task_identity(live["runs"][0]) == task_identity(task)



def test_opencode_message_part_updates_wake_but_session_metadata_does_not():
    from monitor.opencode_monitor.bridge import OpencodeBridge
    bridge = OpencodeBridge.__new__(OpencodeBridge)
    session = {"id": "session", "directory": "fixture", "title": "old title", "agent": "agent",
               "model": "fixture", "parent_id": "", "time_created": 1000, "time_updated": 100000}
    messages = [{"id": "user", "data": '{"role":"user"}', "time_created": 1000, "time_updated": 1000},
                {"id": "assistant", "data": '{"role":"assistant"}', "time_created": 2000, "time_updated": 100000}]
    parts = {"assistant": [{"id": "part", "type": "text", "text": "working", "time_updated": 100000}]}
    def build():
        return {**bridge._build_run(session, 0, messages, [], parts), "app_id": "opencode", "status": "running"}
    selector = TaskSelection()
    task = {**build(), "connection_error": True}
    selector.update(snapshot(task), now=100)
    selector.update(snapshot(task), now=160)
    session.update(title="new title", time_updated=170000)
    selector.update(snapshot(build()), now=170)
    assert not selector.runs
    parts["assistant"][0].update(text="next output", time_updated=180000)
    session["time_updated"] = 180000
    selector.update(snapshot(build()), now=180)
    assert selector.runs


@pytest.mark.parametrize("status", ["completed", "failed", "partial", "cancelled", "interrupted",
                                    "stale", "error", "paused", "cancellation_requested"])
def test_restart_does_not_replay_previously_observed_outcomes_even_before_grace_ends(tmp_path, status):
    path = tmp_path / "display.sqlite3"
    first = TaskSelection(DisplayHistory(path))
    task = run(status, ended_at=100)
    first.update(snapshot(task), now=100)
    assert first.runs
    first.lifetime.history.close()
    restored = TaskSelection(DisplayHistory(path))
    try:
        restored.update(snapshot(), now=101)
        for tick in (102, 120, 300, 10000):
            task.update(updated_at=tick, summary_preview=f"late summary {tick}", lease_expires_at=tick+30)
            restored.update(snapshot(task, backend_id=str(tick), revision=tick), now=tick)
            assert not restored.runs
    finally:
        restored.lifetime.history.close()


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
def test_task_finished_while_solo_was_closed_does_not_open_on_next_launch(tmp_path, status):
    path = tmp_path / "display.sqlite3"
    first = TaskSelection(DisplayHistory(path))
    task = run("running")
    first.update(snapshot(task), now=100)
    first.lifetime.history.close()
    restored = TaskSelection(DisplayHistory(path))
    try:
        task.update(status=status, ended_at=101)
        restored.update(snapshot(task), now=102)
        assert not restored.runs
        task.update(completion_id="next", status="running", started_at=103)
        restored.update(snapshot(task), now=103)
        assert restored.runs
    finally:
        restored.lifetime.history.close()


def test_restart_preserves_paused_resume_and_current_approval(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = TaskSelection(DisplayHistory(path))
    task = run("paused")
    first.update(snapshot(task), now=100)
    first.lifetime.history.close()
    restored = TaskSelection(DisplayHistory(path))
    try:
        task.update(status="running", updated_at=101)
        restored.update(snapshot(task), now=102)
        assert restored.runs
        task.update(connection_error=True)
        restored.update(snapshot(task), now=200)
        restored.update(snapshot(task), now=260)
        assert not restored.runs
        task.update(connection_error=False, status="waiting_confirmation",
                    pending_confirmations=[{"id": "ask", "status": "pending"}])
        restored.update(snapshot(task, actions=[{"type": "approve_tool", "run_id": "same"}]), now=261)
        assert restored.runs
    finally:
        restored.lifetime.history.close()


def test_disk_receipts_survive_memory_eviction_and_months_of_absence(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor.src.display_history.MAX_HISTORY", 2)
    path = tmp_path / "history.sqlite3"
    history = DisplayHistory(path)
    selection = TaskSelection(history)
    tasks = [run("stale", run_id=str(i)) for i in range(7)]
    try:
        selection.update(snapshot(*tasks), now=100)
        selection.update(snapshot(*tasks), now=160)
        assert not selection.runs and len(history.records) == 7
        selection.update(snapshot(), now=170)
        assert len(history.records) == 2
        assert history.connection.execute("SELECT COUNT(*) FROM display_receipts").fetchone()[0] == 7
        selection.update(snapshot(*reversed(tasks)), now=365*86400)
        assert not selection.runs
    finally:
        history.close()


def test_cache_eviction_does_not_retire_an_unexpired_task_in_current_launch(tmp_path, monkeypatch):
    monkeypatch.setattr("monitor.src.display_history.MAX_HISTORY", 1)
    history = DisplayHistory(tmp_path / "history.sqlite3")
    selection = TaskSelection(history)
    one, two = run("paused", run_id="one"), run("paused", run_id="two")
    try:
        selection.update(snapshot(one, two), now=100)
        selection.update(snapshot(two), now=110)
        assert task_identity(one) not in history.records
        selection.update(snapshot(one), now=120)
        assert selection.runs
        selection.update(snapshot(one), now=160)
        assert not selection.runs
    finally:
        history.close()


def test_heartbeat_does_not_write_and_locked_database_keeps_memory_then_retries(tmp_path):
    path = tmp_path / "history.sqlite3"
    history = DisplayHistory(path)
    selection = TaskSelection(history)
    task = run("paused", task_summary="PRIVATE PROMPT", project_id="C:/private/project")
    blocker = sqlite3.connect(path)
    try:
        selection.update(snapshot(task), now=100)
        changes = history.connection.total_changes
        for tick in range(101, 130):
            task.update(updated_at=tick, seq=tick)
            selection.update(snapshot(task, revision=tick), now=tick)
        assert history.connection.total_changes == changes
        blocker.execute("BEGIN IMMEDIATE")
        selection.update(snapshot(task), now=160)
        assert not selection.runs and history.error and history.dirty
        blocker.rollback()
        selection.update(snapshot(task), now=163)
        assert not history.dirty and not history.error
        body = history.connection.execute("SELECT identity,body FROM display_receipts").fetchall()
        assert all(secret not in str(body) for secret in ("PRIVATE PROMPT", "C:/private/project", '"same"'))
    finally:
        blocker.close()
        history.close()


def test_critical_transition_commits_immediately_without_waiting_for_close(tmp_path):
    history = DisplayHistory.for_data_root(tmp_path)
    selection = TaskSelection(history)
    try:
        task = run("running")
        selection.update(snapshot(task), now=100)
        task.update(status="failed", ended_at=100.1)
        selection.update(snapshot(task), now=100.1)
        with sqlite3.connect(history.path) as reader:
            body = json.loads(reader.execute("SELECT body FROM display_receipts").fetchone()[0])
        assert body["terminal_seen"] and body["phase"] == "finished"
    finally:
        history.close()


def test_restart_can_read_existing_receipts_while_database_has_a_write_reservation(tmp_path):
    path = tmp_path / "history.sqlite3"
    first = TaskSelection(DisplayHistory(path))
    task = run("failed", ended_at=100)
    first.update(snapshot(task), now=100)
    first.lifetime.history.close()
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    restored = DisplayHistory(path)
    try:
        assert restored.connection is not None and not restored.error
        selection = TaskSelection(restored)
        selection.update(snapshot(task), now=101)
        assert not selection.runs and restored.dirty  # Writing waits; hiding does not.
        blocker.rollback()
        selection.update(snapshot(task), now=104)
        assert not restored.dirty and not restored.error
    finally:
        blocker.close()
        restored.close()


def test_corrupt_database_falls_back_without_crashing_or_overwriting_original(tmp_path):
    path = tmp_path / "history.sqlite3"
    path.write_bytes(b"invalid SQLite file")
    history = DisplayHistory(path)
    selection = TaskSelection(history)
    try:
        task = run("stale")
        selection.update(snapshot(task), now=100)
        assert selection.runs and history.error
        selection.update(snapshot(task), now=160)
        assert not selection.runs
        assert path.read_bytes() == b"invalid SQLite file"
    finally:
        history.close()


def test_scope_survives_toggle_but_separates_source_data_directories():
    prefs = {"monitor_sources": {"codex": {"enabled": True, "data_path": "C:/one", "connection_mode": "logs"}}}
    source = Mock()
    source.poll.return_value = {"status": "ready", "runs": [run("paused")], "actions": []}
    hub = SourceHub(lambda: deepcopy(prefs), factories={"codex": lambda _: source}, start_worker=False)
    try:
        hub.poll_sources()
        before = task_identity(hub.read()["runs"][0])
        config = prefs["monitor_sources"]["codex"]
        config["enabled"] = False
        assert not hub.read()["runs"]
        config.update(enabled=True, connection_mode="auto")
        hub.poll_sources()
        assert task_identity(hub.read()["runs"][0]) == before
        config["data_path"] = "C:/two"
        hub.poll_sources()
        assert task_identity(hub.read()["runs"][0]) != before
    finally:
        hub.close()


def test_blocked_reader_revokes_controls_and_gets_a_display_deadline():
    source = Mock()
    source.poll.return_value = snapshot(run("running"), status="ready", actions=[{"type": "cancel_run", "run_id": "same"}])
    worker = SourceWorker(lambda _: source, {}, start=False)
    selection = TaskSelection()
    try:
        worker.poll()
        selection.update(worker.snapshot(), now=100)
        worker.poll_started = 100
        with patch("monitor.src.source_hub.time.monotonic", return_value=130):
            lost = worker.snapshot()
        assert lost["runs"][0]["connection_error"] and not lost["actions"]
        selection.update(lost, now=130)
        selection.update(lost, now=189.9)
        assert selection.runs
        selection.update(lost, now=190)
        assert not selection.runs
        worker.poll()
        selection.update(worker.snapshot(), now=191)
        assert not selection.runs  # A successful poll alone is not progress.
    finally:
        worker.close()

