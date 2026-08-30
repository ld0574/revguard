"""风险分级与 Mock 工具契约测试（设计文档 14 / 13）。"""
from __future__ import annotations

import tempfile
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
        resp = self.gw.call("crm.get_order", {"order_id": "EZ202608001"},
                            actor="revguard-evidence", scope=["order:read"])
        self.assertTrue(resp["success"])
        self.assertEqual(resp["data"]["order_amount"], 180000)
        self.assertTrue(resp["tool_receipt"].startswith("RCPT-"))

    def test_not_found_envelope(self):
        resp = self.gw.call("crm.get_order", {"order_id": "NOPE"},
                            actor="revguard-evidence", scope=["order:read"])
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "NOT_FOUND")
        self.assertFalse(resp["error"]["retryable"])

    def test_flaky_finance_then_recover(self):
        gw = ToolGateway(FIXTURES, finance_fail_times=1)
        first = gw.call("finance.get_payment", {"order_id": "EZ202608001"},
                        actor="revguard-evidence", scope=["payment:read"])
        self.assertFalse(first["success"])
        self.assertTrue(first["error"]["retryable"])
        second = gw.call("finance.get_payment", {"order_id": "EZ202608001"},
                         actor="revguard-evidence", scope=["payment:read"])
        self.assertTrue(second["success"])

    def test_one_shot_verification_fault_stays_consumed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "gateway.json"
            first_gateway = ToolGateway(
                FIXTURES, state_path=state_path, verification_tamper_amount="1",
            )
            first = first_gateway.call(
                "finance.get_commission_ledger", {"order_id": "EZ202608001"},
                case_id="CASE-TAMPER", actor="revguard-verifier",
                scope=["ledger:read"],
            )
            restarted_gateway = ToolGateway(
                FIXTURES, state_path=state_path, verification_tamper_amount="1",
            )
            second = restarted_gateway.call(
                "finance.get_commission_ledger", {"order_id": "EZ202608001"},
                case_id="CASE-TAMPER", actor="revguard-verifier",
                scope=["ledger:read"],
            )
        self.assertEqual(
            Decimal(first["data"]["entries"][-1]["amount"])
            - Decimal(second["data"]["entries"][-1]["amount"]),
            Decimal("1"),
        )

    def _approved_draft(self, *, case_id="CASE-T", amount="100"):
        approval = self.gw.call("workflow.create_approval", {
            "case_id": case_id, "amount": amount, "currency": "KES",
            "component_quota": {"SALES_COMMISSION": amount},
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "test",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
        decided = self.gw.call("workflow.decide_approval", {
            "approval_id": approval["data"]["approval_id"],
            "decision": "APPROVED", "comment": "test",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
        draft = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": case_id,
            "amount": amount, "currency": "KES", "component": "SALES_COMMISSION",
        }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])
        return draft["data"], decided["data"]["approval_token"]

    def test_submit_requires_approval_token(self):
        draft = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": "CASE-T",
            "amount": "100", "currency": "KES"}, case_id="CASE-T",
            actor="revguard-executor", scope=["commission:draft"])
        resp = self.gw.call("commission.submit_adjustment",
                            {"action_id": draft["data"]["action_id"]},
                            case_id="CASE-T", actor="revguard-executor",
                            scope=["commission:write"], idempotency_key="k1")
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "AUTH_FAILED")

    def test_idempotency_conflict(self):
        draft, token = self._approved_draft()
        params = {"action_id": draft["action_id"], "approval_token": token}
        first = self.gw.call("commission.submit_adjustment", params, case_id="CASE-T",
                             actor="revguard-executor", scope=["commission:write"],
                             idempotency_key="k2")
        self.assertTrue(first["success"])
        second = self.gw.call("commission.submit_adjustment", params, case_id="CASE-T",
                              actor="revguard-executor", scope=["commission:write"],
                              idempotency_key="k2")
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["type"], "IDEMPOTENCY_CONFLICT")

    def test_reversal_creates_negative_entry(self):
        draft, token = self._approved_draft()
        submitted = self.gw.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": token,
        }, case_id="CASE-T", actor="revguard-executor", scope=["commission:write"],
            idempotency_key="k3-submit")
        entry = submitted["data"]["ledger_entry"]
        resp = self.gw.call("commission.reverse_adjustment", {
            "ledger_id": entry["ledger_id"], "case_id": "CASE-T",
            "rollback_token": submitted["data"]["rollback_token"],
        }, case_id="CASE-T", actor="revguard-executor", scope=["commission:reverse"],
            idempotency_key="k3")
        self.assertTrue(resp["success"])
        self.assertEqual(resp["data"]["reversal_entry"]["amount"], "-100")

    def test_capability_renewal_only_authorizes_unconsumed_components(self):
        case_id = "CASE-RECOVERY"
        created = self.gw.call("workflow.create_approval", {
            "case_id": case_id, "amount": "100", "currency": "KES",
            "component_quota": {
                "SALES_COMMISSION": "70", "COLLECTION_COMMISSION": "30",
            },
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "recovery test",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
        decided = self.gw.call("workflow.decide_approval", {
            "approval_id": created["data"]["approval_id"],
            "decision": "APPROVED",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
        sales = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": case_id,
            "component": "SALES_COMMISSION", "amount": "70", "currency": "KES",
        }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])
        submitted = self.gw.call("commission.submit_adjustment", {
            "action_id": sales["data"]["action_id"],
            "approval_token": decided["data"]["approval_token"],
        }, case_id=case_id, actor="revguard-executor", scope=["commission:write"],
            idempotency_key=f"{case_id}:SALES_COMMISSION")
        self.assertTrue(submitted["success"])

        denied = self.gw.call("workflow.renew_approval_capability", {
            "approval_id": created["data"]["approval_id"], "case_id": case_id,
        }, case_id=case_id, actor="api-operator", scope=["approval:decide"])
        self.assertFalse(denied["success"])
        renewed = self.gw.call("workflow.renew_approval_capability", {
            "approval_id": created["data"]["approval_id"], "case_id": case_id,
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
        self.assertTrue(renewed["success"])
        self.assertEqual(
            renewed["data"]["remaining_component_quota"],
            {"COLLECTION_COMMISSION": "30"},
        )

        collection = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": case_id,
            "component": "COLLECTION_COMMISSION", "amount": "30", "currency": "KES",
        }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])
        resumed = self.gw.call("commission.submit_adjustment", {
            "action_id": collection["data"]["action_id"],
            "approval_token": renewed["data"]["approval_token"],
        }, case_id=case_id, actor="revguard-executor", scope=["commission:write"],
            idempotency_key=f"{case_id}:COLLECTION_COMMISSION")
        self.assertTrue(resumed["success"])

        rollback = self.gw.call("workflow.renew_rollback_capability", {
            "case_id": case_id,
            "ledger_id": submitted["data"]["ledger_entry"]["ledger_id"],
            "action_id": sales["data"]["action_id"],
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
        self.assertTrue(rollback["success"])
        reversed_result = self.gw.call("commission.reverse_adjustment", {
            "case_id": case_id,
            "ledger_id": submitted["data"]["ledger_entry"]["ledger_id"],
            "rollback_token": rollback["data"]["rollback_token"],
        }, case_id=case_id, actor="revguard-executor",
            scope=["commission:reverse"], idempotency_key=f"{case_id}:sales:rollback")
        self.assertTrue(reversed_result["success"])

    def test_forged_token_and_scope_escalation_are_rejected(self):
        draft, _token = self._approved_draft()
        forged = self.gw.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": "RGC1.forged.signature",
        }, case_id="CASE-T", actor="revguard-executor", scope=["commission:write"],
            idempotency_key="forged")
        self.assertFalse(forged["success"])
        self.assertEqual(forged["error"]["type"], "AUTH_FAILED")

        escalated = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": "CASE-T",
            "amount": "1", "currency": "KES",
        }, case_id="CASE-T", actor="revguard-evidence", scope=["commission:draft"])
        self.assertFalse(escalated["success"])
        self.assertEqual(escalated["error"]["type"], "AUTH_FAILED")


if __name__ == "__main__":
    unittest.main()
