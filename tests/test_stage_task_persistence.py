from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revguard.agent_bridge import case_version, create_agent_task
from revguard.models import Case, TaskStatus
from revguard.store import Store


class TestStageTaskPersistence(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "task.db")
        self.case = Case(
            case_id="CASE-STAGE-ATOMIC", case_type="COMMISSION_UNDERPAYMENT",
            source="TEST", partner_id="AGT-10001", order_id="EZ202608001",
        ).to_dict()
        self.store.save_case(self.case)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _task(self):
        return create_agent_task(
            self.case, "CaseNormalizeSkill", {"raw_case": self.case}
        )

    def test_retry_attempts_persist_task_and_result_atomically(self):
        task = self._task()
        self.store.save_agent_task(task)
        running = self.store.transition_agent_task(
            task["task_id"], expected={TaskStatus.PENDING.value},
            status=TaskStatus.RUNNING.value,
        )
        self.assertEqual(running["attempt"], 1)
        failed, first_result = self.store.complete_agent_task(
            task["task_id"], status=TaskStatus.FAILED_RETRYABLE.value,
            error={"type": "ToolError", "message": "temporary"},
        )
        self.assertEqual(failed["status"], TaskStatus.FAILED_RETRYABLE.value)
        self.assertEqual(first_result["attempt"], 1)

        running = self.store.transition_agent_task(
            task["task_id"], expected={TaskStatus.FAILED_RETRYABLE.value},
            status=TaskStatus.RUNNING.value,
        )
        self.assertEqual(running["attempt"], 2)
        completed, _ = self.store.complete_agent_task(
            task["task_id"], status=TaskStatus.SUCCEEDED.value,
            result={"ok": True}, skill_receipt="SKR-TEST",
        )
        results = self.store.list_agent_task_results(task["task_id"])
        self.assertEqual(completed["status"], TaskStatus.SUCCEEDED.value)
        self.assertEqual([item["attempt"] for item in results], [1, 2])
        self.assertEqual(results[-1]["result"], {"ok": True})

    def test_ui_run_progress_does_not_invalidate_domain_case_version(self):
        before = case_version(self.case)
        self.case["team_run"] = {
            "status": "RUNNING", "current_stage": "CaseNormalizeSkill",
            "completed_tasks": 0,
        }
        self.assertEqual(case_version(self.case), before)
        self.case["status"] = "NORMALIZING"
        self.assertNotEqual(case_version(self.case), before)

    def test_failed_task_can_be_reassigned_with_lineage(self):
        old = self._task()
        self.store.save_agent_task(old)
        self.store.transition_agent_task(
            old["task_id"], expected={TaskStatus.PENDING.value},
            status=TaskStatus.RUNNING.value,
        )
        self.store.complete_agent_task(
            old["task_id"], status=TaskStatus.FAILED_FINAL.value,
            error={"type": "ContractError", "message": "bad output"},
        )
        replacement = self._task()
        cancelled, persisted = self.store.replace_agent_task(
            old["task_id"], replacement, actor="revguard-orchestrator",
            reason="更换 Worker 并重放同一输入快照",
        )
        self.assertEqual(cancelled["status"], TaskStatus.CANCELLED.value)
        self.assertEqual(persisted["supersedes_task_id"], old["task_id"])
        self.assertEqual(
            self.store.get_agent_task(old["task_id"])["replaced_by_task_id"],
            persisted["task_id"],
        )
        self.assertEqual(
            self.store.list_audit(self.case["case_id"])[-1]["event"],
            "AGENT_TASK_REASSIGNED",
        )

    def test_case_pause_cancels_open_tasks_but_keeps_completed_results(self):
        pending = self._task()
        completed = self._task()
        self.store.save_agent_task(pending)
        self.store.save_agent_task(completed)
        self.store.transition_agent_task(
            completed["task_id"], expected={TaskStatus.PENDING.value},
            status=TaskStatus.RUNNING.value,
        )
        self.store.complete_agent_task(
            completed["task_id"], status=TaskStatus.SUCCEEDED.value,
            result={"ok": True},
        )
        cancelled = self.store.cancel_open_agent_tasks(
            self.case["case_id"], actor="revguard-orchestrator",
            reason="等待补证",
        )
        self.assertEqual(cancelled, [pending["task_id"]])
        self.assertEqual(
            self.store.get_agent_task(pending["task_id"])["status"],
            TaskStatus.CANCELLED.value,
        )
        self.assertEqual(
            self.store.get_agent_task(completed["task_id"])["status"],
            TaskStatus.SUCCEEDED.value,
        )


if __name__ == "__main__":
    unittest.main()
