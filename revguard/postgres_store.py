"""PostgreSQL/PolarDB persistence adapter.

The primary pool owns every mutation and transactionally consistent lookup.
Analytics-heavy list, Trace playback and metrics queries can use a separately
configured read endpoint through ``REVGUARD_READ_DATABASE_URL``.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import utc_now
from .store import Store


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _money(value: object) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"金额无法转为 NUMERIC(18,2): {value!r}") from exc


class PostgresStore:
    """API-compatible Store backed by PostgreSQL/PolarDB connection pools."""

    backend = "postgresql-polardb"

    def __init__(self, database_url: str, *, read_database_url: str | None = None):
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - guarded by deployment deps
            raise RuntimeError(
                "PolarDB 模式需要 psycopg 与 psycopg-pool："
                "pip install 'psycopg[binary]' psycopg-pool"
            ) from exc

        pool_min = int(os.getenv("REVGUARD_DB_POOL_MIN", "1"))
        pool_max = int(os.getenv("REVGUARD_DB_POOL_MAX", "10"))
        if pool_min < 0 or pool_max < max(1, pool_min):
            raise ValueError("REVGUARD_DB_POOL_MIN/MAX 配置无效")
        kwargs = {"row_factory": dict_row, "connect_timeout": 5}
        self._write_pool = ConnectionPool(
            database_url, min_size=pool_min, max_size=pool_max,
            kwargs=kwargs, open=True,
        )
        self.read_replica_enabled = bool(
            read_database_url and read_database_url != database_url
        )
        self._read_pool = (
            ConnectionPool(
                read_database_url, min_size=pool_min, max_size=pool_max,
                kwargs=kwargs, open=True,
            )
            if self.read_replica_enabled else self._write_pool
        )
        if os.getenv("REVGUARD_AUTO_MIGRATE", "false").lower() == "true":
            self._apply_core_schema()
        else:
            self._validate_core_schema()

    def _apply_core_schema(self) -> None:
        with self._write_pool.connection() as conn:
            conn.execute(self._core_schema())

    @staticmethod
    def _core_schema() -> str:
        return (
            Path(__file__).resolve().parent.parent
            / "migrations" / "polardb" / "001_core.sql"
        ).read_text(encoding="utf-8")

    def _validate_core_schema(self) -> None:
        try:
            with self._write_pool.connection() as conn:
                row = conn.execute(
                    "SELECT to_regclass('public.cases') AS cases_table, "
                    "to_regclass('public.audit_events') AS audit_table"
                ).fetchone()
        except Exception:
            self.close()
            raise
        if not row["cases_table"] or not row["audit_table"]:
            self.close()
            raise RuntimeError(
                "PolarDB Schema 未就绪；请先用独立迁移账号运行 "
                "scripts/migrate_polardb.py。仅本地临时库可设 "
                "REVGUARD_AUTO_MIGRATE=true。"
            )

    @contextmanager
    def _conn(self, *, analytical: bool = False):
        pool = self._read_pool if analytical else self._write_pool
        with pool.connection() as conn:
            yield conn

    def close(self) -> None:
        if self._read_pool is not self._write_pool:
            self._read_pool.close()
        self._write_pool.close()

    def reset(self) -> None:
        if os.getenv("REVGUARD_ALLOW_DATABASE_RESET", "false").lower() != "true":
            raise RuntimeError(
                "PolarDB 正式审计库禁止 Demo reset；仅独立合成录制库可显式设置 "
                "REVGUARD_ALLOW_DATABASE_RESET=true"
            )
        # 录制库重置必须连同 append-only 审计链一起回到干净基线。
        # 使用独立 database owner，在同一事务中重建 public schema；生产应用
        # principal 不得拥有该权限，也不得打开上面的显式开关。
        with self._write_pool.connection() as conn:
            conn.execute("DROP SCHEMA public CASCADE")
            conn.execute("CREATE SCHEMA public")
            conn.execute(self._core_schema())

    def reset_case(self, case_id: str) -> None:
        """清理一个案件的可重跑产物，保留案件行和审计链。"""
        # Keep audit_events append-only: the new attempt is linked to the
        # previous terminal decision by DEMO_CASE_REPREPARED.  Child rows are
        # deleted in FK-safe order and the case itself is replaced by the
        # caller with a fresh Golden Case snapshot.
        with self._write_pool.connection() as conn, conn.transaction():
            for table in (
                "trace_spans", "agent_task_results", "agent_tasks",
                "verifications", "executions", "approvals", "evidence",
            ):
                conn.execute(f"DELETE FROM {table} WHERE case_id=%s", (case_id,))

    # ------------------------------------------------------------------ cases
    def save_case(self, case_dict: dict) -> None:
        claim = case_dict.get("claim") or {}
        created_at = case_dict.get("created_at") or utc_now()
        updated_at = case_dict.get("updated_at") or utc_now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO cases
                   (case_id, data, status, claim_actual_amount,
                    claim_expected_amount, currency, created_at, updated_at)
                   VALUES (%s,%s::jsonb,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (case_id) DO UPDATE SET
                     data=EXCLUDED.data, status=EXCLUDED.status,
                     claim_actual_amount=EXCLUDED.claim_actual_amount,
                     claim_expected_amount=EXCLUDED.claim_expected_amount,
                     currency=EXCLUDED.currency, updated_at=EXCLUDED.updated_at""",
                (case_dict["case_id"], _json(case_dict), case_dict["status"],
                 _money(claim.get("actual_amount")),
                 _money(claim.get("expected_amount")), claim.get("currency"),
                 created_at, updated_at),
            )

    def get_case(self, case_id: str) -> dict | None:
        # Transactional application reads stay on the primary.
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM cases WHERE case_id=%s", (case_id,)
            ).fetchone()
        return row["data"] if row else None

    def list_cases(self) -> list[dict]:
        with self._conn(analytical=True) as conn:
            rows = conn.execute(
                "SELECT data FROM cases ORDER BY updated_at DESC, case_id DESC"
            ).fetchall()
        return [row["data"] for row in rows]

    def list_cases_page(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1..200 之间")
        if cursor:
            updated_at, case_id = Store._decode_case_cursor(cursor)
            query = """SELECT case_id, data, updated_at::text AS updated_at
                         FROM cases
                        WHERE (updated_at, case_id) < (%s::timestamptz, %s)
                        ORDER BY updated_at DESC, case_id DESC LIMIT %s"""
            params: tuple = (updated_at, case_id, limit + 1)
        else:
            query = """SELECT case_id, data, updated_at::text AS updated_at
                         FROM cases
                        ORDER BY updated_at DESC, case_id DESC LIMIT %s"""
            params = (limit + 1,)
        with self._conn(analytical=True) as conn:
            rows = conn.execute(query, params).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = Store._encode_case_cursor(
                page[-1]["updated_at"], page[-1]["case_id"]
            )
        return {"cases": [row["data"] for row in page],
                "next_cursor": next_cursor}

    def count_cases(self) -> int:
        with self._conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM cases").fetchone()
        return int(row["count"])

    # --------------------------------------------------------------- evidence
    def save_evidence(self, ev: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO evidence
                   (evidence_id, case_id, type, data, collected_at)
                   VALUES (%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (evidence_id) DO UPDATE SET
                     case_id=EXCLUDED.case_id, type=EXCLUDED.type,
                     data=EXCLUDED.data, collected_at=EXCLUDED.collected_at""",
                (ev["evidence_id"], ev["case_id"], ev["type"], _json(ev),
                 ev.get("collected_at") or utc_now()),
            )

    def list_evidence(self, case_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM evidence WHERE case_id=%s "
                "ORDER BY collected_at, evidence_id", (case_id,),
            ).fetchall()
        return [row["data"] for row in rows]

    # --------------------------------------------------------------- approval
    def save_approval(self, approval: dict) -> None:
        created_at = (approval.get("created_at") or approval.get("decided_at")
                      or utc_now())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO approvals(approval_id, case_id, data, created_at)
                   VALUES (%s,%s,%s::jsonb,%s)
                   ON CONFLICT (approval_id) DO UPDATE SET
                     case_id=EXCLUDED.case_id, data=EXCLUDED.data""",
                (approval["approval_id"], approval["case_id"],
                 _json(approval), created_at),
            )

    def get_approval(self, case_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM approvals WHERE case_id=%s "
                "ORDER BY created_at DESC, approval_id DESC LIMIT 1", (case_id,),
            ).fetchone()
        return row["data"] if row else None

    # -------------------------------------------------------------- execution
    def save_execution(self, exe: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO executions
                   (action_id, case_id, idempotency_key, amount, currency, data, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (action_id) DO UPDATE SET
                     case_id=EXCLUDED.case_id,
                     idempotency_key=EXCLUDED.idempotency_key,
                     amount=EXCLUDED.amount, currency=EXCLUDED.currency,
                     data=EXCLUDED.data""",
                (exe["action_id"], exe["case_id"], exe.get("idempotency_key"),
                 _money(exe.get("amount")), exe.get("currency"), _json(exe),
                 exe.get("created_at") or utc_now()),
            )

    def get_execution_by_idempotency(self, key: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM executions WHERE idempotency_key=%s", (key,)
            ).fetchone()
        return row["data"] if row else None

    def list_executions(self, case_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM executions WHERE case_id=%s "
                "ORDER BY created_at, action_id", (case_id,),
            ).fetchall()
        return [row["data"] for row in rows]

    # ----------------------------------------------------------- verification
    def save_verification(self, case_id: str, result: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO verifications
                   (case_id, expected_amount, actual_amount, variance, data, created_at)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (case_id) DO UPDATE SET
                     expected_amount=EXCLUDED.expected_amount,
                     actual_amount=EXCLUDED.actual_amount,
                     variance=EXCLUDED.variance, data=EXCLUDED.data,
                     created_at=EXCLUDED.created_at""",
                (case_id, _money(result.get("expected_amount")),
                 _money(result.get("actual_amount")), _money(result.get("variance")),
                 _json(result), result.get("checked_at") or utc_now()),
            )

    def get_verification(self, case_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM verifications WHERE case_id=%s", (case_id,)
            ).fetchone()
        return row["data"] if row else None

    # ------------------------------------------------------------- agent tasks
    def save_agent_task(self, task: dict) -> None:
        created_at = task.get("created_at") or utc_now()
        updated_at = task.get("updated_at") or created_at
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO agent_tasks
                   (task_id, case_id, skill_name, assigned_actor, status, attempt,
                    data, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                   ON CONFLICT (task_id) DO UPDATE SET
                     status=EXCLUDED.status, attempt=EXCLUDED.attempt,
                     data=EXCLUDED.data, updated_at=EXCLUDED.updated_at""",
                (task["task_id"], task["case_id"], task["skill_name"],
                 task["assigned_actor"], task["status"], int(task.get("attempt", 0)),
                 _json(task), created_at, updated_at),
            )

    def get_agent_task(self, task_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=%s", (task_id,)
            ).fetchone()
        return row["data"] if row else None

    def list_agent_tasks(self, case_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM agent_tasks WHERE case_id=%s "
                "ORDER BY created_at, task_id", (case_id,),
            ).fetchall()
        return [row["data"] for row in rows]

    def transition_agent_task(self, task_id: str, *, expected: set[str],
                              status: str, updates: dict | None = None) -> dict:
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=%s FOR UPDATE", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError(task_id)
            task = row["data"]
            if task["status"] not in expected:
                raise ValueError(
                    f"Agent task {task_id} 状态 {task['status']} 不允许转为 {status}"
                )
            task.update(updates or {})
            if status == "RUNNING":
                task["attempt"] = int(task.get("attempt", 0)) + 1
            task["status"] = status
            task["updated_at"] = utc_now()
            conn.execute(
                "UPDATE agent_tasks SET status=%s, attempt=%s, data=%s::jsonb, "
                "updated_at=%s WHERE task_id=%s",
                (status, int(task.get("attempt", 0)), _json(task),
                 task["updated_at"], task_id),
            )
        return task

    def complete_agent_task(self, task_id: str, *, status: str,
                            result: dict | None = None,
                            skill_receipt: str | None = None,
                            error: dict | None = None) -> tuple[dict, dict]:
        if status not in {"SUCCEEDED", "FAILED_RETRYABLE", "FAILED_FINAL"}:
            raise ValueError(f"非法 StageResult 状态: {status}")
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=%s FOR UPDATE", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError(task_id)
            task = row["data"]
            if task["status"] != "RUNNING":
                raise ValueError(
                    f"Agent task {task_id} 状态 {task['status']} 不允许完成"
                )
            now = utc_now()
            task.update({"status": status, "result": result,
                         "skill_receipt": skill_receipt, "error": error,
                         "updated_at": now})
            attempt = int(task.get("attempt", 1))
            stage_result = {
                "result_id": f"RESULT-{task_id.removeprefix('TASK-')}-{attempt}",
                "task_id": task_id, "case_id": task["case_id"],
                "attempt": attempt, "status": status, "result": result,
                "skill_receipt": skill_receipt, "error": error,
                "created_at": now,
            }
            conn.execute(
                "UPDATE agent_tasks SET status=%s, data=%s::jsonb, updated_at=%s "
                "WHERE task_id=%s", (status, _json(task), now, task_id),
            )
            conn.execute(
                """INSERT INTO agent_task_results
                   (result_id, task_id, case_id, attempt, status, data, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
                (stage_result["result_id"], task_id, task["case_id"], attempt,
                 status, _json(stage_result), now),
            )
        return task, stage_result

    def list_agent_task_results(self, task_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT data FROM agent_task_results WHERE task_id=%s ORDER BY attempt",
                (task_id,),
            ).fetchall()
        return [row["data"] for row in rows]

    def replace_agent_task(self, old_task_id: str, replacement: dict, *,
                           actor: str, reason: str) -> tuple[dict, dict]:
        allowed = {"FAILED_RETRYABLE", "FAILED_FINAL", "CANCELLED"}
        with self._conn() as conn, conn.transaction():
            row = conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=%s FOR UPDATE",
                (old_task_id,),
            ).fetchone()
            if not row:
                raise KeyError(old_task_id)
            old = row["data"]
            if old["status"] not in allowed:
                raise ValueError(
                    f"Agent task {old_task_id} 状态 {old['status']} 不允许重派"
                )
            now = utc_now()
            old.update({"status": "CANCELLED", "updated_at": now,
                        "replaced_by_task_id": replacement["task_id"]})
            replacement.update({"supersedes_task_id": old_task_id,
                                "reassignment_reason": reason})
            conn.execute(
                "UPDATE agent_tasks SET status='CANCELLED', data=%s::jsonb, "
                "updated_at=%s WHERE task_id=%s", (_json(old), now, old_task_id),
            )
            conn.execute(
                """INSERT INTO agent_tasks
                   (task_id, case_id, skill_name, assigned_actor, status, attempt,
                    data, created_at, updated_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)""",
                (replacement["task_id"], replacement["case_id"],
                 replacement["skill_name"], replacement["assigned_actor"],
                 replacement["status"], int(replacement.get("attempt", 0)),
                 _json(replacement), replacement.get("created_at") or now, now),
            )
            self._audit_with_conn(conn, replacement["case_id"], actor,
                                  "AGENT_TASK_REASSIGNED", {
                                      "old_task_id": old_task_id,
                                      "new_task_id": replacement["task_id"],
                                      "reason": reason,
                                  })
        return old, replacement

    def cancel_open_agent_tasks(self, case_id: str, *, actor: str,
                                reason: str) -> list[str]:
        open_statuses = {"PENDING", "RUNNING", "WAITING_TOOL", "WAITING_HUMAN",
                         "FAILED_RETRYABLE"}
        cancelled: list[str] = []
        with self._conn() as conn, conn.transaction():
            rows = conn.execute(
                "SELECT task_id, data FROM agent_tasks WHERE case_id=%s FOR UPDATE",
                (case_id,),
            ).fetchall()
            now = utc_now()
            for row in rows:
                task = row["data"]
                if task["status"] not in open_statuses:
                    continue
                task.update({"status": "CANCELLED", "updated_at": now,
                             "cancellation_reason": reason})
                conn.execute(
                    "UPDATE agent_tasks SET status='CANCELLED', data=%s::jsonb, "
                    "updated_at=%s WHERE task_id=%s",
                    (_json(task), now, task["task_id"]),
                )
                cancelled.append(task["task_id"])
            if cancelled:
                self._audit_with_conn(conn, case_id, actor, "AGENT_TASKS_CANCELLED", {
                    "task_ids": cancelled, "reason": reason,
                })
        return cancelled

    # ------------------------------------------------------------------ audit
    @staticmethod
    def _audit_with_conn(conn, case_id: str, actor: str, event: str,
                         detail: dict | None = None) -> None:
        conn.execute(
            "INSERT INTO audit_events(case_id, actor, event, detail, created_at) "
            "VALUES (%s,%s,%s,%s::jsonb,%s)",
            (case_id, actor, event, _json(detail) if detail is not None else None,
             utc_now()),
        )

    def audit(self, case_id: str, actor: str, event: str,
              detail: dict | None = None) -> None:
        with self._conn() as conn:
            self._audit_with_conn(conn, case_id, actor, event, detail)

    def list_audit(self, case_id: str) -> list[dict]:
        with self._conn(analytical=True) as conn:
            rows = conn.execute(
                """SELECT seq, case_id, actor, event, detail::text AS detail,
                          created_at::text AS created_at, previous_hash,
                          row_digest, row_hash
                     FROM audit_events WHERE case_id=%s ORDER BY seq""",
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ trace
    def max_span_sequence(self, case_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM trace_spans "
                "WHERE case_id=%s", (case_id,),
            ).fetchone()
        return int(row["value"])

    def save_span(self, span: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO trace_spans
                   (span_id, case_id, parent_span_id, kind, name, actor, status,
                    sequence, started_at, ended_at, duration_ms, inputs, outputs, error)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                   ON CONFLICT (span_id) DO UPDATE SET
                     status=EXCLUDED.status, ended_at=EXCLUDED.ended_at,
                     duration_ms=EXCLUDED.duration_ms, outputs=EXCLUDED.outputs,
                     error=EXCLUDED.error""",
                (span["span_id"], span["case_id"], span.get("parent_span_id"),
                 span["kind"], span["name"], span.get("actor"), span["status"],
                 span.get("sequence"), span["started_at"], span.get("ended_at"),
                 span.get("duration_ms"), _json(span.get("inputs")),
                 _json(span.get("outputs")), span.get("error")),
            )

    def list_spans(self, case_id: str) -> list[dict]:
        with self._conn(analytical=True) as conn:
            rows = conn.execute(
                """SELECT span_id, case_id, parent_span_id, kind, name, actor,
                          status, sequence AS trace_sequence, sequence,
                          started_at::text AS started_at,
                          ended_at::text AS ended_at, duration_ms,
                          inputs, outputs, error
                     FROM trace_spans WHERE case_id=%s
                    ORDER BY sequence, started_at""",
                (case_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    # --------------------------------------------------------- health/metrics
    def readiness(self) -> dict:
        with self._conn() as conn:
            conn.execute("SELECT 1").fetchone()
        if self.read_replica_enabled:
            with self._conn(analytical=True) as conn:
                conn.execute("SELECT 1").fetchone()
        return {"ready": True, "backend": self.backend,
                "read_replica": self.read_replica_enabled}

    def verify_audit_chain(self) -> dict:
        with self._conn(analytical=True) as conn:
            row = conn.execute(
                """WITH checked AS (
                       SELECT previous_hash, row_digest, row_hash,
                              COALESCE(
                                LAG(row_hash) OVER (PARTITION BY case_id ORDER BY seq),
                                'GENESIS'
                              ) AS expected_previous,
                              encode(digest(previous_hash || ':' || row_digest,
                                            'sha256'), 'hex') AS expected_hash
                         FROM audit_events
                   )
                   SELECT COUNT(*) AS total,
                          COUNT(*) FILTER (
                            WHERE previous_hash <> expected_previous
                               OR row_hash <> expected_hash
                          ) AS broken
                     FROM checked"""
            ).fetchone()
        return {"enforced": True, "valid": int(row["broken"]) == 0,
                "rows_checked": int(row["total"]),
                "broken_links": int(row["broken"])}

    def operational_metrics(self) -> dict:
        with self._conn(analytical=True) as conn:
            engine_version = conn.execute(
                "SELECT version() AS version"
            ).fetchone()["version"]
            case_rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM cases GROUP BY status"
            ).fetchall()
            task_rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM agent_tasks GROUP BY status"
            ).fetchall()
            span_row = conn.execute(
                "SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE status='ERROR') "
                "AS errors FROM trace_spans"
            ).fetchone()
            audit_total = conn.execute(
                "SELECT COUNT(*) AS total FROM audit_events"
            ).fetchone()["total"]
            attempt_total = conn.execute(
                "SELECT COUNT(*) AS total FROM agent_task_results"
            ).fetchone()["total"]
        return {
            "storage_backend": self.backend,
            "database_engine_version": engine_version,
            "read_replica_enabled": self.read_replica_enabled,
            "cases_total": sum(int(row["count"]) for row in case_rows),
            "cases_by_status": {row["status"]: int(row["count"]) for row in case_rows},
            "agent_tasks_by_status": {
                row["status"]: int(row["count"]) for row in task_rows
            },
            "agent_task_attempts_total": int(attempt_total),
            "trace_spans_total": int(span_row["total"]),
            "trace_error_spans_total": int(span_row["errors"]),
            "audit_events_total": int(audit_total),
            "audit_chain": self.verify_audit_chain(),
        }
