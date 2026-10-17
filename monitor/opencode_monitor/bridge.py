"""Bridge from opencode's local data to the Dynamic Island snapshot format.

opencode persists everything into a local SQLite database (``opencode.db``)
plus the live server HTTP API.  This module reads the database read-only and
reduces it into the exact snapshot shape consumed by ``monitor.UI.island`` so
the same Dynamic Island can render opencode activity:

    opencode session            -> run
    opencode todo               -> task chain
    opencode message / part     -> timeline + stages + summary
    opencode pending tool part  -> tool confirmation

No third-party runtime dependencies: only ``sqlite3`` from the standard
library is required.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..src.preferences import normalize_preferences
from ..src.display_history import digest

from .config import load_config

MAX_RUNS = 80
MAX_TASKS = 32
MAX_TIMELINE = 120
MAX_CHILDREN = 24

# Statuses understood by monitor.UI.view_model + window_state.
LIVE_STATUSES = {"queued", "running", "waiting", "waiting_model", "waiting_lock",
                 "waiting_confirmation", "cancellation_requested"}
FINAL_STATUSES = {"completed", "failed", "cancelled"}

# opencode todo status -> monitor task status.
_TODO_STATUS = {"pending": "pending", "in_progress": "running",
                "completed": "completed", "cancelled": "cancelled",
                "failed": "failed"}

# opencode tool state.status -> monitor status.
_TOOL_STATUS = {"pending": "waiting_confirmation", "running": "running",
                "completed": "completed", "error": "failed"}

STATUS_TEXT = {
    "running": "执行中...", "waiting_model": "等待模型...",
    "waiting_confirmation": "待授权", "waiting_lock": "等待资源",
    "queued": "等待调度", "cancellation_requested": "正在停止...",
    "completed": "执行完成", "failed": "执行失败", "cancelled": "已停止",
    "stale": "监控失联", "error": "连接异常", "offline": "等待任务",
}


def opencode_data_dir() -> Path | None:
    """Locate the opencode data directory containing ``opencode.db``."""
    explicit = os.environ.get("OPENCODE_DATA_DIR")
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    home = Path.home()
    candidates.append(home / ".local" / "share" / "opencode")
    for env in ("APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME"):
        value = os.environ.get(env)
        if value:
            candidates.append(Path(value) / "opencode")
    for candidate in candidates:
        try:
            if (candidate / "opencode.db").is_file():
                return candidate
        except OSError:
            continue
    return None


def db_path() -> Path | None:
    data = opencode_data_dir()
    return (data / "opencode.db") if data else None


def _iso(timestamp_ms: float | None) -> str:
    if not timestamp_ms:
        return ""
    try:
        return datetime.fromtimestamp(float(timestamp_ms) / 1000.0, timezone.utc) \
            .astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError):
        return ""


def _safe_text(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_id(value: Any, limit: int = 120) -> str:
    return _safe_text(str(value) if value is not None else "", limit)


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _error_text(value: Any) -> str:
    """Return a useful message for both old string and current object errors."""
    if isinstance(value, dict):
        data = value.get("data") if isinstance(value.get("data"), dict) else {}
        return _safe_text(
            data.get("message") or value.get("message") or
            data.get("name") or value.get("name") or "OpenCode 执行失败"
        )
    return _safe_text(value)


def _parse_part(data: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (TypeError, ValueError):
        return {"type": "unknown", "raw": _safe_text(data, 80)}
    if not isinstance(value, dict):
        return {"type": "unknown", "raw": _safe_text(data, 80)}
    return value


def _message_time(message: sqlite3.Row, field: str = "created") -> int:
    data = _parse_part(message["data"])
    timing = data.get("time") if isinstance(data.get("time"), dict) else {}
    fallback = message["time_created"] if field == "created" else message["time_updated"]
    return _number(timing.get(field), _number(fallback))


def _role_for_part(part_type: str, tool: str = "") -> str:
    """Map an opencode part to a monitor agent string."""
    if part_type == "tool":
        return "tool"
    if part_type == "reasoning":
        return "planner_agent"
    if part_type == "text":
        return "summarizing_agent"
    return "agent"


class OpencodeBridge:
    """Read-only reducer: opencode.db -> island snapshot dict."""

    def __init__(self, path: Path | None = None) -> None:
        self.db = path or db_path()
        self._last_snapshot: dict[str, Any] | None = None
        self._backend_id = "opencode"
        self._revision = 0
        self._run_cache: dict[str, tuple[int, dict[str, Any]]] = {}

    # -- database access -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if not self.db or not self.db.is_file():
            raise RuntimeError("未找到 opencode 数据目录（opencode.db）")
        conn = sqlite3.connect(f"file:{self.db}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    # -- main entry point -----------------------------------------------

    def snapshot(self, user_key: str | None = None) -> dict[str, Any]:
        del user_key  # opencode has a single local identity
        try:
            result = self._build_snapshot()
        except Exception as exc:  # surfaced to the island as a state, not a crash
            result = {
                "schema_version": 3, "revision": self._revision,
                "backend_id": self._backend_id, "status": "error",
                "message": f"读取 opencode 数据失败：{_safe_text(exc, 80)}",
                "updated_at": _iso(time.time() * 1000), "runs": [],
                "timeline": [], "actions": [], "active_runs": 0,
                "preferences": normalize_preferences(load_config()),
                "host": {"state_path": str(self.db or "")},
            }
            return result
        self._last_snapshot = result
        return result

    # -- snapshot construction ------------------------------------------

    def _build_snapshot(self) -> dict[str, Any]:
        self._revision += 1
        conn = self._connect()
        try:
            sessions = self._load_sessions(conn)
            current_ids = {str(row["id"]) for row in sessions}
            self._run_cache = {
                key: value for key, value in self._run_cache.items() if key in current_ids
            }
            session_ids = [
                str(row["id"]) for row in sessions
                if not self._cached_final(row)
            ]
            messages = self._group_rows(
                self._load_for_sessions(conn, "message", session_ids), "session_id"
            )
            todos = self._group_rows(
                self._load_for_sessions(conn, "todo", session_ids), "session_id"
            )
            parts = self._group_rows(
                self._load_for_sessions(conn, "part", session_ids), "session_id",
                transform=self._part_row,
            )
        finally:
            conn.close()

        runs: list[dict[str, Any]] = []
        for index, session in enumerate(sessions):
            session_id = str(session["id"])
            cached = self._run_cache.get(session_id)
            if cached and cached[0] == _number(session["time_updated"]):
                run = dict(cached[1])
                run["started_order"] = index + 1
                runs.append(run)
                continue
            session_parts = parts.get(session_id, [])
            parts_by_message: dict[str, list[dict[str, Any]]] = {}
            for part in session_parts:
                parts_by_message.setdefault(str(part["message_id"]), []).append(part)
            run = self._build_run(
                session,
                index,
                messages.get(session_id, []),
                todos.get(session_id, []),
                parts_by_message,
            )
            runs.append(run)
            if run.get("status") in FINAL_STATUSES:
                self._run_cache[session_id] = (
                    _number(session["time_updated"]), dict(run)
                )

        active = self._select_active(runs)
        status = str(active.get("status") or "offline")
        actions = self._build_actions(active)
        return {
            "schema_version": 3,
            "revision": self._revision,
            "backend_id": self._backend_id,
            "preferences": normalize_preferences(load_config()),
            "status": status,
            "message": active.get("message") or STATUS_TEXT.get(status, "等待任务"),
            "updated_at": _iso(time.time() * 1000),
            "active_run_id": active.get("run_id", ""),
            "project_id": active.get("project_id", ""),
            "conversation_id": active.get("conversation_id", ""),
            "runs": runs[:MAX_RUNS],
            "timeline": active.get("timeline", [])[-MAX_TIMELINE:],
            "actions": actions,
            "active_runs": sum(1 for r in runs if r.get("status") in LIVE_STATUSES),
            "host": {"state_path": str(self.db or "")},
        }

    def _load_sessions(self, conn: sqlite3.Connection) -> list[sqlite3.Row]:
        # OpenCode adds usage/summary columns across releases.  Discover them
        # instead of making the monitor incompatible with an older database.
        available = {str(row[1]) for row in conn.execute("PRAGMA table_info(session)")}
        base = ["id", "title", "directory", "agent", "model", "time_created",
                "time_updated", "time_archived", "parent_id"]
        optional = [
            "cost", "tokens_input", "tokens_output", "tokens_reasoning",
            "tokens_cache_read", "tokens_cache_write", "summary_additions",
            "summary_deletions", "summary_files", "summary_diffs", "time_compacting",
        ]
        columns = base + [name for name in optional if name in available]
        rows = conn.execute(
            f"SELECT {', '.join(columns)} FROM session "
            "ORDER BY time_updated DESC, id DESC LIMIT ?", (MAX_RUNS,)
        ).fetchall()
        return list(rows)

    def _cached_final(self, session: sqlite3.Row) -> bool:
        cached = self._run_cache.get(str(session["id"]))
        return bool(
            cached
            and cached[0] == _number(session["time_updated"])
            and cached[1].get("status") in FINAL_STATUSES
        )

    @staticmethod
    def _load_for_sessions(
        conn: sqlite3.Connection, table: str, session_ids: list[str]
    ) -> list[sqlite3.Row]:
        if not session_ids:
            return []
        placeholders = ",".join("?" for _ in session_ids)
        if table == "message":
            columns = "id, session_id, data, time_created, time_updated"
            order = "session_id, time_created, id"
        elif table == "part":
            columns = "id, message_id, session_id, data, time_created, time_updated"
            order = "session_id, time_created, id"
        elif table == "todo":
            columns = (
                "session_id, content, status, priority, position, "
                "time_created, time_updated"
            )
            order = "session_id, position"
        else:
            raise ValueError(f"不支持的数据表：{table}")
        return list(conn.execute(
            f"SELECT {columns} FROM {table} WHERE session_id IN ({placeholders}) "
            f"ORDER BY {order}",
            session_ids,
        ).fetchall())

    @staticmethod
    def _group_rows(rows: list[Any], key: str, transform=None) -> dict[str, list[Any]]:
        grouped: dict[str, list[Any]] = {}
        for row in rows:
            item = transform(row) if transform else row
            grouped.setdefault(str(row[key]), []).append(item)
        return grouped

    @staticmethod
    def _part_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "message_id": row["message_id"],
            "session_id": row["session_id"],
            "time_created": row["time_created"],
            "time_updated": row["time_updated"],
            **_parse_part(row["data"]),
        }

    # -- per-session reduction ------------------------------------------

    def _build_run(
        self,
        session: sqlite3.Row,
        index: int,
        messages: list[sqlite3.Row],
        todos: list[sqlite3.Row],
        parts_by_message: dict[str, list[dict[str, Any]]],
    ) -> dict[str, Any]:
        session_id = str(session["id"])
        turn_messages = self._turn_messages(messages)
        status, message, error, ended_ms, parts = self._derive_status(
            turn_messages, parts_by_message
        )
        turn_started_ms = self._turn_started_ms(turn_messages) or _number(session["time_created"])
        tasks = self._build_tasks(todos, turn_started_ms)
        timeline = self._build_timeline(turn_messages, parts_by_message)
        stages = self._build_stages(timeline)
        summary = self._derive_summary(turn_messages, parts_by_message, session["title"])
        plan_mode = "direct" if not tasks else "tool"
        usage = self._session_usage(session)
        changes = self._session_changes(session)
        compaction = self._session_compaction(session, status)

        current_task_id = ""
        for task in tasks:
            if task["status"] in ("running", "pending"):
                current_task_id = str(task["id"])
                break

        return {
            "run_id": session_id,
            "completion_id": f"{session_id}:{turn_started_ms}",
            # Message/part revisions reflect execution, unlike the session's
            # updated time (which also changes after title/metadata edits).
            "monitor_activity_id": digest([
                [str(msg["id"]), _number(msg["time_updated"]),
                 [[str(part.get("id", "")), _number(part.get("time_updated"))]
                  for part in parts_by_message.get(str(msg["id"]), [])]]
                for msg in turn_messages[-12:]
            ]),
            "user_key": "opencode",
            "project_id": _safe_id(session["directory"]) or "default_program",
            "conversation_id": "",
            "source": "human",
            "status": status,
            "agent": self._derive_agent(parts),
            "phase": status,
            "message": message,
            "task_id": current_task_id,
            "task_summary": _safe_text(summary, 120),
            "started_at": _iso(turn_started_ms),
            "updated_at": _iso(_number(session["time_updated"])),
            "summary_preview": _safe_text(summary, 200),
            "model": _safe_id(session["model"]),
            "parent_id": _safe_id(session["parent_id"]),
            "usage": usage,
            "context": {
                "used_tokens": usage["total"], "limit_tokens": 0,
                "percent": None, "source": "unknown",
            },
            "changes": changes,
            "children": [],
            "child_summary": {"total": 0, "running": 0, "completed": 0, "failed": 0},
            "compaction": compaction,
            "lease_expires_at": _iso(time.time() * 1000 + 20000),
            "seq": _number(session["time_updated"]),
            "error": _safe_text(error),
            "ended_at": _iso(ended_ms),
            "cancel_requested": False,
            "tasks": tasks[:MAX_TASKS],
            "timeline": timeline[-MAX_TIMELINE:],
            # Replyable permission requests only exist in the HTTP server and
            # are merged by main._enrich_snapshot. A pending DB tool part is
            # merely a tool that has not started yet; its callID is not a
            # permission request ID.
            "confirmations": [],
            "pending_confirmations": [],
            "stages": stages,
            "stage_rows": [stage["agent"] for stage in stages],
            "started_order": index + 1,
            "turn_id": "",
            "plan_mode": plan_mode,
            "progress": {
                "total": len(tasks),
                "completed": sum(t.get("status") == "completed" for t in tasks),
                "failed": sum(t.get("status") == "failed" for t in tasks),
                "current_task_id": current_task_id,
            },
        }

    @staticmethod
    def _session_usage(session: sqlite3.Row) -> dict[str, Any]:
        """Normalize token/cost columns while tolerating old schemas."""
        def value(name: str) -> int:
            try:
                return max(0, _number(session[name]))
            except (IndexError, KeyError):
                return 0
        input_tokens = value("tokens_input")
        output = value("tokens_output")
        reasoning = value("tokens_reasoning")
        cache_read = value("tokens_cache_read")
        cache_write = value("tokens_cache_write")
        return {
            "input": input_tokens, "output": output, "reasoning": reasoning,
            "cache_read": cache_read, "cache_write": cache_write,
            "total": input_tokens + output + reasoning,
            "cost": float(session["cost"] or 0) if "cost" in session.keys() else 0.0,
        }

    @staticmethod
    def _session_changes(session: sqlite3.Row) -> dict[str, Any]:
        def value(name: str) -> int:
            try:
                return max(0, _number(session[name]))
            except (IndexError, KeyError):
                return 0
        return {
            "files": value("summary_files"),
            "additions": value("summary_additions"),
            "deletions": value("summary_deletions"),
            "diffs": value("summary_diffs"),
            "source": "session_summary",
            "items": [],
        }

    @staticmethod
    def _session_compaction(session: sqlite3.Row, status: str) -> dict[str, Any]:
        try:
            timestamp = _number(session["time_compacting"])
        except (IndexError, KeyError):
            timestamp = 0
        # time_compacting is a persisted marker, not a live event.  It is
        # therefore exposed as the last known completion; main.py upgrades it
        # to running when /session/status reports a compaction state.
        return {
            "status": "completed" if timestamp else "idle",
            "at": _iso(timestamp),
            "active": False,
            "source": "session" if timestamp else "unknown",
        }

    # -- reducers --------------------------------------------------------

    @staticmethod
    def _turn_messages(messages: list[sqlite3.Row]) -> list[sqlite3.Row]:
        """Return only the latest user turn from a multi-turn session."""
        for index in range(len(messages) - 1, -1, -1):
            if _parse_part(messages[index]["data"]).get("role") == "user":
                return messages[index:]
        return messages

    @staticmethod
    def _turn_started_ms(messages: list[sqlite3.Row]) -> int:
        """Start at the latest user turn, not at the long-lived session."""
        for message in reversed(messages):
            data = _parse_part(message["data"])
            if data.get("role") == "user":
                return _message_time(message)
        return _message_time(messages[-1]) if messages else 0

    def _derive_status(
        self,
        messages: list[sqlite3.Row],
        parts_by_message: dict[str, list[dict[str, Any]]],
    ) -> tuple[str, str, str, int | None, list[dict[str, Any]]]:
        if not messages:
            return "offline", "等待任务", "", None, []

        last = messages[-1]
        data = _parse_part(last["data"])
        role = str(data.get("role") or "")
        time_ms = data.get("time") if isinstance(data.get("time"), dict) else {}
        completed_ms = time_ms.get("completed")
        parts = parts_by_message.get(str(last["id"]), [])

        if role == "user":
            return "queued", "等待模型处理", "", None, []

        if completed_ms:
            if data.get("error"):
                error = _error_text(data.get("error"))
                return "failed", error, error, _number(completed_ms), parts
            return "completed", "执行完成", "", _number(completed_ms), parts

        # Assistant message still in progress: inspect its parts.
        tool_parts = [p for p in parts if p.get("type") == "tool"]
        pending = [p for p in tool_parts if (p.get("state") or {}).get("status") == "pending"]
        running = [p for p in tool_parts if (p.get("state") or {}).get("status") == "running"]
        errored = [p for p in tool_parts if (p.get("state") or {}).get("status") == "error"]

        if running:
            tool = str(running[-1].get("tool") or "tool")
            return "running", f"正在执行 · {tool}", "", None, parts
        if pending:
            tool = str(pending[-1].get("tool") or "tool")
            return "running", f"准备执行 · {tool}", "", None, parts
        if errored:
            # Tool failures can be handled by the model. The assistant message
            # error/completion fields decide whether the whole run failed.
            return "running", "工具执行出错，等待 Agent 处理", "", None, parts
        if parts and parts[-1].get("type") == "step-start":
            return "running", "Agent 正在处理", "", None, parts
        if any(p.get("type") in ("text", "reasoning") for p in parts):
            return "waiting_model", "等待模型...", "", None, parts
        return "running", "Agent 正在处理", "", None, parts

    @staticmethod
    def _derive_agent(parts: list[dict[str, Any]]) -> str:
        if not parts:
            return "agent"
        last = parts[-1]
        return _role_for_part(str(last.get("type")), str(last.get("tool") or ""))

    @staticmethod
    def _build_tasks(
        todos: list[sqlite3.Row], turn_started_ms: int = 0
    ) -> list[dict[str, Any]]:
        return [{
            "id": _safe_id(row["position"]),
            "goal": _safe_text(row["content"], 120),
            "status": _TODO_STATUS.get(str(row["status"]), "pending"),
        } for row in todos if max(
            _number(row["time_created"]), _number(row["time_updated"])
        ) >= turn_started_ms]

    def _build_timeline(
        self,
        messages: list[sqlite3.Row],
        parts_by_message: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for message in messages[-12:]:
            for part in parts_by_message.get(str(message["id"]), []):
                entries.append(self._part_to_timeline(part))
        return entries

    @staticmethod
    def _build_stages(timeline: list[dict[str, Any]]) -> list[dict[str, str]]:
        """Keep only roles actually observed during this OpenCode turn."""
        aliases = {
            "planner_agent": "planner",
            "tool": "tool manager",
            "summarizing_agent": "summarizer",
            "executor_agent": "executor",
            "verifier_agent": "verifier",
            "reflector_agent": "reflector",
            "compress_agent": "compress",
        }
        stages: dict[str, str] = {}
        for entry in timeline:
            role = aliases.get(str(entry.get("agent") or ""))
            if role:
                stages[role] = str(entry.get("status") or "running")
        order = (
            "planner", "executor", "tool manager", "verifier",
            "reflector", "summarizer", "compress",
        )
        return [
            {"agent": role, "status": stages[role]}
            for role in order if role in stages
        ]

    @staticmethod
    def _part_to_timeline(part: dict[str, Any]) -> dict[str, Any]:
        part_type = str(part.get("type") or "unknown")
        agent = _role_for_part(part_type, str(part.get("tool") or ""))
        if part_type == "tool":
            state = part.get("state") or {}
            status = _TOOL_STATUS.get(str(state.get("status")), "running")
            message = str(part.get("tool") or "工具调用")
        elif part_type == "text":
            status, message = "completed", _safe_text(part.get("text"), 160)
        elif part_type == "reasoning":
            status, message = "running", "正在思考"
        elif part_type == "step-start":
            status, message = "running", "开始处理"
        elif part_type == "step-finish":
            status, message = "completed", "处理完成"
        else:
            status, message = "running", "处理中"
        ts = _number(part.get("time_updated") or part.get("time_created"))
        return {
            "seq": ts, "at": _iso(ts), "type": part_type,
            "agent": agent, "status": status, "message": message,
        }

    def _derive_summary(
        self,
        messages: list[sqlite3.Row],
        parts_by_message: dict[str, list[dict[str, Any]]],
        title: str,
    ) -> str:
        for message in reversed(messages):
            data = _parse_part(message["data"])
            if data.get("role") != "assistant":
                continue
            if data.get("error"):
                return _error_text(data.get("error"))
            parts = parts_by_message.get(str(message["id"]), [])
            for part in reversed(parts):
                if part.get("type") == "text" and part.get("text"):
                    return _safe_text(part.get("text"), 200)
        return _safe_text(title, 200) or "本次任务未提供摘要。"

    # -- active run + actions -------------------------------------------

    @staticmethod
    def _select_active(runs: list[dict[str, Any]]) -> dict[str, Any]:
        if not runs:
            return {}
        priority = {"waiting_confirmation": 0, "cancellation_requested": 1,
                    "running": 2, "waiting_model": 2, "waiting_lock": 3,
                    "queued": 3, "waiting": 3}
        live = [r for r in runs if r.get("status") in priority]
        attention = [r for r in live if r.get("status") == "waiting_confirmation"]
        return (attention[0] if attention else live[0]) if live else runs[0]

    @staticmethod
    def _build_actions(active: dict[str, Any]) -> list[dict[str, Any]]:
        if not active:
            return []
        actions: list[dict[str, Any]] = []
        status = str(active.get("status") or "")
        if status == "waiting_confirmation":
            for confirmation in active.get("pending_confirmations", []):
                if confirmation.get("status") != "pending":
                    continue
                kind = str(confirmation.get("kind") or "permission")
                pairs = (
                    (("reject_question", "拒绝回答"), ("answer_question", "提交回答"))
                    if kind == "question"
                    else (("deny_tool", "拒绝"), ("approve_tool", "同意"))
                )
                for action_type, label in pairs:
                    actions.append({
                        "id": f"{action_type}:{confirmation.get('id')}", "type": action_type,
                        "label": label, "confirmation_id": confirmation.get("id"),
                        "request_id": confirmation.get("id"),
                        "run_id": active.get("run_id"),
                        "directory": confirmation.get("directory") or active.get("project_id", ""),
                    })
        if status in LIVE_STATUSES and status != "cancellation_requested":
            actions.append({
                "id": f"cancel:{active.get('run_id')}", "type": "cancel_run",
                "label": "请求停止", "run_id": active.get("run_id"),
                "directory": active.get("project_id", ""),
            })
        return actions


def read_snapshot(path: Path | None = None, user_key: str | None = None) -> dict[str, Any]:
    return OpencodeBridge(path).snapshot(user_key)
