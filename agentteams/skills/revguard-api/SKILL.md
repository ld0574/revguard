---
name: revguard-api
description: Call RevGuard read-only evidence tools with a Matrix message ID and persist the cross-boundary correlation in RevGuard Trace/Audit.
---

# RevGuard Read-only Evidence Adapter

Use this skill when a RevGuard case requires CRM, contract, policy or finance evidence.
The adapter owns the Bearer credential; never read, print, request or place credentials in chat.

## Required call

```bash
python /root/.copaw-worker/revguard-evidence/skills/revguard-api/scripts/revguard_call.py \
  --tool crm.get_order \
  --case-id CASE-2026-0008 \
  --parameters '{"order_id":"EZ202608001"}' \
  --message-id '$MATRIX_EVENT_ID'
```

Replace `$MATRIX_EVENT_ID` with the exact triggering Matrix event ID included in the task.
The adapter permits only Evidence Agent read tools and always sends `X-AgentTeams-Message-ID`
and `X-Request-ID`. Its output contains business data plus `tool_receipt` and `request_id`,
never the credential.

## Response handling

- `success=true`: cite `request_id` and `tool_receipt`, then return the requested evidence.
- `TOOL_UNAVAILABLE` with `retryable=true`: retry at most 3 times with a new request ID.
- HTTP 401/403: stop and report Adapter/Principal configuration failure; never guess a key.
- Any write request: refuse it and return the task to Orchestrator.

Do not use curl with a hand-written Authorization header. Do not inspect `.copaw.secret`.
