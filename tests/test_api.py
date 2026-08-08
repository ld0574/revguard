#!/usr/bin/env python3
"""RevGuard API 冒烟测试（tests/test_api.py）。

覆盖 wait 审批模式下 API 独有的关键路径：
  创建案件 → 运行至 WAITING_FOR_APPROVAL 挂起 → 人工审批 → 自动续跑执行与验证 → CLOSED，
以及统一工具契约入口、Skill 清单、Trace/报告导出与错误分支（404/409）。

依赖 fastapi + httpx（requirements.txt 之外仅测试需要 httpx）；
纯标准库环境下自动跳过，不影响 `python3 -m unittest discover -s tests`。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 必须在 import revguard.api 之前设置：api.py 在模块级读取环境变量
_TMP = tempfile.mkdtemp(prefix="revguard_api_test_")
os.environ["REVGUARD_DB_PATH"] = str(Path(_TMP) / "api_test.db")
os.environ["REVGUARD_OUTPUT_DIR"] = str(Path(_TMP) / "outputs")
os.environ["REVGUARD_REPORT_DIR"] = str(Path(_TMP) / "reports")
os.environ["REVGUARD_APPROVAL_MODE"] = "wait"      # 服务端默认：挂起等待人工审批
os.environ["REVGUARD_FINANCE_FAIL_TIMES"] = "1"    # 故障注入：验证重试路径也走 API

try:
    from fastapi.testclient import TestClient

    from revguard.api import app, store
    from revguard.models import Case, CaseStatus
    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - 纯标准库环境跳过
    _IMPORT_ERROR = exc


@unittest.skipIf(_IMPORT_ERROR is not None, f"缺少 API 依赖: {_IMPORT_ERROR}")
class TestApiSmoke(unittest.TestCase):
    """端到端走通 API 层；编排内核正确性由 test_golden_e2e.py 保证。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)
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

        resp = self.client.get("/api/v1/skills")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.json()["skills"]), 14)  # 14 个 Skill 契约

    def test_02_tool_gateway_contract(self):
        """统一工具契约入口：成功与权限错误都必须是结构化信封。"""
        resp = self.client.post("/api/v1/tools/call", json={
            "tool_name": "crm.get_partner",
            "parameters": {"partner_name": "Nairobi Solar Solutions Ltd"},
            "case_id": self.case_id, "actor": "test", "scope": ["crm:read"],
        })
        body = resp.json()
        self.assertTrue(body["success"], body)
        self.assertTrue(body["tool_receipt"].startswith("RCPT-"))

        # 未知工具 → 结构化错误而非 500
        resp = self.client.post("/api/v1/tools/call", json={
            "tool_name": "crm.no_such_tool", "parameters": {},
            "case_id": self.case_id, "actor": "test"})
        self.assertFalse(resp.json()["success"])

    def test_03_run_case_suspends_at_approval(self):
        """wait 模式：L2 风险案件必须停在 WAITING_FOR_APPROVAL。"""
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/run")
        self.assertEqual(resp.status_code, 200, resp.text)
        case = resp.json()["case"]
        self.assertEqual(case["status"], CaseStatus.WAITING_FOR_APPROVAL.value)
        self.assertEqual(case["risk_level"], "L2")

    def test_04_duplicate_run_rejected(self):
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/run")
        self.assertEqual(resp.status_code, 409)

    def test_05_approval_resumes_to_closed(self):
        """人工批准后自动续跑：受控执行 + 独立验证 + 归档。"""
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED", "approver": "finance.lead@example.com",
            "comment": "差额属实，同意补付"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["case"]["status"], CaseStatus.CLOSED.value)
        self.assertEqual(body["verification"]["verification_status"], "PASSED")

        # 审批已完结，再次审批必须 409
        resp = self.client.post(f"/api/v1/cases/{self.case_id}/approval", json={
            "decision": "APPROVED", "approver": "finance.lead@example.com"})
        self.assertEqual(resp.status_code, 409)

    def test_06_trace_and_report_exported(self):
        resp = self.client.get(f"/api/v1/cases/{self.case_id}/trace")
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(resp.json()["spans"]), 0)

        resp = self.client.get(f"/api/v1/cases/{self.case_id}/report")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(self.case_id, resp.json()["markdown"])

    def test_07_not_found(self):
        self.assertEqual(self.client.get("/api/v1/cases/CASE-NOPE").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/cases/CASE-NOPE/trace").status_code, 404)

    def test_08_create_case_via_api(self):
        resp = self.client.post("/api/v1/cases", json={
            "partner_name": "Nairobi Prime Electronics",
            "description": "API 创建的手工案件",
            "claim": {"actual_amount": 100, "expected_amount": 200, "currency": "KES"}})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["status"], CaseStatus.CREATED.value)


if __name__ == "__main__":
    unittest.main()
