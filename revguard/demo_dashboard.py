"""Read-only presentation model for the RevGuard recording cockpit.

The cockpit is a projection of persisted case artifacts.  It never fabricates a
successful state and never returns raw approval or rollback credentials.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import artifact_path
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


def _current_generation_start(events: list[dict], recording_id: str | None) -> int | None:
    """Seq of the marker that opened the case's current recording generation.

    Audit rows are append-only, so a reprepared Golden Case keeps every earlier
    generation.  The cockpit must show one run's evidence chain, so the read
    model scopes audit rows to the generation whose ``recording_id`` matches the
    case: rows emitted after ``DEMO_CASE_REPREPARED`` / ``DEMO_RESET`` for that
    generation.  The full chain stays available from the case endpoint.
    """
    if not recording_id:
        return None
    for item in reversed(events):
        if item.get("event") not in {"DEMO_CASE_REPREPARED", "DEMO_RESET"}:
            continue
        detail = item.get("detail")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail) if detail else {}
            except json.JSONDecodeError:
                detail = {}
        if isinstance(detail, dict) and detail.get("recording_id") == recording_id:
            return item.get("seq")
    return None


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


def _public_case(case: dict, agent_tasks: list[dict]) -> dict:
    """Use actual tasks for completed-run progress, keeping the planned budget.

    The execution plan reserves rollback stages. A normal closure never creates
    those tasks, so its original 20-task budget is not unfinished work. This is
    a read-only projection; neither task status nor stored run history changes.
    """
    public = dict(case)
    run = case.get("team_run") or {}
    if run.get("status") == "COMPLETED" and agent_tasks:
        public["team_run"] = {
            **run,
            "planned_total_tasks": run.get("planned_total_tasks", run.get("total_tasks")),
            "total_tasks": len(agent_tasks),
            "completed_tasks": sum(item.get("status") == "SUCCEEDED" for item in agent_tasks),
            "progress_basis": "persisted_stage_tasks",
        }
    return public


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
    audit_history = store.list_audit(case_id)
    generation_start = _current_generation_start(audit_history, case.get("recording_id"))
    audit_events = [
        item for item in audit_history
        if generation_start is None or (item.get("seq") or 0) > generation_start
    ]
    report_path = artifact_path(report_dir, case, ".md")
    executions = [_public_execution(item) for item in store.list_executions(case_id)]
    agent_tasks = [redact_secrets(item) for item in store.list_agent_tasks(case_id)]

    return redact_secrets({
        "case": _public_case(case, agent_tasks),
        "evidence": store.list_evidence(case_id),
        "approval": _public_approval(store.get_approval(case_id)),
        "executions": executions,
        "verification": store.get_verification(case_id),
        "audit_events": _structured_audit(audit_events),
        "audit_generation": {
            "recording_id": case.get("recording_id"),
            "start_seq": generation_start,
            "event_count": len(audit_events),
            "history_event_count": len(audit_history),
        },
        "agent_tasks": agent_tasks,
        "trace": trace,
        "report_available": bool(trace["span_count"] and report_path.exists()),
        "disclosure": {
            "business_data": "synthetic",
            "workflow": "real_executable",
            "ledger": "simulated",
        },
    })
