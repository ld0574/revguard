"""审批 = 参数承诺：把一次人工批准绑定到可复算的规范化参数摘要。

审批凭证已经用 HMAC 绑定案件、币种、总额、逐组件额度和用途。本模块补上另一半：
**审批时承诺的参数本身**要有一个稳定、可被第三方独立复算的摘要，执行时二次比对。

- 批准时：对规范化后的审批参数求 `SHA-256(canonical_json(...))`，摘要同时落进审批单
  和签发的执行能力令牌；
- 执行时：重新计算审批单当前参数的摘要，与审批单上的摘要、令牌里的摘要三方比对，
  任意一处不一致 -> 参数漂移，拒绝写入。

因此"改一个数字再执行"不再可能：金额、组件额度、币种、风险等级、审批单编号、
案件编号、动作摘要与发布版本都进了承诺；发布版本变化也会被拒绝。
"""
from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MONEY_QUANTUM = Decimal("0.01")
COMMITMENT_FIELDS: tuple[str, ...] = (
    "action_summary", "amount", "approval_id", "case_id",
    "component_quota", "currency", "release_version", "risk_level",
)


class CommitmentError(ValueError):
    """参数无法规范化成稳定摘要。"""


def canonical_amount(value) -> str:
    """金额规范化：两位小数定点字符串，消除 `100` / `100.0` / `100.00` 的差异。"""
    try:
        return str(Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CommitmentError(f"金额无法规范化: {value!r}") from exc


def canonical_quota(quota) -> dict[str, str]:
    if not isinstance(quota, dict) or not quota:
        raise CommitmentError("组件额度必须是非空映射")
    return {str(component): canonical_amount(amount) for component, amount in quota.items()}


def canonical_json(value) -> str:
    """稳定序列化：键排序、无多余空白、Non-ASCII 不转义。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def approval_commitment(approval: dict) -> dict:
    """把审批单折算成承诺文档（只包含授权语义相关的字段）。"""
    return {
        "action_summary": str(approval.get("action_summary") or ""),
        "amount": canonical_amount(approval.get("amount", "0")),
        "approval_id": str(approval.get("approval_id") or ""),
        "case_id": str(approval.get("case_id") or ""),
        "component_quota": canonical_quota(approval.get("component_quota") or {}),
        "currency": str(approval.get("currency") or ""),
        "release_version": str(approval.get("release_version") or ""),
        "risk_level": str(approval.get("risk_level") or ""),
    }


def commitment_digest(commitment: dict) -> str:
    payload = canonical_json({field: commitment[field] for field in COMMITMENT_FIELDS})
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def approval_digest(approval: dict) -> str:
    return commitment_digest(approval_commitment(approval))
