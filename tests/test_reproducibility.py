"""Demo reset、重复 seed 与持久化重启的可复现性测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revguard.store import Store
from scripts.seed_demo import seed, seed_store


class TestReproducibleSeed(unittest.TestCase):
    def test_seed_store_supports_the_active_backend_without_closing_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "active.db")
            seeded = seed_store(store, reset=True, quiet=True)
            self.assertEqual(len(seeded), 8)
            self.assertEqual(store.count_cases(), 8)
            store.audit("CASE-2026-0001", "test", "STORE_STILL_OPEN", {})
            self.assertEqual(len(store.list_audit("CASE-2026-0001")), 2)
            store.close()

    def test_seed_preserves_existing_state_unless_reset_requested(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "demo.db")
            seed(db_path, reset=True, quiet=True)
            store = Store(db_path)
            case = store.get_case("CASE-2026-0001")
            case["status"] = "CLOSED"
            store.save_case(case)
            store.close()

            seed(db_path, quiet=True)
            store = Store(db_path)
            self.assertEqual(store.get_case("CASE-2026-0001")["status"], "CLOSED")
            store.close()

            seed(db_path, reset=True, quiet=True)
            store = Store(db_path)
            self.assertEqual(store.get_case("CASE-2026-0001")["status"], "CREATED")
            self.assertEqual(len(store.list_audit("CASE-2026-0001")), 1)
            store.close()


if __name__ == "__main__":
    unittest.main()
