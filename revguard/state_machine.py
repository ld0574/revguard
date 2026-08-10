"""RevGuard Case 状态机的唯一迁移入口。"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import CaseStatus, utc_now

if TYPE_CHECKING:
    from .store import Store


class InvalidStateTransition(ValueError):
    """Case 状态迁移不在显式白名单内。"""


ALLOWED_TRANSITIONS: dict[CaseStatus, frozenset[CaseStatus]] = {
    CaseStatus.CREATED: frozenset({CaseStatus.NORMALIZING}),
    CaseStatus.NORMALIZING: frozenset({
        CaseStatus.EVIDENCE_COLLECTING,
        CaseStatus.WAITING_FOR_EVIDENCE,
    }),
    CaseStatus.EVIDENCE_COLLECTING: frozenset({
        CaseStatus.POLICY_MATCHING,
        CaseStatus.WAITING_FOR_EVIDENCE,
    }),
    CaseStatus.WAITING_FOR_EVIDENCE: frozenset({CaseStatus.NORMALIZING}),
    CaseStatus.POLICY_MATCHING: frozenset({CaseStatus.CALCULATING}),
    CaseStatus.CALCULATING: frozenset({CaseStatus.ROOT_CAUSE_ANALYZING}),
    CaseStatus.ROOT_CAUSE_ANALYZING: frozenset({CaseStatus.RISK_REVIEW}),
    CaseStatus.RISK_REVIEW: frozenset({
        CaseStatus.RESOLVED,
        CaseStatus.WAITING_FOR_APPROVAL,
        CaseStatus.READY_TO_EXECUTE,
        CaseStatus.CLOSED,
    }),
    CaseStatus.WAITING_FOR_APPROVAL: frozenset({
        CaseStatus.READY_TO_EXECUTE,
        CaseStatus.REJECTED,
    }),
    CaseStatus.READY_TO_EXECUTE: frozenset({CaseStatus.EXECUTING}),
    CaseStatus.EXECUTING: frozenset({CaseStatus.VERIFYING, CaseStatus.RESOLVED}),
    CaseStatus.VERIFYING: frozenset({CaseStatus.RESOLVED, CaseStatus.ROLLBACK_REQUIRED}),
    CaseStatus.RESOLVED: frozenset({CaseStatus.KNOWLEDGE_ARCHIVED}),
    CaseStatus.ROLLBACK_REQUIRED: frozenset({CaseStatus.ROLLED_BACK}),
    CaseStatus.REJECTED: frozenset({CaseStatus.KNOWLEDGE_ARCHIVED}),
    CaseStatus.KNOWLEDGE_ARCHIVED: frozenset({CaseStatus.CLOSED}),
    CaseStatus.ROLLED_BACK: frozenset(),
    CaseStatus.CLOSED: frozenset(),
    CaseStatus.FAILED: frozenset(),
}

_FAILURE_SOURCES = frozenset(
    status for status in CaseStatus
    if status not in {CaseStatus.ROLLED_BACK, CaseStatus.CLOSED, CaseStatus.FAILED}
)


def transition_case(
    store: "Store",
    case: dict,
    to: CaseStatus,
    reason: str,
    *,
    actor: str = "revguard-orchestrator",
) -> None:
    """校验、审计并持久化一次状态迁移；禁止绕过此函数改状态。"""
    try:
        old = CaseStatus(case.get("status"))
    except (TypeError, ValueError) as exc:
        raise InvalidStateTransition(f"未知 Case 当前状态: {case.get('status')!r}") from exc

    allowed = to in ALLOWED_TRANSITIONS[old] or (
        to is CaseStatus.FAILED and old in _FAILURE_SOURCES
    )
    if not allowed:
        raise InvalidStateTransition(f"非法 Case 状态迁移: {old.value} -> {to.value}")

    case["status"] = to.value
    case["updated_at"] = utc_now()
    store.audit(case["case_id"], actor, "STATE_TRANSITION", {
        "from": old.value,
        "to": to.value,
        "reason": reason,
    })
    store.save_case(case)
