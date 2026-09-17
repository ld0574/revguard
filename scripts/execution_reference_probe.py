#!/usr/bin/env python3
"""执行引用监视器对抗探针：未读取即不可执行。

5 个场景（前 4 个打开监视器，第 5 个是对照组）：

1. `missing_reads_blocked`     没有任何事实读取 -> `EVIDENCE_GAP`，台账无写入；
2. `fact_bound_references`     读完订单/合同/台账 -> 正常执行，分录带 3 条强绑定引用与锚点；
3. `foreign_order_rejected`    读的是别的订单 -> `EVIDENCE_GAP`，台账无写入；
4. `legacy_receipts_case_only` 只有无键位的历史回执 -> 允许执行但标注 `CASE_ONLY` 弱绑定；
5. `monitor_off_control`       关闭监视器 -> 行为与历史版本一致（兼容性对照）。

全部通过返回 0，任一"未读取却执行成功"返回 1。
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

from revguard.execution_reference import (  # noqa: E402
    BOUND,
    CASE_ONLY,
    reference_anchor,
)
from revguard.mocks import ToolGateway  # noqa: E402

FIXTURES = ROOT / "data" / "fixtures"


def _gateway(*, monitor: bool) -> ToolGateway:
    return ToolGateway(FIXTURES, require_execution_references=monitor)


def _read_facts(gw: ToolGateway, *, case_id: str, order_id: str, partner_id: str) -> None:
    gw.call("crm.get_order", {"order_id": order_id}, case_id=case_id,
            actor="revguard-evidence", scope=["order:read"])
    gw.call("contract.get_contract", {"partner_id": partner_id}, case_id=case_id,
            actor="revguard-evidence", scope=["contract:read"])
    gw.call("finance.get_commission_ledger", {"order_id": order_id}, case_id=case_id,
            actor="revguard-evidence", scope=["ledger:read"])


def _approved_draft(gw: ToolGateway, *, case_id: str, order_id: str = "EZ202608001",
                    amount: str = "100") -> tuple[dict, str]:
    approval = gw.call("workflow.create_approval", {
        "case_id": case_id, "amount": amount, "currency": "KES",
        "component_quota": {"SALES_COMMISSION": amount},
        "risk_level": "L2", "approver_role": "FINANCE_LEAD",
        "action_summary": "执行引用监视器探针",
    }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])["data"]
    decided = gw.call("workflow.decide_approval", {
        "approval_id": approval["approval_id"], "decision": "APPROVED", "comment": "探针",
    }, case_id=case_id, actor="finance.lead", scope=["approval:decide"])["data"]
    draft = gw.call("commission.create_adjustment_draft", {
        "order_id": order_id, "case_id": case_id, "amount": amount,
        "currency": "KES", "component": "SALES_COMMISSION",
    }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])["data"]
    return draft, decided["approval_token"]


def _submit(gw: ToolGateway, *, case_id: str, draft: dict, token: str, key: str) -> dict:
    return gw.call("commission.submit_adjustment", {
        "action_id": draft["action_id"], "approval_token": token,
    }, case_id=case_id, actor="revguard-executor",
        scope=["commission:write"], idempotency_key=key)


def _entry(gw: ToolGateway, case_id: str) -> dict:
    return next((e for e in gw._ledger if str(e.get("source", "")).endswith(case_id)), {})


def _expect(condition: bool, observed: dict, expectation: str, name: str) -> dict:
    return {"scenario": name, "expectation": expectation, "observed": observed, "passed": bool(condition)}


def scenario_missing_reads() -> dict:
    gw = _gateway(monitor=True)
    case_id = "CASE-REF-PROBE-MISSING"
    draft, token = _approved_draft(gw, case_id=case_id)
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-ref-1")
    return _expect(
        (not resp["success"] and resp["error"]["type"] == "EVIDENCE_GAP"
         and not _entry(gw, case_id)),
        {"error": resp.get("error"), "ledger_written": bool(_entry(gw, case_id))},
        "EVIDENCE_GAP：未读取必备事实槽位 ORDER / CONTRACT / COMMISSION_LEDGER，台账无写入",
        "missing_reads_blocked",
    )


def scenario_fact_bound() -> dict:
    gw = _gateway(monitor=True)
    case_id = "CASE-REF-PROBE-BOUND"
    _read_facts(gw, case_id=case_id, order_id="EZ202608001", partner_id="AGT-10001")
    draft, token = _approved_draft(gw, case_id=case_id)
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-ref-2")
    entry = _entry(gw, case_id)
    references = entry.get("execution_references") or []
    anchor_ok = bool(references) and entry.get("reference_anchor") == reference_anchor(references)
    return _expect(
        bool(resp["success"]) and len(references) == 3
        and all(item["binding"] == BOUND for item in references) and anchor_ok,
        {"success": resp.get("success"), "references": references,
         "reference_anchor": entry.get("reference_anchor")},
        "执行成功，分录携带 3 条 FACT_BOUND 引用与可复算锚点",
        "fact_bound_references",
    )


def scenario_foreign_order() -> dict:
    gw = _gateway(monitor=True)
    case_id = "CASE-REF-PROBE-FOREIGN"
    _read_facts(gw, case_id=case_id, order_id="EZ202607002", partner_id="AGT-10002")
    draft, token = _approved_draft(gw, case_id=case_id, order_id="EZ202608001")
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-ref-3")
    return _expect(
        (not resp["success"] and resp["error"]["type"] == "EVIDENCE_GAP"
         and "ORDER" in resp["error"]["message"] and not _entry(gw, case_id)),
        {"error": resp.get("error"), "ledger_written": bool(_entry(gw, case_id))},
        "读了别的订单也不能授权：EVIDENCE_GAP，台账无写入",
        "foreign_order_rejected",
    )


def scenario_legacy_receipts() -> dict:
    gw = _gateway(monitor=True)
    case_id = "CASE-REF-PROBE-LEGACY"
    draft, token = _approved_draft(gw, case_id=case_id)
    for index, tool_name in enumerate(("crm.get_order", "contract.get_contract",
                                       "finance.get_commission_ledger"), start=1):
        gw._receipts.append({
            "tool_receipt": f"RCPT-LEGACY-{index}", "tool_name": tool_name,
            "case_id": case_id, "actor": "revguard-evidence", "success": True,
            "called_at": "2026-09-17T00:00:00Z",
        })
    gw._persist_state()
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-ref-4")
    references = _entry(gw, case_id).get("execution_references") or []
    return _expect(
        bool(resp["success"]) and len(references) == 3
        and all(item["binding"] == CASE_ONLY for item in references),
        {"success": resp.get("success"),
         "bindings": [item["binding"] for item in references],
         "fact_digests": [item["fact_digest"] for item in references]},
        "监视器上线前的历史回执（无事实摘要）允许执行，但标注为 CASE_ONLY 弱绑定",
        "legacy_receipts_case_only",
    )


def scenario_monitor_off() -> dict:
    gw = _gateway(monitor=False)
    case_id = "CASE-REF-PROBE-OFF"
    draft, token = _approved_draft(gw, case_id=case_id)
    resp = _submit(gw, case_id=case_id, draft=draft, token=token, key="probe-ref-5")
    entry = _entry(gw, case_id)
    return _expect(
        bool(resp["success"]) and entry.get("execution_references") == []
        and entry.get("reference_anchor") is None,
        {"success": resp.get("success"),
         "execution_references": entry.get("execution_references"),
         "reference_anchor": entry.get("reference_anchor")},
        "关闭监视器时保持历史行为（默认关闭，演示栈显式打开）",
        "monitor_off_control",
    )


SCENARIOS = (
    scenario_missing_reads,
    scenario_fact_bound,
    scenario_foreign_order,
    scenario_legacy_receipts,
    scenario_monitor_off,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None,
                        help="把结果写成 JSON 文件（默认只打印）")
    args = parser.parse_args()

    results = [scenario() for scenario in SCENARIOS]
    passed = all(item["passed"] for item in results)
    report = {
        "probe": "execution-reference-monitor",
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
    print(f"\n执行引用监视器探针：{sum(1 for i in results if i['passed'])}/{len(results)} 通过")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
