# Contributing to RevGuard

RevGuard accepts fixes, reproducibility improvements, new deterministic test
cases and enterprise read adapters.  Financial decisions must remain in the
deterministic kernel: an adapter may retrieve facts, but it must not select a
policy, calculate an amount, approve a write or advance case state.

## Development contract

1. Create a branch and keep changes focused.
2. Add contract tests for changes to an Adapter, Skill, state transition or
   persistence boundary.
3. Run `bash scripts/verify_docker.sh` on a Docker host.  The gate creates its
   own databases and does not use the demonstration data.
4. Do not commit credentials, customer data, Matrix identifiers, generated
   runtime databases or row-level Olist files.
5. Update documentation when behavior, provenance labels or public claims
   change.

Pull requests should explain the concrete failure or user need, the resulting
behavior, validation performed and any boundary that remains unvalidated.

## Adapter contributions

Implement the `EnterpriseAdapter` protocol in `revguard/adapters.py`, use the
stable tool contract documented in `docs/adapters.md`, and provide tests for
authentication failure, pagination, timeouts, rate limits, schema drift and
secret redaction.  A provider must fail closed; silent fallback to fixtures is
not accepted.

By contributing, you agree that your contribution is licensed under
Apache-2.0.  Dataset licenses remain independent and must be documented.
