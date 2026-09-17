"""Skill 三级摘要：manifest / instruction / callable 的稳定性与 fail-closed 行为。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from revguard import skills
from revguard.mocks import ToolGateway
from revguard.skill_integrity import (
    LEVELS,
    SkillIntegrityError,
    assert_registry_integrity,
    baseline,
    catalog,
    diff_against_baseline,
    digest_of,
    instruction_of,
    manifest_of,
    normalize_source,
    skill_digests,
)
from revguard.skill_runtime import invoke_skill
from revguard.store import Store
from scripts.seed_demo import seed

ROOT = Path(__file__).resolve().parent.parent


class TestSkillIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = skills.SKILL_REGISTRY
        self.catalog = catalog(self.registry)

    def test_catalog_covers_every_registered_skill_with_three_levels(self):
        self.assertEqual(set(self.catalog), set(self.registry))
        self.assertTrue(len(self.catalog) >= 16)
        for name, entry in self.catalog.items():
            with self.subTest(skill=name):
                self.assertEqual(entry["version"], self.registry[name]["version"])
                for level in LEVELS:
                    self.assertTrue(entry[level].startswith("sha256:"), level)

    def test_manifest_digest_ignores_dict_order_only(self):
        meta = self.registry["ApprovalRouteSkill"]
        reordered = {key: meta[key] for key in reversed(list(meta))}
        self.assertEqual(digest_of(manifest_of(meta)), digest_of(manifest_of(reordered)))
        drifted = dict(meta, version="9.9.9")
        self.assertNotEqual(digest_of(manifest_of(meta)), digest_of(manifest_of(drifted)))

    def test_instruction_digest_tracks_schema_changes(self):
        meta = self.registry["LedgerAdjustSkill"]
        original = digest_of(instruction_of(meta))
        mutated = dict(meta, input_schema=json.loads(json.dumps(meta["input_schema"])))
        mutated["input_schema"]["properties"]["new_field"] = {"type": "string"}
        self.assertNotEqual(original, digest_of(instruction_of(mutated)))

    def test_source_normalization_ignores_reindentation(self):
        body = "def f():\n    return 1\n"
        self.assertEqual(normalize_source(body), normalize_source("    " + body.replace("\n", "\n    ")))
        self.assertNotEqual(normalize_source(body), normalize_source("def f():\n    return 2\n"))

    def test_callable_digest_changes_when_implementation_is_swapped(self):
        meta = dict(self.registry["CaseNormalizeSkill"])
        other = dict(meta, func=skills.collect_evidence)
        self.assertNotEqual(
            skill_digests("CaseNormalizeSkill", meta)["callable"],
            skill_digests("CaseNormalizeSkill", other)["callable"],
        )

    def test_diff_reports_unregistered_and_removed_skills(self):
        pinned = baseline(self.catalog)
        pinned["skills"].pop("CaseNormalizeSkill")
        pinned["skills"]["GhostSkill"] = dict(next(iter(self.catalog.values())))
        problems = diff_against_baseline(self.catalog, pinned)
        reasons = {(item["skill"], item["reason"]) for item in problems}
        self.assertIn(("CaseNormalizeSkill", "unregistered"), reasons)
        self.assertIn(("GhostSkill", "baseline-only"), reasons)

    def test_missing_baseline_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(SkillIntegrityError):
                assert_registry_integrity(self.registry, Path(tmp) / "absent.json")

    def test_registry_matches_committed_baseline(self):
        """真实门禁：仓库内置基线与当前实现必须完全一致。"""
        live = assert_registry_integrity(self.registry)
        self.assertEqual(set(live), set(self.catalog))

    def test_skill_execution_audit_carries_three_level_digests(self):
        """每次真实 Skill 调用都把三级摘要写进审计，运行记录可回溯到具体实现。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "integrity.db"
            seed(str(db), quiet=True)
            store = Store(db)
            gateway = ToolGateway(ROOT / "data" / "fixtures")
            case = store.get_case("CASE-2026-0001")
            result = invoke_skill(
                "CaseNormalizeSkill", {"raw_case": case}, actor="revguard-intake",
                case_id=case["case_id"], gateway=gateway, store=store,
            )
            self.assertTrue(result["success"])
            events = [item for item in store.list_audit(case["case_id"])
                      if item.get("event") == "SKILL_INVOKED"]
            detail = events[-1]["detail"]
            if isinstance(detail, str):
                detail = json.loads(detail)
            recorded = detail["skill_integrity"]
            expected = skill_digests("CaseNormalizeSkill", self.registry["CaseNormalizeSkill"])
            for level in LEVELS:
                self.assertEqual(recorded[level], expected[level], level)
            store.close()

    def test_baseline_round_trip_is_self_consistent(self):
        pinned = baseline(self.catalog)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill-integrity.json"
            path.write_text(json.dumps(pinned), encoding="utf-8")
            self.assertEqual(diff_against_baseline(self.catalog, pinned), [])
            assert_registry_integrity(self.registry, path)


if __name__ == "__main__":
    unittest.main()
