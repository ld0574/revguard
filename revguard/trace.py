"""Trace 记录器：覆盖 Agent / Skill / Tool / Rule / Approval / Execution / Verification span。

每个案件一条 Trace（trace_id = case_id），span 持久化到 Store，
可导出为 JSON 供回放与审计（设计文档 15.1）。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import datetime
from threading import Lock, local

from .models import new_id, utc_now
from .security import redact_secrets
from .store import Store

OTEL_SEMANTIC_CONVENTIONS = {
    "name": "OpenTelemetry GenAI semantic conventions",
    "version": "1.43.0",
    "status": "development",
    "reference": "https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/",
}


def semantic_attributes(span: dict) -> dict:
    """把内部 span 投影为保守、可验证的 OpenTelemetry GenAI 属性。"""
    attributes = {
        "revguard.case.id": span["case_id"],
        "revguard.span.kind": span["kind"],
    }
    actor = span.get("actor")
    if actor:
        attributes["gen_ai.agent.name"] = actor
    if span["kind"] == "AGENT":
        attributes.update({
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.workflow.name": "revguard",
        })
    elif span["kind"] in {"SKILL", "TOOL", "REMOTE_TOOL"}:
        attributes.update({
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": span["name"],
            "gen_ai.tool.type": "function",
        })
    return attributes


class Tracer:
    """按案件记录 span。用法：

        tracer = Tracer(store, case_id)
        with tracer.span("SKILL", "CommissionCalculateSkill", actor="revguard-calculation", inputs={...}) as span:
            result = ...
            span["outputs"] = result
    """

    def __init__(self, store: Store, case_id: str):
        self.store = store
        self.case_id = case_id
        self._local = local()
        self._sequence_lock = Lock()
        self._next_sequence = store.max_span_sequence(case_id) + 1

    def _allocate_sequence(self) -> int:
        with self._sequence_lock:
            value = self._next_sequence
            self._next_sequence += 1
            return value

    @property
    def _stack(self) -> list[str]:
        if not hasattr(self._local, "stack"):
            self._local.stack = []
        return self._local.stack

    @property
    def current_span_id(self) -> str | None:
        return self._stack[-1] if self._stack else None

    @contextmanager
    def span(self, kind: str, name: str, *, actor: str = "", inputs: dict | None = None,
             parent_span_id: str | None = None):
        record = {
            "span_id": new_id("SPAN"),
            "case_id": self.case_id,
            "parent_span_id": (parent_span_id if parent_span_id is not None
                               else self.current_span_id),
            "kind": kind,
            "name": name,
            "actor": actor,
            "status": "OK",
            "sequence": self._allocate_sequence(),
            "started_at": utc_now(),
            "duration_ms": 0,
            "inputs": inputs,
            "outputs": None,
            "error": None,
        }
        self._stack.append(record["span_id"])
        start = time.monotonic()
        try:
            yield record
        except Exception as exc:
            # 失败也必须留 span，且状态/错误明确（不产出虚假成功）
            record["status"] = "ERROR"
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            record["duration_ms"] = int((time.monotonic() - start) * 1000)
            record["ended_at"] = utc_now()
            self._stack.pop()
            # Trace 是可导出的审计产物，只保存不可用于授权的指纹。
            self.store.save_span(redact_secrets(record))

    def record_completed_span(
        self,
        kind: str,
        name: str,
        *,
        actor: str = "",
        inputs: dict | None = None,
        outputs: dict | None = None,
        status: str = "OK",
        error: str | None = None,
        started_at: str,
        ended_at: str,
        duration_ms: int,
        parent_span_id: str | None = None,
    ) -> dict:
        """Persist a span timed by an external transport boundary.

        Matrix/LLM work is asynchronous and may complete through another HTTP
        request, so it cannot share the in-process context-manager stack used by
        Skill/Tool spans. The caller measures the real wall time; sequence is
        allocated only after the nested Skill spans have been committed, avoiding
        duplicate sequence numbers across independent Tracer instances.
        """
        record = {
            "span_id": new_id("SPAN"),
            "case_id": self.case_id,
            "parent_span_id": parent_span_id,
            "kind": kind,
            "name": name,
            "actor": actor,
            "status": status,
            "sequence": self._allocate_sequence(),
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": max(0, int(duration_ms)),
            "inputs": inputs,
            "outputs": outputs,
            "error": error,
        }
        safe_record = redact_secrets(record)
        self.store.save_span(safe_record)
        return safe_record

    def export(self) -> dict:
        """导出该案件完整 Trace（平铺 span 列表 + 汇总）。"""
        spans = self.store.list_spans(self.case_id)
        for span in spans:
            span["attributes"] = semantic_attributes(span)
        root_duration = sum(
            s.get("duration_ms") or 0 for s in spans if not s.get("parent_span_id")
        )
        # 报告可能在最外层 span 的 finally 之前生成；此时根 span 尚未入库。
        # 用已完成 span 的时间边界估算墙钟耗时，避免误报 0ms。
        if root_duration == 0 and spans:
            try:
                first = min(datetime.fromisoformat(s["started_at"]) for s in spans)
                last = max(datetime.fromisoformat(s["ended_at"]) for s in spans)
                root_duration = max(0, int((last - first).total_seconds() * 1000))
            except (KeyError, TypeError, ValueError):
                pass
        span_duration_sum = sum(s.get("duration_ms") or 0 for s in spans)
        return {
            "trace_id": self.case_id,
            "semantic_conventions": OTEL_SEMANTIC_CONVENTIONS,
            "span_count": len(spans),
            "wall_duration_ms": root_duration,
            "span_duration_sum_ms": span_duration_sum,
            # 向后兼容；语义现为端到端墙钟耗时，不再重复累计嵌套 span。
            "total_duration_ms": root_duration,
            "error_spans": [s["span_id"] for s in spans if s["status"] == "ERROR"],
            "spans": spans,
        }
