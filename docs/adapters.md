# Enterprise Adapter SDK and validation status

RevGuard exposes stable read tools while allowing the enterprise system behind
them to change.  Policy selection and money effects remain in RevGuard.

```text
crm.* / contract.* / finance reads
               ↓
       ProviderRegistry
       ├── mock       (offline regression only)
       └── erpnext    (VALIDATED)

policy.*       → revguard-policy
commission.*   → revguard-ledger
```

An `EnterpriseAdapter.call(tool_name, parameters)` implementation returns an
`AdapterResult` with provider, external document type and ID, source time, HTTP
status, latency, payload hash, correlation ID, record URL and provenance.  An
error must use a stable semantic such as `AUTH_FAILED`, `TIMEOUT`,
`RATE_LIMITED`, `TOOL_UNAVAILABLE`, `NOT_FOUND` or `DATA_CONFLICT`.  It must
never fall back to fixtures after a live-provider failure.

## Provider matrix

| Provider | Status | Authentication | Pagination | Idempotency / error semantics |
|---|---|---|---|---|
| ERPNext v16 | `VALIDATED` | Frappe API key/secret token bound to a read-only user | `limit_start` + `limit_page_length` | Read only; HTTP status mapped to stable errors |
| Kingdee K3 Cloud | `NOT_VALIDATED` | Tenant/account-set application session | Connector must follow Kingdee page index/limit contract | External writes require a separate idempotent Saga and are outside 0.6.0 |
| Yonyou BIP | `NOT_VALIDATED` | OAuth2/client credentials for an authorized tenant | Provider cursor/page contract | 429/5xx must retain provider request ID and stop the case |
| SAP S/4HANA | `NOT_VALIDATED` | OAuth2 communication arrangement | OData `$top`/`$skiptoken` | ETag/conflict semantics must be mapped explicitly |

The configuration contract is
[`config/enterprise-adapters.schema.json`](../config/enterprise-adapters.schema.json).
The non-ERPNext rows are extension specifications, not claims of connectivity.

## Contract-test checklist

- equivalent normalized output for fixture and live providers;
- insufficient permission, invalid authentication and expired credentials;
- pagination, timeout, rate limit and 5xx;
- missing fields, duplicates, conflicting records and schema drift;
- redaction of tokens, cookies and management URLs;
- an Evidence Gap with no silent provider change.

ERPNext remains a read source in 0.6.0.  Posting formal accounting entries to
an external ERP and compensating across systems are explicitly unvalidated.
