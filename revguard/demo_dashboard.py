"""Read-only presentation model for the RevGuard recording cockpit.

The cockpit is a projection of persisted case artifacts.  It never fabricates a
successful state and never returns raw approval or rollback credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .security import redact_secrets, secret_fingerprint
from .trace import Tracer


def _structured_audit(events: list[dict]) -> list[dict]:
    structured: list[dict] = []
    for event in events:
        item = dict(event)
        detail = item.get("detail")
        if isinstance(detail, str):
            try:
                item["detail"] = json.loads(detail)
            except json.JSONDecodeError:
                item["detail"] = {"message": detail}
        structured.append(redact_secrets(item))
    return structured


def _public_approval(approval: dict | None) -> dict | None:
    if not approval:
        return None
    public = dict(approval)
    token = str(public.pop("approval_token", ""))
    public["approval_token_ref"] = secret_fingerprint(token)
    return redact_secrets(public)


def _public_execution(execution: dict) -> dict:
    public = dict(execution)
    token = str(public.pop("rollback_token", ""))
    public["rollback_token_ref"] = secret_fingerprint(token)
    return redact_secrets(public)


def build_dashboard_snapshot(
    store: Any,
    case_id: str,
    *,
    report_dir: str | Path,
) -> dict | None:
    """Return the complete, sanitized read model needed by the WebUI."""
    case = store.get_case(case_id)
    if not case:
        return None

    trace = Tracer(store, case_id).export()
    report_path = Path(report_dir) / f"{case_id}.md"
    executions = [_public_execution(item) for item in store.list_executions(case_id)]
    agent_tasks = [redact_secrets(item) for item in store.list_agent_tasks(case_id)]

    return redact_secrets({
        "case": case,
        "evidence": store.list_evidence(case_id),
        "approval": _public_approval(store.get_approval(case_id)),
        "executions": executions,
        "verification": store.get_verification(case_id),
        "audit_events": _structured_audit(store.list_audit(case_id)),
        "agent_tasks": agent_tasks,
        "trace": trace,
        "report_available": bool(trace["span_count"] and report_path.exists()),
        "disclosure": {
            "business_data": "synthetic",
            "workflow": "real_executable",
            "ledger": "simulated",
        },
    })
