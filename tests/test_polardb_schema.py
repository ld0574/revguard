from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestPolarDbSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = (ROOT / "migrations/polardb/001_core.sql").read_text(
            encoding="utf-8"
        ).lower()
        cls.vector = (
            ROOT / "migrations/polardb/002_case_memory_pgvector_optional.sql"
        ).read_text(encoding="utf-8").lower()

    def test_money_and_atomic_stage_result_schema(self):
        self.assertGreaterEqual(self.core.count("numeric(18,2)"), 6)
        self.assertIn("create table if not exists agent_task_results", self.core)
        self.assertIn("unique(task_id, attempt)", self.core)

    def test_database_enforces_append_only_hash_chain(self):
        self.assertIn("previous_hash", self.core)
        self.assertIn("row_digest", self.core)
        self.assertIn("pg_advisory_xact_lock", self.core)
        self.assertIn("before update or delete or truncate", self.core)
        self.assertIn("digest(new.previous_hash", self.core)

    def test_pgvector_is_opt_in_not_core_dependency(self):
        self.assertNotIn("create extension if not exists vector", self.core)
        self.assertIn("create extension if not exists vector", self.vector)
        self.assertIn("vector(768)", self.vector)


if __name__ == "__main__":
    unittest.main()
