#!/usr/bin/env python3
"""RevGuard 端到端 Demo 入口（scripts/run_demo.py）。

用法：
    python3 scripts/run_demo.py [--case CASE-2026-0001] [--wait-approval]

演示内容（对照设计文档 20.3 的必演清单）：
1. GOLDEN-001：完整闭环 + 工具失败重试（财务接口故障注入 1 次）+ 人工审批 + 受控写回 + 独立验证
2. GOLDEN-002：证据冲突（当前等级 vs 订单时点等级）+ 负向调整（扣回强制审批）
3. GOLDEN-003：证据不足条件分支（挂起补证、升级人工、不产出虚假结论）

运行产物自动沉淀到：
- docs/reports/CASE-*.md           审计报告
- data/outputs/traces/CASE-*.json  全链路 Trace
- data/outputs/case_memory/*.json  案例评测样本
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.mocks import ToolGateway  # noqa: E402
from revguard.orchestrator import Orchestrator  # noqa: E402
from revguard.store import Store  # noqa: E402
from scripts.seed_demo import seed  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "revguard.db"
FIXTURES = ROOT / "data" / "fixtures"
OUTPUT_DIR = ROOT / "data" / "outputs"
REPORT_DIR = ROOT / "docs" / "reports"


def banner(text: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {text}")
    print("=" * 72)


def main() -> int:
    parser = argparse.ArgumentParser(description="RevGuard 端到端 Demo")
    parser.add_argument("--case", help="只运行指定案件")
    parser.add_argument("--wait-approval", action="store_true",
                        help="审批节点挂起等待（默认演示环境自动模拟人工审批）")
    parser.add_argument("--keep-db", action="store_true", help="不清空历史数据库")
    args = parser.parse_args()

    banner("RevGuard Demo — 初始化")
    store = Store(DB_PATH)
    cases = seed(str(DB_PATH), reset=not args.keep_db)
    if args.case:
        cases = [c for c in cases if c["case_id"] == args.case]
        if not cases:
            print(f"未找到案件 {args.case}")
            return 1

    specs = {fp.stem: json.loads(fp.read_text(encoding="utf-8"))
             for fp in sorted((ROOT / "data" / "golden_cases").glob("*.json"))}

    results: list[dict] = []
    mismatches: list[str] = []
    for case in cases:
        spec = next((s for s in specs.values() if s["input"]["case_id"] == case["case_id"]), {})
        gateway = ToolGateway(
            FIXTURES, finance_fail_times=1,
            verification_tamper_amount=(spec.get("gateway_overrides") or {}).get(
                "verification_tamper_amount", "0"
            ),
        )
        orchestrator = Orchestrator(
            store, gateway, output_dir=OUTPUT_DIR, report_dir=REPORT_DIR,
            approval_mode="wait" if args.wait_approval else "auto")
        banner(f"{case['case_id']} — {spec.get('title', '')}")
        state = orchestrator.run_case(case)
        fresh = store.get_case(case["case_id"])
        verification = state.get("verification") or {}
        summary = {
            "case_id": case["case_id"],
            "final_status": fresh["status"],
            "risk_level": fresh.get("risk_level"),
            "verification": verification.get("verification_status"),
            "rollback_verification": ((state.get("rollback") or {}).get("verification") or {}).get(
                "verification_status"
            ),
            "expected": spec.get("expected", {}),
        }
        results.append(summary)
        rca = state.get("root_cause_report") or {}
        print(f"\n  最终状态      : {summary['final_status']}")
        if rca:
            print(f"  应有佣金      : {rca.get('total_expected')}")
            print(f"  台账实有      : {rca.get('total_posted')}")
            print(f"  差额          : {rca.get('total_delta')}")
            print(f"  根因          : {', '.join(rca.get('root_causes') or ['-'])}")
        if summary["verification"]:
            print(f"  独立验证      : {summary['verification']}")
        if summary["rollback_verification"]:
            print(f"  回滚后验证    : {summary['rollback_verification']}")
        if state.get("errors"):
            print(f"  挂起原因      : {state['errors'][-1]}")
        print(f"  审计报告      : docs/reports/{case['case_id']}.md")
        expected = summary["expected"]
        for key, actual_key in (
            ("final_status", "final_status"),
            ("risk_level", "risk_level"),
            ("verification_status", "verification"),
            ("rollback_verification_status", "rollback_verification"),
        ):
            if key in expected and summary.get(actual_key) != expected[key]:
                mismatches.append(
                    f"{case['case_id']} {key}: {summary.get(actual_key)} != {expected[key]}"
                )

    banner("Demo 结果汇总")
    for r in results:
        print(f"  {r['case_id']}: {r['final_status']}"
              + (f"  verify={r['verification']}" if r["verification"] else ""))
    print(f"\n产物：{REPORT_DIR}  |  {OUTPUT_DIR}")
    if mismatches:
        print("\nGolden 期望不一致：")
        for mismatch in mismatches:
            print(f"  - {mismatch}")
    store.close()
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
