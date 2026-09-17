from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_changelog.py"

BASE_CHANGELOG = """# Changelog

## Unreleased — 2026-09-18

- 待发布内容。

## 0.6.0-rc3 — 2026-09-18

- 候选版内容。

## 0.6.0-rc2 — 2026-09-17

- 上一个候选版。
"""


class ChangelogGateTests(unittest.TestCase):
    def run_gate(self, root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root), *extra],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )

    def make_tree(self, changelog: str, package_version: str = "0.6.0rc3",
                  openapi_version: str = "0.6.0-rc3",
                  pyproject_version: str = "0.6.0rc3") -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory(prefix="revguard-changelog-gate-")
        root = Path(temp.name)
        (root / "revguard").mkdir()
        (root / "docs").mkdir()
        (root / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
        (root / "revguard" / "__init__.py").write_text(
            f'"""fixture"""\n\n__version__ = "{package_version}"\n', encoding="utf-8")
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "revguard"\nversion = "{pyproject_version}"\n', encoding="utf-8")
        (root / "docs" / "openapi.json").write_text(
            json.dumps({"info": {"title": "RevGuard API", "version": openapi_version}}),
            encoding="utf-8")
        return temp

    def test_published_changelog_passes_the_gate(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=ROOT, check=False, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("CHANGELOG 校验通过", result.stdout)

    def test_missing_release_entry_fails(self):
        with self.make_tree(BASE_CHANGELOG, package_version="0.6.1") as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("不一致", result.stderr)

    def test_out_of_order_versions_fail(self):
        # 把第二条改成比首条更新的 rc4：文件顺序不再严格降序，门禁必须拒绝
        changelog = BASE_CHANGELOG.replace("## 0.6.0-rc2", "## 0.6.0-rc4")
        with self.make_tree(changelog) as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("降序", result.stderr)

    def test_duplicate_versions_fail(self):
        changelog = BASE_CHANGELOG + "\n## 0.6.0-rc2 — 2026-09-16\n\n- 重复条目。\n"
        with self.make_tree(changelog) as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("重复", result.stderr)

    def test_missing_date_fails(self):
        changelog = BASE_CHANGELOG.replace("## 0.6.0-rc3 — 2026-09-18", "## 0.6.0-rc3")
        with self.make_tree(changelog) as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("日期", result.stderr)

    def test_list_style_heading_fails(self):  # 不是二级标题的条目会被忽略
        changelog = BASE_CHANGELOG.replace("## 0.6.0-rc3 — 2026-09-18", "### 0.6.0-rc3 — 2026-09-18")
        with self.make_tree(changelog) as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)

    def test_pyproject_version_drift_fails(self):
        with self.make_tree(BASE_CHANGELOG, pyproject_version="0.6.0rc2") as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("pyproject.toml", result.stderr)

    def test_openapi_version_drift_fails(self):
        with self.make_tree(BASE_CHANGELOG, openapi_version="0.6.0-rc2") as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("openapi.json", result.stderr)

    def test_tag_mismatch_fails_and_match_passes(self):
        with self.make_tree(BASE_CHANGELOG) as temp:
            bad = self.run_gate(Path(temp), "--tag", "v0.6.0-rc2")
        self.assertNotEqual(0, bad.returncode)
        self.assertIn("tag", bad.stderr)
        with self.make_tree(BASE_CHANGELOG) as temp:
            good = self.run_gate(Path(temp), "--tag", "v0.6.0-rc3")
        self.assertEqual(0, good.returncode, good.stderr)

    def test_missing_unreleased_section_fails(self):
        changelog = BASE_CHANGELOG.replace("## Unreleased — 2026-09-18\n\n- 待发布内容。\n\n", "")
        with self.make_tree(changelog) as temp:
            result = self.run_gate(Path(temp))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Unreleased", result.stderr)


if __name__ == "__main__":
    unittest.main()
