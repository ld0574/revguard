"""Agent-facing Adapter exposes only assigned Skills and requires StageTask binding."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parent.parent / "agentteams" / "skills" /
          "revguard-api" / "scripts" / "revguard_call.py")
SPEC = importlib.util.spec_from_file_location("revguard_agentteams_adapter", SCRIPT)
adapter = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(adapter)


class _Response:
    def __init__(self):
        self.headers = {
            "X-Request-ID": "REQ-ADAPTER-TEST",
            "X-Skill-Receipt": "SKR-TEST",
        }

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps({"success": True, "data": {"ok": True}}).encode("utf-8")


class TestAgentTeamsAdapter(unittest.TestCase):
    def _run(self, argv: list[str], *, worker: str):
        output = io.StringIO()
        with patch.dict(os.environ, {"AGENTTEAMS_WORKER_NAME": worker}, clear=False), \
                patch.object(sys, "argv", [str(SCRIPT), *argv]), \
                patch("sys.stdout", output):
            code = adapter.main()
        return code, json.loads(output.getvalue())

    def test_worker_requires_dispatched_task_id(self):
        code, result = self._run([
            "--skill", "EvidenceCollectSkill", "--case-id", "CASE-1",
            "--input", "{}", "--message-id", "MATRIX-1",
        ], worker="revguard-evidence")
        self.assertEqual(code, 4)
        self.assertEqual(result["error"]["type"], "TASK_ID_REQUIRED")

    def test_worker_cannot_invoke_another_roles_skill(self):
        code, result = self._run([
            "--skill", "LedgerAdjustSkill", "--task-id", "TASK-1",
            "--case-id", "CASE-1", "--input", "{}", "--message-id", "MATRIX-1",
        ], worker="revguard-evidence")
        self.assertEqual(code, 4)
        self.assertEqual(result["error"]["type"], "SKILL_NOT_ALLOWED")

    def test_task_id_is_forwarded_to_skill_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "key"
            secret.write_text("adapter-test-key", encoding="utf-8")
            with patch.object(adapter, "_credential_path", return_value=secret), \
                    patch.object(adapter.urllib.request, "urlopen", return_value=_Response()) as call:
                code, result = self._run([
                    "--skill", "EvidenceCollectSkill", "--task-id", "TASK-1",
                    "--case-id", "CASE-1", "--input", "{}",
                    "--message-id", "MATRIX-1", "--request-id", "REQ-ADAPTER-TEST",
                ], worker="revguard-evidence")
        self.assertEqual(code, 0)
        self.assertEqual(result["skill_receipt"], "SKR-TEST")
        request = call.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/skills/EvidenceCollectSkill/invoke"))
        self.assertEqual(request.headers["X-revguard-task-id"], "TASK-1")


if __name__ == "__main__":
    unittest.main()
