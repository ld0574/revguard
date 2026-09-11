"""Durable local-ledger transactions and fenced, query-before-resume recovery.

This implements a self-owned ledger, not a distributed transaction over an ERP.
Every intent is committed before execution; effect, result, projection and audit
are committed together. An interrupted intent blocks its settlement channel.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager

from .models import utc_now

LOGGER = logging.getLogger("revguard.money")
MONEY_TOOLS = frozenset({
    "commission.submit_adjustment", "commission.reverse_adjustment",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS money_gateway_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton=1), data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_operations (
    operation_id TEXT PRIMARY KEY, case_id TEXT NOT NULL, channel TEXT NOT NULL,
    kind TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL,
    generation INTEGER NOT NULL DEFAULT 1, result TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_money_channel_status ON money_operations(channel,status);
CREATE TABLE IF NOT EXISTS money_ledger (
    ledger_id TEXT PRIMARY KEY, operation_id TEXT, reversal_of TEXT UNIQUE,
    case_id TEXT NOT NULL, order_id TEXT NOT NULL, currency TEXT NOT NULL,
    amount TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_outbox (
    event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL, data TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS money_holds (
    channel TEXT NOT NULL, case_id TEXT NOT NULL, reason TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY(channel,case_id)
);
"""


class RecoveryRequired(RuntimeError):
    """An unresolved financial effect must be reconciled, never blindly retried."""


def request_hash(tool: str, case_id: str, parameters: dict) -> str:
    def public(value):
        if isinstance(value, dict):
            return {k: public(v) for k, v in value.items()
                    if k not in {"approval_token", "rollback_token"}}
        if isinstance(value, list):
            return [public(v) for v in value]
        return value
    payload = [tool, case_id, public(parameters)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class MoneyTransaction:
    def __init__(self, conn, *, postgres: bool):
        self.conn = conn
        self.postgres = postgres

    def execute(self, sql: str, params=()):
        return self.conn.execute(sql.replace("?", "%s") if self.postgres else sql, params)

    def state(self) -> dict | None:
        row = self.execute("SELECT data FROM money_gateway_state WHERE singleton=1").fetchone()
        return json.loads(row["data"]) if row else None

    def save_state(self, state: dict) -> None:
        self.execute("UPDATE money_gateway_state SET data=? WHERE singleton=1",
                     (json.dumps(state, ensure_ascii=False),))

    def operation(self, key: str) -> dict | None:
        row = self.execute("SELECT * FROM money_operations WHERE operation_id=?", (key,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["result"] = json.loads(result["result"]) if result["result"] else None
        return result

    def audit(self, case_id: str, event: str, detail: dict, *, actor: str = "revguard-executor") -> None:
        sql = "INSERT INTO audit_events(case_id,actor,event,detail,created_at) VALUES (?,?,?,?,?)"
        if self.postgres:
            sql = sql.replace("VALUES (?,?,?,?,?)", "VALUES (?,?,?,?::jsonb,?)")
        self.execute(sql, (case_id, actor, event,
                           json.dumps(detail), utc_now()))

    def execution(self, record: dict) -> None:
        encoded = json.dumps(record, ensure_ascii=False)
        if self.postgres:
            self.execute("""INSERT INTO executions
                (action_id,case_id,idempotency_key,amount,currency,data,created_at)
                VALUES (?,?,?,?,?,?::jsonb,?) ON CONFLICT(action_id) DO UPDATE SET
                data=EXCLUDED.data,amount=EXCLUDED.amount,currency=EXCLUDED.currency""",
                (record["action_id"], record["case_id"], record["idempotency_key"],
                 record["amount"], record["currency"], encoded, utc_now()))
        else:
            self.execute("""INSERT INTO executions(action_id,case_id,idempotency_key,data)
                VALUES (?,?,?,?) ON CONFLICT(action_id) DO UPDATE SET data=excluded.data""",
                (record["action_id"], record["case_id"], record["idempotency_key"], encoded))

    def ledger(self, state: dict) -> None:
        for entry in state["ledger"]:
            self.execute("""INSERT INTO money_ledger
                (ledger_id,operation_id,reversal_of,case_id,order_id,currency,amount,data)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ledger_id) DO NOTHING""",
                (entry["ledger_id"], entry.get("operation_id"), entry.get("reversal_of"),
                 entry.get("source", ""), entry["order_id"], entry.get("currency", "KES"),
                 entry["amount"], json.dumps(entry)))

    def commit_result(self, operation: dict, result: dict, state: dict) -> None:
        key = operation["operation_id"]
        self.ledger(state)
        for execution in result.get("executions", []):
            self.execution(execution)
        self.save_state(state)
        self.execute("UPDATE money_operations SET status='COMMITTED',result=?,updated_at=? "
                     "WHERE operation_id=?", (json.dumps(result), utc_now(), key))
        detail = {"operation_id": key, "kind": operation["kind"],
                  "request_hash": operation["request_hash"],
                  "generation": operation["generation"]}
        self.audit(operation["case_id"], "MONEY_OPERATION_COMMITTED", detail)
        self.execute("INSERT INTO money_outbox(event_id,operation_id,data,created_at) VALUES (?,?,?,?)",
                     (key + ":committed", key, json.dumps(detail), utc_now()))


class MoneyJournal:
    def __init__(self, store):
        self.store = store
        self.postgres = store.backend == "postgresql-polardb"

    @contextmanager
    def transaction(self):
        # The lock is in the effect-owning database, not an API process lease.
        # A stale executor's generation is checked under this same lock.
        if self.postgres:
            with self.store._conn() as conn, conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(7491826301)")
                yield MoneyTransaction(conn, postgres=True)
        else:
            with self.store._lock:
                conn = self.store.conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield MoneyTransaction(conn, postgres=False)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise

    def initialize(self, state: dict) -> None:
        with self.transaction() as tx:
            tx.execute("INSERT INTO money_gateway_state(singleton,data) VALUES(1,?) "
                       "ON CONFLICT(singleton) DO NOTHING", (json.dumps(state),))
            # Import legacy JSON ledger entries at migration, before any new
            # money request. Existing databases always use their current state.
            tx.ledger(tx.state())

    @staticmethod
    def check_recovery_lock() -> None:
        # Set on every writer BEFORE starting a restored/PITR database. This
        # deliberately lives outside that database's possibly stale history.
        if os.getenv("REVGUARD_MONEY_RECOVERY_LOCK", "false").lower() == "true":
            raise RecoveryRequired("数据库恢复锁已开启，须完成恢复窗口对账后由运维解除")

    def prepare(self, key: str, case_id: str, channel: str, kind: str, digest: str) -> dict:
        self.check_recovery_lock()
        if not key:
            raise ValueError("资金操作必须携带稳定的operation/idempotency ID")
        with self.transaction() as tx:
            op = tx.operation(key)
            if op:
                if op["request_hash"] != digest or op["case_id"] != case_id:
                    raise ValueError("同一操作ID的业务参数不一致")
                if op["status"] == "COMMITTED":
                    return op
                if op["status"] != "NOT_COMMITTED":
                    raise RecoveryRequired("原操作结果待核对，禁止重发")
            hold = tx.execute("SELECT channel FROM money_holds WHERE channel=?", (channel,)).fetchone()
            pending = tx.execute("SELECT operation_id FROM money_operations "
                                 "WHERE channel=? AND status IN ('PREPARED','RESULT_UNKNOWN')",
                                 (channel,)).fetchone()
            if hold or pending:
                raise RecoveryRequired("资金通道存在未核对操作，已冻结新写入")
            if op:
                tx.execute("UPDATE money_operations SET status='PREPARED',updated_at=? "
                           "WHERE operation_id=?", (utc_now(), key))
            else:
                tx.execute("""INSERT INTO money_operations
                    (operation_id,case_id,channel,kind,request_hash,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'PREPARED',?,?)""",
                    (key, case_id, channel, kind, digest, utc_now(), utc_now()))
            return tx.operation(key)

    def resolve(self, key: str, case_id: str) -> dict | None:
        with self.transaction() as tx:
            op = tx.operation(key)
            if op and op["case_id"] != case_id:
                raise ValueError("操作与案件不匹配")
            return op

    def mark_unknown(self, key: str) -> None:
        try:
            with self.transaction() as tx:
                tx.execute("UPDATE money_operations SET status='RESULT_UNKNOWN',updated_at=? "
                           "WHERE operation_id=? AND status='PREPARED'", (utc_now(), key))
        except Exception:
            # PREPARED was committed before execution and blocks new writes even
            # if the database cannot persist RESULT_UNKNOWN during the outage.
            LOGGER.exception("money_outcome_unknown", extra={"revguard_fields": {"operation_id": key}})

    def reconcile(self, case_id: str) -> list[dict]:
        """Human-gated recovery of this self-owned database, on the primary.

        Obtaining the effect lock proves any earlier transaction has ended.
        Advancing generation invalidates requests paused between intent/effect.
        Never use this proof for an external ERP or after lossy PITR.
        """
        self.check_recovery_lock()
        with self.transaction() as tx:
            rows = tx.execute("SELECT operation_id FROM money_operations WHERE case_id=?",
                              (case_id,)).fetchall()
            operations = []
            epoch = (tx.state() or {}).get("recording_epochs", {}).get(case_id, 0)
            for row in rows:
                key = row["operation_id"]
                if (epoch and not key.endswith(f":recording-{epoch}")) or (not epoch and ":recording-" in key):
                    continue
                op = tx.operation(row["operation_id"])
                if op["status"] in {"PREPARED", "RESULT_UNKNOWN"}:
                    tx.execute("UPDATE money_operations SET status='NOT_COMMITTED',generation=generation+1,"
                               "updated_at=? WHERE operation_id=?", (utc_now(), op["operation_id"]))
                    op = tx.operation(op["operation_id"])
                if op["status"] == "COMMITTED":
                    for execution in (op["result"] or {}).get("executions", []):
                        # Only refresh execution projection from the latest
                        # gateway image, including any already committed reversal.
                        state = tx.state() or {}
                        current = state.get("execution_results", {}).get(execution["idempotency_key"], execution)
                        tx.execution(current)
                operations.append(op)
            tx.execute("DELETE FROM money_holds WHERE case_id=?", (case_id,))
            tx.audit(case_id, "MONEY_RECOVERY_RECONCILED", {
                "operations": [{"operation_id": o["operation_id"], "status": o["status"],
                                "generation": o["generation"]} for o in operations],
            })
            return operations

    def hold(self, channel: str, case_id: str, reason: str) -> None:
        with self.transaction() as tx:
            tx.execute("INSERT INTO money_holds(channel,case_id,reason,updated_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(channel,case_id) DO UPDATE SET reason=excluded.reason,updated_at=excluded.updated_at",
                       (channel, case_id, reason, utc_now()))

    def freeze_case(self, case_id: str) -> None:
        try:
            with self.transaction() as tx:
                rows = tx.execute("SELECT DISTINCT channel FROM money_operations WHERE case_id=?", (case_id,)).fetchall()
                for row in rows:
                    tx.execute("INSERT INTO money_holds(channel,case_id,reason,updated_at) VALUES(?,?,?,?) "
                               "ON CONFLICT(channel,case_id) DO NOTHING", (row["channel"], case_id, "RESULT_UNKNOWN", utc_now()))
        except Exception:
            LOGGER.exception("money_channel_freeze_pending", extra={"revguard_fields": {"case_id": case_id}})

    def metrics(self) -> dict:
        with self.transaction() as tx:
            unresolved = tx.execute("SELECT COUNT(*) AS n FROM money_operations WHERE status IN ('PREPARED','RESULT_UNKNOWN')").fetchone()["n"]
            frozen = tx.execute("SELECT COUNT(DISTINCT channel) AS n FROM money_holds").fetchone()["n"]
            return {"money_unresolved_operations": int(unresolved), "money_frozen_channels": int(frozen)}
