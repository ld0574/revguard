from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from revguard.postgres_store import PostgresStore


class TestPolarDbRecordingResetGuard(unittest.TestCase):
    def test_reset_is_denied_without_explicit_recording_database_flag(self):
        store = object.__new__(PostgresStore)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "正式审计库禁止"):
                store.reset()


if __name__ == "__main__":
    unittest.main()
