"""Exercise deployment CLI boundaries without a production Docker socket."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent


class TestDeployGuards(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "scripts").mkdir()
        for name in ("deploy_demo.sh", "quiesce_api.py"):
            shutil.copyfile(ROOT / "scripts" / name, self.root / "scripts" / name)
        (self.root / "revguard").mkdir()
        shutil.copyfile(ROOT / "revguard/deployment.py", self.root / "revguard/deployment.py")
        self.config = self.root / ".env"
        self.config.write_text("KEEP_EXISTING_SETTINGS=unchanged\n")
        binary = self.root / "bin"
        binary.mkdir()
        self.log = self.root / "docker.log"
        docker = binary / "docker"
        # Run actual Store/lease code in a child process. Only Docker transport
        # is replaced; unknown database state cannot become an idle recording.
        docker.write_text(f'''#!{sys.executable}
import json,os,subprocess,sys
args=sys.argv[1:]
with open(os.environ["TEST_DOCKER_LOG"],"a") as f:f.write(args[0]+"\\n")
if args[0]=="compose":
    if args[1]=="version":sys.exit(0)
    print(json.dumps({{"name":"workspace"}}));sys.exit(0)
if args[0]=="inspect":
    if os.environ.get("TEST_INSPECT_FAILED"):sys.exit(42)
    if os.environ.get("TEST_INSPECT_INVALID"):print("invalid");sys.exit(0)
    print(json.dumps([{{"Config":{{"Labels":{{"com.docker.compose.project":os.environ.get("TEST_PROJECT","workspace")}},"Env":["REVGUARD_DATABASE_URL="+os.environ.get("TEST_DSN","")]}},"State":{{"Running":True}}}}]));sys.exit(0)
if args[0]=="container":print("existing-api");sys.exit(0)
if args[0]=="exec":
    offset=args.index("python")
    sys.exit(subprocess.run([sys.executable,*args[offset+1:]]).returncode)
sys.exit(97)
''')
        docker.chmod(0o755)
        for name, output in (("curl", ""), ("openssl", "isolatedtestnonce")):
            helper = binary / name
            helper.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
            helper.chmod(0o755)
        self.env = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                    "TEST_DOCKER_LOG": str(self.log), "PYTHONPATH": str(ROOT),
                    "REVGUARD_DATABASE_URL": "", "REVGUARD_DB_PATH": str(self.root / "isolated.db"),
                    "REVGUARD_GATEWAY_STATE_PATH": str(self.root / "gateway.json"),
                    "REVGUARD_ALLOW_INSECURE_DEMO_KEYS": "true",
                    "REVGUARD_APPROVAL_SIGNING_KEY": "isolated-deployment-guard-test-signing-key"}

    def cli(self, *args):
        return subprocess.run(args, cwd=self.root, env=self.env, capture_output=True,
                              text=True, timeout=35, check=False)

    def test_topology_change_with_reset_rejected_before_config_edit(self):
        self.env["TEST_DSN"] = "postgresql://private-credentials"
        result = self.cli("bash", "scripts/deploy_demo.sh", "--local", "--reset")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("数据库拓扑", result.stderr)
        self.assertNotIn("private-credentials", result.stderr)
        self.assertEqual(self.config.read_text(), "KEEP_EXISTING_SETTINGS=unchanged\n")
        self.assertNotIn("run", self.log.read_text().splitlines())

    def test_other_compose_project_rejected_before_config_edit(self):
        self.env["TEST_PROJECT"] = "other"
        result = self.cli("bash", "scripts/deploy_demo.sh", "--local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("其他 Compose 项目", result.stderr)
        self.assertEqual(self.config.read_text(), "KEEP_EXISTING_SETTINGS=unchanged\n")

    def check_stop_denied(self):
        result = self.cli(sys.executable, "scripts/quiesce_api.py", "stop", "--profile", "local",
                          "--project", "workspace", "--owner", "isolatedtestowner")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("静止运行租约", result.stderr)
        self.assertNotIn("stop", self.log.read_text().splitlines())
        self.assertFalse((self.root / ".deployment-fence.json").exists())

    def test_active_run_prevents_container_stop(self):
        store = Store(self.env["REVGUARD_DB_PATH"])
        try:
            store.save_case({"case_id": "ACTIVE", "status": "CREATED",
                             "team_run": {"status": "RUNNING"}})
        finally:
            store.close()
        self.check_stop_denied()

    def test_database_query_failure_cannot_be_treated_as_no_active_run(self):
        Path(self.env["REVGUARD_DB_PATH"]).write_text("not a SQLite database")
        self.check_stop_denied()

    def test_invalid_inspect_is_not_treated_as_missing_container(self):
        self.env["TEST_INSPECT_INVALID"] = "true"
        result = self.cli("bash", "scripts/deploy_demo.sh", "--local")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.config.read_text(), "KEEP_EXISTING_SETTINGS=unchanged\n")

    def test_failed_inspect_of_existing_api_cannot_bypass_guard(self):
        self.env["TEST_INSPECT_FAILED"] = "true"
        result = self.cli("bash", "scripts/deploy_demo.sh", "--local")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker 部署检查失败", result.stderr)
        self.assertEqual(self.config.read_text(), "KEEP_EXISTING_SETTINGS=unchanged\n")
        self.assertNotIn("stop", self.log.read_text().splitlines())
