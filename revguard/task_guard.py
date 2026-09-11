"""Bind an in-process Skill invocation to its durable Worker claim."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar


class StaleTaskExecution(ValueError):
    """A cancelled/recovered Worker must not invoke another tool effect."""


_CLAIM: ContextVar[dict | None] = ContextVar("worker_claim", default=None)


@contextmanager
def claimed_task(task: dict):
    token = _CLAIM.set({key: task[key] for key in ("task_id", "case_id", "attempt", "case_version")})
    try:
        yield
    finally:
        _CLAIM.reset(token)


def assert_active_claim(tx, case_id: str) -> None:
    """Called under the same database transaction lock that owns the effect."""
    claim = _CLAIM.get()
    if claim is None:
        return
    from .agent_bridge import case_version
    from .state_machine import locked_case
    from .workflow_persistence import locked_task

    case = locked_case(tx, case_id)
    task = locked_task(tx, claim["task_id"])
    if claim["case_id"] != case_id or not case or not task or (
        task["status"] != "RUNNING" or task.get("attempt") != claim["attempt"] or
        task.get("case_version") != claim["case_version"] or case_version(case) != claim["case_version"]
    ):
        raise StaleTaskExecution("任务已被取消或案件已恢复，拒绝旧 Worker 的工具调用")
