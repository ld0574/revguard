# Changelog

## 0.6.0-rc2 — 2026-09-17

- Added a fail-closed ERPNext REST provider with authenticated receipts,
  pagination, provenance and schema/error handling.
- Split CASE-0001 and CASE-0008 onto independent orders and recording chains;
  added verifiable Element handoffs and Matrix-bound human approval evidence.
- Added the reproducible 10,000-row Olist experiment, official public fee-rule
  snapshots, controlled anomaly injection and ERPNext draft import.
- Added PostgreSQL physical read-replica deployment, lag-aware routing and
  primary fallback for stale or unavailable replicas.
- Expanded Prometheus/Grafana coverage for providers, model usage, replication,
  database waits, evidence gaps and money recovery.
- Added the static GitHub Pages site, adapter documentation, contribution and
  security policies, release evidence and finals presentation assets.

## 0.5.10 — 2026-09-12

- Made coordinator projection writes atomic with the case-version transition.
- Preserved old-worker isolation during recovery and production deployment.

Earlier detailed release evidence is retained under `docs/evidence/`.
