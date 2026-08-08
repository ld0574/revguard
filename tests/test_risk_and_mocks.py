"""风险分级与 Mock 工具契约测试（设计文档 14 / 13）。"""
from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from revguard.mocks import ToolGateway
from revguard.risk import classify_risk

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


class TestRiskClassify(unittest.TestCase):
    def _classify(self, **over):
        params = dict(
            action_type="LEDGER_ADJUST",
            adjustment_amount=Decimal("14400"),
            currency="KES",
            evidence_score=1.0,
            case_type="COMMISSION_UNDERPAYMENT",
            policy_conflict=False,
        )
        params.update(over)
        return classify_risk(**params)

    def test_readonly_is_l0(self):
        d = self._classify(action_type="READONLY")
        self.assertEqual(d.risk_level, "L0")
        self.assertFalse(d.approval_required)

    def test_small_amount_high_evidence_is_l1(self):
        d = self._classify(adjustment_amount=Decimal("3000"))
        self.assertEqual(d.risk_level, "L1")
        self.assertFalse(d.approval_required)

    def test_medium_amount_is_l2(self):
        d = self._classify(adjustment_amount=Decimal("14400"))
        self.assertEqual(d.risk_level, "L2")
        self.assertTrue(d.approval_required)
        self.assertEqual(d.approver_role, "FINANCE_LEAD")

    def test_negative_adjustment_forces_l2(self):
        """扣回（负向调整）即使小额也必须审批。"""
        d = self._classify(adjustment_amount=Decimal("-3000"))
        self.assertEqual(d.risk_level, "L2")
        self.assertTrue(d.approval_required)

    def test_large_amount_is_l3(self):
        d = self._classify(adjustment_amount=Decimal("999999"))
        self.assertEqual(d.risk_level, "L3")
        self.assertFalse(d.execution_constraints["write"])

    def test_low_evidence_is_l3(self):
        d = self._classify(evidence_score=0.3)
        self.assertEqual(d.risk_level, "L3")

    def test_policy_conflict_is_l3(self):
        d = self._classify(policy_conflict=True)
        self.assertEqual(d.risk_level, "L3")


class TestToolContract(unittest.TestCase):
    def setUp(self):
        self.gw = ToolGateway(FIXTURES)

    def test_success_envelope(self):
        resp = self.gw.call("crm.get_order", {"order_id": "EZ202608001"})
        self.assertTrue(resp["success"])
        self.assertEqual(resp["data"]["order_amount"], 180000)
        self.assertTrue(resp["tool_receipt"].startswith("RCPT-"))

    def test_not_found_envelope(self):
        resp = self.gw.call("crm.get_order", {"order_id": "NOPE"})
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "NOT_FOUND")
        self.assertFalse(resp["error"]["retryable"])

    def test_flaky_finance_then_recover(self):
        gw = ToolGateway(FIXTURES, finance_fail_times=1)
        first = gw.call("finance.get_payment", {"order_id": "EZ202608001"})
        self.assertFalse(first["success"])
        self.assertTrue(first["error"]["retryable"])
        second = gw.call("finance.get_payment", {"order_id": "EZ202608001"})
        self.assertTrue(second["success"])

    def test_submit_requires_approval_token(self):
        draft = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "amount": "100", "currency": "KES"})
        resp = self.gw.call("commission.submit_adjustment",
                            {"action_id": draft["data"]["action_id"]},
                            idempotency_key="k1")
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "AUTH_FAILED")

    def test_idempotency_conflict(self):
        draft = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "amount": "100", "currency": "KES"})
        params = {"action_id": draft["data"]["action_id"], "approval_token": "ATK-x"}
        first = self.gw.call("commission.submit_adjustment", params, idempotency_key="k2")
        self.assertTrue(first["success"])
        second = self.gw.call("commission.submit_adjustment", params, idempotency_key="k2")
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["type"], "IDEMPOTENCY_CONFLICT")

    def test_reversal_creates_negative_entry(self):
        resp = self.gw.call("commission.reverse_adjustment",
                            {"ledger_id": "LED-50001", "case_id": "T"},
                            idempotency_key="k3")
        self.assertTrue(resp["success"])
        self.assertEqual(resp["data"]["reversal_entry"]["amount"], "-18000.0")
        ledger = self.gw.call("finance.get_commission_ledger", {"order_id": "EZ202608001"})
        self.assertEqual(ledger["data"]["posted_total"], 0.0)


if __name__ == "__main__":
    unittest.main()
