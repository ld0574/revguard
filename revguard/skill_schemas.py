"""JSON Schema contracts for every public RevGuard Skill."""
from __future__ import annotations


def _obj(properties: dict, required: list[str] | tuple[str, ...] = (), *,
         additional: bool = False, examples: list | None = None) -> dict:
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": additional,
    }
    if examples:
        schema["examples"] = examples
    return schema


STRING = {"type": "string", "minLength": 1}
OBJECT = {"type": "object", "additionalProperties": True}
ARRAY_OBJECT = {"type": "array", "items": OBJECT}
STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
AMOUNT = {"type": ["string", "number"]}

RISK = _obj({
    "risk_level": {"type": "string", "enum": ["L0", "L1", "L2", "L3"]},
    "approval_required": {"type": "boolean"},
    "approver_role": {"type": ["string", "null"]},
    "execution_constraints": OBJECT,
    "rollback_plan_required": {"type": "boolean"},
    "reason_codes": STRING_ARRAY,
}, ["risk_level", "approval_required", "approver_role", "execution_constraints",
    "rollback_plan_required", "reason_codes"])

CALCULATION = _obj({
    "eligible": {"type": "boolean"},
    "total_commission": AMOUNT,
    "currency": STRING,
    "components": ARRAY_OBJECT,
    "rounding_rule": {"type": "string"},
    "calculation_hash": STRING,
    "policy_version": {"type": "string"},
    "eligibility_failures": {"type": "array"},
    "facts_snapshot": OBJECT,
}, ["eligible", "total_commission", "currency", "components", "rounding_rule",
    "calculation_hash", "policy_version", "eligibility_failures", "facts_snapshot"])

VERIFICATION = _obj({
    "verification_status": {"type": "string", "enum": ["PASSED", "FAILED"]},
    "expected_amount": AMOUNT,
    "actual_amount": AMOUNT,
    "variance": AMOUNT,
    "component_checks": ARRAY_OBJECT,
    "evidence_refs": STRING_ARRAY,
    "rollback_required": {"type": "boolean"},
    "checked_at": STRING,
}, ["verification_status", "expected_amount", "actual_amount", "variance",
    "component_checks", "evidence_refs", "rollback_required", "checked_at"])

SKILL_SCHEMAS: dict[str, dict[str, dict]] = {
    "CaseNormalizeSkill": {
        "input": _obj({"raw_case": OBJECT}, ["raw_case"],
                      examples=[{"raw_case": {"partner_id": "AGT-10001",
                                               "order_id": "EZ202608001"}}]),
        "output": _obj({"entities": OBJECT, "missing_fields": STRING_ARRAY, "claim": OBJECT},
                       ["entities", "missing_fields", "claim"]),
    },
    "EntityResolveSkill": {
        "input": _obj({"entities": OBJECT}, ["entities"]),
        "output": _obj({
            "partner": OBJECT,
            "resolved_by": {"type": "string", "enum": ["partner_id", "partner_name"]},
        }, ["partner", "resolved_by"]),
    },
    "EvidenceCollectSkill": {
        "input": _obj({"partner": OBJECT, "order_id": STRING}, ["partner", "order_id"]),
        "output": _obj({
            "evidence": ARRAY_OBJECT,
            "collected": OBJECT,
            "evidence_gaps": STRING_ARRAY,
            "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
            "parallel": OBJECT,
        }, ["evidence", "collected", "evidence_gaps", "evidence_score", "parallel"]),
    },
    "PolicyVersionMatchSkill": {
        "input": _obj({
            "versions": ARRAY_OBJECT,
            "facts": OBJECT,
            "time_basis": {"type": "string", "enum": ["order_date", "payment_date"]},
        }, ["versions", "facts"]),
        "output": _obj({
            "policy_id": STRING, "policy_version": STRING, "time_basis": STRING,
            "decision_date": STRING, "effective_rule_set": OBJECT,
            "cited_clauses": {"type": "array"}, "excluded_versions": ARRAY_OBJECT,
            "unresolved_conflicts": {"type": "array"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        }, ["policy_id", "policy_version", "time_basis", "decision_date",
            "effective_rule_set", "cited_clauses", "excluded_versions",
            "unresolved_conflicts", "confidence"]),
    },
    "CommissionCalculateSkill": {
        "input": _obj({"rule_dsl": OBJECT, "facts": OBJECT, "currency": STRING},
                      ["rule_dsl", "facts", "currency"]),
        "output": CALCULATION,
    },
    "DifferenceExplainSkill": {
        "input": _obj({
            "calculation": CALCULATION, "ledger_entries": ARRAY_OBJECT,
            "matched_policy_version": STRING, "tier_conflict": {"type": ["string", "null"]},
        }, ["calculation", "ledger_entries", "matched_policy_version"]),
        "output": _obj({
            "diffs": ARRAY_OBJECT, "total_expected": AMOUNT, "total_posted": AMOUNT,
            "total_delta": AMOUNT, "root_causes": STRING_ARRAY,
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        }, ["diffs", "total_expected", "total_posted", "total_delta", "root_causes",
            "confidence"]),
    },
    "RiskClassifySkill": {
        "input": _obj({
            "action_type": STRING, "adjustment_amount": AMOUNT, "currency": STRING,
            "evidence_score": {"type": "number", "minimum": 0, "maximum": 1},
            "case_type": STRING, "policy_conflict": {"type": "boolean"},
            "order_count": {"type": "integer", "minimum": 1},
        }, ["action_type", "adjustment_amount", "currency", "evidence_score", "case_type"]),
        "output": RISK,
    },
    "ApprovalRouteSkill": {
        "input": _obj({
            "risk": RISK, "amount": AMOUNT, "component_quota": OBJECT,
            "currency": STRING, "action_summary": STRING,
        }, ["risk", "amount", "component_quota", "currency", "action_summary"]),
        "output": _obj({
            "approval_id": STRING, "case_id": STRING, "action_summary": STRING,
            "amount": AMOUNT, "component_quota": OBJECT, "currency": STRING,
            "risk_level": {"type": "string", "enum": ["L1", "L2", "L3"]},
            "approver_role": {"type": ["string", "null"]},
            "status": {"type": "string", "enum": ["PENDING"]}, "created_at": STRING,
        }, ["approval_id", "case_id", "action_summary", "amount", "component_quota",
            "currency", "risk_level", "approver_role", "status", "created_at"]),
    },
    "PermissionCheckSkill": {
        "input": _obj({
            "action_type": {"type": "string", "enum": ["DRAFT", "LEDGER_ADJUST",
                                                                  "LEDGER_REVERSE"]},
            "risk": RISK, "approval": {"type": ["object", "null"],
                                         "additionalProperties": True},
        }, ["action_type", "risk"]),
        "output": _obj({"authorized": {"type": "boolean"}}, ["authorized"]),
    },
    "IdempotencyGuardSkill": {
        "input": _obj({"idempotency_key": STRING}, ["idempotency_key"]),
        "output": {"type": ["object", "null"], "additionalProperties": True},
    },
    "AdjustmentDraftSkill": {
        "input": _obj({
            "order_id": STRING, "component": STRING, "delta": AMOUNT,
            "currency": STRING, "reason": {"type": "string"},
        }, ["order_id", "component", "delta", "currency"]),
        "output": _obj({
            "action_id": STRING, "order_id": STRING, "case_id": STRING,
            "component": STRING, "amount": AMOUNT, "currency": STRING,
            "reason": {"type": "string"}, "status": {"type": "string", "enum": ["DRAFT"]},
            "created_at": STRING,
        }, ["action_id", "order_id", "case_id", "component", "amount", "currency",
            "reason", "status", "created_at"]),
    },
    "LedgerAdjustSkill": {
        "input": _obj({
            "action_id": STRING, "approval_token": STRING, "policy_version": STRING,
            "idempotency_key": STRING,
        }, ["action_id", "approval_token", "policy_version", "idempotency_key"]),
        "output": _obj({
            "action_id": STRING, "status": {"type": "string", "enum": ["SUBMITTED"]},
            "ledger_entry": OBJECT, "before_snapshot": ARRAY_OBJECT,
            "after_snapshot": ARRAY_OBJECT, "rollback_token": STRING,
        }, ["action_id", "status", "ledger_entry", "before_snapshot", "after_snapshot",
            "rollback_token"]),
    },
    "LedgerReverseSkill": {
        "input": _obj({"ledger_id": STRING, "rollback_token": STRING,
                       "idempotency_key": STRING},
                      ["ledger_id", "rollback_token", "idempotency_key"]),
        "output": _obj({"reversal_entry": OBJECT, "reversed_entry": OBJECT},
                       ["reversal_entry", "reversed_entry"]),
    },
    "PostActionVerifySkill": {
        "input": _obj({"order_id": STRING, "expected_components": ARRAY_OBJECT},
                      ["order_id", "expected_components"]),
        "output": VERIFICATION,
    },
    "PostRollbackVerifySkill": {
        "input": _obj({"order_id": STRING, "expected_snapshot": ARRAY_OBJECT},
                      ["order_id", "expected_snapshot"]),
        "output": _obj({
            "verification_status": {"type": "string", "enum": ["PASSED", "FAILED"]},
            "component_checks": ARRAY_OBJECT, "evidence_refs": STRING_ARRAY,
            "checked_at": STRING,
        }, ["verification_status", "component_checks", "evidence_refs", "checked_at"]),
    },
    "CaseToDatasetSkill": {
        "input": _obj({"case": OBJECT, "shared_state": OBJECT, "verification": OBJECT},
                      ["case", "shared_state"]),
        "output": _obj({
            "case_id": STRING,
            "label": {"type": "string", "enum": ["GOLDEN", "BAD", "SAFE_ROLLBACK"]},
            "case_type": STRING, "input": OBJECT,
            "expected_policy_version": {"type": ["string", "null"]},
            "expected_amount": {"type": ["string", "number", "null"]},
            "root_causes": STRING_ARRAY, "verification": OBJECT, "archived_at": STRING,
        }, ["case_id", "label", "case_type", "input", "expected_policy_version",
            "expected_amount", "root_causes", "verification", "archived_at"]),
    },
}
