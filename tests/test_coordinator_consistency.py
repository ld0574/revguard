"""Late coordinator projections must not replace a recovered generation."""
from __future__ import annotations

import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from revguard.mcp_team import McpTeamRunner
from revguard.models import CaseStatus
from revguard.money_journal import MoneyTransaction
from revguard.state_machine import StaleCaseTransition
from revguard.workflow_persistence import commit_case_stage
from tests import test_recovery_consistency as recovery
from tests import test_workflow_concurrency as workflow


class TestCoordinatorConsistency(unittest.IsolatedAsyncioTestCase):
    setUp = recovery.TestRecoveryConsistency.setUp
    client = recovery.TestRecoveryConsistency.client
    pending_case = recovery.TestRecoveryConsistency.pending_case
    human_headers = recovery.TestRecoveryConsistency.human_headers
    approved_case = recovery.TestRecoveryConsistency.approved_case
    interrupted = recovery.TestRecoveryConsistency.interrupted
    resume = recovery.TestRecoveryConsistency.resume
    reject_case_write = recovery.TestRecoveryConsistency.reject_case_write
    allow_case_write = recovery.TestRecoveryConsistency.allow_case_write
    independent_store = workflow.TestWorkflowConcurrency.independent_store

    def projections(self):
        return {"case": self.store.get_case(self.case_id),
                "evidence": self.store.list_evidence(self.case_id),
                "approval": self.store.get_approval(self.case_id),
                "verification": self.store.get_verification(self.case_id),
                "executions": self.store.list_executions(self.case_id),
                "audit": self.store.list_audit(self.case_id)}

    def stage_payload(self):
        return {"evidence": [{"case_id": self.case_id, "evidence_id": "EV-stage",
                              "type": "ORDER", "payload": {"source": "isolated"}}],
                "approval": {"case_id": self.case_id, "approval_id": "APR-stage", "status": "PENDING"},
                "execution": {"case_id": self.case_id, "action_id": "ACT-stage", "amount": "1",
                              "currency": "KES", "status": "DRAFT_CREATED"},
                "verification": {"verification_status": "DRAFT_ONLY", "variance": "0"},
                "audit": ("revguard-orchestrator", "ISOLATED_STAGE", {"source": "test"})}

    def runner(self):
        return McpTeamRunner(self.store, self.gateway, output_dir=self.temp.name,
                             report_dir=self.temp.name)

    def test_case_write_failure_rolls_back_all_projections_with_and_without_transition(self):
        case = self.store.get_case(self.case_id)
        case["description"] = "prepared caller fields"
        before_input = copy.deepcopy(case)
        before = self.projections()
        for to in (None, CaseStatus.NORMALIZING):
            with self.subTest(to=to):
                self.reject_case_write(to.value if to else case["status"])
                try:
                    with self.assertRaisesRegex(Exception, "injected case failure"):
                        commit_case_stage(self.store, case, to=to, **self.stage_payload())
                finally:
                    self.allow_case_write()
                    with self.gateway.journal.transaction() as tx:
                        if tx.postgres:
                            tx.execute("DROP FUNCTION reject_case_write()")
                self.assertEqual(case, before_input)
                self.assertEqual(self.projections(), before)
        commit_case_stage(self.store, case, to=CaseStatus.NORMALIZING, **self.stage_payload())
        self.assertEqual(case, self.store.get_case(self.case_id))
        self.assertEqual(case["description"], before_input["description"])
        self.assertEqual(len(self.store.list_evidence(self.case_id)), 1)
        self.assertEqual(len(self.store.list_executions(self.case_id)), 1)
        self.assertEqual(self.store.get_approval(self.case_id)["approval_id"], "APR-stage")
        self.assertEqual(self.store.get_verification(self.case_id)["verification_status"], "DRAFT_ONLY")

    def test_final_transition_audit_failure_rolls_back_stage_and_caller(self):
        case = self.store.get_case(self.case_id)
        before_input, before = copy.deepcopy(case), self.projections()
        audit = MoneyTransaction.audit

        def fail_transition(tx, case_id, event, *args, **kwargs):
            if event == "STATE_TRANSITION":
                raise RuntimeError("injected final audit failure")
            return audit(tx, case_id, event, *args, **kwargs)

        with patch.object(MoneyTransaction, "audit", new=fail_transition):
            with self.assertRaisesRegex(RuntimeError, "injected final audit failure"):
                commit_case_stage(self.store, case, to=CaseStatus.NORMALIZING, **self.stage_payload())
        self.assertEqual(case, before_input)
        self.assertEqual(self.projections(), before)

    def test_stale_revision_and_recording_reject_every_projection_before_writes(self):
        for field, value in (("description", "newer case revision"), ("recording_id", "new-recording")):
            with self.subTest(field=field):
                old = self.store.get_case(self.case_id)
                current = copy.deepcopy(old)
                current[field] = value
                if field == "recording_id":
                    # Recording replacement is an internal transaction participant.
                    with self.gateway.journal.transaction() as tx:
                        self.store._save_case_with_conn(tx.conn, current, recording_replace=True)
                else:
                    self.store.save_case(current)
                before = self.projections()
                with self.assertRaises(StaleCaseTransition):
                    commit_case_stage(self.store, old, **self.stage_payload())
                self.assertEqual(self.projections(), before)

    def test_independent_coordinators_commit_one_complete_stage(self):
        case = self.store.get_case(self.case_id)
        second, start = self.independent_store(), threading.Barrier(2)

        def commit(store, label):
            snapshot = copy.deepcopy(case)
            snapshot["description"] = label
            payload = self.stage_payload()
            payload["verification"]["winner"] = label
            start.wait(timeout=10)
            try:
                commit_case_stage(store, snapshot, to=CaseStatus.NORMALIZING, **payload)
                return label
            except StaleCaseTransition:
                return "CONFLICT"

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(commit, store, label) for store, label in ((self.store, "first"), (second, "second"))]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results.count("CONFLICT"), 1)
        winner = self.store.get_case(self.case_id)["description"]
        self.assertIn(winner, results)
        self.assertEqual(self.store.get_verification(self.case_id)["winner"], winner)
        self.assertEqual(len([e for e in self.store.list_audit(self.case_id) if e["event"] == "ISOLATED_STAGE"]), 1)

    async def test_approval_handoff_failure_leaves_no_pending_projection(self):
        runner, captured = self.runner(), {}
        invoke = runner._invoke

        async def reject_after_result(case, skill_name, skill_input, **kwargs):
            result = await invoke(case, skill_name, skill_input, **kwargs)
            if skill_name == "ApprovalRouteSkill":
                captured.update(self.projections())
                self.reject_case_write("WAITING_FOR_APPROVAL")
            return result

        with patch.object(runner, "_invoke", side_effect=reject_after_result):
            with self.assertRaisesRegex(Exception, "injected case failure"):
                await runner.run_to_human_gate(self.store.get_case(self.case_id))
        self.assertTrue(captured)
        after = self.projections()
        for key in ("approval", "evidence", "verification", "executions"):
            self.assertEqual(after[key], captured[key], key)
        self.assertIsNone(after["approval"])
        self.assertEqual(after["case"]["status"], "FAILED")
        self.assertFalse(any(e["event"] == "STATE_TRANSITION" and
                             (json.loads(e["detail"]) if isinstance(e["detail"], str) else e["detail"]).get("to")
                             == "WAITING_FOR_APPROVAL" for e in after["audit"]))
        self.allow_case_write()

    async def test_verification_transition_failure_keeps_worker_result_without_partial_stage(self):
        case, runner, captured = self.approved_case(), self.runner(), {}
        invoke = runner._invoke

        async def reject_after_result(case, skill_name, skill_input, **kwargs):
            result = await invoke(case, skill_name, skill_input, **kwargs)
            if skill_name == "PostActionVerifySkill":
                self.assertEqual(result["verification_status"], "PASSED")
                captured.update(self.projections())
                self.reject_case_write("RESOLVED")
            return result

        with patch.object(runner, "_invoke", side_effect=reject_after_result):
            with self.assertRaisesRegex(Exception, "injected case failure"):
                await runner.execute_after_approval(case)
        self.assertTrue(captured)
        self.assertEqual(self.projections(), captured)
        verifier = [t for t in self.store.list_agent_tasks(self.case_id) if t["skill_name"] == "PostActionVerifySkill"][-1]
        self.assertEqual(verifier["status"], "SUCCEEDED")
        self.allow_case_write()

    async def test_old_verifier_result_cannot_replace_completed_recovery(self):
        old_case = self.approved_case()
        old = self.runner()
        invoke = old._invoke
        latest = {}

        async def recover_after_result(case, skill_name, skill_input, **kwargs):
            result = await invoke(case, skill_name, skill_input, **kwargs)
            if skill_name == "PostActionVerifySkill":
                self.interrupted(self.store.get_case(self.case_id))
                with patch.object(recovery.fixtures.api_module, "_spawn_team_background"):
                    response = await self.resume()
                self.assertEqual(response.status_code, 202, response.text)
                await self.runner().execute_after_approval(self.store.get_case(self.case_id))
                latest["case"] = self.store.get_case(self.case_id)
                latest["verification"] = self.store.get_verification(self.case_id)
                latest["executions"] = self.store.list_executions(self.case_id)
                self.assertEqual(latest["case"]["status"], "CLOSED")
                self.assertNotEqual(latest["verification"]["checked_at"], result["checked_at"])
            return result

        with patch.object(old, "_invoke", side_effect=recover_after_result):
            with self.assertRaises(StaleCaseTransition):
                await old.execute_after_approval(old_case)
        self.assertTrue(latest)
        self.assertEqual(self.store.get_case(self.case_id), latest["case"])
        self.assertEqual(self.store.get_verification(self.case_id), latest["verification"])
        self.assertEqual(self.store.list_executions(self.case_id), latest["executions"])
