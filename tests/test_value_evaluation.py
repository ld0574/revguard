from __future__ import annotations

import csv
import unittest
from pathlib import Path

from scripts.run_value_evaluation import evaluate_rows

ROOT = Path(__file__).resolve().parent.parent


class TestValueEvaluation(unittest.TestCase):
    def test_synthetic_report_cannot_be_presented_as_production_value(self):
        with (ROOT / "data/value_baseline/synthetic_demo.csv").open(
            encoding="utf-8", newline=""
        ) as handle:
            report = evaluate_rows(list(csv.DictReader(handle)))
        self.assertFalse(report["production_claim_allowed"])
        self.assertTrue(report["methodology_only"])
        self.assertEqual(report["case_count"], 8)
        self.assertGreater(
            report["metrics"]["median_manual_processing_minutes"],
            report["metrics"]["median_revguard_processing_minutes"],
        )
        self.assertEqual(report["metrics"]["median_minutes_saved_per_case"], 108.0)
        self.assertEqual(report["metrics"]["throughput_capacity_multiplier"], 6.5385)
        self.assertEqual(report["metrics"]["recovery_cost_avoided"], "35400.00")
        self.assertEqual(
            report["simulation_contract"]["default_assumptions"]["monthly_case_volume"],
            500,
        )
        self.assertIn("不是现金节省承诺", report["simulation_contract"]["claim_boundary"])

    def test_production_rows_require_source_reference(self):
        row = {
            "case_id": "CASE-1", "data_classification": "measured_production",
            "source_reference": "", "manual_processing_minutes": "10",
            "revguard_processing_minutes": "2", "wrong_payment_before": "false",
            "wrong_payment_after": "false", "recovery_cost_before": "0",
            "recovery_cost_after": "0", "manual_escalation_required": "false",
            "audit_exception_before": "false", "audit_exception_after": "false",
        }
        with self.assertRaisesRegex(ValueError, "来源引用"):
            evaluate_rows([row])


if __name__ == "__main__":
    unittest.main()
