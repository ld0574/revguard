#!/usr/bin/env python3
"""Run a bounded synthetic capacity probe and label its environment honestly."""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from revguard.models import Case  # noqa: E402
from revguard.store import Store  # noqa: E402


def percentile(samples: list[float], fraction: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction) - 1))
    return round(ordered[index], 3)


def run_probe(*, case_count: int, concurrency: int) -> dict:
    if not 1 <= case_count <= 10_000:
        raise ValueError("case_count 必须在 1..10000")
    if not 1 <= concurrency <= 200:
        raise ValueError("concurrency 必须在 1..200")
    with tempfile.TemporaryDirectory(prefix="revguard-capacity-") as temp:
        store = Store(Path(temp) / "capacity.db")

        def write_case(index: int) -> float:
            started = time.perf_counter()
            store.save_case(Case(
                case_id=f"CAPACITY-{index:06d}",
                case_type="COMMISSION_UNDERPAYMENT", source="SYNTHETIC_CAPACITY",
                claim={"actual_amount": "100.00", "expected_amount": "101.00",
                       "currency": "CNY"},
            ).to_dict())
            return (time.perf_counter() - started) * 1000

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            write_ms = list(executor.map(write_case, range(case_count)))
        elapsed = time.perf_counter() - started

        def read_page(_: int) -> float:
            read_started = time.perf_counter()
            page = store.list_cases_page(limit=min(50, case_count))
            if not page["cases"]:
                raise AssertionError("capacity probe returned an empty page")
            return (time.perf_counter() - read_started) * 1000

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            read_ms = list(executor.map(read_page, range(case_count)))
        backend = store.backend
        store.close()
    return {
        "schema_version": "1.0",
        "data_classification": "synthetic_local_capacity_probe",
        "production_slo_claim_allowed": False,
        "backend": backend,
        "environment": {"python": platform.python_version(),
                        "platform": platform.platform()},
        "load": {"cases": case_count, "concurrency": concurrency},
        "results": {
            "write_throughput_cases_per_second": round(case_count / elapsed, 2),
            "write_latency_ms": {
                "median": round(statistics.median(write_ms), 3),
                "p95": percentile(write_ms, 0.95),
                "max": round(max(write_ms), 3),
            },
            "keyset_page_latency_ms": {
                "median": round(statistics.median(read_ms), 3),
                "p95": percentile(read_ms, 0.95),
                "max": round(max(read_ms), 3),
            },
        },
        "guardrail": "本地 SQLite 合成探针只用于防止性能回归；PolarDB 容量结论必须在目标规格重测。",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=200)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    content = json.dumps(
        run_probe(case_count=args.cases, concurrency=args.concurrency),
        ensure_ascii=False, indent=2,
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
