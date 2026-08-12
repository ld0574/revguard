"""Every registered Skill must satisfy its published runtime JSON Schema."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from revguard.models import Case
from revguard.mocks import ToolGateway
from revguard.orchestrator import Orchestrator
from revguard.skill_runtime import invoke_skill
from revguard.store import Store
from scripts.seed_demo import seed


ROOT = Path(__file__).resolve().parent.parent


class TestSkillRuntimeContracts(unittest.TestCase):
    def test_read_and_policy_skill_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "contracts.db"
            seed(str(db), quiet=True)
            store = Store(db)
            gateway = ToolGateway(ROOT / "data" / "fixtures")
            case = store.get_case("CASE-2026-0001")
            state = Orchestrator(
                store, gateway, output_dir=Path(tmp) / "out",
                report_dir=Path(tmp) / "reports",
            ).run_case(case)
            final = store.get_case(case["case_id"])
            snapshot = gateway.call(
                "finance.get_commission_ledger", {"order_id": final["order_id"]},
                case_id=final["case_id"], actor="revguard-verifier", scope=["ledger:read"],
            )["data"]["entries"]
            calls = [
                ("CaseNormalizeSkill", "revguard-intake", {"raw_case": final}),
                ("EntityResolveSkill", "revguard-intake", {"entities": final["entities"]}),
                ("EvidenceCollectSkill", "revguard-evidence", {
                    "partner": state["partner"], "order_id": final["order_id"],
                }),
                ("PolicyVersionMatchSkill", "revguard-policy", {
                    "versions": state["evidence"]["POLICY_VERSIONS"]["versions"],
                    "facts": {
                        "order_date": state["evidence"]["ORDER"]["order_date"],
                        "payment_date": state["evidence"]["PAYMENT_RECORD"]["payment_date"],
                    },
                    "time_basis": "order_date",
                }),
                ("CommissionCalculateSkill", "revguard-calculation", {
                    "rule_dsl": state["policy_decision"]["effective_rule_set"],
                    "facts": state["facts"],
                    "currency": state["calculation_result"]["currency"],
                }),
                ("DifferenceExplainSkill", "revguard-rootcause", {
                    "calculation": state["calculation_result"],
                    "ledger_entries": state["evidence"]["COMMISSION_LEDGER"]["entries"],
                    "matched_policy_version": state["policy_decision"]["policy_version"],
                    "tier_conflict": state["tier_resolution"].get("conflict"),
                }),
                ("RiskClassifySkill", "revguard-risk", {
                    "action_type": "LEDGER_ADJUST",
                    "adjustment_amount": state["root_cause_report"]["total_delta"],
                    "currency": state["calculation_result"]["currency"],
                    "evidence_score": final["evidence_score"],
                    "case_type": final["case_type"], "policy_conflict": False,
                }),
                ("PermissionCheckSkill", "revguard-executor", {
                    "action_type": "LEDGER_ADJUST", "risk": state["risk_decision"],
                    "approval": state["approval"],
                }),
                ("IdempotencyGuardSkill", "revguard-executor", {
                    "idempotency_key": "CONTRACT-UNUSED",
                }),
                ("PostActionVerifySkill", "revguard-verifier", {
                    "order_id": final["order_id"],
                    "expected_components": state["calculation_result"]["components"],
                }),
                ("PostRollbackVerifySkill", "revguard-verifier", {
                    "order_id": final["order_id"], "expected_snapshot": snapshot,
                }),
                ("CaseToDatasetSkill", "revguard-knowledge", {
                    "case": final, "shared_state": state,
                    "verification": state["verification"],
                }),
            ]
            for name, actor, payload in calls:
                with self.subTest(skill=name):
                    result = invoke_skill(
                        name, payload, actor=actor, case_id=final["case_id"],
                        gateway=gateway, store=store,
                    )
                    self.assertTrue(result["success"])
            store.close()

    def test_write_skill_contracts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "contracts.db")
            case = Case(
                "CASE-CONTRACT-WRITE", "TEST", "UNIT",
                partner_id="AGT-10001", order_id="EZ202608001",
            ).to_dict()
            store.save_case(case)
            gateway = ToolGateway(ROOT / "data" / "fixtures")
            risk = {
                "risk_level": "L2", "approval_required": True,
                "approver_role": "FINANCE_LEAD",
                "execution_constraints": {"write": "approval_required"},
                "rollback_plan_required": True,
                "reason_codes": ["AMOUNT_REQUIRES_APPROVAL"],
            }
            approval = invoke_skill(
                "ApprovalRouteSkill", {
                    "risk": risk, "amount": "100",
                    "component_quota": {"SALES_COMMISSION": "100"},
                    "currency": "KES", "action_summary": "contract test",
                }, actor="revguard-risk", case_id=case["case_id"],
                gateway=gateway, store=store,
            )["data"]
            decided = gateway.call(
                "workflow.decide_approval",
                {"approval_id": approval["approval_id"], "decision": "APPROVED"},
                case_id=case["case_id"], actor="finance.lead", scope=["approval:decide"],
            )["data"]
            draft = invoke_skill(
                "AdjustmentDraftSkill", {
                    "order_id": case["order_id"], "component": "SALES_COMMISSION",
                    "delta": "100", "currency": "KES", "reason": "contract test",
                }, actor="revguard-executor", case_id=case["case_id"],
                gateway=gateway, store=store,
            )["data"]
            execution = invoke_skill(
                "LedgerAdjustSkill", {
                    "action_id": draft["action_id"],
                    "approval_token": decided["approval_token"],
                    "policy_version": "2026-Q3",
                    "idempotency_key": "CASE-CONTRACT-WRITE:adjust",
                }, actor="revguard-executor", case_id=case["case_id"],
                gateway=gateway, store=store,
            )["data"]
            reversed_result = invoke_skill(
                "LedgerReverseSkill", {
                    "ledger_id": execution["ledger_entry"]["ledger_id"],
                    "rollback_token": execution["rollback_token"],
                    "idempotency_key": "CASE-CONTRACT-WRITE:reverse",
                }, actor="revguard-executor", case_id=case["case_id"],
                gateway=gateway, store=store,
            )
            self.assertTrue(reversed_result["success"])
            store.close()


if __name__ == "__main__":
    unittest.main()
