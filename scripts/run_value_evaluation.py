#!/usr/bin/env python3
"""Evaluate business value without confusing synthetic data with production facts."""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from decimal import Decimal
from pathlib import Path

REQUIRED_FIELDS = {
    "case_id", "data_classification", "source_reference",
    "manual_processing_minutes", "revguard_processing_minutes",
    "wrong_payment_before", "wrong_payment_after",
    "recovery_cost_before", "recovery_cost_after",
    "manual_escalation_required", "audit_exception_before",
    "audit_exception_after",
}
ALLOWED_CLASSIFICATIONS = {"synthetic_demonstration", "measured_production"}


def _bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"布尔值必须为 true/false: {value!r}")


def evaluate_rows(rows: list[dict[str, str]]) -> dict:
    if not rows:
        raise ValueError("价值基线至少需要 1 条案件")
    missing = REQUIRED_FIELDS - set(rows[0])
    if missing:
        raise ValueError(f"基线列缺失: {sorted(missing)}")
    classifications = {row["data_classification"].strip() for row in rows}
    if not classifications <= ALLOWED_CLASSIFICATIONS:
        raise ValueError(f"未知数据分类: {sorted(classifications)}")
    if "measured_production" in classifications:
        missing_sources = [row["case_id"] for row in rows
                           if not row["source_reference"].strip()]
        if missing_sources:
            raise ValueError(f"生产基线缺少来源引用: {missing_sources}")

    manual = [float(row["manual_processing_minutes"]) for row in rows]
    assisted = [float(row["revguard_processing_minutes"]) for row in rows]
    if any(value < 0 for value in manual + assisted):
        raise ValueError("处理时长不能为负数")
    before_wrong = [_bool(row["wrong_payment_before"]) for row in rows]
    after_wrong = [_bool(row["wrong_payment_after"]) for row in rows]
    escalated = [_bool(row["manual_escalation_required"]) for row in rows]
    audit_before = [_bool(row["audit_exception_before"]) for row in rows]
    audit_after = [_bool(row["audit_exception_after"]) for row in rows]
    recovery_before = sum(Decimal(row["recovery_cost_before"]) for row in rows)
    recovery_after = sum(Decimal(row["recovery_cost_after"]) for row in rows)
    median_manual = statistics.median(manual)
    median_assisted = statistics.median(assisted)

    def rate(values: list[bool]) -> float:
        return round(sum(values) / len(values), 4)

    all_production = classifications == {"measured_production"}
    return {
        "schema_version": "1.0",
        "case_count": len(rows),
        "data_classifications": sorted(classifications),
        "production_claim_allowed": all_production,
        "methodology_only": not all_production,
        "metrics": {
            "median_manual_processing_minutes": round(median_manual, 2),
            "median_revguard_processing_minutes": round(median_assisted, 2),
            "median_processing_time_reduction_rate": (
                round((median_manual - median_assisted) / median_manual, 4)
                if median_manual else None
            ),
            "wrong_payment_rate_before": rate(before_wrong),
            "wrong_payment_rate_after": rate(after_wrong),
            "recovery_cost_before": str(recovery_before.quantize(Decimal("0.01"))),
            "recovery_cost_after": str(recovery_after.quantize(Decimal("0.01"))),
            "manual_escalation_rate": rate(escalated),
            "audit_exception_rate_before": rate(audit_before),
            "audit_exception_rate_after": rate(audit_after),
        },
        "guardrail": (
            "包含合成数据：只验证指标口径与计算链，不得宣称为企业真实收益。"
            if not all_production else
            "全部行已标记为可追溯的生产实测数据。"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    report = evaluate_rows(rows)
    content = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
