"""Persisted StageTask/StageResult bridge for AgentTeams Workers.

The deterministic Case state machine remains authoritative.  This module binds an
external Worker invocation to one case snapshot, one Skill and one server-derived
actor so the server can observe completion without trusting chat text.
"""
from __future__ import annotations

import hashlib
import json

from .json_schema import validate_json
from .mocks import ToolError
from .models import CaseStatus, TaskStatus, new_id, utc_now
from .skill_runtime import SKILL_ACTORS, invoke_skill
from .skills import SKILL_REGISTRY

SKILL_CASE_STATUSES: dict[str, frozenset[str]] = {
    "CaseNormalizeSkill": frozenset({CaseStatus.CREATED.value,
                                      CaseStatus.WAITING_FOR_EVIDENCE.value}),
    "EntityResolveSkill": frozenset({CaseStatus.NORMALIZING.value}),
    "EvidenceCollectSkill": frozenset({CaseStatus.EVIDENCE_COLLECTING.value}),
    "PolicyVersionMatchSkill": frozenset({CaseStatus.POLICY_MATCHING.value}),
    "CommissionCalculateSkill": frozenset({CaseStatus.CALCULATING.value}),
    "DifferenceExplainSkill": frozenset({CaseStatus.ROOT_CAUSE_ANALYZING.value}),
    "RiskClassifySkill": frozenset({CaseStatus.RISK_REVIEW.value}),
    "ApprovalRouteSkill": frozenset({CaseStatus.RISK_REVIEW.value}),
    "PermissionCheckSkill": frozenset({CaseStatus.READY_TO_EXECUTE.value,
                                        CaseStatus.EXECUTING.value}),
    "IdempotencyGuardSkill": frozenset({CaseStatus.READY_TO_EXECUTE.value,
                                         CaseStatus.EXECUTING.value}),
    "AdjustmentDraftSkill": frozenset({CaseStatus.READY_TO_EXECUTE.value,
                                        CaseStatus.EXECUTING.value}),
    "LedgerAdjustSkill": frozenset({CaseStatus.EXECUTING.value}),
    "LedgerReverseSkill": frozenset({CaseStatus.ROLLBACK_REQUIRED.value}),
    "PostActionVerifySkill": frozenset({CaseStatus.VERIFYING.value}),
    "PostRollbackVerifySkill": frozenset({CaseStatus.ROLLBACK_REQUIRED.value}),
    "CaseToDatasetSkill": frozenset({
        CaseStatus.RESOLVED.value, CaseStatus.REJECTED.value,
        CaseStatus.ROLLED_BACK.value, CaseStatus.CLOSED.value,
    }),
}


def case_version(case: dict) -> str:
    """Bind the domain case snapshot while ignoring presentation-only run progress.

    ``team_run`` changes whenever WebUI polling reports the current Agent/Stage;
    it is operational metadata, not business state.  Hashing it would invalidate
    the very StageTask whose progress it describes.  Status, evidence-derived
    fields, amounts and every other case field remain version-bound.
    """
    versioned_case = {key: value for key, value in case.items() if key != "team_run"}
    canonical = json.dumps(versioned_case, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def create_agent_task(case: dict, skill_name: str, skill_input: dict) -> dict:
    meta = SKILL_REGISTRY.get(skill_name)
    if not meta:
        raise ValueError(f"未知 Skill: {skill_name}")
    if case.get("status") not in SKILL_CASE_STATUSES[skill_name]:
        raise ValueError(
            f"案件状态 {case.get('status')} 不允许派发 {skill_name}"
        )
    actors = SKILL_ACTORS.get(skill_name, frozenset())
    if len(actors) != 1:
        raise ValueError(f"{skill_name} 必须且只能绑定一个 Worker actor")
    validate_json(skill_input, meta["input_schema"], path=f"{skill_name}.input")
    now = utc_now()
    return {
        "task_id": new_id("TASK"),
        "case_id": case["case_id"],
        "skill_name": skill_name,
        "assigned_actor": next(iter(actors)),
        "case_status": case["status"],
        "case_version": case_version(case),
        "status": TaskStatus.PENDING.value,
        "attempt": 0,
        "input": skill_input,
        "result": None,
        "skill_receipt": None,
        "request_id": None,
        "agentteams_message_id": None,
        "traceparent": None,
        "transport": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }


def validate_task_invocation(task: dict, case: dict, *, skill_name: str,
                             actor: str, skill_input: dict) -> None:
    if task["case_id"] != case["case_id"]:
        raise ValueError("Agent task 与案件不匹配")
    if task["skill_name"] != skill_name or task["assigned_actor"] != actor:
        raise ValueError("Agent task 与 Skill/Worker 身份不匹配")
    if task["input"] != skill_input:
        raise ValueError("Agent task 输入与派发快照不一致")
    if task["case_status"] != case.get("status") or task["case_version"] != case_version(case):
        raise ValueError("案件状态已变化，Agent task 快照失效")
    if task["status"] not in {TaskStatus.PENDING.value,
                               TaskStatus.FAILED_RETRYABLE.value}:
        raise ValueError(f"Agent task 状态不允许执行: {task['status']}")


def execute_agent_task(*, task_id: str, case_id: str, skill_name: str,
                       skill_input: dict, actor: str, gateway, store,
                       correlation: dict | None = None,
                       execution_input: dict | None = None) -> dict:
    """Execute one server-bound StageTask through REST or MCP.

    Transport adapters are deliberately thin: this function owns the common
    state transition, immutable input binding, Skill invocation and atomic
    StageResult persistence.  The caller supplies ``actor`` from a trusted
    Principal or a process-scoped MCP server; it is never read from tool input.
    ``execution_input`` is reserved for server-side secret hydration after the
    public task input has passed its immutable binding check.  It is never
    persisted into the StageTask or returned to the Worker.
    """
    task = store.get_agent_task(task_id)
    if not task:
        raise LookupError(f"Agent task 不存在: {task_id}")
    case = store.get_case(case_id)
    if not case:
        raise LookupError(f"案件不存在: {case_id}")
    validate_task_invocation(
        task, case, skill_name=skill_name, actor=actor, skill_input=skill_input,
    )
    source_correlation = correlation or {}
    persisted_correlation = {
        key: value for key, value in (correlation or {}).items()
        if key in {
            "request_id", "agentteams_message_id", "traceparent",
            "matrix_dispatch_event_id", "matrix_trigger_event_id",
        } and value is not None
    }
    incoming_transport = source_correlation.get("transport")
    if incoming_transport is not None:
        # Preserve the end-to-end delivery transport already written by the
        # Matrix/MCP dispatcher.  The Worker may reach the Skill through a REST
        # adapter; that is a nested hop, not a reason to relabel the StageTask.
        transport_key = "skill_transport" if task.get("transport") else "transport"
        persisted_correlation[transport_key] = incoming_transport
    running = store.transition_agent_task(
        task_id,
        expected={TaskStatus.PENDING.value, TaskStatus.FAILED_RETRYABLE.value},
        status=TaskStatus.RUNNING.value,
        updates=persisted_correlation,
    )
    store.audit(case_id, actor, "AGENT_TASK_STARTED", {
        "task_id": task_id,
        "skill": skill_name,
        **persisted_correlation,
    })
    try:
        result = invoke_skill(
            skill_name, execution_input or skill_input, actor=actor, case_id=case_id,
            gateway=gateway, store=store, correlation=correlation,
        )
    except Exception as exc:
        failed_status = (
            TaskStatus.FAILED_RETRYABLE.value
            if isinstance(exc, ToolError) and exc.retryable
            else TaskStatus.FAILED_FINAL.value
        )
        store.complete_agent_task(
            running["task_id"], status=failed_status,
            error={"type": type(exc).__name__, "message": str(exc)},
        )
        store.audit(case_id, actor, "AGENT_TASK_FAILED", {
            "task_id": task_id,
            "skill": skill_name,
            "status": failed_status,
            "error_type": type(exc).__name__,
            **persisted_correlation,
        })
        raise
    store.complete_agent_task(
        running["task_id"], status=TaskStatus.SUCCEEDED.value,
        result=result["data"], skill_receipt=result["skill_receipt"],
    )
    store.audit(case_id, actor, "AGENT_TASK_SUCCEEDED", {
        "task_id": task_id,
        "skill": skill_name,
        "skill_receipt": result["skill_receipt"],
        **persisted_correlation,
    })
    return result
