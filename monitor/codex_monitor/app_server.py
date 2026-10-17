"""Local Codex App Server JSON-RPC client. Network work stays off the Tk thread.

Approvals are replies to a server request on its originating socket, never a
command injected into Codex's terminal. A reconnect creates a new authority
generation; old queued decisions cannot be replayed on the new connection.
"""
from collections import deque
from copy import deepcopy
from hashlib import sha256
import json
from threading import RLock
import time
from uuid import uuid4

from ..src.source_config import codex_endpoint
from .projection import Projection, TERMINAL, stamp, text

APPROVAL_METHODS = {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}


class AppServerClient:
    def __init__(self, endpoint, namespace, *, connector=None):
        self.endpoint = codex_endpoint(endpoint)
        self.projection = Projection(namespace)
        self.lock = RLock()
        self.connector = connector
        self.socket = None
        self.closed = False
        self.ready = False
        self.generation = ""
        self.status, self.message = "connecting", "正在连接 Codex App Server"
        self.retry_at, self.failures = 0.0, 0
        self.pending, self.rpc, self.subscribed, self.subscribing = {}, {}, set(), set()
        self.queue = deque()
        self.items = {}
        self.next_list = 0.0

    def _write(self, message):
        self.socket.send(json.dumps(message, ensure_ascii=False, separators=(",", ":")))

    def _request(self, method, params, context=None):
        ident = "monitor-solo:"+uuid4().hex
        self.rpc[ident] = (method, context, time.monotonic())
        self._write({"id": ident, "method": method, "params": params})

    def _connect(self):
        if self.connector:
            socket = self.connector(self.endpoint)
        else:
            import websocket
            socket = websocket.create_connection(self.endpoint, timeout=1, suppress_origin=True,
                http_proxy_host=None, http_no_proxy=["localhost", "127.0.0.1", "::1"])
        socket.settimeout(.02)
        with self.lock:
            if self.closed:
                socket.close()
                return
            self.socket = socket
            self.generation = uuid4().hex
            self._request("initialize", {"clientInfo": {"name": "monitor_solo", "title": "Bright Moon Monitor", "version": "1.0.0"},
                                         # Receive additionalPermissions instead of
                                         # the server stripping them from approvals.
                                         # This exposes metadata, not extra authority.
                                         "capabilities": {"experimentalApi": True}})

    def _disconnect(self, message):
        with self.lock:
            socket, self.socket = self.socket, None
            self.ready = False
            self.status, self.message = "disconnected", message
            self.rpc.clear()
            self.queue.clear()
            self.subscribed.clear()
            self.subscribing.clear()
            self.pending.clear()
            self.items.clear()
            for run in self.projection.runs.values():
                if run["status"] not in TERMINAL:
                    run["connection_error"] = True
                for p in run["pending_confirmations"]:
                    p["status"] = "expired"
                run["pending_confirmations"] = []
            self.failures += 1
            self.retry_at = time.monotonic()+min(15, 2 ** min(self.failures, 4))
        if socket:
            try:
                socket.close(timeout=.1)
            except Exception:
                pass

    def _subscribe(self, tid):
        if not isinstance(tid, str) or not tid or tid in self.subscribed or tid in self.subscribing:
            return
        if len(self.subscribed)+len(self.subscribing) >= 64:
            return
        self.subscribing.add(tid)
        # Resume an ALREADY LOADED thread only, without overriding permissions,
        # model, cwd, instructions or creating a turn. This is the official
        # subscription mechanism; thread/read alone does not subscribe.
        self._request("thread/resume", {"threadId": tid, "excludeTurns": True}, tid)

    def _response(self, message):
        entry = self.rpc.pop(message.get("id"), None)
        if not entry:
            return
        method, context, _ = entry
        if "error" in message:
            if method == "initialize":
                raise ValueError("初始化失败")
            if method == "thread/resume":
                self.subscribing.discard(context)
            self.message = "部分会话未能订阅；将自动重试，请检查 Codex 版本"
            return
        result = message.get("result") or {}
        if method == "initialize":
            self._write({"method": "initialized", "params": {}})
            self.ready = True
            self.status, self.message = "connected", "App Server 已连接 · 支持单次审批"
            self.failures = 0
            self.next_list = 0
        elif method == "thread/loaded/list":
            for tid in result.get("data", [])[:64]:
                self._subscribe(tid)
            cursor = result.get("nextCursor")
            if cursor and (context or 0) < 4 and len(self.subscribed)+len(self.subscribing) < 64:
                self._request("thread/loaded/list", {"limit": 32, "cursor": cursor}, (context or 0)+1)
        elif method == "thread/resume":
            self.subscribing.discard(context)
            self.subscribed.add(context)
            self.message = "App Server 已连接 · 支持单次审批"
            metadata = {**(result.get("thread") or {}), "model": result.get("model", "")}
            self.projection.thread(metadata)
            for run in self.projection.runs.values():
                if run["thread_id"] == context:
                    run["codex"].update(self.projection.threads.get(context, {}))
            # Bounded hydration for attaching during an existing turn. Older
            # servers may reject this method; subsequent live events still work.
            self._request("thread/turns/list", {"threadId": context, "limit": 1, "sortDirection": "desc"}, context)
        elif method == "thread/turns/list":
            for turn in result.get("data", [])[:1]:
                existing = self.projection.runs.get((str(context), str(turn.get("id"))))
                if turn.get("status") != "inProgress" and not existing:
                    continue
                run = self.projection.ensure(context, turn.get("id"), turn.get("startedAt"))
                if run:
                    if turn.get("startedAt") is not None:
                        run["started_at"] = stamp(turn["startedAt"])
                    run["codex"]["mode"] = "live"
                    run["connection_error"] = False
                    if not run["pending_confirmations"] and run["status"] not in TERMINAL:
                        run["status"] = "running"
                    for item in turn.get("items", [])[:100]:
                        self.projection.item(run, item, item.get("status") in {"completed", "failed", "declined"})
                    if turn.get("status") in {"completed", "failed", "interrupted"} and run["status"] not in TERMINAL:
                        self.projection.event("turn/completed", {"threadId": context, "turn": turn})
                        for record in list(self.pending.values()):
                            if record["run_id"] == run["run_id"]:
                                self._resolved(record["request_id"])
        elif method == "thread/items/list":
            tid, turn_id = context
            for entry in result.get("data", [])[:32]:
                item = entry.get("item") or {}
                changes = item.get("changes")
                if item.get("type") != "fileChange" or not changes or len(json.dumps(changes, ensure_ascii=False)) > 65536:
                    continue
                for record in self.pending.values():
                    if (record.get("hydrate") == (tid, turn_id) and record["item_id"] == item.get("id")
                            and not record["queued"] and not record["sent"]):
                        approval = record["approval"]
                        approval["arguments"].pop("说明", None)
                        approval["arguments"]["文件变更"] = deepcopy(changes)
                        approval["revision"] += 1
                        approval["controllable"] = True
                        record["choices"] = ["accept", "decline"]
                        record["hydrate"] = None

    def _approval(self, message):
        request_id, params, method = message["id"], message.get("params") or {}, message["method"]
        if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
            return
        tid, turn_id = params.get("threadId"), params.get("turnId")
        if not tid or not turn_id:
            return
        run = self.projection.ensure(tid, turn_id)
        if run["status"] in TERMINAL:
            return
        key = (type(request_id).__name__, request_id)
        old = self.pending.get(key)
        encoded = json.dumps(params, sort_keys=True, ensure_ascii=False).encode("utf-8")
        signature = sha256(encoded).hexdigest()
        if old and old["signature"] == signature:
            return
        if old:
            self._resolved(request_id)
        elif len(self.pending) >= 128:
            self.message = "待处理交互过多；新增请求请在 Codex 原界面处理"
            return
        ident = f"codex:{self.generation}:{uuid4().hex}"
        supported = (method in APPROVAL_METHODS and not params.get("additionalPermissions")
                     and params.get("kind", "command") == "command")
        choices = params.get("availableDecisions")
        choices = ["accept", "decline"] if choices is None else choices
        choices = [x for x in choices if isinstance(x, str) and x in {"accept", "decline", "cancel"}]
        if "decline" in choices and "cancel" in choices:
            choices.remove("cancel")  # Prefer rejecting just this operation.
        # Preserve full actionable parameters. Never silently approve a
        # truncated command, or an experimental permission-grant request.
        oversized = len(encoded) > 65536
        arguments = {k: deepcopy(params[k]) for k in ("command", "cwd", "reason", "grantRoot", "networkApprovalContext",
                      "additionalPermissions", "permissionProfile", "commandActions", "availableDecisions", "environmentId", "kind") if k in params}
        item = self.items.get((str(tid), str(turn_id), str(params.get("itemId"))), {})
        hydrate = None
        if method == "item/fileChange/requestApproval":
            changes = item.get("changes")
            if changes and len(json.dumps(changes, ensure_ascii=False)) <= 65536:
                arguments["文件变更"] = deepcopy(changes)
            else:
                may_hydrate = supported and not oversized
                supported = False
                arguments["说明"] = "尚未收到完整文件变更，请在 Codex 原界面核对"
                if may_hydrate:
                    hydrate = (str(tid), str(turn_id))
        elif method == "item/commandExecution/requestApproval" and not arguments.get("command") and not arguments.get("networkApprovalContext"):
            supported = False
            arguments["说明"] = "尚未收到命令或网络访问详情，请在 Codex 原界面核对"
        if oversized:
            arguments = {"说明": "授权详情超过显示上限，请在 Codex 原界面核对并授权"}
            supported = False
        tool_name = "命令执行" if method.startswith("item/commandExecution/") else "文件修改" if method.startswith("item/fileChange/") else "Codex 交互请求"
        approval = {"id": ident, "revision": 1, "status": "pending", "tool_name": tool_name,
                    "requested_at": stamp(params.get("startedAtMs")), "arguments": arguments,
                    "reason": text(params.get("reason") or "仅确认本次操作"), "controllable": supported and bool(choices)}
        self.pending[key] = {"request_id": request_id, "run_id": run["run_id"], "approval": approval,
            "choices": choices if supported else [], "generation": self.generation, "signature": signature,
            "sent": False, "queued": False, "item_id": params.get("itemId"), "hydrate": hydrate}
        run.update(status="waiting_confirmation", connection_error=False, updated_at=stamp())
        run["codex"]["mode"] = "live"
        run["pending_confirmations"].append(approval)
        run["confirmations"].append(approval)
        run["confirmations"] = run["confirmations"][-32:]
        if hydrate:
            self._request("thread/items/list", {"threadId": tid, "turnId": turn_id, "limit": 32, "sortDirection": "desc"}, hydrate)

    def _resolved(self, request_id, thread_id=None):
        key = (type(request_id).__name__, request_id)
        record = self.pending.get(key)
        if not record:
            return
        run = next((r for r in self.projection.runs.values() if r["run_id"] == record["run_id"]), None)
        if thread_id and run and run["thread_id"] != thread_id:
            return
        self.pending.pop(key, None)
        # Another Codex client may have won the race. Resolved does not report
        # which decision won, so never manufacture 'approved' from our click.
        record["approval"]["status"] = "superseded"
        if run:
            run["pending_confirmations"] = [p for p in run["pending_confirmations"] if p["status"] == "pending"]
            if not run["pending_confirmations"] and run["status"] == "waiting_confirmation":
                run["status"] = "running"
                run["message"] = "审批请求已处理，等待执行结果"

    def handle(self, message):
        """Protocol boundary shared by the socket and deterministic tests."""
        if not isinstance(message, dict):
            return
        with self.lock:
            method, params = message.get("method"), message.get("params") or {}
            if not method:
                return self._response(message)
            if not isinstance(params, dict):
                return
            if "id" in message:
                return self._approval(message)
            if method == "serverRequest/resolved":
                return self._resolved(params.get("requestId"), params.get("threadId"))
            if method == "thread/closed":
                tid = params.get("threadId")
                self.subscribed.discard(tid)
                for record in list(self.pending.values()):
                    self._resolved(record["request_id"], tid)
                for run in self.projection.runs.values():
                    if run["thread_id"] == tid and run["status"] not in TERMINAL:
                        run["connection_error"] = True
                return
            if method in {"item/started", "item/completed"}:
                item = params.get("item") or {}
                if item.get("type") == "fileChange" and len(json.dumps(item, ensure_ascii=False)) <= 70000:
                    self.items[(str(params.get("threadId")), str(params.get("turnId")), str(item.get("id")))] = deepcopy(item)
                    if len(self.items) > 64:
                        self.items.pop(next(iter(self.items)))
            self.projection.event(method, params)
            if method == "turn/completed":
                turn_id = (params.get("turn") or {}).get("id")
                for record in list(self.pending.values()):
                    run = next((r for r in self.projection.runs.values() if r["run_id"] == record["run_id"]), {})
                    if run.get("thread_id") == params.get("threadId") and run.get("turn_id") == turn_id:
                        self._resolved(record["request_id"])

    def send(self, action, run_id, payload):
        with self.lock:
            if self.closed or not self.ready or not self.socket:
                raise ValueError("Codex 实时连接已断开")
            record = next((p for p in self.pending.values() if p["run_id"] == run_id
                and p["approval"]["id"] == payload.get("confirmation_id")
                and p["approval"]["revision"] == payload.get("expected_revision")), None)
            decision = None
            if action == "approve_tool":
                decision = "accept"
            elif action == "deny_tool" and record:
                decision = "decline" if "decline" in record["choices"] else "cancel"
            if not record or decision not in record["choices"] or record["sent"] or record["queued"]:
                raise ValueError("此审批已失效或已发送，请等待 Codex 更新")
            record["queued"] = True
            command_id = uuid4().hex
            self.queue.append((record["generation"], record["request_id"], record["approval"]["id"], decision, command_id))
            return command_id

    def _flush(self):
        with self.lock:
            while self.queue:
                generation, request_id, ident, decision, _ = self.queue.popleft()
                record = self.pending.get((type(request_id).__name__, request_id))
                if generation != self.generation or not record or record["approval"]["id"] != ident or record["sent"]:
                    continue
                record["sent"] = True  # A partial write is uncertain: never replay it.
                self._write({"id": request_id, "result": {"decision": decision}})

    def poll(self):
        if self.closed:
            return self.snapshot()
        try:
            if not self.socket and time.monotonic() >= self.retry_at:
                self._connect()
            if self.socket:
                # Drain resolutions before sending queued decisions, reducing
                # races when the original Codex client has already answered.
                deadline = time.monotonic()+.15
                for _ in range(200):
                    if time.monotonic() > deadline:
                        break
                    try:
                        raw = self.socket.recv()
                    except TimeoutError:
                        break
                    except Exception as exc:
                        if type(exc).__name__ == "WebSocketTimeoutException":
                            break
                        raise
                    if not raw:
                        raise ConnectionError("closed")
                    if len(raw) > 4*1024*1024:
                        raise ValueError("message exceeds limit")
                    self.handle(json.loads(raw))
                self._flush()
                if self.ready and time.monotonic() >= self.next_list:
                    self.next_list = time.monotonic()+4
                    self._request("thread/loaded/list", {"limit": 32}, 0)
                if any(time.monotonic()-entry[2] > 12 for entry in self.rpc.values()):
                    raise TimeoutError("RPC timeout")
        except ImportError:
            self._disconnect("缺少 websocket-client，请安装运行依赖")
        except Exception:
            self._disconnect("Codex App Server 未连接；请检查地址、服务版本及本机访问认证")
        return self.snapshot()

    def snapshot(self):
        with self.lock:
            actions = []
            if self.ready and self.socket and not self.closed:
                for p in self.pending.values():
                    if p["sent"] or p["queued"]:
                        continue
                    for choice in p["choices"]:
                        action = "approve_tool" if choice == "accept" else "deny_tool"
                        actions.append({"id": f"{action}:{p['approval']['id']}", "type": action,
                            "run_id": p["run_id"], "confirmation_id": p["approval"]["id"], "expected_revision": p["approval"]["revision"],
                            "codex_decision": choice, "label": "同意一次" if choice == "accept" else "拒绝并停止本轮" if choice == "cancel" else "拒绝"})
            return {"runs": self.projection.snapshot(), "actions": actions, "status": self.status,
                    "message": self.message, "read_only": not self.ready}

    def close(self):
        self.closed = True
        # Closing a subscriber never sends turn/interrupt or shuts down Codex.
        self._disconnect("Codex 监控已关闭")
