"""Atomic human recovery in the database that owns RevGuard's ledger."""
from __future__ import annotations

import copy
import json

from .models import CaseStatus, utc_now
from .security import authorize_tool
from .state_machine import StaleCaseTransition, locked_case, persist_case_transition


class RecoveryCapabilityError(ValueError):
    def __init__(self, phase: str, message: str):
        super().__init__(message)
        self.code = "ROLLBACK_CAPABILITY_RENEWAL_FAILED" if phase == "ROLLBACK" else "APPROVAL_CAPABILITY_RENEWAL_FAILED"


def _data(row) -> dict:
    if not row:
        return {}
    return json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]


def resume_case_run(gateway, case: dict, *, approval: dict, verification: dict,
                    rollback: bool, actor: str, subject: str, assertion_ref: str,
                    age_seconds: float) -> str:
    """Fence old tasks, reconcile, renew and queue under one effect lock.

    The API first authenticates a case/approval-bound human action and checks
    the stale/live gate. The exact snapshots are rechecked under this lock.
    A commit failure leaves the original hold, capabilities and tasks intact.
    """
    from .mocks import ToolError

    authorize_tool(actor, ["approval:decide"], "workflow.renew_approval_capability")
    store, case_id = gateway.journal.store, case["case_id"]
    phase = "ROLLBACK" if rollback else "EXECUTION"
    before = None
    with gateway._lock:
        try:
            with gateway.journal.transaction() as tx:
                if locked_case(tx, case_id) != case:
                    raise StaleCaseTransition("恢复案件已变化，请刷新后核对")
                if _data(tx.execute("SELECT data FROM verifications WHERE case_id=?", (case_id,)).fetchone()) != verification:
                    raise StaleCaseTransition("核验结果已变化，请刷新后核对")
                current_approval = _data(tx.execute("SELECT data FROM approvals WHERE case_id=? AND approval_id=?",
                                                  (case_id, approval.get("approval_id", ""))).fetchone())
                if current_approval != approval:
                    raise StaleCaseTransition("审批凭证已变化，请刷新后核对")
                before = tx.state()
                gateway._apply_state(copy.deepcopy(before))
                cancelled = store._cancel_open_agent_tasks_with_conn(
                    tx.conn, case_id, actor=actor, reason="人工恢复隔离上一轮未完成任务",
                )
                operations = gateway.journal.reconcile_with_tx(tx, case_id)
                tx.audit(case_id, "HUMAN_RECOVERY_AUTHORIZED", {
                    "human_subject": subject, "operation_count": len(operations),
                }, actor=actor)
                run = case.get("team_run") or {}
                gateway._in_transaction = True
                try:
                    if not rollback and (case.get("risk_decision") or {}).get("approval_required"):
                        renewed = gateway._tool_workflow_renew_approval_capability({
                            "approval_id": approval.get("approval_id"), "case_id": case_id,
                        }, actor=actor)
                        store._save_approval_with_conn(tx.conn, renewed)
                        tx.audit(case_id, "APPROVAL_CAPABILITY_RENEWED", {
                            "approval_id": renewed["approval_id"], "human_subject": subject,
                            "remaining_component_quota": renewed.get("remaining_component_quota", {}),
                            "previous_run_id": run.get("run_id"),
                        }, actor=actor)
                    if rollback:
                        renewed_ledgers = []
                        rows = tx.execute("SELECT data FROM executions WHERE case_id=?", (case_id,)).fetchall()
                        for row in rows:
                            execution = _data(row)
                            ledger = execution.get("ledger_entry") or {}
                            if execution.get("status") != "SUBMITTED" or not ledger.get("ledger_id"):
                                continue
                            renewed = gateway._tool_workflow_renew_rollback_capability({
                                "case_id": case_id, "ledger_id": ledger["ledger_id"],
                                "action_id": execution.get("action_id"),
                            }, actor=actor)
                            execution["rollback_token"] = renewed["rollback_token"]
                            tx.execution(execution)
                            # Reconciliation reloads from this authoritative image;
                            # preserve the renewed token there as well as the projection.
                            gateway._execution_results[execution["idempotency_key"]] = copy.deepcopy(execution)
                            renewed_ledgers.append(ledger["ledger_id"])
                        tx.audit(case_id, "ROLLBACK_CAPABILITY_RENEWED", {
                            "ledger_ids": renewed_ledgers, "human_subject": subject,
                            "previous_run_id": run.get("run_id"),
                        }, actor=actor)
                except ToolError as exc:
                    raise RecoveryCapabilityError(phase, str(exc)) from exc
                finally:
                    gateway._in_transaction = False
                tx.save_state(gateway._state_snapshot())
                pending = copy.deepcopy(case)
                if rollback and pending["status"] != CaseStatus.ROLLBACK_REQUIRED.value:
                    pending = persist_case_transition(store, tx, pending, CaseStatus.ROLLBACK_REQUIRED,
                                                      "审批人确认原操作对账后恢复补偿", actor=actor)
                if not rollback and pending["status"] not in {CaseStatus.READY_TO_EXECUTE.value, CaseStatus.EXECUTING.value}:
                    if pending["status"] != CaseStatus.RECOVERY_REQUIRED.value:
                        pending = persist_case_transition(store, tx, pending, CaseStatus.RECOVERY_REQUIRED,
                                                          "写后核验中断，先核对原操作", actor=actor)
                    pending = persist_case_transition(store, tx, pending, CaseStatus.EXECUTING,
                                                      "原操作已在主库完成对账", actor=actor)
                now = utc_now()
                pending["_recovery_generation"] = pending.get("_recovery_generation", 0) + 1
                pending["team_run"] = {
                    **run, "status": "QUEUED", "phase": phase, "queued_at": now,
                    "updated_at": now, "error": None,
                    "recovery": {
                        "reason": "api-process-interrupted",
                        "strategy": "resume-unreversed-executions" if rollback else "idempotent-execution-replay",
                        "requested_by": actor, "requested_subject": subject, "requested_at": now,
                        "stale_for_seconds": int(age_seconds), "cancelled_task_ids": cancelled,
                    },
                }
                updated = store._save_case_with_conn(tx.conn, pending)
                tx.audit(case_id, "TEAM_RUN_RESUME_REQUESTED", {
                    "run_id": run.get("run_id"), "phase": phase, "human_subject": subject,
                    "assertion_id_ref": assertion_ref, "stale_for_seconds": int(age_seconds),
                    "recovery_generation": updated["_recovery_generation"], "cancelled_task_ids": cancelled,
                }, actor=actor)
        except BaseException:
            if before is not None:
                gateway._apply_state(before)
            raise
        case.clear()
        case.update(updated)
    return phase


def record_background_failure(gateway, *, case_id: str, phase: str,
                               generation: int, recording_id, error_type: str) -> bool:
    """An old coordinator must never freeze a newly recovered run."""
    store = gateway.journal.store
    with gateway.journal.transaction() as tx:
        case = locked_case(tx, case_id)
        if not case or case.get("recording_id") != recording_id or case.get("_recovery_generation", 0) != generation:
            return False
        if case["status"] in {CaseStatus.CLOSED.value, CaseStatus.ROLLED_BACK.value}:
            return False
        if case["status"] in {CaseStatus.EXECUTING.value, CaseStatus.VERIFYING.value, CaseStatus.ROLLBACK_REQUIRED.value}:
            gateway.journal.freeze_case_with_tx(tx, case_id)
            case = persist_case_transition(store, tx, case, CaseStatus.RECOVERY_REQUIRED,
                                           "执行中断，须由审批人触发原操作对账", actor="revguard-orchestrator")
            verification = _data(tx.execute("SELECT data FROM verifications WHERE case_id=?", (case_id,)).fetchone())
            case["recovery_phase"] = "ROLLBACK" if phase == "ROLLBACK" or verification.get("rollback_required") else "EXECUTION"
        elif case["status"] not in {CaseStatus.READY_TO_EXECUTE.value, CaseStatus.RECOVERY_REQUIRED.value, CaseStatus.FAILED.value}:
            case = persist_case_transition(store, tx, case, CaseStatus.FAILED,
                                           "AgentTeams后台任务失败", actor="revguard-orchestrator")
        case["team_run"] = {**(case.get("team_run") or {}), "status": "FAILED", "updated_at": utc_now(),
                            "error": {"message": "执行中断，请核对案件后恢复。", "type": error_type}}
        store._save_case_with_conn(tx.conn, case)
        tx.audit(case_id, "TEAM_RUN_FAILED", {"phase": phase, "error_type": error_type}, actor="revguard-orchestrator")
    return True
