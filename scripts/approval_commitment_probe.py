#!/usr/bin/env python3
"""审批 = 参数承诺：对抗性探针。

复现"审批通过后改动参数再执行"的攻击，证明执行入口会在写入台账之前拒绝：

1. `unsealed_drift`   只改金额、不动摘要 -> 重算摘要与承诺不一致；
2. `resealed_drift`   金额和摘要一起重算 -> 令牌里的摘要与审批单对不上；
3. `renewal_drift`    参数漂移后重新申请能力令牌 -> 拒绝签发；
4. `canonical_amount` `100` / `100.0` / `100.00` 得到同一摘要，不同案件得到不同摘要；
5. `happy_path`       未改动的审批仍可正常执行，台账金额等于承诺金额（对照组）。

全部通过返回 0，任一篡改被放行返回 1。
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from revguard.commitment import approval_digest  # noqa: E402
from revguard.mocks import ToolGateway  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures"


def _gateway() -> ToolGateway:
    return ToolGateway(FIXTURES)


def _approved_draft(gw: ToolGateway, *, case_id: str, amount: str = "100") -> tuple[dict, str]:
    approval = gw.call("workflow.create_approval", {
        "case_id": case_id, "amount": amount, "currency": "KES",
        "component_quota": {"SALES_COMMISSION": amount},
        "risk_level": "L2", "approver_role": "FINANCE_LEAD",
        "action_summary": "探针：佣金调整",
    }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])["data"]
    decided = gw.call("workflow.decide_approval", {
        "approval_id": approval["approval_id"], "decision": "APPROVED",
        "comment": "探针审批",
    }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])["data"]
    draft = gw.call("commission.create_adjustment_draft", {
        "order_id": "EZ202608001", "case_id": case_id, "amount": amount,
        "currency": "KES", "component": "SALES_COMMISSION",
    }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
    return draft, decided["approval_token"]


def _submit(gw: ToolGateway, *, case_id: str, draft: dict, token: str, key: str) -> dict:
    return gw.call("commission.submit_adjustment", {
        "action_id": draft["action_id"], "approval_token": token,
    }, case_id=case_id, actor="revguard-executor",
        scope=["commission:write"], idempotency_key=key)


def _ledger_written(gw: ToolGateway, case_id: str) -> bool:
    return any(str(entry.get("source", "")).endswith(case_id) for entry in gw._ledger)


def scenario_unsealed_drift() -> dict:
    gw = _gateway()
    case_id = "CASE-PROBE-DRIFT"
    draft, token = _approved_draft(gw, case_id=case_id)
    approval_id = next(iter(gw._approvals))
    gw._approvals[approval_id]["amount"] = "999"
    gw._persist_state()
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-drift-1")
    return {
        "scenario": "unsealed_drift",
        "expectation": "AUTH_FAILED / 参数漂移，且台账无写入",
        "observed": resp.get("error", {}) if not resp.get("success") else {"status": "EXECUTED"},
        "passed": (not resp["success"]
                   and resp["error"]["type"] == "AUTH_FAILED"
                   and "参数漂移" in resp["error"]["message"]
                   and not _ledger_written(gw, case_id)),
    }


def scenario_resealed_drift() -> dict:
    gw = _gateway()
    case_id = "CASE-PROBE-RESEAL"
    draft, token = _approved_draft(gw, case_id=case_id)
    approval_id = next(iter(gw._approvals))
    tampered = gw._approvals[approval_id]
    tampered["amount"] = "999"
    tampered["parameters_digest"] = approval_digest(tampered)
    gw._persist_state()
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-drift-2")
    return {
        "scenario": "resealed_drift",
        "expectation": "AUTH_FAILED / 参数漂移（能力令牌摘要与审批单不一致）",
        "observed": resp.get("error", {}) if not resp.get("success") else {"status": "EXECUTED"},
        "passed": (not resp["success"]
                   and resp["error"]["type"] == "AUTH_FAILED"
                   and "参数漂移" in resp["error"]["message"]
                   and not _ledger_written(gw, case_id)),
    }


def scenario_renewal_drift() -> dict:
    gw = _gateway()
    case_id = "CASE-PROBE-RENEW"
    _approved_draft(gw, case_id=case_id)
    approval_id = next(iter(gw._approvals))
    gw._approvals[approval_id]["amount"] = "999"
    gw._persist_state()
    resp = gw.call("workflow.renew_approval_capability", {
        "approval_id": approval_id, "case_id": case_id,
    }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])
    return {
        "scenario": "renewal_drift",
        "expectation": "AUTH_FAILED / 参数漂移，拒绝重新签发能力令牌",
        "observed": resp.get("error", {}) if not resp.get("success") else {"status": "RENEWED"},
        "passed": (not resp["success"]
                   and resp["error"]["type"] == "AUTH_FAILED"
                   and "参数漂移" in resp["error"]["message"]),
    }


def scenario_canonical_amount() -> dict:
    gw = _gateway()
    _approved_draft(gw, case_id="CASE-PROBE-CANON-A", amount="100")
    _approved_draft(gw, case_id="CASE-PROBE-CANON-B", amount="100.00")
    records = {approval["case_id"]: approval for approval in gw._approvals.values()}
    digests = {case_id: record["parameters_digest"] for case_id, record in records.items()}
    canonical = {case_id: record["parameters_commitment"]["amount"]
                 for case_id, record in records.items()}
    same_amount = set(canonical.values()) == {"100.00"}
    return {
        "scenario": "canonical_amount",
        "expectation": "写法定点化一致；摘要按审批单/案件绑定而不同",
        "observed": {"digests": digests, "canonical_amounts": canonical},
        "passed": same_amount and len(set(digests.values())) == 2,
    }


def scenario_happy_path() -> dict:
    gw = _gateway()
    case_id = "CASE-PROBE-CONTROL"
    draft, token = _approved_draft(gw, case_id=case_id)
    approval_id = next(iter(gw._approvals))
    committed = gw._approvals[approval_id]["parameters_digest"]
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-control-1")
    entry = next((e for e in gw._ledger if str(e.get("source", "")).endswith(case_id)), {})
    return {
        "scenario": "happy_path",
        "expectation": "承诺未变动的审批正常执行，台账金额等于承诺金额",
        "observed": {"success": resp.get("success"), "ledger_amount": entry.get("amount"),
                     "committed_digest": committed},
        "passed": (resp.get("success") and str(entry.get("amount")) == "100"
                   and approval_digest(gw._approvals[approval_id]) == committed),
    }


SCENARIOS = (
    scenario_unsealed_drift,
    scenario_resealed_drift,
    scenario_renewal_drift,
    scenario_canonical_amount,
    scenario_happy_path,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None,
                        help="把结果写成 JSON 文件（默认只打印）")
    args = parser.parse_args()

    results = [scenario() for scenario in SCENARIOS]
    passed = all(item["passed"] for item in results)
    report = {
        "probe": "approval-parameter-commitment",
        "generated_at": datetime.now(UTC).isoformat(),
        "python": platform.python_version(),
        "results": results,
        "passed": passed,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    print(f"\n审批参数承诺探针：{sum(1 for i in results if i['passed'])}/{len(results)} 通过")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
