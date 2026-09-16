from __future__ import annotations

import os
import sqlite3
import unittest
from decimal import Decimal
from unittest.mock import patch

from revguard import skills, telemetry
from revguard.agent_bridge import execute_agent_task
from revguard.mocks import ToolError
from revguard.models import CaseStatus
from revguard.money_journal import MoneyTransaction
from revguard.state_machine import transition_case
from revguard.trace import Tracer
from tests import test_mcp_team as fixtures


class TestMoneyRecovery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fixture = fixtures.TestMcpTeamRunner()
        self.fixture.setUp()
        self.store = self.fixture.store
        self.gateway = self.fixture.gateway
        self.runner = self.fixture.runner
        self.case_id = self.fixture.case["case_id"]
        self.gateway._posting_tamper_amount = Decimal("0")

    def tearDown(self):
        self.fixture.tearDown()

    async def ready(self):
        await self.runner.run_to_human_gate(self.fixture.case)
        approval = self.store.get_approval(self.case_id)
        result = self.gateway.call("workflow.decide_approval", {
            "approval_id": approval["approval_id"], "decision": "APPROVED",
        }, case_id=self.case_id, actor="finance.lead", scope=["approval:decide"])["data"]
        self.store.save_approval({"case_id": self.case_id, **result})
        case = self.store.get_case(self.case_id)
        transition_case(self.store, case, CaseStatus.READY_TO_EXECUTE,
                        "synthetic approval for fault test", actor="finance.lead")
        return case

    def entries(self):
        with self.gateway.journal.transaction() as tx:
            return [e for e in tx.state()["ledger"] if e.get("source") == f"REVGUARD:{self.case_id}"]

    def test_legacy_ledger_is_durable_before_first_new_money_operation(self):
        with self.gateway.journal.transaction() as tx:
            state = tx.state()
            persisted = tx.execute("SELECT ledger_id FROM money_ledger").fetchall()
        self.assertEqual({r["ledger_id"] for r in persisted}, {e["ledger_id"] for e in state["ledger"]})

    def test_reconciling_one_case_cannot_release_another_cases_channel_hold(self):
        other = "CASE-OTHER-RECOVERY"
        other_case = {**self.fixture.case, "case_id": other}
        # A new identity is a fresh insert, not an update of the source case.
        other_case.pop("_case_revision", None)
        self.store.save_case(other_case)
        channel = "order:EZ202608001"
        self.gateway.journal.hold(channel, self.case_id, "verification interrupted")
        self.gateway.journal.hold(channel, other, "verification interrupted")
        self.gateway.journal.reconcile(self.case_id)
        self.assertEqual(self.gateway.journal.metrics()["money_frozen_channels"], 1)
        with self.assertRaisesRegex(Exception, "冻结"):
            self.gateway.journal.prepare("new-operation", self.case_id, channel,
                                         "commission.submit_adjustment", "new-hash")
        self.gateway.journal.reconcile(other)
        self.assertEqual(self.gateway.journal.metrics()["money_frozen_channels"], 0)

    async def test_lost_submit_response_recovers_without_reposting(self):
        case = await self.ready()
        original = self.gateway.call

        def lose_response(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "commission.submit_adjustment" and result["success"]:
                raise TimeoutError("commit response lost")
            return result

        with patch.object(self.gateway, "call", side_effect=lose_response):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(len(self.entries()), 2)
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        ops = self.gateway.journal.reconcile(self.case_id)
        self.assertEqual([o["status"] for o in ops], ["COMMITTED"])
        result = await self.runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "CLOSED")
        self.assertEqual(len(self.entries()), 2)

    async def test_batch_second_component_failure_rolls_back_every_effect(self):
        case = await self.ready()
        original = self.gateway._submit_single
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ToolError("TOOL_UNAVAILABLE", "injected before second component")
            return original(*args, **kwargs)

        with patch.object(self.gateway, "_submit_single", side_effect=fail_second):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(calls, 2)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.gateway.journal.reconcile(self.case_id)
        result = await self.runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "CLOSED")
        self.assertEqual(len(self.entries()), 2)

    async def test_lost_compensation_response_only_reconciles_committed_batch(self):
        self.gateway._posting_tamper_amount = Decimal("1")
        case = await self.ready()
        original = self.gateway.call

        def lose_response(name, *args, **kwargs):
            result = original(name, *args, **kwargs)
            if name == "commission.reverse_adjustment" and result["success"]:
                raise TimeoutError("compensation commit response lost")
            return result

        with patch.object(self.gateway, "call", side_effect=lose_response):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(len(self.entries()), 4)
        self.assertTrue(all(e["status"] == "ROLLED_BACK" for e in self.store.list_executions(self.case_id)))
        self.gateway.journal.reconcile(self.case_id)
        result = await self.runner.resume_rollback(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "ROLLED_BACK")
        self.assertEqual(len(self.entries()), 4)
        self.assertEqual(sum(Decimal(e["amount"]) for e in self.entries()), Decimal(0))

    async def test_stale_executor_is_fenced_after_reconciliation(self):
        case = await self.ready()
        original = self.gateway.journal.prepare

        def paused_executor(*args, **kwargs):
            operation = original(*args, **kwargs)
            self.gateway.journal.reconcile(self.case_id)
            return operation

        with patch.object(self.gateway.journal, "prepare", side_effect=paused_executor):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(self.entries(), [])
        with self.gateway.journal.transaction() as tx:
            op = tx.execute("SELECT generation,status FROM money_operations").fetchone()
        self.assertEqual(op["generation"], 2)
        self.assertEqual(op["status"], "NOT_COMMITTED")
        # The gateway's uncertainty must survive the StageTask bridge. It must
        # not be masked by a secondary persistence error or become retryable.
        task = next(t for t in self.store.list_agent_tasks(self.case_id)
                    if t["skill_name"] == "LedgerAdjustSkill")
        self.assertEqual(task["status"], "RESULT_UNKNOWN")
        results = self.store.list_agent_task_results(task["task_id"])
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "RESULT_UNKNOWN")
        with self.assertRaisesRegex(ValueError, "状态不允许执行"):
            execute_agent_task(
                task_id=task["task_id"], case_id=self.case_id,
                skill_name=task["skill_name"], skill_input=task["input"],
                actor=task["assigned_actor"], gateway=self.gateway, store=self.store,
            )
        with self.assertRaisesRegex(ValueError, "不允许重派"):
            self.store.replace_agent_task(
                task["task_id"], {**task, "task_id": "TASK-UNSAFE-REPLACEMENT"},
                actor="revguard-orchestrator", reason="unsafe retry",
            )

    async def test_transient_read_mismatch_does_not_reverse_good_posting(self):
        self.gateway._verification_tamper_amount = Decimal("1")
        case = await self.ready()
        result = await self.runner.execute_after_approval(case)
        self.assertEqual(result["final_status"], "CLOSED")
        self.assertEqual(len(self.entries()), 2)

    async def test_database_commit_failure_blocks_new_keys_until_reconciled(self):
        case = await self.ready()
        with patch.object(MoneyTransaction, "commit_result", side_effect=sqlite3.OperationalError("db unavailable")):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.gateway.journal.metrics()["money_unresolved_operations"], 1)
        with self.gateway.journal.transaction() as tx:
            op = dict(tx.execute("SELECT * FROM money_operations").fetchone())
        self.assertEqual(op["status"], "RESULT_UNKNOWN")
        with self.assertRaisesRegex(Exception, "冻结"):
            self.gateway.journal.prepare("different-key", self.case_id, op["channel"], op["kind"], "different-hash")
        resolved = self.gateway.journal.reconcile(self.case_id)
        self.assertEqual(resolved[0]["status"], "NOT_COMMITTED")
        self.assertEqual(self.gateway.journal.metrics()["money_frozen_channels"], 0)
        result = await self.runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "CLOSED")

    async def test_committed_result_is_bound_to_original_case_and_parameters(self):
        case = await self.ready()
        await self.runner.execute_after_approval(case)
        task = next(t for t in self.store.list_agent_tasks(self.case_id) if t["skill_name"] == "LedgerAdjustSkill")
        params = {**task["input"], "case_id": self.case_id, "approval_token": self.store.get_approval(self.case_id)["approval_token"]}
        key = params.pop("idempotency_key")
        first = self.gateway.call("commission.submit_adjustment", params, case_id=self.case_id,
                                  actor="revguard-executor", scope=["commission:write"], idempotency_key=key)
        self.assertTrue(first["success"])
        for changed_case, changed_params in [("ANOTHER-CASE", params),
                                             (self.case_id, {**params, "policy_version": "changed"})]:
            result = self.gateway.call("commission.submit_adjustment", changed_params, case_id=changed_case,
                                       actor="revguard-executor", scope=["commission:write"], idempotency_key=key)
            self.assertFalse(result["success"])
            self.assertEqual(result["error"]["type"], "DATA_CONFLICT")
        self.assertEqual(len(self.entries()), 2)

    async def test_missing_execution_projection_is_rebuilt_from_committed_operation(self):
        case = await self.ready()
        await self.runner.execute_after_approval(case)
        with self.store.conn:
            self.store.conn.execute("DELETE FROM executions WHERE case_id=?", (self.case_id,))
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.gateway.journal.reconcile(self.case_id)
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        self.assertEqual(len(self.entries()), 2)

    async def test_recording_reset_keeps_history_without_replaying_old_money(self):
        case = await self.ready()
        await self.runner.execute_after_approval(case)
        self.gateway.reset_case(self.case_id)
        with self.gateway.journal.transaction() as tx:
            self.assertEqual(tx.execute("SELECT COUNT(*) AS n FROM money_operations").fetchone()["n"], 1)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.gateway.journal.reconcile(self.case_id), [])

    async def test_second_compensation_failure_cannot_leave_half_reversed_batch(self):
        self.gateway._posting_tamper_amount = Decimal("1")
        case = await self.ready()
        original = self.gateway._reverse_single
        calls = 0

        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise sqlite3.OperationalError("database connection lost")
            return original(*args, **kwargs)

        with patch.object(self.gateway, "_reverse_single", side_effect=fail_second):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(calls, 2)
        self.assertEqual(len(self.entries()), 2)
        self.assertTrue(all(e["status"] == "SUBMITTED" for e in self.store.list_executions(self.case_id)))
        self.gateway.journal.reconcile(self.case_id)
        result = await self.runner.resume_rollback(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "ROLLED_BACK")
        self.assertEqual(len(self.entries()), 4)

    async def test_other_valid_transaction_does_not_trigger_compensation(self):
        case = await self.ready()
        original = self.gateway.call
        added = False

        def concurrent_transaction(name, *args, **kwargs):
            nonlocal added
            if name == "finance.get_commission_ledger" and kwargs.get("actor") == "revguard-verifier" and not added:
                with self.gateway.journal.transaction() as tx:
                    state = tx.state()
                    state["ledger"].append({**state["ledger"][-1], "ledger_id": "UNRELATED-VALID",
                                            "source": "OTHER-CASE", "amount": "123.45"})
                    tx.save_state(state)
                added = True
            return original(name, *args, **kwargs)

        with patch.object(self.gateway, "call", side_effect=concurrent_transaction):
            result = await self.runner.execute_after_approval(case)
        self.assertTrue(added)
        self.assertEqual(result["final_status"], "CLOSED")
        self.assertEqual(len(self.entries()), 2)

    async def test_restore_lock_blocks_reconciliation_and_money_effects(self):
        case = await self.ready()
        with patch.dict(os.environ, {"REVGUARD_MONEY_RECOVERY_LOCK": "true"}):
            with self.assertRaisesRegex(Exception, "恢复锁"):
                self.gateway.journal.reconcile(self.case_id)
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(self.entries(), [])

    def test_database_outage_does_not_mask_original_exception_or_stop_otel(self):
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        telemetry._provider.add_span_processor(SimpleSpanProcessor(exporter))
        with patch.object(self.store, "max_span_sequence", side_effect=sqlite3.OperationalError("down")), \
             patch.object(self.store, "save_span", side_effect=sqlite3.OperationalError("down")):
            with self.assertRaisesRegex(RuntimeError, "original financial error"):
                with Tracer(self.store, self.case_id).span("TOOL", "fault-injection"):
                    raise RuntimeError("original financial error")
        span = next(s for s in exporter.get_finished_spans() if s.name == "fault-injection")
        self.assertEqual(span.status.status_code.name, "ERROR")

    def test_money_timeout_attempted_once_and_never_treated_as_safe_retry(self):
        class UncertainGateway:
            def __init__(self):
                self.calls = 0

            def call(self, *args, **kwargs):
                self.calls += 1
                return {"success": False, "data": None, "tool_receipt": "test",
                        "error": {"type": "TIMEOUT", "message": "unknown", "retryable": True}}

        gateway = UncertainGateway()
        with self.assertRaises(ToolError) as raised:
            skills.ledger_reverse(gateway, None, case_id="TEST", ledger_id="LED-1",
                                  rollback_token="test", idempotency_key="op-1")
        self.assertEqual(gateway.calls, 1)
        self.assertEqual(raised.exception.error_type, "RESULT_UNKNOWN")
        self.assertFalse(raised.exception.retryable)

    async def test_otel_propagates_agent_parent_to_skill_and_tool(self):
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )

        exporter = InMemorySpanExporter()
        telemetry._provider.add_span_processor(SimpleSpanProcessor(exporter))
        await self.ready()
        spans = exporter.get_finished_spans()
        agents = [s for s in spans if s.name.startswith("Agent.")]
        self.assertTrue(agents)
        skill = next(s for s in spans if s.name == "CaseNormalizeSkill")
        agent = next(s for s in agents if s.name == "Agent.CaseNormalizeSkill")
        self.assertEqual(skill.parent.span_id, agent.context.span_id)
        self.assertEqual(skill.context.trace_id, agent.context.trace_id)
        self.assertNotIn("RGC1.", str([dict(s.attributes) for s in spans]))
