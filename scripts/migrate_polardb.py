#!/usr/bin/env python3
"""Apply the core PolarDB schema with a dedicated migration credential."""
from __future__ import annotations

import os
from pathlib import Path


def main() -> int:
    dsn = os.getenv("REVGUARD_MIGRATION_DATABASE_URL")
    if not dsn:
        raise SystemExit("必须通过 REVGUARD_MIGRATION_DATABASE_URL 注入迁移连接，禁止把密码写入命令行")
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("缺少 psycopg 运行时依赖") from exc
    root = Path(__file__).resolve().parent.parent
    schema = "\n".join((root / "migrations/polardb" / name).read_text(encoding="utf-8")
                       for name in ("001_core.sql", "003_money_recovery.sql"))
    with psycopg.connect(dsn) as conn:
        conn.execute(schema)
        tables = conn.execute(
            "SELECT to_regclass('public.cases'), to_regclass('public.audit_events')"
        ).fetchone()
        if not all(tables):
            raise RuntimeError("迁移后核心表校验失败")
    print("PolarDB core schema applied: cases + append-only audit_events ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
