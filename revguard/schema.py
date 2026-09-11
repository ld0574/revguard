"""Required PostgreSQL migrations shared by deployment and application startup."""
from pathlib import Path

REQUIRED_MIGRATIONS = ("001_core.sql", "003_money_recovery.sql")
REQUIRED_TABLES = (
    "cases", "evidence", "approvals", "executions", "verifications",
    "agent_tasks", "agent_task_results", "audit_events", "trace_spans",
    "money_gateway_state", "money_operations", "money_ledger", "money_outbox", "money_holds",
)


def core_schema() -> str:
    directory = Path(__file__).resolve().parent.parent / "migrations" / "polardb"
    return "\n".join((directory / name).read_text(encoding="utf-8") for name in REQUIRED_MIGRATIONS)


def missing_tables(conn) -> list[str]:
    # Works with both the migration connection's tuples and Store's dict rows.
    absent = []
    for name in REQUIRED_TABLES:
        row = conn.execute("SELECT to_regclass(%s) AS relation", ("public." + name,)).fetchone()
        if (row["relation"] if isinstance(row, dict) else row[0]) is None:
            absent.append(name)
    return absent
