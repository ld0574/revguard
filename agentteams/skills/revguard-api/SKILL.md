---
name: revguard-api
description: Invoke the RevGuard Skill allowed for this Worker and persist Matrix-to-Skill correlation in RevGuard Trace/Audit.
---

# RevGuard Skills-only Adapter（Higress MCP 主路径）

The adapter derives the Worker from `AGENTTEAMS_WORKER_NAME`, enforces the assigned Skill
allowlist and invokes the actor-scoped Higress MCP server through mcporter. Business Workers
hold only a gateway consumer token; Higress injects the backend Bearer credential.
When MCP is configured, errors fail closed and never fall back to direct REST. The legacy REST
path is only for hosts without MCP configuration. Never inspect, print or request credentials.

## Orchestrator dispatch

```bash
python /root/.copaw-worker/revguard-orchestrator/skills/revguard-api/scripts/revguard_call.py \
  --dispatch-skill CaseNormalizeSkill \
  --case-id CASE-STAGE-20260812-001 \
  --input '{"raw_case":{"partner_id":"AGT-10001","order_id":"EZ202608001"}}' \
  --message-id '$MATRIX_EVENT_ID'
```

Pass the returned `task_id`, exact `input`, `skill_name` and `assigned_actor` to the assigned
Worker. Orchestrator must not invoke the domain Skill with another Worker's Principal.

## Assigned Worker invocation

```bash
python /root/.copaw-worker/revguard-intake/skills/revguard-api/scripts/revguard_call.py \
  --skill CaseNormalizeSkill \
  --task-id TASK-REPLACE-WITH-DISPATCHED-ID \
  --case-id CASE-STAGE-20260812-001 \
  --input '{"raw_case":{"partner_id":"AGT-10001","order_id":"EZ202608001"}}' \
  --message-id '$MATRIX_EVENT_ID'
```

Replace `$MATRIX_EVENT_ID` and the Task ID with the exact values included in the dispatched task.
The adapter permits only the Skills assigned to the current Worker and always sends
`X-AgentTeams-Message-ID` and `X-Request-ID`. Its output contains business data plus
`skill_receipt` and `request_id`, never the credential. Direct `tool.*` invocation is not
supported by this Agent-facing adapter.

The server rejects a different actor, Skill, input, stale case snapshot or replay.

## Response handling

- `success=true`: cite `task_id`, `request_id` and `skill_receipt`, then return the requested artifact.
- `success=false`: preserve `error.type`, `error.message`, `task_id` and `request_id`; do not claim completion.
- `TOOL_UNAVAILABLE` with `retryable=true`: retry at most 3 times with a new request ID.
- HTTP 401/403: stop and report Adapter/Principal configuration failure; never guess a key.
- Any Skill outside the current Worker's allowlist: refuse it and return the task to Orchestrator.

Do not use curl with a hand-written Authorization header. Do not inspect `.copaw.secret`.
