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

    def test_golden_004_no_difference(self):
        """申诉不成立：应有与台账一致，零差异直接关闭，绝不产生调整动作。"""
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-004.json")
        exp = spec["expected"]
        self.assertEqual(state["policy_decision"]["policy_version"], exp["policy_version"])
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(state["root_cause_report"]["total_delta"], "0")
        self.assertEqual(final["risk_level"], exp["risk_level"])
        self.assertEqual(final["status"], exp["final_status"])
        # 无差异 => 无审批、无执行
        self.assertNotIn("approval", state)
        self.assertNotIn("executions", state)

    def test_golden_005_missing_whole_order(self):
        """整单漏算：全额补付，差额 6600 走 L2 审批后执行。"""
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-005.json")
        exp = spec["expected"]
        self.assertEqual(state["tier_resolution"]["tier"], exp["tier_at_order_date"])
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(final["risk_level"], exp["risk_level"])
        self.assertEqual(final["status"], exp["final_status"])
        self.assertEqual(state["verification"]["verification_status"], exp["verification_status"])
        # L2 必须真实审批
        self.assertEqual(state["approval"]["status"], "APPROVED")
        self.assertTrue(state["approval"]["approval_token"])

    def test_golden_006_l1_auto_execute(self):
        """L1 小额免审批：证据充分 + 差额 ≤5000，自动执行不经过审批节点。"""
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-006.json")
        exp = spec["expected"]
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(final["risk_level"], exp["risk_level"])
        self.assertGreaterEqual(final["evidence_score"], 0.9)
        self.assertEqual(final["status"], exp["final_status"])
        self.assertEqual(state["verification"]["verification_status"], exp["verification_status"])
        # L1 不允许出现审批节点
        self.assertNotIn("approval", state)
        self.assertTrue(state["executions"])

    def test_golden_007_l3_forced_manual(self):
        """L3 超额强制人工：只生成方案，绝不允许任何执行记录。"""
        spec, final, state = self._run(ROOT / "data" / "golden_cases" / "GOLDEN-007.json")
        exp = spec["expected"]
        self.assertEqual(state["calculation_result"]["total_commission"], exp["total_commission"])
        self.assertEqual(sorted(state["root_cause_report"]["root_causes"]), exp["root_causes"])
        self.assertEqual(final["risk_level"], exp["risk_level"])
        self.assertEqual(final["status"], exp["final_status"])
        self.assertNotIn("executions", state)
        self.assertNotIn("verification", state)

    def test_high_risk_never_auto_executed(self):
        """质量红线：L3 案件不允许出现任何执行记录。"""
        for fp in sorted((ROOT / "data" / "golden_cases").glob("*.json")):
            _, final, state = self._run(fp)
            if final.get("risk_level") == "L3":
                self.assertNotIn("executions", state)


if __name__ == "__main__":
    unittest.main()
