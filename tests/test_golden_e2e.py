"""Golden Case 端到端测试：以 golden_cases/*.json 的期望值为验收标准。

对应设计文档 21.2 质量验收：Golden Case 计算准确率 100%、
高风险误自动执行 0 次、执行后验证覆盖率 100%。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from revguard.mocks import ToolGateway
from revguard.orchestrator import Orchestrator
from revguard.store import Store
from scripts.seed_demo import seed

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "data" / "fixtures"


class TestGoldenCasesE2E(unittest.TestCase):
    """每个 Golden Case 独立数据库运行，结果必须与期望完全一致。"""

    def _run(self, spec_path: Path) -> tuple[dict, dict, dict]:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "test.db")
            store = Store(db)
            gateway = ToolGateway(FIXTURES, finance_fail_times=1)
            orch = Orchestrator(store, gateway,
                                output_dir=Path(tmp) / "outputs",
                                report_dir=Path(tmp) / "reports")
            seed(db)
            case = store.get_case(spec["input"]["case_id"])
            state = orch.run_case(case)
            final = store.get_case(case["case_id"])
            store.close()
        return spec, final, state

    def test_golden_001_underpayment(self):
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-001.json")
        exp = spec["expected"]
        self.assertEqual(state["policy_decision"]["policy_version"], exp["policy_version"])
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(final["risk_level"], exp["risk_level"])
        self.assertEqual(final["status"], exp["final_status"])
        self.assertEqual(state["verification"]["verification_status"], exp["verification_status"])
        # 审批必须真实发生且带凭证
        self.assertEqual(state["approval"]["status"], "APPROVED")
        self.assertTrue(state["approval"]["approval_token"])

    def test_golden_002_tier_conflict(self):
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-002.json")
        exp = spec["expected"]
        self.assertEqual(state["tier_resolution"]["tier"], exp["tier_at_order_date"])
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(final["status"], exp["final_status"])
        self.assertEqual(state["verification"]["verification_status"], exp["verification_status"])

    def test_golden_003_evidence_gap(self):
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-003.json")
        exp = spec["expected"]
        self.assertEqual(final["status"], exp["final_status"])
        # 证据不足时绝不能产出计算结果/执行动作（不生成虚假确定性结论）
        self.assertNotIn("calculation_result", state)
        self.assertNotIn("executions", state)
        self.assertTrue(state["errors"])

    def test_high_risk_never_auto_executed(self):
        """质量红线：L3 案件不允许出现任何执行记录。"""
        for fp in sorted((ROOT / "data" / "golden_cases").glob("*.json")):
            _, final, state = self._run(fp)
            if final.get("risk_level") == "L3":
                self.assertNotIn("executions", state)


if __name__ == "__main__":
    unittest.main()
