"""Overlay live interactions on durable progress without deriving commands from logs."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib


def merge_interactions(logs, bridge, root, identities=None):
    result = deepcopy(logs)
    identities = {} if identities is None else identities
    runs = result.setdefault("runs", [])
    for run in runs:
        identity_key = (run.get("conversation_id"), str(run.get("turn_id", run.get("monitor_detail", {}).get("turn", 0))))
        if identity_key in identities:
            run["run_id"] = identities[identity_key]
    result["bridge_connected"] = bool(bridge.get("connected"))
    result["bridge_message"] = bridge.get("message", "")
    if not bridge.get("connected"):
        return result
    result["read_only"] = False
    result["status"] = "ready" if result.get("status") in {"missing", "unreadable", "error"} else result.get("status", "ready")
    by_session = {run.get("conversation_id"): run for run in runs}
    sessions = set(bridge.get("sessions", []))
    # An online Host is authoritative for its live roots, even before it flushes
    # the current turn's logs. Old audit entries cannot recreate settled buttons.
    for run in runs:
        if run.get("conversation_id") in sessions and run.get("status") == "waiting_confirmation":
            run.update(status="running", pending_confirmations=[], message="等待 Harness 后续状态")
        run["deepseek_receipts"] = [r for r in bridge.get("receipts", []) if r.get("session_id") == run.get("conversation_id")]
    for request in bridge.get("requests", []):
        if not isinstance(request, dict) or request.get("kind") not in {"approval", "question"}:
            continue
        session_id = request["session_id"]
        run = by_session.get(session_id)
        identity_key = (session_id, str(request.get("turn", 0)))
        if run is None:
            identity = hashlib.sha256((str(root) + "\0" + session_id).encode()).hexdigest()[:24]
            when = request.get("requested_at") or datetime.now(timezone.utc).isoformat()
            run = {"run_id": f"deepseek_harness:live:{identity}", "app_id": "deepseek_harness",
                   "monitor_task_id": session_id,
                   "conversation_id": session_id, "project_id": request.get("project", ""),
                   "turn_id": str(request.get("turn", 0)),
                   "task_summary": request.get("title") or "DeepSeek Harness",
                   "started_at": when, "updated_at": when, "status": "running", "agent": "Harness",
                   "steps": [], "tools": [], "pending_confirmations": [], "confirmations": [],
                   "monitor_detail": {"turn": request.get("turn", 0), "step": 0}}
            by_session[session_id] = run
            runs.append(run)
        identities.setdefault(identity_key, run["run_id"])
        run["run_id"] = identities[identity_key]
        item = {**deepcopy(request), "control_key": bridge["control_key"], "instance": bridge["instance"],
                "generation": bridge["generation"]}
        run.setdefault("deepseek_interactions", []).append(item)
        run.update(connection_error=False, monitor_read_only=False)
    for run in runs:
        interactions = run.get("deepseek_interactions", [])
        if not interactions:
            continue
        run["monitor_runtime_status"] = run["status"]
        # Continue to show an answerable timed question after its original turn
        # ends. The source's next fresh poll restores durable execution status.
        run.pop("ended_at", None)
        first = interactions[0]
        if first["kind"] == "approval":
            run.update(status="waiting_confirmation", agent="待授权", message="可在灵动岛同意或拒绝")
            run["pending_confirmations"] = [{**a, "status": "pending", "read_only": False}
                                            for a in interactions if a["kind"] == "approval"]
        else:
            run.update(status="waiting_input", agent="待回答", message="可在灵动岛回答问题")
        run["deepseek_receipts"] = [r for r in bridge.get("receipts", []) if r.get("session_id") == run.get("conversation_id")]
    result["message"] = "已连接 Harness 实时交互"
    while len(identities) > 512:
        identities.pop(next(iter(identities)))
    return result
