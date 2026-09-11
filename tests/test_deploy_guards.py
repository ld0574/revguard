"""Deployment must stop before changing settings when runs cannot be ruled out."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestDeployGuards(unittest.TestCase):
    def check_guard(self, *, state: str, reset: bool):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            script = root / "scripts/deploy_demo.sh"
            shutil.copyfile(ROOT / "scripts/deploy_demo.sh", script)
            config = root / ".env"
            original = "KEEP_EXISTING_SETTINGS=unchanged\n"
            config.write_text(original)
            binary = root / "bin"
            binary.mkdir()
            docker = binary / "docker"
            docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_DOCKER_LOG"
case "$1" in
  compose) [ "$2" = version ] && exit 0 ;;
  inspect) echo true; exit 0 ;;
  exec)
    [ "$TEST_RUN_STATE" = unknown ] && exit 42
    echo CASE-ANOTHER-ACTIVE; exit 0 ;;
esac
exit 97
''')
            docker.chmod(0o755)
            # Guards should run before these helpers are used, on hosts with
            # or without the real binaries installed in their verification image.
            for name in ("curl", "openssl"):
                helper = binary / name
                helper.write_text("#!/bin/sh\nexit 97\n")
                helper.chmod(0o755)
            log = root / "docker.log"
            result = subprocess.run(["bash", str(script), "--local", *(["--reset"] if reset else [])],
                env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                     "TEST_RUN_STATE": state, "TEST_DOCKER_LOG": str(log)},
                capture_output=True, text=True, timeout=10, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(config.read_text(), original)
            self.assertNotIn("up -d", log.read_text())
            self.assertNotIn("build", log.read_text())
            return result.stderr

    def test_reset_cannot_bypass_an_active_run(self):
        self.assertIn("活动 AgentTeams", self.check_guard(state="active", reset=True))

    def test_database_query_failure_cannot_be_treated_as_no_active_run(self):
        self.assertIn("无法核实", self.check_guard(state="unknown", reset=False))
