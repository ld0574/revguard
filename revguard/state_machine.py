"""RevGuard Case 状态机的唯一迁移入口。"""
from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from .models import CaseStatus, new_id, utc_now
from .money_journal import MoneyJournal

if TYPE_CHECKING:
    from .store import Store


class InvalidStateTransition(ValueError):
    """Case 状态迁移不在显式白名单内。"""


class StaleCaseTransition(InvalidStateTransition):
    """Another transition or recording generation has superseded this input."""


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
    # A failed write flow may only reopen into the safety path.  The recovery
    # endpoint additionally proves that verification requested rollback and
    # that the failed stage was a reversal/post-rollback verification stage.
    CaseStatus.FAILED: frozenset({CaseStatus.ROLLBACK_REQUIRED, CaseStatus.RECOVERY_REQUIRED}),
    CaseStatus.RECOVERY_REQUIRED: frozenset({CaseStatus.EXECUTING, CaseStatus.ROLLBACK_REQUIRED}),
}

_FAILURE_SOURCES = frozenset(
    status for status in CaseStatus
    if status not in {CaseStatus.ROLLED_BACK, CaseStatus.CLOSED, CaseStatus.FAILED}
)


def transition_case(
    store: Store,
    case: dict,
    to: CaseStatus,
    reason: str,
    *,
    actor: str = "revguard-orchestrator",
) -> None:
    """Commit state and audit together; mutate the caller only after commit."""
    with MoneyJournal(store).transaction() as tx:
        updated = persist_case_transition(store, tx, case, to, reason, actor=actor)
    case.clear()
    case.update(updated)


def locked_case(tx, case_id: str) -> dict | None:
    query = "SELECT data FROM cases WHERE case_id=? FOR UPDATE" if tx.postgres else "SELECT data FROM cases WHERE case_id=?"
    row = tx.execute(query, (case_id,)).fetchone()
    if not row:
        return None
    return json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]


def persist_case_transition(store, tx, case: dict, to: CaseStatus, reason: str,
                            *, actor: str) -> dict:
    """Internal transaction participant; the caller owns commit/cache updates.

    Compare the state generation while retaining fields prepared by the caller.
    This is a state-transition claim, not CAS for every ordinary case-field edit.
    """
    try:
        old = CaseStatus(case.get("status"))
    except (TypeError, ValueError) as exc:
        raise InvalidStateTransition(f"未知 Case 当前状态: {case.get('status')!r}") from exc

    recovery = to is CaseStatus.RECOVERY_REQUIRED and old in {
        CaseStatus.EXECUTING, CaseStatus.VERIFYING, CaseStatus.ROLLBACK_REQUIRED,
    }
    allowed = recovery or to in ALLOWED_TRANSITIONS[old] or (
        to is CaseStatus.FAILED and old in _FAILURE_SOURCES
    )
    if not allowed:
        raise InvalidStateTransition(f"非法 Case 状态迁移: {old.value} -> {to.value}")

    current = locked_case(tx, case["case_id"])
    if current is None or any(current.get(key) != case.get(key) for key in (
        "status", "recording_id", "_state_version",
    )):
        raise StaleCaseTransition("案件状态或录制批次已变化，请刷新后操作")
    updated = copy.deepcopy(case)
    updated.update(status=to.value, updated_at=utc_now(), _state_version=new_id("STATE"))
    store._save_case_with_conn(tx.conn, updated)
    tx.audit(case["case_id"], "STATE_TRANSITION", {
        "from": old.value,
        "to": to.value,
        "reason": reason,
    }, actor=actor)
    return updated
