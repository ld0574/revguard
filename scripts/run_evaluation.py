#!/usr/bin/env python3
"""运行 RevGuard 102 场景确定性评测并输出机器可读指标。

评测由 8 个端到端 Golden Case、80 个风险边界组合、8 个政策日期样本、
6 个安全攻击探针组成；并额外报告 7 路 I/O 并行基准。任何失败返回非零。
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from revguard.models import Case  # noqa: E402
from revguard.mocks import ToolGateway  # noqa: E402
from revguard.orchestrator import Orchestrator  # noqa: E402
from revguard.policy_matcher import select_policy_version  # noqa: E402
from revguard.risk import classify_risk  # noqa: E402
from revguard.security import CapabilityTokenSigner, SecurityError  # noqa: E402
from revguard.skills import collect_evidence  # noqa: E402
from revguard.store import Store  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures"
GOLDEN_DIR = ROOT / "data" / "golden_cases"
SIGNING_KEY = "revguard-evaluation-signing-key-at-least-32-bytes"


def evaluate_golden_cases() -> tuple[int, list[str]]:
    failures: list[str] = []
    specs = [json.loads(path.read_text(encoding="utf-8"))
             for path in sorted(GOLDEN_DIR.glob("*.json"))]
    for spec in specs:
        raw, expected = spec["input"], spec["expected"]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "case.db")
            gateway = ToolGateway(
                FIXTURES, finance_fail_times=1, signing_key=SIGNING_KEY,
                verification_tamper_amount=(spec.get("gateway_overrides") or {}).get(
                    "verification_tamper_amount", "0"
                ),
            )
            case = Case(
                case_id=raw["case_id"], case_type=raw["case_type"], source=raw["source"],
                partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
                order_id=raw.get("order_id"), description=raw.get("description", ""),
                claim=raw.get("claim", {}),
            ).to_dict()
            store.save_case(case)
            state = Orchestrator(
                store, gateway, output_dir=Path(tmp) / "outputs",
                report_dir=Path(tmp) / "reports",
            ).run_case(case)
            final = store.get_case(raw["case_id"])
            store.close()
        checks = {
            "final_status": final["status"],
            "risk_level": final.get("risk_level"),
            "policy_version": (state.get("policy_decision") or {}).get("policy_version"),
            "total_commission": (state.get("calculation_result") or {}).get("total_commission"),
            "root_causes": sorted(
                (state.get("root_cause_report") or {}).get("root_causes", [])
            ),
            "verification_status": (state.get("verification") or {}).get("verification_status"),
            "rollback_verification_status": ((state.get("rollback") or {}).get("verification") or {}).get(
                "verification_status"
            ),
        }
        for key, wanted in expected.items():
            if key in checks and wanted is not None and checks[key] != wanted:
                failures.append(f"{spec['case_id']} {key}: {checks[key]} != {wanted}")
        if expected.get("no_approval") and "approval" in state:
            failures.append(f"{spec['case_id']} unexpected approval")
        if expected.get("risk_level") == "L1" and any(
                e.get("ledger_entry") for e in state.get("executions", [])):
            failures.append(f"{spec['case_id']} L1 wrote ledger")
        if expected.get("risk_level") == "L3" and state.get("executions"):
            failures.append(f"{spec['case_id']} L3 executed")
    return len(specs), failures


def _risk_oracle(amount: Decimal, score: float, conflict: bool) -> str:
    if amount == 0:
        return "L0"
    absolute = abs(amount)
    if conflict or score < 0.6 or absolute > Decimal("50000"):
        return "L3"
    if amount > 0 and absolute <= Decimal("5000") and score >= 0.9:
        return "L1"
    return "L2"


def evaluate_risk_matrix() -> tuple[int, list[str]]:
    failures: list[str] = []
    amounts = [Decimal("0"), Decimal("1"), Decimal("5000"), Decimal("5000.01"),
               Decimal("50000"), Decimal("50000.01"), Decimal("-1"), Decimal("-5000")]
    scores = [1.0, 0.95, 0.89, 0.6, 0.59]
    count = 0
    for amount in amounts:
        for score in scores:
            for conflict in (False, True):
                count += 1
                actual = classify_risk(
                    action_type="LEDGER_ADJUST", adjustment_amount=amount, currency="KES",
                    evidence_score=score, case_type="EVALUATION", policy_conflict=conflict,
                ).risk_level
                expected = _risk_oracle(amount, score, conflict)
                if actual != expected:
                    failures.append(
                        f"risk amount={amount} score={score} conflict={conflict}: {actual}!={expected}"
                    )
    return count, failures


def evaluate_policy_dates() -> tuple[int, list[str]]:
    policies = json.loads((FIXTURES / "policies.json").read_text(encoding="utf-8"))["versions"]
    samples = [
        ("2026-04-01", "2026-Q2"), ("2026-04-30", "2026-Q2"),
        ("2026-05-31", "2026-Q2"), ("2026-06-30", "2026-Q2"),
        ("2026-07-01", "2026-Q3"), ("2026-07-15", "2026-Q3"),
        ("2026-08-01", "2026-Q3"), ("2026-09-30", "2026-Q3"),
    ]
    failures = []
    for sample, expected in samples:
        actual = select_policy_version(policies, {"order_date": sample}).policy_version
        if actual != expected:
            failures.append(f"policy {sample}: {actual}!={expected}")
    return len(samples), failures


def _approval_and_draft(gateway: ToolGateway, case_id: str, amount: str = "100"):
    approval = gateway.call("workflow.create_approval", {
        "case_id": case_id, "amount": amount, "currency": "KES", "risk_level": "L2",
        "approver_role": "FINANCE_LEAD", "action_summary": "evaluation",
    }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
    decided = gateway.call("workflow.decide_approval", {
        "approval_id": approval["data"]["approval_id"], "decision": "APPROVED",
    }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
    draft = gateway.call("commission.create_adjustment_draft", {
        "order_id": "EZ202608001", "case_id": case_id,
        "component": "SALES_COMMISSION", "amount": amount, "currency": "KES",
    }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])
    return decided["data"]["approval_token"], draft["data"]


def evaluate_security_probes() -> tuple[int, list[str]]:
    failures: list[str] = []
    gateway = ToolGateway(FIXTURES, signing_key=SIGNING_KEY)
    token, draft = _approval_and_draft(gateway, "CASE-SEC")

    probes = []
    probes.append(("forged_signature", not gateway.call("commission.submit_adjustment", {
        "action_id": draft["action_id"], "approval_token": "RGC1.forged.signature",
    }, case_id="CASE-SEC", actor="revguard-executor", scope=["commission:write"],
        idempotency_key="sec-forged")["success"]))
    probes.append(("scope_escalation", not gateway.call("commission.create_adjustment_draft", {
        "order_id": "EZ202608001", "case_id": "CASE-SEC", "amount": "1", "currency": "KES",
    }, case_id="CASE-SEC", actor="revguard-evidence", scope=["commission:draft"])["success"]))

    other_draft = gateway.call("commission.create_adjustment_draft", {
        "order_id": "EZ202608001", "case_id": "CASE-OTHER",
        "component": "SALES_COMMISSION", "amount": "100", "currency": "KES",
    }, case_id="CASE-OTHER", actor="revguard-executor", scope=["commission:draft"])["data"]
    probes.append(("cross_case_token", not gateway.call("commission.submit_adjustment", {
        "action_id": other_draft["action_id"], "approval_token": token,
    }, case_id="CASE-OTHER", actor="revguard-executor", scope=["commission:write"],
        idempotency_key="sec-cross")["success"]))

    def submit(index):
        return gateway.call("commission.submit_adjustment", {
            "action_id": draft["action_id"], "approval_token": token,
        }, case_id="CASE-SEC", actor="revguard-executor", scope=["commission:write"],
            idempotency_key=f"sec-concurrent-{index}")

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = list(pool.map(submit, (1, 2)))
    successful = [item for item in concurrent if item["success"]]
    probes.append(("concurrent_double_submit", len(successful) == 1))

    submitted = successful[0]
    ledger_id = submitted["data"]["ledger_entry"]["ledger_id"]
    rollback_token = submitted["data"]["rollback_token"]
    first = gateway.call("commission.reverse_adjustment", {
        "ledger_id": ledger_id, "rollback_token": rollback_token,
    }, case_id="CASE-SEC", actor="revguard-executor", scope=["commission:reverse"],
        idempotency_key="sec-reverse-1")
    replay = gateway.call("commission.reverse_adjustment", {
        "ledger_id": ledger_id, "rollback_token": rollback_token,
    }, case_id="CASE-SEC", actor="revguard-executor", scope=["commission:reverse"],
        idempotency_key="sec-reverse-2")
    probes.append(("rollback_token_one_time", first["success"] and not replay["success"]))

    signer = CapabilityTokenSigner(SIGNING_KEY)
    expired = signer.issue("ledger_adjust", {"case_id": "CASE-X"}, ttl_seconds=1, now=100)
    try:
        signer.verify(expired, purpose="ledger_adjust", now=101)
        expiry_blocked = False
    except SecurityError:
        expiry_blocked = True
    probes.append(("expired_token", expiry_blocked))

    failures.extend(name for name, passed in probes if not passed)
    return len(probes), failures


class _LatencyGateway(ToolGateway):
    PARALLEL_TOOLS = {
        "crm.get_order", "crm.get_partner_tier_history", "contract.get_contract",
        "finance.get_payment", "finance.get_refund", "finance.get_invoice",
        "finance.get_commission_ledger",
    }

    def call(self, tool_name, parameters, **kwargs):
        if tool_name in self.PARALLEL_TOOLS:
            time.sleep(0.05)
        return super().call(tool_name, parameters, **kwargs)


def benchmark_parallel() -> dict:
    gateway = _LatencyGateway(FIXTURES, signing_key=SIGNING_KEY)
    partner = gateway.fixtures["partners"][0]
    started = time.monotonic()
    result = collect_evidence(gateway, None, case_id="CASE-BENCH", partner=partner,
                              order_id="EZ202608001")
    wall_ms = int((time.monotonic() - started) * 1000)
    serial_baseline_ms = 7 * 50
    return {
        "parallel_batch_ms": result["parallel"]["duration_ms"],
        "end_to_end_ms": wall_ms,
        "serial_io_baseline_ms": serial_baseline_ms,
        "estimated_speedup": round(serial_baseline_ms / max(result["parallel"]["duration_ms"], 1), 2),
        "workers": result["parallel"]["workers"],
        "task_count": result["parallel"]["task_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT / "data" / "outputs" / "evaluation_summary.json"))
    args = parser.parse_args()

    categories = {}
    all_failures = []
    for name, evaluator in (
        ("golden_e2e", evaluate_golden_cases),
        ("risk_boundaries", evaluate_risk_matrix),
        ("policy_dates", evaluate_policy_dates),
        ("security_probes", evaluate_security_probes),
    ):
        count, failures = evaluator()
        categories[name] = {"scenarios": count, "passed": count - len(failures),
                            "failures": failures}
        all_failures.extend(f"{name}: {failure}" for failure in failures)

    total = sum(item["scenarios"] for item in categories.values())
    passed = sum(item["passed"] for item in categories.values())
    summary = {
        "generated_at": date.today().isoformat(),
        "method": "deterministic_oracle_and_golden_holdout",
        "total_scenarios": total,
        "passed": passed,
        "pass_rate": round(passed / total, 4),
        "security_probe_pass_rate": round(
            categories["security_probes"]["passed"]
            / categories["security_probes"]["scenarios"],
            4,
        ),
        "security_probe_scope": (
            "forged/expired/cross-case capability tokens, actor-scope escalation, "
            "concurrent double-submit, and rollback-token replay"
        ),
        "categories": categories,
        "parallel_benchmark": benchmark_parallel(),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if all_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
