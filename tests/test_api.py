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
import time
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
os.environ["REVGUARD_HITL_MATRIX_USERS_JSON"] = json.dumps({
    "@finance-lead:test": {
        "actor": "finance.lead",
        "display_name": "测试财务负责人",
    },
})

try:
    import httpx

    from revguard import api as api_module
    from revguard.api import app, store
    from revguard.hitl import HumanIdentity, issue_human_action_assertion
    from revguard.models import Case, CaseStatus, TaskStatus, utc_now
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

    @classmethod
    def human_headers(cls, case_id: str, action: str) -> dict[str, str]:
        approval = store.get_approval(case_id) or {}
        token = issue_human_action_assertion(
            api_module.HITL_SIGNER,
            HumanIdentity(
                sub="@finance-lead:test",
                actor="finance.lead",
                display_name="测试财务负责人",
                auth_time=int(time.time()),
            ),
            case_id=case_id,
            approval_id=approval.get("approval_id", ""),
            action=action,
            ttl_seconds=120,
        )
        return {"Authorization": f"Bearer {token}"}

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

    def test_04a_matrix_login_issues_action_bound_human_proof(self):
        static_key = self.client.post(
            f"/api/v1/cases/{self.case_id}/approval",
            json={"decision": "APPROVED"},
            headers={"Authorization": "Bearer rg-demo-approver-key"},
        )
        self.assertEqual(static_key.status_code, 401)
        identity = HumanIdentity(
            sub="@finance-lead:test",
            actor="finance.lead",
            display_name="测试财务负责人",
            auth_time=int(time.time()),
        )
        with patch.object(
            api_module.HITL_IDENTITY_PROVIDER,
            "authenticate",
            AsyncMock(return_value=identity),
        ):
            resp = self.client.post(
                f"/api/v1/cases/{self.case_id}/human-action/assertion",
                json={
                    "username": "finance-lead",
                    "password": "not-persisted",
                    "action": "APPROVED",
                },
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["identity"]["sub"], "@finance-lead:test")
        self.assertEqual(body["binding"]["case_id"], self.case_id)
        self.assertEqual(body["binding"]["action"], "APPROVED")
        self.assertNotIn("not-persisted", json.dumps(body))

    def test_05_approval_resumes_to_closed(self):
        """人工批准后自动续跑：受控执行 + 独立验证 + 归档。"""
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED", "comment": "差额属实，同意补付"},
            headers=self.human_headers(self.case_id, "APPROVED"))
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["case"]["status"], CaseStatus.CLOSED.value)
        self.assertEqual(body["verification"]["verification_status"], "PASSED")
        self.assertNotIn("approval_token", body["approval"])
        self.assertTrue(body["approval"]["approval_token_ref"].startswith("sha256:"))

        # 审批已完结，再次审批必须 409
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED"}, headers=self.human_headers(self.case_id, "APPROVED"))
        self.assertEqual(resp.status_code, 409)

    def test_06_trace_and_report_exported(self):
        resp = self.client.get(f"/api/v1/cases/{self.case_id}/trace", headers=self.viewer)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.json()["spans"]), 0)
        approval_token = store.get_approval(self.case_id)["approval_token"]
        self.assertNotIn(approval_token, json.dumps(resp.json(), ensure_ascii=False))

        resp = self.client.get(f"/api/v1/cases/{self.case_id}/report", headers=self.viewer)
        self.assertEqual(resp.status_code, 200)
        markdown = resp.json()["markdown"]
        self.assertIn(self.case_id, markdown)
        self.assertIn("证据链", markdown)
        self.assertIn("独立验证（重新查询，非执行主体自证）", markdown)
        self.assertIn("调用链与审计摘要", markdown)
        self.assertNotIn("Evidence Package", markdown)
        self.assertNotIn("Policy Time Travel", markdown)
        self.assertNotIn("CRM_MOCK", markdown)
        self.assertNotIn("FINANCE_MOCK", markdown)
        self.assertNotIn(approval_token, markdown)

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
        # 静态 Worker/API key 不能代替动作绑定的人类身份证明。
        self.assertEqual(self.client.post(
            f"/api/v1/cases/{self.case_id}/approval", json={"decision": "APPROVED"},
            headers=self.evidence,
        ).status_code, 409)
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
                     "X-AgentTeams-Message-ID": "MATRIX-AGENT-BRIDGE",
                     "X-RevGuard-Transport": "higress-mcp"},
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
        self.assertEqual(tasks[0]["skill_transport"], "higress-mcp")
        self.assertEqual(tasks[0]["skill_receipt"], completed.json()["skill_receipt"])
        self.assertEqual(tasks[0]["result"]["entities"]["partner_id"], "AGT-10001")
        results = self.client.get(
            f"/api/v1/agent-tasks/{task['task_id']}/results", headers=self.intake
        )
        self.assertEqual(results.status_code, 200)
        self.assertEqual(results.json()["results"][0]["status"], "SUCCEEDED")

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

        failed = self.client.post(f"/api/v1/cases/{case_id}/agent-tasks", json={
            "skill_name": "CaseNormalizeSkill", "input": skill_input,
        }, headers=self.orchestrator).json()
        store.transition_agent_task(
            failed["task_id"], expected={TaskStatus.PENDING.value},
            status=TaskStatus.RUNNING.value,
        )
        store.complete_agent_task(
            failed["task_id"], status=TaskStatus.FAILED_FINAL.value,
            error={"type": "WorkerLost", "message": "lease expired"},
        )
        reassigned = self.client.post(
            f"/api/v1/agent-tasks/{failed['task_id']}/reassign",
            json={"reason": "Worker 已离线，转交同职能备用实例"},
            headers=self.orchestrator,
        )
        self.assertEqual(reassigned.status_code, 201, reassigned.text)
        self.assertEqual(reassigned.json()["supersedes_task_id"], failed["task_id"])

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
        }, headers=self.human_headers(case_id, "REJECTED"))
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
        reset_body = reset.json()
        self.assertEqual(len(reset_body["case_ids"]), 8)
        self.assertIn("CASE-2026-0007", reset_body["case_ids"])
        self.assertIn("CASE-2026-0008", reset_body["case_ids"])
        snapshot = reset_body["snapshot"]
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

    def test_16_health_and_queryable_metrics(self):
        live = self.client.get("/api/v1/health/live")
        self.assertEqual(live.status_code, 200)
        ready = self.client.get("/api/v1/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["backend"], "sqlite-demo")
        metrics = self.client.get("/api/v1/ops/metrics", headers=self.viewer)
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("cases_by_status", metrics.json())
        prometheus = self.client.get(
            "/api/v1/ops/metrics/prometheus", headers=self.viewer
        )
        self.assertEqual(prometheus.status_code, 200)
        self.assertIn("revguard_cases_total", prometheus.text)
        evidence = self.client.get("/api/v1/ops/evidence", headers=self.viewer)
        self.assertEqual(evidence.status_code, 200)
        self.assertEqual(evidence.json()["release"], "0.4.0")
        self.assertFalse(
            evidence.json()["business_value"]["production_claim_allowed"]
        )
        self.assertEqual(
            evidence.json()["business_value"]["simulation_contract"]
            ["default_assumptions"]["monthly_case_volume"],
            500,
        )
        self.assertEqual(
            evidence.json()["external_validation"]["polardb_pitr_drill"],
            "PENDING_CLOUD_INSTANCE",
        )
        self.assertEqual(
            evidence.json()["self_hosted_polardb"]["deployment"]["application_backend"],
            "postgresql-polardb",
        )

    def test_17_human_assertion_state_guards(self):
        missing = self.client.post(
            "/api/v1/cases/CASE-NOPE/human-action/assertion",
            json={"username": "u", "password": "p", "action": "APPROVED"},
        )
        self.assertEqual(missing.status_code, 404)

        case_id = "CASE-HITL-NO-APPROVAL"
        store.save_case(Case(
            case_id=case_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
        ).to_dict())
        no_approval = self.client.post(
            f"/api/v1/cases/{case_id}/human-action/assertion",
            json={"username": "u", "password": "p", "action": "APPROVED"},
        )
        self.assertEqual(no_approval.status_code, 409)

        closed = self.client.post(
            f"/api/v1/cases/{self.case_id}/human-action/assertion",
            json={"username": "u", "password": "p", "action": "APPROVED"},
        )
        self.assertEqual(closed.status_code, 409)
        non_matrix_resume = self.client.post(
            f"/api/v1/cases/{self.case_id}/human-action/assertion",
            json={"username": "u", "password": "p", "action": "RESUME"},
        )
        self.assertEqual(non_matrix_resume.status_code, 409)

    def test_18_matrix_team_run_is_enqueued_without_faking_worker_results(self):
        case_id = "CASE-MATRIX-QUEUE"
        store.save_case(Case(
            case_id=case_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
        ).to_dict())
        old_transport = api_module.TEAM_TRANSPORT
        try:
            api_module.TEAM_TRANSPORT = "matrix"
            with patch("revguard.api._spawn_team_background") as spawn:
                response = self.client.post(
                    f"/api/v1/cases/{case_id}/team/run", headers=self.operator,
                )
        finally:
            api_module.TEAM_TRANSPORT = old_transport
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["state_status"], "QUEUED")
        self.assertEqual(response.json()["agent_tasks"], [])
        self.assertEqual(store.get_case(case_id)["execution_mode"], "AGENTTEAMS_MATRIX")
        spawn.assert_called_once_with(case_id, "INVESTIGATION")

    def test_19_resume_rejects_unknown_wrong_mode_and_active_runs(self):
        self.assertEqual(self.client.post(
            "/api/v1/cases/CASE-NOPE/team/resume",
        ).status_code, 404)

        local_id = "CASE-RESUME-LOCAL"
        store.save_case(Case(
            case_id=local_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
        ).to_dict())
        self.assertEqual(self.client.post(
            f"/api/v1/cases/{local_id}/team/resume",
        ).status_code, 409)

        idle_id = "CASE-RESUME-IDLE"
        idle = Case(
            case_id=idle_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
        ).to_dict()
        idle["execution_mode"] = "AGENTTEAMS_MATRIX"
        store.save_case(idle)
        self.assertEqual(self.client.post(
            f"/api/v1/cases/{idle_id}/team/resume",
        ).status_code, 409)

        active_id = "CASE-RESUME-ACTIVE"
        active = Case(
            case_id=active_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
            status=CaseStatus.EXECUTING.value,
        ).to_dict()
        active.update({
            "execution_mode": "AGENTTEAMS_MATRIX",
            "team_run": {"status": "RUNNING", "updated_at": utc_now()},
        })
        store.save_case(active)
        self.assertEqual(self.client.post(
            f"/api/v1/cases/{active_id}/team/resume",
        ).status_code, 409)

    def test_15a_mcp_team_api_pauses_and_resumes_after_human_approval(self):
        case_id = "CASE-2026-0008"
        api_module.gateway._verification_tamper_amount = Decimal("1")
        api_module.gateway._verification_tamper_used = False
        started = self.client.post(
            f"/api/v1/cases/{case_id}/team/run",
            headers={**self.operator, "X-Request-ID": "REQ-MCP-TEAM-API"},
        )
        self.assertEqual(started.status_code, 200, started.text)
        self.assertEqual(started.headers["X-Request-ID"], "REQ-MCP-TEAM-API")
        body = started.json()
        self.assertEqual(body["case"]["status"], CaseStatus.WAITING_FOR_APPROVAL.value)
        self.assertEqual(body["case"]["execution_mode"], "MCP_TEAM")
        self.assertEqual(len(body["agent_tasks"]), 8)
        self.assertEqual({item["status"] for item in body["agent_tasks"]}, {"SUCCEEDED"})

        approved = self.client.post(
            f"/api/v1/cases/{case_id}/approval",
            json={"decision": "APPROVED", "comment": "录制测试人工批准"},
            headers=self.human_headers(case_id, "APPROVED"),
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["case"]["status"], CaseStatus.ROLLED_BACK.value)
        tasks = store.list_agent_tasks(case_id)
        self.assertEqual(len(tasks), 20)
        details = [
            json.loads(item["detail"])
            for item in store.list_audit(case_id)
            if item.get("detail")
        ]
        self.assertTrue(any(item.get("transport") == "mcp" for item in details))
        self.assertTrue(any(item.get("simulated_human") is False for item in details))

    def test_15b_stale_matrix_run_requires_approver_and_is_requeued(self):
        case_id = "CASE-STALE-MATRIX"
        case = Case(
            case_id=case_id,
            case_type="COMMISSION_UNDERPAYMENT",
            source="TEST",
            status=CaseStatus.EXECUTING.value,
            order_id="EZ202608001",
            claim={"actual_amount": "0", "expected_amount": "100", "currency": "KES"},
        ).to_dict()
        case.update({
            "execution_mode": "AGENTTEAMS_MATRIX",
            "risk_decision": {
                "risk_level": "L2", "approval_required": True,
                "approver_role": "FINANCE_LEAD",
                "execution_constraints": {"write": True},
                "rollback_plan_required": True, "reason_codes": ["TEST"],
            },
            "team_run": {
                "run_id": "RUN-STALE", "status": "RUNNING",
                "phase": "EXECUTION", "updated_at": "2020-01-01T00:00:00Z",
                "completed_tasks": 3, "total_tasks": 20,
            },
        })
        store.save_case(case)
        created = api_module.gateway.call("workflow.create_approval", {
            "case_id": case_id, "amount": "100", "currency": "KES",
            "component_quota": {"SALES_COMMISSION": "100"},
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "stale recovery",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
        decided = api_module.gateway.call("workflow.decide_approval", {
            "approval_id": created["data"]["approval_id"], "decision": "APPROVED",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])["data"]
        store.save_approval({
            "approval_id": decided["approval_id"], "case_id": case_id, **decided,
        })

        denied = self.client.post(
            f"/api/v1/cases/{case_id}/team/resume", headers=self.operator,
        )
        self.assertEqual(denied.status_code, 401)
        with patch("revguard.api._spawn_team_background") as spawn:
            resumed = self.client.post(
                f"/api/v1/cases/{case_id}/team/resume",
                headers=self.human_headers(case_id, "RESUME"),
            )
        self.assertEqual(resumed.status_code, 202, resumed.text)
        self.assertEqual(resumed.json()["state_status"], "QUEUED")
        self.assertEqual(
            store.get_case(case_id)["team_run"]["recovery"]["strategy"],
            "idempotent-execution-replay",
        )
        spawn.assert_called_once_with(case_id, "EXECUTION")

    def test_15c_failed_rollback_can_only_reopen_into_safety_path(self):
        case_id = "CASE-FAILED-ROLLBACK"
        created = api_module.gateway.call("workflow.create_approval", {
            "case_id": case_id, "amount": "20", "currency": "KES",
            "component_quota": {"SALES_COMMISSION": "20"},
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "rollback recovery",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
        decided = api_module.gateway.call("workflow.decide_approval", {
            "approval_id": created["data"]["approval_id"], "decision": "APPROVED",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])["data"]
        draft = api_module.gateway.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": case_id,
            "component": "SALES_COMMISSION", "amount": "20", "currency": "KES",
        }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
        submitted = api_module.gateway.call("commission.submit_adjustment", {
            "action_id": draft["action_id"],
            "approval_token": decided["approval_token"],
        }, case_id=case_id, actor="revguard-executor", scope=["commission:write"],
            idempotency_key=f"{case_id}:SALES_COMMISSION")["data"]
        original_token = submitted["rollback_token"]
        store.save_execution({
            "action_id": draft["action_id"], "case_id": case_id,
            "action_type": "LEDGER_ADJUST", "status": "SUBMITTED",
            "amount": "20", "currency": "KES", "component": "SALES_COMMISSION",
            "idempotency_key": f"{case_id}:SALES_COMMISSION",
            "before_snapshot": submitted["before_snapshot"],
            "after_snapshot": submitted["after_snapshot"],
            "rollback_token": original_token,
            "ledger_entry": submitted["ledger_entry"],
        })
        store.save_verification(case_id, {
            "verification_status": "FAILED", "rollback_required": True,
            "expected_amount": "0", "actual_amount": "20", "variance": "20",
        })
        case = Case(
            case_id=case_id, case_type="COMMISSION_UNDERPAYMENT", source="TEST",
            status=CaseStatus.FAILED.value, order_id="EZ202608001",
        ).to_dict()
        case.update({
            "execution_mode": "AGENTTEAMS_MATRIX",
            "team_run": {
                "run_id": "RUN-ROLLBACK-FAILED", "status": "FAILED",
                "phase": "EXECUTION", "current_stage": "LedgerReverseSkill",
                "updated_at": "2020-01-01T00:00:00Z",
            },
        })
        store.save_case(case)

        with patch("revguard.api._spawn_team_background") as spawn:
            resumed = self.client.post(
                f"/api/v1/cases/{case_id}/team/resume",
                headers=self.human_headers(case_id, "RESUME"),
            )
        self.assertEqual(resumed.status_code, 202, resumed.text)
        recovered = store.get_case(case_id)
        self.assertEqual(recovered["status"], CaseStatus.ROLLBACK_REQUIRED.value)
        self.assertEqual(recovered["team_run"]["phase"], "ROLLBACK")
        self.assertNotEqual(
            store.list_executions(case_id)[0]["rollback_token"], original_token,
        )
        spawn.assert_called_once_with(case_id, "ROLLBACK")


if __name__ == "__main__":
    unittest.main()
