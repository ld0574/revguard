from __future__ import annotations

import os
import unittest
import uuid
from decimal import Decimal

from revguard.agent_bridge import create_agent_task
from revguard.models import Case


@unittest.skipUnless(
    os.getenv("REVGUARD_TEST_POSTGRES_DSN"),
    "set REVGUARD_TEST_POSTGRES_DSN to run against a disposable PostgreSQL database",
)
class TestPostgresStoreIntegration(unittest.TestCase):
    def test_numeric_audit_chain_and_atomic_stage_result(self):
        os.environ["REVGUARD_AUTO_MIGRATE"] = "true"
        from revguard.postgres_store import PostgresStore

        store = PostgresStore(os.environ["REVGUARD_TEST_POSTGRES_DSN"])
        suffix = uuid.uuid4().hex[:10].upper()
        case = Case(
            case_id=f"CASE-PG-{suffix}", case_type="COMMISSION_UNDERPAYMENT",
            source="POSTGRES-INTEGRATION",
            claim={"actual_amount": "100.10", "expected_amount": "112.34",
                   "currency": "CNY"},
        ).to_dict()
        store.save_case(case)
        store.audit(case["case_id"], "integration-test", "CASE_CREATED", {})
        task = create_agent_task(case, "CaseNormalizeSkill", {"raw_case": case})
        store.save_agent_task(task)
        store.transition_agent_task(task["task_id"], expected={"PENDING"},
                                    status="RUNNING")
        store.complete_agent_task(task["task_id"], status="SUCCEEDED",
                                  result={"ok": True})
        with store._conn() as conn:
            amount = conn.execute(
                "SELECT claim_expected_amount FROM cases WHERE case_id=%s",
                (case["case_id"],),
            ).fetchone()["claim_expected_amount"]
            with self.assertRaisesRegex(Exception, "append-only"):
                conn.execute(
                    "UPDATE audit_events SET event='TAMPERED' WHERE case_id=%s",
                    (case["case_id"],),
                )
        self.assertEqual(amount, Decimal("112.34"))
        self.assertTrue(store.verify_audit_chain()["valid"])
        self.assertEqual(len(store.list_agent_task_results(task["task_id"])), 1)
        store.close()


if __name__ == "__main__":
    unittest.main()
