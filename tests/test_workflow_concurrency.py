"""Regression contracts for stale coordinators and task-claim races."""
from __future__ import annotations

import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import AsyncMock, patch

from revguard import agent_bridge
from revguard.matrix_team import MatrixTeamRunner
from revguard.models import CaseStatus
from revguard.money_journal import MoneyTransaction
from revguard.state_machine import transition_case
from tests import test_runtime_safety as fixtures


class TestWorkflowConcurrency(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TestRuntimeSafety.setUp(self)

    client = fixtures.TestRuntimeSafety.client

    async def test_http_snapshot_conflict_returns_refreshable_409(self):
        def stale_run(case):
            newer = self.store.get_case(self.case_id)
            newer["description"] = "newer input already saved"
            self.store.save_case(newer)
            case["priority"] = "P0"
            self.store.save_case(case)
        from unittest.mock import Mock
        runner = Mock()
        runner.run_case.side_effect = stale_run
        with patch.object(fixtures.api_module, "_orchestrator", return_value=runner):
            async with self.client() as client:
                result = await client.post(f"/api/v1/cases/{self.case_id}/run", headers=self.operator)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()["detail"]["code"], "CASE_CHANGED")
        self.assertEqual(self.store.get_case(self.case_id)["description"], "newer input already saved")

    def test_stale_matrix_progress_cannot_reopen_advanced_case(self):
        case = self.store.get_case(self.case_id)
        case["team_run"] = {"run_id": "RUN-current", "status": "RUNNING"}
        self.store.save_case(case)
        stale = copy.deepcopy(case)
        transition_case(self.store, case, CaseStatus.NORMALIZING, "another coordinator advanced")
        before = self.store.get_case(self.case_id)
        runner = MatrixTeamRunner(self.store, self.gateway, output_dir=self.temp.name, report_dir=self.temp.name)
        with self.assertRaises(ValueError):
            runner._update_run(stale, status="FAILED")
        self.assertEqual(self.store.get_case(self.case_id), before)
        self.assertEqual(stale["team_run"]["status"], "RUNNING")

    def test_stale_ordinary_case_write_cannot_erase_new_business_fields(self):
        case = self.store.get_case(self.case_id)
        stale = copy.deepcopy(case)
        case["description"] = "newer persisted evidence"
        self.store.save_case(case)
        stale["priority"] = "P0"
        with self.assertRaises(ValueError):
            self.store.save_case(stale)
        self.assertEqual(self.store.get_case(self.case_id), case)

    def test_case_change_between_validation_and_claim_prevents_skill_execution(self):
        case = self.store.get_case(self.case_id)
        task = agent_bridge.create_agent_task(case, "CaseNormalizeSkill", {"raw_case": case})
        self.store.save_agent_task(task)
        validate = agent_bridge.validate_task_invocation
        changed = False
        def change_after_validation(*args, **kwargs):
            nonlocal changed
            validate(*args, **kwargs)
            if not changed:
                changed = True
                current = self.store.get_case(self.case_id)
                current["description"] = "case changed before task claim"
                self.store.save_case(current)
        with patch.object(agent_bridge, "validate_task_invocation", side_effect=change_after_validation):
            with patch.object(agent_bridge, "invoke_skill", return_value={"data": {}, "skill_receipt": "isolated"}) as invoke:
                with self.assertRaises(ValueError):
                    agent_bridge.execute_agent_task(
                        task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                        skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
                    )
                invoke.assert_not_called()
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "PENDING")

    def task(self):
        case = self.store.get_case(self.case_id)
        task = agent_bridge.create_agent_task(case, "CaseNormalizeSkill", {"raw_case": case})
        self.store.save_agent_task(task)
        return task

    def test_competing_case_writers_preserve_exactly_one_revision(self):
        original = self.store.get_case(self.case_id)
        second_store = self.independent_store()
        def write(store, label):
            case = copy.deepcopy(original)
            case["description"] = label
            try:
                store.save_case(case)
                return label
            except ValueError:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(write, store, label) for store, label in ((self.store, "first"), (second_store, "second"))]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(results.count("CONFLICT"), 1)
        self.assertIn(self.store.get_case(self.case_id)["description"], results)

    def independent_store(self):
        if self.store.backend == "postgresql-polardb":
            from revguard.postgres_store import PostgresStore
            second = PostgresStore(self.store._write_pool.conninfo)
        else:
            second = fixtures.Store(self.store.db_path)
        self.addCleanup(second.close)
        return second

    def test_independent_workers_cannot_claim_same_task_twice(self):
        task = self.task()
        second_store = self.independent_store()
        def execute(store):
            try:
                agent_bridge.execute_agent_task(
                    task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                    skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=store,
                )
                return "SUCCEEDED"
            except ValueError:
                return "CONFLICT"
        with patch.object(agent_bridge, "invoke_skill", return_value={"data": {"winner": True}, "skill_receipt": "test"}) as invoke:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(execute, store) for store in (self.store, second_store)]
                results = [future.result(timeout=10) for future in futures]
            invoke.assert_called_once()
        self.assertCountEqual(results, ["SUCCEEDED", "CONFLICT"])
        self.assertEqual(len(self.store.list_agent_task_results(task["task_id"])), 1)

    def test_claim_audit_failure_never_starts_skill_or_leaves_running_task(self):
        task = self.task()
        audit = MoneyTransaction.audit
        def fail_start(tx, case_id, event, detail, **kwargs):
            if event == "AGENT_TASK_STARTED":
                raise RuntimeError("injected claim audit failure")
            return audit(tx, case_id, event, detail, **kwargs)
        with patch.object(MoneyTransaction, "audit", fail_start):
            with patch.object(agent_bridge, "invoke_skill") as invoke:
                with self.assertRaises(RuntimeError):
                    agent_bridge.execute_agent_task(
                        task_id=task["task_id"], case_id=self.case_id, skill_name=task["skill_name"],
                        skill_input=task["input"], actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
                    )
                invoke.assert_not_called()
        self.assertEqual(self.store.get_agent_task(task["task_id"]), task)

    def test_result_audit_failure_rolls_back_result_and_allows_same_attempt_completion(self):
        task = self.task()
        running = self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")
        with patch.object(MoneyTransaction, "audit", side_effect=RuntimeError("injected result audit failure")):
            with self.assertRaises(RuntimeError):
                self.store.complete_agent_task(task["task_id"], status="SUCCEEDED",
                                               expected_attempt=running["attempt"], result={"answer": "durable"})
        self.assertEqual(self.store.get_agent_task(task["task_id"]), running)
        self.assertEqual(self.store.list_agent_task_results(task["task_id"]), [])
        self.store.complete_agent_task(task["task_id"], status="SUCCEEDED",
                                       expected_attempt=running["attempt"], result={"answer": "durable"})
        self.assertEqual(len(self.store.list_agent_task_results(task["task_id"])), 1)
        self.assertEqual(len([e for e in self.store.list_audit(self.case_id) if e["event"]=="AGENT_TASK_SUCCEEDED"]), 1)

    def test_old_attempt_cannot_complete_a_new_running_attempt(self):
        task = self.task()
        self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")
        self.store.complete_agent_task(task["task_id"], status="FAILED_RETRYABLE", expected_attempt=1)
        second = self.store.transition_agent_task(task["task_id"], expected={"FAILED_RETRYABLE"}, status="RUNNING")
        self.assertEqual(second["attempt"], 2)
        with self.assertRaises(ValueError):
            self.store.complete_agent_task(task["task_id"], status="SUCCEEDED", expected_attempt=1, result={"late": True})
        self.assertEqual(self.store.get_agent_task(task["task_id"]), second)

    async def test_late_usage_metadata_cannot_erase_a_concurrent_worker_result(self):
        task = self.task()
        get_task = self.store.get_agent_task
        def complete_after_read(task_id):
            stale = get_task(task_id)
            self.store.transition_agent_task(task_id, expected={"PENDING"}, status="RUNNING")
            self.store.complete_agent_task(task_id, status="SUCCEEDED", expected_attempt=1,
                                           result={"worker": "committed"})
            return stale
        runner = MatrixTeamRunner(self.store, self.gateway, output_dir=self.temp.name, report_dir=self.temp.name)
        with patch.object(self.store, "get_agent_task", side_effect=complete_after_read):
            await runner._capture_task_usage(task["task_id"], task["assigned_actor"], None)
        persisted = get_task(task["task_id"])
        self.assertEqual(persisted["status"], "SUCCEEDED")
        self.assertEqual(persisted["result"], {"worker": "committed"})
        self.assertEqual(len(self.store.list_agent_task_results(task["task_id"])), 1)

    def test_metadata_patch_preserves_result_and_rejects_execution_fields(self):
        from revguard.workflow_persistence import update_task_metadata
        task = self.task()
        self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")
        self.store.complete_agent_task(task["task_id"], status="SUCCEEDED", expected_attempt=1, result={"real": True})
        updated = update_task_metadata(self.store, task["task_id"], {"matrix_trigger_event_id": "$late"})
        self.assertEqual(updated["status"], "SUCCEEDED")
        self.assertEqual(updated["result"], {"real": True})
        with self.assertRaises(ValueError):
            update_task_metadata(self.store, task["task_id"], {"status": "PENDING"})
        self.assertEqual(self.store.get_agent_task(task["task_id"]), updated)

    def test_duplicate_task_insert_cannot_replace_a_committed_result(self):
        task = self.task()
        self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")
        self.store.complete_agent_task(task["task_id"], status="SUCCEEDED", expected_attempt=1, result={"original": True})
        before = self.store.get_agent_task(task["task_id"])
        with self.assertRaises(ValueError):
            self.store.save_agent_task(task)
        self.assertEqual(self.store.get_agent_task(task["task_id"]), before)

    async def test_unknown_task_result_stops_matrix_nudges_immediately(self):
        case = self.store.get_case(self.case_id)
        client = AsyncMock()
        async def send(body, **kwargs):
            if "/clear" in body:
                return "$session-reset"
            if "adapter_command=" in body:
                task = self.store.list_agent_tasks(self.case_id)[-1]
                self.store.transition_agent_task(task["task_id"], expected={"PENDING"}, status="RUNNING")
                self.store.complete_agent_task(task["task_id"], status="RESULT_UNKNOWN", expected_attempt=1,
                                               error={"message": "original operation must be reconciled"})
            return "$fixture-event"
        client.send_text.side_effect = send
        client.wait_for_event.return_value = {
            "event_id": "$session-reset-response",
            "sender": "@revguard-intake:matrix-local.agentteams.io:8086",
            "content": {"body": "**History Cleared!**"},
        }
        runner = MatrixTeamRunner(self.store, self.gateway, output_dir=self.temp.name,
                                  report_dir=self.temp.name, client=client)
        runner.settings = replace(runner.settings, stage_timeout_seconds=0.05, retry_nudge_seconds=(0,))
        with self.assertRaisesRegex(Exception, "RESULT_UNKNOWN"):
            await runner._invoke_transport(case, "CaseNormalizeSkill", {"raw_case": case})
        # 派发链路发 4 条消息（交接 + 会话清理 + 派发 + 触发）；任务在触发消息处
        # 即 RESULT_UNKNOWN，因此不得出现第 5 条（重试 nudge）。
        self.assertEqual(client.send_text.await_count, 4)
