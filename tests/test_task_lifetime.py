"""A terminal task retires independently of its siblings, focus and polling."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from monitor.UI.task_selection import TaskSelection
from monitor.UI.window_state import FINISHED


def task(key, status="running", **extra):
    return {"run_id": key, "status": status, "started_order": 1, **extra}


def state(*runs, minutes=1):
    return {"backend_id": "one", "runs": list(runs), "preferences": {"monitor_hide_minutes": minutes}}


@pytest.mark.parametrize("status", sorted(FINISHED))
def test_each_terminal_status_expires_with_other_tasks_running(status):
    selector = TaskSelection()
    value = state(task("done", status, ended_at=100), task("active"))
    selector.update(value, now=100)
    selector.select("done")
    selector.update(value, now=159.9)
    assert selector.focus_id == "done"
    selector.update(value, now=160)
    assert [r["run_id"] for r in selector.runs] == ["active"]
    assert selector.focus_id == "active" and not selector.select("done")
    selector.update(value, now=161)
    assert len(selector.runs) == 1  # Source still retains the completion.


def test_staggered_deadlines_across_providers_reclaim_slots_and_preserve_approval():
    selector = TaskSelection()
    runs = [task(str(i), app_id="codex" if i % 2 else "brightmoon", started_order=i) for i in range(7)]
    value = state(*runs, minutes=2)
    selector.update(value, now=100)
    selector.select("2")
    runs[2].update(status="completed", ended_at=100)
    runs[3].update(status="cancelled", ended_at=130)
    runs[6].update(status="waiting_confirmation", pending_confirmations=[{"id": "approval", "status": "pending", "requested_at": 150}])
    selector.update(value, expanded=True, now=150)
    assert selector.focus_id == "6"
    selector.update(value, expanded=True, now=220)
    assert "2" not in {r["run_id"] for r in selector.runs}
    assert len(selector.runs) == 5 and selector.focus_id == "6"
    selector.update(value, expanded=True, now=250)
    assert "3" not in {r["run_id"] for r in selector.runs}
    assert selector.focus_id == "6"


def test_missing_terminal_timestamp_and_repeated_summary_updates_cannot_reset_timer():
    selector = TaskSelection()
    done = task("done", "completed")
    selector.update(state(done), now=100)
    done.update(updated_at=150, summary_preview="Late summary")
    selector.update(state(done), now=150)
    assert selector.runs
    selector.update(state(done), now=160)
    assert not selector.runs
    selector.update(state(), now=170)
    done.update(ended_at=200)
    selector.update(state(done, minutes=20), now=200)
    assert not selector.runs  # A later preference increase does not resurrect it.


def test_preference_changes_affect_unexpired_tasks_and_new_tasks_wake():
    selector = TaskSelection()
    done = task("done", "completed", ended_at=100)
    selector.update(state(done), now=100)
    selector.update(state(done, minutes=20), now=160)
    assert selector.runs
    selector.update(state(done, minutes=20), now=1300)
    assert not selector.runs
    selector.update(state(done, task("new")), now=1301)
    assert selector.focus_id == "new"


def test_new_batch_preserves_recent_completion_and_backend_restart_keeps_retirement():
    selector = TaskSelection()
    done = task("done", "completed", ended_at=100)
    selector.update(state(done), now=100)
    selector.update(state(done, task("new")), now=110)
    assert {r["run_id"] for r in selector.runs} == {"done", "new"}
    selector.update(state(done), now=160)
    assert not selector.runs
    selector.update({**state(task("done", "completed")), "backend_id": "restart"}, now=200)
    assert not selector.runs


@pytest.mark.parametrize("status, disconnected", [
    ("running", True), ("waiting_confirmation", True), ("stale", False),
    ("error", False), ("cancellation_requested", False),
])
def test_loss_and_unconfirmed_stop_retire_after_one_minute_without_extending_on_polls(status, disconnected):
    selector = TaskSelection()
    lost = task("lost", status, connection_error=disconnected, updated_at=1)
    value = state(lost, task("healthy"), minutes=20)
    selector.update(value, now=100)
    selector.select("lost")
    lost["updated_at"] = 159
    selector.update(value, now=159.9)
    assert selector.focus_id == "lost"
    selector.update(value, now=160)
    assert [run["run_id"] for run in selector.runs] == ["healthy"]
    selector.update(value, now=200)
    assert not selector.select("lost")
    assert lost["status"] == status  # Display expiry never claims an actual stop.


@pytest.mark.parametrize("status", ["cancelled", "interrupted"])
def test_confirmed_stop_uses_one_minute_even_if_completion_preference_is_longer(status):
    selector = TaskSelection()
    value = state(task("stopped", status, ended_at=100), minutes=20)
    selector.update(value, now=100)
    selector.update(value, now=159.9)
    assert selector.runs
    selector.update(value, now=160)
    assert not selector.runs


def test_disconnect_recovery_and_new_turn_clear_retirement_without_hiding_healthy_approval():
    selector = TaskSelection()
    lost = task("lost", "running", started_at=1, connection_error=True)
    approval = task("approval", "waiting_confirmation", pending_confirmations=[
        {"id": "ask", "status": "pending", "requested_at": 1}])
    value = state(lost, approval)
    selector.update(value, expanded=True, now=100)
    selector.update(value, expanded=True, now=160)
    assert selector.focus_id == "approval" and len(selector.runs) == 1
    lost["connection_error"] = False
    selector.update(value, now=170)
    assert len(selector.runs) == 1  # Transport recovery alone is not task progress.
    lost["tools"] = [{"id": "new-call", "status": "running"}]
    selector.update(value, now=171)
    assert len(selector.runs) == 2
    lost["connection_error"] = True
    selector.update(value, now=180)
    selector.update(value, now=239.9)
    assert len(selector.runs) == 2
    selector.update(value, now=240)
    assert len(selector.runs) == 1
    # A reused OpenCode session ID starts a new turn and gets its own deadline.
    lost["started_at"] = 250
    selector.update(value, now=250)
    assert len(selector.runs) == 2
    selector.update(value, expanded=True, now=10000)
    assert selector.focus_id == "approval" and len(selector.runs) == 1


def test_stale_approval_never_steals_focus_and_empty_reader_errors_have_a_deadline():
    selector = TaskSelection()
    lost = task("lost", "waiting_confirmation", connection_error=True,
                pending_confirmations=[{"id": "ask", "status": "pending", "requested_at": 1}])
    selector.update(state(task("healthy"), lost), now=100)
    selector.select("healthy")
    selector.update(state(task("healthy"), lost), expanded=True, now=120)
    assert selector.focus_id == "healthy"
    assert "失联" in selector.caption()
    empty = TaskSelection()
    error = {**state(), "status": "error"}
    empty.update(error, now=100)
    empty.update(error, now=159)
    assert not empty.lifetime.empty_error_expired(159)
    empty.update(error, now=160)
    assert empty.lifetime.empty_error_expired(160)
    empty.update(state(task("new")), now=170)
    assert not empty.lifetime.empty_error_expired(170)
