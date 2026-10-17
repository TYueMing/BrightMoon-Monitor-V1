"""HTTP control client for the opencode server (stdlib ``urllib`` only).

opencode exposes a local HTTP server (``opencode serve``, default port 4096, or
the desktop "sidecar" on a random authenticated port).  The monitor uses it for
the control actions offered by the Dynamic Island:

    cancel_run  -> ``POST /session/:id/abort``
    approve     -> ``POST /permission/:requestID/reply``  ``{"reply":"once"}``
    deny        -> ``POST /permission/:requestID/reply``  ``{"reply":"reject"}``

Pending permission and question requests are not persisted to ``opencode.db``;
they live only on the server. This client polls ``GET /permission`` and
``GET /question`` in a background thread and exposes both live lists. When the
server is unreachable the island disables controls but keeps monitoring.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .discovery import discover

DEFAULT_URL = "http://127.0.0.1:4096"

_POLL_SECONDS = 0.4
_RECONNECT_SECONDS = 2.0
_REDISCOVER_SECONDS = 8.0
_HEALTH_PATHS = ("/global/health", "/api/global/health")
_METRIC_CACHE_SECONDS = 0.8
_PROVIDER_CACHE_SECONDS = 10.0


class OpencodeClient:
    def __init__(self, base_url: str | None = None, username: str = "",
                 password: str = "", auto_discover: bool = True) -> None:
        preferred = base_url or os.environ.get("OPENCODE_MONITOR_URL") or ""
        self._auto_discover = bool(auto_discover and not preferred)
        self.username = username or os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
        self.password = password or os.environ.get("OPENCODE_SERVER_PASSWORD", "")
        if self._auto_discover:
            found = discover()
            if found:
                preferred = found.get("url", "")
                self.username = found.get("username") or self.username
                self.password = found.get("password") or self.password
        self.base_url = (preferred or DEFAULT_URL).rstrip("/")
        self.available = False
        self._lock = threading.RLock()
        self._pending: list[dict[str, Any]] = []
        self._questions: list[dict[str, Any]] = []
        self._session_statuses: dict[str, dict[str, Any]] = {}
        self._session_status_known = False
        self._directories: list[str] = []
        self._metric_cache: dict[tuple[str, str, str], tuple[float, Any]] = {}
        self._provider_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._stop = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_discovery = time.monotonic()
        self.available = self.health()
        self._start_poll()

    # -- low-level HTTP ------------------------------------------------

    @staticmethod
    def _apply_auth(
        request: urllib.request.Request, username: str, password: str
    ) -> None:
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None,
                 timeout: float = 5.0) -> tuple[int, bytes]:
        with self._lock:
            base_url = self.base_url
            username = self.username
            password = self.password
        url = base_url + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        self._apply_auth(request, username, password)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()

    # -- health / control ----------------------------------------------

    def health(self) -> bool:
        for path in _HEALTH_PATHS:
            try:
                status, _ = self._request("GET", path, timeout=1.5)
                if status == 200:
                    return True
            except (urllib.error.URLError, OSError, ValueError):
                continue
        return False

    @staticmethod
    def _scoped_path(path: str, directory: str = "") -> str:
        if not directory:
            return path
        separator = "&" if "?" in path else "?"
        return path + separator + urllib.parse.urlencode({"directory": directory})

    def set_directories(self, directories: list[str]) -> None:
        """Set active project directories used by directory-scoped APIs."""
        unique: list[str] = []
        for value in directories:
            directory = str(value or "").strip()
            if directory and directory not in unique:
                unique.append(directory)
        with self._lock:
            self._directories = unique

    def _request_directory(self, items: list[dict[str, Any]], request_id: str) -> str:
        with self._lock:
            for item in items:
                if str(item.get("id") or item.get("requestID") or "") == request_id:
                    return str(item.get("_directory") or "")
        return ""

    def abort(self, session_id: str, directory: str = "") -> bool:
        """Ask opencode to abort a running session."""
        if not self.available:
            return False
        try:
            session = urllib.parse.quote(session_id, safe="")
            path = self._scoped_path(f"/session/{session}/abort", directory)
            self._request("POST", path, timeout=3.0)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            self.available = False
            return False

    def reply_permission(self, request_id: str, reply: str, directory: str = "") -> bool:
        """Approve (once/always) or reject a pending permission request."""
        if not self.available or not request_id:
            return False
        try:
            directory = directory or self._request_directory(self._pending, request_id)
            request = urllib.parse.quote(request_id, safe="")
            path = self._scoped_path(f"/permission/{request}/reply", directory)
            self._request(
                "POST", path, {"reply": reply}, timeout=3.0
            )
            with self._lock:
                self._pending = [
                    item for item in self._pending
                    if str(item.get("id") or item.get("requestID") or "") != request_id
                ]
            return True
        except (urllib.error.URLError, OSError, ValueError):
            self.available = False
            return False

    def pending_permissions(self) -> list[dict[str, Any]]:
        """Return the cached list of live pending permission requests."""
        with self._lock:
            return list(self._pending)

    def pending_questions(self) -> list[dict[str, Any]]:
        """Return the cached list of live human-decision requests."""
        with self._lock:
            return list(self._questions)

    def reply_question(self, request_id: str, answers: list[list[str]],
                       directory: str = "") -> bool:
        if not self.available or not request_id:
            return False
        try:
            directory = directory or self._request_directory(self._questions, request_id)
            request = urllib.parse.quote(request_id, safe="")
            path = self._scoped_path(f"/question/{request}/reply", directory)
            self._request(
                "POST", path, {"answers": answers}, timeout=3.0
            )
            self._remove_question(request_id)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            self.available = False
            return False

    def reject_question(self, request_id: str, directory: str = "") -> bool:
        if not self.available or not request_id:
            return False
        try:
            directory = directory or self._request_directory(self._questions, request_id)
            request = urllib.parse.quote(request_id, safe="")
            path = self._scoped_path(f"/question/{request}/reject", directory)
            self._request("POST", path, timeout=3.0)
            self._remove_question(request_id)
            return True
        except (urllib.error.URLError, OSError, ValueError):
            self.available = False
            return False

    def _remove_question(self, request_id: str) -> None:
        with self._lock:
            self._questions = [
                item for item in self._questions
                if str(item.get("id") or item.get("requestID") or "") != request_id
            ]

    def session_status_snapshot(self) -> tuple[bool, dict[str, dict[str, Any]]]:
        """Return whether /session/status succeeded and its latest value."""
        with self._lock:
            return self._session_status_known, {
                key: dict(value) for key, value in self._session_statuses.items()
            }

    def _scoped_list(self, endpoint: str, directories: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for directory in directories or [""]:
            status, body = self._request(
                "GET", self._scoped_path(endpoint, directory), timeout=3.0
            )
            data = json.loads(body) if status == 200 else []
            if isinstance(data, dict):
                data = [data]
            for item in data if isinstance(data, list) else []:
                if isinstance(item, dict):
                    result.append({**item, "_directory": directory})
        return result

    def _scoped_statuses(self, directories: list[str]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for directory in directories or [""]:
            status, body = self._request(
                "GET", self._scoped_path("/session/status", directory), timeout=3.0
            )
            raw = json.loads(body) if status == 200 else {}
            if not isinstance(raw, dict):
                continue
            for session_id, value in raw.items():
                if session_id:
                    result[str(session_id)] = (
                        value if isinstance(value, dict) else {"type": value}
                    )
        return result

    def _metric_json(self, endpoint: str, session_id: str, directory: str = "") -> Any:
        """Fetch a read-only metric endpoint with a tiny polling cache."""
        key = (endpoint, str(session_id), str(directory))
        now = time.monotonic()
        with self._lock:
            cache = getattr(self, "_metric_cache", {})
            cached = cache.get(key)
        if cached and now - cached[0] < _METRIC_CACHE_SECONDS:
            return cached[1]
        encoded = urllib.parse.quote(str(session_id), safe="")
        status, body = self._request(
            "GET", self._scoped_path(endpoint.format(session=encoded), directory), timeout=1.0
        )
        value: Any = []
        if status == 200:
            try:
                value = json.loads(body)
            except (TypeError, ValueError):
                value = []
        with self._lock:
            if not hasattr(self, "_metric_cache"):
                self._metric_cache = {}
            self._metric_cache[key] = (now, value)
        return value

    def session_diff(self, session_id: str, directory: str = "") -> list[dict[str, Any]]:
        """Return the server's current file diff for a session."""
        if not self.available or not session_id:
            return []
        try:
            value = self._metric_json("/session/{session}/diff", session_id, directory)
            if isinstance(value, dict):
                value = value.get("files") or value.get("diff") or []
            return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        except (urllib.error.URLError, OSError, ValueError):
            with self._lock:
                if not hasattr(self, "_metric_cache"):
                    self._metric_cache = {}
                self._metric_cache[("/session/{session}/diff", str(session_id), str(directory))] = (time.monotonic(), [])
            return []

    def session_children(self, session_id: str, directory: str = "") -> list[dict[str, Any]]:
        """Return child sessions (subagents/subtasks) spawned by a session."""
        if not self.available or not session_id:
            return []
        try:
            value = self._metric_json("/session/{session}/children", session_id, directory)
            if isinstance(value, dict):
                value = value.get("children") or value.get("sessions") or []
            return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []
        except (urllib.error.URLError, OSError, ValueError):
            with self._lock:
                if not hasattr(self, "_metric_cache"):
                    self._metric_cache = {}
                self._metric_cache[("/session/{session}/children", str(session_id), str(directory))] = (time.monotonic(), [])
            return []

    def provider_snapshot(self, directory: str = "") -> dict[str, Any]:
        """Return provider/model limits used to calculate context occupancy."""
        if not self.available:
            return {}
        now = time.monotonic()
        with self._lock:
            cache = getattr(self, "_provider_cache", {})
            cached = cache.get(directory)
        if cached and now - cached[0] < _PROVIDER_CACHE_SECONDS:
            return dict(cached[1])
        try:
            # The provider catalog is global; unlike session APIs it should
            # not inherit a project directory (Unicode project roots can also
            # be rejected by older sidecars).
            status, body = self._request("GET", "/config/providers", timeout=3.0)
            if status != 200:
                status, body = self._request("GET", "/provider", timeout=5.0)
            value = json.loads(body) if status == 200 else {}
            if not isinstance(value, dict):
                value = {}
        except (urllib.error.URLError, OSError, ValueError):
            value = {}
        with self._lock:
            if not hasattr(self, "_provider_cache"):
                self._provider_cache = {}
            self._provider_cache[directory] = (now, value)
        return dict(value)

    # -- background permission polling ---------------------------------

    def _start_poll(self) -> None:
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="opencode-monitor-permissions", daemon=True,
        )
        self._poll_thread.start()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            if not self.available:
                self._try_reconnect()
                self._stop.wait(_RECONNECT_SECONDS)
                continue
            with self._lock:
                directories = list(self._directories)
            try:
                pending = self._scoped_list("/permission", directories)
                with self._lock:
                    self._pending = pending
            except (urllib.error.URLError, OSError, ValueError):
                with self._lock:
                    self._pending = []
                    self._questions = []
                    self._session_statuses = {}
                    self._session_status_known = False
                    self.available = False
                self._stop.wait(_RECONNECT_SECONDS)
                continue

            try:
                questions = self._scoped_list("/question", directories)
                with self._lock:
                    self._questions = questions
            except (urllib.error.URLError, OSError, ValueError):
                # Question support was added after permissions; keep the rest
                # of the monitor usable with older servers.
                with self._lock:
                    self._questions = []

            try:
                normalized = self._scoped_statuses(directories)
                with self._lock:
                    self._session_statuses = normalized
                    self._session_status_known = True
            except (urllib.error.URLError, OSError, ValueError):
                # Older servers may not expose this endpoint. Permissions and
                # control remain usable; the bridge then falls back to DB data.
                with self._lock:
                    self._session_statuses = {}
                    self._session_status_known = False
            self._stop.wait(_POLL_SECONDS)

    def _try_reconnect(self) -> None:
        if self.health():
            with self._lock:
                self.available = True
            return
        if not self._auto_discover:
            return
        now = time.monotonic()
        if now - self._last_discovery < _REDISCOVER_SECONDS:
            return
        self._last_discovery = now
        found = discover()
        if not found:
            return
        with self._lock:
            self.base_url = str(found.get("url") or self.base_url).rstrip("/")
            self.username = str(found.get("username") or self.username)
            self.password = str(found.get("password") or self.password)
        if self.health():
            with self._lock:
                self.available = True

    def close(self) -> None:
        self._stop.set()
        thread = self._poll_thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout=1.0)
