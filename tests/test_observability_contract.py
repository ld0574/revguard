import json
import tomllib
import unittest
from pathlib import Path

from revguard.observability import prometheus_text

ROOT = Path(__file__).resolve().parents[1]


class TestObservabilityContract(unittest.TestCase):
    def test_prometheus_exports_observability_metrics(self):
        text = prometheus_text({
            "cases_total": 8,
            "trace_spans_total": 10,
            "trace_error_spans_total": 1,
            "audit_events_total": 20,
            "agent_task_attempts_total": 16,
            "cases_by_status": {"CLOSED": 1},
            "agent_tasks_by_status": {"SUCCEEDED": 16},
            "audit_chain": {"enforced": True, "valid": True},
            "evidence_gaps_total": 2,
            "agent_model_calls_total": 4,
            "agent_model_input_tokens_total": 500,
            "agent_model_output_tokens_total": 80,
            "agent_model_timeouts_total": 1,
            "rollback_success_total": 1,
            "money_reversal_entries_total": 2,
            "read_replica_fallback_total": 3,
            "read_replica_healthy": True,
            "read_replica_fallback_active": False,
            "read_replica_lag_seconds": 0.02,
            "read_replica_lag_bytes": 0,
            "database_connections": 5,
            "database_lock_waits": 0,
            "money_operations_by_status": {"COMMITTED": 2, "RESULT_UNKNOWN": 1},
        })
        for metric in (
            "revguard_evidence_gaps_total 2",
            "revguard_agent_model_calls_total 4",
            "revguard_read_replica_healthy 1",
            "revguard_read_replica_lag_seconds 0.02",
            "revguard_database_connections 5",
            'revguard_money_operations_by_status{status="RESULT_UNKNOWN"} 1',
        ):
            self.assertIn(metric, text)

    def test_dashboard_covers_observability_areas(self):
        dashboard = json.loads((
            ROOT / "config/observability/grafana/dashboards/revguard.json"
        ).read_text())
        titles = {panel["title"] for panel in dashboard["panels"]}
        for fragment in (
            "ERPNext", "模型", "副本", "复制延迟", "数据库连接",
            "Evidence Gap", "资金操作状态", "冲销与恢复",
        ):
            self.assertTrue(any(fragment in title for title in titles), fragment)
        self.assertGreaterEqual(len(dashboard["panels"]), 20)

    def test_release_version_is_consistent_in_active_artifacts(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertEqual(metadata["project"]["version"], "0.6.0")
        for path in (
            ROOT / "Dockerfile",
            ROOT / "docker-compose.yml",
            ROOT / "docker-compose.dev.yml",
            ROOT / "revguard/api.py",
            ROOT / "website/index.html",
        ):
            text = path.read_text()
            self.assertIn("0.6.0", text, str(path))
            self.assertNotIn("0.6.0-rc", text, str(path))
        # 发布门禁：封版版本必须在 CHANGELOG 与导出的 OpenAPI 文档里同时出现。
        self.assertIn("## 0.6.0 — ", (ROOT / "CHANGELOG.md").read_text())
        openapi = json.loads((ROOT / "docs/openapi.json").read_text())
        self.assertEqual(openapi["info"]["version"], "0.6.0")

    def test_public_data_summary_keeps_real_and_synthetic_layers_separate(self):
        summary = json.loads((
            ROOT / "docs/public-data-experiment-summary.json"
        ).read_text())
        self.assertEqual(summary["metrics"]["total_transactions"], 10_000)
        self.assertEqual(summary["metrics"]["risk_cases"], 800)
        self.assertEqual(len(summary["anomaly_types"]), 10)
        self.assertEqual(summary["data_boundary"]["transaction"], "PUBLIC_REAL")
        self.assertEqual(summary["data_boundary"]["erp"], "LIVE_SYSTEM")
        self.assertEqual(summary["data_boundary"]["settlement"], "SYNTHETIC_DOMAIN")
        self.assertFalse(summary["data_boundary"]["historical_recalculation"])
        self.assertEqual(
            summary["erpnext"]["observed_counts"]["Sales Order"], 10_000
        )
        self.assertEqual(summary["erpnext"]["read_only_write_rejection_status"], 403)


if __name__ == "__main__":
    unittest.main()
