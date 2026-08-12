"""Case 状态机白名单、终态与审计不变量。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from revguard.models import Case, CaseStatus
from revguard.state_machine import InvalidStateTransition, transition_case
from revguard.store import Store


class TestCaseStateMachine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "state.db")
        self.case = Case("CASE-STATE", "TEST", "UNIT").to_dict()
        self.store.save_case(self.case)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_legal_transition_is_persisted_and_audited(self):
        transition_case(self.store, self.case, CaseStatus.NORMALIZING, "start")
        self.assertEqual(self.store.get_case("CASE-STATE")["status"], "NORMALIZING")
        event = self.store.list_audit("CASE-STATE")[-1]
        self.assertEqual(event["event"], "STATE_TRANSITION")
        detail = json.loads(event["detail"])
        self.assertEqual(detail["from"], "CREATED")
        self.assertEqual(detail["to"], "NORMALIZING")

    def test_illegal_jump_is_rejected_without_mutation(self):
        with self.assertRaises(InvalidStateTransition):
            transition_case(self.store, self.case, CaseStatus.EXECUTING, "skip controls")
        self.assertEqual(self.case["status"], "CREATED")
        self.assertEqual(self.store.get_case("CASE-STATE")["status"], "CREATED")
        self.assertEqual(self.store.list_audit("CASE-STATE"), [])

    def test_terminal_state_cannot_reopen(self):
        self.case["status"] = CaseStatus.CLOSED.value
        self.store.save_case(self.case)
        with self.assertRaises(InvalidStateTransition):
            transition_case(self.store, self.case, CaseStatus.NORMALIZING, "reopen")


if __name__ == "__main__":
    unittest.main()
