from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER = ROOT / "scripts" / "check_audit_chain_export.py"
SCHEMA = "revguard.audit-chain/v1"


def make_rows(count: int = 3, start_seq: int = 1) -> list[dict]:
    """按库内触发器算法合成一段自洽的哈希链（不含任何真实数据）。"""
    rows: list[dict] = []
    previous = "GENESIS"
    for offset in range(count):
        seq = start_seq + offset
        digest = hashlib.sha256(f"synthetic-row-{seq}".encode()).hexdigest()
        current = hashlib.sha256(f"{previous}:{digest}".encode()).hexdigest()
        rows.append(
            {
                "seq": seq,
                "actor": "test-actor",
                "event": "TEST_EVENT",
                "detail": json.dumps({"seq": seq}),
                "created_at": "2026-09-18T00:00:00+00:00",
                "previous_hash": previous,
                "row_digest": digest,
                "row_hash": current,
            }
        )
        previous = current
    return rows


def make_document(rows: list[dict], start_seq: int | None = None) -> dict:
    window = [row for row in rows if start_seq is None or row["seq"] > start_seq]
    return {
        "schema": SCHEMA,
        "case_id": "CASE-0000-0000",
        "database_check": {"enforced": True, "valid": True, "rows_checked": len(rows), "broken_links": 0},
        "summary": {
            "rows": len(rows),
            "first_seq": rows[0]["seq"],
            "last_seq": rows[-1]["seq"],
            "head_hash": rows[-1]["row_hash"],
            "chain_ok": True,
            "first_broken_index": None,
        },
        "generation_window": {
            "recording_id": "REC-TEST",
            "start_seq": start_seq,
            "rows": len(window),
            "summary": {
                "rows": len(window),
                "first_seq": window[0]["seq"] if window else None,
                "last_seq": window[-1]["seq"] if window else None,
                "head_hash": window[-1]["row_hash"] if window else None,
                "chain_ok": True,
                "first_broken_index": None,
            },
        },
        "rows": rows,
    }


class AuditChainExportTests(unittest.TestCase):
    def run_checker(self, document: dict) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory(prefix="revguard-audit-chain-") as temp:
            path = Path(temp) / "audit-chain-case-0000-0000.json"
            path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(CHECKER), str(path)],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )

    def test_self_consistent_chain_passes(self):
        result = self.run_checker(make_document(make_rows(4), start_seq=2))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("复算通过", result.stdout)

    def test_tampered_row_hash_fails(self):
        document = make_document(make_rows(3))
        document["rows"][1]["row_hash"] = "0" * 64
        result = self.run_checker(document)
        self.assertEqual(1, result.returncode)
        self.assertIn("row_hash 复算不一致", result.stderr)

    def test_broken_previous_hash_link_fails(self):
        document = make_document(make_rows(3))
        document["rows"][2]["previous_hash"] = "GENESIS"
        result = self.run_checker(document)
        self.assertEqual(1, result.returncode)
        self.assertIn("previous_hash 断链", result.stderr)

    def test_summary_drift_fails(self):
        document = make_document(make_rows(3))
        document["summary"]["rows"] = 99
        result = self.run_checker(document)
        self.assertEqual(1, result.returncode)
        self.assertIn("summary.rows 与复算不一致", result.stderr)

    def test_missing_chain_verification_fails(self):
        document = make_document(make_rows(2))
        document["database_check"] = {"enforced": True, "valid": False, "rows_checked": 2, "broken_links": 1}
        result = self.run_checker(document)
        self.assertEqual(1, result.returncode)
        self.assertIn("库级链校验未通过", result.stderr)


if __name__ == "__main__":
    unittest.main()
