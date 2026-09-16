"""Human-in-the-loop identity proof security boundaries."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch

from revguard.hitl import (
    HumanIdentity,
    issue_human_action_assertion,
    load_human_approvers,
    verify_human_action_assertion,
)
from revguard.matrix_team import MatrixTransportError
from revguard.security import CapabilityTokenSigner, SecurityError


class TestHumanActionProof(unittest.TestCase):
    def setUp(self) -> None:
        self.signer = CapabilityTokenSigner(
            "human-action-test-signing-key-at-least-32-bytes",
            issuer="revguard-hitl-test",
        )
        self.now = int(time.time())
        self.approvers = load_human_approvers(
            '{"@finance:test":{"actor":"finance.lead",'
            '"display_name":"测试财务负责人"}}'
        )
        self.identity = HumanIdentity(
            sub="@finance:test",
            actor="finance.lead",
            display_name="测试财务负责人",
            auth_time=self.now,
        )

    def token(self, action: str = "APPROVED") -> str:
        return issue_human_action_assertion(
            self.signer,
            self.identity,
            case_id="CASE-1",
            approval_id="APR-1",
            action=action,
            ttl_seconds=120,
        )

    def test_exact_binding_and_allowlist(self):
        proof = verify_human_action_assertion(
            self.signer,
            self.token(),
            self.approvers,
            case_id="CASE-1",
            approval_id="APR-1",
            action="APPROVED",
            max_auth_age_seconds=300,
            now=self.now,
        )
        self.assertEqual(proof.identity.actor, "finance.lead")
        self.assertEqual(proof.action, "APPROVED")

    def test_proof_cannot_move_to_another_case_or_action(self):
        for case_id, action in (("CASE-2", "APPROVED"), ("CASE-1", "REJECTED")):
            with self.subTest(case_id=case_id, action=action):
                with self.assertRaises(SecurityError):
                    verify_human_action_assertion(
                        self.signer,
                        self.token(),
                        self.approvers,
                        case_id=case_id,
                        approval_id="APR-1",
                        action=action,
                        max_auth_age_seconds=300,
                        now=self.now,
                    )

    def test_stale_human_authentication_is_rejected(self):
        stale = HumanIdentity(
            sub=self.identity.sub,
            actor=self.identity.actor,
            display_name=self.identity.display_name,
            auth_time=self.now - 301,
        )
        token = issue_human_action_assertion(
            self.signer,
            stale,
            case_id="CASE-1",
            approval_id="APR-1",
            action="APPROVED",
            ttl_seconds=120,
        )
        with self.assertRaises(SecurityError):
            verify_human_action_assertion(
                self.signer,
                token,
                self.approvers,
                case_id="CASE-1",
                approval_id="APR-1",
                action="APPROVED",
                max_auth_age_seconds=300,
                now=self.now,
            )

    def test_approver_configuration_rejects_untrusted_shapes(self):
        invalid_values = [
            "{",
            "[]",
            '{"finance":{"actor":"finance.lead"}}',
            '{"@finance:test":"finance.lead"}',
            '{"@finance:test":{"actor":"revguard-executor"}}',
        ]
        for raw in invalid_values:
            with self.subTest(raw=raw), self.assertRaises(SecurityError):
                load_human_approvers(raw)

    def test_matrix_provider_verifies_whoami_and_exact_allowlist(self):
        from revguard.hitl import MatrixHumanIdentityProvider

        provider = MatrixHumanIdentityProvider(
            "http://matrix.test", self.approvers, server_name="test",
        )
        client = AsyncMock()
        client.whoami.return_value = {"user_id": "@finance:test"}
        with patch("revguard.hitl.MatrixClient", return_value=client):
            identity = asyncio.run(provider.authenticate("finance", "secret"))
        self.assertEqual(identity.sub, "@finance:test")
        self.assertEqual(identity.actor, "finance.lead")
        client.authenticate.assert_awaited_once()
        client.whoami.assert_awaited_once()

    def test_matrix_provider_fails_closed(self):
        from revguard.hitl import MatrixHumanIdentityProvider

        unconfigured = MatrixHumanIdentityProvider("", {})
        with self.assertRaises(SecurityError):
            asyncio.run(unconfigured.authenticate("finance", "secret"))
        provider = MatrixHumanIdentityProvider("http://matrix.test", self.approvers)
        with self.assertRaises(SecurityError):
            asyncio.run(provider.authenticate("", ""))

        failed_client = AsyncMock()
        failed_client.authenticate.side_effect = MatrixTransportError("unavailable")
        with patch("revguard.hitl.MatrixClient", return_value=failed_client), \
                self.assertRaises(SecurityError):
            asyncio.run(provider.authenticate("finance", "wrong"))

        unknown_client = AsyncMock()
        unknown_client.whoami.return_value = {"user_id": "@unknown:test"}
        with patch("revguard.hitl.MatrixClient", return_value=unknown_client), \
                self.assertRaises(SecurityError):
            asyncio.run(provider.authenticate("unknown", "secret"))


if __name__ == "__main__":
    unittest.main()
