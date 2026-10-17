"""Independent source workers; no login, application backend or user account."""
from copy import deepcopy
from threading import Event, RLock, Thread
from uuid import uuid4
import os
import time

from .source_config import normalize_sources
from .display_history import digest
from .task_presentation import approval_queue, timestamp
from ..UI.window_state import LIVE

SOURCE_STALL_SECONDS = 30


def source_factories():
    from ..deepseek_monitor.adapter import DeepSeekSource
    from ..codex_monitor.adapter import CodexSource
    from ..opencode_monitor.adapter import OpenCodeSource
    return {"deepseek_harness": DeepSeekSource, "codex": CodexSource, "opencode": OpenCodeSource}


class SourceWorker:
    def __init__(self, factory, config, *, start=True):
        self.factory, self.config = factory, deepcopy(config)
        self.stop = Event()
        self.lock, self.adapter_lock = RLock(), RLock()
        self.source = None
        self.poll_started = None
        self.result = {"status": "connecting", "message": "正在连接", "runs": [], "actions": []}
        self.thread = None
        if start:
            self.thread = Thread(target=self.work, name="solo-source-reader", daemon=True)
            self.thread.start()

    def poll(self):
        with self.lock:
            self.poll_started = time.monotonic()
        try:
            with self.adapter_lock:
                if self.stop.is_set():
                    return
                if self.source is None:
                    self.source = self.factory(self.config)
                result = self.source.poll()
            if not isinstance(result, dict):
                raise ValueError("Invalid source snapshot")
        except Exception:
            result = {"status": "error", "message": "连接异常，请检查来源应用或数据目录", "runs": [], "actions": []}
        with self.lock:
            self.poll_started = None
            if not self.stop.is_set():
                if result.get("status") == "error":
                    result["runs"] = [{**r, "connection_error": True} for r in self.result.get("runs", [])]
                    result["actions"] = []
                self.result = result

    def work(self):
        try:
            while not self.stop.is_set():
                self.poll()
                self.stop.wait(.7)
        finally:
            self.dispose()

    def snapshot(self):
        with self.lock:
            result = deepcopy(self.result)
            # A blocked reader cannot publish its own failure. Detect loss of
            # refresh here without blocking siblings or inventing task success.
            if self.poll_started is not None and time.monotonic() - self.poll_started >= SOURCE_STALL_SECONDS:
                result.update(status="error", message="来源读取超时，等待恢复", actions=[],
                              runs=[{**r, "connection_error": True} for r in result.get("runs", [])])
            return result

    def send(self, action, run_id, payload):
        with self.adapter_lock:
            if self.stop.is_set() or self.source is None:
                raise ValueError("此监控来源已关闭")
            sender = getattr(self.source, "send", None)
            if not sender:
                raise ValueError("请在来源应用中处理此操作")
            return sender(action, run_id, payload)

    def dispose(self):
        with self.adapter_lock:
            source, self.source = self.source, None
            if source and callable(getattr(source, "close", None)):
                try:
                    source.close()
                except Exception:
                    pass

    def close(self):
        self.stop.set()
        if self.thread is None:
            self.dispose()
        # A slow OS discovery call must not block the Tk thread. The worker
        # releases its own adapter in finally; the detached result is ignored.


class SourceHub:
    def __init__(self, preferences_reader, *, factories=None, start_worker=True):
        self.preferences_reader = preferences_reader
        self.factories = source_factories() if factories is None else factories
        self.start_worker = start_worker
        self.backend_id = "monitor-solo:" + uuid4().hex
        self.revision = 0
        self.workers = {}
        self.lock = RLock()
        self.closed = False

    def read(self):
        preferences = self.preferences_reader()
        configured = normalize_sources(preferences.get("monitor_sources"))
        enabled = {k: v for k, v in configured.items() if v["enabled"] and k in self.factories}
        with self.lock:
            if self.closed:
                enabled = {}
            for key in list(self.workers):
                if key not in enabled or self.workers[key].config != enabled[key]:
                    self.workers.pop(key).close()
            for key, config in enabled.items():
                if key not in self.workers:
                    self.workers[key] = SourceWorker(self.factories[key], config, start=self.start_worker)
            results = {k: w.snapshot() for k, w in self.workers.items()}
            self.revision += 1
            revision = self.revision
        runs, actions, health = [], [], {}
        for key in configured:
            result = results.get(key)
            if result is None:
                health[key] = {"status": "unsupported" if key == "codeagent" else "disabled"}
                continue
            health[key] = {k: result[k] for k in ("status", "message", "read_only", "session_count") if k in result}
            data_path = configured[key].get("data_path") or ""
            scope = digest(os.path.normcase(os.path.normpath(os.path.expanduser(data_path)))) if data_path else "auto"
            runs.extend({**r, "app_id": key, "monitor_scope": scope,
                         "started_order": int(timestamp(r.get("started_at"))*1000)}
                        for r in result.get("runs", []) if isinstance(r, dict))
            actions.extend(result.get("actions", []))
        live = [r for r in runs if r.get("status") in LIVE and not r.get("connection_error")]
        queue = approval_queue(live)
        active = queue[0][0] if queue else max(live or runs, key=lambda r: r["started_order"], default={})
        return {"backend_id": self.backend_id, "revision": revision, "preferences": preferences,
                "status": active.get("status", "offline"), "active_run_id": active.get("run_id", ""),
                "runs": runs, "actions": actions, "active_runs": len(live), "source_states": health}

    def poll_sources(self):
        """Deterministic entry point for tests without background threads."""
        self.read()
        for worker in list(self.workers.values()):
            worker.poll()

    def send(self, action, run_id, payload):
        snapshot = self.read()
        run = next((r for r in snapshot["runs"] if r.get("run_id") == run_id), None)
        if not run or run.get("connection_error"):
            raise ValueError("任务已失效或连接已断开")
        available = any(a.get("type") == action and a.get("run_id") == run_id
                        and (not a.get("confirmation_id") or
                             (a["confirmation_id"] == payload.get("confirmation_id") and
                              a.get("expected_revision") == payload.get("expected_revision")))
                        for a in snapshot["actions"])
        if not available:
            raise ValueError("此请求已更新，请在来源应用中核对")
        with self.lock:
            worker = self.workers.get(run["app_id"])
        if not worker:
            raise ValueError("此监控来源已关闭")
        return worker.send(action, run_id, payload)

    def close(self):
        with self.lock:
            self.closed = True
            workers, self.workers = self.workers, {}
        for worker in workers.values():
            worker.close()
