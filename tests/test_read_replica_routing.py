import unittest
from contextlib import contextmanager
from threading import Lock
from unittest.mock import Mock

from revguard.postgres_store import PostgresStore


class FakePool:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    @contextmanager
    def connection(self):
        self.calls += 1
        yield self.value


class TestReadReplicaRouting(unittest.TestCase):
    def make_store(self, healthy):
        store = PostgresStore.__new__(PostgresStore)
        store.read_replica_enabled = True
        store._write_pool = FakePool("primary")
        store._read_pool = FakePool("replica")
        store._replica_status_lock = Lock()
        store._replica_fallback_total = 0
        store._replica_status = Mock(return_value={"healthy": healthy})
        return store

    def test_healthy_analytical_read_uses_replica(self):
        store = self.make_store(True)
        with store._conn(analytical=True) as value:
            self.assertEqual(value, "replica")
        self.assertEqual(store._read_pool.calls, 1)
        self.assertEqual(store._write_pool.calls, 0)

    def test_unhealthy_replica_falls_back_to_primary(self):
        store = self.make_store(False)
        with store._conn(analytical=True) as value:
            self.assertEqual(value, "primary")
        self.assertEqual(store._read_pool.calls, 0)
        self.assertEqual(store._write_pool.calls, 1)
        self.assertEqual(store._replica_fallback_total, 1)

    def test_critical_read_always_uses_primary(self):
        store = self.make_store(True)
        with store._conn(analytical=False) as value:
            self.assertEqual(value, "primary")
        store._replica_status.assert_not_called()


if __name__ == "__main__":
    unittest.main()
