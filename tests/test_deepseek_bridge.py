"""Source-local integration tests; run from the BrightMoon project root with -B."""
from concurrent.futures import Future
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import Mock, patch

import pytest

from monitor.deepseek_monitor.bridge_client import BridgeClient, BridgeError
from monitor.deepseek_monitor.interactions import merge_interactions
from monitor.deepseek_monitor.controls import InteractionControls
from monitor.UI.detail_panels import panel_for


def interaction(kind="question", **changes):
    return {"id": "event-one", "kind": kind, "mode": "live", "revision": "rev-one", "session_id": "session-one",
            "call_id": "call-one", "title": "测试任务", "project": "C:/fixture", "tool_name": "write_file",
            "reason": "写入测试文件", "arguments": {"path": "fixture.txt"}, "requested_at": "2026-10-13T08:00:00+00:00",
            "order": 1, "questions": [
                {"id": "q1", "question": "选择开发语言", "options": [{"label": "Python"}, {"label": "JavaScript"}]},
                {"id": "q2", "question": "选择需要的功能", "multiSelect": True, "options": [{"label": "授权"}, {"label": "答题"}]},
                {"id": "q3", "question": "补充说明"},
            ] if kind == "question" else [], **changes}


def bridge(*requests):
    return {"connected": True, "protocol": 1, "instance": "fixture-instance", "generation": 1,
            "control_key": "fixture-client", "requests": list(requests), "sessions": ["session-one"], "receipts": []}


def merged(*requests, logs=None):
    return merge_interactions(logs or {"status": "ready", "runs": [], "actions": [], "read_only": True}, bridge(*requests), "C:/fixture/sessions")


def test_live_request_appears_before_any_log_directory_exists():
    result = merged(interaction(), logs={"status": "missing", "runs": [], "actions": [], "read_only": True})
    assert result["status"] == "ready" and not result["read_only"]
    assert result["runs"][0]["status"] == "waiting_input"
    assert result["runs"][0]["deepseek_interactions"][0]["generation"] == 1


def test_host_clears_stale_logged_approval_and_preserves_unrelated_session():
    stale = {"run_id": "old", "conversation_id": "session-one", "status": "waiting_confirmation", "pending_confirmations": [{"id":"old-audit"}]}
    unrelated = {**stale, "run_id": "other", "conversation_id": "another-session"}
    logs = {"status": "ready", "runs": [stale, unrelated], "actions": []}
    result = merged(logs=logs)
    assert result["runs"][0]["status"] == "running" and not result["runs"][0]["pending_confirmations"]
    assert result["runs"][1]["status"] == "waiting_confirmation"
    assert logs["runs"][0]["status"] == "waiting_confirmation"


def test_continued_question_survives_completed_turn():
    result = merged(interaction(mode="continued"), logs={"runs": [{"run_id": "done", "conversation_id": "session-one",
                    "status": "completed", "ended_at": "2026-10-13T08:00:00+00:00"}], "actions": []})
    run = result["runs"][0]
    assert run["run_id"] == "done" and run["status"] == "waiting_input" and "ended_at" not in run
    assert run["monitor_runtime_status"] == "completed"


def test_offline_source_retains_read_only_behavior():
    logs = {"status": "ready", "runs": [], "actions": [], "read_only": True}
    result = merge_interactions(logs, {"connected":False,"requests":[]}, "C:/fixture")
    assert result["read_only"] and result["runs"] == []


def test_first_log_flush_does_not_replace_the_task_being_edited():
    identities = {}
    first = merge_interactions({"runs":[], "actions":[]}, bridge(interaction(turn=2)), "C:/fixture", identities)
    run_id = first["runs"][0]["run_id"]
    logs = {"runs":[{"run_id":"new-log-id", "conversation_id":"session-one", "turn_id":"2", "status":"running"}], "actions":[]}
    second = merge_interactions(logs, bridge(interaction(turn=2)), "C:/fixture", identities)
    assert second["runs"][0]["run_id"] == run_id
    offline = merge_interactions(logs, {"connected":False}, "C:/fixture", identities)
    assert offline["runs"][0]["run_id"] == run_id


def test_client_checks_descriptor_root_and_request_generation(tmp_path):
    root = tmp_path/"sessions"; root.mkdir()
    client = BridgeClient(root, runtime=tmp_path)
    descriptor = {"protocol":1,"port":12345,"token":"a"*64,"instance":"fixture-instance","sessions_root":str(root)}
    client.descriptor.write_text(json.dumps(descriptor), encoding="utf-8")
    assert client._connection()["port"] == 12345
    descriptor["sessions_root"] = str(tmp_path/"another")
    client.descriptor.write_text(json.dumps(descriptor), encoding="utf-8")
    with pytest.raises(BridgeError): client._connection()
    item = interaction(instance="fixture-instance", generation=1)
    client._state = bridge(interaction()); client._observed_at = time.monotonic()
    with patch.object(client, "_request", return_value=({"status":"submitted"}, {})) as send:
        assert client.submit(item,"answer",{"answers":[]}).result(timeout=2)["status"] == "submitted"
        assert send.call_count == 1
        with pytest.raises(BridgeError): client.submit({**item,"generation":0},"answer",{})
        client._observed_at -= 4
        with pytest.raises(BridgeError): client.submit(item,"answer",{})










def test_different_approval_requests_for_same_tool_are_not_locked_by_old_receipt():
    controls = InteractionControls()
    one=interaction("approval", instance="one")
    two={**one,"id":"second-request"}
    controls.receipts[controls.key(one)]={"status":"confirmed"}
    assert not controls.busy(two)
