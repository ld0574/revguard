"""政策版本匹配与等级回溯单元测试（设计文档 12.3 / 3.2）。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from revguard.policy_matcher import (PolicyMatchError, resolve_tier_at_date,
                                     select_policy_version)

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


def _versions() -> list[dict]:
    return json.loads((FIXTURES / "policies.json").read_text(encoding="utf-8"))["versions"]


class TestSelectPolicyVersion(unittest.TestCase):
    def test_july_order_uses_q3(self):
        decision = select_policy_version(_versions(), {"order_date": "2026-07-10"})
        self.assertEqual(decision.policy_version, "2026-Q3")
        excluded = {e["version"] for e in decision.excluded_versions}
        self.assertEqual(excluded, {"2026-Q1", "2026-Q2"})
        self.assertEqual(decision.unresolved_conflicts, [])

    def test_june_order_uses_q2(self):
        decision = select_policy_version(_versions(), {"order_date": "2026-06-15"})
        self.assertEqual(decision.policy_version, "2026-Q2")

    def test_boundary_dates(self):
        # 生效日与失效日当天都应在覆盖范围内
        self.assertEqual(
            select_policy_version(_versions(), {"order_date": "2026-07-01"}).policy_version,
            "2026-Q3")
        self.assertEqual(
            select_policy_version(_versions(), {"order_date": "2026-06-30"}).policy_version,
            "2026-Q2")

    def test_no_effective_version_raises(self):
        with self.assertRaises(PolicyMatchError):
            select_policy_version(_versions(), {"order_date": "2025-06-01"})

    def test_missing_time_field_raises(self):
        with self.assertRaises(PolicyMatchError):
            select_policy_version(_versions(), {})

    def test_malformed_date_raises(self):
        with self.assertRaises(PolicyMatchError):
            select_policy_version(_versions(), {"order_date": "2026-99-42"})


class TestResolveTierAtDate(unittest.TestCase):
    def test_tier_conflict_case(self):
        """Golden Case 2：GOLD 7/15 生效，7/10 的订单必须回溯为 SILVER。"""
        history = [
            {"tier": "SILVER", "effective_from": "2025-06-01"},
            {"tier": "GOLD", "effective_from": "2026-07-15"},
        ]
        result = resolve_tier_at_date(history, "2026-07-10")
        self.assertEqual(result["tier"], "SILVER")
        self.assertIsNotNone(result["conflict"])

    def test_tier_after_upgrade(self):
        history = [
            {"tier": "SILVER", "effective_from": "2025-06-01"},
            {"tier": "GOLD", "effective_from": "2026-07-15"},
        ]
        result = resolve_tier_at_date(history, "2026-07-28")
        self.assertEqual(result["tier"], "GOLD")
        self.assertIsNone(result["conflict"])

    def test_empty_history_raises(self):
        with self.assertRaises(PolicyMatchError):
            resolve_tier_at_date([], "2026-07-10")

    def test_date_before_first_tier_raises(self):
        with self.assertRaises(PolicyMatchError):
            resolve_tier_at_date([
                {"tier": "SILVER", "effective_from": "2026-01-01"},
            ], "2025-12-31")


if __name__ == "__main__":
    unittest.main()
