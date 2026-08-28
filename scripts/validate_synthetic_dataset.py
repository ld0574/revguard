#!/usr/bin/env python3
"""Validate the competition fixture pack and emit deterministic provenance evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
SOURCE_FILES = [
    DATA / "synthetic-provenance.json",
    *sorted((DATA / "fixtures").glob("*.json")),
    *sorted((DATA / "golden_cases").glob("*.json")),
]


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def unique(items: list[dict], key: str, errors: list[str]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for item in items:
        value = item[key]
        if value in index:
            errors.append(f"duplicate {key}: {value}")
        index[value] = item
    return index


def validate() -> dict:
    errors: list[str] = []
    manifest = load(DATA / "synthetic-provenance.json")
    fixtures = {path.stem: load(path) for path in (DATA / "fixtures").glob("*.json")}
    cases = [load(path) for path in sorted((DATA / "golden_cases").glob("*.json"))]

    if manifest.get("classification") != "SYNTHETIC":
        errors.append("dataset classification must be SYNTHETIC")
    if manifest.get("production_data_used") is not False:
        errors.append("production_data_used must be false")

    partners = unique(fixtures["partners"], "partner_id", errors)
    orders = unique(fixtures["orders"], "order_id", errors)
    unique(fixtures["contracts"], "contract_id", errors)
    unique(fixtures["invoices"], "invoice_id", errors)
    unique(fixtures["payments"], "payment_id", errors)
    unique(fixtures["refunds"], "refund_id", errors)
    ledger = unique(fixtures["ledger"]["entries"], "ledger_id", errors)
    golden = unique(cases, "case_id", errors)

    for order in orders.values():
        if order["partner_id"] not in partners:
            errors.append(f"order {order['order_id']} has unknown partner")
        if order["currency"] != manifest["currency"]:
            errors.append(f"order {order['order_id']} has unexpected currency")
        if order.get("completed_date") and date.fromisoformat(order["completed_date"]) < date.fromisoformat(order["order_date"]):
            errors.append(f"order {order['order_id']} completes before creation")

    for collection, amount_key, date_key in (
        (fixtures["invoices"], "invoice_amount", "invoice_date"),
        (fixtures["payments"], "payment_amount", "payment_date"),
    ):
        for record in collection:
            order = orders.get(record["order_id"])
            if not order:
                errors.append(f"record for unknown order {record['order_id']}")
                continue
            if record["currency"] != order["currency"]:
                errors.append(f"currency mismatch for {record['order_id']}")
            if record[amount_key] != order["order_amount"]:
                errors.append(f"amount mismatch for {record['order_id']}")
            if date.fromisoformat(record[date_key]) < date.fromisoformat(order["order_date"]):
                errors.append(f"{date_key} precedes order date for {record['order_id']}")

    for refund in fixtures["refunds"]:
        order = orders.get(refund["order_id"])
        if not order:
            errors.append(f"refund references unknown order {refund['order_id']}")
        elif refund["refund_amount"] > order["order_amount"]:
            errors.append(f"refund exceeds order amount for {refund['order_id']}")
    for entry in ledger.values():
        order = orders.get(entry["order_id"])
        if not order:
            errors.append(f"ledger references unknown order {entry['order_id']}")
        elif entry["currency"] != order["currency"]:
            errors.append(f"ledger currency mismatch for {entry['ledger_id']}")

    versions = fixtures["policies"]["versions"]
    for previous, current in zip(versions, versions[1:]):
        if date.fromisoformat(previous["effective_to"]) >= date.fromisoformat(current["effective_from"]):
            errors.append(f"policy periods overlap: {previous['version']} and {current['version']}")
    for case in cases:
        case_input = case["input"]
        order_id = case_input.get("order_id")
        if order_id and order_id not in orders:
            errors.append(f"golden case {case['case_id']} references unknown order {order_id}")
        if case_input["claim"]["currency"] != manifest["currency"]:
            errors.append(f"golden case {case['case_id']} has unexpected currency")
        if case["case_id"] not in manifest["scenario_catalog"]:
            errors.append(f"golden case {case['case_id']} has no provenance catalog entry")
    if set(manifest["scenario_catalog"]) != set(golden):
        errors.append("scenario catalog and golden case IDs differ")

    hashes = {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in SOURCE_FILES
    }
    return {
        "schema_version": "1.0",
        "dataset_id": manifest["dataset_id"],
        "classification": manifest["classification"],
        "production_data_used": manifest["production_data_used"],
        "validation_status": "PASSED" if not errors else "FAILED",
        "checks": {
            "referential_integrity": "PASSED" if not errors else "FAILED",
            "temporal_consistency": "PASSED" if not errors else "FAILED",
            "currency_consistency": "PASSED" if not errors else "FAILED",
            "scenario_provenance_coverage": "PASSED" if not errors else "FAILED",
        },
        "record_counts": {
            "partners": len(partners),
            "orders": len(orders),
            "invoices": len(fixtures["invoices"]),
            "payments": len(fixtures["payments"]),
            "ledger_entries": len(ledger),
            "golden_cases": len(golden),
        },
        "source_sha256": hashes,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()
    result = validate()
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not args.check.exists() or args.check.read_text(encoding="utf-8") != rendered:
            print(f"stale synthetic validation evidence: {args.check}")
            return 2
    elif args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    if result["validation_status"] != "PASSED":
        print("\n".join(result["errors"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
