"""Authenticated local bridge client. No Harness cookies or credentials are read."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
from threading import RLock
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener
from uuid import uuid4
from weakref import WeakValueDictionary
from ..src.paths import deepseek_runtime_root

RUNTIME = deepseek_runtime_root()
_CLIENTS = WeakValueDictionary()
_CLIENT_LOCK = RLock()
_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="deepseek-interaction")


class BridgeError(ValueError):
    def __init__(self, message, code="BRIDGE_ERROR"):
        super().__init__(message)
        self.code = code


class BridgeClient:
    def __init__(self, sessions_root, *, runtime=RUNTIME):
        self.root = Path(sessions_root).resolve()
        canonical = os.path.normcase(str(self.root))
        self.descriptor = Path(runtime) / (hashlib.sha256(canonical.encode()).hexdigest()[:24] + ".json")
        self.key = uuid4().hex
        self._lock = RLock()
        self._state = None
        self._observed_at = 0.0
        self._holds = {}
        self._opener = build_opener(ProxyHandler({}))
        with _CLIENT_LOCK:
            _CLIENTS[self.key] = self

    def _connection(self):
        try:
            if self.descriptor.stat().st_size > 8192:
                raise ValueError()
            value = json.loads(self.descriptor.read_text(encoding="utf-8"))
            if (value.get("protocol") != 1 or not isinstance(value.get("port"), int)
                    or not 1 <= value["port"] <= 65535
                    or not re.fullmatch(r"[a-f0-9]{64}", value.get("token", ""))
                    or os.path.normcase(str(Path(value.get("sessions_root", "")).resolve())) != os.path.normcase(str(self.root))):
                raise ValueError()
            return value
        except (OSError, ValueError, TypeError):
            raise BridgeError("交互桥接未连接；请安装桥接扩展并重启 Harness", "NOT_CONNECTED") from None

    def _request(self, route, payload):
        connection = self._connection()
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 512 * 1024:
            raise BridgeError("回答内容超过提交限制", "TOO_LARGE")
        request = Request(f"http://127.0.0.1:{connection['port']}/v1/{route}", data=encoded,
                          headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
        try:
            try:
                response = self._opener.open(request, timeout=2)
            except HTTPError as error:
                response = error
            with response:
                raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise BridgeError("交互状态过大，暂不可读取", "TOO_LARGE")
            value = json.loads(raw)
            if value.get("ok") is not True:
                error = value.get("error", {})
                raise BridgeError(str(error.get("message") or "Harness 未接受操作"), str(error.get("code") or "BRIDGE_ERROR"))
            return value["value"], connection
        except BridgeError:
            raise
        except Exception:
            raise BridgeError("交互连接中断，请等待重新同步", "DISCONNECTED") from None

    def poll(self):
        with self._lock:
            holds = [key for key, until in self._holds.items() if until > time.monotonic()]
            self._holds = {key: self._holds[key] for key in holds}
        try:
            state, connection = self._request("snapshot", {"client_id": self.key, "holds": holds})
            if state.get("protocol") != 1 or state.get("instance") != connection["instance"] or not isinstance(state.get("requests"), list):
                raise BridgeError("桥接协议不兼容", "PROTOCOL_MISMATCH")
            with self._lock:
                self._state, self._observed_at = deepcopy(state), time.monotonic()
            return {**state, "control_key": self.key}
        except BridgeError as error:
            with self._lock:
                self._state = None
            return {"connected": False, "requests": [], "receipts": [], "message": str(error), "control_key": self.key}

    def hold(self, request_id):
        with self._lock:
            self._holds[request_id] = time.monotonic() + 1.5

    def submit(self, interaction, action, answer=None):
        with self._lock:
            state = deepcopy(self._state)
            fresh = time.monotonic() - self._observed_at < 3
        if not state or not state.get("connected") or not fresh:
            raise BridgeError("连接已断开，请等待重新同步", "DISCONNECTED")
        live = next((r for r in state["requests"] if r["id"] == interaction["id"]), None)
        if (not live or live["revision"] != interaction["revision"] or live["session_id"] != interaction["session_id"]
                or interaction.get("instance") != state["instance"] or interaction.get("generation") != state["generation"]):
            raise BridgeError("请求已更新，请查看当前卡片", "STALE_REQUEST")
        command = {"command_id": uuid4().hex, "request_id": live["id"], "revision": live["revision"],
                   "session_id": live["session_id"], "instance": state["instance"], "generation": state["generation"], "action": action}
        if answer is not None:
            command["answer"] = deepcopy(answer)
        # No automatic retries: a timeout may occur after Harness accepted a decision.
        future = _WORKERS.submit(lambda: self._request("respond", command)[0])
        future.command_id = command["command_id"]
        return future


def client_for(interaction):
    with _CLIENT_LOCK:
        client = _CLIENTS.get(interaction.get("control_key"))
    if client is None:
        raise BridgeError("监控来源已关闭或切换", "DISCONNECTED")
    return client


def submit(interaction, action, answer=None):
    return client_for(interaction).submit(interaction, action, answer)


def hold(interaction):
    try:
        client_for(interaction).hold(interaction["id"])
    except BridgeError:
        pass
