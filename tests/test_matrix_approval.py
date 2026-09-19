from __future__ import annotations

import time
import unittest

from revguard.matrix_approval import (
    MatrixApprovalBridge,
    PendingApprovalRequest,
    build_approval_request_message,
    parse_approval_reply,
)
from revguard.matrix_team import MatrixSettings


class TestMatrixApprovalProtocol(unittest.TestCase):
    def setUp(self) -> None:
        self.pending = PendingApprovalRequest(
            case_id="CASE-2026-0008",
            approval_id="APR-8",
            request_event_id="$request-8",
            expires_at=2_000,
        )

    def reply(self, body: str, *, event_id: str = "$reply-8", sender: str = "@finance:test") -> dict:
        return {
            "type": "m.room.message",
            "event_id": event_id,
            "sender": sender,
            "origin_server_ts": 1_500_000,
            "content": {
                "msgtype": "m.text",
                "body": body,
                "m.relates_to": {"m.in_reply_to": {"event_id": "$request-8"}},
            },
        }

    def test_only_direct_allow_listed_reply_is_a_decision(self):
        parsed = parse_approval_reply(
            self.reply("批准：证据完整"),
            room_id="!team:test",
            pending_requests={"$request-8": self.pending},
            allowed_senders={"@finance:test"},
            now=1_000,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.decision, "APPROVED")
        self.assertEqual(parsed.comment, "证据完整")
        self.assertEqual(parsed.case_id, "CASE-2026-0008")
        self.assertEqual(parsed.binding_mode, "reply")

        fallback = parse_approval_reply(
            self.reply("> <@revguard-bot:test> REVGUARD_HUMAN_APPROVAL_REQUEST\n\n批准"),
            room_id="!team:test",
            pending_requests={"$request-8": self.pending},
            allowed_senders={"@finance:test"},
            now=1_000,
        )
        self.assertIsNotNone(fallback)

    def test_single_pending_plain_decision_is_bound_without_reply_relation(self):
        event = self.reply("批准")
        event["content"].pop("m.relates_to")
        parsed = parse_approval_reply(
            event,
            room_id="!team:test",
            pending_requests={"$request-8": self.pending},
            allowed_senders={"@finance:test"},
            allow_unthreaded_fallback=True,
            now=1_000,
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.request_event_id, "$request-8")
        self.assertEqual(parsed.binding_mode, "single_pending_direct")

    def test_rejection_requires_a_reason_and_exact_reply_anchor(self):
        for body, event in (
            ("驳回", self.reply("驳回")),
            ("批准", {**self.reply("批准"), "content": {"msgtype": "m.text", "body": "批准"}}),
            ("批准", self.reply("批准", sender="@unknown:test")),
        ):
            with self.subTest(body=body):
                self.assertIsNone(parse_approval_reply(
                    event,
                    room_id="!team:test",
                    pending_requests={"$request-8": self.pending},
                    allowed_senders={"@finance:test"},
                    allow_unthreaded_fallback=False,
                    now=1_000,
                ))

    def test_expired_request_is_ignored(self):
        self.assertIsNone(parse_approval_reply(
            self.reply("批准"),
            room_id="!team:test",
            pending_requests={"$request-8": self.pending},
            allowed_senders={"@finance:test"},
            now=2_001,
        ))

    def test_request_message_does_not_expose_capability_data(self):
        message = build_approval_request_message(
            {
                "case_id": "CASE-2026-0008",
                "recording_id": "REC-8",
                "risk_level": "L2",
                "partner_name": "Nairobi Solar Solutions Ltd",
                "partner_id": "AGT-10001",
                "order_id": "EZ202608001",
                "description": "订单已完成回款，但佣金少算。",
                "claim": {"actual_amount": 18000, "expected_amount": 27000, "currency": "KES"},
                "facts": {"agent_tier": "GOLD"},
                "policy_decision": {"policy_version": "2026-Q3"},
                "calculation_result": {
                    "currency": "KES",
                    "total_commission": "32400.00",
                    "components": [{"type": "SALES_COMMISSION", "amount": "27000.00", "applied": True}],
                },
                "root_cause_report": {
                    "total_posted": "18000.00",
                    "total_expected": "32400.00",
                    "total_delta": "14400.00",
                    "root_causes": ["WRONG_POLICY_VERSION"],
                },
                "risk_decision": {
                    "execution_constraints": {"max_amount": "50000", "requires_approval_token": True},
                    "rollback_plan_required": True,
                },
                "team_run": {"run_id": "RUN-8"},
            },
            {
                "approval_id": "APR-8", "amount": "14400.00",
                "currency": "KES", "approver_role": "FINANCE_LEAD",
            },
            expires_at=2_000,
        )
        self.assertIn("REVGUARD_HUMAN_APPROVAL_REQUEST", message)
        self.assertIn("订单：EZ202608001", message)
        self.assertIn("复算应付：32400.00 KES", message)
        self.assertIn("WRONG_POLICY_VERSION", message)
        self.assertIn("REVGUARD_APPROVAL_CONTEXT", message)
        self.assertIn("请回复本消息或直接发送：批准", message)
        self.assertNotIn("approval_token", message)


class _FakeApprovalClient:
    def __init__(self, settings, event):
        self.settings = settings
        self.event = event
        self.sent: list[str] = []
        self.did_sync = False

    async def authenticate(self):
        return None

    async def whoami(self):
        # The transport and human Element session intentionally share this
        # account in the recording deployment.
        return {"user_id": "@finance:test"}

    async def cursor(self):
        return "s0"

    async def sync(self, *, since: str, timeout_ms: int):
        del since, timeout_ms
        if self.did_sync:
            return {"next_batch": "s1", "rooms": {"join": {}}}
        self.did_sync = True
        return {
            "next_batch": "s1",
            "rooms": {"join": {"!team:test": {
                "timeline": {"events": [self.event]},
            }}},
        }

    async def send_text(self, body: str, *, room_id: str | None = None, txn_id: str | None = None):
        del room_id, txn_id
        self.sent.append(body)
        return "$result-8"


class TestMatrixApprovalBridge(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_dispatches_reply_and_publishes_result(self):
        settings = MatrixSettings(
            homeserver_url="http://matrix.test",
            room_id="!team:test",
            server_name="test",
            access_token="token",
        )
        event = {
            "type": "m.room.message",
            "event_id": "$reply-8",
            "sender": "@finance:test",
            "content": {
                "msgtype": "m.text",
                "body": "批准",
                "m.relates_to": {"m.in_reply_to": {"event_id": "$request-8"}},
            },
        }
        client = _FakeApprovalClient(settings, event)
        seen = []
        bridge = None

        async def handle(reply):
            seen.append(reply)
            bridge.stop()
            return "REVGUARD_HUMAN_APPROVAL_RESULT"

        bridge = MatrixApprovalBridge(
            settings,
            pending_requests=lambda: {
                "$request-8": PendingApprovalRequest(
                    "CASE-2026-0008", "APR-8", "$request-8", int(time.time()) + 300,
                ),
            },
            on_reply=handle,
            allowed_senders={"@finance:test"},
            client=client,
        )
        await bridge.run()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].decision, "APPROVED")
        self.assertEqual(client.sent, ["REVGUARD_HUMAN_APPROVAL_RESULT"])


if __name__ == "__main__":
    unittest.main()
