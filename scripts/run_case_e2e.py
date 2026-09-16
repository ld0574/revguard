"""单案件端到端驱动（容器内使用，env 可配置路径与审批模式）。

用法（容器内）：
    python scripts/run_case_e2e.py CASE-2026-0001

环境变量：
    REVGUARD_DB_PATH        案件库路径（默认 data/revguard.db）
    REVGUARD_OUTPUT_DIR     Trace 输出目录
    REVGUARD_REPORT_DIR     审计报告目录
    REVGUARD_APPROVAL_MODE  auto（模拟审批）| wait（真人审批挂起）
    REVGUARD_ENTERPRISE_PROVIDER  mock | erpnext（ToolGateway 自动读取）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.mocks import ToolGateway
from revguard.orchestrator import Orchestrator
from revguard.store import Store
from scripts.seed_demo import seed

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    case_id = sys.argv[1] if len(sys.argv) > 1 else "CASE-2026-0001"
    db = os.environ.get("REVGUARD_DB_PATH", str(ROOT / "data" / "revguard.db"))
    output_dir = os.environ.get("REVGUARD_OUTPUT_DIR", str(ROOT / "data" / "outputs"))
    report_dir = os.environ.get("REVGUARD_REPORT_DIR", str(ROOT / "docs" / "reports"))
    approval_mode = os.environ.get("REVGUARD_APPROVAL_MODE", "auto")

    cases = seed(db, reset=True)
    matched = [c for c in cases if c["case_id"] == case_id]
    if not matched:
        print(f"未找到案件 {case_id}")
        return 1
    case = matched[0]

    specs = {
        fp.stem: json.loads(fp.read_text(encoding="utf-8"))
        for fp in sorted((ROOT / "data" / "golden_cases").glob("*.json"))
    }
    spec = next((s for s in specs.values() if s["input"]["case_id"] == case_id), {})

    store = Store(db)
    # reset 路径会为本次运行生成新的 recording 代次，必须从库里重读最新快照
    case = store.get_case(case_id)
    if case is None:
        print(f"案件 {case_id} 种子后不存在")
        return 1
    gateway = ToolGateway(
        ROOT / "data" / "fixtures",
        finance_fail_times=int(os.environ.get("REVGUARD_FINANCE_FAIL_TIMES", "1")),
        posting_tamper_amount=(spec.get("gateway_overrides") or {}).get(
            "posting_tamper_amount", "0"
        ),
    )
    orchestrator = Orchestrator(
        store, gateway, output_dir=output_dir, report_dir=report_dir,
        approval_mode="wait" if approval_mode == "wait" else "auto",
    )
    state = orchestrator.run_case(case)
    fresh = store.get_case(case_id)
    verification = state.get("verification") or {}
    rollback = (state.get("rollback") or {}).get("verification") or {}
    summary = {
        "case_id": case_id,
        "final_status": fresh["status"],
        "risk_level": fresh.get("risk_level"),
        "verification_status": verification.get("verification_status"),
        "rollback_verification_status": rollback.get("verification_status"),
        "expected": spec.get("expected", {}),
        "pending_errors": [str(e) for e in (state.get("errors") or [])[-3:]],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    mismatches = [
        f"{key}: {summary.get(actual)} != {expected}"
        for key, actual, expected in (
            ("final_status", "final_status", spec.get("expected", {}).get("final_status")),
            ("risk_level", "risk_level", spec.get("expected", {}).get("risk_level")),
            ("verification_status", "verification_status",
             spec.get("expected", {}).get("verification_status")),
            ("rollback_verification_status", "rollback_verification_status",
             spec.get("expected", {}).get("rollback_verification_status")),
        )
        if expected and summary.get(actual) != expected
    ]
    if mismatches:
        print("MISMATCH:")
        for item in mismatches:
            print(f"  {item}")
        return 2
    print("GOLDEN_MATCH")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
