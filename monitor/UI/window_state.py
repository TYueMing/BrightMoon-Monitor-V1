"""Presentation-only visibility and motion; never cancels an Agent."""

import math
from datetime import datetime
from ..src.preferences import DEFAULT_HIDE_MINUTES, normalize_preferences
from .common_model import FINAL as FINISHED

LIVE = {"queued", "running", "waiting", "waiting_model", "waiting_lock", "waiting_confirmation", "waiting_input", "paused", "cancellation_requested", "reminding"}
IDLE_HIDE_SECONDS = DEFAULT_HIDE_MINUTES * 60.0
SPRING_SECONDS = .72


def spring_progress(t: float) -> float:
    """A damped step response with two overshoots, then an exact rest."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    damping, frequency = 6.3, 4.0 * math.pi
    return 1 - math.exp(-damping*t) * (math.cos(frequency*t) + damping/frequency*math.sin(frequency*t))


def should_hide(snapshot: dict, now: float) -> bool:
    # A failed read or an unresolved approval never triggers idle hiding.
    if snapshot.get("status") == "error":
        return False
    runs = [r for r in snapshot.get("runs", []) if isinstance(r, dict)]
    if any(r.get("status") in LIVE for r in runs):
        return False
    finished = [r for r in runs if r.get("status") in FINISHED]
    if not finished:
        return False
    ends = []
    for run in finished:
        try:
            ends.append(datetime.fromisoformat(str(run.get("ended_at") or run["updated_at"])).timestamp())
        except (KeyError, TypeError, ValueError):
            return False
    preferences = normalize_preferences(snapshot.get("preferences"))
    hide_seconds = preferences["monitor_hide_minutes"] * 60
    return now >= max(ends) + hide_seconds


class SnapshotCursor:
    """A user-selected historical run must not pin the *next* request."""

    def __init__(self):
        self.backend_id = None
        self.revision = -1
        self.seen = set()

    def accept(self, snapshot: dict) -> tuple[bool, bool]:
        backend = snapshot.get("backend_id", "")
        revision = int(snapshot.get("revision", 0))
        same = backend == self.backend_id
        if same and revision < self.revision:
            return False, False
        live = {r.get("run_id") for r in snapshot.get("runs", []) if r.get("status") in LIVE}
        fresh = bool(live - self.seen) or (not same and self.backend_id is not None)
        self.seen = {r.get("run_id") for r in snapshot.get("runs", [])}
        self.backend_id, self.revision = backend, revision
        return True, fresh
