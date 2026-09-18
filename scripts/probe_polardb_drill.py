"""Read-only probes used by the isolated PolarDB HA/PITR drill."""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=("sync", "pitr", "ha"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dsn = os.getenv("REVGUARD_RECOVERY_DATABASE_URL")
    if not dsn:
        raise SystemExit("REVGUARD_RECOVERY_DATABASE_URL is required")
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        common = {
            "captured_at": now(),
            "database_version": conn.execute("SELECT version() AS value").fetchone()["value"],
            "in_recovery": bool(conn.execute("SELECT pg_is_in_recovery() AS value").fetchone()["value"]),
        }
        if args.kind == "sync":
            row = conn.execute(
                """SELECT application_name, state, sync_state, sent_lsn::text,
                          write_lsn::text, flush_lsn::text, replay_lsn::text
                     FROM pg_stat_replication
                    WHERE application_name='standby1'"""
            ).fetchone()
            result = {**common, "replication": dict(row) if row else None}
        elif args.kind == "pitr":
            rows = conn.execute(
                "SELECT marker, value, created_at::text FROM revguard_pitr_markers ORDER BY marker"
            ).fetchall()
            result = {**common, "markers": [dict(row) for row in rows]}
        else:
            rows = conn.execute(
                "SELECT marker, value, created_at::text FROM revguard_ha_markers ORDER BY marker"
            ).fetchall()
            result = {**common, "markers": [dict(row) for row in rows]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
