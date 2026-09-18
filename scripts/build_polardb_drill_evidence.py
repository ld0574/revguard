#!/usr/bin/env python3
"""Assemble verdicts and a manifest from raw P0 PolarDB drill artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--pitr-target-time", required=True)
    parser.add_argument("--failure-started-at", required=True)
    parser.add_argument("--write-restored-at", required=True)
    args = parser.parse_args()
    root = args.evidence_dir
    sync = load(root / "sync-before-fault.json")
    pitr = load(root / "pitr-marker-check.json")
    expected = load(root / "pitr-expected.json")
    actual = load(root / "pitr-actual.json")
    ha = load(root / "ha-after-failover.json")
    sync_state = ((sync.get("replication") or {}).get("sync_state"))
    pitr_markers = {row["marker"]: row for row in pitr.get("markers", [])}
    ha_markers = {row["marker"]: row for row in ha.get("markers", [])}
    pitr_passed = (
        "A" in pitr_markers
        and "B" not in pitr_markers
        and actual.get("verification", {}).get("verdict") == "PASSED"
        and bool(actual.get("audit_chain", {}).get("valid"))
    )
    ha_passed = (
        sync_state == "sync"
        and not bool(ha.get("in_recovery"))
        and "committed_before_failover" in ha_markers
        and "written_after_failover" in ha_markers
    )
    started = datetime.fromisoformat(args.failure_started_at.replace("Z", "+00:00"))
    restored = datetime.fromisoformat(args.write_restored_at.replace("Z", "+00:00"))
    rto_seconds = round((restored - started).total_seconds(), 3)
    ha_result = {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "verdict": "PASSED" if ha_passed and rto_seconds <= 60 else "FAILED",
        "synchronous_replication": {"sync_state_before_fault": sync_state},
        "failure_started_at": args.failure_started_at,
        "stable_endpoint_writable_at": args.write_restored_at,
        "rto_seconds": rto_seconds,
        "rpo": 0 if "committed_before_failover" in ha_markers else None,
        "promoted_node_in_recovery": ha.get("in_recovery"),
        "committed_marker_survived": "committed_before_failover" in ha_markers,
        "stable_endpoint_write_verified": "written_after_failover" in ha_markers,
        "limitations": [
            "Two containers and all volumes ran on one 10.10.10.202 host.",
            "This drill does not prove managed control-plane, cross-host, or cross-AZ SLA.",
        ],
    }
    pitr_result = {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "verdict": "PASSED" if pitr_passed else "FAILED",
        "target_time": args.pitr_target_time,
        "marker_A_present": "A" in pitr_markers,
        "marker_B_absent": "B" not in pitr_markers,
        "money_fingerprint_matches": actual.get("verification", {}).get("matches_expected_restore_point"),
        "audit_chain_valid": actual.get("audit_chain", {}).get("valid"),
        "basebackup_method": "checkpointed_localfs_shared_storage_snapshot_with_polar_initdb_replica",
        "limitations": [
            "Recovery writes to independent named volumes and does not replace the source nodes.",
            "The target reflects a synthetic RevGuard dataset in a single-host drill.",
        ],
    }
    (root / "ha-result.json").write_text(json.dumps(ha_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (root / "pitr-result.json").write_text(json.dumps(pitr_result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    members = [path for path in sorted(root.iterdir()) if path.is_file() and path.name != "SHA256SUMS.txt"]
    manifest = {
        "schema_version": "1.0",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "environment": "10.10.10.202 Docker isolated single-host drill",
        "ha_verdict": ha_result["verdict"],
        "pitr_verdict": pitr_result["verdict"],
        "members": [{"path": path.name, "sha256": sha256(path)} for path in members],
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    members = [path for path in sorted(root.iterdir()) if path.is_file() and path.name != "SHA256SUMS.txt"]
    (root / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in members), encoding="utf-8"
    )
    print(json.dumps({"ha": ha_result["verdict"], "pitr": pitr_result["verdict"]}))
    return 0 if ha_result["verdict"] == pitr_result["verdict"] == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
