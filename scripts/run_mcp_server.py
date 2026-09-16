#!/usr/bin/env python3
"""Run one process-scoped RevGuard MCP server over standard stdio transport."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from mcp.server.stdio import stdio_server

from revguard.mcp_server import build_scoped_server
from revguard.mocks import ToolGateway
from revguard.store import create_store

ROOT = Path(__file__).resolve().parent.parent


def _required_actor() -> str:
    actor = os.getenv("REVGUARD_MCP_ACTOR", "").strip()
    if not actor:
        raise RuntimeError("必须通过 REVGUARD_MCP_ACTOR 配置进程绑定的 Worker")
    return actor


def _signing_key() -> str:
    key = os.getenv("REVGUARD_APPROVAL_SIGNING_KEY", "")
    insecure = os.getenv("REVGUARD_ALLOW_INSECURE_DEMO_KEYS", "false").lower() == "true"
    if not key and insecure:
        return "revguard-demo-signing-key-change-before-production-2026"
    if not key:
        raise RuntimeError("必须配置 REVGUARD_APPROVAL_SIGNING_KEY")
    return key


async def serve() -> None:
    db_path = os.getenv("REVGUARD_DB_PATH", str(ROOT / "data" / "revguard.db"))
    store = create_store(
        db_path,
        database_url=os.getenv("REVGUARD_DATABASE_URL"),
        read_database_url=os.getenv("REVGUARD_READ_DATABASE_URL"),
    )
    gateway = ToolGateway(
        os.getenv("REVGUARD_FIXTURES_DIR", str(ROOT / "data" / "fixtures")),
        finance_fail_times=int(os.getenv("REVGUARD_FINANCE_FAIL_TIMES", "1")),
        signing_key=_signing_key(),
        state_path=os.getenv(
            "REVGUARD_GATEWAY_STATE_PATH",
            str(Path(db_path).with_suffix(".gateway.json")),
        ),
        verification_tamper_amount=os.getenv("REVGUARD_VERIFICATION_TAMPER_AMOUNT", "0"),
    )
    server = build_scoped_server(actor=_required_actor(), store=store, gateway=gateway)
    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        store.close()


if __name__ == "__main__":
    asyncio.run(serve())
