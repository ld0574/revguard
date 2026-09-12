"""Cooperative request/background barrier for destructive recording actions.

PostgreSQL leases use a separate primary session, so waiting for Workers never
holds a pool slot or a write transaction. SQLite uses a stable sidecar flock.
This coordinates live application processes; it is not a database HA fence or
a substitute for the money journal's durable recovery checks.
"""
from __future__ import annotations

import fcntl
from contextvars import ContextVar
from pathlib import Path
from threading import Lock


class RuntimeBusy(RuntimeError):
    pass


class _Resource:
    def __init__(self, close):
        self.close = close
        self.references = 1
        self.lock = Lock()


class RuntimeLease:
    def __init__(self, resource: _Resource, *, exclusive: bool):
        self._resource = resource
        self.exclusive = exclusive
        self._closed = False

    def fork(self):
        with self._resource.lock:
            if self._closed or self.exclusive:
                raise RuntimeError("Only a live shared lease can cover background work")
            self._resource.references += 1
            return RuntimeLease(self._resource, exclusive=False)

    def close(self):
        with self._resource.lock:
            if self._closed:
                return
            self._closed = True
            self._resource.references -= 1
            if self._resource.references == 0:
                self._resource.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


CURRENT_LEASE: ContextVar[RuntimeLease | None] = ContextVar("runtime_lease", default=None)


def acquire_runtime_lease(store, *, exclusive: bool = False) -> RuntimeLease:
    """Try once; contention raises RuntimeBusy and connection errors propagate."""
    if store.backend == "postgresql-polardb":
        import psycopg

        conn = psycopg.connect(store._write_pool.conninfo, autocommit=True, connect_timeout=5)
        try:
            query = ("SELECT pg_try_advisory_lock(7491826302)" if exclusive else
                     "SELECT pg_try_advisory_lock_shared(7491826302)")
            if not conn.execute(query).fetchone()[0]:
                raise RuntimeBusy("运行中的请求或后台任务与录制重置互斥，请稍后重试")
        except BaseException:
            conn.close()
            raise
        return RuntimeLease(_Resource(conn.close), exclusive=exclusive)
    # Never unlink this sidecar on reset: all processes must lock the same inode.
    lock_path = Path(store.db_path).resolve().with_suffix(".runtime.lock")
    handle = lock_path.open("a+b")
    try:
        fcntl.flock(handle, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeBusy("运行中的请求或后台任务与录制重置互斥，请稍后重试") from exc
    except BaseException:
        handle.close()
        raise
    return RuntimeLease(_Resource(handle.close), exclusive=exclusive)


class RuntimeBarrierMiddleware:
    """Hold through the complete ASGI response, including cancellation cleanup."""
    def __init__(self, app, *, store_getter):
        self.app = app
        self.store_getter = store_getter

    async def __call__(self, scope, receive, send):
        from anyio import CancelScope
        from starlette.concurrency import run_in_threadpool
        from starlette.responses import JSONResponse

        path = scope.get("path", "").rstrip("/")
        if scope["type"] != "http" or not path.startswith("/api/") or path in {
            "/api/v1/health/live", "/api/v1/ops/observability",
        }:
            return await self.app(scope, receive, send)
        exclusive = scope["method"] == "POST" and (
            path == "/api/v1/demo/reset" or
            (path.startswith("/api/v1/cases/") and path.endswith("/reprepare"))
        )
        try:
            with CancelScope(shield=True):
                lease = await run_in_threadpool(
                    acquire_runtime_lease, self.store_getter(), exclusive=exclusive,
                )
        except Exception as exc:  # noqa: BLE001 -- fail closed; no connection details in HTTP errors
            busy = isinstance(exc, RuntimeBusy)
            response = JSONResponse(status_code=409 if busy else 503, content={"detail": {
                "code": "RECORDING_RUNTIME_BUSY" if busy else "RUNTIME_GUARD_UNAVAILABLE",
                "message": str(exc) if busy else "运行保护暂不可用，请稍后重试",
            }}, headers={"Cache-Control": "no-store"})
            return await response(scope, receive, send)
        token = CURRENT_LEASE.set(lease)
        try:
            # Check under the lease: a request dispatched before deployment
            # must also observe a fence installed before its lease was acquired.
            from .deployment import deployment_pending
            try:
                maintenance = scope["method"] not in {"GET", "HEAD", "OPTIONS"} and deployment_pending()
            except OSError:
                maintenance = True
            if maintenance:
                response = JSONResponse(status_code=503, content={"detail": {
                    "code": "DEPLOYMENT_MAINTENANCE",
                    "message": "部署维护中，暂不接受业务操作，请稍后重试。",
                }}, headers={"Cache-Control": "no-store", "Retry-After": "30"})
                return await response(scope, receive, send)
            await self.app(scope, receive, send)
        finally:
            CURRENT_LEASE.reset(token)
            with CancelScope(shield=True):
                await run_in_threadpool(lease.close)


def assert_recording_quiescent(store, journal, *, case_id: str | None = None) -> None:
    """Check durable primary state while the caller holds an exclusive lease."""
    journal.check_recovery_lock()
    with journal.transaction() as tx:
        query = "SELECT data FROM cases WHERE case_id=?" if case_id else "SELECT data FROM cases"
        params = (case_id,) if case_id else ()
        cases = tx.execute(query, params).fetchall()
        import json
        for row in cases:
            case = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
            if (case.get("team_run") or {}).get("status") in {"QUEUED", "STARTING", "RUNNING"} or case.get("status") in {
                "READY_TO_EXECUTE", "EXECUTING", "VERIFYING", "ROLLBACK_REQUIRED", "RECOVERY_REQUIRED",
            }:
                raise RuntimeBusy("案件仍在运行或等待恢复，须先完成运行和对账")
        conditions = (
            ("money_holds", "1=1"),
            ("money_operations", "status IN ('PREPARED','RESULT_UNKNOWN')"),
            ("agent_tasks", "status IN ('RUNNING','WAITING_TOOL')"),
        )
        for table, condition in conditions:
            query = f"SELECT 1 FROM {table} WHERE {condition}"  # nosec B608 -- literal allowlist
            if case_id:
                query += " AND case_id=?"
            if tx.execute(query + " LIMIT 1", params).fetchone():
                raise RuntimeBusy("存在未完成任务或待对账资金，不能清理运行现场")
