"""Per-task display episodes and durable suppression of already-shown records."""
import time

from .window_state import FINISHED
from ..src.preferences import normalize_preferences
from ..src.task_presentation import timestamp
from ..src.display_history import DisplayHistory, TOUCH_SECONDS
from ..src.task_identity import task_identity, activity_evidence, attention_identity

ATTENTION_RETIRE_SECONDS = 60
STOPPED = {"cancelled", "interrupted"}
UNAVAILABLE = {"stale", "error"}
TIMED = {"paused", "unavailable", "stopping", "finished"}


class TaskLifetime:
    def __init__(self, history=None):
        self.history = history if history is not None else DisplayHistory()
        self.empty_error_since = None
        self.has_seen_tasks = False

    @staticmethod
    def _phase(run, attention):
        state = run.get("status")
        # Durable terminal facts take precedence over a transport error.
        if state in FINISHED:
            return "finished"
        if run.get("connection_error") or state in UNAVAILABLE:
            return "unavailable"
        if attention or state in {"waiting_confirmation", "waiting_input"}:
            return "attention"
        if state == "paused":
            return "paused"
        if state == "cancellation_requested":
            return "stopping"
        return "live"

    @staticmethod
    def _since(run, phase, now):
        return min(now, timestamp(run.get("ended_at"), timestamp(run.get("updated_at"), now))) if phase == "finished" else now

    def visible(self, snapshot, now=None):
        now = time.time() if now is None else now
        delay = normalize_preferences(snapshot.get("preferences"))["monitor_hide_minutes"] * 60
        if snapshot.get("status") == "error" and not snapshot.get("runs"):
            if self.empty_error_since is None:
                self.empty_error_since = now
        else:
            self.empty_error_since = None
        visible, present = {}, set()
        actionable = {a.get("run_id") for a in snapshot.get("actions", []) if isinstance(a, dict)
                      and a.get("type") in {"approve_tool", "deny_tool", "answer_question", "reject_question"}}
        for run in snapshot.get("runs", []):
            if not isinstance(run, dict) or not run.get("run_id"):
                continue
            if run.get("app_id") == "solo_task":
                # Local occurrences already have durable snooze/acknowledgement
                # state. Agent outage/retirement rules must not suppress them.
                if run.get("reminder",{}).get("expires_at",0) > now:
                    visible[run["run_id"]] = run
                continue
            self.has_seen_tasks = True
            key = task_identity(run)
            present.add(key)
            evidence = activity_evidence(run)
            attention = attention_identity(run, now)
            phase = self._phase(run, attention)
            event_time = timestamp(run.get("updated_at"))
            prior = self.history.get(key)
            if prior is None:
                entry = {"phase": phase, "since": self._since(run, phase, now), "retired": False,
                         "evidence": [evidence] if evidence else [], "attention": attention, "watermark": event_time,
                         "terminal_seen": phase == "finished", "stopped": run.get("status") in STOPPED,
                         "touched": now}
            else:
                entry = {**prior, "evidence": list(prior["evidence"])}
                recent = not event_time or event_time >= entry["watermark"]
                advanced = bool(evidence) and evidence not in entry["evidence"] and recent
                resumed = entry["phase"] == "paused" and phase == "live" and recent
                request = phase == "attention" and (
                    (attention and attention != entry["attention"] and recent)
                    or run["run_id"] in actionable or bool(run.get("deepseek_interactions"))
                    or run.get("status") == "waiting_input")
                # Reconnection alone, changed summaries and old replayed steps
                # cannot rearm a retired episode. Actual requests always win.
                # A final outcome is authoritative for this generation. An
                # adapter replaying an earlier running snapshot must not undo
                # it, even if hydration adds previously unseen tool metadata.
                wake = (phase == "live" and not entry["terminal_seen"] and (advanced or resumed)) or request or (
                    phase == "finished" and not entry["terminal_seen"])
                if wake and (entry["retired"] or entry["phase"] in TIMED):
                    entry.update(phase=phase, since=self._since(run, phase, now), retired=False,
                                 stopped=run.get("status") in STOPPED)
                elif not entry["retired"] and entry["phase"] not in TIMED:
                    if phase != entry["phase"]:
                        entry.update(phase=phase, since=self._since(run, phase, now),
                                     stopped=run.get("status") in STOPPED)
                elif phase == "paused" and not entry["terminal_seen"]:
                    # Learn an explicit pause while keeping its existing
                    # deadline/retirement. A later paused -> running is resume.
                    entry["phase"] = "paused"
                # Keep evidence seen while disconnected as baseline, not proof
                # of later recovery. Backlog decoding must not wake on connect.
                if evidence and evidence not in entry["evidence"]:
                    entry["evidence"] = [*entry["evidence"], evidence][-16:]
                    entry["watermark"] = max(entry["watermark"], event_time)
                entry["attention"] = attention or entry["attention"]
                if entry["phase"] != prior["phase"]:
                    entry["watermark"] = max(entry["watermark"], event_time)
                entry["terminal_seen"] |= phase == "finished"
                if prior.get("display_session") != self.history.session and phase == "finished":
                    # It finished while Solo was closed. Historical results
                    # should not open the island or play a completion cue.
                    entry.update(phase="finished", since=self._since(run, phase, now), retired=True,
                                 stopped=run.get("status") in STOPPED)
            if entry["phase"] in TIMED:
                timeout = delay if entry["phase"] == "finished" and not entry["stopped"] else ATTENTION_RETIRE_SECONDS
                if now >= entry["since"] + timeout:
                    entry["retired"] = True
            if prior != entry or now - entry["touched"] >= TOUCH_SECONDS:
                entry["touched"] = now
            self.history.put(key, entry)
            if not entry["retired"]:
                visible[run["run_id"]] = run
        # Transport backend IDs change on restart; they are deliberately not a
        # reason to clear this account's display history.
        self.history.prune(present, now)
        self.history.flush(now)
        return visible

    def empty_error_expired(self, now):
        return self.empty_error_since is not None and now >= self.empty_error_since + ATTENTION_RETIRE_SECONDS
