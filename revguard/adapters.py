"""Enterprise read adapters and provenance-aware provider routing.

RevGuard keeps policy and money effects in its deterministic control plane.  CRM,
contract and finance facts can be supplied either by the reproducible fixture
provider or by a live ERPNext site.  Selecting ERPNext is fail-closed: an HTTP,
authentication or schema error is returned as an Evidence Gap by the evidence
skill and is never retried against fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock
from typing import ClassVar, Protocol
from urllib import error, parse, request

ENTERPRISE_QUERY_TOOLS = frozenset({
    "crm.get_order",
    "crm.get_partner",
    "crm.get_partner_tier_history",
    "crm.list_orders_by_partner",
    "contract.get_contract",
    "contract.get_effective_terms",
    "finance.get_invoice",
    "finance.get_payment",
    "finance.get_refund",
})


class EnterpriseAdapterError(RuntimeError):
    """Stable adapter failure consumed by the ToolGateway error contract."""

    def __init__(self, error_type: str, message: str):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


@dataclass(frozen=True)
class AdapterResult:
    data: dict
    provider: str
    external_document_type: str
    external_document_id: str
    source_timestamp: str
    http_status: int
    latency_ms: int
    payload_hash: str
    correlation_id: str
    record_url: str
    provenance_kind: str = "LIVE_SYSTEM"

    def metadata(self) -> dict:
        return {
            "provider": self.provider,
            "external_document_type": self.external_document_type,
            "external_document_id": self.external_document_id,
            "source_timestamp": self.source_timestamp,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "payload_hash": self.payload_hash,
            "correlation_id": self.correlation_id,
            "record_url": self.record_url,
            "provenance_kind": self.provenance_kind,
        }


class EnterpriseAdapter(Protocol):
    provider: str

    def call(self, tool_name: str, parameters: dict) -> AdapterResult: ...


def _read_secret(name: str) -> str:
    file_name = os.getenv(name + "_FILE", "").strip()
    if file_name:
        try:
            return Path(file_name).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise RuntimeError(f"无法读取 {name}_FILE") from exc
    return os.getenv(name, "").strip()


@dataclass(frozen=True)
class ERPNextSettings:
    base_url: str
    api_key: str
    api_secret: str
    site_name: str = ""
    timeout_seconds: float = 8.0
    page_size: int = 50
    max_pages: int = 20

    @classmethod
    def from_env(cls) -> ERPNextSettings:
        settings = cls(
            base_url=os.getenv("REVGUARD_ERPNEXT_BASE_URL", "").strip().rstrip("/"),
            api_key=_read_secret("REVGUARD_ERPNEXT_API_KEY"),
            api_secret=_read_secret("REVGUARD_ERPNEXT_API_SECRET"),
            site_name=os.getenv("REVGUARD_ERPNEXT_SITE_NAME", "").strip(),
            timeout_seconds=float(os.getenv("REVGUARD_ERPNEXT_TIMEOUT_SECONDS", "8")),
            page_size=int(os.getenv("REVGUARD_ERPNEXT_PAGE_SIZE", "50")),
            max_pages=int(os.getenv("REVGUARD_ERPNEXT_MAX_PAGES", "20")),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise RuntimeError("REVGUARD_ERPNEXT_BASE_URL 必须是 HTTP(S) 地址")
        if not self.api_key or not self.api_secret:
            raise RuntimeError("ERPNext Provider 需要只读 API Key 和 API Secret")
        if not 0 < self.timeout_seconds <= 60:
            raise RuntimeError("REVGUARD_ERPNEXT_TIMEOUT_SECONDS 必须在 0 到 60 秒之间")
        if not 1 <= self.page_size <= 500 or not 1 <= self.max_pages <= 100:
            raise RuntimeError("ERPNext 分页配置超出安全范围")


class ERPNextAdapter:
    """Frappe REST adapter that maps ERPNext records to RevGuard's stable schema."""

    provider = "erpnext"

    _FIELDS: ClassVar[dict[str, list[str]]] = {
        "Sales Order": [
            "name", "modified", "transaction_date", "grand_total", "currency", "status",
            "customer", "sales_partner", "commission_rate", "total_commission",
            "custom_revguard_order_id", "custom_revguard_partner_id",
            "custom_revguard_product_id", "custom_revguard_order_status",
            "custom_revguard_completed_date", "custom_revguard_sales_owner",
        ],
        "Sales Partner": [
            "name", "modified", "partner_name", "commission_rate",
            "custom_revguard_partner_id", "custom_revguard_region",
        ],
        "Partner Tier History": [
            "name", "modified", "partner_id", "tier", "effective_from",
        ],
        "Contract": [
            "name", "modified", "party_type", "party_name", "start_date", "end_date",
            "status", "custom_revguard_contract_id", "custom_revguard_partner_id",
            "custom_revguard_policy_id", "custom_revguard_terms_json",
        ],
        "Sales Invoice": [
            "name", "modified", "posting_date", "grand_total", "currency", "status",
            "is_return", "return_against", "custom_revguard_order_id",
            "custom_revguard_invoice_id", "custom_revguard_refund_id",
            "custom_revguard_refund_reason",
        ],
        "Payment Entry": [
            "name", "modified", "posting_date", "paid_amount", "received_amount",
            "paid_to_account_currency", "status", "custom_revguard_order_id",
            "custom_revguard_payment_id", "custom_revguard_payment_status",
        ],
    }

    def __init__(self, settings: ERPNextSettings):
        settings.validate()
        self.settings = settings

    @staticmethod
    def _correlation_id() -> str:
        import uuid

        return "ERP-" + uuid.uuid4().hex[:16].upper()

    def _request_json(self, path: str, query: dict, correlation_id: str) -> tuple[dict, int, int]:
        encoded = parse.urlencode(query)
        url = f"{self.settings.base_url}{path}"
        if encoded:
            url += "?" + encoded
        headers = {
            "Accept": "application/json",
            "Authorization": f"token {self.settings.api_key}:{self.settings.api_secret}",
            "X-RevGuard-Correlation-ID": correlation_id,
        }
        if self.settings.site_name:
            headers["X-Frappe-Site-Name"] = self.settings.site_name
            headers["Host"] = self.settings.site_name
        started = time.monotonic()
        req = request.Request(url, method="GET", headers=headers)
        try:
            with request.urlopen(req, timeout=self.settings.timeout_seconds) as response:  # nosec B310
                status = response.status
                payload = json.load(response)
        except error.HTTPError as exc:
            status = exc.code
            if status in {401, 403}:
                kind = "AUTH_FAILED"
            elif status == 404:
                kind = "NOT_FOUND"
            elif status == 429:
                kind = "RATE_LIMITED"
            elif status >= 500:
                kind = "TOOL_UNAVAILABLE"
            else:
                kind = "DATA_CONFLICT"
            raise EnterpriseAdapterError(kind, f"ERPNext HTTP {status}") from exc
        except TimeoutError as exc:
            raise EnterpriseAdapterError("TIMEOUT", "ERPNext 请求超时") from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise EnterpriseAdapterError(
                "TOOL_UNAVAILABLE", f"ERPNext 请求失败: {type(exc).__name__}"
            ) from exc
        latency_ms = max(0, int((time.monotonic() - started) * 1000))
        if not isinstance(payload, dict):
            raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext 返回格式不是对象")
        return payload, status, latency_ms

    def _list(
        self,
        doctype: str,
        *,
        filters: list,
        correlation_id: str,
    ) -> tuple[list[dict], int, int]:
        records: list[dict] = []
        status = 0
        latency_ms = 0
        for page in range(self.settings.max_pages):
            payload, status, elapsed = self._request_json(
                "/api/resource/" + parse.quote(doctype, safe=""),
                {
                    "filters": json.dumps(filters, separators=(",", ":")),
                    "fields": json.dumps(self._FIELDS[doctype], separators=(",", ":")),
                    "limit_start": page * self.settings.page_size,
                    "limit_page_length": self.settings.page_size,
                    "order_by": "modified asc,name asc",
                },
                correlation_id,
            )
            latency_ms += elapsed
            page_records = payload.get("data")
            if not isinstance(page_records, list) or not all(
                isinstance(item, dict) for item in page_records
            ):
                raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext data 列表格式异常")
            records.extend(page_records)
            if len(page_records) < self.settings.page_size:
                return records, status, latency_ms
        raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext 分页超过配置上限")

    @staticmethod
    def _required(record: dict, *fields: str) -> None:
        missing = [field for field in fields if record.get(field) in (None, "")]
        if missing:
            raise EnterpriseAdapterError(
                "DATA_CONFLICT", "ERPNext Schema 缺少字段: " + ", ".join(missing)
            )

    def _one(
        self,
        doctype: str,
        filters: list,
        correlation_id: str,
    ) -> tuple[dict, int, int]:
        records, status, latency_ms = self._list(
            doctype, filters=filters, correlation_id=correlation_id
        )
        if not records:
            raise EnterpriseAdapterError("NOT_FOUND", f"ERPNext {doctype} 记录不存在")
        if len(records) > 1:
            raise EnterpriseAdapterError("DATA_CONFLICT", f"ERPNext {doctype} 记录不唯一")
        return records[0], status, latency_ms

    @staticmethod
    def _terms(record: dict) -> dict:
        raw = record.get("custom_revguard_terms_json") or "{}"
        if isinstance(raw, dict):
            return raw
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError) as exc:
            raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext 合同条款 JSON 无效") from exc
        if not isinstance(value, dict):
            raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext 合同条款不是对象")
        return value

    @staticmethod
    def _order(record: dict) -> dict:
        ERPNextAdapter._required(
            record, "custom_revguard_order_id", "custom_revguard_partner_id",
            "transaction_date", "grand_total", "currency",
        )
        return {
            "order_id": record["custom_revguard_order_id"],
            "partner_id": record["custom_revguard_partner_id"],
            "product_id": record.get("custom_revguard_product_id"),
            "order_amount": record["grand_total"],
            "currency": record["currency"],
            "order_status": record.get("custom_revguard_order_status") or record.get("status"),
            "order_date": record["transaction_date"],
            "completed_date": record.get("custom_revguard_completed_date"),
            "sales_owner": record.get("custom_revguard_sales_owner"),
            "erpnext_sales_partner": record.get("sales_partner"),
            "observed_commission_rate": record.get("commission_rate"),
            "observed_commission_amount": record.get("total_commission"),
            "domain_data_kind": "SYNTHETIC_DOMAIN",
        }

    @staticmethod
    def _record_url(base_url: str, doctype: str, document_id: str) -> str:
        slug = doctype.lower().replace(" ", "-")
        return f"{base_url}/app/{parse.quote(slug)}/{parse.quote(document_id, safe='')}"

    @staticmethod
    def _payload_hash(data: dict) -> str:
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    def call(self, tool_name: str, parameters: dict) -> AdapterResult:
        correlation_id = self._correlation_id()
        doctype = ""
        document_id = ""
        modified = ""
        status = 200
        latency_ms = 0

        if tool_name == "crm.get_order":
            doctype = "Sales Order"
            record, status, latency_ms = self._one(
                doctype,
                [["custom_revguard_order_id", "=", parameters.get("order_id")]],
                correlation_id,
            )
            data = self._order(record)
            document_id = record["name"]
            modified = str(record.get("modified") or "")
        elif tool_name == "crm.list_orders_by_partner":
            doctype = "Sales Order"
            records, status, latency_ms = self._list(
                doctype,
                filters=[["custom_revguard_partner_id", "=", parameters.get("partner_id")]],
                correlation_id=correlation_id,
            )
            data = {"orders": [self._order(item) for item in records], "count": len(records)}
            document_id = ",".join(str(item.get("name")) for item in records)
            modified = max((str(item.get("modified") or "") for item in records), default="")
        elif tool_name == "crm.get_partner":
            doctype = "Sales Partner"
            if parameters.get("partner_id"):
                filters = [["custom_revguard_partner_id", "=", parameters["partner_id"]]]
            else:
                filters = [["partner_name", "=", parameters.get("partner_name")]]
            record, status, latency_ms = self._one(doctype, filters, correlation_id)
            self._required(record, "custom_revguard_partner_id", "partner_name")
            data = {
                "partner_id": record["custom_revguard_partner_id"],
                "name": record["partner_name"],
                "region": record.get("custom_revguard_region"),
                "commission_rate": record.get("commission_rate"),
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            document_id = record["name"]
            modified = str(record.get("modified") or "")
        elif tool_name == "crm.get_partner_tier_history":
            doctype = "Partner Tier History"
            records, status, latency_ms = self._list(
                doctype,
                filters=[["partner_id", "=", parameters.get("partner_id")]],
                correlation_id=correlation_id,
            )
            for item in records:
                self._required(item, "partner_id", "tier", "effective_from")
            data = {
                "partner_id": parameters.get("partner_id"),
                "tier_history": [
                    {"tier": item["tier"], "effective_from": item["effective_from"]}
                    for item in records
                ],
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            document_id = ",".join(str(item.get("name")) for item in records)
            modified = max((str(item.get("modified") or "") for item in records), default="")
        elif tool_name in {"contract.get_contract", "contract.get_effective_terms"}:
            doctype = "Contract"
            if parameters.get("contract_id"):
                filters = [["custom_revguard_contract_id", "=", parameters["contract_id"]]]
            else:
                filters = [["custom_revguard_partner_id", "=", parameters.get("partner_id")]]
            record, status, latency_ms = self._one(doctype, filters, correlation_id)
            self._required(
                record, "custom_revguard_contract_id", "custom_revguard_partner_id",
                "custom_revguard_policy_id",
            )
            terms = self._terms(record)
            contract = {
                "contract_id": record["custom_revguard_contract_id"],
                "partner_id": record["custom_revguard_partner_id"],
                "signed_date": record.get("start_date"),
                "policy_id": record["custom_revguard_policy_id"],
                "terms": terms,
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            data = (
                {"contract_id": contract["contract_id"], "terms": terms,
                 "domain_data_kind": "SYNTHETIC_DOMAIN"}
                if tool_name == "contract.get_effective_terms" else contract
            )
            document_id = record["name"]
            modified = str(record.get("modified") or "")
        elif tool_name == "finance.get_invoice":
            doctype = "Sales Invoice"
            record, status, latency_ms = self._one(
                doctype,
                [["custom_revguard_order_id", "=", parameters.get("order_id")],
                 ["is_return", "=", 0]],
                correlation_id,
            )
            self._required(record, "custom_revguard_order_id", "grand_total", "currency")
            data = {
                "invoice_id": record.get("custom_revguard_invoice_id") or record["name"],
                "order_id": record["custom_revguard_order_id"],
                "invoice_amount": record["grand_total"],
                "currency": record["currency"],
                "invoice_status": record.get("status"),
                "invoice_date": record.get("posting_date"),
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            document_id = record["name"]
            modified = str(record.get("modified") or "")
        elif tool_name == "finance.get_payment":
            doctype = "Payment Entry"
            record, status, latency_ms = self._one(
                doctype,
                [["custom_revguard_order_id", "=", parameters.get("order_id")]],
                correlation_id,
            )
            self._required(record, "custom_revguard_order_id", "posting_date")
            amount = record.get("received_amount") or record.get("paid_amount")
            currency = record.get("paid_to_account_currency")
            if amount in (None, "") or not currency:
                raise EnterpriseAdapterError("DATA_CONFLICT", "ERPNext 回款金额或币种缺失")
            data = {
                "payment_id": record.get("custom_revguard_payment_id") or record["name"],
                "order_id": record["custom_revguard_order_id"],
                "payment_amount": amount,
                "currency": currency,
                "payment_status": record.get("custom_revguard_payment_status") or record.get("status"),
                "payment_date": record["posting_date"],
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            document_id = record["name"]
            modified = str(record.get("modified") or "")
        elif tool_name == "finance.get_refund":
            doctype = "Sales Invoice"
            records, status, latency_ms = self._list(
                doctype,
                filters=[
                    ["custom_revguard_order_id", "=", parameters.get("order_id")],
                    ["is_return", "=", 1],
                ],
                correlation_id=correlation_id,
            )
            refunds = []
            total = Decimal("0")
            for item in records:
                self._required(item, "custom_revguard_order_id", "grand_total", "currency")
                amount = abs(Decimal(str(item["grand_total"])))
                total += amount
                refunds.append({
                    "refund_id": item.get("custom_revguard_refund_id") or item["name"],
                    "order_id": item["custom_revguard_order_id"],
                    "refund_amount": str(amount),
                    "currency": item["currency"],
                    "refund_date": item.get("posting_date"),
                    "reason": item.get("custom_revguard_refund_reason") or "RETURN_INVOICE",
                    "domain_data_kind": "SYNTHETIC_DOMAIN",
                })
            data = {
                "refunds": refunds,
                "refund_amount": str(total),
                "domain_data_kind": "SYNTHETIC_DOMAIN",
            }
            document_id = ",".join(str(item.get("name")) for item in records)
            modified = max((str(item.get("modified") or "") for item in records), default="")
        else:
            raise EnterpriseAdapterError("NOT_FOUND", f"ERPNext 不支持工具 {tool_name}")

        return AdapterResult(
            data=data,
            provider=self.provider,
            external_document_type=doctype,
            external_document_id=document_id,
            source_timestamp=modified or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            http_status=status,
            latency_ms=latency_ms,
            payload_hash=self._payload_hash(data),
            correlation_id=correlation_id,
            record_url=(
                self._record_url(self.settings.base_url, doctype, document_id)
                if document_id and "," not in document_id else self.settings.base_url + "/app"
            ),
        )


class ProviderRegistry:
    """Route stable RevGuard tool names to one explicitly selected provider."""

    def __init__(self, enterprise_provider: str = "mock", adapter: EnterpriseAdapter | None = None):
        provider = enterprise_provider.strip().lower()
        if provider not in {"mock", "erpnext"}:
            raise RuntimeError(f"不支持的企业 Provider: {enterprise_provider}")
        if provider == "erpnext" and adapter is None:
            raise RuntimeError("ERPNext Provider 未配置 Adapter")
        self.enterprise_provider = provider
        self.adapter = adapter

    @classmethod
    def from_env(cls) -> ProviderRegistry:
        provider = os.getenv("REVGUARD_ENTERPRISE_PROVIDER", "mock").strip().lower()
        adapter = ERPNextAdapter(ERPNextSettings.from_env()) if provider == "erpnext" else None
        return cls(provider, adapter)

    def handles(self, tool_name: str) -> bool:
        return self.enterprise_provider == "erpnext" and tool_name in ENTERPRISE_QUERY_TOOLS

    def call(self, tool_name: str, parameters: dict) -> AdapterResult:
        if not self.handles(tool_name) or self.adapter is None:
            raise EnterpriseAdapterError("NOT_FOUND", f"Provider 未路由工具 {tool_name}")
        return self.adapter.call(tool_name, parameters)

    def local_metadata(self, tool_name: str, data: dict, timestamp: str, receipt: str) -> dict:
        if tool_name.startswith("policy."):
            provider, provenance = "revguard-policy", "SYNTHETIC_DOMAIN"
        elif tool_name == "finance.get_commission_ledger" or tool_name.startswith("commission."):
            provider, provenance = "revguard-ledger", "SYSTEM_GENERATED"
        elif tool_name.startswith("workflow."):
            provider, provenance = "revguard-workflow", "SYSTEM_GENERATED"
        else:
            provider, provenance = "mock", "SYNTHETIC_DOMAIN"
        canonical = json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
        return {
            "provider": provider,
            "external_document_type": "fixture" if provider == "mock" else provider,
            "external_document_id": "",
            "source_timestamp": timestamp,
            "http_status": None,
            "latency_ms": 0,
            "payload_hash": "sha256:" + hashlib.sha256(canonical).hexdigest(),
            "correlation_id": receipt,
            "record_url": None,
            "provenance_kind": provenance,
        }


class AdapterMetrics:
    """Bounded counters for enterprise provider calls."""

    def __init__(self):
        self._lock = Lock()
        self._values: dict[tuple[str, str, str], list[int]] = {}

    def observe(self, provider: str, tool_name: str, outcome: str, latency_ms: int) -> None:
        key = (provider or "unknown", tool_name, outcome)
        with self._lock:
            value = self._values.setdefault(key, [0, 0])
            value[0] += 1
            value[1] += max(0, int(latency_ms))

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "provider": provider,
                    "tool": tool,
                    "outcome": outcome,
                    "calls": values[0],
                    "latency_ms_total": values[1],
                }
                for (provider, tool, outcome), values in sorted(self._values.items())
            ]

    def prometheus_text(self) -> str:
        lines = [
            "# HELP revguard_provider_calls_total Tool calls by provider and outcome.",
            "# TYPE revguard_provider_calls_total counter",
            "# HELP revguard_provider_latency_milliseconds_total Accumulated provider latency.",
            "# TYPE revguard_provider_latency_milliseconds_total counter",
        ]
        for item in self.snapshot():
            labels = (
                f'provider={json.dumps(item["provider"])},'
                f'tool={json.dumps(item["tool"])},outcome={json.dumps(item["outcome"])}'
            )
            lines.append(f'revguard_provider_calls_total{{{labels}}} {item["calls"]}')
            lines.append(
                f'revguard_provider_latency_milliseconds_total{{{labels}}} '
                f'{item["latency_ms_total"]}'
            )
        return "\n".join(lines) + "\n"


ADAPTER_METRICS = AdapterMetrics()
