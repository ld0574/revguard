from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib import error

from revguard.adapters import (
    EnterpriseAdapterError,
    ERPNextAdapter,
    ERPNextSettings,
    ProviderRegistry,
)
from revguard.mocks import ToolGateway

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, *_args):
        return json.dumps(self.payload).encode()


def settings(**overrides) -> ERPNextSettings:
    values = {
        "base_url": "https://erpnext.example.test",
        "api_key": "readonly-key",
        "api_secret": "readonly-secret",
        "timeout_seconds": 1,
        "page_size": 50,
        "max_pages": 2,
    }
    values.update(overrides)
    return ERPNextSettings(**values)


class TestERPNextAdapter(unittest.TestCase):
    def test_order_mapping_and_live_receipt(self):
        record = {
            "name": "SAL-ORD-0001",
            "modified": "2026-09-16 12:00:00.000000",
            "transaction_date": "2026-07-10",
            "grand_total": 180000,
            "currency": "KES",
            "status": "Completed",
            "sales_partner": "Nairobi Solar Solutions Ltd",
            "commission_rate": 10,
            "total_commission": 18000,
            "custom_revguard_order_id": "EZ202608001",
            "custom_revguard_partner_id": "AGT-10001",
            "custom_revguard_product_id": "SHS-500",
            "custom_revguard_order_status": "COMPLETED",
            "custom_revguard_completed_date": "2026-07-12",
            "custom_revguard_sales_owner": "j.mwangi",
        }
        seen = {}

        def open_request(req, timeout):
            seen["request"] = req
            seen["timeout"] = timeout
            return FakeResponse({"data": [record]})

        with patch("revguard.adapters.request.urlopen", side_effect=open_request):
            result = ERPNextAdapter(settings()).call(
                "crm.get_order", {"order_id": "EZ202608001"}
            )

        self.assertEqual(result.data["order_amount"], 180000)
        self.assertEqual(result.data["domain_data_kind"], "SYNTHETIC_DOMAIN")
        self.assertEqual(result.provider, "erpnext")
        self.assertEqual(result.provenance_kind, "LIVE_SYSTEM")
        self.assertEqual(result.external_document_type, "Sales Order")
        self.assertEqual(result.external_document_id, "SAL-ORD-0001")
        self.assertTrue(result.payload_hash.startswith("sha256:"))
        self.assertIn("/app/sales-order/SAL-ORD-0001", result.record_url)
        self.assertEqual(seen["timeout"], 1)
        self.assertEqual(
            seen["request"].get_header("Authorization"),
            "token readonly-key:readonly-secret",
        )
        self.assertNotIn("readonly-secret", json.dumps(result.metadata()))

    def test_pagination_is_bounded_and_combined(self):
        page = {
            "name": "SAL-ORD-0001",
            "modified": "2026-09-16 12:00:00.000000",
            "transaction_date": "2026-07-10",
            "grand_total": 10,
            "currency": "KES",
            "custom_revguard_order_id": "E1",
            "custom_revguard_partner_id": "P1",
        }
        responses = [FakeResponse({"data": [page]}), FakeResponse({"data": []})]
        with patch("revguard.adapters.request.urlopen", side_effect=responses) as opened:
            result = ERPNextAdapter(settings(page_size=1)).call(
                "crm.list_orders_by_partner", {"partner_id": "P1"}
            )
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(opened.call_count, 2)

    def test_auth_timeout_and_schema_errors_have_stable_semantics(self):
        adapter = ERPNextAdapter(settings())
        http_error = error.HTTPError(
            "https://erpnext.example.test", 403, "forbidden", {}, io.BytesIO(b"{}")
        )
        with patch("revguard.adapters.request.urlopen", side_effect=http_error):
            with self.assertRaisesRegex(EnterpriseAdapterError, "ERPNext HTTP 403") as ctx:
                adapter.call("crm.get_order", {"order_id": "E1"})
        self.assertEqual(ctx.exception.error_type, "AUTH_FAILED")

        with patch("revguard.adapters.request.urlopen", return_value=FakeResponse({"data": [{
            "name": "BROKEN", "modified": "2026-09-16 12:00:00",
        }]})):
            with self.assertRaises(EnterpriseAdapterError) as ctx:
                adapter.call("crm.get_order", {"order_id": "E1"})
        self.assertEqual(ctx.exception.error_type, "DATA_CONFLICT")


class FailingAdapter:
    provider = "erpnext"

    def call(self, _tool_name: str, _parameters: dict):
        raise EnterpriseAdapterError("TOOL_UNAVAILABLE", "ERPNext unavailable")


class TestProviderRouting(unittest.TestCase):
    def test_erpnext_failure_never_falls_back_to_fixture(self):
        gateway = ToolGateway(
            FIXTURES,
            provider_registry=ProviderRegistry("erpnext", FailingAdapter()),
        )
        response = gateway.call(
            "crm.get_order",
            {"order_id": "EZ202608001"},
            actor="revguard-evidence",
            scope=["order:read"],
        )
        self.assertFalse(response["success"])
        self.assertEqual(response["provider"], "erpnext")
        self.assertEqual(response["provenance_kind"], "LIVE_SYSTEM")
        self.assertEqual(response["error"]["type"], "TOOL_UNAVAILABLE")

    def test_default_mock_receipt_is_explicitly_synthetic(self):
        gateway = ToolGateway(
            FIXTURES, provider_registry=ProviderRegistry("mock")
        )
        response = gateway.call(
            "crm.get_order",
            {"order_id": "EZ202608001"},
            actor="revguard-evidence",
            scope=["order:read"],
        )
        self.assertTrue(response["success"])
        self.assertEqual(response["provider"], "mock")
        self.assertEqual(response["provenance_kind"], "SYNTHETIC_DOMAIN")
        self.assertTrue(response["payload_hash"].startswith("sha256:"))


if __name__ == "__main__":
    unittest.main()
