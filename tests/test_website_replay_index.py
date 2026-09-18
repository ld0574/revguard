from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WebsiteReplayIndexTests(unittest.TestCase):
    def copy_website_fixture(self, temp: str) -> Path:
        root = Path(temp)
        shutil.copytree(ROOT / "website", root / "website")
        shutil.copy(ROOT / "pyproject.toml", root / "pyproject.toml")
        return root / "website"

    def test_published_index_matches_bundles_and_links(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_website_replay.py")],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("官网回放索引校验通过", result.stdout)

    def test_published_replay_release_matches_package_and_capture_is_explicit(self):
        index = json.loads((ROOT / "website" / "data" / "index.json").read_text(encoding="utf-8"))
        package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(index["release"], package["project"]["version"])
        self.assertEqual(index["capture"]["kind"], "CAPTURED_FROM_RUNTIME")
        self.assertTrue(index["capture"]["source_release"])

    def test_release_drift_fails_the_gate(self):
        with tempfile.TemporaryDirectory(prefix="revguard-replay-release-") as temp:
            shutil.copytree(ROOT, Path(temp) / "revguard")
            root = Path(temp) / "revguard"
            index_path = root / "website" / "data" / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["release"] = "0.6.0-rc3"
            index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(root / "scripts" / "check_website_replay.py")],
                cwd=root, check=False, capture_output=True, text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("release 与 pyproject.toml 不一致", result.stderr)

    def test_missing_bundle_fails_the_gate(self):
        with tempfile.TemporaryDirectory(prefix="revguard-replay-gate-") as temp:
            website = self.copy_website_fixture(temp)
            index_path = website / "data" / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["cases"][0]["file"] = "case-does-not-exist.json"
            index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_website_replay.py"), "--root", temp],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("数据包不存在", result.stderr)

    def test_relative_svg_share_card_fails_the_gate(self):
        """社交爬虫不解析相对 SVG：分享卡片退回 og-image.svg 必须让门禁失败。"""
        with tempfile.TemporaryDirectory(prefix="revguard-share-card-") as temp:
            website = self.copy_website_fixture(temp)
            index_path = website / "index.html"
            index_path.write_text(
                index_path.read_text(encoding="utf-8").replace(
                    "https://ld0574.github.io/revguard/og-image.png", "og-image.svg"
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_website_replay.py"), "--root", temp],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("og:image", result.stderr)

    def test_missing_share_card_png_fails_the_gate(self):
        with tempfile.TemporaryDirectory(prefix="revguard-share-png-") as temp:
            website = self.copy_website_fixture(temp)
            (website / "og-image.png").unlink()
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "check_website_replay.py"), "--root", temp],
                cwd=ROOT, check=False, capture_output=True, text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("og-image.png", result.stderr)


if __name__ == "__main__":
    unittest.main()
