"""Stable task/approval ordering shared by snapshot projection and UI."""
from datetime import datetime
import math
import time


def timestamp(value, fallback=0.0):
    try:
        # Python 3.10 does not accept the common UTC "Z" suffix directly.
        result = float(value) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        return result if math.isfinite(result) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def run_order(run):
    return (int(run.get("started_order") or 0), timestamp(run.get("started_at")), str(run.get("run_id", "")))


def pending_approvals(run, now=None):
    if run.get("status") != "waiting_confirmation":
        return []
    now = time.time() if now is None else now
    items = [item for item in run.get("pending_confirmations", [])
             if isinstance(item, dict) and item.get("id") and item.get("status") == "pending"
             and (not item.get("expires_at") or timestamp(item["expires_at"]) > now)]
    return sorted(items, key=lambda item: (timestamp(item.get("requested_at"), timestamp(run.get("started_at"))), str(item["id"])))


def approval_queue(runs, now=None):
    items = [(run, item) for run in runs for item in pending_approvals(run, now)]
    return sorted(items, key=lambda pair: (timestamp(pair[1].get("requested_at"), timestamp(pair[0].get("started_at"))), run_order(pair[0]), str(pair[1]["id"])))
