"""Project Harness turns/steps/tools without inventing a BrightMoon planner."""
from datetime import datetime, timezone
from copy import deepcopy
import hashlib
import json
import time


def iso(milliseconds):
    try:
        return datetime.fromtimestamp(float(milliseconds)/1000, timezone.utc).isoformat()
    except (ValueError, TypeError, OSError, OverflowError):
        return datetime.now(timezone.utc).isoformat()


def plain_text(message, limit=200):
    content = message.get("content", []) if isinstance(message, dict) else []
    if isinstance(content, str):
        return " ".join(content.split())[:limit]
    if not isinstance(content, list):
        return ""
    # Reasoning/tool-call blocks are not public task summaries.
    return " ".join(str(block.get("text", "")) for block in content
                    if isinstance(block, dict) and block.get("type") == "text")[:limit]


class SessionProjection:
    def __init__(self, header, source_key):
        self.header = header
        identity = source_key + "\0" + header["id"]
        self.prefix = "deepseek_harness:" + hashlib.sha256(identity.encode()).hexdigest()[:24]
        self.run = None
        self.tools = {}
        self.approvals = {}
        self.in_seed = bool(header.get("isSeeded"))

    def apply(self, event):
        kind, data = event["type"], event["data"]
        if kind == "session/end-seed":
            self.in_seed = False
            self.run, self.tools, self.approvals = None, {}, {}
            return
        if self.in_seed:
            return
        when = event.get("time", self.header.get("createdAt", 0))
        if kind == "turn/start":
            turn = data.get("turn")
            if not isinstance(turn, int) or isinstance(turn, bool) or turn < 0:
                return
            self.tools, self.approvals = {}, {}
            self.run = {
                "run_id": f"{self.prefix}:{turn}", "app_id": "deepseek_harness", "status": "running",
                "monitor_task_id": self.header["id"],
                "monitor_activity_id": "",
                "started_at": iso(when), "updated_at": iso(when), "turn_id": str(turn),
                "project_id": str(self.header.get("cwd") or "DeepSeek Harness"),
                "conversation_id": self.header["id"], "task_summary": "DeepSeek Harness 任务",
                "summary_preview": "", "message": "已接收任务", "agent": "Harness",
                "steps": [], "tools": [], "pending_confirmations": [], "confirmations": [],
                "monitor_read_only": True, "monitor_detail": {"turn": turn, "step": 0},
            }
            return
        if self.run is None:
            return
        run = self.run
        if "turn" in data and str(data["turn"]) != run["turn_id"]:
            return
        run["updated_at"] = iso(when)
        run["seq"] = event["seq"]
        if kind in {"step/start", "step/end", "assistant/message", "tool/call", "tool/result",
                    "compaction/start", "compaction/end", "llm/retry", "llm/retry-started", "approval/decided"}:
            # Harness sequence numbers belong to durable execution events;
            # title changes, reconnects and monitor polling cannot advance this.
            run["monitor_activity_id"] = str(event["seq"])
        if kind == "user/message":
            message = data.get("message", data)
            source = message.get("source", {}) if isinstance(message, dict) else {}
            if not source or source.get("kind") in {"human", "user"}:
                run["task_summary"] = plain_text(message, 100) or run["task_summary"]
        elif kind == "step/start":
            step = data.get("step", 0)
            run["monitor_detail"]["step"] = step
            run["steps"].append({"id": str(step), "label": f"模型轮次 {step}", "status": "running"})
            run["steps"] = run["steps"][-64:]
            run.update(status="waiting_model", agent="模型推理", message=f"第 {step} 轮：等待模型输出")
        elif kind == "assistant/message":
            run["summary_preview"] = plain_text(data.get("message", {}))
            run.update(status="running", agent="Harness", message="模型输出已落盘，继续执行")
            if isinstance(data.get("usage"), dict):
                run["monitor_detail"]["usage"] = data["usage"]
        elif kind == "compaction/start":
            run.update(agent="上下文压缩", message="Harness 正在压缩上下文")
        elif kind == "compaction/end":
            run.update(agent="Harness", message="上下文压缩结束，继续执行")
        elif kind in {"llm/retry", "llm/retry-started"}:
            run.update(status="waiting_model", agent="模型重试", message="Harness 正在重试模型请求")
        elif kind == "session/title" and isinstance(data.get("title"), str):
            run["task_summary"] = data["title"][:100]
        elif kind == "tool/call":
            call_id = str(data.get("callId", ""))
            raw = str(data.get("arguments", ""))
            try:
                args = json.loads(raw) if len(raw) <= 32768 else {"参数预览": raw[:2000], "说明": "完整参数请在 Harness 查看"}
            except ValueError:
                args = {"原始参数": raw[:2000]}
            item = {"id": call_id, "name": str(data.get("name", "工具"))[:100], "status": "running",
                    "step": data.get("step"), "arguments": args}
            self.tools[call_id] = item
            if len(self.tools) > 64:
                self.tools.pop(next(iter(self.tools)))
            run.update(status="running", agent="工具执行", message="执行 " + item["name"])
        elif kind == "tool/result":
            message = data.get("message", {})
            if not isinstance(message, dict):
                message = {}
            call_id = str(message.get("toolCallId") or (message.get("source") or {}).get("callId") or data.get("callId") or "")
            if call_id in self.tools:
                self.tools[call_id]["status"] = "failed" if message.get("isError") or data.get("failure") else "completed"
            run.update(agent="Harness", message="工具返回，等待后续步骤")
        elif kind == "approval/asked":
            key = str(data.get("id", ""))
            self.approvals[key] = {
                "id": key, "status": "pending", "tool_name": str(data.get("toolName", "工具"))[:100],
                "requested_at": iso(when), "revision": event["seq"], "reason": str(data.get("reason", ""))[:500],
                "arguments": deepcopy(self.tools.get(str(data.get("callId", "")), {}).get("arguments", {})),
                "read_only": True,
            }
        elif kind == "approval/decided":
            approval = self.approvals.get(str(data.get("id", "")))
            if approval:
                approval["status"] = {"allowed-once": "approved", "rejected": "denied"}.get(data.get("outcome"), "expired")
                run.update(status="running", agent="Harness", message="授权结果已同步")
        elif kind == "step/end":
            for step in run["steps"]:
                if step["id"] == str(data.get("step")):
                    step["status"] = "completed"
        elif kind == "turn/end":
            reason = data.get("reason", {})
            reason = reason if isinstance(reason, dict) else {"kind": reason}
            state = {"completed": "completed", "aborted": "cancelled", "error": "failed",
                     "blocked": "blocked", "max-tokens": "budget_exhausted",
                     "interrupted": "cancelled", "forked": "cancelled"}.get(reason.get("kind"), "partial")
            run.update(status=state, ended_at=iso(when), message="任务已结束", agent="Harness")
            error = reason.get("error")
            if isinstance(error, dict):
                run["error"] = str(error.get("message", "任务失败"))[:200]
            for item in (*run["steps"], *self.tools.values()):
                if item["status"] == "running":
                    item["status"] = state if state != "completed" else "partial"
            for item in self.approvals.values():
                if item["status"] == "pending":
                    item["status"] = "expired"
        run["tools"] = list(self.tools.values())
        run["confirmations"] = list(self.approvals.values())
        run["pending_confirmations"] = [a for a in self.approvals.values() if a["status"] == "pending"]
        if run["pending_confirmations"] and not run.get("ended_at"):
            run.update(status="waiting_confirmation", agent="待授权", message="请在 DeepSeek Harness 中处理授权")

    def snapshot(self, *, now=None):
        if not self.run:
            return None
        value = deepcopy(self.run)
        now = time.time() if now is None else now
        # A durable log cannot prove process liveness. Don't leave crash-orphaned
        # turns looking active forever; never manufacture a success/denial.
        updated = datetime.fromisoformat(value["updated_at"]).timestamp()
        if not value.get("ended_at") and now-updated > 300:
            value.update(status="stale", message="5 分钟未收到已落盘事件，请在 Harness 核对状态")
        return value
