"""Merge OpenCode database snapshots with its live local HTTP state."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from .bridge import LIVE_STATUSES, OpencodeBridge
from .client import OpencodeClient

_PERMISSION_LABELS = {
    "bash": "执行命令", "edit": "修改文件", "write": "写入文件", "patch": "修改文件",
    "read": "读取文件", "glob": "搜索文件", "grep": "搜索内容",
    "webfetch": "访问网页", "websearch": "网络搜索",
    "external_directory": "访问工作区外文件", "doom_loop": "重复操作",
    "task": "启动子任务", "skill": "加载技能", "lsp": "LSP 查询",
}

_HIGH_RISK = {"bash", "edit", "write", "patch", "external_directory", "doom_loop"}
_LOW_RISK = {"read", "glob", "grep", "lsp"}


def _risk(permission: str) -> str:
    if permission in _HIGH_RISK:
        return "high"
    if permission in _LOW_RISK:
        return "low"
    return "medium"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _age_seconds(value: object) -> float | None:
    try:
        updated = datetime.fromisoformat(str(value))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _normalize_questions(value: object) -> list[dict]:
    result: list[dict] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        options = []
        for option in item.get("options") if isinstance(item.get("options"), list) else []:
            if not isinstance(option, dict) or not str(option.get("label") or "").strip():
                continue
            options.append({
                "label": str(option.get("label"))[:80],
                "description": str(option.get("description") or "")[:180],
            })
        result.append({
            "header": str(item.get("header") or "需要选择")[:40],
            "question": str(item.get("question") or "请选择下一步操作")[:300],
            "options": options[:12],
            "multiple": bool(item.get("multiple", False)),
            "custom": item.get("custom", True) is not False,
        })
    return result[:8]


def _model_candidates(value: object) -> list[str]:
    """Get comparable model ids from OpenCode's string or JSON model field."""
    result: list[str] = []
    raw = str(value or "").strip()
    if raw:
        result.append(raw)
        if "/" in raw:
            result.append(raw.split("/", 1)[1])
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        for key in ("modelID", "model", "id", "name"):
            item = str(parsed.get(key) or "").strip()
            if item:
                result.extend(_model_candidates(item))
    return list(dict.fromkeys(result))


def _context_limit(provider: dict, model: object) -> int:
    """Find provider.models[*].limit.context without assuming one API shape."""
    candidates = set(_model_candidates(model))
    found: list[int] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            model_id = str(value.get("id") or value.get("modelID") or value.get("name") or "")
            limit = value.get("limit")
            if model_id and (not candidates or model_id in candidates or model_id.split("/", 1)[-1] in candidates):
                if isinstance(limit, dict):
                    try:
                        number = int(float(limit.get("context") or 0))
                        if number > 0:
                            found.append(number)
                    except (TypeError, ValueError, OverflowError):
                        pass
            for key, child in value.items():
                if str(key) in candidates and isinstance(child, dict):
                    direct = child.get("limit")
                    if isinstance(direct, dict):
                        try:
                            number = int(float(direct.get("context") or 0))
                            if number > 0:
                                found.append(number)
                        except (TypeError, ValueError, OverflowError):
                            pass
                if key in {"models", "providers", "model", "data"} or isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(provider)
    return min(found) if found else 0


def _change_summary(items: object, fallback: dict | None = None) -> dict:
    """Aggregate /session/:id/diff into a small, privacy-safe summary."""
    files: list[dict] = []
    additions = deletions = 0
    if isinstance(items, dict):
        items = items.get("files") or items.get("diff") or []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("file") or item.get("path") or item.get("filename") or "")
        try:
            add = max(0, int(float(item.get("additions") or item.get("added") or 0)))
        except (TypeError, ValueError, OverflowError):
            add = 0
        try:
            delete = max(0, int(float(item.get("deletions") or item.get("removed") or 0)))
        except (TypeError, ValueError, OverflowError):
            delete = 0
        additions += add
        deletions += delete
        if path:
            files.append({"path": path[:180], "additions": add, "deletions": delete,
                          "status": str(item.get("status") or "modified")[:20]})
    if not files and isinstance(fallback, dict):
        return {**fallback, "items": list(fallback.get("items") or [])[:24]}
    return {"files": len(files), "additions": additions, "deletions": deletions,
            "diffs": len(files), "source": "session_diff", "items": files[:24]}


def _child_summary(items: object, fallback: dict | None = None) -> tuple[list[dict], dict]:
    children: list[dict] = []
    counts = {"total": 0, "running": 0, "completed": 0, "failed": 0}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        child_id = str(item.get("id") or item.get("run_id") or item.get("sessionID") or item.get("sessionId") or "")
        if not child_id:
            continue
        status = str(item.get("status") or item.get("state") or item.get("type") or "queued").casefold()
        if status in {"busy", "running", "working", "in_progress"}:
            normalized = "running"
        elif status in {"error", "failed", "failure"}:
            normalized = "failed"
        elif status in {"completed", "complete", "done", "success"}:
            normalized = "completed"
        else:
            normalized = "queued"
        children.append({"id": child_id[:120], "title": str(item.get("title") or item.get("name") or "")[:100],
                         "status": normalized})
        counts["total"] += 1
        if normalized in counts:
            counts[normalized] += 1
    if not children and fallback:
        counts.update({key: int(fallback.get(key) or 0) for key in counts})
    return children[:24], counts


def _active_directories(snapshot: dict, limit: int = 4) -> list[str]:
    """Return recent live project roots for OpenCode's scoped HTTP APIs."""
    runs = [run for run in snapshot.get("runs", []) if isinstance(run, dict)]
    active_id = str(snapshot.get("active_run_id") or "")
    ordered = sorted(runs, key=lambda run: str(run.get("run_id") or "") != active_id)
    result: list[str] = []
    for run in ordered:
        if run.get("status") not in LIVE_STATUSES:
            continue
        directory = str(run.get("project_id") or "").strip()
        if directory and directory not in result:
            result.append(directory)
        if len(result) >= limit:
            break
    return result


def _enrich_snapshot(snapshot: dict, client: OpencodeClient) -> dict:
    """Surface live pending permission requests (only known to the server).

    opencode keeps "ask"-mode tool authorizations (bash/edit/external_directory
    etc.) out of ``opencode.db``, so the bridge cannot see them.  The client
    polls ``GET /permission``; merge those requests into the matching run so the
    island renders the authorization panel with the real request id.
    """
    result = {**snapshot}
    result["runs"] = [dict(run) for run in snapshot.get("runs", []) if isinstance(run, dict)]

    status_reader = getattr(client, "session_status_snapshot", None)
    status_known, session_statuses = status_reader() if callable(status_reader) else (False, {})
    for run in result["runs"]:
        run_id = str(run.get("run_id") or "")
        server_state = session_statuses.get(run_id)
        state_type = str((server_state or {}).get("type") or "").casefold()
        if state_type == "busy":
            if run.get("status") not in {"running", "waiting_model"}:
                run["status"] = "running"
                run["phase"] = "running"
                run["message"] = "Agent 正在处理"
            run["ended_at"] = ""
        elif state_type == "retry":
            attempt = (server_state or {}).get("attempt")
            suffix = f"（第 {attempt} 次）" if isinstance(attempt, int) else ""
            run["status"] = "waiting_model"
            run["phase"] = "retry"
            run["message"] = f"模型请求重试中{suffix}"
            run["ended_at"] = ""
        if "compact" in state_type:
            run["status"] = "running"
            run["phase"] = "compress"
            run["agent"] = "compress_agent"
            run["message"] = "正在压缩上下文"
            run["compaction"] = {"status": "running", "active": True, "source": "session_status"}
            stages = [dict(item) for item in run.get("stages", []) if isinstance(item, dict)]
            stages = [item for item in stages if item.get("agent") != "compress"]
            stages.append({"agent": "compress", "status": "running"})
            run["stages"] = stages
            rows = [str(item) for item in run.get("stage_rows", []) if item]
            if "compress" not in rows:
                rows.append("compress")
            run["stage_rows"] = rows

    # Enrich only the active/recent live sessions.  The client caches these
    # read-only calls, keeping the 400ms UI poll inexpensive.
    metric_reader = getattr(client, "session_diff", None)
    child_reader = getattr(client, "session_children", None)
    provider_reader = getattr(client, "provider_snapshot", None)
    active_id = str(result.get("active_run_id") or "")
    metric_runs = [run for run in result["runs"]
                   if str(run.get("run_id") or "") == active_id or run.get("status") in LIVE_STATUSES][:8]
    for run in metric_runs:
        run_id = str(run.get("run_id") or "")
        directory = str(run.get("project_id") or "")
        if callable(metric_reader) and run_id:
            try:
                run["changes"] = _change_summary(metric_reader(run_id, directory), run.get("changes"))
            except Exception:
                pass
        if callable(child_reader) and run_id:
            try:
                children, counts = _child_summary(child_reader(run_id, directory), run.get("child_summary"))
                run["children"], run["child_summary"] = children, counts
            except Exception:
                pass
        usage = run.get("usage") if isinstance(run.get("usage"), dict) else {}
        used = int(usage.get("total") or 0)
        if callable(provider_reader) and used:
            try:
                limit = _context_limit(provider_reader(directory), run.get("model"))
            except Exception:
                limit = 0
            context = dict(run.get("context") or {})
            context["used_tokens"] = used
            context["limit_tokens"] = limit
            context["percent"] = round(used * 100 / limit, 1) if limit > 0 else None
            context["source"] = "observed" if limit > 0 else "estimated"
            run["context"] = context

    # The database already knows parent_id for sessions spawned by the task
    # tool.  Use it as a fallback when the HTTP children endpoint is absent.
    by_parent: dict[str, list[dict]] = {}
    for candidate in result["runs"]:
        parent = str(candidate.get("parent_id") or "")
        if parent:
            by_parent.setdefault(parent, []).append(candidate)
    for run in result["runs"]:
        if run.get("children"):
            continue
        children, counts = _child_summary(by_parent.get(str(run.get("run_id") or "")), run.get("child_summary"))
        if children:
            run["children"], run["child_summary"] = children, counts
    # /session/status contains active sessions; an absent session is idle.
    # Allow a short DB/API race before converting an unfinished persisted turn
    # into cancelled, which also prevents old interrupted sessions staying live.
    if status_known:
        active_server_ids = {
            str(session_id) for session_id, state in session_statuses.items()
            if str((state or {}).get("type") or "").casefold() != "idle"
        }
        for run in result["runs"]:
            run_id = str(run.get("run_id") or "")
            if run_id in active_server_ids or run.get("status") not in {
                "queued", "running", "waiting", "waiting_model", "waiting_lock",
                "cancellation_requested",
            }:
                continue
            age = _age_seconds(run.get("updated_at"))
            if age is not None and age >= 2.0:
                run["status"] = "cancelled"
                run["phase"] = "cancelled"
                run["message"] = "已停止"
                run["ended_at"] = run.get("updated_at") or _now_iso()

    pending = client.pending_permissions()
    question_reader = getattr(client, "pending_questions", None)
    pending_questions = question_reader() if callable(question_reader) else []

    by_session: dict[str, list[dict]] = {}
    for request in pending:
        session_id = str(request.get("sessionID") or request.get("sessionId") or "")
        if session_id:
            by_session.setdefault(session_id, []).append(request)

    questions_by_session: dict[str, list[dict]] = {}
    for request in pending_questions:
        session_id = str(request.get("sessionID") or request.get("sessionId") or "")
        if session_id:
            questions_by_session.setdefault(session_id, []).append(request)

    for run in result["runs"]:
        run_id = str(run.get("run_id"))
        requests = by_session.get(run_id, [])
        question_requests = questions_by_session.get(run_id, [])
        if not requests and not question_requests:
            continue
        confirmations = []
        for request in question_requests:
            request_id = str(request.get("id") or request.get("requestID") or "")
            questions = _normalize_questions(request.get("questions"))
            if not request_id or not questions:
                continue
            confirmations.append({
                "id": request_id,
                "kind": "question",
                "tool_name": questions[0]["header"],
                "questions": questions,
                "directory": str(request.get("_directory") or run.get("project_id") or ""),
                "status": "pending",
                "requested_at": _now_iso(),
            })
        for request in requests:
            permission = str(request.get("permission") or "tool")
            request_id = str(request.get("id") or request.get("requestID") or "")
            if not request_id:
                continue
            confirmations.append({
                "id": request_id,
                "kind": "permission",
                "tool_name": _PERMISSION_LABELS.get(permission, permission),
                "permission": permission,
                "arguments": (
                    request.get("metadata") if isinstance(request.get("metadata"), dict)
                    else {"patterns": request.get("patterns", [])}
                ),
                "reason": str(request.get("message") or request.get("reason") or "")[:180],
                "directory": str(request.get("_directory") or run.get("project_id") or ""),
                "risk_level": _risk(permission),
                "status": "pending",
                "requested_at": _now_iso(),
            })
        if not confirmations:
            continue
        run["status"] = "waiting_confirmation"
        run["phase"] = "waiting_confirmation"
        first = confirmations[0]
        run["message"] = (
            f"等待你的选择 · {first['tool_name']}"
            if first.get("kind") == "question"
            else f"等待工具确认 · {first['tool_name']}"
        )
        run["confirmations"] = confirmations
        run["pending_confirmations"] = confirmations

    active = OpencodeBridge._select_active(result["runs"])
    result["active_run_id"] = active.get("run_id", result.get("active_run_id", ""))
    result["status"] = active.get("status", result.get("status", "offline"))
    result["message"] = active.get("message", result.get("message", ""))
    result["timeline"] = active.get("timeline", [])
    result["active_runs"] = sum(
        run.get("status") in LIVE_STATUSES
        for run in result["runs"]
    )
    result["server_available"] = bool(getattr(client, "available", False))
    result["actions"] = (
        OpencodeBridge._build_actions(active) if result["server_available"] else []
    )
    return result


