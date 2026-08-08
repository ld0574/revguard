"""Trace 记录器：覆盖 Agent / Skill / Tool / Rule / Approval / Execution / Verification span。

每个案件一条 Trace（trace_id = case_id），span 持久化到 Store，
可导出为 JSON 供回放与审计（设计文档 15.1）。
"""
from __future__ import annotations

import time
from contextlib import contextmanager

from .models import new_id, utc_now
from .store import Store


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
        self._stack: list[str] = []  # 当前 span 栈，用于 parent 关联

    @contextmanager
    def span(self, kind: str, name: str, *, actor: str = "", inputs: dict | None = None):
        record = {
            "span_id": new_id("SPAN"),
            "case_id": self.case_id,
            "parent_span_id": self._stack[-1] if self._stack else None,
            "kind": kind,
            "name": name,
            "actor": actor,
            "status": "OK",
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
            self._stack.pop()
            self.store.save_span(record)

    def export(self) -> dict:
        """导出该案件完整 Trace（平铺 span 列表 + 汇总）。"""
        spans = self.store.list_spans(self.case_id)
        return {
            "trace_id": self.case_id,
            "span_count": len(spans),
            "total_duration_ms": sum(s.get("duration_ms") or 0 for s in spans),
            "error_spans": [s["span_id"] for s in spans if s["status"] == "ERROR"],
            "spans": spans,
        }
