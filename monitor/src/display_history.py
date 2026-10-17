"""Portable task display receipts. No account, parent project or agent commands.

SQLite retains receipts after the bounded memory cache evicts them. Completed
and unavailable episodes from a previous launch start retired; a real resume
or a new execution generation is still recognized by TaskLifetime.
"""
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

MAX_HISTORY = 8192  # Memory cache only; durable receipts are not aged/deleted.
TOUCH_SECONDS = 86400
_TIMED = {"paused", "unavailable", "stopping", "finished"}
_PHASES = _TIMED | {"live", "attention"}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                             default=str).encode("utf-8")).hexdigest()


def _validated(value):
    if not isinstance(value, dict) or value.get("phase") not in _PHASES:
        raise ValueError("Invalid display phase")
    if not all(isinstance(value.get(k), (int, float)) and not isinstance(value[k], bool)
               and math.isfinite(value[k]) for k in ("since", "touched", "watermark")):
        raise ValueError("Invalid display timestamps")
    evidence = value.get("evidence", [])
    if not isinstance(evidence, list) or not all(isinstance(v, str) and len(v) == 64 for v in evidence):
        raise ValueError("Invalid evidence")
    return {"phase": value["phase"], "since": value["since"], "touched": value["touched"],
            "watermark": value["watermark"], "evidence": evidence[-16:],
            "attention": str(value.get("attention", ""))[:64],
            "retired": value.get("retired") is True, "stopped": value.get("stopped") is True,
            "terminal_seen": value.get("terminal_seen") is True,
            "display_session": str(value.get("display_session", ""))[:32]}


class DisplayHistory:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else None
        self.records = {}
        self.pending = set()
        self.error = ""
        self.session = uuid4().hex
        self.connection = None
        self._next_write = 0.0
        self._urgent = False
        self._closed = False
        self._open()

    @classmethod
    def for_data_root(cls, root):
        return cls(Path(root) / "display_history.sqlite3")

    @property
    def dirty(self):
        return bool(self.pending)

    def _open(self):
        if self.path is None or self.connection is not None or self._closed:
            return
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=.15)
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("Unsupported history version")
            if version == 0:
                connection.execute("CREATE TABLE IF NOT EXISTS display_receipts (identity TEXT PRIMARY KEY, body TEXT NOT NULL)")
                connection.execute("PRAGMA user_version=1")
                connection.commit()
            else:
                # Existing receipts can be read even while another connection
                # briefly holds a write reservation. Opening need not write.
                connection.execute("SELECT identity, body FROM display_receipts LIMIT 0")
            self.connection = connection
            self.error = ""
        except (OSError, ValueError, sqlite3.Error):
            if connection is not None:
                connection.close()
            self.error = "任务显示记录无法读取或保存，当前仅在内存中去重"

    def get(self, key):
        if key in self.records:
            return self.records[key]
        if self.connection is None:
            return None
        try:
            row = self.connection.execute("SELECT body FROM display_receipts WHERE identity=?", (key,)).fetchone()
            if row is None:
                return None
            record = _validated(json.loads(row[0]))
        except (ValueError, TypeError, sqlite3.Error):
            self.error = "部分任务显示记录不可读取，当前任务将重新核对"
            return None
        # Reopening Solo must not replay a previously shown result, including
        # a completion whose configured grace period had not yet elapsed.
        if record["display_session"] != self.session and record["phase"] in _TIMED:
            record["retired"] = True
        self.records[key] = record
        return record

    def put(self, key, record):
        record = {**record, "display_session": self.session}
        old = self.records.get(key)
        if old != record:
            self.records[key] = record
            self.pending.add(key)
            # Save retirements and final outcomes in this poll, even when a
            # progress write occurred less than two seconds ago.
            if record["phase"] in _TIMED and (old is None or old["phase"] != record["phase"]
                                               or old["retired"] != record["retired"]):
                self._urgent = True

    def prune(self, present, now):
        # Evict only saved, absent entries from RAM. An old task can return
        # after months or after moving this folder: its disk receipt survives.
        excess = max(0, len(self.records) - MAX_HISTORY)
        absent = sorted((key for key in self.records if key not in present and key not in self.pending),
                        key=lambda key: self.records[key]["touched"])
        for key in absent[:excess]:
            del self.records[key]

    def flush(self, now, *, force=False):
        if self.path is None:
            self.pending.clear()
            self._urgent = False
            return
        if self._closed or not self.pending or (not force and not self._urgent and now < self._next_write):
            return
        self._next_write = now + 2
        self._urgent = False
        self._open()
        if self.connection is None:
            return
        try:
            # Transactional upserts keep previous receipts intact on failures.
            with self.connection:
                self.connection.executemany(
                    "INSERT INTO display_receipts(identity,body) VALUES(?,?) "
                    "ON CONFLICT(identity) DO UPDATE SET body=excluded.body",
                    [(key, json.dumps(self.records[key], ensure_ascii=False, separators=(",", ":")))
                     for key in self.pending])
            self.pending.clear()
            self.error = ""
        except sqlite3.Error:
            self.error = "任务显示记录暂未保存，当前窗口仍保留去重记录，将自动重试"

    def close(self):
        if not self._closed:
            self.flush(time.time(), force=True)
            if self.connection is not None:
                self.connection.close()
                self.connection = None
            self._closed = True
