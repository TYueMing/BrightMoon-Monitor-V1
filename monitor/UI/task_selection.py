"""Pure five-task view state, independent of Tk and of the monitored app."""
from .window_state import LIVE, FINISHED
from .task_lifetime import TaskLifetime
from ..src.task_presentation import approval_queue, run_order
from ..src.task_identity import task_identity


class TaskSelection:
    LIMIT = 5

    def __init__(self, history=None):
        self.runs = []
        self.records = {}
        self.focus_id = ""
        self.more = False
        self.backend_id = None
        self._batch = set()
        self._attention = None
        self._previous_live = set()
        self.lifetime = TaskLifetime(history)

    def update(self, snapshot, *, expanded=False, now=None):
        """Return True for a new independent batch/backend, not a parallel run."""
        records = self.lifetime.visible(snapshot, now)
        self.records = records
        backend = snapshot.get("backend_id", "")
        live = {task_identity(run) for run in records.values() if run.get("status") in LIVE and not run.get("connection_error")}
        changed = self.backend_id is not None and backend != self.backend_id
        new_batch = bool(live - self._previous_live) and not (live & self._previous_live)
        reset = changed or new_batch
        if reset:
            if changed:
                self._batch.clear()
            self.focus_id = ""
            self.more = False
            self._attention = None
        self.backend_id = backend
        self._batch.intersection_update(records)
        self._batch.update(records)
        # Each completion has its own deadline, even across new task batches.
        candidates = [r for key, r in records.items() if key in self._batch]
        if not candidates and records:
            candidates = [max(records.values(), key=run_order)]
            self._batch.add(candidates[0]["run_id"])
        queue = approval_queue([run for run in records.values() if not run.get("connection_error")], now)
        keys = [(run["run_id"], approval["id"]) for run, approval in queue]
        # Once attention is presented, a late/replayed request cannot preempt
        # it. The next FIFO item takes over only after that request is gone.
        head = self._attention if self._attention in keys else (keys[0] if keys else None)
        # Only a new queue head can automatically navigate. Manual navigation
        # does not clear this latch, so later arrivals cannot steal focus.
        auto_focused = bool(expanded and head and head != self._attention)
        if expanded and head != self._attention:
            if head:
                self.focus_id = head[0]
            self._attention = head
        priorities = []
        def add(run_id):
            if run_id in records and run_id not in priorities:
                priorities.append(run_id)
        reminders = sorted((r for r in records.values() if r.get("app_id")=="solo_task"),
                           key=lambda r:(r["reminder"]["remind_at"],r["reminder"]["due_at"],r["run_id"]))
        for run in reminders:
            add(run["run_id"])
        add(self.focus_id)
        for run, _ in queue:
            add(run["run_id"])
        for run in sorted(candidates, key=lambda r: (r.get("status") in LIVE, run_order(r)), reverse=True):
            add(run["run_id"])
        if reminders:
            self.runs = [records[key] for key in priorities[:self.LIMIT]]
            self.more = False
        else:
            self.runs = sorted((records[key] for key in priorities[:self.LIMIT]), key=run_order)
        if self.focus_id not in {r["run_id"] for r in self.runs}:
            target = head[0] if head else reminders[0]["run_id"] if reminders else snapshot.get("active_run_id")
            # Start with the first three circles; the last two remain behind
            # the ellipsis until the user opens it (or an approval needs it).
            available = self.runs if head else self.runs[:3]
            self.focus_id = target if target in {r["run_id"] for r in available} else (available[0]["run_id"] if available else "")
        if len(self.runs) <= 3:
            self.more = False
        elif auto_focused and self.focus_id not in {r["run_id"] for r in self.runs[:3]}:
            self.more = True
        self._previous_live = live
        return reset

    def select(self, run_id):
        if run_id not in {r["run_id"] for r in self.runs}:
            return False
        self.focus_id = run_id
        if run_id not in {r["run_id"] for r in self.runs[:3]}:
            self.more = True
        return True

    def collapse(self):
        """A fresh expansion may show the oldest outstanding request again."""
        self._attention = None

    def focused(self):
        return next((r for r in self.runs if r["run_id"] == self.focus_id), {})

    def visible_runs(self):
        if self.has_reminders() or self.more or len(self.runs) <= 3:
            return self.runs
        visible = self.runs[:3]
        if self.focus_id not in {r["run_id"] for r in visible}:
            visible = [*visible[:2], self.focused()]
        return visible

    def has_reminders(self):
        return any(r.get("app_id")=="solo_task" for r in self.runs)

    def caption(self):
        count = len(self.runs)
        waiting = sum(r.get("status") == "waiting_confirmation" and not r.get("connection_error") for r in self.runs)
        unavailable = sum(bool(r.get("connection_error")) or r.get("status") in {"stale", "error"} for r in self.runs)
        finished = sum(r.get("status") in FINISHED for r in self.runs)
        state = f"{waiting}项待授权" if waiting else f"{unavailable}项失联" if unavailable else "已结束" if finished == count else f"{finished}项已结束" if finished else "执行中"
        return f"共{count}个任务 · {state}"
