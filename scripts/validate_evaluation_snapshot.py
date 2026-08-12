"""校验提交版评测快照的确定性结果与记录方法，不比较波动耗时。"""
from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SNAPSHOT = ROOT / "docs" / "evaluation-summary.json"
EXPECTED_CATEGORIES = {
    "golden_e2e": 8,
    "risk_boundaries": 80,
    "policy_dates": 8,
    "security_probes": 9,
}


def validate_snapshot(document: dict) -> None:
    generated = datetime.fromisoformat(document["generated_at"].replace("Z", "+00:00"))
    if generated.utcoffset() != timedelta(0):
        raise ValueError("generated_at 必须是 UTC 时间")
    environment = document["environment"]
    for key in ("python", "implementation", "platform"):
        if not environment.get(key):
            raise ValueError(f"environment.{key} 缺失")
    if document["total_scenarios"] != 105 or document["passed"] != 105:
        raise ValueError("发布快照必须通过 105/105 场景")
    for name, expected in EXPECTED_CATEGORIES.items():
        category = document["categories"][name]
        if category["scenarios"] != expected or category["passed"] != expected:
            raise ValueError(f"{name} 必须通过 {expected}/{expected}")
        if category["failures"]:
            raise ValueError(f"{name} 含失败项")
    benchmark = document["parallel_benchmark"]
    if benchmark["method"] != "median_of_latency_injection_runs":
        raise ValueError("并行基准必须声明中位数方法")
    iterations = benchmark["iterations"]
    batch_samples = benchmark["parallel_batch_ms_samples"]
    wall_samples = benchmark["end_to_end_ms_samples"]
    if iterations < 1 or len(batch_samples) != iterations or len(wall_samples) != iterations:
        raise ValueError("并行基准重复次数与样本数不一致")
    if benchmark["parallel_batch_ms"] != int(statistics.median(batch_samples)):
        raise ValueError("parallel_batch_ms 不是样本中位数")
    if benchmark["end_to_end_ms"] != int(statistics.median(wall_samples)):
        raise ValueError("end_to_end_ms 不是样本中位数")


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 RevGuard 评测发布快照")
    parser.add_argument("snapshot", type=Path, nargs="?", default=DEFAULT_SNAPSHOT)
    args = parser.parse_args()
    validate_snapshot(json.loads(args.snapshot.read_text(encoding="utf-8")))
    print(f"verified {args.snapshot}: 105/105, UTC, median benchmark")


if __name__ == "__main__":
    main()
