"""Provider boundary, durable Harness logs, routing and account settings regressions."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
import json
import sys
import time

import pytest
import zstandard

from monitor.deepseek_monitor.log_reader import SessionLogReader, LogFormatError
from monitor.deepseek_monitor.projection import SessionProjection
from monitor.deepseek_monitor.adapter import DeepSeekSource, discover_logs
from monitor.src.source_config import normalize_sources, deepseek_session_root, inspect_source
from monitor.src.source_hub import SourceHub
from monitor.UI.detail_panels import panel_for, register_panel, UnknownPanel


def header(version=4, seeded=False):
    return {"type":"session", "version":version, "id":"session-fixture", "createdAt":int(time.time()*1000),
            "isSeeded":seeded, "delegationDepth":0, "cwd":"C:/fixture/project"}


def events(end=False):
    rows = [
        ("turn/start", {"turn":1}), ("step/start", {"turn":1,"step":1}),
        ("user/message", {"turn":1,"message":{"content":[{"type":"text","text":"检查测试工程"}]}}),
        ("tool/call", {"turn":1,"step":1,"callId":"call-1","name":"run_code","arguments":'{"code":"fixture()"}'}),
        ("approval/asked", {"id":"ask-1","toolName":"run_code","callId":"call-1","reason":"需要读取工程"}),
    ]
    if end:
        rows += [
            ("approval/decided", {"id":"ask-1","outcome":"allowed-once"}),
            ("tool/result", {"turn":1,"step":1,"message":{"toolCallId":"call-1","isError":False}}),
            ("assistant/message", {"turn":1,"step":1,"message":{"content":[
                {"type":"reasoning","text":"不应展示的推理"}, {"type":"text","text":"测试工程检查完成"}]}}),
            ("step/end", {"turn":1,"step":1}), ("turn/end", {"turn":1,"reason":{"kind":"completed"}}),
        ]
    return [{"type":k,"data":d,"seq":i,"time":int(time.time()*1000)+i} for i,(k,d) in enumerate(rows)]


def lines(rows):
    return b"".join((json.dumps(r,ensure_ascii=False)+"\n").encode() for r in rows)


def log(tmp_path, *, compressed=False, version=4, rows=None):
    path=tmp_path/"project"/"session"/f"session.v{version}.jsonl{'.zstd' if compressed else ''}"
    path.parent.mkdir(parents=True,exist_ok=True)
    records=[header(version), *(events() if rows is None else rows)]
    if compressed:
        codec=zstandard.ZstdCompressor(write_checksum=True,write_content_size=False)
        path.write_bytes(codec.compress(lines(records[:1]))+codec.compress(lines(records[1:])))
    else:
        path.write_bytes(lines(records))
    return path


@pytest.mark.parametrize("compressed", [False,True])
@pytest.mark.parametrize("version", [3,4])
def test_incremental_reader_and_projection_wait_for_complete_committed_records(tmp_path,compressed,version):
    path=log(tmp_path,compressed=compressed,version=version)
    original=path.read_bytes()
    reader=SessionLogReader(path)
    reset,got,caught_up=reader.poll()
    assert not reset and caught_up and len(got)==5
    projection=SessionProjection(reader.header,str(path.parent))
    for event in got: projection.apply(event)
    run=projection.snapshot()
    assert run["status"]=="waiting_confirmation" and run["app_id"]=="deepseek_harness"
    assert run["pending_confirmations"][0]["arguments"]=={"code":"fixture()"}
    assert not reader.poll()[1]
    tail=lines(events(True)[5:])
    if compressed: tail=zstandard.ZstdCompressor(write_checksum=True,write_content_size=False).compress(tail)
    # Whole earlier records can be returned for raw JSONL; a checksum-protected
    # compressed frame is indivisible even if it already contains decoded text.
    with path.open("ab") as stream: stream.write(tail[:-3])
    partial=reader.poll()[1]
    if compressed: assert not partial
    for event in partial: projection.apply(event)
    assert projection.snapshot()["status"] != "completed"
    with path.open("ab") as stream: stream.write(tail[-3:])
    for event in reader.poll()[1]: projection.apply(event)
    run=projection.snapshot()
    assert run["status"]=="completed" and run["summary_preview"]=="测试工程检查完成"
    assert run["tools"][0]["status"]=="completed" and not run["pending_confirmations"]
    assert path.read_bytes()==original+tail  # reader never repairs or rewrites logs


def test_corrupt_checksum_refuses_frame_and_future_generation_does_not_fall_back(tmp_path):
    path=log(tmp_path,compressed=True)
    raw=path.read_bytes();path.write_bytes(raw[:-1]+bytes([raw[-1]^1]))
    with pytest.raises(LogFormatError,match="校验失败"): SessionLogReader(path).poll()
    path.unlink()
    old=log(tmp_path,version=3)
    future=old.with_name("session.v99.jsonl");future.write_bytes(lines([header(99)]))
    paths,_=discover_logs(tmp_path)
    assert paths==[future]
    with pytest.raises(LogFormatError,match="v3/v4"): SessionLogReader(future).poll()


def test_sequence_gap_and_header_mismatch_are_explicit(tmp_path):
    rows=events();rows[3]["seq"]=999
    path=log(tmp_path,rows=rows)
    with pytest.raises(LogFormatError,match="序号"):SessionLogReader(path).poll()
    path.write_bytes(lines([header(3),*events()]))
    with pytest.raises(LogFormatError,match="文件名"):SessionLogReader(path).poll()


def test_new_turn_resets_approval_summary_tools_and_inherited_seed_is_not_a_task():
    projection=SessionProjection(header(seeded=True),"source")
    for event in events(True):projection.apply(event)
    assert projection.snapshot() is None
    projection.apply({"type":"session/end-seed","data":{},"seq":10})
    for event in events(True):projection.apply(event)
    previous=projection.run["run_id"]
    projection.apply({"type":"turn/start","seq":11,"time":int(time.time()*1000),"data":{"turn":2}})
    run=projection.snapshot()
    assert run["run_id"]!=previous and run["summary_preview"]=="" and not run["tools"] and not run["pending_confirmations"]
    assert run["status"]=="running"


def test_stale_is_uncertainty_not_fabricated_completion_and_stop_freezes_timer():
    projection=SessionProjection(header(),"source")
    for event in events():projection.apply(event)
    assert projection.snapshot(now=time.time()+601)["status"]=="stale"
    projection.apply({"type":"turn/end","seq":6,"time":int(time.time()*1000),"data":{"turn":1,"reason":{"kind":"aborted"}}})
    result=projection.snapshot(now=time.time()+601)
    assert result["status"]=="cancelled" and result["ended_at"] and not result["pending_confirmations"]


def test_adapter_does_not_replay_history_and_keeps_observed_completion(tmp_path):
    rows=events(True)
    for e in rows:e["time"]-=3600_000
    path=log(tmp_path,rows=rows)
    source=DeepSeekSource({"data_path":str(tmp_path)})
    assert source.poll()["runs"]==[]
    new=[{"type":"turn/start","seq":10,"time":int(time.time()*1000),"data":{"turn":2}}]
    with path.open("ab") as stream:stream.write(lines(new))
    run=source.poll()["runs"][0]
    assert run["turn_id"]=="2" and run["status"]=="running"
    with path.open("ab") as stream:stream.write(lines([{"type":"turn/end","seq":11,"time":int(time.time()*1000),"data":{"turn":2,"reason":{"kind":"completed"}}}]))
    assert source.poll()["runs"][0]["status"]=="completed"
    assert source.poll()["runs"][0]["status"]=="completed"
    with pytest.raises(ValueError,match="只读"):source.send("approve_tool",run["run_id"],{})


def test_adapter_retains_last_frame_during_backlog_and_parse_error_then_recovers(tmp_path):
    path = log(tmp_path)
    source = DeepSeekSource({"data_path": str(tmp_path)})
    initial = source.poll()["runs"][0]
    with path.open("ab") as stream:
        stream.write(lines(events(True)[5:]))
    with patch("monitor.deepseek_monitor.log_reader.READ_BYTES", 30):
        waiting = source.poll()["runs"][0]
        assert waiting["run_id"] == initial["run_id"] and waiting["monitor_syncing"]
    assert source.poll()["runs"][0]["status"] == "completed"
    with path.open("ab") as stream:
        stream.write(b"invalid-json\n")
    for _ in range(2):
        broken = source.poll()
        assert broken["status"] == "degraded" and broken["runs"][0]["connection_error"]
        assert broken["runs"][0]["status"] == "completed"
    # Simulate upstream atomic replacement; the monitor itself never repairs it.
    log(tmp_path, rows=events())
    assert source.poll()["runs"][0]["status"] == "waiting_confirmation"
    assert not source.poll()["runs"][0].get("connection_error")


def test_reader_yields_when_compressed_decode_budget_is_used(tmp_path):
    path = log(tmp_path, compressed=True)
    with patch("monitor.deepseek_monitor.log_reader.DECODE_BYTES", 1):
        reader = SessionLogReader(path)
        _, first, caught_up = reader.poll()
        assert not caught_up and not first and reader.header
        _, second, caught_up = reader.poll()
        assert caught_up and len(second) == 5


def test_adapter_retries_transient_read_error_without_needing_a_file_change(tmp_path):
    log(tmp_path)
    source = DeepSeekSource({"data_path": str(tmp_path)})
    with patch("monitor.deepseek_monitor.adapter.time.monotonic", return_value=100):
        with patch.object(SessionLogReader, "poll", side_effect=PermissionError("fixture file sharing")):
            assert source.poll()["status"] == "degraded"
        assert source.poll()["status"] == "degraded"
    with patch("monitor.deepseek_monitor.adapter.time.monotonic", return_value=106):
        recovered = source.poll()
        assert recovered["status"] == "ready" and recovered["runs"][0]["status"] == "waiting_confirmation"




def test_reader_resets_after_truncation_and_skips_plugin_payload_without_losing_sequence(tmp_path):
    path=log(tmp_path)
    reader=SessionLogReader(path);reader.poll()
    path.write_bytes(lines([header()]))
    assert reader.poll()[0]
    with path.open("ab") as stream:stream.write(lines([
        {"seq":0,"time":1,"type":"plugin:test/info","data":"opaque"},
        {"seq":1,"time":2,"type":"turn/start","data":{"turn":1}}]))
    assert reader.poll()[1][0]["seq"]==1












