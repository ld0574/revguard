"""初始化演示数据库（scripts/seed_demo.py）。

用法：
    python3 scripts/seed_demo.py [--db data/revguard.db]

把 data/golden_cases/*.json 中的案件输入写入案件表，状态 CREATED，
供 run_demo.py 或 API 服务启动流程使用。重复执行幂等（INSERT OR REPLACE）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.models import Case, CaseStatus
from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent


def seed(db_path: str, *, reset: bool = False, quiet: bool = False) -> list[dict]:
    store = Store(db_path)
    if reset:
        store.reset()
    cases: list[dict] = []
    for fp in sorted((ROOT / "data" / "golden_cases").glob("*.json")):
        spec = json.loads(fp.read_text(encoding="utf-8"))
        raw = spec["input"]
        existing = store.get_case(raw["case_id"])
        if existing and not reset:
            cases.append(existing)
            if not quiet:
                print(f"  kept   {raw['case_id']}  ({spec['title']})")
            continue
        case = Case(
            case_id=raw["case_id"],
            case_type=raw["case_type"],
            source=raw["source"],
            partner_id=raw.get("partner_id"),
            partner_name=raw.get("partner_name"),
            order_id=raw.get("order_id"),
            description=raw.get("description", ""),
            claim=raw.get("claim", {}),
            entities={"partner_id": raw.get("partner_id"),
                      "partner_name": raw.get("partner_name"),
                      "order_id": raw.get("order_id"),
                      "contract_id": raw.get("contract_id")},
            status=CaseStatus.CREATED.value,
        ).to_dict()
        store.save_case(case)
        store.audit(case["case_id"], "seed", "CASE_CREATED", {"source": fp.name})
        cases.append(case)
        if not quiet:
            print(f"  seeded {case['case_id']}  ({spec['title']})")
    store.close()
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化 RevGuard 演示案件")
    parser.add_argument("--db", default=str(ROOT / "data" / "revguard.db"))
    parser.add_argument("--reset", action="store_true",
                        help="先原子清空案件/证据/审批/执行/验证/审计/Trace")
    parser.add_argument("--gateway-state", default="",
                        help="--reset 时同步删除 ToolGateway 持久化状态文件")
    args = parser.parse_args()
    if args.reset and args.gateway_state:
        gateway_state = Path(args.gateway_state).resolve()
        if gateway_state.exists() and gateway_state.is_file():
            gateway_state.unlink()
    print(f"Seeding demo cases into {args.db}")
    seed(args.db, reset=args.reset)
    print("Done.")
