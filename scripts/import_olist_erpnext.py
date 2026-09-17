#!/usr/bin/env python3
"""Import the reproducible Olist experiment into standard ERPNext DocTypes.

This module is copied into the ERPNext container and executed with ``bench``.
It imports all 10,000 sampled transactions as draft Sales Orders, Sales
Invoices and Payment Entries.  Customers, products and sellers are deduplicated
and imported into Customer, Item and Sales Partner.  Draft documents are used
deliberately: the public dataset is evidence for a reproducible experiment and
must not create General Ledger effects that could be mistaken for production
accounting entries.

The source rows remain in the private runtime directory under their original
CC BY-NC-SA 4.0 terms.  Only aggregate counts and hashes are written to the
release evidence bundle.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import frappe

_RUNTIME_DIR = Path(tempfile.gettempdir())
STAGING = Path(os.getenv("REVGUARD_OLIST_STAGING", str(_RUNTIME_DIR / "olist-staging")))
OUTPUT = Path(os.getenv("REVGUARD_OLIST_IMPORT_OUTPUT", str(_RUNTIME_DIR / "olist-import.json")))
DATASET = "olist-brazilian-ecommerce"
PROVENANCE = "PUBLIC_REAL"
COMPANY = "RevGuard Olist Public Lab"
COMPANY_ABBR = "ROL"


def _read(name: str) -> list[dict[str, str]]:
    with (STAGING / f"{name}.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ", timespec="seconds")


def _system(name: str, now: str) -> tuple:
    return (name, now, now, "Administrator", "Administrator", 0, 0)


SYSTEM_FIELDS = [
    "name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
]


def _bulk(doctype: str, fields: list[str], values: list[tuple]) -> int:
    if not values:
        return 0
    frappe.db.bulk_insert(
        doctype, SYSTEM_FIELDS + fields, values,
        ignore_duplicates=True, chunk_size=500,
    )
    return len(values)


def _ensure_master_data() -> dict[str, str]:
    # The canonical fixture seeder owns custom fields and the read-only roles.
    from erpnext import revguard_seed

    revguard_seed._ensure_warehouse_types()
    revguard_seed._ensure_role()
    revguard_seed._ensure_custom_doctype()
    revguard_seed._ensure_custom_fields()
    revguard_seed._ensure_doctype_permissions()
    revguard_seed._ensure_selling_masters()

    if not frappe.db.exists("Company", COMPANY):
        frappe.get_doc({
            "doctype": "Company", "company_name": COMPANY, "abbr": COMPANY_ABBR,
            "country": "Brazil", "default_currency": "BRL",
            "chart_of_accounts": "Standard",
        }).insert(ignore_permissions=True)
    for year in (2016, 2017, 2018):
        name = str(year)
        if not frappe.db.exists("Fiscal Year", name):
            frappe.get_doc({
                "doctype": "Fiscal Year", "year": name,
                "year_start_date": f"{year}-01-01",
                "year_end_date": f"{year}-12-31",
            }).insert(ignore_permissions=True)
    frappe.db.set_value("Currency", "BRL", "enabled", 1)

    if not frappe.db.exists("Customer Group", "Olist Public Customers"):
        frappe.get_doc({
            "doctype": "Customer Group",
            "customer_group_name": "Olist Public Customers",
            "parent_customer_group": "All Customer Groups", "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", "Brazil"):
        frappe.get_doc({
            "doctype": "Territory", "territory_name": "Brazil",
            "parent_territory": "All Territories", "is_group": 0,
        }).insert(ignore_permissions=True)
    if not frappe.db.exists("Price List", "Olist BRL"):
        frappe.get_doc({
            "doctype": "Price List", "price_list_name": "Olist BRL",
            "currency": "BRL", "selling": 1, "buying": 0, "enabled": 1,
        }).insert(ignore_permissions=True)
    frappe.db.commit()

    receivable = revguard_seed._receivable_account(COMPANY)
    cash = revguard_seed._cash_account(COMPANY)
    income = frappe.db.get_value(
        "Account",
        {"company": COMPANY, "root_type": "Income", "is_group": 0},
        "name",
    )
    if not income:
        raise RuntimeError("Olist company has no leaf income account")
    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    if not item_group:
        item_group = "All Item Groups"
    return {
        "receivable": receivable, "cash": cash,
        "income": income, "item_group": item_group,
    }


def _money(raw: str) -> str:
    return str(Decimal(raw or "0").quantize(Decimal("0.01")))


def main() -> dict:
    required = {
        "customers.csv", "items.csv", "partners.csv", "sales_orders.csv",
        "sales_invoices.csv", "payment_entries.csv",
    }
    missing = required - {path.name for path in STAGING.glob("*.csv")}
    if missing:
        raise RuntimeError("Olist staging files missing: " + ", ".join(sorted(missing)))

    frappe.flags.ignore_permissions = True
    masters = _ensure_master_data()
    now = _now()
    customers = _read("customers")
    items = _read("items")
    partners = _read("partners")
    orders = _read("sales_orders")
    invoices = _read("sales_invoices")
    payments = _read("payment_entries")
    if not (len(orders) == len(invoices) == len(payments) == 10_000):
        raise RuntimeError("the finals experiment requires exactly 10,000 staged transactions")

    customer_name = {row["customer_id"]: "OLIST-CUST-" + row["customer_id"] for row in customers}
    item_name = {row["product_id"]: "OLIST-ITEM-" + row["product_id"] for row in items}
    partner_name = {row["partner_id"]: "OLIST-SELLER-" + row["partner_id"] for row in partners}
    order_by_id = {row["order_id"]: row for row in orders}

    inserted: dict[str, int] = {}
    inserted["Customer"] = _bulk("Customer", [
        "customer_name", "customer_type", "customer_group", "territory",
        "custom_revguard_source_id", "custom_revguard_source_dataset",
        "custom_revguard_provenance_kind",
    ], [
        _system(customer_name[row["customer_id"]], now) + (
            row["customer_name"], "Individual", "Olist Public Customers", "Brazil",
            row["customer_id"], DATASET, row["source_provenance"],
        ) for row in customers
    ])
    inserted["Item"] = _bulk("Item", [
        "item_code", "item_name", "description", "item_group", "stock_uom",
        "is_stock_item", "is_sales_item", "custom_revguard_source_id",
        "custom_revguard_source_dataset", "custom_revguard_provenance_kind",
    ], [
        _system(item_name[row["product_id"]], now) + (
            item_name[row["product_id"]], row["item_name"],
            row.get("product_category") or "Olist public product", masters["item_group"],
            "Nos", 0, 1, row["product_id"], DATASET, row["source_provenance"],
        ) for row in items
    ])
    inserted["Sales Partner"] = _bulk("Sales Partner", [
        "partner_name", "territory", "commission_rate",
        "custom_revguard_partner_id", "custom_revguard_region",
        "custom_revguard_source_id", "custom_revguard_source_dataset",
        "custom_revguard_provenance_kind",
    ], [
        _system(partner_name[row["partner_id"]], now) + (
            row["partner_name"], "Brazil", 0, row["partner_id"],
            f"{row.get('state', '')}/{row.get('city', '')}", row["partner_id"],
            DATASET, row["source_provenance"],
        ) for row in partners
    ])

    order_values: list[tuple] = []
    order_item_values: list[tuple] = []
    for row in orders:
        name = "OLIST-SO-" + row["order_id"]
        amount = _money(row["gross_amount"])
        customer = customer_name[row["customer_id"]]
        product = item_name[row["product_id"]]
        partner = partner_name[row["partner_id"]]
        tx_date = (row.get("transaction_date") or "2018-01-01")[:10]
        order_values.append(_system(name, now) + (
            "OLIST-.#####", customer, row["customer_id"], COMPANY, tx_date, tx_date,
            "BRL", 1, "Olist BRL", "BRL", 1, "Sales", "Draft",
            amount, amount, amount, amount, amount, amount, partner, 0, 0,
            row["order_id"], row["partner_id"], row["product_id"], row["status"],
            DATASET, PROVENANCE, row["source_row_hash"],
        ))
        child = "OLIST-SOI-" + row["order_id"]
        order_item_values.append(_system(child, now) + (
            name, "items", "Sales Order", product, product, product, tx_date,
            "Nos", "Nos", 1, 1, amount, amount, amount, amount, amount, amount,
        ))
    inserted["Sales Order"] = _bulk("Sales Order", [
        "naming_series", "customer", "customer_name", "company", "transaction_date",
        "delivery_date", "currency", "conversion_rate", "selling_price_list",
        "price_list_currency", "plc_conversion_rate", "order_type", "status",
        "net_total", "grand_total", "base_net_total", "base_grand_total",
        "rounded_total", "base_rounded_total", "sales_partner", "commission_rate",
        "total_commission", "custom_revguard_order_id", "custom_revguard_partner_id",
        "custom_revguard_product_id", "custom_revguard_order_status",
        "custom_revguard_source_dataset", "custom_revguard_provenance_kind",
        "custom_revguard_source_hash",
    ], order_values)
    inserted["Sales Order Item"] = _bulk("Sales Order Item", [
        "parent", "parentfield", "parenttype", "item_code", "item_name", "description",
        "delivery_date", "stock_uom", "uom", "conversion_factor", "qty", "rate",
        "amount", "base_rate", "base_amount", "net_rate", "net_amount",
    ], order_item_values)

    invoice_values: list[tuple] = []
    invoice_item_values: list[tuple] = []
    for row in invoices:
        order = order_by_id[row["order_id"]]
        name = "OLIST-INV-" + row["order_id"]
        amount = _money(row["gross_amount"])
        product = item_name[order["product_id"]]
        posting = (row.get("posting_date") or order["transaction_date"] or "2018-01-01")[:10]
        invoice_values.append(_system(name, now) + (
            "ACC-SINV-.YYYY.-", customer_name[order["customer_id"]], COMPANY, posting,
            posting, "BRL", 1, "Olist BRL", "BRL", 1, masters["receivable"],
            0, 0, "Draft", amount, amount, amount, amount, amount, amount,
            row["order_id"], row["invoice_id"], DATASET, PROVENANCE,
        ))
        invoice_item_values.append(_system("OLIST-SII-" + row["order_id"], now) + (
            name, "items", "Sales Invoice", product, product, product, "Nos", "Nos",
            1, 1, amount, amount, amount, amount, amount, amount, masters["income"],
        ))
    inserted["Sales Invoice"] = _bulk("Sales Invoice", [
        "naming_series", "customer", "company", "posting_date", "due_date",
        "currency", "conversion_rate", "selling_price_list", "price_list_currency",
        "plc_conversion_rate", "debit_to", "is_pos", "is_return", "status",
        "net_total", "grand_total", "base_net_total", "base_grand_total",
        "rounded_total", "base_rounded_total", "custom_revguard_order_id",
        "custom_revguard_invoice_id", "custom_revguard_source_dataset",
        "custom_revguard_provenance_kind",
    ], invoice_values)
    inserted["Sales Invoice Item"] = _bulk("Sales Invoice Item", [
        "parent", "parentfield", "parenttype", "item_code", "item_name", "description",
        "stock_uom", "uom", "conversion_factor", "qty", "rate", "amount",
        "base_rate", "base_amount", "net_rate", "net_amount", "income_account",
    ], invoice_item_values)

    payment_values: list[tuple] = []
    for row in payments:
        order = order_by_id[row["order_id"]]
        amount = _money(row["payment_value"])
        posting = (row.get("posting_date") or order["transaction_date"] or "2018-01-01")[:10]
        payment_values.append(_system("OLIST-PAY-" + row["order_id"], now) + (
            "ACC-PAY-.YYYY.-", "Receive", COMPANY, posting, "Customer",
            customer_name[order["customer_id"]], masters["receivable"], masters["cash"],
            "BRL", "BRL", amount, amount, amount, amount, 1, 1, amount, "Draft",
            row["order_id"], row["payment_id"], "SYNTHETIC_EXPERIMENT_DRAFT",
            DATASET, PROVENANCE,
        ))
    inserted["Payment Entry"] = _bulk("Payment Entry", [
        "naming_series", "payment_type", "company", "posting_date", "party_type",
        "party", "paid_from", "paid_to", "paid_from_account_currency",
        "paid_to_account_currency", "paid_amount", "received_amount",
        "base_paid_amount", "base_received_amount", "source_exchange_rate",
        "target_exchange_rate", "unallocated_amount", "status",
        "custom_revguard_order_id", "custom_revguard_payment_id",
        "custom_revguard_payment_status", "custom_revguard_source_dataset",
        "custom_revguard_provenance_kind",
    ], payment_values)
    frappe.db.commit()
    frappe.clear_cache()

    observed = {
        doctype: frappe.db.count(
            doctype, {"custom_revguard_source_dataset": DATASET}
        )
        for doctype in (
            "Customer", "Item", "Sales Partner", "Sales Order",
            "Sales Invoice", "Payment Entry",
        )
    }
    if observed["Sales Order"] != 10_000:
        raise RuntimeError(f"ERPNext Olist Sales Order count is {observed['Sales Order']}")
    mapping = "\n".join(
        f"{row['order_id']},OLIST-SO-{row['order_id']}" for row in orders
    ).encode()
    result = {
        "schema_version": "1.0",
        "dataset": DATASET,
        "import_method": "frappe.db.bulk_insert",
        "document_state": "DRAFT_NO_GL_EFFECT",
        "source_provenance": PROVENANCE,
        "transaction_count": len(orders),
        "observed_counts": observed,
        "attempted_rows": inserted,
        "mapping_sha256": hashlib.sha256(mapping).hexdigest(),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "validation_status": "PASSED",
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OUTPUT.chmod(0o600)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return result


if __name__ == "__main__":
    main()
