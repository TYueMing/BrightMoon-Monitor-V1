"""Source-neutral task state, timers and approval identity."""
from datetime import datetime


FINAL = {"completed", "partial", "blocked", "budget_exhausted", "failed", "cancelled", "interrupted"}


STATUS_TEXT = {
    "reminding": "待办提醒",
    "running": "执行中...", "waiting_model": "等待模型...",
    "waiting_confirmation": "待授权", "waiting_lock": "等待资源",
    "queued": "等待调度", "cancellation_requested": "正在停止...",
    "completed": "执行完成", "failed": "执行失败", "cancelled": "已停止",
    "stale": "监控失联", "error": "连接异常", "offline": "等待任务",
    "partial": "部分完成", "blocked": "执行受阻", "budget_exhausted": "预算已用尽",
    "paused": "已暂停", "waiting_input": "待补充信息", "interrupted": "已中断",
    "denied": "已拒绝", "expired": "已过期", "superseded": "已更新", "unknown": "待核对结果",
}


def selected_run(snapshot: dict, run_id: str = "") -> dict:
    runs = [r for r in snapshot.get("runs", []) if isinstance(r, dict)]
    target = run_id or snapshot.get("active_run_id")
    return next((r for r in runs if r.get("run_id") == target), runs[0] if runs else {})


def status_for(snapshot: dict, run: dict) -> str:
    if run.get("connection_error"):
        return "error"
    return str(run.get("status") or snapshot.get("status") or "offline")


def pending_confirmation(run: dict) -> dict:
    if run.get("connection_error") or run.get("status") != "waiting_confirmation":
        return {}
    from ..src.task_presentation import pending_approvals
    return next(iter(pending_approvals(run)), {})


def authorization_actions(snapshot: dict, run: dict) -> dict:
    """Bind labels to action types and the *displayed* run + confirmation."""
    pending = pending_confirmation(run)
    if not pending:
        return {}
    return {
        a["type"]: a for a in snapshot.get("actions", [])
        if isinstance(a, dict) and a.get("type") in {"approve_tool", "deny_tool"}
        and a.get("run_id") == run.get("run_id")
        and a.get("confirmation_id") == pending.get("id")
    }


def elapsed(run: dict) -> str:
    if run.get("remote_origin") and type(run.get("remote_elapsed_seconds")) is int:
        seconds=max(0,run["remote_elapsed_seconds"])
        return f"{seconds//60:02d}:{seconds%60:02d}"
    try:
        start = datetime.fromisoformat(str(run["started_at"]))
        end = datetime.fromisoformat(str(run.get("ended_at") or run["updated_at"])) if run.get("status") in FINAL else datetime.now(start.tzinfo)
        seconds = max(0, int((end - start).total_seconds()))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"
    except (KeyError, TypeError, ValueError):
        return "00:00"

