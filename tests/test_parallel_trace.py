"""真实并行证据采集、内容哈希与 Trace 错误语义评测。"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from threading import Lock
from typing import ClassVar

from revguard.mocks import ToolGateway
from revguard.skills import collect_evidence
from revguard.store import Store
from revguard.trace import Tracer

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures"


class SlowGateway(ToolGateway):
    """为并行评测注入确定性 I/O 等待，不改变工具结果。"""

    PARALLEL_TOOLS: ClassVar[frozenset[str]] = frozenset({
        "crm.get_order", "crm.get_partner_tier_history", "contract.get_contract",
        "finance.get_payment", "finance.get_refund", "finance.get_invoice",
        "finance.get_commission_ledger",
    })

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._inflight_lock = Lock()
        self._inflight = 0
        self.max_inflight = 0

    def call(self, tool_name, parameters, **kwargs):
        if tool_name in self.PARALLEL_TOOLS:
            with self._inflight_lock:
                self._inflight += 1
                self.max_inflight = max(self.max_inflight, self._inflight)
            try:
                time.sleep(0.08)
            finally:
                with self._inflight_lock:
                    self._inflight -= 1
        return super().call(tool_name, parameters, **kwargs)


class TestParallelEvidenceAndTrace(unittest.TestCase):
    def test_seven_independent_tools_run_concurrently(self):
        gateway = SlowGateway(FIXTURES, finance_fail_times=1)
        partner = gateway.fixtures["partners"][0]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "trace.db")
            tracer = Tracer(store, "CASE-PARALLEL")
            with tracer.span("SKILL", "EvidenceCollectSkill",
                             actor="revguard-evidence"):
                result = collect_evidence(
                    gateway, tracer, case_id="CASE-PARALLEL", partner=partner,
                    order_id="EZ202608001",
                )
            exported = tracer.export()
            store.close()

        # 直接观察同时在途调用，避免共享 CI 主机的调度抖动让固定毫秒阈值误报。
        self.assertGreaterEqual(gateway.max_inflight, 5)
        self.assertLess(result["parallel"]["duration_ms"], 800)
        self.assertEqual(result["parallel"]["task_count"], 7)
        self.assertEqual(result["evidence_score"], 1.0)
        self.assertTrue(all(e["content_hash"].startswith("sha256:")
                            for e in result["evidence"]))
        # 首次财务故障是结构化错误，也必须在 Trace 中显示为 ERROR 后重试成功。
        self.assertGreaterEqual(len(exported["error_spans"]), 1)
        sequences = [span["trace_sequence"] for span in exported["spans"]]
        self.assertEqual(sequences, sorted(sequences))
        self.assertGreater(exported["wall_duration_ms"], 0)
        self.assertLessEqual(exported["wall_duration_ms"], exported["span_duration_sum_ms"])
        self.assertEqual(exported["semantic_conventions"]["status"], "development")
        skill = next(span for span in exported["spans"] if span["kind"] == "SKILL")
        self.assertEqual(skill["attributes"]["gen_ai.operation.name"], "execute_tool")
        self.assertEqual(skill["attributes"]["gen_ai.tool.name"], "EvidenceCollectSkill")

    def test_programming_error_is_not_disguised_as_evidence_gap(self):
        class BrokenGateway(ToolGateway):
            def call(self, tool_name, parameters, **kwargs):
                if tool_name == "crm.get_order":
                    raise KeyError("programming-contract-bug")
                return super().call(tool_name, parameters, **kwargs)

        gateway = BrokenGateway(FIXTURES)
        with self.assertRaisesRegex(KeyError, "programming-contract-bug"):
            collect_evidence(
                gateway, None, case_id="CASE-BROKEN",
                partner=gateway.fixtures["partners"][0],
                order_id="EZ202608001",
            )


if __name__ == "__main__":
    unittest.main()
