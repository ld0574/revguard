"""能力令牌、业务绑定、防重放与并发写安全评测。"""
from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from revguard.mocks import ToolGateway
from revguard.security import CapabilityTokenSigner, SecurityError, redact_secrets
from revguard.store import Store
from revguard.trace import Tracer

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"
SIGNING_KEY = "revguard-security-test-signing-key-at-least-32-bytes"


class TestCapabilityTokenSigner(unittest.TestCase):
    def setUp(self):
        self.signer = CapabilityTokenSigner(SIGNING_KEY)

    def test_signature_purpose_and_expiry(self):
        token = self.signer.issue("ledger_adjust", {"case_id": "CASE-A"},
                                  ttl_seconds=60, now=1000)
        self.assertEqual(self.signer.verify(
            token, purpose="ledger_adjust", now=1030
        )["case_id"], "CASE-A")
        with self.assertRaises(SecurityError):
            self.signer.verify(token, purpose="ledger_reverse", now=1030)
        with self.assertRaises(SecurityError):
            self.signer.verify(token, purpose="ledger_adjust", now=1060)
        protected = self.signer.issue(
            "ledger_adjust",
            {"case_id": "CASE-A", "purpose": "forged", "exp": 999999},
            ttl_seconds=60,
            now=1000,
        )
        protected_claims = self.signer.verify(
            protected, purpose="ledger_adjust", now=1030
        )
        self.assertEqual(protected_claims["purpose"], "ledger_adjust")
        self.assertEqual(protected_claims["exp"], 1060)

        # 即使调用方误把完整令牌写入 span，持久化 Trace 也只能保留指纹。
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "trace.db")
            tracer = Tracer(store, "CASE-TRACE")
            with tracer.span("APPROVAL", "redaction-check") as span:
                span["outputs"] = {"approval_token": token, "nested": [token]}
            exported = json.dumps(tracer.export(), ensure_ascii=False)
            store.close()
        self.assertNotIn("RGC1.", exported)
        self.assertIn("<redacted:sha256:", exported)

    def test_tampered_signature_rejected(self):
        token = self.signer.issue("ledger_adjust", {"case_id": "CASE-A"})
        altered = token[:-1] + ("A" if token[-1] != "A" else "B")
        with self.assertRaises(SecurityError):
            self.signer.verify(altered, purpose="ledger_adjust")

    def test_embedded_tokens_are_redacted_inside_text(self):
        token = self.signer.issue("ledger_adjust", {"case_id": "CASE-A"})
        redacted = redact_secrets({
            "message": f"upstream failed; token={token}; AUTHORIZATION: bEaReR abc.def-123",
            "raw_approval": {"token_used": f"prefix {token} suffix"},
        })
        rendered = json.dumps(redacted)
        self.assertNotIn(token, rendered)
        self.assertNotIn("abc.def-123", rendered)
        self.assertGreaterEqual(rendered.count("<redacted:sha256:"), 3)


class TestGatewaySecurity(unittest.TestCase):
    def setUp(self):
        self.gw = ToolGateway(FIXTURES, signing_key=SIGNING_KEY)

    def _approval_token(self, case_id: str, amount: str = "100") -> str:
        created = self.gw.call("workflow.create_approval", {
            "case_id": case_id, "amount": amount, "currency": "KES",
            "component_quota": {"SALES_COMMISSION": amount},
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "security test",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
        decided = self.gw.call("workflow.decide_approval", {
            "approval_id": created["data"]["approval_id"], "decision": "APPROVED",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
        return decided["data"]["approval_token"]

    def _draft(self, case_id: str, amount: str = "100") -> dict:
        return self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": case_id,
            "component": "SALES_COMMISSION", "amount": amount, "currency": "KES",
        }, case_id=case_id, actor="revguard-executor",
            scope=["commission:draft"])["data"]

    def test_token_is_bound_to_case_and_amount(self):
        token = self._approval_token("CASE-A", "100")
        wrong_case = self._draft("CASE-B", "100")
        response = self.gw.call("commission.submit_adjustment", {
            "action_id": wrong_case["action_id"], "approval_token": token,
        }, case_id="CASE-B", actor="revguard-executor", scope=["commission:write"],
            idempotency_key="wrong-case")
        self.assertFalse(response["success"])
        self.assertEqual(response["error"]["type"], "AUTH_FAILED")

        too_large = self._draft("CASE-A", "101")
        response = self.gw.call("commission.submit_adjustment", {
            "action_id": too_large["action_id"], "approval_token": token,
        }, case_id="CASE-A", actor="revguard-executor", scope=["commission:write"],
            idempotency_key="too-large")
        self.assertFalse(response["success"])
        self.assertEqual(response["error"]["type"], "AUTH_FAILED")

    def test_concurrent_double_submit_allows_exactly_one(self):
        token = self._approval_token("CASE-C")
        draft = self._draft("CASE-C")

        def submit(index: int):
            return self.gw.call("commission.submit_adjustment", {
                "action_id": draft["action_id"], "approval_token": token,
            }, case_id="CASE-C", actor="revguard-executor", scope=["commission:write"],
                idempotency_key=f"concurrent-{index}")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, (1, 2)))
        self.assertEqual(sum(1 for result in results if result["success"]), 1)

    def test_token_cannot_move_quota_to_another_component(self):
        token = self._approval_token("CASE-COMPONENT", "100")
        draft = self.gw.call("commission.create_adjustment_draft", {
            "order_id": "EZ202608001", "case_id": "CASE-COMPONENT",
            "component": "COLLECTION_COMMISSION", "amount": "100", "currency": "KES",
        }, case_id="CASE-COMPONENT", actor="revguard-executor",
            scope=["commission:draft"])["data"]
        response = self.gw.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": token,
        }, case_id="CASE-COMPONENT", actor="revguard-executor",
            scope=["commission:write"], idempotency_key="wrong-component")
        self.assertFalse(response["success"])
        self.assertEqual(response["error"]["type"], "AUTH_FAILED")

    def test_rollback_token_is_one_time_and_bound(self):
        token = self._approval_token("CASE-D")
        draft = self._draft("CASE-D")
        submitted = self.gw.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": token,
        }, case_id="CASE-D", actor="revguard-executor", scope=["commission:write"],
            idempotency_key="submit-d")
        ledger_id = submitted["data"]["ledger_entry"]["ledger_id"]
        rollback_token = submitted["data"]["rollback_token"]
        first = self.gw.call("commission.reverse_adjustment", {
            "ledger_id": ledger_id, "rollback_token": rollback_token,
        }, case_id="CASE-D", actor="revguard-executor", scope=["commission:reverse"],
            idempotency_key="reverse-d-1")
        second = self.gw.call("commission.reverse_adjustment", {
            "ledger_id": ledger_id, "rollback_token": rollback_token,
        }, case_id="CASE-D", actor="revguard-executor", scope=["commission:reverse"],
            idempotency_key="reverse-d-2")
        self.assertTrue(first["success"])
        self.assertFalse(second["success"])
        self.assertEqual(second["error"]["type"], "AUTH_FAILED")

    def test_gateway_state_survives_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "gateway.json"
            first = ToolGateway(FIXTURES, signing_key=SIGNING_KEY, state_path=state_path)
            response = first.call("finance.get_commission_ledger", {
                "order_id": "EZ202608001",
            }, case_id="CASE-P", actor="revguard-verifier", scope=["ledger:read"])
            self.assertTrue(response["success"])
            second = ToolGateway(FIXTURES, signing_key=SIGNING_KEY, state_path=state_path)
            self.assertGreaterEqual(len(second.receipts), 1)


if __name__ == "__main__":
    unittest.main()
