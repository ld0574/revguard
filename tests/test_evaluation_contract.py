from __future__ import annotations

import importlib.util
import json
import statistics
import tempfile
import unittest
from pathlib import Path


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class EvaluationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parent.parent
        cls.evaluation = _load_module(
            "run_evaluation_for_test", cls.root / "scripts" / "run_evaluation.py"
        )
        cls.validator = _load_module(
            "validate_evaluation_for_test",
            cls.root / "scripts" / "validate_evaluation_snapshot.py",
        )

    def test_parallel_benchmark_reports_repetitions_and_medians(self):
        benchmark = self.evaluation.benchmark_parallel(iterations=3)
        self.assertEqual(3, benchmark["iterations"])
        self.assertEqual(
            int(statistics.median(benchmark["parallel_batch_ms_samples"])),
            benchmark["parallel_batch_ms"],
        )
        self.assertEqual(
            int(statistics.median(benchmark["end_to_end_ms_samples"])),
            benchmark["end_to_end_ms"],
        )

    def test_release_snapshot_uses_utc_and_deterministic_results(self):
        source = self.root / "docs" / "evaluation-summary.json"
        with tempfile.TemporaryDirectory(prefix="revguard-eval-contract-") as temp:
            copied = Path(temp) / "snapshot.json"
            copied.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            document = json.loads(copied.read_text(encoding="utf-8"))
        self.validator.validate_snapshot(document)


if __name__ == "__main__":
    unittest.main()
