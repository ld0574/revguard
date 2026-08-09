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

    def export(self) -> dict:
        """导出该案件完整 Trace（平铺 span 列表 + 汇总）。"""
        spans = self.store.list_spans(self.case_id)
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
            "span_count": len(spans),
            "wall_duration_ms": root_duration,
            "span_duration_sum_ms": span_duration_sum,
            # 向后兼容；语义现为端到端墙钟耗时，不再重复累计嵌套 span。
            "total_duration_ms": root_duration,
            "error_spans": [s["span_id"] for s in spans if s["status"] == "ERROR"],
            "spans": spans,
        }
