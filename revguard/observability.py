"""Low-cardinality metrics and structured access logging."""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line without request bodies or credentials."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = getattr(record, "revguard_fields", None)
        if fields:
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_structured_logging(logger: logging.Logger) -> None:
    if any(getattr(handler, "_revguard_json", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._revguard_json = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def prometheus_text(snapshot: dict) -> str:
    """Render the fixed metrics contract in Prometheus exposition format."""
    lines = [
        "# HELP revguard_cases_total Current persisted cases.",
        "# TYPE revguard_cases_total gauge",
        f"revguard_cases_total {int(snapshot['cases_total'])}",
        "# HELP revguard_trace_spans_total Persisted trace spans.",
        "# TYPE revguard_trace_spans_total counter",
        f"revguard_trace_spans_total {int(snapshot['trace_spans_total'])}",
        "# HELP revguard_trace_error_spans_total Trace spans in error state.",
        "# TYPE revguard_trace_error_spans_total counter",
        f"revguard_trace_error_spans_total {int(snapshot['trace_error_spans_total'])}",
        "# HELP revguard_audit_events_total Append-only audit events.",
        "# TYPE revguard_audit_events_total counter",
        f"revguard_audit_events_total {int(snapshot['audit_events_total'])}",
        "# HELP revguard_agent_task_attempts_total Persisted StageResult attempts.",
        "# TYPE revguard_agent_task_attempts_total counter",
        f"revguard_agent_task_attempts_total {int(snapshot['agent_task_attempts_total'])}",
    ]
    for status, count in sorted(snapshot["cases_by_status"].items()):
        lines.append(f'revguard_cases_by_status{{status="{status}"}} {int(count)}')
    for status, count in sorted(snapshot["agent_tasks_by_status"].items()):
        lines.append(f'revguard_agent_tasks_by_status{{status="{status}"}} {int(count)}')
    chain = snapshot.get("audit_chain") or {}
    if chain.get("enforced"):
        lines.extend([
            "# HELP revguard_audit_chain_valid Whether the database audit hash chain verifies.",
            "# TYPE revguard_audit_chain_valid gauge",
            f"revguard_audit_chain_valid {1 if chain.get('valid') else 0}",
        ])
    return "\n".join(lines) + "\n"
