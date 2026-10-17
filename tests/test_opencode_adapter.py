from __future__ import annotations

from pathlib import Path
import threading
import unittest
from unittest.mock import patch, Mock

from monitor.opencode_monitor.adapter import OpenCodeSource, database_path
from monitor.src.app_sources import app_source
ICON_PATH = app_source("opencode").icon


class _Player:
    def __init__(self):
        self.calls = []

    def play(self, name):
        self.calls.append(name)
        return True


class _Client:
    available = True

    def __init__(self):
        self.calls = []

    def abort(self, *args):
        self.calls.append(("abort", args)); return True

    def reply_permission(self, *args):
        self.calls.append(("permission", args)); return True

    def reply_question(self, *args):
        self.calls.append(("question", args)); return True

    def reject_question(self, *args):
        self.calls.append(("reject_question", args)); return True


def source() -> OpenCodeSource:
    value = OpenCodeSource.__new__(OpenCodeSource)
    value.last_runs = {"ses_1": {"run_id": "opencode:ses_1", "project_id": "D:/work"}}
    value.client = _Client()
    value._lock = threading.RLock()
    value._decision_ids = set()
    value._decision_player = _Player()
    return value


class AdapterTests(unittest.TestCase):
    def test_unreachable_server_marks_live_rows_unavailable_but_preserves_final_facts(self):
        value = source()
        value.config = {"data_path": "fixture.db"}
        value.started_at = 0
        value.observed, value.published = set(), {}
        value.bridge = Mock()
        value.client = Mock(available=False)
        value._play_decisions = Mock()
        raw = {"revision": 1, "status": "running", "actions": [], "runs": [
            {"run_id": "live", "status": "running", "updated_at": "2026-10-16T00:00:00+00:00"},
            {"run_id": "done", "status": "completed", "updated_at": "2026-10-16T00:00:00+00:00"},
        ]}
        value.bridge.snapshot.return_value = raw
        with patch("monitor.opencode_monitor.adapter._enrich_snapshot", side_effect=lambda snapshot, _: snapshot):
            result = value.poll()
        rows = {r["source_run_id"]: r for r in result["runs"]}
        self.assertTrue(rows["live"]["connection_error"])
        self.assertFalse(rows["done"].get("connection_error", False))
        self.assertEqual(rows["done"]["status"], "completed")
        self.assertEqual(result["actions"], [])

    def test_official_icon_asset_is_available(self):
        self.assertTrue(ICON_PATH.is_file())
        self.assertGreater(ICON_PATH.stat().st_size, 1000)

    def test_database_path_accepts_file_existing_or_future_directory(self):
        self.assertEqual(database_path({"data_path": "D:/data/opencode.db"}), Path("D:/data/opencode.db"))
        self.assertEqual(database_path({"data_path": "D:/data/opencode"}), Path("D:/data/opencode/opencode.db"))
        self.assertIsNone(database_path({}))

    def test_decorate_keeps_metrics_and_prefixes_identity(self):
        run = {
            "run_id": "ses_1", "context": {"percent": 72.5},
            "changes": {"files": 2}, "child_summary": {"total": 1},
            "compaction": {"active": True},
            "pending_confirmations": [{"id": "per_1", "status": "pending"}],
        }
        decorated = source()._decorate(run, 9)
        self.assertEqual(decorated["run_id"], "opencode:ses_1")
        self.assertEqual(decorated["source_run_id"], "ses_1")
        self.assertEqual(decorated["monitor_detail"]["context"]["percent"], 72.5)
        self.assertEqual(decorated["confirmations"][0]["revision"], 9)

    def test_actions_are_scoped_and_revision_bound(self):
        snapshot = {"revision": 12, "actions": [
            {"id": "a", "type": "approve_tool", "run_id": "ses_1"},
            {"id": "b", "type": "cancel_run", "run_id": "hidden"},
        ]}
        actions = source()._actions(snapshot, {"ses_1": {}})
        self.assertEqual(actions, [{
            "id": "a", "type": "approve_tool", "run_id": "opencode:ses_1",
            "expected_revision": 12,
        }])

    def test_sound_covers_new_permission_and_question_once(self):
        value = source()
        initial = [{"pending_confirmations": [
            {"id": "p1", "kind": "permission", "status": "pending"},
        ]}]
        later = [{"pending_confirmations": [
            {"id": "p1", "kind": "permission", "status": "pending"},
            {"id": "q1", "kind": "question", "status": "pending"},
        ]}]
        with patch("monitor.opencode_monitor.adapter.load_config", return_value={"monitor_sound_enabled": True, "monitor_completion_sound": "sound-4"}):
            value._play_decisions(initial)
            value._play_decisions(later)
            value._play_decisions(later)
        self.assertEqual(value._decision_player.calls, ["sound-4", "sound-4"])

    def test_send_routes_all_supported_controls(self):
        value = source()
        value.send("cancel_run", "opencode:ses_1", {})
        value.send("approve_tool", "opencode:ses_1", {"confirmation_id": "p1"})
        value.send("deny_tool", "opencode:ses_1", {"confirmation_id": "p2"})
        value.send("answer_question", "opencode:ses_1", {"confirmation_id": "q1", "answers": [["A"]]})
        value.send("reject_question", "opencode:ses_1", {"confirmation_id": "q2"})
        self.assertEqual([item[0] for item in value.client.calls], [
            "abort", "permission", "permission", "question", "reject_question"
        ])
        self.assertEqual(value.client.calls[1][1][1], "once")
        self.assertEqual(value.client.calls[2][1][1], "reject")


if __name__ == "__main__":
    unittest.main()
