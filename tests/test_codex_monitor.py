"""Codex boundary, incremental replay, approval race and lifecycle regressions."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from monitor.codex_monitor.adapter import CodexSource
from monitor.codex_monitor.app_server import AppServerClient
from monitor.codex_monitor.log_reader import RolloutReader, discover
from monitor.codex_monitor.projection import Projection, RolloutProjection, stamp
from monitor.src.source_config import normalize_sources, codex_endpoint, codex_session_root, inspect_source
from monitor.src.source_hub import SourceHub


class Socket:
    def __init__(self):
        self.sent, self.incoming, self.closed = [], [], False
    def send(self, payload):
        self.sent.append(json.loads(payload))
    def recv(self):
        if self.incoming:
            return json.dumps(self.incoming.pop(0))
        raise TimeoutError()
    def settimeout(self, value): pass
    def close(self, **kwargs): self.closed = True


def connected():
    socket = Socket()
    client = AppServerClient("ws://127.0.0.1:4500", "fixture", connector=lambda _: socket)
    client.poll()
    initialize = socket.sent[0]
    assert initialize["params"]["capabilities"]["experimentalApi"] is True
    client.handle({"id": initialize["id"], "result": {"userAgent": "fixture"}})
    return client, socket


def approval(ident=7, tid="thread", turn="turn", **extra):
    return {"id": ident, "method": "item/commandExecution/requestApproval", "params": {
        "threadId": tid, "turnId": turn, "itemId": "command-1", "command": "python -m pytest",
        "cwd": "C:/Fixture Project", "reason": "运行测试", **extra}}


def choose(client, action="approve_tool", run_index=0):
    snapshot = client.snapshot()
    run = snapshot["runs"][run_index]
    target = next(a for a in snapshot["actions"] if a["type"] == action and a["run_id"] == run["run_id"])
    return target, {"confirmation_id": target["confirmation_id"], "expected_revision": target["expected_revision"]}


@pytest.mark.parametrize("url", ["ws://remote.example:4500", "wss://127.0.0.1:4500", "ws://127.0.0.1", "ws://u:p@localhost:4500",
    "ws://localhost:4500/?token=secret", "ws://127.0.0.1:4500/path", "ws://127.0.0.1:99999", "ws://127.0.0.1:4500/#x"])
def test_endpoint_refuses_remote_credentials_and_ambiguous_urls(url):
    with pytest.raises(ValueError): codex_endpoint(url)


def test_defaults_paths_placeholders_and_diagnostics(tmp_path):
    defaults = normalize_sources()
    assert not any(v["enabled"] for v in defaults.values())
    for key in ("codeagent",):
        with pytest.raises(ValueError): normalize_sources({key: {"enabled": True}}, strict=True)
        assert inspect_source(key, {})["status"] == "unsupported"
    old = normalize_sources({"opencodex": {"app_path": "C:/App", "data_path": "C:/Data"}})
    assert old["codex"]["data_path"] == "" and not old["codex"]["enabled"]
    root = tmp_path/"sessions"; root.mkdir()
    assert codex_session_root({"data_path": str(tmp_path)}) == root
    assert inspect_source("codex", {"data_path": str(root)})["read_only"]
    assert inspect_source("codex", {"server_url": "ws://localhost:4500"})["status"] == "configured"
    assert codex_endpoint("ws://[::1]:4500/") == "ws://[::1]:4500"


def record(kind, payload, when=None):
    return {"type": kind, "timestamp": when or stamp(), "payload": payload}


def lines(*values):
    return b"".join((json.dumps(v, ensure_ascii=False)+"\n").encode("utf-8") for v in values)


def seed(path, when=None, complete=False):
    values = [record("session_meta", {"id": "thread", "cwd": "C:/Fixture"}, when),
              record("event_msg", {"type": "task_started", "turn_id": "turn"}, when)]
    if complete:
        values.append(record("event_msg", {"type": "task_complete", "turn_id": "turn", "last_agent_message": "已完成"}, when))
    path.write_bytes(lines(*values))


def test_reader_partial_chinese_corrupt_record_and_truncation(tmp_path):
    path = tmp_path/"rollout-test.jsonl"
    first = lines(record("session_meta", {"id": "中文线程"}))
    marker = first.index("中".encode("utf-8"))+1
    path.write_bytes(first[:marker])
    reader = RolloutReader(path, tmp_path)
    assert reader.poll()[1] == []
    with path.open("ab") as stream: stream.write(first[marker:]+b"not-json\n")
    assert reader.poll()[1][0]["payload"]["id"] == "中文线程"
    assert reader.errors == 1
    path.write_bytes(lines(record("session_meta", {"id": "new"})))
    reset, events, _ = reader.poll()
    assert reset and events[0]["payload"]["id"] == "new"


def test_large_record_is_skipped_without_losing_next_event(tmp_path):
    path = tmp_path/"rollout-test.jsonl"
    path.write_bytes(b"x"*90+b"\n"+lines(record("event_msg", {"type": "task_started", "turn_id": "next"})))
    with patch("monitor.codex_monitor.log_reader.MAX_LINE", 60), patch("monitor.codex_monitor.log_reader.READ_BYTES", 40):
        reader = RolloutReader(path, tmp_path)
        for _ in range(3): reader.poll()
        assert reader.errors >= 1
    # Reopen with a realistic record budget: malformed first line must not poison valid JSON.
    reader = RolloutReader(path, tmp_path)
    assert reader.poll()[1][-1]["payload"]["turn_id"] == "next"


def test_log_history_completion_suppressed_new_turn_resets_and_duplicate_channels_merge(tmp_path):
    path = tmp_path/"rollout-test.jsonl"
    old = (datetime.now(timezone.utc)-timedelta(days=1)).isoformat()
    seed(path, old, complete=True)
    source = CodexSource({"data_path": str(tmp_path)})
    assert source.poll()["runs"] == []
    with path.open("ab") as stream:
        stream.write(lines(record("event_msg", {"type": "task_started", "turn_id": "next"}),
            record("turn_context", {"turn_id": "next", "model": "fixture-model"}),
            record("event_msg", {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 34000},
                "total_token_usage": {"total_tokens": 300000}, "model_context_window": 128000}})))
    run = source.poll()["runs"][0]
    assert run["turn_id"] == "next" and run["summary_preview"] == "" and run["codex"]["usage"]["input"] == 34000
    client, _ = connected()
    client.projection = Projection(source.root)
    client.handle({"method": "turn/started", "params": {"threadId": "thread", "turn": {"id": "next"}}})
    source.client = client
    with patch.object(client, "poll", side_effect=client.snapshot):
        combined = source.poll()
    assert len(combined["runs"]) == 1 and combined["runs"][0]["codex"]["mode"] == "live"
    assert combined["runs"][0]["run_id"] == run["run_id"]
    client.close()
    with path.open("ab") as stream: stream.write(lines(record("event_msg", {"type": "task_complete", "turn_id": "next"})))
    assert source.poll()["runs"][0]["status"] == "completed"


def test_projection_ignores_reasoning_secrets_and_does_not_invent_tool_success():
    model = RolloutProjection("fixture")
    for row in [record("session_meta", {"id": "t", "base_instructions": "PRIVATE"}),
                record("event_msg", {"type": "task_started", "turn_id": "u"}),
                record("response_item", {"type": "reasoning", "encrypted_content": "PRIVATE"}),
                record("response_item", {"type": "function_call", "name": "tool", "call_id": "x", "arguments": "PRIVATE"}),
                record("response_item", {"type": "function_call_output", "call_id": "x", "output": "PRIVATE"})]:
        model.apply(row)
    assert "PRIVATE" not in json.dumps(model.snapshot())
    assert model.snapshot()[0]["tools"][0]["status"] == "unknown"


def test_stale_initial_logs_are_not_fake_active_tasks_and_root_is_bounded(tmp_path):
    path = tmp_path/"rollout-test.jsonl"
    seed(path, (datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
    assert CodexSource({"data_path": str(tmp_path)}).poll()["runs"] == []
    unrelated = tmp_path/"auth.json"; unrelated.write_text("not a log")
    assert discover(tmp_path) == [path]
    with pytest.raises(ValueError): RolloutReader(path, tmp_path/"other").poll()


def test_subscribe_only_loaded_threads_and_no_configuration_overrides():
    client, socket = connected()
    client.poll()
    listing = next(m for m in socket.sent if m.get("method") == "thread/loaded/list")
    client.handle({"id": listing["id"], "result": {"data": ["loaded-a", "loaded-b"], "nextCursor": None}})
    resumes = [m for m in socket.sent if m.get("method") == "thread/resume"]
    assert [m["params"] for m in resumes] == [{"threadId": k, "excludeTurns": True} for k in ("loaded-a", "loaded-b")]
    client.handle({"id": resumes[0]["id"], "result": {"thread": {"id": "loaded-a", "cwd": "C:/Fixture"}}})
    listing = socket.sent[-1]
    assert listing["method"] == "thread/turns/list" and listing["params"]["limit"] == 1
    client.handle({"id": listing["id"], "result": {"data": [{"id": "u", "status": "inProgress", "startedAt": 1700000000, "items": []}]}})
    assert client.snapshot()["runs"][0]["status"] == "running"
    assert client.snapshot()["runs"][0]["started_at"] == stamp(1700000000)
    client.close()
    assert socket.closed and not any(m.get("method") in {"turn/interrupt", "turn/start"} for m in socket.sent)


@pytest.mark.parametrize("ident", [7, "7"])
@pytest.mark.parametrize("action,decision", [("approve_tool", "accept"), ("deny_tool", "decline")])
def test_approval_exact_request_identity_single_send_and_no_fake_ack(ident, action, decision):
    client, socket = connected()
    client.handle(approval(ident))
    a, payload = choose(client, action)
    client.send(a["type"], a["run_id"], payload)
    with pytest.raises(ValueError): client.send(a["type"], a["run_id"], payload)
    client.poll()
    assert {"id": ident, "result": {"decision": decision}} in socket.sent
    assert client.snapshot()["runs"][0]["status"] == "waiting_confirmation"
    assert not client.snapshot()["actions"]
    client.handle({"method": "serverRequest/resolved", "params": {"threadId": "thread", "requestId": ident}})
    run = client.snapshot()["runs"][0]
    assert run["status"] == "running" and run["confirmations"][-1]["status"] == "superseded"
    with pytest.raises(ValueError): client.send(a["type"], a["run_id"], payload)


def test_other_client_resolution_before_flush_and_disconnect_generation_cannot_replay():
    client, socket = connected()
    client.handle(approval())
    a, payload = choose(client)
    client.send(a["type"], a["run_id"], payload)
    socket.incoming.append({"method": "serverRequest/resolved", "params": {"threadId": "thread", "requestId": 7}})
    client.poll()
    assert not any("result" in m for m in socket.sent)
    client.handle(approval())
    old, old_payload = choose(client)
    client.send(old["type"], old["run_id"], old_payload)
    client._disconnect("fixture")
    assert not client.snapshot()["actions"]
    client.retry_at = 0
    client.poll()
    client.ready = True
    client.handle(approval())
    new, _ = choose(client)
    assert new["confirmation_id"] != old["confirmation_id"]
    with pytest.raises(ValueError): client.send(old["type"], old["run_id"], old_payload)
    assert not client.queue


def test_available_decisions_unsupported_permissions_and_missing_command():
    client, _ = connected()
    client.handle(approval(availableDecisions=["decline", "acceptForSession"]))
    assert {a["type"] for a in client.snapshot()["actions"]} == {"deny_tool"}
    client.handle(approval(ident=8, tid="another", additionalPermissions={"network": True}))
    assert len(client.snapshot()["actions"]) == 1
    client.handle(approval(ident=9, tid="third", command=None))
    assert len(client.snapshot()["actions"]) == 1
    client.handle({"id": 10, "method": "item/tool/requestUserInput", "params": {"threadId": "q", "turnId": "q"}})
    assert len(client.snapshot()["actions"]) == 1


def test_file_approval_requires_complete_changes_and_cross_thread_resolution_is_ignored():
    client, _ = connected()
    client.handle({"method": "item/started", "params": {"threadId": "t", "turnId": "u", "item": {
        "id": "file", "type": "fileChange", "changes": [{"path": "hello.py", "kind": {"type": "add"}, "diff": "+print('hi')"}]}}})
    client.handle({"id": "file-approval", "method": "item/fileChange/requestApproval", "params": {
        "threadId": "t", "turnId": "u", "itemId": "file", "reason": "创建文件"}})
    state = client.snapshot()
    assert len(state["actions"]) == 2
    assert "+print('hi')" in json.dumps(state, ensure_ascii=False)
    client.handle({"method": "serverRequest/resolved", "params": {"threadId": "wrong", "requestId": "file-approval"}})
    assert len(client.snapshot()["actions"]) == 2
    client.handle({"method": "turn/completed", "params": {"threadId": "t", "turn": {"id": "u", "status": "interrupted"}}})
    assert not client.snapshot()["actions"] and client.snapshot()["runs"][0]["status"] == "cancelled"


def test_replayed_approval_identity_stays_stable_until_content_changes():
    client, _ = connected()
    client.handle(approval())
    old = deepcopy(client.snapshot())
    client.handle(approval())
    assert client.snapshot() == old
    client.handle(approval(command="python -m pytest tests/new"))
    assert client.snapshot()["actions"][0]["confirmation_id"] != old["actions"][0]["confirmation_id"]
    assert len(client.snapshot()["runs"][0]["pending_confirmations"]) == 1


def test_hub_routes_codex_only_and_disposes_on_reconfigure_disable_and_close():
    client, _ = connected(); client.handle(approval())
    source = Mock(); source.poll.side_effect = client.snapshot; source.send.side_effect = client.send
    primary = Mock()
    config = {"codex": {"enabled": True, "server_url": "ws://127.0.0.1:4500"}}
    base = {"runs": [], "preferences": {"monitor_sources": config}}
    primary.read.side_effect = lambda: deepcopy(base)
    factory = Mock(return_value=source)
    hub = SourceHub(lambda: deepcopy(base["preferences"]), factories={"codex": factory}, start_worker=False)
    hub.read(); hub.poll_sources()
    a, payload = choose(client)
    with pytest.raises(ValueError): hub.send("cancel_run", a["run_id"], {})
    hub.send(a["type"], a["run_id"], payload)
    primary.send.assert_not_called()
    config["codex"]["server_url"] = "ws://127.0.0.1:4501"
    hub.read()
    with pytest.raises(ValueError): hub.send(a["type"], a["run_id"], payload)
    hub.poll_sources(); assert source.close.call_count == 1
    config["codex"]["enabled"] = False
    assert hub.read()["runs"] == []
    hub.poll_sources(); assert source.close.call_count == 2
    hub.close()


def test_complete_for_previous_turn_does_not_finish_new_turn():
    model = Projection("fixture")
    model.event("turn/started", {"threadId": "t", "turn": {"id": "old"}})
    model.event("turn/started", {"threadId": "t", "turn": {"id": "new"}})
    model.event("turn/completed", {"threadId": "t", "turn": {"id": "old", "status": "completed"}})
    assert model.runs[("t", "new")]["status"] == "running"
    assert model.runs[("t", "old")]["status"] == "completed"


def test_hydration_recovers_task_completed_while_disconnected_without_replaying_old_history():
    client, socket = connected()
    client.handle(approval())
    client._disconnect("fixture")
    client.socket = socket
    client._request("thread/turns/list", {"threadId": "thread", "limit": 1}, "thread")
    ident = socket.sent[-1]["id"]
    client.handle({"id": ident, "result": {"data": [{"id": "turn", "status": "completed", "items": [
        {"type": "agentMessage", "id": "final", "text": "Finished while disconnected"}]}]}})
    run = client.snapshot()["runs"][0]
    assert run["status"] == "completed" and not run["connection_error"]
    assert run["summary_preview"] == "Finished while disconnected"
    client._request("thread/turns/list", {"threadId": "unseen", "limit": 1}, "unseen")
    client.handle({"id": socket.sent[-1]["id"], "result": {"data": [{"id": "old", "status": "completed"}]}})
    assert len(client.snapshot()["runs"]) == 1


def test_cancel_only_choice_is_explicit_and_sent_without_policy_amendment():
    client, socket = connected()
    client.handle(approval(availableDecisions=["accept", "acceptForSession", "cancel"]))
    a, payload = choose(client, "deny_tool")
    assert a["label"] == "拒绝并停止本轮" and a["codex_decision"] == "cancel"
    client.send("deny_tool", a["run_id"], payload); client.poll()
    assert {"id": 7, "result": {"decision": "cancel"}} in socket.sent


def test_legacy_enabled_flag_cannot_implicitly_enable_new_connector():
    assert not normalize_sources({"opencodex": {"enabled": True}})["codex"]["enabled"]


def test_file_replay_hydrates_full_diff_and_rejects_previous_revision():
    client, socket = connected()
    client.handle({"id": 8, "method": "item/fileChange/requestApproval", "params": {
        "threadId": "t", "turnId": "u", "itemId": "f"}})
    assert not client.snapshot()["actions"]
    request = socket.sent[-1]
    assert request["method"] == "thread/items/list" and request["params"]["limit"] == 32
    client.handle({"id": request["id"], "result": {"data": [{"item": {"id": "f", "type": "fileChange",
        "changes": [{"path": "hello.txt", "diff": "+hello"}]}}]}})
    action, payload = choose(client)
    assert payload["expected_revision"] == 2
    with pytest.raises(ValueError): client.send("approve_tool", action["run_id"], {**payload,"expected_revision":1})
    client.send("approve_tool", action["run_id"], payload); client.poll()
    assert {"id": 8, "result": {"decision": "accept"}} in socket.sent


def test_tail_can_recover_identity_from_real_turn_id_without_start_record():
    model = RolloutProjection("fixture")
    model.apply(record("session_meta", {"id": "t"}))
    model.apply(record("event_msg", {"type": "item_completed", "thread_id": "t", "turn_id": "u",
        "item": {"type": "commandExecution", "id": "cmd", "command": "python --version", "status": "completed", "exitCode": 0}}))
    run = model.snapshot()[0]
    assert run["turn_id"] == "u" and run["codex"]["time_basis"] == "observed"
    assert run["tools"][0]["status"] == "completed"


def test_backlog_keeps_last_published_frame_until_caught_up(tmp_path):
    path = tmp_path/"rollout-active.jsonl"
    seed(path)
    source = CodexSource({"data_path": str(tmp_path)})
    original = source.poll()["runs"][0]
    with path.open("ab") as stream:
        stream.write(lines(record("response_item", {"type": "reasoning", "encrypted_content": "x"*6000}),
                           record("event_msg", {"type": "task_complete", "turn_id": "turn"})))
    with patch("monitor.codex_monitor.log_reader.READ_BYTES", 512):
        progress = source.poll()
        assert progress["runs"][0]["run_id"] == original["run_id"]
        assert progress["runs"][0]["status"] == "running"
    assert source.poll()["runs"][0]["status"] == "completed"
