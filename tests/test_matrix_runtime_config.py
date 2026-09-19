"""AgentTeams Matrix runtime discovery writes reproducible, secret-safe settings."""
from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "configure_matrix_worker_rooms.py"
)
SPEC = importlib.util.spec_from_file_location("configure_matrix_runtime", SCRIPT)
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(runtime)


class TestMatrixRuntimeConfig(unittest.TestCase):
    def test_controller_local_url_is_rewritten_for_compose_network(self):
        self.assertEqual(
            runtime.runtime_homeserver_url(
                "http://127.0.0.1:6167", "agentteams-controller",
            ),
            "http://agentteams-controller:6167",
        )

    def test_collect_runtime_discovers_credentials_and_all_rooms(self):
        def fake_environment(container: str) -> dict[str, str]:
            if container == "agentteams-controller":
                return {
                    "AGENTTEAMS_MATRIX_URL": "http://127.0.0.1:6167",
                    "AGENTTEAMS_MATRIX_DOMAIN": "matrix.test:8086",
                    "AGENTTEAMS_ADMIN_USER": "admin",
                    "AGENTTEAMS_ADMIN_PASSWORD": "secret",
                }
            if container == "agentteams-worker-revguard-orchestrator":
                return {
                    "AGENTTEAMS_WORKER_MATRIX_TOKEN": "orchestrator-token",
                }
            actor = container.removeprefix("agentteams-worker-")
            return {"AGENTTEAMS_WORKER_ROOM_ID": f"!{actor}:matrix.test"}

        def fake_resources(controller: str) -> dict[str, dict]:
            # Host-side `docker exec ... agt get workers` is stubbed so the
            # discovery contract is verified without a Docker CLI inside the
            # verification container.
            self.assertEqual(controller, "agentteams-controller")
            return {
                actor: {"roomID": f"!{actor}:matrix.test"}
                # The host-side discovery returns the 9 Workers plus the
                # orchestrator entry that binds the case room.
                for actor in (*runtime.ACTORS, "revguard-orchestrator")
            }

        def fake_team_resource(controller: str, team_name: str) -> dict:
            self.assertEqual(controller, "agentteams-controller")
            self.assertEqual(team_name, "revguard-team")
            return {
                "name": team_name,
                "teamRoomID": "!team:matrix.test",
                "leaderDMRoomID": "!leader:matrix.test",
            }

        with (
            patch.object(runtime, "container_environment", fake_environment),
            patch.object(runtime, "worker_resources", fake_resources),
            patch.object(runtime, "team_resource", fake_team_resource),
        ):
            values = runtime.collect_runtime(
                "agentteams-worker-", "agentteams-controller",
            )
        self.assertEqual(values["REVGUARD_TEAM_TRANSPORT"], "matrix")
        self.assertEqual(
            values["REVGUARD_MATRIX_HOMESERVER_URL"],
            "http://agentteams-controller:6167",
        )
        self.assertEqual(
            values["REVGUARD_MATRIX_ROOM_ID"],
            "!team:matrix.test",
        )
        self.assertEqual(
            values["REVGUARD_MATRIX_ORCHESTRATOR_ROOM_ID"],
            "!leader:matrix.test",
        )
        self.assertIn('"revguard-executor":"!revguard-executor:matrix.test"',
                      values["REVGUARD_MATRIX_WORKER_ROOMS_JSON"])
        self.assertEqual(
            values["REVGUARD_MATRIX_APPROVAL_ACCESS_TOKEN"],
            "orchestrator-token",
        )
        self.assertEqual(
            values["REVGUARD_MATRIX_ACCESS_TOKEN"],
            "orchestrator-token",
        )

    def test_update_env_replaces_managed_values_and_uses_mode_0600(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("KEEP=value\nREVGUARD_TEAM_TRANSPORT=mcp\n", encoding="utf-8")
            runtime.update_env(path, {
                "REVGUARD_TEAM_TRANSPORT": "matrix",
                "REVGUARD_MATRIX_USERNAME": "admin",
            })
            text = path.read_text(encoding="utf-8")
            mode = stat.S_IMODE(path.stat().st_mode)
        self.assertIn("KEEP=value", text)
        self.assertIn("REVGUARD_TEAM_TRANSPORT=matrix", text)
        self.assertIn("REVGUARD_MATRIX_USERNAME=admin", text)
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
