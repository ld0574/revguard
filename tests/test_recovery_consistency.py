"""Resume transaction and delayed Worker contracts on disposable databases."""
from __future__ import annotations

import asyncio
import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from revguard import agent_bridge
from revguard.mocks import ToolError
from revguard.models import CaseStatus
from revguard.money_journal import MoneyTransaction
from revguard.state_machine import transition_case
from tests import test_approval_consistency as approval_contracts
from tests import test_runtime_safety as fixtures


class TestRecoveryConsistency(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TestRuntimeSafety.setUp(self)

    client = fixtures.TestRuntimeSafety.client
    pending_case = approval_contracts.TestApprovalConsistency.pending_case
    human_headers = approval_contracts.TestApprovalConsistency.human_headers
    reject_case_write = approval_contracts.TestApprovalConsistency.reject_case_write
    allow_case_write = approval_contracts.TestApprovalConsistency.allow_case_write

    def approved_case(self):
        case = self.pending_case()
        approval = self.store.get_approval(self.case_id)
        self.gateway.decide_case_approval(case, {
            "approval_id": approval["approval_id"], "decision": "APPROVED",
        }, actor="finance.lead", assertion_ref="isolated-recovery-test")
        transition_case(self.store, case, CaseStatus.EXECUTING, "isolated worker start")
        return case

    def interrupted(self, case):
        transition_case(self.store, case, CaseStatus.RECOVERY_REQUIRED, "isolated interrupted process")
        case["team_run"].update(status="FAILED", updated_at="2020-01-01T00:00:00Z")
        self.store.save_case(case)
        return case

    def snapshot(self):
        result = approval_contracts.TestApprovalConsistency.snapshot(self)
        with self.gateway.journal.transaction() as tx:
            result["operations"] = [dict(r) for r in tx.execute("SELECT * FROM money_operations ORDER BY operation_id").fetchall()]
            result["holds"] = [dict(r) for r in tx.execute("SELECT * FROM money_holds ORDER BY channel,case_id").fetchall()]
        return result

    def running_task(self, case):
        task = agent_bridge.create_agent_task(case, "PermissionCheckSkill", {
            "action_type": "LEDGER_ADJUST", "risk": case["risk_decision"],
        })
        self.store.save_agent_task(task)
        return self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")

    def independent_gateway(self):
        from revguard.mocks import ToolGateway
        from tests.test_workflow_concurrency import TestWorkflowConcurrency
        store = TestWorkflowConcurrency.independent_store(self)
        return ToolGateway(fixtures.ROOT / "data/fixtures", store=store,
                           signing_key=fixtures.api_module.SIGNING_KEY)

    async def resume(self):
        transport = fixtures.httpx.ASGITransport(app=fixtures.api_module.app, raise_app_exceptions=False)
        async with fixtures.httpx.AsyncClient(transport=transport, base_url="http://isolated") as client:
            return await client.post(f"/api/v1/cases/{self.case_id}/team/resume", headers=self.human_headers("RESUME"))

    async def test_failed_resume_keeps_hold_task_capability_and_case_together(self):
        case = self.approved_case()
        self.running_task(case)
        self.interrupted(case)
        self.gateway.journal.prepare("unfinished-intent", self.case_id, "order:test", "commission.submit_adjustment", "test-digest")
        self.gateway.journal.hold("order:test", self.case_id, "unknown")
        before = self.snapshot()
        self.reject_case_write("EXECUTING")
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
        self.assertIn(response.status_code, {500, 503}, response.text)
        spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.allow_case_write()
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        spawn.assert_called_once_with(self.case_id, "EXECUTION")

    async def test_resume_fences_old_running_tasks_and_rejects_duplicate_recovery(self):
        case = self.approved_case()
        task = self.running_task(case)
        self.interrupted(case)
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
            duplicate = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        spawn.assert_called_once_with(self.case_id, "EXECUTION")
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "CANCELLED")
        self.assertEqual(self.store.get_case(self.case_id)["team_run"]["status"], "QUEUED")

    async def test_delayed_claimed_worker_cannot_post_after_human_resume(self):
        case = self.approved_case()
        approval = self.store.get_approval(self.case_id)
        draft = self.gateway.call("commission.create_adjustment_draft", {
            "case_id": self.case_id, "order_id": case["order_id"],
            "component": "SALES_COMMISSION", "amount": "20", "currency": "KES",
        }, case_id=self.case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
        task = agent_bridge.create_agent_task(case, "LedgerAdjustSkill", {
            "action_id": draft["action_id"], "approval_token": approval["approval_token"],
            "policy_version": "POL-2026-01", "idempotency_key": self.case_id + ":delayed-submit",
        })
        self.store.save_agent_task(task)
        entered, release = threading.Event(), threading.Event()
        invoke = agent_bridge.invoke_skill
        def paused(*args, **kwargs):
            entered.set()
            if not release.wait(15):
                raise TimeoutError("isolated worker was not released")
            return invoke(*args, **kwargs)
        def execute():
            try:
                return agent_bridge.execute_agent_task(
                    task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                    skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
                )
            except (ValueError, ToolError) as exc:
                return {"error": type(exc).__name__}
        with patch.object(agent_bridge, "invoke_skill", side_effect=paused), ThreadPoolExecutor(max_workers=1) as pool:
            worker = pool.submit(execute)
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                self.interrupted(case)
                with patch.object(fixtures.api_module, "_spawn_team_background"):
                    response = await self.resume()
                self.assertEqual(response.status_code, 202, response.text)
                state = copy.deepcopy(self.gateway._ledger)
            finally:
                release.set()
            await asyncio.to_thread(worker.result, 10)
        self.assertEqual(self.gateway._ledger, state)
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "CANCELLED")

    async def test_old_background_error_cannot_freeze_a_new_recovery_generation(self):
        case = self.approved_case()
        async def interrupted_runner(snapshot):
            self.interrupted(self.store.get_case(self.case_id))
            with patch.object(fixtures.api_module, "_spawn_team_background"):
                response = await self.resume()
            self.assertEqual(response.status_code, 202, response.text)
            raise RuntimeError("old process reports an error after new recovery")
        from unittest.mock import Mock
        runner = Mock()
        runner.execute_after_approval = interrupted_runner
        with patch.object(fixtures.api_module, "_team_runner_for_case", return_value=runner):
            await fixtures.api_module._run_team_background(self.case_id, "EXECUTION")
        self.assertEqual(self.store.get_case(case["case_id"])["status"], "EXECUTING")
        self.assertEqual(self.store.get_case(case["case_id"])["team_run"]["status"], "QUEUED")

    async def test_final_recovery_audit_failure_rolls_back_every_write(self):
        case = self.approved_case()
        self.running_task(case)
        self.interrupted(case)
        self.gateway.journal.hold("order:test", self.case_id, "unknown")
        before = self.snapshot()
        audit = MoneyTransaction.audit
        def fail_last(tx, case_id, event, detail, **kwargs):
            if event == "TEAM_RUN_RESUME_REQUESTED":
                raise RuntimeError("injected final recovery audit failure")
            return audit(tx, case_id, event, detail, **kwargs)
        with patch.object(MoneyTransaction, "audit", fail_last), patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
        self.assertEqual(response.status_code, 503, response.text)
        spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.gateway._approvals, before["gateway"]["approvals"])

    async def test_dispatch_failure_remains_immediately_recoverable(self):
        self.interrupted(self.approved_case())
        with patch.object(fixtures.api_module, "_spawn_team_background", side_effect=RuntimeError("isolated spawn failure")):
            response = await self.resume()
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(self.store.get_case(self.case_id)["status"], "RECOVERY_REQUIRED")
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        spawn.assert_called_once_with(self.case_id, "EXECUTION")

    def rollback_case(self):
        case = self.approved_case()
        approval = self.store.get_approval(self.case_id)
        components = list(approval["component_quota"])
        self.assertGreaterEqual(len(components), 2)
        for component in components[:2]:
            draft = self.gateway.call("commission.create_adjustment_draft", {
                "case_id": self.case_id, "order_id": case["order_id"], "component": component,
                "amount": "20", "currency": "KES",
            }, case_id=self.case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
            submitted = self.gateway.call("commission.submit_adjustment", {
                "action_id": draft["action_id"], "approval_token": approval["approval_token"],
            }, case_id=self.case_id, actor="revguard-executor", scope=["commission:write"],
                idempotency_key=self.case_id + ":" + component)
            self.assertTrue(submitted["success"], submitted.get("error"))
        transition_case(self.store, case, CaseStatus.VERIFYING, "isolated verification")
        self.store.save_verification(self.case_id, {"rollback_required": True, "verification_status": "FAILED"})
        transition_case(self.store, case, CaseStatus.ROLLBACK_REQUIRED, "isolated compensation")
        return self.interrupted(case)

    async def test_second_rollback_renewal_failure_preserves_all_original_tokens(self):
        self.rollback_case()
        before = self.snapshot()
        renew = self.gateway._tool_workflow_renew_rollback_capability
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ToolError("TOOL_UNAVAILABLE", "isolated second renewal failure")
            return renew(*args, **kwargs)
        with patch.object(self.gateway, "_tool_workflow_renew_rollback_capability", side_effect=fail_second):
            with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
                response = await self.resume()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(calls, 2)
        spawn.assert_not_called()
        self.assertEqual(self.snapshot(), before)
        with patch.object(fixtures.api_module, "_spawn_team_background") as spawn:
            response = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        spawn.assert_called_once_with(self.case_id, "ROLLBACK")
        renewed = self.store.list_executions(self.case_id)
        self.assertNotEqual({e["rollback_token"] for e in renewed}, {e["rollback_token"] for e in before["executions"]})
        self.gateway.journal.reconcile(self.case_id)
        self.assertEqual(self.store.list_executions(self.case_id), renewed)

    async def test_real_mcp_closure_after_fencing_leaves_no_running_task(self):
        from revguard.mcp_team import McpTeamRunner
        from revguard.runtime_barrier import assert_recording_quiescent
        case = self.approved_case()
        old = self.running_task(case)
        self.interrupted(case)
        with patch.object(fixtures.api_module, "_spawn_team_background"):
            response = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        runner = McpTeamRunner(self.store, self.gateway, output_dir=self.temp.name, report_dir=self.temp.name)
        state = await runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(state["final_status"], "CLOSED")
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        self.assertEqual(self.store.get_agent_task(old["task_id"])["status"], "CANCELLED")
        self.assertEqual(len([t for t in self.store.list_agent_tasks(self.case_id)
                             if t["skill_name"] == "PermissionCheckSkill" and t["status"] == "SUCCEEDED"]), 1)
        assert_recording_quiescent(self.store, self.gateway.journal, case_id=self.case_id)

    async def test_committed_money_recovery_checks_read_only_permission_without_new_quota(self):
        from revguard.mcp_team import McpTeamRunner
        case = self.approved_case()
        runner = McpTeamRunner(self.store, self.gateway, output_dir=self.temp.name, report_dir=self.temp.name)
        call = self.gateway.call
        def lose_response(name, *args, **kwargs):
            result = call(name, *args, **kwargs)
            if name == "commission.submit_adjustment" and result["success"]:
                raise TimeoutError("isolated committed response lost")
            return result
        with patch.object(self.gateway, "call", side_effect=lose_response):
            with self.assertRaises(Exception):
                await runner.execute_after_approval(case)
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        self.interrupted(self.store.get_case(self.case_id))
        with patch.object(fixtures.api_module, "_spawn_team_background"):
            response = await self.resume()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertFalse(self.store.get_approval(self.case_id)["approval_token"])
        state = await runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(state["final_status"], "CLOSED")
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        permissions = [t for t in self.store.list_agent_tasks(self.case_id) if t["skill_name"] == "PermissionCheckSkill"]
        self.assertEqual(permissions[-1]["input"]["action_type"], "VERIFY_COMMITTED")
        self.assertEqual(permissions[-1]["status"], "SUCCEEDED")

    def test_read_only_permission_rejects_uncommitted_money(self):
        case = self.approved_case()
        task = agent_bridge.create_agent_task(case, "PermissionCheckSkill", {
            "action_type": "VERIFY_COMMITTED", "risk": case["risk_decision"],
            "approval": {"status": "APPROVED"},
        })
        self.store.save_agent_task(task)
        with self.assertRaises(ToolError):
            agent_bridge.execute_agent_task(
                task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
            )
        self.assertEqual(self.store.list_executions(self.case_id), [])

    def test_competing_recovery_transactions_commit_only_one_generation(self):
        from revguard.recovery import resume_case_run
        case = self.interrupted(self.approved_case())
        other = self.independent_gateway()
        approval = self.store.get_approval(self.case_id)
        def resume(gateway):
            try:
                resume_case_run(gateway, copy.deepcopy(case), approval=approval, verification={}, rollback=False,
                                actor="finance.lead", subject="isolated", assertion_ref="isolated", age_seconds=600)
                return "QUEUED"
            except ValueError:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(resume, gateway) for gateway in (self.gateway, other)]
            self.assertCountEqual([future.result(timeout=10) for future in futures], ["QUEUED", "CONFLICT"])
        self.assertEqual(self.store.get_case(self.case_id)["_recovery_generation"], 1)
        self.assertEqual(len([e for e in self.store.list_audit(self.case_id) if e["event"] == "TEAM_RUN_RESUME_REQUESTED"]), 1)

    def test_independent_recovery_between_intent_and_effect_blocks_old_worker(self):
        from revguard.recovery import resume_case_run
        case = self.approved_case()
        other = self.independent_gateway()
        approval = self.store.get_approval(self.case_id)
        draft = self.gateway.call("commission.create_adjustment_draft", {
            "case_id": self.case_id, "order_id": case["order_id"],
            "component": "SALES_COMMISSION", "amount": "20", "currency": "KES",
        }, case_id=self.case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
        task = agent_bridge.create_agent_task(case, "LedgerAdjustSkill", {
            "action_id": draft["action_id"], "approval_token": approval["approval_token"],
            "policy_version": "POL-2026-01", "idempotency_key": self.case_id + ":intent-gap",
        })
        self.store.save_agent_task(task)
        prepare = self.gateway.journal.prepare
        def recover_after_intent(*args, **kwargs):
            operation = prepare(*args, **kwargs)
            self.interrupted(case)
            resume_case_run(other, case, approval=approval, verification={}, rollback=False,
                            actor="finance.lead", subject="isolated", assertion_ref="isolated", age_seconds=600)
            return operation
        with patch.object(self.gateway.journal, "prepare", side_effect=recover_after_intent):
            with self.assertRaises(ValueError):
                agent_bridge.execute_agent_task(
                    task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                    skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
                )
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "CANCELLED")
        operation = self.gateway.journal.resolve(self.case_id + ":intent-gap", self.case_id)
        self.assertEqual(operation["status"], "NOT_COMMITTED")
        self.assertEqual(operation["generation"], 2)
