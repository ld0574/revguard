# Prototype Instructions

Run the local server yourself and open the preview in the browser available to this environment. Do not give the user server-start instructions when you can run it.

Before making substantial visual changes, use the Product Design plugin's `get-context` skill when the visual source is unclear or no longer matches the current goal. When the user gives durable prototype-specific design feedback, preferences, or decisions, record them in `AGENTS.md`.

When implementing from a selected generated mock, treat that image as the source of truth for layout, component anatomy, density, spacing, color, typography, visible content, and hierarchy.

Build app UI in `src/`. Keep `.openai/hosting.json`, `worker/index.js`, `scripts/prepare-sites-build.mjs`, and `tests/sites-worker.test.mjs` intact so the same local prototype can be handed to Sites. Before a Sites handoff, run `npm run build` and `npm run test:sites`; the build must leave `dist/client/index.html`, `dist/server/index.js`, and `dist/.openai/hosting.json`.

## RevGuard selected direction

- Preserve the approved dark RevGuard governance cockpit visual documented in
  `design-qa.md` and the project screenshots generated during local QA.
- Preserve the clear evidence → policy → calculation → human approval → execution →
  independent verification → rollback → post-rollback verification pipeline from the selected
  direction.
- Keep the detailed evidence table, policy time-travel timeline, calculation ledger, capability
  constraints, Agent responsibility matrix, and immutable audit trail from the approved fusion.
- The UI must disclose synthetic business data and real executable workflow at all times.
- Core actions must use the RevGuard API; do not hard-code a successful or rolled-back outcome.
- Use dark navy, cyan structure, orange governed action, red verification failure, and green safe
  restoration. Avoid generic dashboard KPI grids, chat UI, decorative graphs, and cyberpunk excess.
