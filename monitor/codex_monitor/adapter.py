"""Merge durable rollout facts with authoritative App Server task/approval state."""
from copy import deepcopy
import time
from threading import RLock

from ..src.source_config import codex_session_root
from ..src.task_presentation import timestamp
from .app_server import AppServerClient
from .log_reader import discover, RolloutReader
from .projection import RolloutProjection, TERMINAL
from .discovery import ServerDiscovery


class CodexSource:
    app_id = "codex"

    def __init__(self, config):
        self.root = codex_session_root(config).resolve()
        self.started = time.time()
        self.logs, self.observed, self.published = {}, set(), {}
        self.scanned_at = 0.0
        self.scan_error = ""
        self.mode = config.get("connection_mode", "manual" if config.get("server_url") else "auto")
        self.client = AppServerClient(config["server_url"], self.root) if self.mode == "manual" and config.get("server_url") else None
        self.discovery = ServerDiscovery() if self.mode == "auto" else None
        self.auto_clients = {}
        self._clients_lock = RLock()
        self.closed = False

    def _clients(self):
        with self._clients_lock:
            return ([self.client] if self.client else []) + [entry[1] for entry in self.auto_clients.values()]

    def _discover_clients(self):
        if not self.discovery:
            return
        listeners = self.discovery.poll()
        with self._clients_lock:
            if self.closed:
                return  # A concurrent disable must not recreate a disposed adapter.
            for endpoint in set(self.auto_clients)-set(listeners):
                self.auto_clients.pop(endpoint)[1].close()
            for endpoint, pid in listeners.items():
                entry = self.auto_clients.get(endpoint)
                if not entry or entry[0] != pid:
                    if entry:
                        entry[1].close()
                    self.auto_clients[endpoint] = (pid, AppServerClient(endpoint, self.root))

    def _logs(self):
        if not self.root.is_dir():
            return [], "missing", "未找到 Codex 会话目录"
        if not self.scanned_at or time.monotonic()-self.scanned_at > 4:
            self.scanned_at = time.monotonic()
            try:
                paths = discover(self.root)
                self.logs = {path: self.logs.get(path) or (RolloutReader(path, self.root), RolloutProjection(self.root))
                             for path in paths}
                self.published = {p: self.published[p] for p in paths if p in self.published}
                self.scan_error = ""
            except (OSError, ValueError):
                self.scan_error = "部分 Codex 日志无法扫描，请检查会话目录和访问权限"
        runs = []
        errors = bool(self.scan_error)
        for path, (reader, projection) in list(self.logs.items()):
            try:
                reset, records, caught_up = reader.poll()
                if reset:
                    projection = RolloutProjection(self.root)
                    self.published.pop(path, None)
                for record in records:
                    try:
                        projection.apply(record)
                    except (TypeError, ValueError, AttributeError, KeyError):
                        reader.errors += 1
                self.logs[path] = (reader, projection)
                errors = errors or bool(reader.errors)
                if not caught_up:
                    runs.extend(deepcopy(self.published.get(path, [])))
                    continue  # Never flash a historical in-progress record while replaying.
                current = []
                for run in projection.snapshot():
                    rid = run["run_id"]
                    active = run["status"] not in TERMINAL
                    recent = timestamp(run["updated_at"]) >= self.started
                    if not active and not recent and rid not in self.observed:
                        continue
                    # Old unclosed logs cannot prove a process is still alive.
                    if active and time.time()-timestamp(run["updated_at"]) > 600:
                        run["status"] = "stale"
                        run["message"] = "超过 10 分钟无日志更新，无法确认任务仍在运行"
                        if rid not in self.observed:
                            continue
                    self.observed.add(rid)
                    if not active and time.time()-timestamp(run.get("ended_at")) > 1200:
                        continue
                    current.append(run)
                self.published[path] = deepcopy(current)
                runs.extend(current)
            except (OSError, ValueError):
                errors = True
                for run in deepcopy(self.published.get(path, [])):
                    if run["run_id"] in self.observed and run["status"] not in TERMINAL:
                        run["connection_error"] = True
                        runs.append(run)
        # Keep the bookkeeping bounded even after many turns in a long session.
        self.observed.intersection_update(r["run_id"] for _, p in self.logs.values() for r in p.runs.values())
        return runs, "degraded" if errors else "ready", self.scan_error or (
            "部分日志记录无效，已跳过" if errors else "Codex 日志监控 · 审批请在 Codex 中处理")

    def poll(self):
        if self.closed:
            return {"runs": [], "actions": [], "status": "disabled"}
        logs, status, message = self._logs()
        merged = {(r["thread_id"], r["turn_id"]): r for r in logs}
        actions = []
        self._discover_clients()
        clients = self._clients()
        live_states = []
        for client in clients:
            live = client.poll()
            live_states.append(live)
            actions.extend(live["actions"])
            for run in live["runs"]:
                key = (run["thread_id"], run["turn_id"])
                log = merged.get(key)
                if log:
                    # Identity is shared, but a stale disconnected live snapshot
                    # must not hide a newer durable terminal event.
                    if run.get("connection_error") and log["status"] in TERMINAL:
                        merged[key] = log
                        continue
                    run["started_at"] = log["started_at"]
                    if not run["steps"]:
                        run["steps"] = log["steps"]
                    if not run["summary_preview"]:
                        run["summary_preview"] = log["summary_preview"]
                    run["codex"] = {**log["codex"], **run["codex"]}
                merged[key] = run
        if live_states:
            connected = any(client.ready for client in clients)
            status = "connected" if connected else status
            message = "已自动连接 Codex 实时服务 · 支持单次审批" if connected and self.discovery else live_states[0]["message"]
            if not connected:
                message += "；本机会话监控继续运行"
        elif self.discovery:
            message = (self.discovery.message or "已开启 Codex 自动监控 · 直接在 Codex 中开始任务即可") + "；桌面审批请在 Codex 中处理"
        ordered = sorted(merged.values(), key=lambda r: (r["status"] not in TERMINAL, timestamp(r["updated_at"])), reverse=True)[:32]
        for run in ordered:
            run["codex"]["connection_message"] = message
        visible = {r["run_id"] for r in ordered}
        return {"status": status, "message": message, "runs": ordered,
                "actions": [a for a in actions if a["run_id"] in visible], "session_count": len(self.logs),
                "read_only": not any(client.ready for client in clients)}

    def send(self, action, run_id, payload):
        if self.closed:
            raise ValueError("当前 Codex 为日志监控，请在 Codex 中审批")
        clients = self._clients()
        # Route by the original connection's approval identity, not only thread ID.
        for client in clients:
            if any(a.get("type") == action and a.get("run_id") == run_id and
                   a.get("confirmation_id") == payload.get("confirmation_id") for a in client.snapshot()["actions"]):
                return client.send(action, run_id, payload)
        raise ValueError("此审批已失效或没有实时连接，请在 Codex 中处理")

    def close(self):
        with self._clients_lock:
            self.closed = True
            clients = self._clients()
            self.auto_clients.clear()
        for client in clients:
            client.close()
