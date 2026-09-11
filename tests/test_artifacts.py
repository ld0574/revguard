from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from revguard.artifacts import artifact_path, write_artifact


class TestArtifacts(unittest.TestCase):
    def test_disk_flush_failure_preserves_previous_complete_report(self):
        with tempfile.TemporaryDirectory() as directory:
            case = {"case_id": "CASE-TEST", "recording_id": "REC-TEST"}
            path = artifact_path(directory, case, ".md")
            write_artifact(path, "previous complete report")
            with patch("revguard.artifacts.os.fsync", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_artifact(path, "uncommitted partial report")
            self.assertEqual(path.read_text(), "previous complete report")
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_recording_identifiers_cannot_escape_artifact_root(self):
        with self.assertRaises(ValueError):
            artifact_path("/tmp/reports", {"case_id": "CASE-TEST", "recording_id": "../../private"}, ".md")
