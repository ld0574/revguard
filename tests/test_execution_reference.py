"""执行引用监视器：没有读取过的事实不得成为资金执行的依据（竞品差距 P1）。"""
from __future__ import annotations

import unittest
from pathlib import Path

from revguard.execution_reference import BOUND, CASE_ONLY, reference_anchor
from revguard.mocks import ToolGateway

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


class TestExecutionReferences(unittest.TestCase):
    def _gateway(self, **kw) -> ToolGateway:
        return ToolGateway(FIXTURES, **kw)

    def _read_facts(self, gw: ToolGateway, *, case_id: str,
                    order_id: str = "EZ202608001",
                    partner_id: str = "AGT-10001") -> None:
        gw.call("crm.get_order", {"order_id": order_id}, case_id=case_id,
                actor="revguard-evidence", scope=["order:read"])
        gw.call("contract.get_contract", {"partner_id": partner_id}, case_id=case_id,
                actor="revguard-evidence", scope=["contract:read"])
        gw.call("finance.get_commission_ledger", {"order_id": order_id}, case_id=case_id,
                actor="revguard-evidence", scope=["ledger:read"])

    def _approved_draft(self, gw: ToolGateway, *, case_id: str,
                        order_id: str = "EZ202608001", amount: str = "100"):
        approval = gw.call("workflow.create_approval", {
            "case_id": case_id, "amount": amount, "currency": "KES",
            "component_quota": {"SALES_COMMISSION": amount},
            "risk_level": "L2", "approver_role": "FINANCE_LEAD",
            "action_summary": "执行引用监视器测试",
        }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])["data"]
        decided = gw.call("workflow.decide_approval", {
            "approval_id": approval["approval_id"], "decision": "APPROVED",
            "comment": "test",
        }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])["data"]
        draft = gw.call("commission.create_adjustment_draft", {
            "order_id": order_id, "case_id": case_id, "amount": amount,
            "currency": "KES", "component": "SALES_COMMISSION",
        }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
        return draft, decided["approval_token"]

    def _submit(self, gw: ToolGateway, *, case_id: str, draft: dict, token: str, key: str):
        return gw.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": token,
        }, case_id=case_id, actor="revguard-executor",
            scope=["commission:write"], idempotency_key=key)

    def _ledger_entry(self, gw: ToolGateway, case_id: str) -> dict:
        return next((e for e in gw._ledger if str(e.get("source", "")).endswith(case_id)), {})

    def test_submit_without_any_read_is_blocked(self):
        gw = self._gateway(require_execution_references=True)
        case_id = "CASE-REF-MISSING"
        draft, token = self._approved_draft(gw, case_id=case_id)
        resp = self._submit(gw, case_id=case_id, draft=draft, token=token, key="ref-1")
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "EVIDENCE_GAP")
        self.assertIn("未读取必备事实槽位 ORDER", resp["error"]["message"])
        self.assertEqual(self._ledger_entry(gw, case_id), {})
        self.assertEqual(gw._adjustments[draft["action_id"]]["status"], "DRAFT")

    def test_submit_records_fact_bound_references(self):
        gw = self._gateway(require_execution_references=True)
        case_id = "CASE-REF-OK"
        self._read_facts(gw, case_id=case_id)
        draft, token = self._approved_draft(gw, case_id=case_id)
        resp = self._submit(gw, case_id=case_id, draft=draft, token=token, key="ref-2")
        self.assertTrue(resp["success"], resp.get("error"))
        entry = self._ledger_entry(gw, case_id)
        references = entry["execution_references"]
        self.assertEqual([item["slot"] for item in references],
                         ["ORDER", "CONTRACT", "COMMISSION_LEDGER"])
        self.assertTrue(all(item["binding"] == BOUND for item in references))
        self.assertTrue(all(item["fact_digest"].startswith("sha256:") for item in references))
        self.assertTrue(all(item["tool_receipt"].startswith("RCPT-") for item in references))
        self.assertEqual(entry["reference_anchor"], reference_anchor(references))

    def test_reads_for_another_order_do_not_authorize(self):
        gw = self._gateway(require_execution_references=True)
        case_id = "CASE-REF-OTHER"
        self._read_facts(gw, case_id=case_id, order_id="EZ202607002", partner_id="AGT-10002")
        draft, token = self._approved_draft(gw, case_id=case_id, order_id="EZ202608001")
        resp = self._submit(gw, case_id=case_id, draft=draft, token=token, key="ref-3")
        self.assertFalse(resp["success"])
        self.assertEqual(resp["error"]["type"], "EVIDENCE_GAP")
        self.assertIn("事实引用与执行依据不一致：ORDER", resp["error"]["message"])
        self.assertEqual(self._ledger_entry(gw, case_id), {})

    def test_legacy_receipts_degrade_to_case_only_binding(self):
        gw = self._gateway(require_execution_references=True)
        case_id = "CASE-REF-LEGACY"
        draft, token = self._approved_draft(gw, case_id=case_id)
        for index, tool_name in enumerate(("crm.get_order", "contract.get_contract",
                                           "finance.get_commission_ledger"), start=1):
            gw._receipts.append({
                "tool_receipt": f"RCPT-LEGACY-{index}", "tool_name": tool_name,
                "case_id": case_id, "actor": "revguard-evidence", "success": True,
                "called_at": "2026-09-17T00:00:00Z",
            })
        # 执行入口每次都从持久层重载回执，历史回执必须先落盘才有效。
        gw._persist_state()
        resp = self._submit(gw, case_id=case_id, draft=draft, token=token, key="ref-4")
        self.assertTrue(resp["success"], resp.get("error"))
        references = self._ledger_entry(gw, case_id)["execution_references"]
        self.assertTrue(all(item["binding"] == CASE_ONLY for item in references))
        self.assertTrue(all(item["fact_digest"] is None for item in references))

    def test_monitor_off_keeps_legacy_behaviour(self):
        gw = self._gateway()
        case_id = "CASE-REF-OFF"
        draft, token = self._approved_draft(gw, case_id=case_id)
        resp = self._submit(gw, case_id=case_id, draft=draft, token=token, key="ref-5")
        self.assertTrue(resp["success"], resp.get("error"))
        entry = self._ledger_entry(gw, case_id)
        self.assertEqual(entry["execution_references"], [])
        self.assertIsNone(entry["reference_anchor"])


if __name__ == "__main__":
    unittest.main()
