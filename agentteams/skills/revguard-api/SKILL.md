---
name: revguard-api
description: Invoke the RevGuard Skill allowed for this Worker and persist Matrix-to-Skill correlation in RevGuard Trace/Audit.
---

# RevGuard Skills-only Adapter

Use this adapter when a RevGuard Worker must invoke one of its registered domain Skills. The
adapter derives the Worker from `AGENTTEAMS_WORKER_NAME`, enforces a local Skill allowlist and
owns the Bearer credential; never read, print, request or place credentials in chat.

## Orchestrator dispatch

```bash
python /root/.copaw-worker/revguard-orchestrator/skills/revguard-api/scripts/revguard_call.py \
  --dispatch-skill CaseNormalizeSkill \
  --case-id CASE-2026-0008 \
  --input '{"raw_case":{"partner_id":"AGT-10001","order_id":"EZ202608001"}}' \
  --message-id '$MATRIX_EVENT_ID'
```

Pass the returned `task_id`, exact `input`, `skill_name` and `assigned_actor` to the assigned
Worker. Orchestrator must not invoke the domain Skill with another Worker's Principal.

## Assigned Worker invocation

```bash
python /root/.copaw-worker/revguard-evidence/skills/revguard-api/scripts/revguard_call.py \
  --skill EvidenceCollectSkill \
  --task-id TASK-REPLACE-WITH-DISPATCHED-ID \
  --case-id CASE-2026-0008 \
  --input '{"partner":{"partner_id":"AGT-10001"},"order_id":"EZ202608001"}' \
  --message-id '$MATRIX_EVENT_ID'
```

Replace `$MATRIX_EVENT_ID` and the Task ID with the exact values included in the dispatched task.
The adapter permits only the Skills assigned to the current Worker and always sends
`X-AgentTeams-Message-ID` and `X-Request-ID`. Its output contains business data plus
`skill_receipt` and `request_id`, never the credential. Direct `tool.*` invocation is not
supported by this Agent-facing adapter.

The server rejects a different actor, Skill, input, stale case snapshot or replay.

## Response handling

- `success=true`: cite `request_id` and `skill_receipt`, then return the requested artifact.
- `TOOL_UNAVAILABLE` with `retryable=true`: retry at most 3 times with a new request ID.
- HTTP 401/403: stop and report Adapter/Principal configuration failure; never guess a key.
- Any Skill outside the current Worker's allowlist: refuse it and return the task to Orchestrator.

Do not use curl with a hand-written Authorization header. Do not inspect `.copaw.secret`.
