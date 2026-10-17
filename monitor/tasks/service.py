"""Merge local reminders into the island without touching Agent adapters."""
from datetime import datetime
import sqlite3
import time

from .store import TaskStore
from .schedule import next_due
from ..music.play.reminder import ReminderSoundPlayer

APP_ID = "solo_task"


class TaskService:
    def __init__(self, root, preferences, *, player=None):
        self.store = TaskStore(root)
        self.preferences = preferences
        self.player = player if player is not None else ReminderSoundPlayer()
        self.error = ""
        self.rows = []
        self.upcoming = None
        self.upcoming_count = 0

    def read(self, now=None):
        now = time.time() if now is None else now
        try:
            self.rows = self.store.poll(now)
            prefs = self.preferences()
            preview = (self.store.upcoming_reminders(now) if prefs.get("island_resident",False)
                       and prefs.get("task_monitor_enabled",True) else [])
            # Header count and expanded detail always come from one query.
            self.upcoming = preview[0] if preview else None
            self.upcoming_count = len(preview)
            if not prefs.get("task_monitor_enabled", True):
                self.player.retain(set())
                self.rows = []
                self.error = ""
                return []
            audible = prefs.get("task_sound_enabled", True)
            keys = {f"{r['id']}:{r['cycle']}" for r in self.rows[:5]}
            self.player.retain(keys if audible else set())
            for row in self.rows[:5]:
                if self.store.claim_sound(row["id"], row["cycle"]) and audible:
                    self.player.play(f"{row['id']}:{row['cycle']}", prefs.get("task_reminder_sound", "sound-1"))
            self.error = ""
            return [self._run(row, now) for row in self.rows]
        except (OSError, ValueError, sqlite3.Error) as error:
            self.error = f"待办数据无法读取或保存：{error}"
            self.player.retain(set())
            self.rows = []
            self.upcoming = None
            self.upcoming_count = 0
            return []

    @staticmethod
    def _run(row, now):
        task = row["task"]
        iso = lambda stamp: datetime.fromtimestamp(stamp).astimezone().isoformat()
        return {"app_id": APP_ID, "run_id": "todo:"+row["id"], "turn_id": str(row["cycle"]),
                "status": "reminding", "started_at": iso(row["remind_at"]), "updated_at": iso(row["remind_at"]),
                "task_summary": task["title"], "message": task["body"], "steps": [], "tools": [],
                "pending_confirmations": [], "reminder": {**row, "next_due": next_due(task, max(now, row["due_at"]))}}

    def send(self, run_id, payload):
        if not self.preferences().get("task_monitor_enabled", True):
            raise ValueError("任务监控已关闭")
        if not run_id.startswith("todo:"):
            raise ValueError("待办身份无效")
        self.store.act(run_id[5:], payload.get("cycle"), payload.get("action"))
        self.read()

    def close(self):
        self.player.close()
        self.store.close()
