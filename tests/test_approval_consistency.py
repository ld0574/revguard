"""Approval and transition failure contracts on disposable databases."""
from __future__ import annotations

import copy
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

from revguard.hitl import HumanIdentity, issue_human_action_assertion
from revguard.mocks import ToolGateway
from revguard.models import CaseStatus
from revguard.money_journal import MoneyTransaction
from revguard.orchestrator import Orchestrator
from revguard.state_machine import StaleCaseTransition, transition_case
from tests import test_runtime_safety as fixtures


class TestApprovalConsistency(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TestRuntimeSafety.setUp(self)

    client = fixtures.TestRuntimeSafety.client

    def pending_case(self):
        case = self.store.get_case(self.case_id)
        Orchestrator(self.store, self.gateway, output_dir=self.temp.name,
                     report_dir=self.temp.name, approval_mode="wait").run_case(case)
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["status"], "WAITING_FOR_APPROVAL")
        case["execution_mode"] = "AGENTTEAMS_MATRIX"
        self.store.save_case(case)
        return case

    def human_headers(self, action):
        approval = self.store.get_approval(self.case_id)
        token = issue_human_action_assertion(
            fixtures.api_module.HITL_SIGNER,
            HumanIdentity(sub="@finance-lead:test", actor="finance.lead",
                          display_name="测试财务负责人", auth_time=int(time.time())),
            case_id=self.case_id, approval_id=approval["approval_id"], action=action,
            ttl_seconds=120,
        )
        return {"Authorization": "Bearer " + token}

    def snapshot(self):
        with self.gateway.journal.transaction() as tx:
            state = tx.state()
        return {"case": self.store.get_case(self.case_id), "gateway": state,
                "approval": self.store.get_approval(self.case_id),
                "tasks": self.store.list_agent_tasks(self.case_id),
                "audit": self.store.list_audit(self.case_id),
                "executions": self.store.list_executions(self.case_id)}

    def reject_case_write(self, status):
        with self.gateway.journal.transaction() as tx:
            if tx.postgres:
                tx.execute("""CREATE FUNCTION reject_case_write() RETURNS trigger AS $$
                    BEGIN IF NEW.status=TG_ARGV[0] THEN RAISE EXCEPTION 'injected case failure';
                    END IF; RETURN NEW; END; $$ LANGUAGE plpgsql""")
                # status comes only from the tests' literal CaseStatus values.
                tx.execute("CREATE TRIGGER reject_case_write BEFORE INSERT ON cases "
                           "FOR EACH ROW EXECUTE FUNCTION reject_case_write('" + status + "')")
            else:
                tx.execute("CREATE TRIGGER reject_case_write BEFORE INSERT ON cases "
                           "WHEN NEW.status='" + status + "' BEGIN "
                           "SELECT RAISE(ABORT, 'injected case failure'); END")

    def allow_case_write(self):
        with self.gateway.journal.transaction() as tx:
            tx.execute("DROP TRIGGER reject_case_write" + (" ON cases" if tx.postgres else ""))

    async def test_approval_case_write_failure_rolls_back_every_decision_projection(self):
        self.pending_case()
        before = self.snapshot()
        self.reject_case_write("READY_TO_EXECUTE")
        transport = fixtures.httpx.ASGITransport(app=fixtures.api_module.app, raise_app_exceptions=False)
        async with fixtures.httpx.AsyncClient(transport=transport, base_url="http://isolated") as client:
            result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                       json={"decision": "APPROVED"}, headers=self.human_headers("APPROVED"))
        self.assertIn(result.status_code, {500, 503}, result.text)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gateway._approvals[before["approval"]["approval_id"]]["status"], "PENDING")
        self.allow_case_write()
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            async with self.client() as client:
                result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                           json={"decision": "APPROVED"}, headers=self.human_headers("APPROVED"))
        self.assertEqual(result.status_code, 202, result.text)
        spawn.assert_called_once_with(self.case_id, "EXECUTION")
        self.assertEqual(self.store.get_case(self.case_id)["status"], "READY_TO_EXECUTE")
        self.assertEqual(self.store.list_executions(self.case_id), [])

    def test_state_write_failure_preserves_input_and_durable_audit(self):
        case = self.store.get_case(self.case_id)
        case["description"] = "caller changes must survive a successful transition"
        before_input = copy.deepcopy(case)
        before = self.snapshot()
        self.reject_case_write("NORMALIZING")
        with self.assertRaises(Exception):
            transition_case(self.store, case, CaseStatus.NORMALIZING, "test write failure")
        self.assertEqual(case, before_input)
        self.assertEqual(self.snapshot(), before)
        self.allow_case_write()
        transition_case(self.store, case, CaseStatus.NORMALIZING, "successful retry")
        self.assertEqual(self.store.get_case(self.case_id), case)
        self.assertEqual(case["description"], before_input["description"])

    def test_late_transition_audit_failure_rolls_back_state_and_preserves_input(self):
        case = self.store.get_case(self.case_id)
        before = self.snapshot()
        before_input = copy.deepcopy(case)
        with patch.object(MoneyTransaction, "audit", side_effect=RuntimeError("injected audit failure")):
            with self.assertRaises(RuntimeError):
                transition_case(self.store, case, CaseStatus.NORMALIZING, "late audit failure")
        self.assertEqual(case, before_input)
        self.assertEqual(self.snapshot(), before)

    def test_state_generation_rejects_stale_claim_even_after_return_to_same_status(self):
        case = self.store.get_case(self.case_id)
        case["status"] = "EXECUTING"
        self.store.save_case(case)
        stale = copy.deepcopy(case)
        transition_case(self.store, case, CaseStatus.RECOVERY_REQUIRED, "unknown")
        transition_case(self.store, case, CaseStatus.EXECUTING, "reconciled")
        before = self.snapshot()
        with self.assertRaises(StaleCaseTransition):
            transition_case(self.store, stale, CaseStatus.VERIFYING, "late callback")
        self.assertEqual(self.snapshot(), before)

    async def test_rejection_failure_preserves_pending_tasks_and_all_approval_state(self):
        case = self.pending_case()
        from revguard.agent_bridge import create_agent_task
        # An unfinished task from the preceding risk stage must be cancelled
        # together with rejection, including after an interrupted handoff.
        approval = self.store.get_approval(self.case_id)
        task = create_agent_task({**case, "status": "RISK_REVIEW"}, "ApprovalRouteSkill", {
            "risk": case["risk_decision"], "amount": approval["amount"],
            "currency": approval["currency"], "component_quota": approval["component_quota"],
            "action_summary": "isolated unfinished approval handoff",
        })
        self.store.save_agent_task(task)
        before = self.snapshot()
        self.reject_case_write("REJECTED")
        async with self.client() as client:
            result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                       json={"decision": "REJECTED"}, headers=self.human_headers("REJECTED"))
        self.assertEqual(result.status_code, 503, result.text)
        self.assertEqual(self.snapshot(), before)
        self.allow_case_write()
        runner = AsyncMock()
        with patch.object(fixtures.api_module, "_team_runner_for_case", return_value=runner):
            async with self.client() as client:
                result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                           json={"decision": "REJECTED"}, headers=self.human_headers("REJECTED"))
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(self.store.get_case(self.case_id)["status"], "REJECTED")
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "CANCELLED")
        self.assertEqual(self.store.list_executions(self.case_id), [])

    async def test_execution_start_failure_retains_approved_state_for_human_recovery(self):
        self.pending_case()
        with patch.object(fixtures.api_module, "_spawn_team_background"):
            async with self.client() as client:
                result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                           json={"decision": "APPROVED"}, headers=self.human_headers("APPROVED"))
                self.assertEqual(result.status_code, 202, result.text)
        runner = AsyncMock()
        runner.execute_after_approval.side_effect = ConnectionError("injected startup failure")
        with patch.object(fixtures.api_module, "_team_runner_for_case", return_value=runner):
            await fixtures.api_module._run_team_background(self.case_id, "EXECUTION")
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["status"], "READY_TO_EXECUTE")
        self.assertEqual(case["team_run"]["status"], "FAILED")
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            async with self.client() as client:
                result = await client.post(f"/api/v1/cases/{self.case_id}/team/resume", headers=self.human_headers("RESUME"))
                self.assertEqual(result.status_code, 202, result.text)
        spawn.assert_called_once_with(self.case_id, "EXECUTION")
        self.assertEqual(self.store.list_executions(self.case_id), [])

    async def test_reference_mcp_completion_closes_durable_approval_queue(self):
        case = self.pending_case()
        case["execution_mode"] = "MCP_TEAM"
        self.store.save_case(case)
        async with self.client() as client:
            result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                       json={"decision": "APPROVED"}, headers=self.human_headers("APPROVED"))
        self.assertEqual(result.status_code, 200, result.text)
        case = self.store.get_case(self.case_id)
        self.assertEqual(case["status"], "CLOSED")
        self.assertEqual(case["team_run"]["status"], "COMPLETED")
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        from revguard.runtime_barrier import assert_recording_quiescent
        assert_recording_quiescent(self.store, self.gateway.journal, case_id=self.case_id)

    def test_competing_human_decisions_commit_only_once(self):
        case = self.pending_case()
        second_gateway = ToolGateway(fixtures.ROOT / "data/fixtures", store=self.store,
                                     signing_key=fixtures.api_module.SIGNING_KEY)
        approval = self.store.get_approval(self.case_id)
        def decide(gateway, decision):
            try:
                gateway.decide_case_approval(copy.deepcopy(case), {
                    "approval_id": approval["approval_id"], "decision": decision,
                    "human_subject": "@finance-lead:test",
                }, actor="finance.lead", assertion_ref="test-proof-ref")
                return decision
            except StaleCaseTransition:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(decide, self.gateway, "APPROVED")
            second = pool.submit(decide, second_gateway, "REJECTED")
            results = [first.result(timeout=10), second.result(timeout=10)]
        self.assertEqual(results.count("CONFLICT"), 1)
        self.assertEqual(len([e for e in self.store.list_audit(self.case_id) if e["event"] == "APPROVAL_DECIDED"]), 1)
        self.assertEqual(self.store.list_executions(self.case_id), [])

    async def test_committed_approval_lost_reply_is_readable_after_restart_and_recoverable(self):
        self.pending_case()
        original_transaction = self.gateway.journal.transaction
        @contextmanager
        def lose_commit_reply():
            with original_transaction() as tx:
                yield tx
            raise ConnectionError("injected lost commit acknowledgement")
        async with self.client() as client:
            with patch.object(self.gateway.journal, "transaction", lose_commit_reply):
                result = await client.post(f"/api/v1/cases/{self.case_id}/approval",
                                           json={"decision": "APPROVED"}, headers=self.human_headers("APPROVED"))
            self.assertEqual(result.status_code, 503, result.text)
            case = self.store.get_case(self.case_id)
            self.assertEqual(case["status"], "READY_TO_EXECUTE")
            self.assertEqual(case["team_run"]["status"], "QUEUED")
            self.assertEqual(self.store.get_approval(self.case_id)["status"], "APPROVED")
            restarted = ToolGateway(fixtures.ROOT / "data/fixtures", store=self.store,
                                    signing_key=fixtures.api_module.SIGNING_KEY)
            with patch.object(fixtures.api_module, "gateway", restarted):
                active = await client.post(f"/api/v1/cases/{self.case_id}/team/resume", headers=self.human_headers("RESUME"))
                self.assertEqual(active.status_code, 409, active.text)
                case["team_run"]["updated_at"] = "2020-01-01T00:00:00Z"
                self.store.save_case(case)
                with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
                    result = await client.post(f"/api/v1/cases/{self.case_id}/team/resume", headers=self.human_headers("RESUME"))
                self.assertEqual(result.status_code, 202, result.text)
                spawn.assert_called_once_with(self.case_id, "EXECUTION")
        self.assertEqual(self.store.get_case(self.case_id)["status"], "READY_TO_EXECUTE")
        self.assertEqual(self.store.list_executions(self.case_id), [])
