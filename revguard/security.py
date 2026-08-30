"""RevGuard 安全基元：工具授权、API Principal 与签名能力令牌。

本模块只依赖 Python 标准库，便于 Mock、AgentTeams Adapter 与真实 MCP
Server 共享同一套安全语义。令牌是带 HMAC-SHA256 签名的最小能力凭证，
不是登录会话；它必须同时通过签名、时效、用途和业务绑定校验。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections.abc import Iterable
from dataclasses import dataclass

_CAPABILITY_TOKEN_RE = re.compile(r"RGC1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+([A-Za-z0-9._~+/-]+=*)", re.IGNORECASE)


class SecurityError(ValueError):
    """安全校验失败；调用层应统一映射为 AUTH_FAILED。"""


TOOL_REQUIRED_SCOPES: dict[str, str] = {
    "crm.get_order": "order:read",
    "crm.get_partner": "partner:read",
    "crm.get_partner_tier_history": "partner:read",
    "crm.list_orders_by_partner": "order:read",
    "contract.get_contract": "contract:read",
    "contract.get_effective_terms": "contract:read",
    "policy.search_versions": "policy:read",
    "policy.get_rule_dsl": "policy:read",
    "finance.get_payment": "payment:read",
    "finance.get_refund": "payment:read",
    "finance.get_invoice": "payment:read",
    "finance.get_commission_ledger": "ledger:read",
    "commission.create_adjustment_draft": "commission:draft",
    "commission.submit_adjustment": "commission:write",
    "commission.reverse_adjustment": "commission:reverse",
    "workflow.create_approval": "approval:write",
    "workflow.get_approval_status": "approval:read",
    "workflow.decide_approval": "approval:decide",
    "workflow.renew_approval_capability": "approval:decide",
    "workflow.renew_rollback_capability": "approval:decide",
    "ticket.update_case": "ticket:write",
    "mail.create_reply_draft": "mail:draft",
}


ACTOR_ALLOWED_SCOPES: dict[str, frozenset[str]] = {
    "revguard-orchestrator": frozenset(),
    "revguard-intake": frozenset({"partner:read", "order:read", "ticket:write"}),
    "revguard-evidence": frozenset({
        "order:read", "partner:read", "contract:read", "policy:read",
        "payment:read", "ledger:read",
    }),
    "revguard-policy": frozenset({"policy:read", "partner:read"}),
    "revguard-calculation": frozenset({"order:read"}),
    "revguard-rootcause": frozenset(),
    "revguard-risk": frozenset({"approval:write", "approval:read"}),
    "revguard-executor": frozenset({
        "commission:draft", "commission:write", "commission:reverse",
    }),
    "revguard-verifier": frozenset({"ledger:read"}),
    "revguard-knowledge": frozenset({"ticket:write", "mail:draft"}),
    "finance.lead": frozenset({"approval:read", "approval:decide"}),
    "api-operator": frozenset(),
    "api-viewer": frozenset(),
    "agentteams-worker": frozenset({
        "order:read", "partner:read", "contract:read", "policy:read",
        "payment:read", "ledger:read",
    }),
}


def authorize_tool(actor: str, claimed_scopes: Iterable[str], tool_name: str) -> str:
    """校验工具所需 scope 同时被调用方声明且属于 actor 固有权限。"""
    required = TOOL_REQUIRED_SCOPES.get(tool_name)
    if required is None:
        raise SecurityError(f"工具未登记权限契约: {tool_name}")
    claimed = set(claimed_scopes)
    allowed = ACTOR_ALLOWED_SCOPES.get(actor)
    if allowed is None:
        raise SecurityError(f"未知或未授权 actor: {actor or '<empty>'}")
    if required not in claimed:
        raise SecurityError(f"调用 {tool_name} 缺少 scope: {required}")
    if required not in allowed:
        raise SecurityError(f"actor {actor} 不允许 scope: {required}")
    return required


@dataclass(frozen=True)
class ApiPrincipal:
    """由 API key 映射出的服务端可信身份。"""

    actor: str
    roles: frozenset[str]
    scopes: frozenset[str]


def load_api_principals(raw_json: str) -> dict[str, ApiPrincipal]:
    """解析 ``API key -> Principal`` 映射；配置错误时直接失败而非放行。"""
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise SecurityError(f"REVGUARD_API_KEYS_JSON 不是合法 JSON: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise SecurityError("REVGUARD_API_KEYS_JSON 必须是非空对象")
    principals: dict[str, ApiPrincipal] = {}
    for api_key, item in raw.items():
        if not isinstance(api_key, str) or len(api_key) < 12:
            raise SecurityError("每个 API key 至少 12 个字符")
        if not isinstance(item, dict) or not item.get("actor"):
            raise SecurityError(f"API key {api_key[:4]}… 缺少 actor")
        actor = str(item["actor"])
        configured_scopes = frozenset(str(x) for x in item.get("scopes", []))
        allowed = ACTOR_ALLOWED_SCOPES.get(actor)
        if allowed is None:
            raise SecurityError(f"API Principal 使用了未知 actor: {actor}")
        if not configured_scopes.issubset(allowed):
            extra = sorted(configured_scopes - allowed)
            raise SecurityError(f"API Principal {actor} 配置了越权 scope: {extra}")
        principals[api_key] = ApiPrincipal(
            actor=actor,
            roles=frozenset(str(x) for x in item.get("roles", [])),
            scopes=configured_scopes,
        )
    return principals


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(data + padding)
    except Exception as exc:
        raise SecurityError("能力令牌 Base64 编码无效") from exc


class CapabilityTokenSigner:
    """签发并验证短期、业务绑定的能力令牌。"""

    def __init__(self, secret: str | bytes, *, issuer: str = "revguard"):
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 32:
            raise SecurityError("签名密钥至少 32 字节")
        self._secret = secret_bytes
        self.issuer = issuer

    def issue(self, purpose: str, claims: dict, *, ttl_seconds: int = 900,
              now: int | None = None) -> str:
        issued_at = int(time.time() if now is None else now)
        payload = {
            **claims,
            "v": 1,
            "iss": self.issuer,
            "purpose": purpose,
            "iat": issued_at,
            "exp": issued_at + ttl_seconds,
            "jti": secrets.token_urlsafe(12),
        }
        body = _b64encode(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"))
        signature = _b64encode(hmac.new(
            self._secret, body.encode("ascii"), hashlib.sha256
        ).digest())
        return f"RGC1.{body}.{signature}"

    def verify(self, token: str, *, purpose: str, now: int | None = None) -> dict:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "RGC1":
            raise SecurityError("能力令牌格式无效")
        body, supplied = parts[1], parts[2]
        expected = _b64encode(hmac.new(
            self._secret, body.encode("ascii"), hashlib.sha256
        ).digest())
        if not hmac.compare_digest(supplied, expected):
            raise SecurityError("能力令牌签名无效")
        try:
            payload = json.loads(_b64decode(body))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SecurityError("能力令牌载荷无效") from exc
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise SecurityError("能力令牌版本无效")
        current = int(time.time() if now is None else now)
        if payload.get("iss") != self.issuer or payload.get("purpose") != purpose:
            raise SecurityError("能力令牌签发者或用途不匹配")
        if type(payload.get("exp")) is not int or current >= payload["exp"]:
            raise SecurityError("能力令牌已过期")
        if type(payload.get("iat")) is not int or payload["iat"] > current + 30:
            raise SecurityError("能力令牌签发时间无效")
        return payload


def constant_time_lookup(api_key: str, principals: dict[str, ApiPrincipal]) -> ApiPrincipal | None:
    """常量时间比较 API key，避免直接字典查找泄露前缀时序。"""
    match = None
    for candidate, principal in principals.items():
        if hmac.compare_digest(api_key, candidate):
            match = principal
    return match


def secret_fingerprint(value: str) -> str:
    """返回可审计但不可用于授权的短指纹。"""
    if not value:
        return "-"
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def redact_secrets(value):
    """递归清理 Trace/API 日志中的凭证字段。"""
    sensitive = {
        "approval_token", "rollback_token", "assertion_token",
        "human_assertion", "authorization", "api_key", "signing_key",
        "password",
    }
    if isinstance(value, dict):
        return {
            key: (f"<redacted:{secret_fingerprint(str(item))}>"
                  if key.lower() in sensitive and item else redact_secrets(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        value = _BEARER_TOKEN_RE.sub(
            lambda match: f"Bearer <redacted:{secret_fingerprint(match.group(1))}>",
            value,
        )
        value = _CAPABILITY_TOKEN_RE.sub(
            lambda match: f"<redacted:{secret_fingerprint(match.group(0))}>",
            value,
        )
    return value
