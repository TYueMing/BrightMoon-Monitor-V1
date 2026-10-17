"""Allowlisted Codex facts. Never project reasoning, credentials or raw prompts."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json

from ..src.display_history import digest

TERMINAL = {"completed", "failed", "cancelled"}


def stamp(value=None):
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, timezone.utc).isoformat()
        if value:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
    except (ValueError, OverflowError, OSError):
        pass
    return datetime.now(timezone.utc).isoformat()


def text(value, limit=300):
    return str(value or "").replace("\x00", "")[:limit]


class Projection:
    def __init__(self, namespace):
        self.namespace = sha256(str(namespace).encode()).hexdigest()[:12]
        self.runs, self.threads, self.current = {}, {}, {}

    def thread(self, data):
        tid = str(data.get("id") or data.get("session_id") or "")
        if tid:
            self.threads[tid] = {k: text(data.get(k), 2048 if k == "cwd" else 100)
                                 for k in ("cwd", "model", "modelProvider")}
        return tid

    def ensure(self, thread_id, turn_id, when=None):
        if not thread_id or not turn_id:
            return None
        key = (str(thread_id), str(turn_id))
        if key not in self.runs:
            now = stamp(when)
            self.runs[key] = {"run_id": f"codex:{self.namespace}:{key[0]}:{key[1]}", "app_id": "codex",
                "thread_id": key[0], "turn_id": key[1], "status": "running", "agent": "Codex",
                "monitor_activity_id": "",
                "started_at": now, "updated_at": now, "message": "正在处理任务", "summary_preview": "",
                "steps": [], "tools": [], "pending_confirmations": [], "confirmations": [],
                "codex": {**self.threads.get(key[0], {}), "mode": "logs", "compactions": 0}}
            # Bound retained history independently of the shell's five-task limit.
            if len(self.runs) > 128:
                oldest = min(self.runs, key=lambda k: (self.runs[k]["status"] not in TERMINAL, self.runs[k]["updated_at"]))
                if oldest != key:
                    del self.runs[oldest]
        self.current[key[0]] = key[1]
        return self.runs[key]

    def find(self, params):
        tid = str(params.get("threadId") or "")
        turn = params.get("turnId") or self.current.get(tid)
        return self.runs.get((tid, str(turn))) if turn else None

    def finish(self, run, status, when=None, error=""):
        run.update(status=status, ended_at=stamp(when), updated_at=stamp(when))
        if error:
            run["error"] = text(error)
        run["message"] = {"completed": "任务完成", "failed": "执行失败", "cancelled": "已停止"}[status]
        for pending in run["pending_confirmations"]:
            pending["status"] = "superseded"
        run["pending_confirmations"] = []

    def item(self, run, item, completed=False):
        kind = item.get("type", "")
        if kind in {"reasoning", "userMessage"}:
            return
        if kind == "agentMessage":
            content = text(item.get("text"), 200)
            if content:
                run["message"] = content
                run["summary_preview"] = content
            return
        if kind in {"contextCompaction", "context_compaction"}:
            if completed:
                run["codex"]["compactions"] += 1
            return
        labels = {"commandExecution": "命令执行", "fileChange": "文件修改", "mcpToolCall": "MCP 工具",
                  "webSearch": "网页搜索", "collabAgentToolCall": "协作 Agent", "dynamicToolCall": "工具调用",
                  "function_call": "工具调用", "custom_tool_call": "工具调用"}
        if kind not in labels:
            return
        ident = str(item.get("id") or item.get("call_id") or "")
        if not ident:
            return
        existing = next((t for t in run["tools"] if t["id"] == ident), None)
        raw_status = item.get("status")
        state = "failed" if raw_status in {"failed", "declined"} else "completed" if completed else "running"
        detail = text(item.get("command") or item.get("tool") or item.get("name") or item.get("query"), 220)
        if kind == "fileChange":
            changes = item.get("changes") or []
            paths = [text(c.get("path"), 256) for c in changes[:30] if isinstance(c, dict)]
            detail = "、".join(paths)[:220]
        record = {"id": ident, "name": text(item.get("name") or labels[kind], 80), "type": kind,
                  "status": state, "detail": detail, "exit_code": item.get("exitCode")}
        if existing:
            existing.update({k: v for k, v in record.items() if v not in ("", None)})
        else:
            run["tools"].append(record)
            run["tools"] = run["tools"][-24:]
        if not completed:
            run["message"] = detail or labels[kind]

    def usage(self, run, info):
        last = info.get("last") or info.get("last_token_usage") or {}
        total = info.get("total") or info.get("total_token_usage") or {}
        def number(data, camel, snake):
            value = data.get(camel, data.get(snake))
            return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else None
        # Last prompt size is context usage; cumulative billing tokens are separate.
        run["codex"]["usage"] = {
            "input": number(last, "inputTokens", "input_tokens"),
            "total": number(total, "totalTokens", "total_tokens"),
            "window": number(info, "modelContextWindow", "model_context_window")}

    def event(self, method, params):
        if method == "thread/started":
            self.thread(params.get("thread") or {})
            return
        if method == "turn/started":
            turn = params.get("turn") or {}
            run = self.ensure(params.get("threadId"), turn.get("id"), turn.get("startedAt"))
            if run:
                run["codex"]["mode"] = "live"
                run["connection_error"] = False
                if turn.get("startedAt") is not None:
                    run["started_at"] = stamp(turn["startedAt"])
            return
        if method == "turn/completed":
            params = {**params, "turnId": (params.get("turn") or {}).get("id") or params.get("turnId")}
        run = self.find(params)
        if run is None:
            if not params.get("turnId"):
                return
            run = self.ensure(params.get("threadId"), params["turnId"])
        if not run:
            return
        run["codex"]["mode"] = "live"
        run["connection_error"] = False
        run["updated_at"] = stamp()
        if method in {"item/started", "item/completed", "turn/plan/updated", "thread/tokenUsage/updated",
                      "item/agentMessage/delta", "item/commandExecution/outputDelta"}:
            # Real notifications only: reconnect and history hydration do not
            # mint a new activity marker. Persist the digest, never event text.
            run["monitor_activity_id"] = digest([method, params])
        if method == "turn/completed":
            turn = params.get("turn") or {}
            state = {"completed": "completed", "failed": "failed", "interrupted": "cancelled"}.get(turn.get("status"))
            if state:
                self.finish(run, state, turn.get("completedAt"), (turn.get("error") or {}).get("message"))
        elif method in {"item/started", "item/completed"}:
            self.item(run, params.get("item") or {}, method == "item/completed")
        elif method == "turn/plan/updated":
            run["steps"] = [{"name": text(s.get("step"), 220), "status":
                {"pending": "pending", "inProgress": "running", "completed": "completed"}.get(s.get("status"), "pending")}
                for s in (params.get("plan") or [])[:50] if isinstance(s, dict)]
        elif method == "thread/tokenUsage/updated":
            self.usage(run, params.get("tokenUsage") or {})
        elif method == "thread/status/changed":
            flags = (params.get("status") or {}).get("activeFlags", [])
            if "waitingOnUserInput" in flags:
                run["message"] = "等待补充信息，请在 Codex 中回答"
            elif "waitingOnApproval" in flags and not run["pending_confirmations"]:
                run["message"] = "Codex 正在等待审批，等待请求详情同步"
        elif method == "error":
            run["message"] = text((params.get("error") or {}).get("message") or "Codex 报告错误，等待后续状态")

    def snapshot(self):
        return deepcopy(list(self.runs.values()))


class RolloutProjection(Projection):
    def __init__(self, namespace):
        super().__init__(namespace)
        self.thread_id = ""

    def apply(self, record):
        kind, p, when = record.get("type"), record.get("payload"), record.get("timestamp")
        if not isinstance(p, dict):
            return
        if kind == "session_meta":
            self.thread_id = self.thread(p)
            return
        tid = self.thread_id
        event = p.get("type") if kind == "event_msg" else kind
        turn = p.get("turn_id") or self.current.get(tid)
        if event in {"task_started", "turn_context"}:
            run = self.ensure(tid, turn, p.get("started_at") or when)
        else:
            run = self.runs.get((tid, str(turn)))
        if run is None and p.get("turn_id") and event in {"item_completed", "token_usage_record", "task_complete", "turn_aborted", "task_failed"}:
            # A long rollout may begin before our bounded initial tail. Recover
            # identity only from a real turn id, never guess it from tool names.
            run = self.ensure(tid, p["turn_id"], p.get("started_at") or when)
            if run:
                run["codex"]["time_basis"] = "observed" if not p.get("started_at") else "reported"
        if run is None:
            return
        run["updated_at"] = stamp(when)
        if event in {"token_count", "token_usage_record", "context_compacted", "compacted", "agent_message",
                     "item_completed", "exec_command_begin", "exec_command_end"} or (
                event == "response_item" and p.get("type") in {
                    "function_call", "custom_tool_call", "function_call_output", "custom_tool_call_output", "message"}
                and p.get("role", "assistant") == "assistant" and p.get("channel") != "analysis"):
            # Replay yields the exact same marker, unlike a poll timestamp.
            run["monitor_activity_id"] = digest([event, when, p])
        if event == "turn_context":
            run["codex"].update({k: text(p[k], 2048) for k in ("cwd", "model") if k in p})
        elif event == "task_complete":
            run["summary_preview"] = text(p.get("last_agent_message") or run["summary_preview"], 200)
            self.finish(run, "completed", p.get("completed_at") or when)
        elif event in {"turn_aborted", "task_failed"}:
            self.finish(run, "cancelled" if event == "turn_aborted" else "failed", when, p.get("message"))
        elif event == "token_count":
            self.usage(run, p.get("info") or {})
        elif event in {"context_compacted", "compacted"}:
            run["codex"]["compactions"] += 1
        elif event == "agent_message":
            run["message"] = text(p.get("message"), 200)
            run["summary_preview"] = run["message"]
        elif event == "item_completed":
            self.item(run, p.get("item") or {}, True)
        elif event == "response_item":
            item_type = p.get("type")
            if item_type in {"function_call", "custom_tool_call"}:
                self.item(run, p)
                # Only the public plan tool's schema is interpreted; other raw
                # tool arguments may include secrets and are not copied to UI.
                if p.get("name", "").split(".")[-1] == "update_plan":
                    try:
                        args = json.loads(p.get("arguments", "{}"))
                        run["steps"] = [{"name": text(s.get("step")), "status":
                            {"in_progress": "running", "completed": "completed"}.get(s.get("status"), "pending")}
                            for s in args.get("plan", [])[:50] if isinstance(s, dict)]
                    except (ValueError, TypeError, AttributeError):
                        pass
            elif item_type in {"function_call_output", "custom_tool_call_output"}:
                tool = next((t for t in run["tools"] if t["id"] == p.get("call_id")), None)
                if tool:
                    # A returned tool payload alone does not prove success.
                    tool["status"] = "unknown"
                    tool["detail"] = "已返回；日志未提供结构化执行状态"
            elif item_type == "message" and p.get("role") == "assistant" and p.get("channel") != "analysis":
                content = "".join(text(c.get("text"), 200) for c in p.get("content", []) if isinstance(c, dict))
                if content:
                    run["message"] = content[:200]
                    if p.get("phase") == "final_answer" or p.get("channel") == "final":
                        run["summary_preview"] = content[:200]
        elif event in {"exec_command_begin", "exec_command_end"}:
            self.item(run, {"type": "commandExecution", "id": p.get("call_id"),
                "command": text(p.get("command")), "exitCode": p.get("exit_code"),
                "status": "failed" if p.get("exit_code") not in (None, 0) else "completed"}, event.endswith("end"))
        elif event in {"exec_approval_request", "apply_patch_approval_request"}:
            run["message"] = "等待授权，请在 Codex 中处理（日志模式）"
