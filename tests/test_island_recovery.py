"""Real Tk window lifecycle, approval isolation, appearance and durable restart."""
from copy import deepcopy
import tkinter as tk
from unittest.mock import Mock, patch

import pytest

from monitor.UI.island import DynamicIsland
from monitor.src.display_history import DisplayHistory


def task(status="running", **changes):
    return {"run_id": "codex:fixture", "app_id": "codex", "turn_id": "one", "status": status,
            "started_at": 1, "updated_at": 100, "tools": [], "steps": [],
            "pending_confirmations": [], "confirmations": [], **changes}


def stop_callbacks(island):
    for job in island._jobs.values():
        island.root.after_cancel(job)
    island._jobs.clear()


@pytest.fixture
def native(tmp_path):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable")
    state = {"backend_id": "fixture", "status": "running", "runs": [task()], "actions": [],
             "preferences": {"monitor_hide_minutes": 10, "monitor_sound_enabled": False}}
    player, sender = Mock(), Mock()
    with patch("monitor.UI.island.time.time", return_value=100):
        island = DynamicIsland(root, lambda: deepcopy(state), sender, completion_player=player,
                               display_history=DisplayHistory.for_data_root(tmp_path))
    stop_callbacks(island)
    yield island, state, player, sender
    island.close()


@pytest.mark.parametrize("failure", ["exception", "invalid", "empty_error", "connection_error", "stale",
                                    "paused", "cancelled", "interrupted", "cancellation_requested"])
def test_bad_read_and_stopped_task_hide_after_sixty_seconds_without_stopping_agent(native, failure):
    island, state, _, sender = native
    original = island.snapshot_reader
    if failure == "exception":
        island.snapshot_reader = Mock(side_effect=OSError("fixture"))
    elif failure == "invalid":
        island.snapshot_reader = lambda: None
    elif failure == "empty_error":
        island.snapshot_reader = lambda: {"status": "error"}
    elif failure == "connection_error":
        state["runs"][0]["connection_error"] = True
    else:
        state["runs"][0].update(status=failure)
        if failure in {"cancelled", "interrupted"}:
            state["runs"][0]["ended_at"] = 100
    for tick in (100, 159.9, 160, 200):
        with patch("monitor.UI.island.time.time", return_value=tick):
            island._poll()
        assert island._hidden is (tick >= 160)
        if tick >= 160:
            assert island.root.state() == "withdrawn"
        assert "poll" in island._jobs
        stop_callbacks(island)
    sender.assert_not_called()
    island.snapshot_reader = original
    state["runs"][0].update(status="running", connection_error=False, tools=[{"id": "next", "status": "running"}],
                            updated_at=201)
    if failure in {"cancelled", "interrupted"}:
        state["runs"][0]["turn_id"] = "two"
    with patch("monitor.UI.island.time.time", return_value=201):
        island._poll()
    assert not island._hidden
    stop_callbacks(island)


def test_one_failed_source_does_not_turn_healthy_approval_red_or_revoke_it(native):
    island, state, _, _ = native
    state.update(status="error", source_states={"codex": {"status": "error"}})
    state["runs"] = [task("error"), task("waiting_confirmation", app_id="opencode", run_id="opencode:live",
        pending_confirmations=[{"id": "ask", "status": "pending", "requested_at": 101}])]
    state["actions"] = [{"type": "approve_tool", "run_id": "opencode:live", "confirmation_id": "ask"}]
    for tick in (100, 160):
        with patch("monitor.UI.island.time.time", return_value=tick):
            island._poll()
        stop_callbacks(island)
    assert not island._hidden
    assert island._run()["run_id"] == "opencode:live" and island._state() == "waiting_confirmation"
    assert island.snapshot["actions"] == state["actions"]


@pytest.mark.parametrize("expanded", [False, True])
def test_borderless_surface_follows_color_and_entire_window_alpha(native, expanded):
    island, state, _, _ = native
    island.expanded = expanded
    for color, opacity, ink in [("#ffffff", 100, "#19253a"), ("#2362b0", 50, "#eef4ff"),
                                ("#3f3c6d", 20, "#eef4ff"), ("#ffffff", 0, "#19253a")]:
        state["preferences"].update(monitor_background_color=color, monitor_opacity=opacity)
        island._poll()
        stop_callbacks(island)
        island._opening = False
        island._animation = None
        island._size = (420, 260) if expanded else (300, 62)
        island._geometry(*island._size, island.root.winfo_x(), island.root.winfo_y())
        island.draw()
        canvas = island.canvas
        assert canvas.find_withtag("island_surface") == (canvas.find_all()[0],)
        # Every body pixel uses exactly the selected fill, including the rim;
        # transparency alone cuts its silhouette. No tinted outer bands remain.
        frame = island._surface_frame
        rgb = tuple(int(color[i:i+2],16) for i in (1,3,5))
        assert frame.convert("RGB").getextrema() == tuple((v,v) for v in rgb)
        assert frame.getpixel((0,0))[3] == 0
        assert frame.getpixel((frame.width//2,0))[3] == 255
        assert float(island.root.attributes("-alpha")) == pytest.approx(opacity/100, abs=.01)


def test_delayed_history_after_restart_stays_hidden_and_silent_then_new_turn_plays(native, tmp_path):
    first, state, _, _ = native
    state["preferences"]["monitor_sound_enabled"] = True
    state["runs"][0].update(status="completed", ended_at=101)
    with patch("monitor.UI.island.time.time", return_value=101):
        first._poll()
    stop_callbacks(first)
    path = first.selection.lifetime.history.path
    first.selection.lifetime.history.close()
    # Start with no source data to model delayed worker initialization.
    value = {"backend_id": "new", "status": "offline", "runs": [], "preferences": state["preferences"]}
    root = tk.Toplevel(first.root)
    player = Mock()
    second = DynamicIsland(root, lambda: deepcopy(value), completion_player=player,
                           display_history=DisplayHistory(path))
    try:
        stop_callbacks(second)
        value.update(runs=deepcopy(state["runs"]), status="completed")
        with patch("monitor.UI.island.time.time", return_value=102):
            second._poll()
        assert second._hidden
        player.play.assert_not_called()
        stop_callbacks(second)
        value["runs"][0].update(turn_id="next", status="running", started_at=103, updated_at=103)
        with patch("monitor.UI.island.time.time", return_value=103):
            second._poll()
        assert not second._hidden
        stop_callbacks(second)
        value["runs"][0].update(status="completed", ended_at=104, updated_at=104)
        with patch("monitor.UI.island.time.time", return_value=104):
            second._poll()
            second._poll()
        player.play.assert_called_once_with("sound-1")
        stop_callbacks(second)
    finally:
        second.close()
