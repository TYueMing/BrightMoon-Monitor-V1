"""Pure allowlist projection of the desktop's already-selected runs."""
import hashlib
import hmac
import json
import time
from .privacy import text, number
from ..src.task_identity import task_identity
from ..src.task_presentation import timestamp
from ..UI.common_model import FINAL, STATUS_TEXT

NAMES = {"codex": "Codex", "deepseek_harness": "DeepSeek Harness", "opencode": "OpenCode", "claude": "Claude Code", "solo_task": "待办任务"}


def rows(value, limit):
    return [v for v in value[:limit] if isinstance(v, dict)] if isinstance(value, list) else []


def mapping(value):
    return value if isinstance(value, dict) else {}


def state(value):
    if value in ("pending", "in_progress", "done"):
        return {"pending": "queued", "in_progress": "running", "done": "completed"}[value]
    return value if isinstance(value, str) and value in STATUS_TEXT else "unknown"


def chain(run, private):
    source = run.get("app_id")
    if source == "solo_task":
        return {"kind": "reminder", "label": "待办提醒 · 按电脑计划触发", "total": 0,
                "completed": None, "truncated": False, "nodes": []}
    values = rows(run.get("tasks") if source == "opencode" else run.get("steps"), 50)
    kind = "plan"
    if source in ("deepseek_harness", "claude"):
        kind = "observed"
    if source == "opencode" and not values:
        values, kind = rows(run.get("stages"), 50), "observed"
    names = {"plan": "执行计划", "observed": "已观察执行轨迹"}
    original = run.get("tasks") if source == "opencode" and kind == "plan" else run.get("steps") if source != "opencode" else run.get("stages")
    total = len(original) if isinstance(original, list) else 0
    return {"kind": kind, "label": names[kind] if values else "来源未报告执行计划",
            "total": total, "completed": sum(v.get("status") == "completed" for v in values) if total <= 50 and kind == "plan" else None,
            "truncated": total > 50,
            "nodes": [{"id": str(i), "label": text(v.get("name") or v.get("title") or v.get("content") or v.get("agent"), 120) if not private else f"步骤 {i+1}",
                       "status": state(v.get("status"))} for i, v in enumerate(values)]}


class PhoneProjector:
    def __init__(self, secret):
        self.secret = secret

    def key(self, run):
        return hmac.new(self.secret, task_identity(run).encode(), hashlib.sha256).hexdigest()

    def task(self, run, private, index):
        app_id = run.get("app_id") if run.get("app_id") in NAMES else "unknown"
        status = state(run.get("status"))
        final = status in FINAL
        summary = text(run.get("summary_preview") or run.get("error") or run.get("message") or run.get("task_summary")) if not private else ""
        blocks = []
        info = mapping(run.get("codex"))
        usage = mapping(info.get("usage"))
        if app_id == "codex":
            metrics = [("最近输入 tokens", usage.get("input")), ("上下文窗口", usage.get("window")),
                       ("会话累计 tokens（非当前上下文）", usage.get("total")), ("已观察压缩次数", info.get("compactions"))]
        elif app_id == "claude":
            ci = mapping(run.get("claude"))
            metrics = [("已观察事件", ci.get("events")), ("已观察压缩次数", ci.get("compactions")),
                       ("子 Agent 数量", ci.get("subagents"))]
        elif app_id == "opencode":
            context, changes = mapping(run.get("context")), mapping(run.get("changes"))
            children = mapping(run.get("child_summary"))
            metrics = [("上下文 tokens", context.get("used_tokens")), ("上下文占用 %（来源报告）", context.get("percent")),
                       ("变更文件数", changes.get("files")), ("新增行", changes.get("additions")), ("删除行", changes.get("deletions")),
                       ("子任务总数", children.get("total")), ("子任务已完成", children.get("completed"))]
        else:
            detail = mapping(run.get("monitor_detail"))
            metrics = [("任务轮次", detail.get("turn")), ("已观察模型调用轮次", detail.get("step"))]
        metrics = [{"label": k, "value": number(v)} for k, v in metrics if number(v) is not None]
        if metrics and not private:
            blocks.append({"id": "metrics", "type": "metric_list", "title": "来源报告的指标", "items": metrics})
        if not private:
            if app_id == "codex":
                blocks.append({"id": "source", "type": "text", "title": "模型与连接",
                               "text": "模型："+(text(info.get("model"), 100) or "来源未报告")+"\n"+
                               (text(info.get("connection_message"), 200) or "来源未报告连接方式")})
            if app_id == "opencode" and (mapping(run.get("compaction")).get("active") or mapping(run.get("compaction")).get("status") == "running"):
                blocks.append({"id": "compaction", "type": "text", "title": "上下文压缩", "text": "来源正在压缩上下文。"})
            tools = [{"name": text(v.get("name"), 120), "status": state(v.get("status")),
                      "detail": text(v.get("detail") or v.get("summary"), 500),
                      "exit_code": v.get("exit_code") if type(v.get("exit_code")) is int else None}
                     for v in rows(run.get("tools"), 20)]
            if tools:
                blocks.append({"id": "tools", "type": "tool_list", "title": "近期工具（最多 20 条）", "items": tools})
            if app_id == "solo_task":
                reminder = mapping(run.get("reminder"))
                task = mapping(reminder.get("task"))
                weekdays = task.get("weekdays") or []
                days = ["一二三四五六日"[day] for day in weekdays if type(day) is int and 0 <= day <= 6]
                cycle = f"每隔 {task['interval_hours']} 小时" if task.get("interval_hours") else "每周 "+"、".join(days) if days else "单次提醒"
                schedule = f"{cycle}\n起始时间：{task.get('start_date', '')} {task.get('time_of_day', '未报告')}\n提前 {task.get('lead_minutes', '未报告')} 分钟提醒"
                if number(reminder.get("cycle")):
                    schedule += "\n已延后再次提醒"
                blocks.append({"id": "schedule", "type": "text", "title": "提醒周期", "text": text(schedule, 600)})
        attention = None
        requests = rows(run.get("pending_confirmations"), 10) + rows(run.get("deepseek_interactions"), 10)
        requests = [v for v in requests if v.get("status", "pending") == "pending" and
                    (not v.get("expires_at") or timestamp(v["expires_at"]) > time.time())]
        requests.sort(key=lambda v: timestamp(v.get("requested_at"), timestamp(run.get("started_at"))))
        if status in ("waiting_confirmation", "waiting_input"):
            request = requests[0] if requests else {}
            # Original request parameters/IDs never leave this projection.
            request_key = hmac.new(self.secret, (self.key(run)+":"+str(request.get("id", status))).encode(), hashlib.sha256).hexdigest()
            attention = {"id": request_key, "requested_at_ms": timestamp(request.get("requested_at"), timestamp(run.get("started_at"))) * 1000,
                         "title": text(request.get("tool_name") or request.get("title") or "等待电脑处理", 120) if not private else "等待电脑处理",
                         "reason": text(request.get("reason"), 500) if not private else "",
                         "notice": "手机为只读监控，请在电脑端或来源应用中处理。参数未共享。"}
            if not private:
                questions = rows(request.get("questions"), 5)
                if questions:
                    blocks.append({"id": "questions", "type": "text", "title": "待回答问题（只读摘要）",
                                   "text": "\n".join(text(q.get("question") or q.get("prompt") or q.get("header") or q.get("title"), 200) for q in questions)})
        if run.get("connection_error"):
            attention = None
        return {"task_key": self.key(run), "app_id": app_id, "source": NAMES.get(app_id, "其他来源"),
                "title": f"{NAMES.get(app_id, '任务')} · {index+1}" if private else text(run.get("task_summary") or run.get("title") or NAMES.get(app_id, "任务"), 120),
                "status": status, "connection_state": "error" if run.get("connection_error") else "ready",
                "reminder_due_at_ms": timestamp(mapping(run.get("reminder")).get("due_at"))*1000 or None if app_id == "solo_task" else None,
                "timer": {"started_at_ms": timestamp(run.get("started_at"))*1000 or None,
                          "ended_at_ms": timestamp(run.get("ended_at") or run.get("updated_at"))*1000 if final else None,
                          "final": final, "basis": "observed" if info.get("time_basis") == "observed" else "reported"},
                "summary": summary, "chain": chain(run, private), "detail_blocks": blocks, "attention": attention}

    def project(self, snapshot, visible, focus, desktop_visible, policy):
        private = policy["privacy_mode"] == "status_only"
        tasks = []
        for i, run in enumerate(visible[:5]):
            try:
                tasks.append(self.task(run, private, i))
            except (ValueError, TypeError, KeyError, OverflowError):
                # A malformed provider must not stop other sources or the Tk poll.
                continue
        prefs = mapping(snapshot.get("preferences"))
        upcoming = mapping(snapshot.get("next_reminder"))
        idle_preview = None
        if upcoming and not private:
            task = mapping(upcoming.get("task"))
            idle_preview = {"title": text(task.get("title") or upcoming.get("title"), 120),
                            "body": text(task.get("body") or upcoming.get("body")),
                            "remind_at_ms": timestamp(upcoming.get("remind_at"))*1000}
        try:
            focus_key = next((self.key(r) for r in visible[:5] if r.get("run_id") == focus), "")
        except (ValueError, TypeError, KeyError):
            focus_key = ""
        health = mapping(snapshot.get("source_states"))
        color = prefs.get("monitor_background_color", "#141d2c")
        import re
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            color = "#141d2c"
        value = {"schema_version": 1, "mode": "viewer", "tasks": tasks, "desktop_focus_key": focus_key,
                 "desktop_visible": bool(desktop_visible), "privacy_mode": policy["privacy_mode"],
                 "idle": {"preview_enabled": bool(prefs.get("island_resident") and prefs.get("task_monitor_enabled", True)),
                          "upcoming_count": number(snapshot.get("upcoming_reminder_count")) or 0, "next_reminder": idle_preview,
                          "error": bool(snapshot.get("task_data_error"))},
                 "source_health": [{"app_id": k, "status": text(mapping(v).get("status"), 40)} for k, v in health.items() if k in NAMES],
                 "appearance": {"background_color": color, "background_alpha": min(1, max(0, (number(prefs.get("monitor_opacity")) or 0)/100)),
                                "panel_color": prefs.get("panel_color", "#ffffff") if re.fullmatch(r"#[0-9a-fA-F]{6}", str(prefs.get("panel_color", "#ffffff"))) else "#ffffff",
                                "panel_alpha": 1-min(100, number(prefs.get("panel_transparency")) or 0)/100}}
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        if len(encoded) > 256*1024:
            for task in tasks:
                task.update(detail_blocks=[], truncated=True)
        return value
