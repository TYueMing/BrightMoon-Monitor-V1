"""Stable identity and evidence of real activity, excluding transport polling."""
from .display_history import digest
from .task_presentation import timestamp


def task_identity(run):
    # A session may contain many independent turns. Prefer the provider's exact
    # turn/completion identity; textual timestamp formatting is not identity.
    generation = ("completion", str(run["completion_id"])) if run.get("completion_id") else (
        ("turn", str(run["turn_id"])) if run.get("turn_id") else ("start", timestamp(run.get("started_at"))))
    # An adapter may expose one logical task through several transport IDs
    # (e.g. a live Harness question before its durable log becomes available).
    task = run.get("monitor_task_id") or run["run_id"]
    return digest([run.get("app_id", "unknown"), run.get("monitor_scope", ""), task, generation])


def activity_evidence(run):
    if "monitor_activity_id" in run:
        # Adapters that own an execution marker also own its absence: an empty
        # marker means history hydration has not proved any current activity.
        # Do not fall back to a partially reloaded tool list on reconnect.
        return digest(["event", run["monitor_activity_id"]]) if run["monitor_activity_id"] else ""
    # Allowlisted execution facts. Never use hub revision, lease, connection
    # flags, summaries, cosmetic titles or updated_at alone as wake evidence.
    facts = {key: run[key] for key in ("task_id", "activity_id") if run.get(key)}
    for field in ("tasks", "steps", "tools", "activities"):
        rows = run.get(field)
        if isinstance(rows, list) and rows:
            facts[field] = sorted(digest({key: row[key] for key in
                ("id", "action_id", "call_id", "status", "exit_code") if key in row})
                for row in rows[:128] if isinstance(row, dict))
    usage = run.get("usage")
    if isinstance(usage, dict):
        facts["usage"] = {key: value for key, value in usage.items()
                          if isinstance(value, (int, float)) and not isinstance(value, bool)}
    return digest(facts)


def attention_identity(run, now):
    requests = []
    for field in ("pending_confirmations", "deepseek_interactions"):
        for item in run.get(field, []):
            if not isinstance(item, dict) or not item.get("id") or item.get("status", "pending") != "pending":
                continue
            if item.get("expires_at") and timestamp(item["expires_at"]) <= now:
                continue
            # Some adapters generate requested_at/revision at every poll.
            requests.append({key: item[key] for key in
                ("id", "kind", "tool_name", "arguments", "questions") if key in item})
    return digest(sorted(digest(item) for item in requests)) if requests else ""
