"""Transactional task definitions and durable reminder episodes (no login)."""
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .schedule import next_due, occurrence_key, validate


class TaskStore:
    def __init__(self, root):
        self.path = Path(root) / "tasks.sqlite3"
        self.connection = None
        self.error = ""
        self._closed = False

    def _db(self):
        if self._closed:
            raise ValueError("任务存储已关闭")
        if self.connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=.2)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("PRAGMA foreign_keys=ON")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version not in (0, 1, 2):
                    raise ValueError("任务数据库版本较新，请使用更新的 Solo")
                if version == 0:
                    with connection:
                        connection.execute("""CREATE TABLE IF NOT EXISTS tasks (
                            id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
                            start_date TEXT NOT NULL, time_of_day TEXT NOT NULL, weekdays TEXT NOT NULL,
                            lead_minutes INTEGER NOT NULL, enabled INTEGER NOT NULL, revision INTEGER NOT NULL,
                            created_at REAL NOT NULL, updated_at REAL NOT NULL)""")
                        connection.execute("""CREATE TABLE IF NOT EXISTS occurrences (
                            id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                            revision INTEGER NOT NULL, due_date TEXT NOT NULL, due_at REAL NOT NULL,
                            remind_at REAL NOT NULL, expires_at REAL NOT NULL, state TEXT NOT NULL,
                            cycle INTEGER NOT NULL DEFAULT 0, sound_cycle INTEGER NOT NULL DEFAULT -1,
                            UNIQUE(task_id, revision, due_date))""")
                        connection.execute("PRAGMA user_version=1")
                if version < 2:
                    # Explicit transaction makes schema + version atomic. Old
                    # definitions and acknowledged episode identities survive.
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        connection.execute("ALTER TABLE tasks ADD COLUMN interval_hours INTEGER NOT NULL DEFAULT 0")
                        connection.execute("PRAGMA user_version=2")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
                connection.execute("SELECT id FROM tasks LIMIT 0")
            except Exception:
                connection.close()
                raise
            self.connection = connection
        return self.connection

    @staticmethod
    def _task(row):
        task = dict(row)
        task["weekdays"] = json.loads(task["weekdays"])
        task["enabled"] = bool(task["enabled"])
        task.update(validate(task))
        return task

    def list(self):
        return [self._task(r) for r in self._db().execute("SELECT * FROM tasks ORDER BY created_at DESC, id")]

    def get(self, task_id):
        row = self._db().execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise ValueError("此任务已被删除")
        return self._task(row)

    def save(self, value, task_id=None, *, now=None, expected_revision=None):
        now = time.time() if now is None else now
        task = validate(value)
        if task["enabled"] and next_due(task, now) is None:
            raise ValueError("请选择未来的提醒时间，或设置有效的重复规则")
        db = self._db()
        with db:
            old = self.get(task_id) if task_id else None
            if old and expected_revision is not None and old["revision"] != expected_revision:
                raise ValueError("任务已更新，请重新打开编辑")
            if not old and db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 1000:
                raise ValueError("最多保存 1000 个任务，请先删除不需要的任务")
            task_id = task_id or uuid4().hex
            revision = old["revision"] + 1 if old else 1
            db.execute("""INSERT INTO tasks
                (id,title,body,start_date,time_of_day,weekdays,lead_minutes,enabled,revision,created_at,updated_at,interval_hours)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title, body=excluded.body,
                start_date=excluded.start_date, time_of_day=excluded.time_of_day, weekdays=excluded.weekdays,
                lead_minutes=excluded.lead_minutes, enabled=excluded.enabled, revision=excluded.revision,
                updated_at=excluded.updated_at, interval_hours=excluded.interval_hours""",
                (task_id, task["title"], task["body"], task["start_date"], task["time_of_day"],
                 json.dumps(task["weekdays"]), task["lead_minutes"], int(task["enabled"]), revision,
                 old["created_at"] if old else now, now, task["interval_hours"]))
            db.execute("UPDATE occurrences SET state='expired' WHERE task_id=? AND state IN ('active','snoozed')", (task_id,))
        return self.get(task_id)

    def delete(self, task_id):
        with self._db() as db:
            db.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def poll(self, now=None):
        now = time.time() if now is None else now
        db = self._db()
        tasks = self.list()
        with db:
            db.execute("UPDATE occurrences SET state='expired' WHERE expires_at<=? AND state IN ('active','snoozed')", (now,))
            # Only windows still relevant now are materialized. No catch-up
            # loop over every missed day when resuming after a long shutdown.
            for task in tasks:
                if not task["enabled"]:
                    continue
                due = next_due(task, now)
                if due is None or now < due - task["lead_minutes"] * 60:
                    continue
                key = occurrence_key(task, due)
                identifier = f"{task['id']}:{task['revision']}:{key}"
                db.execute("""INSERT OR IGNORE INTO occurrences
                    (id,task_id,revision,due_date,due_at,remind_at,expires_at,state)
                    VALUES (?,?,?,?,?,?,?,'active')""",
                    (identifier, task["id"], task["revision"], key, due,
                     due-task["lead_minutes"]*60, due))
            db.execute("UPDATE occurrences SET state='active' WHERE state='snoozed' AND remind_at<=? AND expires_at>?", (now, now))
        definitions = {t["id"]: t for t in tasks}
        rows = db.execute("""SELECT * FROM occurrences WHERE state='active' AND remind_at<=? AND expires_at>?
                             ORDER BY remind_at,due_at,task_id""", (now, now))
        return [{**dict(row), "task": definitions[row["task_id"]]} for row in rows
                if definitions[row["task_id"]]["enabled"] and definitions[row["task_id"]]["revision"] == row["revision"]]

    def act(self, occurrence_id, cycle, action, *, now=None):
        if action not in {"snooze", "acknowledge"} or type(cycle) is not int:
            raise ValueError("待办操作无效")
        now = time.time() if now is None else now
        db = self._db()
        with db:
            row = db.execute("SELECT * FROM occurrences WHERE id=?", (occurrence_id,)).fetchone()
            if row is None:
                raise ValueError("提醒已失效")
            task = self.get(row["task_id"])
            if (row["cycle"] != cycle or row["state"] != "active" or not row["remind_at"] <= now < row["expires_at"]
                    or not task["enabled"] or row["revision"] != task["revision"]):
                raise ValueError("此轮提醒已处理或到期，请查看最新卡片")
            if action == "acknowledge":
                db.execute("UPDATE occurrences SET state='acknowledged' WHERE id=?", (occurrence_id,))
            else:
                db.execute("""UPDATE occurrences SET state='snoozed', cycle=cycle+1, remind_at=?, expires_at=? WHERE id=?""",
                           (now+300, now+300+task["lead_minutes"]*60, occurrence_id))

    def claim_sound(self, occurrence_id, cycle):
        with self._db() as db:
            result = db.execute("""UPDATE occurrences SET sound_cycle=? WHERE id=? AND cycle=?
                                   AND state='active' AND sound_cycle<?""", (cycle, occurrence_id, cycle, cycle))
            return result.rowcount == 1

    def state_for(self, task_id):
        row = self._db().execute("""SELECT o.state,o.remind_at,o.due_at FROM occurrences o JOIN tasks t ON t.id=o.task_id
                                  WHERE o.task_id=? AND o.revision=t.revision ORDER BY o.due_at DESC LIMIT 1""", (task_id,)).fetchone()
        return dict(row) if row else {}

    def _reminder_candidates(self, now):
        """Read the next actual notification without creating/claiming a cycle.

        A snoozed instance and its future recurring schedule are independent.
        Already acknowledged/expired cycles must never reappear as 'next'.
        """
        candidates = []
        db = self._db()
        for task in self.list():
            if not task["enabled"]:
                continue
            rows = [dict(r) for r in db.execute(
                "SELECT * FROM occurrences WHERE task_id=? AND revision=? AND (due_at>? OR expires_at>?)",
                (task["id"],task["revision"],now,now))]
            known = {r["due_date"]:r for r in rows}
            for row in rows:
                if row["state"] == "snoozed" and row["remind_at"] > now:
                    candidates.append(dict(task=task,due_at=row["due_at"],remind_at=row["remind_at"],snoozed=True))
            after = now
            for _ in range(len(known)+1):
                due = next_due(task, after)
                if due is None:
                    break
                if occurrence_key(task,due) in known:
                    after = due
                    continue
                remind = due-task["lead_minutes"]*60
                if remind > now:
                    candidates.append(dict(task=task,due_at=due,remind_at=remind,snoozed=False))
                break
        return sorted(candidates,key=lambda r:(r["remind_at"],r["due_at"],r["task"]["id"]))

    def next_reminder(self, now=None):
        now = time.time() if now is None else now
        return next(iter(self._reminder_candidates(now)),None)

    def upcoming_reminders(self, now=None):
        """Next hour's distinct tasks, ordered by actual notification time.

        Use (now, now+3600]: active notifications belong to the task UI already.
        Snoozed and recurring occurrences of one definition count as one task.
        This query never creates occurrences or claims their sound.
        """
        now = time.time() if now is None else now
        result, seen = [], set()
        for row in self._reminder_candidates(now):
            if row["remind_at"] > now+3600:
                break
            task_id = row["task"]["id"]
            if task_id not in seen:
                seen.add(task_id)
                result.append(row)
        return result

    def close(self):
        self._closed = True
        if self.connection:
            self.connection.close()
            self.connection = None
