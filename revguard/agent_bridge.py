"""Persisted StageTask/StageResult bridge for AgentTeams Workers.

The deterministic Case state machine remains authoritative.  This module binds an
external Worker invocation to one case snapshot, one Skill and one server-derived
actor so the server can observe completion without trusting chat text.
"""
from __future__ import annotations

import hashlib
import json

from .json_schema import validate_json
from .models import CaseStatus, TaskStatus, new_id, utc_now
from .skill_runtime import SKILL_ACTORS
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
    """绑定完整案件快照；updated_at 或状态变化会使 pending Task 主动失效。"""
    canonical = json.dumps(case, ensure_ascii=False, sort_keys=True,
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
