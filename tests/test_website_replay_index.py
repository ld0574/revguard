from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class WebsiteReplayIndexTests(unittest.TestCase):
    def test_published_index_matches_bundles_and_links(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "check_website_replay.py")],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("官网回放索引校验通过", result.stdout)

    def test_missing_bundle_fails_the_gate(self):
        with tempfile.TemporaryDirectory(prefix="revguard-replay-gate-") as temp:
            website = Path(temp) / "website"
            shutil.copytree(ROOT / "website", website)
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


if __name__ == "__main__":
    unittest.main()
