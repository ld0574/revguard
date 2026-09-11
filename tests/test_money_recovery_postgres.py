"""Run the same recovery contracts in newly created disposable PostgreSQL DBs.

REVGUARD_TEST_POSTGRES_DSN must point to an isolated test server with CREATEDB.
Only databases created by this test are dropped; existing data is never reset.
"""
from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from tests import test_money_recovery as contracts


@unittest.skipUnless(os.getenv("REVGUARD_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL server")
class TestPostgresMoneyRecovery(contracts.TestMoneyRecovery):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        from revguard.postgres_store import PostgresStore

        self.admin_dsn = os.environ["REVGUARD_TEST_POSTGRES_DSN"]
        self.database = "revguard_fault_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database)))
        self.pg_env = patch.dict(os.environ, {"REVGUARD_AUTO_MIGRATE": "true"})
        self.pg_env.start()
        dsn = make_conninfo(self.admin_dsn, dbname=self.database)
        with patch.object(contracts.fixtures, "Store", side_effect=lambda _: PostgresStore(dsn)):
            super().setUp()

    def tearDown(self):
        import psycopg
        from psycopg import sql

        try:
            super().tearDown()
            with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.database)))
        finally:
            self.pg_env.stop()

    async def test_missing_execution_projection_is_rebuilt_from_committed_operation(self):
        case = await self.ready()
        await self.runner.execute_after_approval(case)
        with self.store._conn() as conn:
            conn.execute("DELETE FROM executions WHERE case_id=%s", (self.case_id,))
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.gateway.journal.reconcile(self.case_id)
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)
        self.assertEqual(len(self.entries()), 2)
        self.assertTrue(self.store.verify_audit_chain()["valid"])

    async def test_terminated_database_connection_rolls_back_ledger_and_result_together(self):
        case = await self.ready()
        original = contracts.MoneyTransaction.commit_result

        def terminate_after_writes(tx, *args):
            original(tx, *args)
            # Real PostgreSQL backend termination after all statements and
            # before COMMIT: proves atomicity beyond a mocked Python error.
            tx.execute("SELECT pg_terminate_backend(pg_backend_pid())")

        with patch.object(contracts.MoneyTransaction, "commit_result", terminate_after_writes):
            with self.assertRaises(Exception):
                await self.runner.execute_after_approval(case)
        self.assertEqual(self.entries(), [])
        self.assertEqual(self.store.list_executions(self.case_id), [])
        self.gateway.journal.reconcile(self.case_id)
        result = await self.runner.execute_after_approval(self.store.get_case(self.case_id))
        self.assertEqual(result["final_status"], "CLOSED")
        self.assertEqual(len(self.entries()), 2)
        self.assertTrue(self.store.verify_audit_chain()["valid"])
