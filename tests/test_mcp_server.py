from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mcp import Client

from revguard.agent_bridge import create_agent_task
from revguard.mcp_server import build_scoped_server
from revguard.mocks import ToolGateway
from revguard.models import Case, CaseStatus, TaskStatus
from revguard.skills import SKILL_REGISTRY
from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent


class TestScopedMcpServer(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "mcp.db")
        self.gateway = ToolGateway(
            ROOT / "data" / "fixtures",
            finance_fail_times=0,
            signing_key="mcp-test-signing-key-that-is-long-enough",
            state_path=Path(self.temp.name) / "gateway.json",
        )
        self.case = Case(
            case_id="CASE-MCP-001",
            case_type="COMMISSION_UNDERPAYMENT",
            source="SYNTHETIC_TEST",
            partner_id="AGT-10001",
            order_id="EZ202608001",
        ).to_dict()
        self.store.save_case(self.case)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _task(self) -> dict:
        skill_input = {"raw_case": self.case}
        task = create_agent_task(self.case, "CaseNormalizeSkill", skill_input)
        self.store.save_agent_task(task)
        return task

    async def test_tools_list_is_actor_scoped_and_reuses_skill_schema(self):
        server = build_scoped_server(
            actor="revguard-intake", store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            listed = await client.list_tools()
        names = [tool.name for tool in listed.tools]
        self.assertEqual(names, ["CaseNormalizeSkill", "EntityResolveSkill"])
        normalize = next(tool for tool in listed.tools
                         if tool.name == "CaseNormalizeSkill")
        self.assertEqual(
            normalize.input_schema["properties"]["input"],
            SKILL_REGISTRY["CaseNormalizeSkill"]["input_schema"],
        )
        self.assertEqual(
            normalize.meta["revguard/assignedActor"], "revguard-intake"
        )
        self.assertTrue(normalize.meta["revguard/requiresTaskBinding"])

    async def test_bound_task_executes_and_persists_mcp_evidence(self):
        task = self._task()
        server = build_scoped_server(
            actor="revguard-intake", store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            result = await client.call_tool("CaseNormalizeSkill", {
                "case_id": self.case["case_id"],
                "task_id": task["task_id"],
                "input": task["input"],
                "request_id": "REQ-MCP-TEST-001",
                "agentteams_message_id": "$matrix-mcp-test-001",
            })
        self.assertFalse(result.is_error)
        self.assertTrue(result.structured_content["success"])
        self.assertEqual(result.structured_content["task_id"], task["task_id"])
        self.assertEqual(
            self.store.get_agent_task(task["task_id"])["status"],
            TaskStatus.SUCCEEDED.value,
        )
        self.assertEqual(
            self.store.list_agent_task_results(task["task_id"])[0]["status"],
            TaskStatus.SUCCEEDED.value,
        )
        events = self.store.list_audit(self.case["case_id"])
        succeeded = next(event for event in events
                         if event["event"] == "AGENT_TASK_SUCCEEDED")
        self.assertEqual(json.loads(succeeded["detail"])["transport"], "mcp")

    async def test_unlisted_skill_call_is_rejected_even_if_client_sends_it(self):
        server = build_scoped_server(
            actor="revguard-evidence", store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            result = await client.call_tool("LedgerAdjustSkill", {
                "case_id": self.case["case_id"],
                "task_id": "TASK-NOT-ALLOWED",
                "input": {},
                "request_id": "REQ-MCP-SCOPE-001",
            })
        self.assertTrue(result.is_error)
        self.assertIn("SKILL_NOT_ALLOWED", result.content[0].text)
        self.assertEqual(
            self.store.list_audit(self.case["case_id"])[-1]["event"],
            "MCP_SCOPE_REJECTED",
        )

    async def test_missing_task_binding_is_a_model_visible_error(self):
        server = build_scoped_server(
            actor="revguard-intake", store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            result = await client.call_tool("CaseNormalizeSkill", {
                "case_id": self.case["case_id"],
                "input": {"raw_case": self.case},
            })
        self.assertTrue(result.is_error)
        self.assertIn("task_id", result.content[0].text)

    async def test_mcp_rejects_raw_capability_token_in_model_input(self):
        case = Case(
            case_id="CASE-MCP-SECRET-001",
            case_type="COMMISSION_UNDERPAYMENT",
            source="SYNTHETIC_TEST",
            status=CaseStatus.EXECUTING.value,
        ).to_dict()
        self.store.save_case(case)
        skill_input = {
            "action_id": "ACT-MCP-SECRET-001",
            "approval_token": "RGC1.raw-model-visible-token",
            "policy_version": "2026-Q3",
            "idempotency_key": "CASE-MCP-SECRET-001:SALES_COMMISSION",
        }
        task = create_agent_task(case, "LedgerAdjustSkill", skill_input)
        self.store.save_agent_task(task)
        server = build_scoped_server(
            actor="revguard-executor", store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            result = await client.call_tool("LedgerAdjustSkill", {
                "case_id": case["case_id"],
                "task_id": task["task_id"],
                "input": skill_input,
            })
        self.assertTrue(result.is_error)
        self.assertIn("必须使用服务端审批凭证引用", result.content[0].text)
        self.assertEqual(
            self.store.get_agent_task(task["task_id"])["status"],
            TaskStatus.PENDING.value,
        )


if __name__ == "__main__":
    unittest.main()
