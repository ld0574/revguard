#!/usr/bin/env python3
"""Build a reproducible Olist revenue-assurance experiment.

The source transaction rows stay untouched and retain their original IDs.  Public
fee rules are evaluated as a current-policy counterfactual on the historical
transactions.  Platform assignment, settlement rows and injected anomalies are
explicitly synthetic.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import zipfile
from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data" / "public" / "fee-rules-v1.json"
SEED = 202609
MONEY = Decimal("0.01")
REQUIRED_MEMBERS = {
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_payments_dataset.csv",
    "olist_sellers_dataset.csv",
    "olist_customers_dataset.csv",
    "olist_products_dataset.csv",
}
ANOMALY_TYPES = (
    "COMMISSION_RATE_MISMATCH",
    "DUPLICATE_COMMISSION",
    "MISSING_COMMISSION",
    "CANCELLED_ORDER_SETTLED",
    "WRONG_SELLER_TIER",
    "SETTLEMENT_AMOUNT_MISMATCH",
    "MISSING_FEE",
    "UNEXPECTED_FEE",
    "DUPLICATE_SETTLEMENT",
    "INVALID_RULE_EFFECTIVE_DATE",
)


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value or 0)).quantize(MONEY, rounding=ROUND_HALF_UP)


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_rank(identifier: str, *, salt: str) -> str:
    return hashlib.sha256(f"{SEED}:{salt}:{identifier}".encode()).hexdigest()


def read_csv(archive: zipfile.ZipFile, name: str):
    with archive.open(name) as raw:
        with io.TextIOWrapper(raw, encoding="utf-8-sig", newline="") as text:
            yield from csv.DictReader(text)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_rules(path: Path = RULES_PATH) -> tuple[dict, list[dict]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rules = document.get("rules") or []
    if len(rules) < 5:
        raise ValueError("public rule pack must contain at least five rules")
    required = {
        "rule_id", "platform", "rule_type", "percentage", "fixed_fee",
        "effective_from", "source_url", "source_type", "provenance_kind",
    }
    for rule in rules:
        missing = required - set(rule)
        if missing:
            raise ValueError(f"rule {rule.get('rule_id')} misses {sorted(missing)}")
        if rule["source_type"] != "OFFICIAL_PUBLIC_RULE":
            raise ValueError(f"rule {rule['rule_id']} is not an official public rule")
    return document, rules


def rule_fee(gross: Decimal, rule: dict, evaluation_date: date) -> Decimal:
    if evaluation_date < date.fromisoformat(rule["effective_from"]):
        raise ValueError(f"rule {rule['rule_id']} is not effective")
    if rule.get("effective_to") and evaluation_date > date.fromisoformat(rule["effective_to"]):
        raise ValueError(f"rule {rule['rule_id']} expired")
    percentage = Decimal(rule["percentage"]) / Decimal("100")
    return money(gross * percentage + Decimal(rule["fixed_fee"]))


def load_source(
    archive_path: Path, sample_size: int, anomaly_rate: Decimal
) -> tuple[list[dict], dict]:
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        missing = REQUIRED_MEMBERS - names
        if missing:
            raise ValueError("Olist archive misses: " + ", ".join(sorted(missing)))

        orders = {row["order_id"]: row for row in read_csv(archive, "olist_orders_dataset.csv")}
        ranked = sorted(orders, key=lambda item: stable_rank(item, salt="sample"))
        cancelled_ranked = [
            item for item in ranked
            if orders[item]["order_status"] in {"canceled", "unavailable"}
        ]
        cancelled_quota = max(
            1,
            math.ceil(
                Decimal(sample_size) * anomaly_rate / Decimal(len(ANOMALY_TYPES))
            ),
        )
        candidates = set(ranked[: min(len(ranked), max(sample_size * 2, sample_size + 1000))])
        candidates.update(cancelled_ranked)

        items: dict[str, dict] = defaultdict(lambda: {
            "price": Decimal("0"), "freight": Decimal("0"), "seller_ids": set(),
            "product_ids": set(), "item_count": 0,
        })
        for row in read_csv(archive, "olist_order_items_dataset.csv"):
            order_id = row["order_id"]
            if order_id not in candidates:
                continue
            item = items[order_id]
            item["price"] += Decimal(row["price"])
            item["freight"] += Decimal(row["freight_value"])
            item["seller_ids"].add(row["seller_id"])
            item["product_ids"].add(row["product_id"])
            item["item_count"] += 1

        payments: dict[str, dict] = defaultdict(lambda: {
            "value": Decimal("0"), "types": set(), "installments": 0,
        })
        for row in read_csv(archive, "olist_order_payments_dataset.csv"):
            order_id = row["order_id"]
            if order_id not in candidates:
                continue
            payment = payments[order_id]
            payment["value"] += Decimal(row["payment_value"])
            payment["types"].add(row["payment_type"])
            payment["installments"] = max(
                payment["installments"], int(row["payment_installments"] or 0)
            )

        complete = [item for item in ranked if item in items and item in payments]
        # The ERP acceptance model stores one Sales Partner and one product on
        # each order header.  Selecting only single-seller/single-product orders
        # keeps that mapping lossless instead of silently choosing the first ID
        # from a multi-party order.  The source archive itself remains intact.
        eligible = [
            item for item in complete
            if len(items[item]["seller_ids"]) == 1
            and len(items[item]["product_ids"]) == 1
        ]
        cancelled_eligible = [
            item for item in eligible
            if orders[item]["order_status"] in {"canceled", "unavailable"}
        ]
        normal_eligible = [
            item for item in eligible
            if orders[item]["order_status"] not in {"canceled", "unavailable"}
        ]
        if len(cancelled_eligible) < cancelled_quota:
            raise ValueError(
                f"only {len(cancelled_eligible)} complete canceled orders, need {cancelled_quota}"
            )
        if len(normal_eligible) < sample_size - cancelled_quota:
            raise ValueError(f"only {len(normal_eligible)} complete normal orders")
        selected_ids = sorted(
            cancelled_eligible[:cancelled_quota]
            + normal_eligible[: sample_size - cancelled_quota],
            key=lambda item: stable_rank(item, salt="sample"),
        )
        selected = set(selected_ids)
        customer_ids = {orders[item]["customer_id"] for item in selected_ids}
        seller_ids = set().union(*(items[item]["seller_ids"] for item in selected_ids))
        product_ids = set().union(*(items[item]["product_ids"] for item in selected_ids))
        customers = {
            row["customer_id"]: row for row in read_csv(archive, "olist_customers_dataset.csv")
            if row["customer_id"] in customer_ids
        }
        sellers = {
            row["seller_id"]: row for row in read_csv(archive, "olist_sellers_dataset.csv")
            if row["seller_id"] in seller_ids
        }
        products = {
            row["product_id"]: row for row in read_csv(archive, "olist_products_dataset.csv")
            if row["product_id"] in product_ids
        }

        transactions = []
        for order_id in selected_ids:
            order, item, payment = orders[order_id], items[order_id], payments[order_id]
            gross = money(item["price"] + item["freight"])
            record = {
                "order_id": order_id,
                "customer_id": order["customer_id"],
                "seller_id": sorted(item["seller_ids"])[0],
                "seller_count": len(item["seller_ids"]),
                "product_id": sorted(item["product_ids"])[0],
                "product_count": len(item["product_ids"]),
                "item_count": item["item_count"],
                "order_status": order["order_status"],
                "purchase_timestamp": order["order_purchase_timestamp"],
                "approved_at": order["order_approved_at"],
                "delivered_at": order["order_delivered_customer_date"],
                "item_amount": str(money(item["price"])),
                "freight_amount": str(money(item["freight"])),
                "gross_amount": str(gross),
                "payment_value": str(money(payment["value"])),
                "payment_types": ";".join(sorted(payment["types"])),
                "payment_installments": payment["installments"],
                "currency": "BRL",
                "transaction_provenance": "PUBLIC_REAL",
                "source_dataset": "olist-brazilian-ecommerce",
            }
            record["source_row_hash"] = canonical_hash(record)
            transactions.append(record)

        dimensions = {
            "customers": [customers[item] for item in sorted(customers)],
            "sellers": [sellers[item] for item in sorted(sellers)],
            "products": [products[item] for item in sorted(products)],
            "selected_order_ids": selected,
            "sampling_quality": {
                "complete_order_count": len(complete),
                "eligible_single_seller_product_count": len(eligible),
                "excluded_multi_seller_or_product_count": len(complete) - len(eligible),
                "erp_mapping_mode": "ORDER_LEVEL_SINGLE_SELLER_PRODUCT_AGGREGATE",
            },
        }
        return transactions, dimensions


def anomaly_assignments(transactions: list[dict], anomaly_rate: Decimal) -> dict[str, str]:
    target = max(len(ANOMALY_TYPES), int(Decimal(len(transactions)) * anomaly_rate))
    if target >= len(transactions):
        raise ValueError("anomaly_rate leaves no normal control population")
    per_type = target // len(ANOMALY_TYPES)
    remainder = target % len(ANOMALY_TYPES)
    selected: dict[str, str] = {}
    used: set[str] = set()
    ranked = sorted(transactions, key=lambda item: stable_rank(item["order_id"], salt="anomaly"))
    for index, anomaly_type in enumerate(ANOMALY_TYPES):
        count = per_type + (1 if index < remainder else 0)
        pool = [
            item for item in ranked if item["order_id"] not in used
            and (
                item["order_status"] in {"canceled", "unavailable"}
                if anomaly_type == "CANCELLED_ORDER_SETTLED"
                else item["order_status"] not in {"canceled", "unavailable"}
            )
        ]
        if len(pool) < count:
            raise ValueError(f"not enough rows for {anomaly_type}: need {count}")
        for item in pool[:count]:
            selected[item["order_id"]] = anomaly_type
            used.add(item["order_id"])
    return selected


def synthesize(
    transactions: list[dict], rules: list[dict], anomaly_rate: Decimal
) -> tuple[list[dict], list[dict]]:
    evaluation_date = date(2026, 9, 16)
    assignments = anomaly_assignments(transactions, anomaly_rate)
    settlements: list[dict] = []
    cases: list[dict] = []
    for transaction in transactions:
        order_id = transaction["order_id"]
        rule_index = int(stable_rank(order_id, salt="rule")[:8], 16) % len(rules)
        rule = rules[rule_index]
        gross = Decimal(transaction["gross_amount"])
        nominal_fee = rule_fee(gross, rule, evaluation_date)
        cancelled = transaction["order_status"] in {"canceled", "unavailable"}
        expected_commission = Decimal("0.00") if cancelled else nominal_fee
        expected_settlement = Decimal("0.00") if cancelled else money(gross - nominal_fee)
        actual_commission = expected_commission
        actual_settlement = expected_settlement
        actual_rule_id = rule["rule_id"]
        anomaly_type = assignments.get(order_id)

        if anomaly_type == "COMMISSION_RATE_MISMATCH":
            actual_commission = money(expected_commission + gross * Decimal("0.02"))
            actual_settlement = money(gross - actual_commission)
        elif anomaly_type == "DUPLICATE_COMMISSION":
            actual_commission = money(expected_commission * 2)
            actual_settlement = money(gross - actual_commission)
        elif anomaly_type == "MISSING_COMMISSION":
            actual_commission = Decimal("0.00")
            actual_settlement = gross
        elif anomaly_type == "CANCELLED_ORDER_SETTLED":
            actual_commission = nominal_fee
            actual_settlement = money(gross - nominal_fee)
        elif anomaly_type == "WRONG_SELLER_TIER":
            actual_commission = money(expected_commission + gross * Decimal("0.015"))
            actual_settlement = money(gross - actual_commission)
        elif anomaly_type == "SETTLEMENT_AMOUNT_MISMATCH":
            actual_settlement = money(expected_settlement - Decimal("1.00"))
        elif anomaly_type == "MISSING_FEE":
            actual_commission = money(max(Decimal("0"), expected_commission - gross * Decimal("0.01")))
            actual_settlement = money(gross - actual_commission)
        elif anomaly_type == "UNEXPECTED_FEE":
            actual_commission = money(expected_commission + gross * Decimal("0.01"))
            actual_settlement = money(gross - actual_commission)
        elif anomaly_type == "DUPLICATE_SETTLEMENT":
            actual_settlement = money(expected_settlement * 2)
        elif anomaly_type == "INVALID_RULE_EFFECTIVE_DATE":
            wrong_rule = rules[(rule_index + 1) % len(rules)]
            actual_rule_id = wrong_rule["rule_id"] + ":SYNTHETIC_EXPIRED_VERSION"
            actual_commission = rule_fee(gross, wrong_rule, evaluation_date)
            if actual_commission == expected_commission:
                actual_commission = money(actual_commission + Decimal("1.00"))
            actual_settlement = money(gross - actual_commission)

        commission_variance = money(actual_commission - expected_commission)
        settlement_variance = money(actual_settlement - expected_settlement)
        impact = max(abs(commission_variance), abs(settlement_variance))
        settlement = {
            "settlement_id": "SET-" + order_id,
            "order_id": order_id,
            "seller_id": transaction["seller_id"],
            "rule_id": rule["rule_id"],
            "actual_rule_id": actual_rule_id,
            "platform": rule["platform"],
            "gross_amount": str(money(gross)),
            "commission_rate": rule["percentage"],
            "fixed_fee": rule["fixed_fee"],
            "expected_commission": str(money(expected_commission)),
            "expected_settlement": str(money(expected_settlement)),
            "actual_commission": str(money(actual_commission)),
            "actual_settlement": str(money(actual_settlement)),
            "commission_variance": str(commission_variance),
            "settlement_variance": str(settlement_variance),
            "financial_impact": str(money(impact)),
            "settlement_status": "EXCEPTION" if anomaly_type else "MATCHED",
            "settlement_date": "2026-09-16",
            "is_synthetic": True,
            "scenario_id": (
                f"SCN-{anomaly_type}-{order_id}" if anomaly_type else "CONTROL"
            ),
            "random_seed": SEED,
            "transaction_source_type": "PUBLIC_REAL",
            "rule_source_type": "PUBLIC_REAL",
            "rule_mapping_source_type": "SYNTHETIC_DOMAIN",
            "settlement_source_type": "SYNTHETIC_DOMAIN",
            "exception_source_type": "SYNTHETIC_DOMAIN" if anomaly_type else "NONE",
            "rule_source_url": rule["source_url"],
            "rule_evaluation_date": evaluation_date.isoformat(),
            "rule_effective_basis": rule["effective_basis"],
        }
        settlement["settlement_hash"] = canonical_hash(settlement)
        settlements.append(settlement)

        if anomaly_type:
            if impact <= 0:
                raise ValueError(f"anomaly {anomaly_type} has no financial impact")
            cases.append({
                "case_id": "RG-" + hashlib.sha256(order_id.encode()).hexdigest()[:12].upper(),
                "risk_type": anomaly_type,
                "order_id": order_id,
                "seller_id": transaction["seller_id"],
                "rule_id": rule["rule_id"],
                "gross_amount": str(money(gross)),
                "expected_commission": str(money(expected_commission)),
                "actual_commission": str(money(actual_commission)),
                "expected_settlement": str(money(expected_settlement)),
                "actual_settlement": str(money(actual_settlement)),
                "variance_amount": str(money(impact)),
                "severity": "high" if impact >= Decimal("100") else "medium",
                "source_transaction_type": "PUBLIC_REAL",
                "rule_source_type": "PUBLIC_REAL",
                "rule_mapping_source_type": "SYNTHETIC_DOMAIN",
                "settlement_source_type": "SYNTHETIC_DOMAIN",
                "scenario_source_type": "SYNTHETIC_DOMAIN",
                "status": "OPEN",
                "explanation": {
                    "what_happened": anomaly_type,
                    "why_abnormal": "实际合成结算与确定性规则结果不一致",
                    "correct_rule": rule["rule_id"],
                    "actual_rule": actual_rule_id,
                    "financial_impact": str(money(impact)),
                    "recommended_action": "人工复核后执行受控调整或冲销",
                },
                "evidence": [
                    {
                        "type": "PUBLIC_TRANSACTION",
                        "source_id": order_id,
                        "content_hash": transaction["source_row_hash"],
                        "provenance_kind": "PUBLIC_REAL",
                    },
                    {
                        "type": "PUBLIC_FEE_RULE",
                        "source_id": rule["rule_id"],
                        "source_url": rule["source_url"],
                        "content_hash": canonical_hash(rule),
                        "provenance_kind": "PUBLIC_REAL",
                    },
                    {
                        "type": "SYNTHETIC_SETTLEMENT",
                        "source_id": settlement["settlement_id"],
                        "content_hash": settlement["settlement_hash"],
                        "provenance_kind": "SYNTHETIC_DOMAIN",
                    },
                ],
            })
    return settlements, cases


def erpnext_staging(transactions: list[dict], dimensions: dict) -> dict[str, list[dict]]:
    customer_by_id = {item["customer_id"]: item for item in dimensions["customers"]}
    seller_by_id = {item["seller_id"]: item for item in dimensions["sellers"]}
    product_by_id = {item["product_id"]: item for item in dimensions["products"]}
    customer_ids = sorted({item["customer_id"] for item in transactions})
    seller_ids = sorted({item["seller_id"] for item in transactions})
    product_ids = sorted({item["product_id"] for item in transactions})
    return {
        "customers": [{
            "customer_id": item,
            "customer_name": "Olist Customer " + item[:12],
            "city": customer_by_id.get(item, {}).get("customer_city", ""),
            "state": customer_by_id.get(item, {}).get("customer_state", ""),
            "source_provenance": "PUBLIC_REAL",
        } for item in customer_ids],
        "partners": [{
            "partner_id": item,
            "partner_name": "Olist Seller " + item[:12],
            "city": seller_by_id.get(item, {}).get("seller_city", ""),
            "state": seller_by_id.get(item, {}).get("seller_state", ""),
            "source_provenance": "PUBLIC_REAL",
        } for item in seller_ids],
        "items": [{
            "product_id": item,
            "item_name": "Olist Product " + item[:12],
            "product_category": product_by_id.get(item, {}).get("product_category_name", "unknown"),
            "source_provenance": "PUBLIC_REAL",
        } for item in product_ids],
        "sales_orders": [{
            "order_id": item["order_id"],
            "customer_id": item["customer_id"],
            "partner_id": item["seller_id"],
            "product_id": item["product_id"],
            "transaction_date": item["purchase_timestamp"][:10],
            "gross_amount": item["gross_amount"],
            "currency": item["currency"],
            "status": item["order_status"],
            "source_row_hash": item["source_row_hash"],
        } for item in transactions],
        "sales_invoices": [{
            "invoice_id": "INV-OLIST-" + item["order_id"],
            "order_id": item["order_id"],
            "posting_date": (item["approved_at"] or item["purchase_timestamp"])[:10],
            "gross_amount": item["gross_amount"],
            "currency": item["currency"],
        } for item in transactions],
        "payment_entries": [{
            "payment_id": "PAY-OLIST-" + item["order_id"],
            "order_id": item["order_id"],
            "posting_date": (item["approved_at"] or item["purchase_timestamp"])[:10],
            "payment_value": item["payment_value"],
            "currency": item["currency"],
            "payment_types": item["payment_types"],
        } for item in transactions],
    }


def run(
    archive_path: Path,
    output_dir: Path,
    *,
    sample_size: int = 10_000,
    anomaly_rate: Decimal = Decimal("0.08"),
    rules_path: Path = RULES_PATH,
) -> dict:
    if sample_size < len(ANOMALY_TYPES) * 2:
        raise ValueError("sample_size is too small for controls and every anomaly type")
    rules_document, rules = load_rules(rules_path)
    transactions, dimensions = load_source(archive_path, sample_size, anomaly_rate)
    settlements, cases = synthesize(transactions, rules, anomaly_rate)
    output_dir.mkdir(parents=True, exist_ok=True)

    transaction_fields = list(transactions[0])
    settlement_fields = list(settlements[0])
    write_csv(output_dir / "transactions.csv", transactions, transaction_fields)
    write_csv(output_dir / "synthetic-settlements.csv", settlements, settlement_fields)
    with (output_dir / "risk-cases.jsonl").open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")

    staging = erpnext_staging(transactions, dimensions)
    staging_fields = {
        "customers": ["customer_id", "customer_name", "city", "state", "source_provenance"],
        "partners": ["partner_id", "partner_name", "city", "state", "source_provenance"],
        "items": ["product_id", "item_name", "product_category", "source_provenance"],
        "sales_orders": ["order_id", "customer_id", "partner_id", "product_id", "transaction_date", "gross_amount", "currency", "status", "source_row_hash"],
        "sales_invoices": ["invoice_id", "order_id", "posting_date", "gross_amount", "currency"],
        "payment_entries": ["payment_id", "order_id", "posting_date", "payment_value", "currency", "payment_types"],
    }
    for name, rows in staging.items():
        write_csv(output_dir / "erpnext-staging" / f"{name}.csv", rows, staging_fields[name])

    transaction_sources = {
        "order_id": "olist_orders_dataset.order_id",
        "customer_id": "olist_orders_dataset.customer_id",
        "seller_id": "olist_order_items_dataset.seller_id",
        "product_id": "olist_order_items_dataset.product_id",
        "order_status": "olist_orders_dataset.order_status",
        "purchase_timestamp": "olist_orders_dataset.order_purchase_timestamp",
        "approved_at": "olist_orders_dataset.order_approved_at",
        "delivered_at": "olist_orders_dataset.order_delivered_customer_date",
        "payment_value": "sum(olist_order_payments_dataset.payment_value)",
        "payment_types": "set(olist_order_payments_dataset.payment_type)",
        "payment_installments": "max(olist_order_payments_dataset.payment_installments)",
    }
    transaction_derived = {
        "seller_count": "count(distinct seller_id)",
        "product_count": "count(distinct product_id)",
        "item_count": "count(olist_order_items_dataset rows)",
        "item_amount": "sum(olist_order_items_dataset.price)",
        "freight_amount": "sum(olist_order_items_dataset.freight_value)",
        "gross_amount": "item_amount + freight_amount",
        "currency": "dataset country currency assumption: BRL",
        "transaction_provenance": "constant PUBLIC_REAL",
        "source_dataset": "constant olist-brazilian-ecommerce",
        "source_row_hash": "canonical SHA-256 of normalized transaction",
    }
    transaction_dictionary = {
        name: {"source": source, "provenance": "PUBLIC_REAL"}
        for name, source in transaction_sources.items()
    }
    transaction_dictionary.update({
        name: {"source": source, "provenance": "PUBLIC_REAL_DERIVED"}
        for name, source in transaction_derived.items()
    })
    settlement_dictionary = {
        name: {
            "source": (
                "official public fee-rule snapshot"
                if name in {
                    "rule_id", "commission_rate", "fixed_fee",
                    "rule_source_url", "rule_effective_basis",
                }
                else "deterministic counterfactual rule engine"
            ),
            "provenance": (
                "PUBLIC_REAL" if name in {
                    "rule_id", "commission_rate", "fixed_fee",
                    "rule_source_url", "rule_effective_basis",
                }
                else "SYSTEM_GENERATED"
            ),
        }
        for name in settlement_fields
    }
    for name in {
        "actual_rule_id", "actual_commission", "actual_settlement",
        "settlement_status", "scenario_id", "exception_source_type",
    }:
        settlement_dictionary[name] = {
            "source": "fixed-seed controlled settlement generator",
            "provenance": "SYNTHETIC_DOMAIN",
        }
    settlement_dictionary["platform"] = {
        "source": "deterministic assignment of external rule to Olist row",
        "provenance": "SYNTHETIC_DOMAIN",
    }
    dictionary = {
        "schema_version": "1.1",
        "source": "Olist Brazilian E-Commerce Public Dataset",
        "transaction_fields": transaction_dictionary,
        "settlement_fields": settlement_dictionary,
        "risk_case_fields": {
            "identity": ["case_id", "risk_type", "order_id", "seller_id", "rule_id"],
            "amounts": [
                "gross_amount", "expected_commission", "actual_commission",
                "expected_settlement", "actual_settlement", "variance_amount",
            ],
            "provenance": [
                "source_transaction_type", "rule_source_type",
                "rule_mapping_source_type", "settlement_source_type",
                "scenario_source_type",
            ],
            "explainability": ["explanation", "evidence", "severity", "status"],
        },
        "erp_mapping": {
            "mode": dimensions["sampling_quality"]["erp_mapping_mode"],
            "reason": (
                "single-seller/single-product source orders prevent lossy header mapping; "
                "item price and freight are aggregated at order level"
            ),
            "staging_files": staging_fields,
        },
    }
    (output_dir / "data-dictionary.json").write_text(
        json.dumps(dictionary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rule_counts = Counter(item["rule_id"] for item in settlements)
    anomaly_counts = Counter(item["risk_type"] for item in cases)
    summary = {
        "schema_version": "1.0",
        "experiment_id": "revguard-olist-public-rates-20260916",
        "random_seed": SEED,
        "sampling_method": "deterministic_hash_rank_with_cancelled_stratum_for_exception_coverage",
        "sampling_quality": dimensions["sampling_quality"],
        "transaction_count": len(transactions),
        "normal_count": len(transactions) - len(cases),
        "anomaly_count": len(cases),
        "anomaly_rate": str(Decimal(len(cases)) / Decimal(len(transactions))),
        "anomaly_types": dict(sorted(anomaly_counts.items())),
        "rule_count": len(rules),
        "rule_assignments": dict(sorted(rule_counts.items())),
        "total_revenue_audited_brl": str(money(sum(Decimal(item["gross_amount"]) for item in transactions))),
        "expected_commission_brl": str(money(sum(Decimal(item["expected_commission"]) for item in settlements))),
        "actual_commission_brl": str(money(sum(Decimal(item["actual_commission"]) for item in settlements))),
        "revenue_at_risk_brl": str(money(sum(Decimal(item["variance_amount"]) for item in cases))),
        "provenance": {
            "transaction": "PUBLIC_REAL",
            "erp": "PENDING_LIVE_SYSTEM_IMPORT",
            "fee_rules": "PUBLIC_REAL",
            "platform_mapping": "SYNTHETIC_DOMAIN",
            "settlement": "SYNTHETIC_DOMAIN",
            "exceptions": "SYNTHETIC_DOMAIN",
        },
        "counterfactual_notice": rules_document["mapping_boundary"],
        "rule_evaluation_mode": "CURRENT_POLICY_COUNTERFACTUAL",
        "historical_transaction_time_recalculation": False,
        "rule_component_scope": rules_document["calculation_scope"],
        "source_archive_sha256": file_hash(archive_path),
        "rules_sha256": file_hash(rules_path),
        "validation_status": "PASSED",
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    generated = sorted(path for path in output_dir.rglob("*") if path.is_file())
    manifest = {
        "schema_version": "1.0",
        "experiment_id": summary["experiment_id"],
        "files": {
            str(path.relative_to(output_dir)): {
                "bytes": path.stat().st_size,
                "sha256": file_hash(path),
            }
            for path in generated
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sample-size", type=int, default=10_000)
    parser.add_argument("--anomaly-rate", type=Decimal, default=Decimal("0.08"))
    parser.add_argument("--rules", type=Path, default=RULES_PATH)
    args = parser.parse_args()
    summary = run(
        args.archive, args.output_dir, sample_size=args.sample_size,
        anomaly_rate=args.anomaly_rate, rules_path=args.rules,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
