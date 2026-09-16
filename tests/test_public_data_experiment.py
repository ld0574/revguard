from __future__ import annotations

import csv
import io
import json
import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path

from scripts.run_public_data_experiment import ANOMALY_TYPES, run


def csv_bytes(fields: list[str], rows: list[dict]) -> bytes:
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode()


def fixture_archive(path: Path, count: int = 40) -> None:
    orders = []
    items = []
    payments = []
    customers = []
    sellers = []
    products = []
    for index in range(count):
        order_id = f"ORDER-{index:04d}"
        customer_id = f"CUSTOMER-{index:04d}"
        seller_id = f"SELLER-{index % 8:02d}"
        product_id = f"PRODUCT-{index % 12:02d}"
        orders.append({
            "order_id": order_id,
            "customer_id": customer_id,
            "order_status": "canceled" if index < 10 else "delivered",
            "order_purchase_timestamp": "2018-01-01 10:00:00",
            "order_approved_at": "2018-01-01 10:05:00",
            "order_delivered_customer_date": "" if index < 10 else "2018-01-05 12:00:00",
        })
        items.append({
            "order_id": order_id,
            "price": str(100 + index),
            "freight_value": "10.00",
            "seller_id": seller_id,
            "product_id": product_id,
        })
        payments.append({
            "order_id": order_id,
            "payment_value": str(110 + index),
            "payment_type": "credit_card",
            "payment_installments": "1",
        })
        customers.append({
            "customer_id": customer_id, "customer_city": "sao paulo", "customer_state": "SP",
        })
    for index in range(8):
        sellers.append({
            "seller_id": f"SELLER-{index:02d}", "seller_city": "sao paulo", "seller_state": "SP",
        })
    for index in range(12):
        products.append({
            "product_id": f"PRODUCT-{index:02d}", "product_category_name": "test_category",
        })

    members = {
        "olist_orders_dataset.csv": csv_bytes(list(orders[0]), orders),
        "olist_order_items_dataset.csv": csv_bytes(list(items[0]), items),
        "olist_order_payments_dataset.csv": csv_bytes(list(payments[0]), payments),
        "olist_customers_dataset.csv": csv_bytes(list(customers[0]), customers),
        "olist_sellers_dataset.csv": csv_bytes(list(sellers[0]), sellers),
        "olist_products_dataset.csv": csv_bytes(list(products[0]), products),
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in sorted(members.items()):
            archive.writestr(name, payload)


class TestPublicDataExperiment(unittest.TestCase):
    def test_fixed_seed_produces_traceable_diverse_exceptions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "olist.zip"
            fixture_archive(archive)
            first = root / "first"
            second = root / "second"
            summary = run(
                archive, first, sample_size=20, anomaly_rate=Decimal("0.50")
            )
            repeated = run(
                archive, second, sample_size=20, anomaly_rate=Decimal("0.50")
            )

            self.assertEqual(summary, repeated)
            self.assertEqual(summary["transaction_count"], 20)
            self.assertEqual(summary["anomaly_count"], 10)
            self.assertEqual(set(summary["anomaly_types"]), set(ANOMALY_TYPES))
            self.assertGreaterEqual(summary["rule_count"], 5)
            self.assertEqual(
                (first / "risk-cases.jsonl").read_bytes(),
                (second / "risk-cases.jsonl").read_bytes(),
            )
            cases = [
                json.loads(line)
                for line in (first / "risk-cases.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertTrue(all(Decimal(item["variance_amount"]) > 0 for item in cases))
            self.assertTrue(all(len(item["evidence"]) == 3 for item in cases))
            self.assertTrue(all(
                item["rule_mapping_source_type"] == "SYNTHETIC_DOMAIN" for item in cases
            ))
            with (first / "transactions.csv").open(encoding="utf-8") as handle:
                transactions = list(csv.DictReader(handle))
            self.assertTrue(all(item["order_id"].startswith("ORDER-") for item in transactions))
            self.assertTrue((first / "erpnext-staging" / "sales_orders.csv").exists())


if __name__ == "__main__":
    unittest.main()
