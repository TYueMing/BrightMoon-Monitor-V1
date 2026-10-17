from __future__ import annotations

import unittest

from monitor.opencode_monitor.enrichment import (
    _change_summary, _context_limit, _enrich_snapshot,
)


class _Client:
    available = True

    def __init__(self, permissions=None, questions=None):
        self.permissions = permissions or []
        self.questions = questions or []

    def pending_permissions(self): return self.permissions
    def pending_questions(self): return self.questions
    def session_status_snapshot(self): return False, {}
    def session_diff(self, *_): return []
    def session_children(self, *_): return []
    def provider_snapshot(self, *_): return {}


def snapshot():
    return {
        "revision": 4, "status": "running", "runs": [{
            "run_id": "ses_1", "status": "running", "project_id": "D:/work",
            "confirmations": [], "pending_confirmations": [], "timeline": [],
            "usage": {"input_tokens": 1200, "output_tokens": 300, "reasoning_tokens": 100},
        }], "actions": [], "timeline": [],
    }


class EnrichmentTests(unittest.TestCase):
    def test_permission_exposes_real_arguments_and_reason(self):
        client = _Client(permissions=[{
            "id": "per_1", "sessionID": "ses_1", "permission": "external_directory",
            "metadata": {"path": "D:/outside"}, "message": "需要读取依赖",
        }])
        result = _enrich_snapshot(snapshot(), client)
        confirmation = result["runs"][0]["confirmations"][0]
        self.assertEqual(confirmation["arguments"], {"path": "D:/outside"})
        self.assertEqual(confirmation["reason"], "需要读取依赖")
        self.assertEqual(confirmation["kind"], "permission")
        self.assertEqual({item["type"] for item in result["actions"]},
                         {"approve_tool", "deny_tool", "cancel_run"})

    def test_question_preserves_multiple_custom_and_options(self):
        client = _Client(questions=[{
            "id": "q1", "sessionID": "ses_1", "questions": [{
                "header": "发布", "question": "选择环境", "multiple": True,
                "custom": True, "options": [{"label": "测试"}, {"label": "生产"}],
            }],
        }])
        question = _enrich_snapshot(snapshot(), client)["runs"][0]["confirmations"][0]["questions"][0]
        self.assertTrue(question["multiple"])
        self.assertTrue(question["custom"])
        self.assertEqual([item["label"] for item in question["options"]], ["测试", "生产"])

    def test_metric_helpers(self):
        provider = {"providers": [{"id": "openai", "models": {
            "gpt-x": {"limit": {"context": 32768}}
        }}]}
        self.assertEqual(_context_limit(provider, "openai/gpt-x"), 32768)
        changes = _change_summary([
            {"file": "a.py", "additions": 4, "deletions": 1},
            {"path": "b.py", "added": 3, "removed": 2},
        ])
        self.assertEqual((changes["files"], changes["additions"], changes["deletions"]), (2, 7, 3))


if __name__ == "__main__":
    unittest.main()
