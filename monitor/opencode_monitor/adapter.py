"""OpenCode source adapter for the unified BrightMoon LiveView.

All OpenCode-specific state and control stays in this package.  The shared
SourceHub consumes the same ``poll`` / ``send`` contract as other providers.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from ..music.play import CompletionSoundPlayer
from .bridge import LIVE_STATUSES, OpencodeBridge
from .client import OpencodeClient
from .config import load_config
from .enrichment import _active_directories, _enrich_snapshot

# Match the account preference key; "opencodex" is a legacy Codex alias.
APP_ID = "opencode"
RUN_PREFIX = f"{APP_ID}:"
FINAL = {"completed", "failed", "cancelled"}
MAX_PUBLISHED_RUNS = 32


def database_path(config: dict[str, Any]) -> Path | None:
    """Interpret data_path as either opencode.db or its containing directory."""
    raw = str(config.get("data_path") or "").strip()
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    # The configured directory does not have to exist yet.  Treat only an
    # explicit .db path as a file so a temporarily missing data directory can
    # recover automatically when OpenCode creates it.
    return candidate if candidate.suffix.lower() == ".db" else candidate / "opencode.db"


def inspect(config: dict[str, Any]) -> dict[str, Any]:
    """Read-only source diagnostics suitable for a settings page."""
    path = database_path(config)
    bridge = OpencodeBridge(path)
    resolved = bridge.db
    if not resolved or not resolved.is_file():
        return {
            "status": "missing",
            "message": "未找到 OpenCode 数据库；请选择 opencode.db 或其数据目录。",
            "read_only": True,
            "data_path": str(resolved or path or ""),
        }
    try:
        snapshot = bridge.snapshot()
    except Exception:
        snapshot = {"status": "error"}
    if snapshot.get("status") == "error":
        return {
            "status": "unreadable", "message": "OpenCode 数据库无法只读解析。",
            "read_only": True, "data_path": str(resolved),
        }
    return {
        "status": "ready",
        "message": "数据库可读取；启用后自动发现 OpenCode 本地服务。连接成功后可处理授权、问题和停止请求；当前检查仅验证数据库。",
        "read_only": True,
        "data_path": str(resolved),
        "session_count": len(snapshot.get("runs", [])),
    }


class OpenCodeSource:
    """Database snapshot + live HTTP APIs exposed as one unified source."""

    app_id = APP_ID

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = dict(config or {})
        self.bridge = OpencodeBridge(database_path(self.config))
        self.client = OpencodeClient()
        self.started_at = time.time()
        self.observed: set[str] = set()
        self.published: dict[str, dict[str, Any]] = {}
        self.last_runs: dict[str, dict[str, Any]] = {}
        self._decision_ids: set[str] = set()
        self._decision_player = CompletionSoundPlayer()
        self._lock = threading.RLock()

    @staticmethod
    def _raw_id(run_id: str) -> str:
        value = str(run_id or "")
        return value[len(RUN_PREFIX):] if value.startswith(RUN_PREFIX) else value

    @staticmethod
    def _public_id(run_id: str) -> str:
        value = str(run_id or "")
        return value if value.startswith(RUN_PREFIX) else RUN_PREFIX + value

    @staticmethod
    def _timestamp(value: object) -> float:
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            return 0.0

    def _visible(self, run: dict[str, Any]) -> bool:
        raw_id = str(run.get("run_id") or "")
        if run.get("status") in LIVE_STATUSES:
            self.observed.add(raw_id)
            return True
        if raw_id in self.observed:
            return True
        return self._timestamp(run.get("updated_at")) >= self.started_at

    def _decorate(self, run: dict[str, Any], revision: int) -> dict[str, Any]:
        value = deepcopy(run)
        raw_id = str(value.get("run_id") or "")
        value["source_run_id"] = raw_id
        value["run_id"] = self._public_id(raw_id)
        value["app_id"] = APP_ID
        value["source_name"] = "OpenCode"
        value["monitor_detail"] = {
            "context": deepcopy(value.get("context") or {}),
            "changes": deepcopy(value.get("changes") or {}),
            "children": deepcopy(value.get("child_summary") or {}),
            "compaction": deepcopy(value.get("compaction") or {}),
        }
        confirmations = []
        for item in value.get("pending_confirmations", []):
            if not isinstance(item, dict):
                continue
            confirmation = deepcopy(item)
            confirmation["revision"] = revision
            confirmation.setdefault("arguments", {})
            confirmations.append(confirmation)
        value["confirmations"] = confirmations
        value["pending_confirmations"] = confirmations
        return value

    def _actions(self, snapshot: dict[str, Any], published: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        revision = int(snapshot.get("revision") or 0)
        for action in snapshot.get("actions", []):
            if not isinstance(action, dict):
                continue
            raw_id = str(action.get("run_id") or "")
            if raw_id not in published:
                continue
            item = deepcopy(action)
            item["run_id"] = self._public_id(raw_id)
            item["expected_revision"] = revision
            result.append(item)
        return result

    def _play_decisions(self, runs: list[dict[str, Any]]) -> None:
        """Play once for every new permission or human-question request."""
        current = {
            f"{item.get('kind', 'permission')}:{item.get('id')}"
            for run in runs
            for item in run.get("pending_confirmations", [])
            if isinstance(item, dict) and item.get("status") == "pending" and item.get("id")
        }
        preferences = load_config()
        if preferences.get("sound_enabled", True) and preferences.get("monitor_sound_enabled", True):
            for _ in sorted(current - self._decision_ids):
                self._decision_player.play(preferences.get("monitor_completion_sound", "sound-1"))
        # Keep only live IDs.  If OpenCode later reuses an ID for a genuinely
        # new request, it should produce a new reminder.
        self._decision_ids = current

    def poll(self) -> dict[str, Any]:
        # Settings may enable monitoring before OpenCode creates its first DB.
        # Rediscover only in automatic mode; never replace an explicit path.
        if not self.config.get("data_path") and (not self.bridge.db or not self.bridge.db.is_file()):
            from .bridge import db_path
            self.bridge.db = db_path()
        snapshot = self.bridge.snapshot()
        if snapshot.get("status") == "error":
            cached = [deepcopy(run) for run in self.published.values()]
            for run in cached:
                run["connection_error"] = True
            return {
                "status": "error", "message": snapshot.get("message") or "OpenCode 数据库读取失败",
                "runs": cached, "actions": [], "read_only": False,
            }
        self.client.set_directories(_active_directories(snapshot))
        enriched = _enrich_snapshot(snapshot, self.client)
        revision = int(enriched.get("revision") or 0)
        visible = [run for run in enriched.get("runs", []) if isinstance(run, dict) and self._visible(run)]
        visible = visible[:MAX_PUBLISHED_RUNS]
        decorated = [self._decorate(run, revision) for run in visible]
        if not self.client.available:
            # A desktop killed externally leaves its SQLite row running. Keep
            # durable outcomes but give unreachable live tasks a display limit.
            for run in decorated:
                if run.get("status") in LIVE_STATUSES:
                    run["connection_error"] = True
        published = {str(run.get("source_run_id")): run for run in decorated}
        self._play_decisions(decorated)
        with self._lock:
            self.published = deepcopy(published)
            self.last_runs = {key: deepcopy(run) for key, run in published.items()}
        return {
            "status": "ready" if self.client.available else "degraded",
            "message": (
                f"正在监控 {len(decorated)} 个 OpenCode 会话"
                if self.client.available else "数据库监控可用；正在重新连接 OpenCode 本地服务"
            ),
            "runs": decorated,
            "actions": self._actions(enriched, published) if self.client.available else [],
            "session_count": len(enriched.get("runs", [])),
            "read_only": not self.client.available,
        }

    def send(self, action: str, run_id: str, payload: dict[str, Any]) -> str:
        raw_id = self._raw_id(run_id)
        with self._lock:
            run = deepcopy(self.last_runs.get(raw_id) or {})
        if not run:
            raise ValueError("OpenCode 会话已失效")
        directory = str(payload.get("directory") or run.get("project_id") or "")
        confirmation_id = str(payload.get("confirmation_id") or payload.get("request_id") or "")
        if action == "cancel_run":
            accepted = self.client.abort(raw_id, directory)
        elif action in {"approve_tool", "deny_tool"}:
            accepted = self.client.reply_permission(
                confirmation_id, "once" if action == "approve_tool" else "reject", directory
            )
        elif action == "answer_question":
            answers = payload.get("answers")
            accepted = isinstance(answers, list) and self.client.reply_question(
                confirmation_id, answers, directory
            )
        elif action == "reject_question":
            accepted = self.client.reject_question(confirmation_id, directory)
        else:
            raise ValueError(f"OpenCode 不支持操作：{action}")
        if not accepted:
            raise RuntimeError("OpenCode 本地服务没有接受操作")
        return f"{APP_ID}:{uuid4().hex}"

    def close(self) -> None:
        self.client.close()

