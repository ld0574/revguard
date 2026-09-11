"""Recording safety contracts, run only in disposable 202 Docker databases."""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from revguard.agent_bridge import create_agent_task, execute_agent_task
from revguard.mocks import ToolGateway
from revguard.runtime_barrier import CURRENT_LEASE, RuntimeBusy, acquire_runtime_lease
from revguard.store import Store
from scripts.seed_demo import seed_store

# unittest discover imports test_api as a top-level module, while named runs
# use tests.test_api. Reuse its bootstrap instead of resetting environment
# paths underneath an already initialized singleton API.
api_tests = sys.modules.get("test_api") or importlib.import_module("tests.test_api")
ROOT, api_module, httpx = api_tests.ROOT, api_tests.api_module, api_tests.httpx


class TestRuntimeSafety(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / "isolated.db")
        self.addCleanup(self.store.close)
        seed_store(self.store, quiet=True)
        self.gateway = ToolGateway(ROOT / "data/fixtures", store=self.store,
                                   signing_key=api_module.SIGNING_KEY)
        self.state_path = Path(self.temp.name) / "legacy.json"
        self.state_path.write_text("preserve-before-reset")
        settings = patch.multiple(
            api_module, store=self.store, gateway=self.gateway, ENABLE_RECORDING_UI=True,
            GATEWAY_STATE_PATH=str(self.state_path), TEAM_TRANSPORT="matrix",
            BACKGROUND_TEAM_TASKS={}, OUTPUT_DIR=self.temp.name, REPORT_DIR=self.temp.name,
        )
        settings.start()
        self.addCleanup(settings.stop)
        self.operator = {"Authorization": "Bearer rg-demo-operator-key"}
        self.viewer = {"Authorization": "Bearer rg-demo-viewer-key-1"}
        self.case_id = "CASE-2026-0008"

    def client(self):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=api_module.app), base_url="http://isolated")

    async def test_reset_cannot_race_an_inflight_request(self):
        entered, release = threading.Event(), threading.Event()

        class PausedRunner:
            def run_case(_, case):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("test request not released")
                return {"final_status": case["status"]}

        before = self.store.list_cases()
        async with self.client() as client:
            with patch.object(api_module, "_orchestrator", return_value=PausedRunner()):
                request = asyncio.create_task(client.post(f"/api/v1/cases/{self.case_id}/run", headers=self.operator))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    reset = await client.post("/api/v1/demo/reset", headers=self.operator)
                    self.assertEqual(reset.status_code, 409, reset.text)
                    self.assertEqual(self.store.list_cases(), before)
                    self.assertEqual((await client.get("/api/v1/health/live")).status_code, 200)
                finally:
                    release.set()
                    self.assertEqual((await request).status_code, 200)
            self.assertEqual((await client.post("/api/v1/demo/reset", headers=self.operator)).status_code, 200)

    async def test_exclusive_reset_blocks_new_requests_but_not_liveness(self):
        entered, release = threading.Event(), threading.Event()

        def paused_seed(*args, **kwargs):
            entered.set()
            if not release.wait(10):
                raise TimeoutError("test reset not released")
            return seed_store(*args, **kwargs)

        async with self.client() as client:
            with patch("scripts.seed_demo.seed_store", side_effect=paused_seed):
                reset = asyncio.create_task(client.post("/api/v1/demo/reset", headers=self.operator))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    for method, path, headers in (
                        ("GET", "/api/v1/cases", self.viewer),
                        ("POST", f"/api/v1/cases/{self.case_id}/run", self.operator),
                    ):
                        result = await client.request(method, path, headers=headers)
                        self.assertEqual(result.status_code, 409, result.text)
                    self.assertEqual((await client.get("/api/v1/health/live")).status_code, 200)
                finally:
                    release.set()
                    self.assertEqual((await reset).status_code, 200)

    async def test_background_retains_request_lease_until_cancelled(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def paused_run(*_):
            started.set()
            await release.wait()

        async with self.client() as client:
            with patch.object(api_module, "_run_team_background", side_effect=paused_run):
                response = await client.post(f"/api/v1/cases/{self.case_id}/team/run", headers=self.operator)
                self.assertEqual(response.status_code, 202, response.text)
                await asyncio.wait_for(started.wait(), 5)
                task = api_module.BACKGROUND_TEAM_TASKS[self.case_id]
                try:
                    reset = await client.post("/api/v1/demo/reset", headers=self.operator)
                    self.assertEqual(reset.status_code, 409, reset.text)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    await asyncio.sleep(0)
            # A dead task releases the runtime lease, but its durable QUEUED
            # record must still prevent reset until explicitly handled.
            with acquire_runtime_lease(self.store, exclusive=True):
                pass
            reset = await client.post("/api/v1/demo/reset", headers=self.operator)
            self.assertEqual(reset.status_code, 409, reset.text)

    async def test_cancel_before_coroutine_start_releases_background_lease(self):
        with acquire_runtime_lease(self.store) as lease:
            token = CURRENT_LEASE.set(lease)
            try:
                api_module._spawn_team_background(self.case_id, "INVESTIGATION")
                task = api_module.BACKGROUND_TEAM_TASKS[self.case_id]
                task.cancel()
            finally:
                CURRENT_LEASE.reset(token)
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertFalse(api_module.BACKGROUND_TEAM_TASKS)
        with acquire_runtime_lease(self.store, exclusive=True):
            pass

    async def test_durable_recovery_and_money_holds_cannot_be_erased(self):
        case = self.store.get_case(self.case_id)
        async with self.client() as client:
            for blocker in ("RECOVERY_REQUIRED", "STARTING", "HOLD", "PREPARED"):
                with self.subTest(blocker=blocker):
                    if blocker == "RECOVERY_REQUIRED":
                        self.store.save_case({**case, "status": blocker})
                    elif blocker == "STARTING":
                        self.store.save_case({**case, "team_run": {"status": blocker}})
                    elif blocker == "HOLD":
                        self.gateway.journal.hold("order:test", self.case_id, "injected uncertainty")
                    else:
                        self.gateway.journal.prepare("unresolved", self.case_id, "order:test", "submit", "hash")
                    before = self.store.list_cases()
                    result = await client.post("/api/v1/demo/reset", headers=self.operator)
                    self.assertEqual(result.status_code, 409, result.text)
                    self.assertEqual(self.store.list_cases(), before)
                    self.assertEqual(self.state_path.read_text(), "preserve-before-reset")
                    # Restore the test fixture using its current revision;
                    # reusing the original snapshot is now correctly rejected.
                    restored = {**case, "_case_revision": self.store.get_case(self.case_id).get("_case_revision", 0)}
                    self.store.save_case(restored)
                    case = restored
                    self.gateway.journal.reconcile(self.case_id)

    async def test_reprepare_refuses_unreconciled_terminal_case(self):
        case = self.store.get_case(self.case_id)
        self.store.save_case({**case, "status": "FAILED"})
        self.gateway.journal.hold("order:test", self.case_id, "injected uncertainty")
        before = self.store.get_case(self.case_id)
        async with self.client() as client:
            result = await client.post(f"/api/v1/cases/{self.case_id}/reprepare", headers=self.operator)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.store.get_case(self.case_id), before)
        self.assertEqual(self.gateway.journal.metrics()["money_frozen_channels"], 1)

    async def test_lock_unavailable_fails_closed_without_exposing_connection_details(self):
        with patch("revguard.runtime_barrier.acquire_runtime_lease", side_effect=OSError("secret-dsn")):
            async with self.client() as client:
                result = await client.post("/api/v1/demo/reset", headers=self.operator)
                self.assertEqual(result.status_code, 503)
                self.assertNotIn("secret-dsn", result.text)
                self.assertEqual((await client.get("/api/v1/health/live")).status_code, 200)
        self.assertEqual(self.state_path.read_text(), "preserve-before-reset")

    def test_independent_sessions_exclude_reset_without_consuming_write_pool(self):
        with acquire_runtime_lease(self.store), acquire_runtime_lease(self.store):
            self.assertEqual(self.store.count_cases(), 8)
            with self.assertRaises(RuntimeBusy):
                acquire_runtime_lease(self.store, exclusive=True)
        with acquire_runtime_lease(self.store, exclusive=True):
            with self.assertRaises(RuntimeBusy):
                acquire_runtime_lease(self.store)

    def test_direct_mcp_task_cannot_invoke_during_reset(self):
        case = self.store.get_case(self.case_id)
        task = create_agent_task(case, "CaseNormalizeSkill", {"raw_case": case})
        self.store.save_agent_task(task)
        with acquire_runtime_lease(self.store, exclusive=True):
            with self.assertRaises(RuntimeBusy):
                execute_agent_task(
                    task_id=task["task_id"], case_id=self.case_id,
                    skill_name=task["skill_name"], skill_input=task["input"],
                    actor=task["assigned_actor"], store=self.store, gateway=self.gateway,
                )
        self.assertEqual(self.store.get_agent_task(task["task_id"])["status"], "PENDING")
        self.assertEqual(self.store.list_agent_task_results(task["task_id"]), [])

    def test_seed_failure_rolls_back_entire_reset_and_audit(self):
        before = self.store.list_cases()
        audit = self.store.list_audit(self.case_id)
        first = self.store.get_case(self.case_id)
        second = {**first, "case_id": "INVALID"}
        del second["status"]
        with patch.dict(os.environ, {"REVGUARD_ALLOW_DATABASE_RESET": "true"}):
            with self.assertRaises(KeyError):
                self.store.reset(seed_cases=[(first, "valid.json"), (second, "invalid.json")])
        self.assertEqual(self.store.list_cases(), before)
        self.assertEqual(self.store.list_audit(self.case_id), audit)
        if self.store.backend == "postgresql-polardb":
            self.assertTrue(self.store.verify_audit_chain()["valid"])
