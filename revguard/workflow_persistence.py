"""Database claims for mutable case snapshots and Worker task metadata."""
from __future__ import annotations

import copy
import json

from .models import utc_now
from .money_journal import MoneyJournal, MoneyTransaction
from .state_machine import StaleCaseTransition, locked_case


def versioned_case_write(conn, case: dict, *, postgres: bool, recording_replace: bool = False) -> dict:
    """Called inside the caller's transaction before the actual case write."""
    current = locked_case(MoneyTransaction(conn, postgres=postgres), case["case_id"])
    revision = (current or {}).get("_case_revision", 0)
    if case.get("_case_revision", 0) != revision or (
        current is not None and not recording_replace and current.get("recording_id") != case.get("recording_id")
    ):
        raise StaleCaseTransition("案件快照已被更新，请刷新后操作")
    updated = copy.deepcopy(case)
    updated["_case_revision"] = revision + 1
    return updated


def update_case_run(store, case: dict, updates: dict) -> None:
    """Patch progress without letting a stale coordinator replace the case."""
    with MoneyJournal(store).transaction() as tx:
        current = locked_case(tx, case["case_id"])
        if current is None or current.get("_case_revision", 0) != case.get("_case_revision", 0) or (
            current.get("recording_id") != case.get("recording_id")
        ):
            raise StaleCaseTransition("运行所属案件快照已变化，拒绝旧进度更新")
        run = {**(current.get("team_run") or {}), **copy.deepcopy(updates), "updated_at": utc_now()}
        current["team_run"] = run
        committed = store._save_case_with_conn(tx.conn, current)
    # Preserve any domain fields that the coordinator has prepared but has
    # not yet saved. Only its own committed metadata/revision is refreshed.
    case["team_run"] = run
    case["_case_revision"] = committed["_case_revision"]


def locked_task(tx, task_id: str) -> dict | None:
    query = "SELECT data FROM agent_tasks WHERE task_id=? FOR UPDATE" if tx.postgres else "SELECT data FROM agent_tasks WHERE task_id=?"
    row = tx.execute(query, (task_id,)).fetchone()
    if not row:
        return None
    return json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]


def claim_agent_task(store, task_id: str, *, case_id: str, skill_name: str,
                     actor: str, skill_input: dict, updates: dict) -> dict:
    """Validate the authoritative case/task and claim plus audit in one commit."""
    from .agent_bridge import validate_task_invocation

    with MoneyJournal(store).transaction() as tx:
        case = locked_case(tx, case_id)
        task = locked_task(tx, task_id)
        if case is None or task is None:
            raise LookupError("案件或任务不存在")
        validate_task_invocation(task, case, skill_name=skill_name, actor=actor, skill_input=skill_input)
        if set(updates) - TASK_METADATA:
            raise ValueError("任务领取包含非法元数据")
        task.update(copy.deepcopy(updates))
        task.update(status="RUNNING", attempt=int(task.get("attempt", 0)) + 1, updated_at=utc_now())
        if tx.postgres:
            tx.execute("UPDATE agent_tasks SET status=?,attempt=?,data=?::jsonb,updated_at=? WHERE task_id=?",
                       (task["status"], task["attempt"], json.dumps(task), task["updated_at"], task_id))
        else:
            tx.execute("UPDATE agent_tasks SET status=?,data=?,updated_at=? WHERE task_id=?",
                       (task["status"], json.dumps(task), task["updated_at"], task_id))
        tx.audit(case_id, "AGENT_TASK_STARTED", {
            "task_id": task_id, "skill": skill_name, **updates,
        }, actor=actor)
    return task


TASK_METADATA = frozenset({
    "request_id", "agentteams_message_id", "matrix_dispatch_event_id", "matrix_trigger_event_id",
    "matrix_response_event_id", "matrix_room_id", "transport", "skill_transport", "traceparent",
    "telemetry", "token_usage", "token_usage_source",
})


def audit_task_completion(tx, task: dict) -> None:
    succeeded = task["status"] == "SUCCEEDED"
    detail = {"task_id": task["task_id"], "skill": task["skill_name"],
              "status": task["status"], "attempt": task.get("attempt")}
    detail.update({key: task[key] for key in TASK_METADATA if key in task and key not in {
        "telemetry", "token_usage", "token_usage_source",
    }})
    if succeeded:
        detail["skill_receipt"] = task.get("skill_receipt")
    else:
        detail["error_type"] = (task.get("error") or {}).get("type")
    tx.audit(task["case_id"], "AGENT_TASK_SUCCEEDED" if succeeded else "AGENT_TASK_FAILED",
             detail, actor=task["assigned_actor"])


def update_task_metadata(store, task_id: str, updates: dict, *, retry_event_id: str | None = None) -> dict:
    """Late transport/usage metadata must never replace a committed result."""
    if set(updates) - TASK_METADATA:
        raise ValueError("任务元数据更新不得修改状态、输入、尝试次数或执行结果")
    with MoneyJournal(store).transaction() as tx:
        task = locked_task(tx, task_id)
        if task is None:
            raise LookupError(task_id)
        if "telemetry" in updates:
            updates = {**updates, "telemetry": {**(task.get("telemetry") or {}), **updates["telemetry"]}}
        task.update(copy.deepcopy(updates))
        if retry_event_id is not None:
            events = task.setdefault("matrix_retry_event_ids", [])
            if retry_event_id not in events:
                events.append(retry_event_id)
        task["updated_at"] = utc_now()
        encoded = json.dumps(task, ensure_ascii=False)
        query = "UPDATE agent_tasks SET data=?::jsonb,updated_at=? WHERE task_id=?" if tx.postgres else "UPDATE agent_tasks SET data=?,updated_at=? WHERE task_id=?"
        tx.execute(query, (encoded, task["updated_at"], task_id))
    return task
