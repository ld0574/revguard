"""Fresh and legacy-schema upgrades on databases created only by this test."""
from __future__ import annotations

import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(os.getenv("REVGUARD_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL server")
class TestPostgresMigrations(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        self.admin_dsn = os.environ["REVGUARD_TEST_POSTGRES_DSN"]
        self.database = "revguard_migration_" + uuid.uuid4().hex
        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database)))
        self.dsn = make_conninfo(self.admin_dsn, dbname=self.database)
        self.addCleanup(self.drop_database)

    def drop_database(self):
        import psycopg
        from psycopg import sql

        with psycopg.connect(self.admin_dsn, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(self.database)))

    def test_legacy_core_only_database_is_rejected_before_serving(self):
        import psycopg

        from revguard.postgres_store import PostgresStore

        core = Path(__file__).resolve().parent.parent / "migrations/polardb/001_core.sql"
        with psycopg.connect(self.dsn) as conn:
            conn.execute(core.read_text())
        with patch.dict(os.environ, {"REVGUARD_AUTO_MIGRATE": "false"}):
            with self.assertRaisesRegex(RuntimeError, "money_"):
                store = PostgresStore(self.dsn)
                store.close()

    def test_migration_entrypoint_is_complete_repeatable_and_preserves_history(self):
        import psycopg

        from revguard.postgres_store import PostgresStore
        from scripts.migrate_polardb import main

        with patch.dict(os.environ, {"REVGUARD_MIGRATION_DATABASE_URL": self.dsn,
                                     "REVGUARD_AUTO_MIGRATE": "false"}):
            self.assertEqual(main(), 0)
            store = PostgresStore(self.dsn)
            self.addCleanup(store.close)
            with store._conn() as conn:
                for table in ("money_gateway_state", "money_operations", "money_ledger", "money_outbox", "money_holds"):
                    self.assertIsNotNone(conn.execute("SELECT to_regclass(%s) AS name", (table,)).fetchone()["name"])
            store.audit("CASE-MIGRATION", "migration-test", "BEFORE_UPGRADE", {})
            before = store.verify_audit_chain()
            self.assertEqual(main(), 0)
            self.assertEqual(store.verify_audit_chain(), before)
            self.assertEqual(before["rows_checked"], 1)
            with psycopg.connect(self.dsn) as conn:
                with self.assertRaisesRegex(Exception, "append-only"):
                    conn.execute("DELETE FROM audit_events")
