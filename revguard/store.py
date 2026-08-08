"""SQLite 持久化层。

存储案件、证据、审批、执行、验证、审计事件与 Trace span。
设计约定：
- 复杂结构一律 JSON 序列化存储，字段含义以 models.py 为准；
- 所有写操作先记审计事件再改状态，保证「关键操作留痕」；
- 复赛可整体替换为 PostgreSQL/PolarDB（仅本文件需要改动）。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    case_id TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
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
    started_at TEXT NOT NULL,
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
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False：FastAPI/uvicorn 会在 worker 线程中复用本连接。
        # Demo 为单进程单实例部署，写操作均为短事务，线程安全风险可接受。
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ cases
    def save_case(self, case_dict: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cases(case_id, data, status, updated_at) VALUES (?,?,?,?)",
            (case_dict["case_id"], json.dumps(case_dict, ensure_ascii=False),
             case_dict["status"], utc_now()),
        )
        self.conn.commit()

    def get_case(self, case_id: str) -> dict | None:
        row = self.conn.execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_cases(self) -> list[dict]:
        rows = self.conn.execute("SELECT data FROM cases ORDER BY updated_at DESC").fetchall()
        return [json.loads(r["data"]) for r in rows]

    # --------------------------------------------------------------- evidence
    def save_evidence(self, ev: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO evidence(evidence_id, case_id, type, data) VALUES (?,?,?,?)",
            (ev["evidence_id"], ev["case_id"], ev["type"], json.dumps(ev, ensure_ascii=False)),
        )
        self.conn.commit()

    def list_evidence(self, case_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT data FROM evidence WHERE case_id=? ORDER BY rowid", (case_id,)
        ).fetchall()
        return [json.loads(r["data"]) for r in rows]

    # --------------------------------------------------------------- approval
    def save_approval(self, approval: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO approvals(approval_id, case_id, data) VALUES (?,?,?)",
            (approval["approval_id"], approval["case_id"], json.dumps(approval, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_approval(self, case_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT data FROM approvals WHERE case_id=? ORDER BY rowid DESC LIMIT 1", (case_id,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # -------------------------------------------------------------- execution
    def save_execution(self, exe: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO executions(action_id, case_id, idempotency_key, data) VALUES (?,?,?,?)",
            (exe["action_id"], exe["case_id"], exe.get("idempotency_key"),
             json.dumps(exe, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_execution_by_idempotency(self, key: str) -> dict | None:
        row = self.conn.execute(
            "SELECT data FROM executions WHERE idempotency_key=?", (key,)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    # ----------------------------------------------------------- verification
    def save_verification(self, case_id: str, result: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO verifications(case_id, data) VALUES (?,?)",
            (case_id, json.dumps(result, ensure_ascii=False)),
        )
        self.conn.commit()

    def get_verification(self, case_id: str) -> dict | None:
        row = self.conn.execute("SELECT data FROM verifications WHERE case_id=?", (case_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    # ------------------------------------------------------------------ audit
    def audit(self, case_id: str, actor: str, event: str, detail: dict | None = None) -> None:
        """写审计事件。所有关键动作（状态迁移、审批、执行、回滚）必须调用。"""
        self.conn.execute(
            "INSERT INTO audit_events(case_id, actor, event, detail, created_at) VALUES (?,?,?,?,?)",
            (case_id, actor, event,
             json.dumps(detail, ensure_ascii=False) if detail else None, utc_now()),
        )
        self.conn.commit()

    def list_audit(self, case_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM audit_events WHERE case_id=? ORDER BY seq", (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ trace
    def save_span(self, span: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO trace_spans
               (span_id, case_id, parent_span_id, kind, name, actor, status,
                started_at, duration_ms, inputs, outputs, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (span["span_id"], span["case_id"], span.get("parent_span_id"), span["kind"],
             span["name"], span.get("actor"), span["status"], span["started_at"],
             span.get("duration_ms"),
             json.dumps(span.get("inputs"), ensure_ascii=False, default=str),
             json.dumps(span.get("outputs"), ensure_ascii=False, default=str),
             span.get("error")),
        )
        self.conn.commit()

    def list_spans(self, case_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM trace_spans WHERE case_id=? ORDER BY started_at, span_id", (case_id,)
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["inputs"] = json.loads(d["inputs"]) if d["inputs"] else None
            d["outputs"] = json.loads(d["outputs"]) if d["outputs"] else None
            result.append(d)
        return result
