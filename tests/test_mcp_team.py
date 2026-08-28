from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from revguard.mcp_server import SERVER_INJECTION_REF
from revguard.mcp_team import McpTeamRunner
from revguard.mocks import ToolGateway
from revguard.models import Case, CaseStatus
from revguard.state_machine import transition_case
from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent


class TestMcpTeamRunner(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tmp = Path(self.temp.name)
        self.store = Store(tmp / "team.db")
        self.gateway = ToolGateway(
            ROOT / "data" / "fixtures",
            finance_fail_times=1,
            signing_key="mcp-team-test-signing-key-at-least-32-bytes",
            state_path=tmp / "gateway.json",
            verification_tamper_amount="1",
        )
        self.runner = McpTeamRunner(
            self.store, self.gateway,
            output_dir=tmp / "outputs", report_dir=tmp / "reports",
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
                "order_id": raw.get("order_id"),
                "contract_id": None,
            },
        ).to_dict()
        self.store.save_case(self.case)

    def case_from_golden(self, number: str) -> dict:
        spec = json.loads(
            (ROOT / "data" / "golden_cases" / f"GOLDEN-{number}.json")
            .read_text(encoding="utf-8")
        )
        raw = spec["input"]
        case = Case(
            case_id=raw["case_id"], case_type=raw["case_type"], source=raw["source"],
            partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
            order_id=raw.get("order_id"), description=raw.get("description", ""),
            claim=raw.get("claim", {}),
            entities={
                "partner_id": raw.get("partner_id"),
                "partner_name": raw.get("partner_name"),
                "order_id": raw.get("order_id"),
                "contract_id": None,
            },
        ).to_dict()
        self.store.save_case(case)
        return case

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    async def test_full_mcp_team_pauses_then_rolls_back_safely(self):
        state = await self.runner.run_to_human_gate(self.case)
        waiting = self.store.get_case(self.case["case_id"])
        self.assertEqual(waiting["status"], CaseStatus.WAITING_FOR_APPROVAL.value)
        self.assertEqual(waiting["execution_mode"], "MCP_TEAM")
        self.assertEqual(state["risk_decision"]["risk_level"], "L2")
        tasks_at_gate = self.store.list_agent_tasks(self.case["case_id"])
        self.assertEqual(len(tasks_at_gate), 8)
        self.assertEqual({item["status"] for item in tasks_at_gate}, {"SUCCEEDED"})
        self.assertGreaterEqual(len({item["assigned_actor"] for item in tasks_at_gate}), 6)

        approval = self.store.get_approval(self.case["case_id"])
        decided = self.gateway.call(
            "workflow.decide_approval",
            {"approval_id": approval["approval_id"], "decision": "APPROVED"},
            case_id=self.case["case_id"], actor="finance.lead",
            scope=["approval:decide"],
        )["data"]
        self.store.save_approval({
            "approval_id": decided["approval_id"],
            "case_id": self.case["case_id"], **decided,
        })
        self.store.audit(self.case["case_id"], "finance.lead", "APPROVAL_DECIDED", {
            "decision": decided["status"], "simulated_human": False,
        })
        waiting = self.store.get_case(self.case["case_id"])
        transition_case(
            self.store, waiting, CaseStatus.READY_TO_EXECUTE,
            "测试中的独立人工批准", actor="finance.lead",
        )
        final_state = await self.runner.execute_after_approval(waiting)

        final = self.store.get_case(self.case["case_id"])
        self.assertEqual(final["status"], CaseStatus.ROLLED_BACK.value)
        self.assertEqual(
            final_state["rollback"]["verification"]["verification_status"], "PASSED"
        )
        all_tasks = self.store.list_agent_tasks(self.case["case_id"])
        self.assertEqual(len(all_tasks), 20)
        self.assertEqual({item["status"] for item in all_tasks}, {"SUCCEEDED"})
        serialized_inputs = json.dumps(
            [item["input"] for item in all_tasks], ensure_ascii=False,
        )
        self.assertNotIn("RGC1.", serialized_inputs)
        adjust_inputs = [
            item["input"] for item in all_tasks
            if item["skill_name"] in {"LedgerAdjustSkill", "LedgerReverseSkill"}
        ]
        self.assertTrue(adjust_inputs)
        self.assertTrue(all(
            SERVER_INJECTION_REF in item.values() for item in adjust_inputs
        ))
        self.assertEqual(len(self.store.list_agent_task_results(all_tasks[-1]["task_id"])), 1)
        self.assertTrue(
            (Path(self.temp.name) / "outputs" / "case_memory" /
             f"{self.case['case_id']}.json").exists()
        )
        audit_details = [
            json.loads(item["detail"])
            for item in self.store.list_audit(self.case["case_id"])
            if item.get("detail")
        ]
        self.assertTrue(any(item.get("transport") == "mcp" for item in audit_details))
        self.assertTrue(any(item.get("simulated_human") is False for item in audit_details))

    async def test_mcp_team_stops_on_ambiguous_evidence(self):
        case = self.case_from_golden("003")
        state = await self.runner.run_to_human_gate(case)
        final = self.store.get_case(case["case_id"])
        self.assertEqual(final["status"], CaseStatus.WAITING_FOR_EVIDENCE.value)
        self.assertTrue(state["errors"])
        self.assertEqual(len(self.store.list_agent_tasks(case["case_id"])), 2)
        self.assertEqual(self.store.list_executions(case["case_id"]), [])

    async def test_mcp_team_closes_exact_match_without_action(self):
        case = self.case_from_golden("004")
        state = await self.runner.run_to_human_gate(case)
        final = self.store.get_case(case["case_id"])
        self.assertEqual(final["status"], CaseStatus.CLOSED.value)
        self.assertEqual(Decimal(state["root_cause_report"]["total_delta"]), Decimal("0"))
        self.assertEqual(self.store.list_executions(case["case_id"]), [])

    async def test_mcp_team_l1_creates_drafts_only(self):
        case = self.case_from_golden("006")
        state = await self.runner.run_to_human_gate(case)
        final = self.store.get_case(case["case_id"])
        self.assertEqual(final["status"], CaseStatus.CLOSED.value)
        self.assertEqual(
            state["verification"]["verification_status"],
            "NOT_APPLICABLE_DRAFT_ONLY",
        )
        executions = self.store.list_executions(case["case_id"])
        self.assertTrue(executions)
        self.assertEqual({item["status"] for item in executions}, {"DRAFT"})

    async def test_mcp_team_l3_escalates_without_execution(self):
        case = self.case_from_golden("007")
        state = await self.runner.run_to_human_gate(case)
        final = self.store.get_case(case["case_id"])
        self.assertEqual(final["status"], CaseStatus.CLOSED.value)
        self.assertEqual(state["risk_decision"]["risk_level"], "L3")
        self.assertEqual(self.store.list_executions(case["case_id"]), [])


if __name__ == "__main__":
    unittest.main()
