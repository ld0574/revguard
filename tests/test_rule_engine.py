"""规则引擎单元测试（ADR-001：计算必须可重复、可审计）。"""
from __future__ import annotations

import json
import unittest
from decimal import Decimal
from pathlib import Path

from revguard.rule_engine import (
    FormulaError,
    evaluate_formula,
    match_condition,
    run_policy,
    to_decimal,
)

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def _q3_dsl() -> dict:
    policies = json.loads((FIXTURES / "policies.json").read_text(encoding="utf-8"))
    return next(v["rule_dsl"] for v in policies["versions"] if v["version"] == "2026-Q3")


GOLDEN_FACTS = {
    "order_amount": Decimal("180000"),
    "payment_amount": Decimal("180000"),
    "refund_amount": Decimal("0"),
    "order_date": "2026-07-10",
    "payment_date": "2026-07-28",
    "payment_days": 16,
    "agent_tier": "GOLD",
    "product_id": "SHS-500",
    "order_status": "COMPLETED",
    "payment_status": "SETTLED",
    "monthly_completed_orders": 1,
}


class TestFormulaEval(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(evaluate_formula("order_amount * 0.10", {"order_amount": 180000}),
                         Decimal("18000"))

    def test_parentheses_and_mixed_ops(self):
        result = evaluate_formula("(a + b) * 0.5 - c / 4", {"a": 10, "b": 20, "c": 8})
        self.assertEqual(result, Decimal("13"))

    def test_decimal_precision(self):
        # 浮点噪声不得进入金额：0.1 + 0.2 必须精确等于 0.3
        result = evaluate_formula("a + b", {"a": 0.1, "b": 0.2})
        self.assertEqual(result, Decimal("0.3"))

    def test_missing_variable_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula("order_amount * rate", {"order_amount": 100})

    def test_arbitrary_code_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula("__import__('os').system('id')", {})
        with self.assertRaises(FormulaError):
            evaluate_formula("a ** b", {"a": 2, "b": 3})

    def test_divide_by_zero_rejected(self):
        with self.assertRaises(FormulaError):
            evaluate_formula("a / 0", {"a": 1})


class TestConditionMatch(unittest.TestCase):
    def test_exact_match(self):
        ok, failures = match_condition({"agent_tier": "GOLD"}, {"agent_tier": "GOLD"})
        self.assertTrue(ok)
        self.assertEqual(failures, [])

    def test_suffix_operators(self):
        ok, _ = match_condition({"payment_days_lte": 30}, {"payment_days": 16})
        self.assertTrue(ok)
        ok, _ = match_condition({"monthly_completed_orders_gte": 20},
                                {"monthly_completed_orders": 1})
        self.assertFalse(ok)

    def test_missing_field_reported(self):
        ok, failures = match_condition({"payment_days_lte": 30}, {})
        self.assertFalse(ok)
        self.assertTrue(any("payment_days" in f for f in failures))


class TestRunPolicy(unittest.TestCase):
    def test_golden_case_calculation(self):
        """Golden Case：Q3 复算 = 销售 27000 + 回款 5400 = 32400，月度激励跳过。"""
        result = run_policy(_q3_dsl(), GOLDEN_FACTS, "KES")
        self.assertTrue(result["eligible"])
        self.assertEqual(result["total_commission"], "32400.00")
        applied = {c["type"]: c for c in result["components"] if c["applied"]}
        self.assertEqual(applied["SALES_COMMISSION"]["amount"], "27000.00")
        self.assertEqual(applied["COLLECTION_COMMISSION"]["amount"], "5400.00")
        skipped = {c["type"]: c for c in result["components"] if not c["applied"]}
        self.assertIn("MONTHLY_INCENTIVE", skipped)
        self.assertTrue(skipped["MONTHLY_INCENTIVE"]["skip_reasons"])

    def test_ineligible_order(self):
        facts = dict(GOLDEN_FACTS, order_status="PENDING")
        result = run_policy(_q3_dsl(), facts, "KES")
        self.assertFalse(result["eligible"])
        self.assertEqual(result["total_commission"], "0.00")
        self.assertTrue(result["eligibility_failures"])

    def test_reproducible_hash(self):
        """相同输入 + 相同规则 => 相同哈希（可复现性）。"""
        r1 = run_policy(_q3_dsl(), GOLDEN_FACTS, "KES")
        r2 = run_policy(_q3_dsl(), dict(GOLDEN_FACTS), "KES")
        self.assertEqual(r1["calculation_hash"], r2["calculation_hash"])

    def test_to_decimal_rejects_bool(self):
        with self.assertRaises(FormulaError):
            to_decimal(True)


if __name__ == "__main__":
    unittest.main()
