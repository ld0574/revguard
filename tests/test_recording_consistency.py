"""Failures must not split a recording reset across the case, ledger and files."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from revguard.mocks import ToolGateway
from revguard.money_journal import MoneyTransaction
from revguard.orchestrator import Orchestrator
from tests import test_runtime_safety as fixtures


class TestRecordingConsistency(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixtures.TestRuntimeSafety.setUp(self)

    client = fixtures.TestRuntimeSafety.client

    def closed_case(self):
        case = self.store.get_case(self.case_id)
        runner = Orchestrator(self.store, self.gateway, output_dir=self.temp.name,
                              report_dir=self.temp.name, approval_mode="auto")
        runner.run_case(case)
        self.assertEqual(self.store.get_case(self.case_id)["status"], "CLOSED")
        self.assertEqual(len(self.store.list_executions(self.case_id)), 2)

    def snapshot(self):
        with self.gateway.journal.transaction() as tx:
            state = tx.state()
        return {
            "case": self.store.get_case(self.case_id),
            "gateway": state, "evidence": self.store.list_evidence(self.case_id),
            "executions": self.store.list_executions(self.case_id),
            "tasks": self.store.list_agent_tasks(self.case_id),
            "audit": self.store.list_audit(self.case_id),
        }

    async def test_failed_case_insert_preserves_gateway_executions_and_audit(self):
        self.closed_case()
        before = self.snapshot()
        with self.gateway.journal.transaction() as tx:
            if tx.postgres:
                tx.execute("""CREATE FUNCTION reject_recording_insert() RETURNS trigger AS $$
                    BEGIN IF NEW.status='CREATED' THEN RAISE EXCEPTION 'injected case write failure';
                    END IF; RETURN NEW; END; $$ LANGUAGE plpgsql;
                    CREATE TRIGGER reject_recording BEFORE INSERT ON cases
                    FOR EACH ROW EXECUTE FUNCTION reject_recording_insert();""")
            else:
                tx.execute("""CREATE TRIGGER reject_recording BEFORE INSERT ON cases
                    WHEN NEW.status='CREATED' BEGIN
                    SELECT RAISE(ABORT, 'injected case write failure'); END""")
        transport = fixtures.httpx.ASGITransport(app=fixtures.api_module.app, raise_app_exceptions=False)
        async with fixtures.httpx.AsyncClient(transport=transport, base_url="http://isolated") as client:
            result = await client.post(f"/api/v1/cases/{self.case_id}/reprepare", headers=self.operator)
        self.assertIn(result.status_code, {500, 503}, result.text)
        self.assertEqual(self.snapshot(), before)

    async def test_readonly_old_artifacts_do_not_block_reprepare_or_leak_into_new_run(self):
        self.closed_case()
        report = Path(self.temp.name) / f"{self.case_id}.md"
        old_report = report.read_text()
        old_case = self.store.get_case(self.case_id)
        async with self.client() as client:
            with patch.object(Path, "unlink", side_effect=PermissionError("read-only history")):
                result = await client.post(f"/api/v1/cases/{self.case_id}/reprepare", headers=self.operator)
            self.assertEqual(result.status_code, 200, result.text)
            current = self.store.get_case(self.case_id)
            self.assertTrue(current.get("recording_id"))
            self.assertNotEqual(current.get("recording_id"), old_case.get("recording_id"))
            self.assertEqual(report.read_text(), old_report)
            self.assertEqual((await client.get(f"/api/v1/cases/{self.case_id}/report", headers=self.viewer)).status_code, 404)
            self.assertEqual(self.store.list_executions(self.case_id), [])
            self.closed_case()
            current_report = await client.get(f"/api/v1/cases/{self.case_id}/report", headers=self.viewer)
            self.assertEqual(current_report.status_code, 200, current_report.text)
            self.assertEqual(report.read_text(), old_report)
            self.assertTrue((await client.get(f"/api/v1/cases/{self.case_id}/dashboard", headers=self.viewer)).json()["report_available"])

    def test_durable_gateway_ignores_corrupt_legacy_json(self):
        self.closed_case()
        before = self.snapshot()
        self.state_path.write_text("{invalid legacy JSON")
        recovered = ToolGateway(fixtures.ROOT / "data/fixtures", store=self.store,
                                signing_key=fixtures.api_module.SIGNING_KEY, state_path=self.state_path)
        self.assertEqual(recovered._ledger, before["gateway"]["ledger"])
        self.assertEqual(self.snapshot(), before)

    async def test_full_reset_seeds_gateway_and_changes_artifact_generation(self):
        self.closed_case()
        old_report = Path(self.temp.name) / f"{self.case_id}.md"
        before = old_report.read_text()
        async with self.client() as client:
            with patch.object(Path, "unlink", side_effect=PermissionError("read-only history")):
                reset = await client.post("/api/v1/demo/reset", headers=self.operator)
            self.assertEqual(reset.status_code, 200, reset.text)
            first = self.store.get_case(self.case_id)["recording_id"]
            self.assertEqual(old_report.read_text(), before)
            self.assertEqual((await client.get(f"/api/v1/cases/{self.case_id}/report", headers=self.viewer)).status_code, 404)
            with self.gateway.journal.transaction() as tx:
                self.assertEqual(tx.state()["execution_results"], {})
                self.assertEqual(tx.state()["ledger"], self.gateway.fixtures["ledger"]["entries"])
            reset = await client.post("/api/v1/demo/reset", headers=self.operator)
            self.assertEqual(reset.status_code, 200, reset.text)
            self.assertNotEqual(self.store.get_case(self.case_id)["recording_id"], first)

    async def test_reset_audit_failure_rolls_back_cases_and_gateway_together(self):
        self.closed_case()
        before = self.snapshot()
        transport = fixtures.httpx.ASGITransport(app=fixtures.api_module.app, raise_app_exceptions=False)
        async with fixtures.httpx.AsyncClient(transport=transport, base_url="http://isolated") as client:
            with patch.object(MoneyTransaction, "audit", side_effect=RuntimeError("injected audit failure")):
                result = await client.post("/api/v1/demo/reset", headers=self.operator)
        self.assertEqual(result.status_code, 500)
        self.assertEqual(self.snapshot(), before)
