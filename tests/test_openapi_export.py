from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class OpenAPIExportTests(unittest.TestCase):
    def test_openapi_31_contains_complete_skill_registry(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory(prefix="revguard-openapi-test-") as temp:
            output = Path(temp) / "openapi.json"
            subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "export_openapi.py"),
                    "--output",
                    str(output),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            document = json.loads(output.read_text(encoding="utf-8"))

        self.assertTrue(document["openapi"].startswith("3.1."))
        registry = document["x-revguard-skill-registry"]
        self.assertEqual(16, len(registry))
        self.assertIn("/api/v1/skills/{skill_name}/invoke", document["paths"])
        for skill in registry:
            self.assertNotIn("func", skill)
            self.assertRegex(skill["version"], r"^\d+\.\d+\.\d+$")
            self.assertIn("input_schema", skill)
            self.assertIn("output_schema", skill)
            self.assertIn("security", skill)
            self.assertTrue(skill["allowed_actors"])
            self.assertIn("input", skill["example"])


if __name__ == "__main__":
    unittest.main()
