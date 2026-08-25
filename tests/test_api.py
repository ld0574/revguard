"""RevGuard API 冒烟测试（tests/test_api.py）。

覆盖 wait 审批模式下 API 独有的关键路径：
  创建案件 → 运行至 WAITING_FOR_APPROVAL 挂起 → 人工审批 → 自动续跑执行与验证 → CLOSED，
以及统一工具契约入口、Skill 清单、Trace/报告导出与错误分支（404/409）。

依赖 fastapi + httpx（requirements.txt 之外仅测试需要 httpx）；
纯标准库环境下自动跳过，不影响 `python3 -m unittest discover -s tests`。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import revguard.api 之前设置：api.py 在模块级读取环境变量
_TMP = tempfile.mkdtemp(prefix="revguard_api_test_")
os.environ["REVGUARD_DB_PATH"] = str(Path(_TMP) / "api_test.db")
os.environ["REVGUARD_OUTPUT_DIR"] = str(Path(_TMP) / "outputs")
os.environ["REVGUARD_REPORT_DIR"] = str(Path(_TMP) / "reports")
os.environ["REVGUARD_APPROVAL_MODE"] = "wait"      # 服务端默认：挂起等待人工审批
os.environ["REVGUARD_FINANCE_FAIL_TIMES"] = "1"    # 故障注入：验证重试路径也走 API
os.environ["REVGUARD_ALLOW_INSECURE_DEMO_KEYS"] = "true"
os.environ["REVGUARD_APPROVAL_SIGNING_KEY"] = "revguard-api-test-signing-key-at-least-32-bytes"
os.environ["REVGUARD_GATEWAY_STATE_PATH"] = str(Path(_TMP) / "gateway.json")
os.environ["REVGUARD_ENABLE_LEGACY_TOOL_API"] = "true"
os.environ["REVGUARD_ENABLE_RECORDING_UI"] = "true"

try:
    import httpx

    from revguard import api as api_module
    from revguard.api import app, store
    from revguard.models import Case, CaseStatus
    from revguard.trace import Tracer
    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - 纯标准库环境跳过
    _IMPORT_ERROR = exc


class ASGITestClient:
    """基于 httpx ASGITransport 的无网络同步测试薄封装。"""

    def request(self, method: str, path: str, **kwargs):
        async def _request():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(_request())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)


@unittest.skipIf(_IMPORT_ERROR is not None, f"缺少 API 依赖: {_IMPORT_ERROR}")
class TestApiSmoke(unittest.TestCase):
    """端到端走通 API 层；编排内核正确性由 test_golden_e2e.py 保证。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = ASGITestClient()
        cls.operator = {"Authorization": "Bearer rg-demo-operator-key"}
        cls.orchestrator = {"Authorization": "Bearer rg-demo-orchestrator-key"}
        cls.viewer = {"Authorization": "Bearer rg-demo-viewer-key-1"}
        cls.approver = {"Authorization": "Bearer rg-demo-approver-key"}
        cls.evidence = {"Authorization": "Bearer rg-demo-evidence-key"}
        cls.intake = {"Authorization": "Bearer rg-demo-intake-key-1"}
        cls.executor = {"Authorization": "Bearer rg-demo-executor-key"}
        # 直接以 GOLDEN-001 的输入播种一个案件（等价于 scripts/seed_demo.py）
        spec = json.loads((ROOT / "data" / "golden_cases" / "GOLDEN-001.json")
                          .read_text(encoding="utf-8"))
        raw = spec["input"]
        cls.case_id = raw["case_id"]
        case = Case(
            case_id=cls.case_id, case_type=raw["case_type"], source=raw["source"],
            partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
            order_id=raw.get("order_id"), description=raw.get("description", ""),
            claim=raw.get("claim", {}),
            entities={"partner_id": raw.get("partner_id"),
                      "partner_name": raw.get("partner_name"),
                      "order_id": raw.get("order_id"), "contract_id": None},
            status=CaseStatus.CREATED.value,
        ).to_dict()
        store.save_case(case)

    # 用例按名字字母序执行：test_01 → test_02 …… 依赖顺序用编号显式表达
    def test_01_health_and_skills(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        resp = self.client.get("/api/v1/skills", headers=self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["skills"]), 16)

    def test_02_tool_gateway_contract(self):
        """统一工具契约入口：成功与权限错误都必须是结构化信封。"""
        resp = self.client.post("/api/v1/tools/call", json={
            "tool_name": "crm.get_partner",
            "parameters": {"partner_name": "Nairobi Solar Solutions Ltd"},
            "case_id": self.case_id,
        }, headers={**self.evidence,
                    "X-Request-ID": "REQ-API-TEST",
                    "X-AgentTeams-Message-ID": "MATRIX-API-TEST"})
        body = resp.json()
        self.assertTrue(body["success"], body)
        self.assertTrue(body["tool_receipt"].startswith("RCPT-"))
        self.assertEqual(resp.headers["X-Request-ID"], "REQ-API-TEST")
        spans = Tracer(store, self.case_id).export()["spans"]
        remote = [span for span in spans if span["kind"] == "REMOTE_TOOL"]
        self.assertEqual(remote[-1]["actor"], "revguard-evidence")
        self.assertEqual(remote[-1]["inputs"]["agentteams_message_id"], "MATRIX-API-TEST")

        # 未登记工具不属于历史只读白名单，必须在 Gateway 前被拒绝。
        resp = self.client.post("/api/v1/tools/call", json={
            "tool_name": "crm.no_such_tool", "parameters": {},
            "case_id": self.case_id}, headers=self.evidence)
        self.assertEqual(resp.status_code, 403)

    def test_03_run_case_suspends_at_approval(self):
        """wait 模式：L2 风险案件必须停在 WAITING_FOR_APPROVAL。"""
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/run", headers=self.operator)
        self.assertEqual(resp.status_code, 200, resp.text)
        case = resp.json()["case"]
        self.assertEqual(case["status"], CaseStatus.WAITING_FOR_APPROVAL.value)
        self.assertEqual(case["risk_level"], "L2")

    def test_04_duplicate_run_rejected(self):
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/run", headers=self.operator)
        self.assertEqual(resp.status_code, 409)

    def test_05_approval_resumes_to_closed(self):
        """人工批准后自动续跑：受控执行 + 独立验证 + 归档。"""
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED", "comment": "差额属实，同意补付"},
            headers=self.approver)
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["case"]["status"], CaseStatus.CLOSED.value)
        self.assertEqual(body["verification"]["verification_status"], "PASSED")
        self.assertNotIn("approval_token", body["approval"])
        self.assertTrue(body["approval"]["approval_token_ref"].startswith("sha256:"))

        # 审批已完结，再次审批必须 409
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED"}, headers=self.approver)
        self.assertEqual(resp.status_code, 409)

    def test_06_trace_and_report_exported(self):
        resp = self.client.get(f"/api/v1/cases/{self.case_id}/trace", headers=self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.json()["spans"]), 0)
        approval_token = store.get_approval(self.case_id)["approval_token"]
        self.assertNotIn(approval_token, json.dumps(resp.json(), ensure_ascii=False))

        resp = self.client.get(f"/api/v1/cases/{self.case_id}/report", headers=self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.case_id, resp.json()["markdown"])
        self.assertNotIn(approval_token, resp.json()["markdown"])

    def test_06a_dashboard_projection_is_complete_and_sanitized(self):
        resp = self.client.get(
            f"/api/v1/cases/{self.case_id}/dashboard", headers=self.viewer
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["case"]["case_id"], self.case_id)
        self.assertEqual(body["disclosure"]["business_data"], "synthetic")
        self.assertEqual(body["disclosure"]["ledger"], "simulated")
        self.assertGreater(len(body["evidence"]), 0)
        self.assertGreater(body["trace"]["span_count"], 0)
        self.assertTrue(body["report_available"])
        self.assertTrue(body["approval"]["approval_token_ref"].startswith("sha256:"))
        raw_approval_token = store.get_approval(self.case_id)["approval_token"]
        raw_rollback_tokens = [
            item.get("rollback_token") for item in store.list_executions(self.case_id)
            if item.get("rollback_token")
        ]
        serialized = json.dumps(body, ensure_ascii=False)
        self.assertNotIn(raw_approval_token, serialized)
        for token in raw_rollback_tokens:
            self.assertNotIn(token, serialized)

    def test_07_not_found(self):
        self.assertEqual(self.client.get("/api/v1/cases/CASE-NOPE", headers=self.viewer).status_code, 404)
        self.assertEqual(self.client.get("/api/v1/cases/CASE-NOPE/trace", headers=self.viewer).status_code, 404)

    def test_08_create_case_via_api(self):
        resp = self.client.post("/api/v1/cases", json={
            "partner_name": "Nairobi Prime Electronics",
            "description": "API 创建的手工案件",
            "claim": {"actual_amount": 100, "expected_amount": 200, "currency": "KES"}},
            headers=self.operator)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], CaseStatus.CREATED.value)

    def test_09_authentication_and_role_boundaries(self):
        self.assertEqual(self.client.get("/api/v1/cases").status_code, 401)
        self.assertEqual(self.client.get(
            "/api/v1/cases", headers={"Authorization": "Bearer invalid-key-value"}
        ).status_code, 401)
        # Evidence Worker 不能代替审批人。
        self.assertEqual(self.client.post(
            "/api/v1/cases/CASE-NOPE/approval", json={"decision": "APPROVED"},
            headers=self.evidence,
        ).status_code, 403)
        old_legacy = api_module.ENABLE_LEGACY_TOOL_API
        try:
            api_module.ENABLE_LEGACY_TOOL_API = False
            legacy = self.client.post("/api/v1/tools/call", json={
                "tool_name": "crm.get_order", "parameters": {}, "case_id": self.case_id,
            }, headers=self.evidence)
            self.assertEqual(legacy.status_code, 410)
        finally:
            api_module.ENABLE_LEGACY_TOOL_API = old_legacy

        try:
            api_module.ENABLE_LEGACY_TOOL_API = True
            legacy_write = self.client.post("/api/v1/tools/call", json={
                "tool_name": "commission.create_adjustment_draft",
                "parameters": {}, "case_id": self.case_id,
            }, headers=self.evidence)
            self.assertEqual(legacy_write.status_code, 403)
            executor_read = self.client.post("/api/v1/tools/call", json={
                "tool_name": "finance.get_commission_ledger",
                "parameters": {"order_id": "EZ202608001"},
                "case_id": self.case_id,
            }, headers=self.executor)
            self.assertEqual(executor_read.status_code, 403)
        finally:
            api_module.ENABLE_LEGACY_TOOL_API = old_legacy

    def test_09b_cases_cursor_pagination(self):
        first = self.client.get("/api/v1/cases?limit=1", headers=self.viewer)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["cases"]), 1)
        if first.json()["next_cursor"]:
            second = self.client.get(
                "/api/v1/cases",
                params={"limit": 1, "cursor": first.json()["next_cursor"]},
                headers=self.viewer,
            )
            self.assertEqual(second.status_code, 200)
            self.assertNotEqual(
                first.json()["cases"][0]["case_id"],
                second.json()["cases"][0]["case_id"],
            )
        invalid = self.client.get("/api/v1/cases?cursor=not-base64", headers=self.viewer)
        self.assertEqual(invalid.status_code, 400)

    def test_10_versioned_skill_invoke_contract(self):
        response = self.client.post("/api/v1/skills/CaseNormalizeSkill/invoke", json={
            "case_id": "CASE-SKILL-API",
            "input": {"raw_case": {"partner_id": "AGT-10001", "order_id": "EZ202608001"}},
        }, headers={**self.intake, "X-Request-ID": "REQ-SKILL-API",
                    "X-AgentTeams-Message-ID": "MATRIX-SKILL-API"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["version"], "1.0.0")
        self.assertTrue(body["skill_receipt"].startswith("SKR-"))
        self.assertEqual(response.headers["X-Request-ID"], "REQ-SKILL-API")
        self.assertEqual(response.headers["X-Skill-Receipt"], body["skill_receipt"])

        catalog = self.client.get("/api/v1/skills", headers=self.viewer).json()["skills"]
        normalize = next(item for item in catalog if item["name"] == "CaseNormalizeSkill")
        self.assertEqual(normalize["input_schema"]["required"], ["raw_case"])
        risk = next(item for item in catalog if item["name"] == "RiskClassifySkill")
        self.assertIn("currency", risk["input_schema"]["required"])
        self.assertIn("case_type", risk["input_schema"]["required"])

        invalid = self.client.post("/api/v1/skills/CaseNormalizeSkill/invoke", json={
            "case_id": "CASE-SKILL-API", "input": {"unexpected": True},
        }, headers=self.intake)
        self.assertEqual(invalid.status_code, 422)
        self.assertIn("raw_case", invalid.text)

        forbidden = self.client.post("/api/v1/skills/LedgerAdjustSkill/invoke", json={
            "case_id": "CASE-SKILL-API", "input": {},
        }, headers=self.evidence)
        self.assertEqual(forbidden.status_code, 403)

    def test_11_waiting_for_evidence_can_resume(self):
        spec = json.loads((ROOT / "data" / "golden_cases" / "GOLDEN-003.json")
                          .read_text(encoding="utf-8"))
        raw = spec["input"]
        case_id = "CASE-EVIDENCE-RESUME"
        case = Case(
            case_id=case_id, case_type=raw["case_type"], source=raw["source"],
            partner_name=raw["partner_name"], description=raw["description"],
            claim=raw["claim"],
            entities={"partner_id": None, "partner_name": raw["partner_name"],
                      "order_id": None, "contract_id": None},
        ).to_dict()
        store.save_case(case)

        waiting = self.client.post(f"/api/v1/cases/{case_id}/run", headers=self.operator)
        self.assertEqual(waiting.status_code, 200, waiting.text)
        self.assertEqual(waiting.json()["case"]["status"],
                         CaseStatus.WAITING_FOR_EVIDENCE.value)

        resumed = self.client.post(f"/api/v1/cases/{case_id}/evidence/resume", json={
            "order_id": "EZ202607101",
        }, headers=self.operator)
        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertNotEqual(resumed.json()["case"]["status"],
                            CaseStatus.WAITING_FOR_EVIDENCE.value)
        events = store.list_audit(case_id)
        self.assertTrue(any(event["event"] == "EVIDENCE_SUPPLIED" for event in events))

    def test_12_agent_task_binds_case_skill_actor_and_result(self):
        case_id = "CASE-AGENT-BRIDGE"
        case = Case(
            case_id=case_id, case_type="COMMISSION_UNDERPAYMENT", source="MATRIX",
            partner_id="AGT-10001", order_id="EZ202608001",
            entities={"partner_id": "AGT-10001", "partner_name": None,
                      "order_id": "EZ202608001", "contract_id": None},
        ).to_dict()
        store.save_case(case)
        skill_input = {"raw_case": case}
        dispatched = self.client.post(f"/api/v1/cases/{case_id}/agent-tasks", json={
            "skill_name": "CaseNormalizeSkill", "input": skill_input,
        }, headers=self.orchestrator)
        self.assertEqual(dispatched.status_code, 201, dispatched.text)
        task = dispatched.json()
        self.assertEqual(task["assigned_actor"], "revguard-intake")
        self.assertEqual(task["status"], "PENDING")

        wrong_worker = self.client.post(
            "/api/v1/skills/CaseNormalizeSkill/invoke",
            json={"case_id": case_id, "input": skill_input},
            headers={**self.evidence, "X-RevGuard-Task-ID": task["task_id"]},
        )
        self.assertEqual(wrong_worker.status_code, 403)

        completed = self.client.post(
            "/api/v1/skills/CaseNormalizeSkill/invoke",
            json={"case_id": case_id, "input": skill_input},
            headers={**self.intake, "X-RevGuard-Task-ID": task["task_id"],
                     "X-AgentTeams-Message-ID": "MATRIX-AGENT-BRIDGE"},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        viewer = self.client.get(
            f"/api/v1/cases/{case_id}/agent-tasks", headers=self.viewer
        )
        self.assertEqual(viewer.status_code, 403)
        tasks = self.client.get(
            f"/api/v1/cases/{case_id}/agent-tasks", headers=self.intake
        ).json()["tasks"]
        self.assertEqual(tasks[0]["status"], "SUCCEEDED")
        self.assertEqual(tasks[0]["skill_receipt"], completed.json()["skill_receipt"])
        self.assertEqual(tasks[0]["result"]["entities"]["partner_id"], "AGT-10001")

        replay = self.client.post(
            "/api/v1/skills/CaseNormalizeSkill/invoke",
            json={"case_id": case_id, "input": skill_input},
            headers={**self.intake, "X-RevGuard-Task-ID": task["task_id"]},
        )
        self.assertEqual(replay.status_code, 409)

        stale = self.client.post(f"/api/v1/cases/{case_id}/agent-tasks", json={
            "skill_name": "CaseNormalizeSkill", "input": skill_input,
        }, headers=self.orchestrator).json()
        changed_input = self.client.post(
            "/api/v1/skills/CaseNormalizeSkill/invoke",
            json={"case_id": case_id,
                  "input": {"raw_case": {**case, "description": "changed by worker"}}},
            headers={**self.intake, "X-RevGuard-Task-ID": stale["task_id"]},
        )
        self.assertEqual(changed_input.status_code, 409)
        mutated = store.get_case(case_id)
        mutated["description"] = "new evidence arrived"
        store.save_case(mutated)
        stale_snapshot = self.client.post(
            "/api/v1/skills/CaseNormalizeSkill/invoke",
            json={"case_id": case_id, "input": skill_input},
            headers={**self.intake, "X-RevGuard-Task-ID": stale["task_id"]},
        )
        self.assertEqual(stale_snapshot.status_code, 409)

        dispatcher_tasks = self.client.get(
            f"/api/v1/cases/{case_id}/agent-tasks", headers=self.orchestrator
        )
        self.assertEqual(dispatcher_tasks.status_code, 200)
        self.assertGreaterEqual(len(dispatcher_tasks.json()["tasks"]), 2)

    def test_13_rejection_finalizes_and_closes(self):
        spec = json.loads((ROOT / "data" / "golden_cases" / "GOLDEN-002.json")
                          .read_text(encoding="utf-8"))
        raw = spec["input"]
        case_id = "CASE-API-REJECT"
        case = Case(
            case_id=case_id, case_type=raw["case_type"], source=raw["source"],
            partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
            order_id=raw.get("order_id"), description=raw.get("description", ""),
            claim=raw.get("claim", {}),
            entities={"partner_id": raw.get("partner_id"),
                      "partner_name": raw.get("partner_name"),
                      "order_id": raw.get("order_id"), "contract_id": None},
        ).to_dict()
        store.save_case(case)
        waiting = self.client.post(
            f"/api/v1/cases/{case_id}/run", headers={
                **self.operator, "X-Request-ID": "REQ-REJECT-RUN",
            },
        )
        self.assertEqual(waiting.status_code, 200, waiting.text)
        self.assertEqual(waiting.headers["X-Request-ID"], "REQ-REJECT-RUN")
        self.assertEqual(waiting.json()["case"]["status"],
                         CaseStatus.WAITING_FOR_APPROVAL.value)

        rejected = self.client.post(f"/api/v1/cases/{case_id}/approval", json={
            "decision": "REJECTED", "comment": "证据不足，驳回",
        }, headers=self.approver)
        self.assertEqual(rejected.status_code, 200, rejected.text)
        body = rejected.json()
        self.assertEqual(body["case"]["status"], CaseStatus.CLOSED.value)
        self.assertNotIn("approval_token", body["approval"])
        self.assertEqual(body["approval"]["approval_token_ref"], "-")
        self.assertIsNone(body["verification"])
        self.assertTrue((Path(os.environ["REVGUARD_OUTPUT_DIR"]) / "traces" /
                         f"{case_id}.json").exists())
        self.assertTrue((Path(os.environ["REVGUARD_OUTPUT_DIR"]) / "case_memory" /
                         f"{case_id}.json").exists())
        self.assertTrue((Path(os.environ["REVGUARD_REPORT_DIR"]) /
                         f"{case_id}.md").exists())

    def test_14_internal_error_is_stable_and_correlated(self):
        case_id = "CASE-API-ERROR"
        store.save_case(Case(
            case_id=case_id, case_type="COMMISSION_UNDERPAYMENT", source="API",
        ).to_dict())

        class _BrokenOrchestrator:
            def run_case(self, _case):
                raise RuntimeError("sensitive-internal-detail")

        with patch.object(api_module, "_orchestrator", return_value=_BrokenOrchestrator()):
            failed = self.client.post(
                f"/api/v1/cases/{case_id}/run",
                headers={**self.operator, "X-Request-ID": "REQ-STABLE-500"},
            )
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.headers["X-Request-ID"], "REQ-STABLE-500")
        self.assertEqual(failed.json()["detail"], {
            "code": "CASE_RUN_FAILED", "request_id": "REQ-STABLE-500",
        })
        self.assertNotIn("sensitive-internal-detail", failed.text)
        event = next(item for item in store.list_audit(case_id)
                     if item["event"] == "CASE_RUN_FAILED")
        self.assertIn("RuntimeError", event["detail"])

    def test_15_recording_reset_is_explicit_and_role_guarded(self):
        forbidden = self.client.post("/api/v1/demo/reset", headers=self.viewer)
        self.assertEqual(forbidden.status_code, 403)

        reset = self.client.post("/api/v1/demo/reset", headers=self.operator)
        self.assertEqual(reset.status_code, 200, reset.text)
        snapshot = reset.json()["snapshot"]
        self.assertEqual(snapshot["case"]["case_id"], "CASE-2026-0008")
        self.assertEqual(snapshot["case"]["status"], CaseStatus.CREATED.value)
        self.assertEqual(snapshot["trace"]["span_count"], 0)

        old_recording = api_module.ENABLE_RECORDING_UI
        try:
            api_module.ENABLE_RECORDING_UI = False
            disabled = self.client.post("/api/v1/demo/reset", headers=self.operator)
            self.assertEqual(disabled.status_code, 404)
        finally:
            api_module.ENABLE_RECORDING_UI = old_recording


if __name__ == "__main__":
    unittest.main()
