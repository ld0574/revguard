# Security policy

## Supported versions

Security fixes are applied to the latest published RevGuard release candidate
and the current `main` branch.  Historical competition evidence is immutable
and is not a supported runtime.

## Reporting a vulnerability

Do not open a public issue for a suspected credential leak, authorization
bypass, capability-token flaw, audit-chain mutation, unsafe money retry or
cross-case data exposure.  Use GitHub's private vulnerability reporting for
`ld0574/revguard` and include the affected commit, reproduction conditions and
impact.  The project will acknowledge a report within 72 hours and publish a
coordinated fix and advisory when confirmed.

## Deployment expectations

- Replace every demo signing key and API principal before non-demo use.
- Keep ERPNext, Matrix and database credentials in permission-restricted
  server files or Docker secrets.
- Keep the legacy Tool endpoint disabled.
- Use TLS and a trusted identity provider outside an isolated demonstration
  network.
- Treat external ERP accounting writes, cloud PolarDB HA and PITR as
  unvalidated until independently accepted in the target environment.

The repository intentionally contains synthetic business fixtures and
aggregate public-data evidence.  It must never contain confidential customer
production data or row-level Olist extracts.
