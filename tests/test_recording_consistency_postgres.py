"""Recording consistency on disposable PostgreSQL databases only."""
from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

from tests import test_recording_consistency as contracts


@unittest.skipUnless(os.getenv("REVGUARD_TEST_POSTGRES_DSN"), "requires disposable PostgreSQL server")
class TestPostgresRecordingConsistency(contracts.TestRecordingConsistency):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import make_conninfo

        from revguard.postgres_store import PostgresStore

        admin = os.environ["REVGUARD_TEST_POSTGRES_DSN"]
        database = "revguard_recording_" + uuid.uuid4().hex
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))

        def drop():
            with psycopg.connect(admin, autocommit=True) as conn:
                conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))

        self.addCleanup(drop)
        settings = patch.dict(os.environ, {
            "REVGUARD_AUTO_MIGRATE": "true", "REVGUARD_ALLOW_DATABASE_RESET": "true",
            "REVGUARD_DB_POOL_MIN": "1", "REVGUARD_DB_POOL_MAX": "1",
        })
        settings.start()
        self.addCleanup(settings.stop)
        dsn = make_conninfo(admin, dbname=database)
        with patch.object(contracts.fixtures, "Store", side_effect=lambda _: PostgresStore(dsn)):
            super().setUp()
