# Public-data experiment and provenance boundary

RevGuard does not use confidential customer production data.  The finals
experiment combines four separately labelled layers:

| Label | Meaning in this release |
|---|---|
| `PUBLIC_REAL` | Anonymized Olist transaction fields and official public fee-rule snapshots |
| `SYNTHETIC_DOMAIN` | Platform assignment, commission settlement, seller tier and controlled anomaly |
| `LIVE_SYSTEM` | ERPNext documents and authenticated REST receipts produced by the running system |
| `SYSTEM_GENERATED` | Approval, StageTask, execution, verification, recovery, Trace and audit records |

The Olist source archive is fetched from Kaggle and remains untouched.  Its
license is CC BY-NC-SA 4.0, so the Apache-2.0 repository does not redistribute
the archive or transformed row files.  The release contains the source URL,
license, source SHA-256, deterministic seed (`202609`), transformation code,
aggregate summary and output hashes.

The 10,000 selected transactions are imported into a real ERPNext v16 site as
draft Customer, Item, Sales Partner, Sales Order, Sales Invoice and Payment
Entry records.  Draft status prevents the public experiment from creating
General Ledger effects.  The order-to-ERP mapping is hashed in the acceptance
record; row-level mappings remain on the private runtime host under the dataset
license.

Official Etsy and eBay fee pages are captured as rule source URLs.  Assigning a
historical Olist row to one of those platforms is a deterministic experimental
mapping and is always labelled `SYNTHETIC_DOMAIN`; it is not evidence that an
Olist seller used those platforms or rules.

The fee experiment is a **current-policy counterfactual evaluated on
2026-09-16**, not a historical recalculation of the 2016–2018 Olist orders.
It evaluates the published percentage component only. Currency-specific
payment charges, taxes, caps, thresholds and eBay per-order fixed fees are
outside this experiment unless a rule explicitly models them. Source orders
selected for ERP import have one seller and one product so the ERP order header
never silently drops a second party or product; item price and freight remain a
documented order-level aggregate.

The standard public statement is:

> 真实公开交易底座 + 真实 ERP 系统 + 真实公开费率规则 + 可复现的合成结算与异常场景。
