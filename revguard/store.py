"""SQLite 持久化层。

存储案件、证据、审批、执行、验证、审计事件与 Trace span。
设计约定：
- 复杂结构一律 JSON 序列化存储，字段含义以 models.py 为准；
- 所有写操作先记审计事件再改状态，保证「关键操作留痕」；
- 复赛可整体替换为 PostgreSQL/PolarDB（仅本文件需要改动）。
"""
from __future__ import annotations

import base64
import binascii
import json
import sqlite3
from pathlib import Path
from threading import RLock

from .models import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cases_updated_cursor ON cases(updated_at DESC, case_id DESC);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    type TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_case ON evidence(case_id);
CREATE TABLE IF NOT EXISTS approvals (
    approval_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS executions (
    action_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS verifications (
    case_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    skill_name TEXT NOT NULL,
    assigned_actor TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_tasks_case ON agent_tasks(case_id, updated_at);
CREATE TABLE IF NOT EXISTS audit_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_events(case_id);
CREATE TABLE IF NOT EXISTS trace_spans (
    span_id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    parent_span_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    actor TEXT,
    status TEXT NOT NULL,
    sequence INTEGER,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_ms INTEGER,
    inputs TEXT,
    outputs TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_spans_case ON trace_spans(case_id);
"""


class Store:
    """对 SQLite 的轻量封装；每个方法即一个审计友好的读写单元。"""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._lock = RLock()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.executescript(_SCHEMA)
        columns = {r[1] for r in self.conn.execute("PRAGMA table_info(trace_spans)")}
        if "sequence" not in columns:
            self.conn.execute("ALTER TABLE trace_spans ADD COLUMN sequence INTEGER")
        if "ended_at" not in columns:
            self.conn.execute("ALTER TABLE trace_spans ADD COLUMN ended_at TEXT")
        self.conn.execute("UPDATE trace_spans SET sequence=rowid WHERE sequence IS NULL")
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def reset(self) -> None:
        """原子清空 Demo 运行状态，保留 Schema。"""
        with self._lock, self.conn:
            self.conn.executescript("""
                DELETE FROM trace_spans;
                DELETE FROM audit_events;
                DELETE FROM agent_tasks;
                DELETE FROM verifications;
                DELETE FROM executions;
                DELETE FROM approvals;
                DELETE FROM evidence;
                DELETE FROM cases;
            """)
            self.conn.execute("DELETE FROM sqlite_sequence WHERE name='audit_events'")

    # ------------------------------------------------------------------ cases
    def save_case(self, case_dict: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO cases(case_id, data, status, updated_at) VALUES (?,?,?,?)",
                (case_dict["case_id"], json.dumps(case_dict, ensure_ascii=False),
                 case_dict["status"], utc_now()),
            )

    def get_case(self, case_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_cases(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT data FROM cases ORDER BY updated_at DESC").fetchall()
        return [json.loads(r["data"]) for r in rows]

    @staticmethod
    def _encode_case_cursor(updated_at: str, case_id: str) -> str:
        raw = f"{updated_at}\0{case_id}".encode()
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_case_cursor(cursor: str) -> tuple[str, str]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = base64.urlsafe_b64decode(padded).decode("utf-8")
            updated_at, case_id = decoded.split("\0", 1)
            if not updated_at or not case_id:
                raise ValueError
            return updated_at, case_id
        except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
            raise ValueError("无效的 cases cursor") from exc

    def list_cases_page(self, *, limit: int = 50, cursor: str | None = None) -> dict:
        """按 updated_at/case_id 做稳定的 keyset 分页，避免全表装入内存。"""
        if not 1 <= limit <= 200:
            raise ValueError("limit 必须在 1..200 之间")
        if cursor:
            updated_at, case_id = self._decode_case_cursor(cursor)
            with self._lock:
                rows = self.conn.execute(
                    "SELECT case_id, data, updated_at FROM cases "
                    "WHERE updated_at < ? OR (updated_at = ? AND case_id < ?) "
                    "ORDER BY updated_at DESC, case_id DESC LIMIT ?",
                    (updated_at, updated_at, case_id, limit + 1),
                ).fetchall()
        else:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT case_id, data, updated_at FROM cases "
                    "ORDER BY updated_at DESC, case_id DESC LIMIT ?",
                    (limit + 1,),
                ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more and page:
            next_cursor = self._encode_case_cursor(page[-1]["updated_at"], page[-1]["case_id"])
        return {
            "cases": [json.loads(row["data"]) for row in page],
            "next_cursor": next_cursor,
        }

    def count_cases(self) -> int:
        with self._lock:
            row = self.conn.execute("SELECT COUNT(*) AS count FROM cases").fetchone()
        return int(row["count"])

    # --------------------------------------------------------------- evidence
    def save_evidence(self, ev: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO evidence(evidence_id, case_id, type, data) VALUES (?,?,?,?)",
                (ev["evidence_id"], ev["case_id"], ev["type"], json.dumps(ev, ensure_ascii=False)),
            )

    def list_evidence(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM evidence WHERE case_id=? ORDER BY rowid", (case_id,)
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # --------------------------------------------------------------- approval
    def save_approval(self, approval: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO approvals(approval_id, case_id, data) VALUES (?,?,?)",
                (approval["approval_id"], approval["case_id"], json.dumps(approval, ensure_ascii=False)),
            )

    def get_approval(self, case_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM approvals WHERE case_id=? ORDER BY rowid DESC LIMIT 1", (case_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    # -------------------------------------------------------------- execution
    def save_execution(self, exe: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO executions(action_id, case_id, idempotency_key, data) VALUES (?,?,?,?)",
                (exe["action_id"], exe["case_id"], exe.get("idempotency_key"),
                 json.dumps(exe, ensure_ascii=False)),
            )

    def get_execution_by_idempotency(self, key: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM executions WHERE idempotency_key=?", (key,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_executions(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM executions WHERE case_id=? ORDER BY rowid", (case_id,)
            ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # ----------------------------------------------------------- verification
    def save_verification(self, case_id: str, result: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO verifications(case_id, data) VALUES (?,?)",
                (case_id, json.dumps(result, ensure_ascii=False)),
            )

    def get_verification(self, case_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM verifications WHERE case_id=?", (case_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    # ------------------------------------------------------------- agent tasks
    def save_agent_task(self, task: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO agent_tasks
                   (task_id, case_id, skill_name, assigned_actor, status, data, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (task["task_id"], task["case_id"], task["skill_name"],
                 task["assigned_actor"], task["status"],
                 json.dumps(task, ensure_ascii=False), utc_now()),
            )

    def get_agent_task(self, task_id: str) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def list_agent_tasks(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT data FROM agent_tasks WHERE case_id=? ORDER BY rowid", (case_id,)
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def transition_agent_task(self, task_id: str, *, expected: set[str],
                              status: str, updates: dict | None = None) -> dict:
        """Atomically change a task only from an explicitly allowed status."""
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT data FROM agent_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                raise KeyError(task_id)
            task = json.loads(row["data"])
            if task["status"] not in expected:
                raise ValueError(
                    f"Agent task {task_id} 状态 {task['status']} 不允许转为 {status}"
                )
            task.update(updates or {})
            task["status"] = status
            task["updated_at"] = utc_now()
            self.conn.execute(
                "UPDATE agent_tasks SET status=?, data=?, updated_at=? WHERE task_id=?",
                (status, json.dumps(task, ensure_ascii=False), task["updated_at"], task_id),
            )
        return task

    # ------------------------------------------------------------------ audit
    def audit(self, case_id: str, actor: str, event: str, detail: dict | None = None) -> None:
        """写审计事件。所有关键动作（状态迁移、审批、执行、回滚）必须调用。"""
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO audit_events(case_id, actor, event, detail, created_at) VALUES (?,?,?,?,?)",
                (case_id, actor, event,
                 json.dumps(detail, ensure_ascii=False) if detail else None, utc_now()),
            )

    def list_audit(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM audit_events WHERE case_id=? ORDER BY seq", (case_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ trace
    def max_span_sequence(self, case_id: str) -> int:
        with self._lock:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) AS value FROM trace_spans WHERE case_id=?",
                (case_id,),
            ).fetchone()
        return int(row["value"])

    def save_span(self, span: dict) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                """INSERT OR REPLACE INTO trace_spans
                   (span_id, case_id, parent_span_id, kind, name, actor, status,
                    sequence, started_at, ended_at, duration_ms, inputs, outputs, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (span["span_id"], span["case_id"], span.get("parent_span_id"), span["kind"],
                 span["name"], span.get("actor"), span["status"], span.get("sequence"),
                 span["started_at"],
                 span.get("ended_at"), span.get("duration_ms"),
                 json.dumps(span.get("inputs"), ensure_ascii=False, default=str),
                 json.dumps(span.get("outputs"), ensure_ascii=False, default=str),
                 span.get("error")),
            )

    def list_spans(self, case_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT rowid AS trace_sequence, * FROM trace_spans "
                "WHERE case_id=? ORDER BY sequence, trace_sequence", (case_id,)
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["trace_sequence"] = d.get("sequence") or d["trace_sequence"]
            d["inputs"] = json.loads(d["inputs"]) if d["inputs"] else None
            d["outputs"] = json.loads(d["outputs"]) if d["outputs"] else None
            result.append(d)
        return result
