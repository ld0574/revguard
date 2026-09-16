#!/usr/bin/env python3
"""Capture or compare read-only evidence for a PolarDB PITR drill."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

TABLES = (
    "cases", "evidence", "approvals", "executions", "verifications",
    "agent_tasks", "agent_task_results", "audit_events", "trace_spans",
)


def _fingerprint(rows: list[dict]) -> str:
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str,
                     separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def capture(dsn: str) -> dict:
    try:
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
    except ImportError as exc:
        raise RuntimeError("缺少 psycopg 运行时依赖") from exc
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        identity = conn.execute(
            "SELECT current_database() AS database, current_user AS user, "
            "CURRENT_TIMESTAMP::text AS database_time"
        ).fetchone()
        counts = {
            table: int(conn.execute(
                sql.SQL("SELECT COUNT(*) AS count FROM {}").format(
                    sql.Identifier(table)
                )
            ).fetchone()["count"])
            for table in TABLES
        }
        audit_heads = conn.execute(
            """SELECT DISTINCT ON (case_id) case_id, seq, row_hash
                 FROM audit_events ORDER BY case_id, seq DESC"""
        ).fetchall()
        money_rows = conn.execute(
            """SELECT case_id, claim_actual_amount::text AS actual,
                      claim_expected_amount::text AS expected, currency
                 FROM cases ORDER BY case_id"""
        ).fetchall()
        chain = conn.execute(
            """WITH checked AS (
                   SELECT previous_hash, row_digest, row_hash,
                          COALESCE(LAG(row_hash) OVER (
                            PARTITION BY case_id ORDER BY seq
                          ), 'GENESIS') AS expected_previous,
                          encode(digest(previous_hash || ':' || row_digest,
                                        'sha256'), 'hex') AS expected_hash
                     FROM audit_events
               )
               SELECT COUNT(*) AS rows_checked,
                      COUNT(*) FILTER (
                        WHERE previous_hash <> expected_previous
                           OR row_hash <> expected_hash
                      ) AS broken_links
                 FROM checked"""
        ).fetchone()
        numeric_columns = conn.execute(
            """SELECT table_name, column_name, numeric_precision, numeric_scale
                 FROM information_schema.columns
                WHERE table_schema='public' AND data_type='numeric'
                ORDER BY table_name, column_name"""
        ).fetchall()
    return {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "database_identity": dict(identity),
        "table_counts": counts,
        "audit_chain": {
            "rows_checked": int(chain["rows_checked"]),
            "broken_links": int(chain["broken_links"]),
            "valid": int(chain["broken_links"]) == 0,
            "heads_fingerprint": _fingerprint([dict(row) for row in audit_heads]),
        },
        "money_fingerprint": _fingerprint([dict(row) for row in money_rows]),
        "numeric_columns": [dict(row) for row in numeric_columns],
    }


def comparable(evidence: dict) -> dict:
    return {key: value for key, value in evidence.items()
            if key not in {"captured_at", "database_identity", "verification"}}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected", type=Path)
    args = parser.parse_args()
    dsn = os.getenv("REVGUARD_RECOVERY_DATABASE_URL")
    if not dsn:
        raise SystemExit("必须通过 REVGUARD_RECOVERY_DATABASE_URL 注入只读连接")
    evidence = capture(dsn)
    if args.expected:
        expected = json.loads(args.expected.read_text(encoding="utf-8"))
        matches = comparable(evidence) == comparable(expected)
        evidence["verification"] = {
            "expected_file": str(args.expected),
            "matches_expected_restore_point": matches,
            "verdict": "PASSED" if matches else "FAILED",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    if evidence.get("verification", {}).get("verdict") == "FAILED":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
