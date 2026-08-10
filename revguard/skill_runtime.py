"""版本化 Skill 运行时：把注册表能力暴露为统一可调用契约。"""
from __future__ import annotations

from decimal import Decimal

from . import skills
from .models import CalculationResult, RiskDecision, new_id
from .mocks import ToolError, ToolGateway
from .store import Store
from .trace import Tracer
from .security import redact_secrets


class SkillInvocationError(ValueError):
    pass


SKILL_ACTORS: dict[str, frozenset[str]] = {
    "CaseNormalizeSkill": frozenset({"revguard-intake"}),
    "EntityResolveSkill": frozenset({"revguard-intake"}),
    "EvidenceCollectSkill": frozenset({"revguard-evidence"}),
    "PolicyVersionMatchSkill": frozenset({"revguard-policy"}),
    "CommissionCalculateSkill": frozenset({"revguard-calculation"}),
    "DifferenceExplainSkill": frozenset({"revguard-rootcause"}),
    "RiskClassifySkill": frozenset({"revguard-risk"}),
    "ApprovalRouteSkill": frozenset({"revguard-risk"}),
    "PermissionCheckSkill": frozenset({"revguard-executor"}),
    "IdempotencyGuardSkill": frozenset({"revguard-executor"}),
    "AdjustmentDraftSkill": frozenset({"revguard-executor"}),
    "LedgerAdjustSkill": frozenset({"revguard-executor"}),
    "LedgerReverseSkill": frozenset({"revguard-executor"}),
    "PostActionVerifySkill": frozenset({"revguard-verifier"}),
    "PostRollbackVerifySkill": frozenset({"revguard-verifier"}),
    "CaseToDatasetSkill": frozenset({"revguard-knowledge"}),
}


def _required(payload: dict, name: str):
    if name not in payload:
        raise SkillInvocationError(f"缺少输入字段: {name}")
    return payload[name]


def invoke_skill(name: str, payload: dict, *, actor: str, case_id: str,
                 gateway: ToolGateway, store: Store) -> dict:
    """按统一契约调用一个注册 Skill，并记录 Skill span 与调用回执。"""
    meta = skills.SKILL_REGISTRY.get(name)
    if not meta:
        raise SkillInvocationError(f"未知 Skill: {name}")
    if actor not in SKILL_ACTORS.get(name, frozenset()):
        raise ToolError("AUTH_FAILED", f"actor {actor} 无权调用 {name}")
    tracer = Tracer(store, case_id or "SKILL-NO-CASE")
    with tracer.span("SKILL", name, actor=actor, inputs=redact_secrets(payload)) as span:
        if name == "CaseNormalizeSkill":
            result = skills.case_normalize(_required(payload, "raw_case"))
        elif name == "EntityResolveSkill":
            result = skills.entity_resolve(
                gateway, tracer, case_id=case_id, entities=_required(payload, "entities")
            )
        elif name == "EvidenceCollectSkill":
            result = skills.collect_evidence(
                gateway, tracer, case_id=case_id, partner=_required(payload, "partner"),
                order_id=_required(payload, "order_id"),
            )
        elif name == "PolicyVersionMatchSkill":
            result = skills.policy_version_match(
                _required(payload, "versions"), _required(payload, "facts"),
                payload.get("time_basis", "order_date"),
            )
        elif name == "CommissionCalculateSkill":
            result = skills.commission_calculate(
                _required(payload, "rule_dsl"), _required(payload, "facts"),
                _required(payload, "currency"),
            )
        elif name == "DifferenceExplainSkill":
            result = skills.difference_explain(
                calculation=CalculationResult(**_required(payload, "calculation")),
                ledger_entries=_required(payload, "ledger_entries"),
                matched_policy_version=_required(payload, "matched_policy_version"),
                tier_conflict=payload.get("tier_conflict"),
            )
        elif name == "RiskClassifySkill":
            result = skills.risk_classify(
                action_type=_required(payload, "action_type"),
                adjustment_amount=Decimal(str(_required(payload, "adjustment_amount"))),
                currency=_required(payload, "currency"),
                evidence_score=float(_required(payload, "evidence_score")),
                case_type=_required(payload, "case_type"),
                policy_conflict=bool(payload.get("policy_conflict", False)),
                order_count=int(payload.get("order_count", 1)),
            )
        elif name == "ApprovalRouteSkill":
            result = skills.approval_route(
                gateway, tracer, case_id=case_id,
                risk=RiskDecision(**_required(payload, "risk")),
                amount=Decimal(str(_required(payload, "amount"))),
                component_quota=_required(payload, "component_quota"),
                currency=_required(payload, "currency"),
                action_summary=_required(payload, "action_summary"),
            )
        elif name == "PermissionCheckSkill":
            skills.permission_check(
                actor=actor, action_type=_required(payload, "action_type"),
                risk=RiskDecision(**_required(payload, "risk")),
                approval=payload.get("approval"),
            )
            result = {"authorized": True}
        elif name == "IdempotencyGuardSkill":
            result = skills.idempotency_guard(store, _required(payload, "idempotency_key"))
        elif name == "AdjustmentDraftSkill":
            result = skills.adjustment_draft(
                gateway, tracer, case_id=case_id,
                order_id=_required(payload, "order_id"),
                component=_required(payload, "component"),
                delta=Decimal(str(_required(payload, "delta"))),
                currency=_required(payload, "currency"),
                reason=payload.get("reason", ""),
            )
        elif name == "LedgerAdjustSkill":
            result = skills.ledger_adjust(
                gateway, tracer, case_id=case_id,
                action_id=_required(payload, "action_id"),
                approval_token=_required(payload, "approval_token"),
                policy_version=_required(payload, "policy_version"),
                idempotency_key=_required(payload, "idempotency_key"),
            )
        elif name == "LedgerReverseSkill":
            result = skills.ledger_reverse(
                gateway, tracer, case_id=case_id,
                ledger_id=_required(payload, "ledger_id"),
                rollback_token=_required(payload, "rollback_token"),
                idempotency_key=_required(payload, "idempotency_key"),
            )
        elif name == "PostActionVerifySkill":
            result = skills.post_action_verify(
                gateway, tracer, case_id=case_id,
                order_id=_required(payload, "order_id"),
                expected_components=_required(payload, "expected_components"),
            )
        elif name == "PostRollbackVerifySkill":
            result = skills.post_rollback_verify(
                gateway, tracer, case_id=case_id,
                order_id=_required(payload, "order_id"),
                expected_snapshot=_required(payload, "expected_snapshot"),
            )
        elif name == "CaseToDatasetSkill":
            result = skills.case_to_dataset(
                _required(payload, "case"), _required(payload, "shared_state"),
                payload.get("verification", {}),
            )
        else:  # pragma: no cover - registry/runtime drift guard
            raise SkillInvocationError(f"Skill 已注册但运行时无适配器: {name}")

        if hasattr(result, "__dataclass_fields__"):
            from dataclasses import asdict
            result = asdict(result)
        span["outputs"] = result
    store.audit(case_id or "SKILL-NO-CASE", actor, "SKILL_INVOKED",
                {"skill": name, "version": meta["version"]})
    return {
        "success": True,
        "data": result,
        "error": None,
        "skill_receipt": new_id("SKR"),
        "skill": name,
        "version": meta["version"],
    }
