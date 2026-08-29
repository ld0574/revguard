from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revguard.store import Store
from revguard.trace import Tracer


class TestExternalTransportSpan(unittest.TestCase):
    def test_completed_span_preserves_measured_wall_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "trace.db")
            span = Tracer(store, "CASE-EXT").record_completed_span(
                "AGENT",
                "AgentTeams.WorkerRoundTrip",
                actor="revguard-evidence",
                inputs={"transport": "agentteams-matrix"},
                outputs={"status": "SUCCEEDED"},
                started_at="2026-08-29T10:00:00Z",
                ended_at="2026-08-29T10:00:04Z",
                duration_ms=4321,
            )
            self.assertEqual(span["duration_ms"], 4321)
            persisted = store.list_spans("CASE-EXT")[0]
            self.assertEqual(persisted["duration_ms"], 4321)
            self.assertEqual(persisted["kind"], "AGENT")
            store.close()


if __name__ == "__main__":
    unittest.main()
