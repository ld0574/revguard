from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from decimal import Decimal
from pathlib import Path

from mcp import Client

from revguard.agent_bridge import create_agent_task
from revguard.mcp_server import build_scoped_server
from revguard.mocks import ToolGateway
from revguard.models import Case

ROOT = Path(__file__).resolve().parent.parent


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

    def test_mcp_call_persists_stage_result_in_postgresql(self):
        os.environ["REVGUARD_AUTO_MIGRATE"] = "true"
        from revguard.postgres_store import PostgresStore

        store = PostgresStore(os.environ["REVGUARD_TEST_POSTGRES_DSN"])
        try:
            suffix = uuid.uuid4().hex[:10].upper()
            case = Case(
                case_id=f"CASE-PG-MCP-{suffix}",
                case_type="COMMISSION_UNDERPAYMENT",
                source="POSTGRES-MCP-INTEGRATION",
                partner_id="AGT-10001",
                order_id="EZ202608001",
                entities={
                    "partner_id": "AGT-10001", "partner_name": None,
                    "order_id": "EZ202608001", "contract_id": None,
                },
            ).to_dict()
            store.save_case(case)
            skill_input = {"raw_case": case}
            task = create_agent_task(case, "CaseNormalizeSkill", skill_input)
            store.save_agent_task(task)
            gateway = ToolGateway(
                ROOT / "data" / "fixtures",
                signing_key="postgres-mcp-integration-key-at-least-32-bytes",
            )
            server = build_scoped_server(
                actor="revguard-intake", store=store, gateway=gateway,
            )

            async def invoke():
                async with Client(server) as client:
                    return await client.call_tool("CaseNormalizeSkill", {
                        "case_id": case["case_id"],
                        "task_id": task["task_id"],
                        "input": skill_input,
                        "request_id": "REQ-PG-MCP-INTEGRATION",
                    })

            result = asyncio.run(invoke())
            self.assertFalse(result.is_error)
            persisted = store.get_agent_task(task["task_id"])
            self.assertEqual(persisted["status"], "SUCCEEDED")
            self.assertEqual(persisted["result"]["entities"]["partner_id"], "AGT-10001")
            self.assertEqual(len(store.list_agent_task_results(task["task_id"])), 1)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
