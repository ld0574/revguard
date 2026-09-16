from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.configure_demo_principals import configure, read_principals
from scripts.configure_matrix_worker_rooms import update_env
from scripts.verify_higress_isolation import valid_probe


class TestDeploymentPrincipals(unittest.TestCase):
    def test_mcp_isolation_requires_real_denials_not_hidden_configuration(self):
        expected = {"mcp-evidence": ["Collect"], "mcp-executor": ["Write"]}
        own = {"server": "mcp-evidence", "status": 200, "tools": ["Collect"]}
        other = {"server": "mcp-executor", "status": 403, "tools": []}
        self.assertTrue(valid_probe("mcp-evidence", expected, [own, other]))
        for status in (200, 404, 500, 0):
            self.assertFalse(valid_probe("mcp-evidence", expected, [own, {**other, "status": status}]))
        self.assertFalse(valid_probe("mcp-evidence", expected, [own]))
        self.assertFalse(valid_probe("mcp-evidence", expected, [{**own, "tools": []}, other]))

    def test_private_backend_keys_are_stable_and_webui_key_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            template = Path(directory) / "principals.json"
            template.write_text(json.dumps({
                "rg-demo-viewer-key-1": {"actor": "api-viewer", "roles": ["viewer"]},
                "rg-demo-intake-key-1": {"actor": "revguard-intake", "roles": ["worker"]},
            }))
            env.write_text("KEEP_THIS=value\n")
            configure(env, template)
            first = read_principals(env, template)
            self.assertIn("rg-demo-viewer-key-1", first)
            self.assertNotIn("rg-demo-intake-key-1", first)
            self.assertTrue(any(key.startswith("rg-backend-") for key in first))
            configure(env, template)
            self.assertEqual(first, read_principals(env, template))
            self.assertIn("KEEP_THIS=value", env.read_text())
            self.assertEqual(env.stat().st_mode & 0o777, 0o600)

    def test_matrix_setup_preserves_explicit_human_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            env = Path(directory) / ".env"
            existing = '{"@reviewer:matrix":{"actor":"finance.lead"}}'
            env.write_text("REVGUARD_HITL_MATRIX_USERS_JSON=" + existing + "\n")
            update_env(env, {
                "REVGUARD_HITL_MATRIX_USERS_JSON": '{"@admin:matrix":{}}',
                "REVGUARD_MATRIX_ROOM_ID": "!room:matrix",
            })
            self.assertIn(existing, env.read_text())
            self.assertNotIn("@admin", env.read_text())
