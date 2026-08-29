from __future__ import annotations

import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib import error

from revguard.agent_bridge import execute_agent_task
from revguard.matrix_team import (
    MatrixClient,
    MatrixSettings,
    MatrixTeamRunner,
    MatrixTransportError,
)
from revguard.mcp_server import hydrate_server_secrets
from revguard.mcp_team import McpTeamRunner
from revguard.mocks import ToolGateway
from revguard.models import Case, CaseStatus
from revguard.state_machine import transition_case
from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent


class FakeMatrixClient:
    """Executes the mentioned Worker like AgentTeams while preserving Matrix events."""

    def __init__(self, settings, store, gateway):
        self.settings = settings
        self.store = store
        self.gateway = gateway
        self.counter = 0
        self.events = []

    async def authenticate(self):
        return None

    async def cursor(self):
        return f"s{self.counter}"

    async def send_text(self, body, *, mentions=None, room_id=None):
        self.counter += 1
        event_id = f"$event-{self.counter}"
        if mentions:
            target = mentions[0]
            if "revguard-orchestrator" in target:
                run_id = re.search(r"run_id=([^ ]+)", body).group(1)
                self.events.append({
                    "event_id": f"$reply-{self.counter}", "sender": target,
                    "room_id": room_id or self.settings.room_id,
                    "content": {"body": f"RUN_ACCEPTED {run_id}"},
                })
            else:
                fields = dict(
                    line.split("=", 1) for line in body.splitlines()
                    if "=" in line and not line.startswith("input=")
                )
                task = self.store.get_agent_task(fields["task_id"])
                actor = target.split(":", 1)[0].removeprefix("@")
                execution_input, _ = hydrate_server_secrets(
                    task["skill_name"], task["input"],
                    case_id=task["case_id"], store=self.store,
                )
                execute_agent_task(
                    task_id=task["task_id"], case_id=task["case_id"],
                    skill_name=task["skill_name"], skill_input=task["input"],
                    actor=actor, gateway=self.gateway, store=self.store,
                    correlation={
                        "request_id": fields["request_id"],
                        "agentteams_message_id": fields["message_id"],
                        "transport": "rest",
                    },
                    execution_input=execution_input,
                )
                self.events.append({
                    "event_id": f"$reply-{self.counter}", "sender": target,
                    "room_id": room_id or self.settings.room_id,
                    "content": {"body": f"task_id={task['task_id']} success=true"},
                })
        return event_id

    async def wait_for_event(
        self, *, since, predicate, timeout_seconds, room_id=None,
    ):
        del since, timeout_seconds
        for index, event in enumerate(self.events):
            if (room_id is None or event.get("room_id") == room_id) and predicate(event):
                return self.events.pop(index)
        return None


class ScriptedMatrixClient(MatrixClient):
    def __init__(self, settings, responses):
        super().__init__(settings)
        self.responses = list(responses)
        self.calls = []

    async def _request(self, method, path, payload, **kwargs):
        self.calls.append((method, path, payload, kwargs))
        return self.responses.pop(0)


class NoCompletionMatrixClient:
    def __init__(self, settings):
        self.settings = settings
        self.counter = 0

    async def authenticate(self):
        return None

    async def cursor(self):
        return "cursor"

    async def send_text(self, body, *, mentions=None, room_id=None):
        del body, mentions, room_id
        self.counter += 1
        return f"$event-{self.counter}"

    async def wait_for_event(self, **kwargs):
        del kwargs
        return None


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class TestMatrixSettingsAndClient(unittest.IsolatedAsyncioTestCase):
    def test_settings_from_env_and_validation_errors(self):
        values = {
            "REVGUARD_MATRIX_HOMESERVER_URL": "http://matrix.test/",
            "REVGUARD_MATRIX_ROOM_ID": "!team:test",
            "REVGUARD_MATRIX_SERVER_NAME": "test",
            "REVGUARD_MATRIX_ACCESS_TOKEN": "token",
            "REVGUARD_MATRIX_WORKER_ROOMS_JSON": '{"revguard-intake":"!dm:test"}',
            "REVGUARD_MATRIX_STAGE_TIMEOUT_SECONDS": "3",
            "REVGUARD_MATRIX_RESPONSE_TIMEOUT_SECONDS": "4",
            "REVGUARD_MATRIX_ORCHESTRATOR_TIMEOUT_SECONDS": "5",
            "REVGUARD_MATRIX_REQUIRE_ORCHESTRATOR_ACK": "false",
        }
        with patch.dict(os.environ, values, clear=True):
            settings = MatrixSettings.from_env()
        self.assertEqual(settings.homeserver_url, "http://matrix.test")
        self.assertEqual(settings.worker_rooms["revguard-intake"], "!dm:test")
        self.assertFalse(settings.require_orchestrator_ack)
        settings.validate()

        with patch.dict(
            os.environ,
            {"REVGUARD_MATRIX_WORKER_ROOMS_JSON": "not-json"},
            clear=True,
        ):
            with self.assertRaisesRegex(MatrixTransportError, "不是有效 JSON"):
                MatrixSettings.from_env()
        with patch.dict(
            os.environ,
            {"REVGUARD_MATRIX_WORKER_ROOMS_JSON": "[]"},
            clear=True,
        ):
            with self.assertRaisesRegex(MatrixTransportError, "必须是 actor"):
                MatrixSettings.from_env()
        with self.assertRaisesRegex(MatrixTransportError, "HOMESERVER.*ROOM_ID"):
            MatrixSettings("", "", "test").validate()

    async def test_client_auth_send_sync_cursor_and_wait(self):
        settings = MatrixSettings(
            "http://matrix.test", "!team:test", "test",
            username="admin", password="pw",
        )
        client = ScriptedMatrixClient(settings, [
            {"access_token": "logged-in"},
            {"event_id": "$sent"},
            {"next_batch": "s1"},
            {"next_batch": "s2"},
            {
                "next_batch": "s3",
                "rooms": {"join": {"!dm:test": {"timeline": {"events": [
                    {"event_id": "$reply", "sender": "@worker:test"},
                ]}}}},
            },
        ])
        await client.authenticate()
        self.assertEqual(client._access_token, "logged-in")
        self.assertEqual(
            await client.send_text("hello", mentions=["@worker:test"]), "$sent"
        )
        self.assertEqual(await client.cursor(), "s1")
        self.assertEqual((await client.sync(since="s1"))["next_batch"], "s2")
        event = await client.wait_for_event(
            since="s2", room_id="!dm:test", timeout_seconds=1,
            predicate=lambda item: item.get("event_id") == "$reply",
        )
        self.assertEqual(event["event_id"], "$reply")
        self.assertIn("m.mentions", client.calls[1][2])

        missing = ScriptedMatrixClient(settings, [{}])
        with self.assertRaisesRegex(MatrixTransportError, "access_token"):
            await missing.authenticate()
        no_event = ScriptedMatrixClient(
            MatrixSettings(
                "http://matrix.test", "!team:test", "test", access_token="token",
            ),
            [],
        )
        with patch("revguard.matrix_team.time.monotonic", side_effect=[0, 1]):
            self.assertIsNone(await no_event.wait_for_event(
                since="s", timeout_seconds=0.5, predicate=lambda item: True,
            ))

    async def test_client_request_success_and_structured_failures(self):
        settings = MatrixSettings(
            "http://matrix.test", "!team:test", "test", access_token="token",
        )
        client = MatrixClient(settings)
        with patch(
            "revguard.matrix_team.request.urlopen",
            return_value=JsonResponse(b'{"ok":true}'),
        ) as urlopen:
            self.assertTrue((await client._request("POST", "/ok", {"x": 1}))["ok"])
            req = urlopen.call_args.args[0]
            self.assertEqual(req.get_header("Authorization"), "Bearer token")

        structured = error.HTTPError(
            "http://matrix.test", 403, "denied", {},
            JsonResponse(b'{"errcode":"M_FORBIDDEN"}'),
        )
        with patch("revguard.matrix_team.request.urlopen", side_effect=structured):
            with self.assertRaisesRegex(MatrixTransportError, "M_FORBIDDEN"):
                await client._request("GET", "/denied", None)
        unstructured = error.HTTPError(
            "http://matrix.test", 500, "bad", {}, JsonResponse(b"not-json"),
        )
        with patch("revguard.matrix_team.request.urlopen", side_effect=unstructured):
            with self.assertRaisesRegex(MatrixTransportError, "HTTP_500"):
                await client._request("GET", "/bad", None)
        with patch("revguard.matrix_team.request.urlopen", side_effect=OSError("down")):
            with self.assertRaisesRegex(MatrixTransportError, "OSError"):
                await client._request("GET", "/down", None)

class TestMatrixTeamRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tmp = Path(self.temp.name)
        self.store = Store(tmp / "matrix.db")
        self.gateway = ToolGateway(
            ROOT / "data" / "fixtures", finance_fail_times=1,
            signing_key="matrix-team-test-signing-key-at-least-32-bytes",
            state_path=tmp / "gateway.json", verification_tamper_amount="1",
        )
        self.settings = MatrixSettings(
            homeserver_url="http://matrix.test", room_id="!revguard:test",
            server_name="test", access_token="test-token",
            worker_rooms={
                "revguard-intake": "!intake:test",
                "revguard-knowledge": "!knowledge:test",
            },
            stage_timeout_seconds=2, response_timeout_seconds=1,
            orchestrator_timeout_seconds=1,
            retry_nudge_seconds=(),
        )
        self.client = FakeMatrixClient(self.settings, self.store, self.gateway)
        self.runner = MatrixTeamRunner(
            self.store, self.gateway, output_dir=tmp / "outputs",
            report_dir=tmp / "reports", settings=self.settings, client=self.client,
        )
        spec = json.loads(
            (ROOT / "data" / "golden_cases" / "GOLDEN-008.json")
            .read_text(encoding="utf-8")
        )
        raw = spec["input"]
        self.case = Case(
            case_id=raw["case_id"], case_type=raw["case_type"], source=raw["source"],
            partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
            order_id=raw.get("order_id"), description=raw.get("description", ""),
            claim=raw.get("claim", {}),
            entities={
                "partner_id": raw.get("partner_id"),
                "partner_name": raw.get("partner_name"),
                "order_id": raw.get("order_id"), "contract_id": None,
            },
        ).to_dict()
        self.store.save_case(self.case)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_real_transport_contract_persists_input_output_and_correlations(self):
        await self.runner.run_to_human_gate(self.case)
        waiting = self.store.get_case(self.case["case_id"])
        self.assertEqual(waiting["status"], CaseStatus.WAITING_FOR_APPROVAL.value)
        self.assertEqual(waiting["execution_mode"], "AGENTTEAMS_MATRIX")
        self.assertEqual(waiting["team_run"]["status"], "WAITING_HUMAN")
        self.assertEqual(
            waiting["team_run"]["orchestrator"]["status"], "ACKNOWLEDGED"
        )
        tasks = self.store.list_agent_tasks(self.case["case_id"])
        self.assertEqual(len(tasks), 8)
        self.assertTrue(all(task["input"] and task["result"] for task in tasks))
        self.assertTrue(all(task["request_id"].startswith("REQ-AGT-") for task in tasks))
        self.assertTrue(all(task["agentteams_message_id"].startswith("$") for task in tasks))
        self.assertTrue(all(task["matrix_response_event_id"].startswith("$") for task in tasks))
        self.assertEqual({task["transport"] for task in tasks}, {"agentteams-matrix"})
        self.assertEqual({task["skill_transport"] for task in tasks}, {"rest"})
        self.assertEqual(
            next(task for task in tasks if task["assigned_actor"] == "revguard-intake")[
                "matrix_room_id"
            ],
            "!intake:test",
        )

        approval = self.store.get_approval(self.case["case_id"])
        decided = self.gateway.call(
            "workflow.decide_approval",
            {"approval_id": approval["approval_id"], "decision": "APPROVED"},
            case_id=self.case["case_id"], actor="finance.lead",
            scope=["approval:decide"],
        )["data"]
        self.store.save_approval({
            "approval_id": decided["approval_id"], "case_id": self.case["case_id"],
            **decided,
        })
        transition_case(
            self.store, waiting, CaseStatus.READY_TO_EXECUTE,
            "测试中的独立人工批准", actor="finance.lead",
        )
        await self.runner.execute_after_approval(waiting)
        final = self.store.get_case(self.case["case_id"])
        self.assertEqual(final["status"], CaseStatus.ROLLED_BACK.value)
        self.assertEqual(final["team_run"]["status"], "COMPLETED")
        all_tasks = self.store.list_agent_tasks(self.case["case_id"])
        self.assertEqual(len(all_tasks), 20)
        self.assertEqual({task["status"] for task in all_tasks}, {"SUCCEEDED"})
        agent_spans = [
            span for span in self.store.list_spans(self.case["case_id"])
            if span["kind"] == "AGENT" and span["name"].startswith("AgentTeams.")
        ]
        self.assertEqual(len(agent_spans), 21)
        self.assertTrue(all(span["duration_ms"] >= 0 for span in agent_spans))
        self.assertIn(
            "AgentTeams.OrchestratorHandshake",
            {span["name"] for span in agent_spans},
        )

    async def test_orchestrator_timeout_is_persisted_as_failed_run(self):
        settings = MatrixSettings(
            "http://matrix.test", "!team:test", "test", access_token="token",
            orchestrator_timeout_seconds=0.01,
        )
        runner = MatrixTeamRunner(
            self.store, self.gateway, output_dir=Path(self.temp.name) / "timeout-out",
            report_dir=Path(self.temp.name) / "timeout-report", settings=settings,
            client=NoCompletionMatrixClient(settings),
        )
        with self.assertRaisesRegex(MatrixTransportError, "Orchestrator"):
            await runner.run_to_human_gate(self.case)
        failed = self.store.get_case(self.case["case_id"])["team_run"]
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["orchestrator"]["status"], "TIMEOUT")

    async def test_pending_worker_is_nudged_then_times_out(self):
        settings = MatrixSettings(
            "http://matrix.test", "!team:test", "test", access_token="token",
            stage_timeout_seconds=0.03, orchestrator_timeout_seconds=0.01,
            require_orchestrator_ack=False, retry_nudge_seconds=(0,),
        )
        client = NoCompletionMatrixClient(settings)
        runner = MatrixTeamRunner(
            self.store, self.gateway, output_dir=Path(self.temp.name) / "nudge-out",
            report_dir=Path(self.temp.name) / "nudge-report", settings=settings,
            client=client,
        )
        with self.assertRaisesRegex(MatrixTransportError, "未完成"):
            await runner.run_to_human_gate(self.case)
        task = self.store.list_agent_tasks(self.case["case_id"])[0]
        self.assertEqual(len(task["matrix_retry_event_ids"]), 1)
        self.assertGreaterEqual(client.counter, 5)

    async def test_execute_failure_marks_team_run_failed(self):
        self.case["team_run"] = {"run_id": "RUN-FAIL"}
        self.store.save_case(self.case)
        with patch.object(
            McpTeamRunner, "execute_after_approval",
            new=AsyncMock(side_effect=RuntimeError("execution failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "execution failed"):
                await self.runner.execute_after_approval(self.case)
        failed = self.store.get_case(self.case["case_id"])["team_run"]
        self.assertEqual(failed["status"], "FAILED")
        self.assertEqual(failed["error"]["type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
