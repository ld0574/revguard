"""Human-in-the-loop identity proof for high-risk RevGuard actions.

The AgentTeams/Matrix account is the human identity provider for the recording
environment.  Credentials are verified out-of-band from the Agent runtime and
are never stored.  RevGuard then issues a very short-lived assertion bound to
one case, one approval record and one decision/recovery action.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from .matrix_team import MatrixClient, MatrixSettings, MatrixTransportError
from .security import (
    ACTOR_ALLOWED_SCOPES,
    CapabilityTokenSigner,
    SecurityError,
)


@dataclass(frozen=True)
class HumanIdentity:
    """Server-verified natural-person identity used at the approval boundary."""

    sub: str
    actor: str
    display_name: str
    auth_time: int
    auth_method: str = "matrix-password"

    def public(self) -> dict:
        return {
            "sub": self.sub,
            "actor": self.actor,
            "display_name": self.display_name,
            "auth_time": self.auth_time,
            "auth_method": self.auth_method,
        }


@dataclass(frozen=True)
class HumanActionProof:
    """Verified action-bound assertion presented to a protected endpoint."""

    identity: HumanIdentity
    case_id: str
    approval_id: str
    action: str
    assertion_id: str


def load_human_approvers(raw_json: str) -> dict[str, dict[str, str]]:
    """Load exact Matrix ``user_id`` allow-list entries.

    Roles cannot be self-asserted by the browser.  Every Matrix subject is
    mapped server-side to an existing RevGuard actor that owns
    ``approval:decide``.
    """
    if not raw_json:
        return {}
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SecurityError(
            f"REVGUARD_HITL_MATRIX_USERS_JSON 不是合法 JSON: {exc}"
        ) from exc
    if not isinstance(raw, dict):
        raise SecurityError("REVGUARD_HITL_MATRIX_USERS_JSON 必须是对象")

    approvers: dict[str, dict[str, str]] = {}
    for subject, item in raw.items():
        if not isinstance(subject, str) or not subject.startswith("@"):
            raise SecurityError("HITL Matrix subject 必须是完整 user_id")
        if not isinstance(item, dict):
            raise SecurityError(f"HITL Matrix subject {subject} 配置必须是对象")
        actor = str(item.get("actor") or "")
        allowed = ACTOR_ALLOWED_SCOPES.get(actor)
        if allowed is None or "approval:decide" not in allowed:
            raise SecurityError(f"HITL actor {actor or '<empty>'} 没有审批权限")
        approvers[subject] = {
            "actor": actor,
            "display_name": str(item.get("display_name") or subject),
        }
    return approvers


class MatrixHumanIdentityProvider:
    """Verify a human password against Matrix and return an allow-listed subject."""

    def __init__(
        self,
        homeserver_url: str,
        approvers: dict[str, dict[str, str]],
        *,
        server_name: str = "matrix",
    ):
        self.homeserver_url = homeserver_url.rstrip("/")
        self.approvers = approvers
        self.server_name = server_name or "matrix"

    async def authenticate(self, username: str, password: str) -> HumanIdentity:
        if not self.homeserver_url or not self.approvers:
            raise SecurityError("HITL Matrix 身份提供方未配置")
        if not username.strip() or not password:
            raise SecurityError("请输入 AgentTeams 审批人账号和密码")
        settings = MatrixSettings(
            homeserver_url=self.homeserver_url,
            room_id=f"!hitl-auth:{self.server_name}",
            server_name=self.server_name,
            username=username.strip(),
            password=password,
        )
        client = MatrixClient(settings)
        try:
            await client.authenticate()
            whoami = await client.whoami()
        except MatrixTransportError as exc:
            raise SecurityError("AgentTeams 审批人身份验证失败") from exc
        subject = str(whoami.get("user_id") or "")
        mapped = self.approvers.get(subject)
        if not mapped:
            raise SecurityError(f"Matrix 用户 {subject or '<unknown>'} 不在审批白名单")
        return HumanIdentity(
            sub=subject,
            actor=mapped["actor"],
            display_name=mapped["display_name"],
            auth_time=int(time.time()),
        )


def issue_human_action_assertion(
    signer: CapabilityTokenSigner,
    identity: HumanIdentity,
    *,
    case_id: str,
    approval_id: str,
    action: str,
    ttl_seconds: int,
) -> str:
    """Issue a short proof that cannot be moved to another action or case."""
    return signer.issue(
        "human_approval",
        {
            **identity.public(),
            "case_id": case_id,
            "approval_id": approval_id,
            "action": action,
        },
        ttl_seconds=ttl_seconds,
    )


def verify_human_action_assertion(
    signer: CapabilityTokenSigner,
    token: str,
    approvers: dict[str, dict[str, str]],
    *,
    case_id: str,
    approval_id: str,
    action: str,
    max_auth_age_seconds: int,
    now: int | None = None,
) -> HumanActionProof:
    """Verify signature, freshness, allow-list and exact business binding."""
    current = int(time.time() if now is None else now)
    claims = signer.verify(token, purpose="human_approval", now=current)
    for key, expected in {
        "case_id": case_id,
        "approval_id": approval_id,
        "action": action,
    }.items():
        if claims.get(key) != expected:
            raise SecurityError(f"人工审批证明与 {key} 不匹配")
    subject = str(claims.get("sub") or "")
    mapped = approvers.get(subject)
    if not mapped or claims.get("actor") != mapped.get("actor"):
        raise SecurityError("人工审批身份不在当前服务端白名单")
    auth_time = claims.get("auth_time")
    if type(auth_time) is not int or auth_time > current + 30:
        raise SecurityError("人工身份验证时间无效")
    if current - auth_time > max_auth_age_seconds:
        raise SecurityError("人工身份会话不够新鲜，请重新登录 AgentTeams")
    assertion_id = str(claims.get("jti") or "")
    if not assertion_id:
        raise SecurityError("人工审批证明缺少唯一标识")
    return HumanActionProof(
        identity=HumanIdentity(
            sub=subject,
            actor=mapped["actor"],
            display_name=str(claims.get("display_name") or mapped["display_name"]),
            auth_time=auth_time,
            auth_method=str(claims.get("auth_method") or "matrix-password"),
        ),
        case_id=case_id,
        approval_id=approval_id,
        action=action,
        assertion_id=assertion_id,
    )
