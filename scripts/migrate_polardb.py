#!/usr/bin/env python3
"""Apply the core PolarDB schema with a dedicated migration credential."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.schema import core_schema, missing_tables


def main() -> int:
    dsn = os.getenv("REVGUARD_MIGRATION_DATABASE_URL")
    if not dsn:
        raise SystemExit("必须通过 REVGUARD_MIGRATION_DATABASE_URL 注入迁移连接，禁止把密码写入命令行")
    try:
        import psycopg
    except ImportError as exc:
        raise SystemExit("缺少 psycopg 运行时依赖") from exc
    with psycopg.connect(dsn) as conn:
        conn.execute(core_schema())
        absent = missing_tables(conn)
        if absent:
            raise RuntimeError("迁移后缺少必要表: " + ", ".join(absent))
    print("PolarDB schema applied: cases, tasks, append-only audit and money recovery ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
