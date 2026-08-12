"""Skill 注册中心（Skill = 可被多个 Agent 复用的稳定任务能力，设计文档第 8 章）。

设计原则落实：
1. 输入输出全部结构化（dataclass/dict），不是一次性提示词；
2. 一个 Skill 只做一种稳定能力；
3. LLM 理解与确定性计算分离——本文件没有任何"让模型算金额"的路径；
4. 每个 Skill 携带元数据（版本/输入输出/失败处理/安全边界/复用价值），
   通过 SKILL_REGISTRY 统一暴露，可被编排器、API 或 AgentTeams Worker 调用；
5. 失败返回明确错误类型，不吞异常。

本层不直接访问 fixtures，一切外部数据经由 ToolGateway（MCP/Adapter 契约）。
"""
from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from decimal import Decimal

from . import rule_engine
from .mocks import ToolError, ToolGateway
from .models import (
    CalculationResult,
    Evidence,
    PolicyDecision,
    RiskDecision,
    new_id,
    utc_now,
)
from .policy_matcher import resolve_tier_at_date, select_policy_version
from .risk import classify_risk
from .security import redact_secrets
from .skill_schemas import SKILL_SCHEMAS
from .trace import Tracer

# ---------------------------------------------------------------------------
# 工具调用辅助：超时/重试/留痕（设计文档 13.3）
# ---------------------------------------------------------------------------

def call_tool(gateway: ToolGateway, tracer: Tracer | None, tool_name: str,
              parameters: dict, *, case_id: str, actor: str,
              scope: list[str] | None = None, idempotency_key: str | None = None,
              max_retries: int = 3, retry_backoff: float = 0.05,
              parent_span_id: str | None = None) -> dict:
    """带重试与 Trace 的工具调用。可重试错误按次数退避重试，最终失败抛 ToolError。"""
    attempt = 0
    while True:
        attempt += 1
        span_ctx = (tracer.span("TOOL", tool_name, actor=actor,
                                inputs={"parameters": redact_secrets(parameters),
                                        "attempt": attempt},
                                parent_span_id=parent_span_id)
                    if tracer else _null_span())
        with span_ctx as span:
            resp = gateway.call(tool_name, parameters, case_id=case_id, actor=actor,
                                scope=scope, idempotency_key=idempotency_key)
            span["outputs"] = {"success": resp["success"], "error": resp["error"],
                               "tool_receipt": resp["tool_receipt"]}
            if not resp["success"]:
                span["status"] = "ERROR"
                span["error"] = (resp.get("error") or {}).get("message", "工具调用失败")
        if resp["success"]:
            return resp
        error = resp["error"] or {}
        if error.get("retryable") and attempt < max_retries:
            time.sleep(retry_backoff * attempt)  # 简单退避
            continue
        raise ToolError(error.get("type", "UNKNOWN"), error.get("message", "工具调用失败"))


class _null_span:
    """无 Tracer 时的空上下文，保持代码路径一致。"""

    def __call__(self):
        return self

    def __enter__(self):
        return {"outputs": None}

    def __exit__(self, *exc):
        return False


# ---------------------------------------------------------------------------
# A. 案件类 Skill
# ---------------------------------------------------------------------------

def case_normalize(raw_case: dict) -> dict:
    """CaseNormalizeSkill：把申诉/工单原文解析成标准化案件实体。

    Demo 版为确定性规则（复赛可由 Intake Agent 用 LLM 解析后回填同一 Schema）。
    """
    entities = {
        "partner_id": raw_case.get("partner_id"),
        "partner_name": raw_case.get("partner_name"),
        "order_id": raw_case.get("order_id"),
        "contract_id": raw_case.get("contract_id"),
    }
    missing = [] if entities.get("partner_id") or entities.get("partner_name") else ["partner_identity"]
    return {"entities": entities, "missing_fields": missing,
            "claim": raw_case.get("claim", {})}


def entity_resolve(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                   entities: dict) -> dict:
    """EntityResolveSkill：把案件中的代理商解析为系统内唯一实体。"""
    if entities.get("partner_id"):
        resp = call_tool(gateway, tracer, "crm.get_partner",
                         {"partner_id": entities["partner_id"]},
                         case_id=case_id, actor="revguard-intake",
                         scope=["partner:read"])
        return {"partner": resp["data"], "resolved_by": "partner_id"}
    if entities.get("partner_name"):
        resp = call_tool(gateway, tracer, "crm.get_partner",
                         {"partner_name": entities["partner_name"]},
                         case_id=case_id, actor="revguard-intake",
                         scope=["partner:read"])
        return {"partner": resp["data"], "resolved_by": "partner_name"}
    from .mocks import ToolError
    raise ToolError("INVALID_PARAMS", "无法解析代理商：既无 partner_id 也无 partner_name")


# ---------------------------------------------------------------------------
# B. 证据类 Skill
# ---------------------------------------------------------------------------

# 案件闭环所需的证据清单（缺一项即产生 Evidence Gap）
REQUIRED_EVIDENCE = [
    "ORDER", "TIER_HISTORY", "CONTRACT", "POLICY_VERSIONS",
    "PAYMENT_RECORD", "REFUND_RECORD", "INVOICE", "COMMISSION_LEDGER",
]


def collect_evidence(gateway: ToolGateway, tracer: Tracer | None, *,
                     case_id: str, partner: dict, order_id: str) -> dict:
    """Evidence 组合 Skill：跨系统并行采集 + 完整度评分。

    包含：OrderEvidenceSkill / PaymentEvidenceSkill / ContractEvidenceSkill /
    CommissionLedgerEvidenceSkill / EvidenceCompletenessSkill。
    采集批次在 Trace 中归为同一并行组（evidence_batch）。
    """
    evidence: list[Evidence] = []
    gaps: list[str] = []

    def _record(ev_type: str, source_system: str, source_ref: str, payload: dict,
                tool_receipt: str) -> Evidence:
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), default=str).encode("utf-8")
        ev = Evidence(
            evidence_id=new_id("EV"), case_id=case_id, type=ev_type,
            source_system=source_system, source_ref=source_ref,
            collected_by="revguard-evidence", payload=payload,
            strength="STRONG", tool_receipt=tool_receipt,
            content_hash="sha256:" + hashlib.sha256(canonical).hexdigest(),
        )
        evidence.append(ev)
        return ev

    # ---- 同一采集批次真实并行：任一失败不影响其他项，财务类失败自动重试 ----
    batch: list[tuple[str, str, str, dict, list[str]]] = [
        ("ORDER", "CRM_MOCK", order_id, "crm.get_order", ["order:read"]),
        ("TIER_HISTORY", "CRM_MOCK", partner["partner_id"], "crm.get_partner_tier_history", ["partner:read"]),
        ("CONTRACT", "CONTRACT_MOCK", partner["partner_id"], "contract.get_contract", ["contract:read"]),
        ("PAYMENT_RECORD", "FINANCE_MOCK", order_id, "finance.get_payment", ["payment:read"]),
        ("REFUND_RECORD", "FINANCE_MOCK", order_id, "finance.get_refund", ["payment:read"]),
        ("INVOICE", "FINANCE_MOCK", order_id, "finance.get_invoice", ["payment:read"]),
        ("COMMISSION_LEDGER", "FINANCE_MOCK", order_id, "finance.get_commission_ledger", ["ledger:read"]),
    ]
    collected: dict[str, dict] = {}
    batch_parent = tracer.current_span_id if tracer else None
    started = time.monotonic()

    def _collect_one(item):
        ev_type, source_system, ref, tool, scope = item
        resp = call_tool(
            gateway, tracer, tool,
            {"order_id": order_id, "partner_id": partner["partner_id"]},
            case_id=case_id, actor="revguard-evidence", scope=scope,
            parent_span_id=batch_parent,
        )
        return ev_type, source_system, ref, resp

    results: dict[str, tuple[str, str, dict]] = {}
    max_workers = min(7, len(batch))
    with ThreadPoolExecutor(max_workers=max_workers,
                            thread_name_prefix="revguard-evidence") as pool:
        future_to_type = {pool.submit(_collect_one, item): item[0] for item in batch}
        for future in as_completed(future_to_type):
            ev_type = future_to_type[future]
            try:
                result_type, source_system, ref, resp = future.result()
                results[result_type] = (source_system, ref, resp)
            except ToolError as exc:
                gaps.append(f"{ev_type}: {exc}")

    # 固定证据输出顺序，保证报告/哈希在并发完成顺序变化时仍可复现。
    for ev_type, _source_system, _ref, _tool, _scope in batch:
        result = results.get(ev_type)
        if not result:
            continue
        source_system, ref, resp = result
        collected[ev_type] = resp["data"]
        _record(ev_type, source_system, ref, resp["data"], resp["tool_receipt"])
    parallel_duration_ms = int((time.monotonic() - started) * 1000)

    # 政策库查询依赖合同结果（政策 ID 来自合同）
    contract = collected.get("CONTRACT") or {}
    policy_id = contract.get("policy_id")
    if policy_id:
        resp = call_tool(gateway, tracer, "policy.search_versions", {"policy_id": policy_id},
                         case_id=case_id, actor="revguard-evidence", scope=["policy:read"])
        collected["POLICY_VERSIONS"] = resp["data"]
        _record("POLICY_VERSIONS", "CONTRACT_MOCK", policy_id, resp["data"], resp["tool_receipt"])
    else:
        gaps.append("POLICY_VERSIONS: 合同缺失，无法确定政策 ID")

    for missing in REQUIRED_EVIDENCE:
        if missing not in collected and not any(g.startswith(missing) for g in gaps):
            gaps.append(f"{missing}: 未采集")

    score = round(sum(1 for t in REQUIRED_EVIDENCE if t in collected) / len(REQUIRED_EVIDENCE), 2)
    return {
        "evidence": [asdict(e) for e in evidence],
        "collected": collected,
        "evidence_gaps": gaps,
        "evidence_score": score,
        "parallel": {"enabled": True, "workers": max_workers,
                     "duration_ms": parallel_duration_ms, "task_count": len(batch)},
    }


# ---------------------------------------------------------------------------
# C. 政策与计算类 Skill
# ---------------------------------------------------------------------------

def policy_version_match(versions: list[dict], facts: dict, time_basis: str) -> PolicyDecision:
    """PolicyVersionMatchSkill：按业务时点选择政策版本（Time Travel）。"""
    return select_policy_version(versions, facts, time_basis=time_basis)


def tier_at_order_date(tier_history: list[dict], order_date: str) -> dict:
    """PolicyClauseExtractSkill（等级条款）：回溯订单时点的代理商等级。"""
    return resolve_tier_at_date(tier_history, order_date)


def commission_calculate(rule_dsl: dict, facts: dict, currency: str) -> CalculationResult:
    """CommissionCalculateSkill：确定性复算（ADR-001，规则引擎而非 LLM）。"""
    result = rule_engine.run_policy(rule_dsl, facts, currency)
    return CalculationResult(**result)


def difference_explain(*, calculation: CalculationResult, ledger_entries: list[dict],
                       matched_policy_version: str, tier_conflict: str | None) -> dict:
    """DifferenceExplainSkill：逐项对比「应有」与「台账实有」，输出根因。

    根因判定规则（全部可审计）：
    - 台账有该组件但政策版本不符 => WRONG_POLICY_VERSION
    - 台账缺少该组件 => MISSING_COMPONENT
    - 金额方向相反且存在等级时点冲突 => TIER_EFFECTIVE_DATE_CONFLICT
    """
    diffs: list[dict] = []
    posted_by_component: dict[str, Decimal] = {}
    versions_by_component: dict[str, set] = {}
    for entry in ledger_entries:
        if entry.get("status") != "POSTED":
            continue
        comp = entry.get("component", "UNKNOWN")
        posted_by_component[comp] = posted_by_component.get(comp, Decimal("0")) + Decimal(str(entry.get("amount", "0")))
        versions_by_component.setdefault(comp, set()).add(entry.get("policy_version"))

    for comp in calculation.components:
        if not comp["applied"]:
            continue  # 不满足条件的组件不参与对账
        expected = Decimal(comp["amount"])
        posted = posted_by_component.get(comp["type"], Decimal("0"))
        delta = expected - posted
        if delta == 0:
            diffs.append({"component": comp["type"], "expected": str(expected),
                          "posted": str(posted), "delta": "0", "root_cause": None})
            continue
        versions = versions_by_component.get(comp["type"], set())
        if not versions:
            root_cause = "MISSING_COMPONENT"
            explanation = f"{comp['type']} 在台账中不存在，应为 {expected}（{comp.get('substituted') or comp['formula']}）"
        elif versions != {matched_policy_version}:
            root_cause = "WRONG_POLICY_VERSION"
            explanation = (f"{comp['type']} 台账按版本 {sorted(versions)} 计算为 {posted}，"
                           f"但业务时点应适用 {matched_policy_version}，应为 {expected}")
        elif tier_conflict:
            root_cause = "TIER_EFFECTIVE_DATE_CONFLICT"
            explanation = f"{comp['type']} 因等级时点冲突多/少算：{tier_conflict}；台账 {posted}，应为 {expected}"
        else:
            root_cause = "AMOUNT_MISMATCH"
            explanation = f"{comp['type']} 台账 {posted} 与应有 {expected} 不一致，原因待人工复核"
        diffs.append({"component": comp["type"], "expected": str(expected), "posted": str(posted),
                      "delta": str(delta), "root_cause": root_cause, "explanation": explanation})

    total_delta = sum((Decimal(d["delta"]) for d in diffs), Decimal("0"))
    root_causes = sorted({d["root_cause"] for d in diffs if d.get("root_cause")})
    return {
        "diffs": diffs,
        "total_expected": calculation.total_commission,
        "total_posted": str(sum(posted_by_component.values(), Decimal("0"))),
        "total_delta": str(total_delta),
        "root_causes": root_causes,
        "confidence": 1.0 if root_causes else 0.95,
    }


# ---------------------------------------------------------------------------
# D. 治理类 Skill
# ---------------------------------------------------------------------------

def risk_classify(*, action_type: str, adjustment_amount: Decimal, currency: str,
                  evidence_score: float, case_type: str, policy_conflict: bool,
                  order_count: int = 1) -> RiskDecision:
    """RiskClassifySkill：L0-L3 风险分级（确定性规则，见 risk.py）。"""
    return classify_risk(action_type=action_type, adjustment_amount=adjustment_amount,
                         currency=currency, evidence_score=evidence_score,
                         case_type=case_type, policy_conflict=policy_conflict,
                         order_count=order_count)


def approval_route(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                   risk: RiskDecision, amount: Decimal, currency: str,
                   component_quota: dict[str, str], action_summary: str) -> dict:
    """ApprovalRouteSkill：按风险等级创建审批单并路由到对应审批角色。"""
    resp = call_tool(gateway, tracer, "workflow.create_approval", {
        "case_id": case_id,
        "action_summary": action_summary,
        "amount": str(amount),
        "component_quota": component_quota,
        "currency": currency,
        "risk_level": risk.risk_level,
        "approver_role": risk.approver_role,
    }, case_id=case_id, actor="revguard-risk", scope=["approval:write"])
    return resp["data"]


def permission_check(*, actor: str, action_type: str, risk: RiskDecision,
                     approval: dict | None) -> None:
    """PermissionCheckSkill：执行前权限校验（设计文档 14.3）。

    违规直接抛异常——高风险工具只能由 Executor 携带有效审批凭证调用。
    """
    from .mocks import ToolError
    if actor != "revguard-executor":
        raise ToolError("AUTH_FAILED", f"{actor} 无权执行写操作")
    if risk.risk_level == "L3":
        raise ToolError("AUTH_FAILED", "L3 高风险案件禁止系统自动执行")
    if (risk.execution_constraints.get("write") == "draft_only"
            and action_type != "DRAFT"):
        raise ToolError("AUTH_FAILED", "L1 仅允许创建不生效草稿，禁止写入台账")
    if risk.approval_required:
        if not approval or approval.get("status") != "APPROVED" or not approval.get("approval_token"):
            raise ToolError("AUTH_FAILED", "缺少有效审批凭证，禁止执行")


def idempotency_guard(store, idempotency_key: str) -> dict | None:
    """IdempotencyGuardSkill：幂等键检查，返回已存在的执行记录或 None。"""
    return store.get_execution_by_idempotency(idempotency_key)


# ---------------------------------------------------------------------------
# E. 执行与验证类 Skill
# ---------------------------------------------------------------------------

def adjustment_draft(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                     order_id: str, component: str, delta: Decimal,
                     currency: str, reason: str) -> dict:
    """AdjustmentDraftSkill：创建不生效的调整草稿（L1 安全边界）。"""
    resp = call_tool(gateway, tracer, "commission.create_adjustment_draft", {
        "order_id": order_id, "case_id": case_id, "component": component,
        "amount": str(delta), "currency": currency, "reason": reason,
    }, case_id=case_id, actor="revguard-executor", scope=["commission:draft"])
    return resp["data"]


def ledger_reverse(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                   ledger_id: str, rollback_token: str,
                   idempotency_key: str) -> dict:
    """LedgerReverseSkill：用一次性回滚令牌新增反向台账，不物理删除原记录。"""
    resp = call_tool(gateway, tracer, "commission.reverse_adjustment", {
        "case_id": case_id,
        "ledger_id": ledger_id,
        "rollback_token": rollback_token,
    }, case_id=case_id, actor="revguard-executor", scope=["commission:reverse"],
        idempotency_key=idempotency_key)
    return resp["data"]


def ledger_adjust(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                  action_id: str, approval_token: str, policy_version: str,
                  idempotency_key: str) -> dict:
    """LedgerAdjustSkill：提交调整写入台账（强制审批凭证 + 幂等键）。"""
    resp = call_tool(gateway, tracer, "commission.submit_adjustment", {
        "action_id": action_id, "case_id": case_id,
        "approval_token": approval_token, "policy_version": policy_version,
    }, case_id=case_id, actor="revguard-executor", scope=["commission:write"],
        idempotency_key=idempotency_key)
    return resp["data"]


def post_action_verify(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                       order_id: str, expected_components: list[dict]) -> dict:
    """PostActionVerifySkill：独立重新查询台账验证执行结果（ADR-002）。

    不复用 Executor 的任何返回值，全部以最新独立查询为准。
    """
    resp = call_tool(gateway, tracer, "finance.get_commission_ledger", {"order_id": order_id},
                     case_id=case_id, actor="revguard-verifier", scope=["ledger:read"])
    entries = [e for e in resp["data"]["entries"] if e.get("status") == "POSTED"]
    per_component: dict[str, Decimal] = {}
    for e in entries:
        per_component[e["component"]] = per_component.get(e["component"], Decimal("0")) + Decimal(str(e["amount"]))

    checks, failed = [], []
    expected_total = Decimal("0")
    for comp in expected_components:
        if not comp["applied"]:
            continue
        expected = Decimal(comp["amount"])
        expected_total += expected
        actual = per_component.get(comp["type"], Decimal("0"))
        ok = actual == expected
        checks.append({"component": comp["type"], "expected": str(expected),
                       "actual": str(actual), "passed": ok})
        if not ok:
            failed.append(comp["type"])

    actual_total = sum(per_component.values(), Decimal("0"))
    variance = actual_total - expected_total
    return {
        "verification_status": "PASSED" if not failed and variance == 0 else "FAILED",
        "expected_amount": str(expected_total),
        "actual_amount": str(actual_total),
        "variance": str(variance),
        "component_checks": checks,
        "evidence_refs": [resp["tool_receipt"]],
        "rollback_required": bool(failed or variance != 0),
        "checked_at": utc_now(),
    }


def post_rollback_verify(gateway: ToolGateway, tracer: Tracer | None, *, case_id: str,
                         order_id: str, expected_snapshot: list[dict]) -> dict:
    """PostRollbackVerifySkill：独立查询并确认冲销后恢复到执行前台账净额。"""
    resp = call_tool(gateway, tracer, "finance.get_commission_ledger", {"order_id": order_id},
                     case_id=case_id, actor="revguard-verifier", scope=["ledger:read"])

    def _totals(entries: list[dict]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for entry in entries:
            if entry.get("status") != "POSTED":
                continue
            component = entry.get("component", "UNKNOWN")
            totals[component] = totals.get(component, Decimal("0")) + Decimal(
                str(entry.get("amount", 0))
            )
        return totals

    expected = _totals(expected_snapshot)
    actual = _totals(resp["data"]["entries"])
    components = sorted(set(expected) | set(actual))
    checks = [{
        "component": component,
        "expected": str(expected.get(component, Decimal("0"))),
        "actual": str(actual.get(component, Decimal("0"))),
        "passed": expected.get(component, Decimal("0")) == actual.get(component, Decimal("0")),
    } for component in components]
    passed = all(item["passed"] for item in checks)
    return {
        "verification_status": "PASSED" if passed else "FAILED",
        "component_checks": checks,
        "evidence_refs": [resp["tool_receipt"]],
        "checked_at": utc_now(),
    }


# ---------------------------------------------------------------------------
# F. 沉淀类 Skill
# ---------------------------------------------------------------------------

def case_to_dataset(case: dict, shared_state: dict, verification: dict) -> dict:
    """CaseToDatasetSkill：把完整案件轨迹沉淀为可回放评测样本。"""
    accepted = {"PASSED", "NOT_APPLICABLE_DRAFT_ONLY"}
    label = "GOLDEN" if verification.get("verification_status") in accepted else "BAD"
    if case.get("status") == "ROLLED_BACK":
        label = "SAFE_ROLLBACK"
    return {
        "case_id": case["case_id"],
        "label": label,
        "case_type": case["case_type"],
        "input": {"claim": case.get("claim"), "entities": case.get("entities")},
        "expected_policy_version": shared_state.get("policy_decision", {}).get("policy_version"),
        "expected_amount": shared_state.get("calculation_result", {}).get("total_commission"),
        "root_causes": shared_state.get("root_cause_report", {}).get("root_causes", []),
        "verification": verification,
        "archived_at": utc_now(),
    }


# ---------------------------------------------------------------------------
# Skill 注册表：元数据（设计文档 9.1 要求的清单字段）
# ---------------------------------------------------------------------------

def _meta(name, version, stype, desc, deps, failures, security, reusability, func):
    contract = SKILL_SCHEMAS[name]
    write_permission = security.get("write_permission")
    return {
        "name": name, "version": version, "type": stype, "description": desc,
        "input_schema": contract["input"], "output_schema": contract["output"],
        # 兼容现有文档/API 消费者；字段名从 Schema 派生，不再单独维护。
        "inputs": list(contract["input"].get("properties", {})),
        "outputs": list(contract["output"].get("properties", {})),
        "dependencies": deps,
        "failure_handling": failures, "security": security,
        "annotations": {
            "readOnlyHint": bool(security.get("read_only")
                                 or write_permission is False),
            "destructiveHint": write_permission in {"commission_post", "commission_reverse"},
            "openWorldHint": False,
        },
        "reusability": reusability, "func": func,
    }


SKILL_REGISTRY: dict[str, dict] = {m["name"]: m for m in [
    _meta("CaseNormalizeSkill", "1.0.0", "deterministic", "申诉/工单解析为标准化案件实体",
          [], ["missing_entity"], {"read_only": True, "pii": True},
          ["commission_dispute", "ticket_intake"], case_normalize),
    _meta("EntityResolveSkill", "1.0.0", "tool", "解析代理商为唯一系统实体",
          ["crm.get_partner"], ["not_found", "ambiguous"],
          {"read_only": True, "pii": True},
          ["commission_dispute", "partner_lookup"], entity_resolve),
    _meta("EvidenceCollectSkill", "1.1.0", "tool", "跨系统真实并行证据采集与完整度评分",
          ["crm.*", "contract.*", "policy.*", "finance.*"],
          ["tool_unavailable_retry", "evidence_gap"], {"read_only": True, "pii": False},
          ["commission_dispute", "batch_reconciliation", "audit"], collect_evidence),
    _meta("PolicyVersionMatchSkill", "1.0.0", "deterministic", "按业务时点匹配政策版本",
          [], ["no_effective_version", "version_conflict"],
          {"read_only": True, "pii": False},
          ["commission_dispute", "policy_simulation"], policy_version_match),
    _meta("CommissionCalculateSkill", "1.0.0", "deterministic", "规则引擎确定性佣金复算",
          ["rule_engine"], ["invalid_schema", "missing_rule", "conflicting_rule"],
          {"read_only": True, "pii": False},
          ["commission_dispute", "policy_simulation", "batch_reconciliation"], commission_calculate),
    _meta("DifferenceExplainSkill", "1.0.0", "deterministic", "差异解释与根因判定",
          [], ["evidence_conflict"], {"read_only": True, "pii": False},
          ["commission_dispute", "audit"], difference_explain),
    _meta("RiskClassifySkill", "1.0.0", "policy", "L0-L3 风险分级与审批路由判定",
          [], ["unknown_policy", "missing_threshold"], {"write_permission": False},
          ["commission_dispute", "batch_reconciliation", "any_write_action"], risk_classify),
    _meta("ApprovalRouteSkill", "1.0.0", "tool", "创建审批单并路由审批角色",
          ["workflow.create_approval"], ["workflow_unavailable"],
          {"write_permission": "approval"}, ["any_approval_needed_case"], approval_route),
    _meta("PermissionCheckSkill", "1.0.0", "policy", "执行前权限与审批凭证校验",
          [], ["auth_failed", "missing_token"], {"write_permission": False},
          ["any_write_action"], permission_check),
    _meta("IdempotencyGuardSkill", "1.0.0", "policy", "幂等键冲突检查",
          ["store"], ["idempotency_conflict"], {"write_permission": False},
          ["any_write_action"], idempotency_guard),
    _meta("AdjustmentDraftSkill", "1.0.0", "tool", "创建不生效的佣金调整草稿",
          ["commission.create_adjustment_draft"], ["tool_unavailable"],
          {"write_permission": "commission_draft"}, ["commission_dispute"], adjustment_draft),
    _meta("LedgerAdjustSkill", "2.0.0", "tool", "提交调整写入台账（签名审批凭证+幂等）",
          ["commission.submit_adjustment"], ["auth_failed", "idempotency_conflict"],
          {"write_permission": "commission_post"}, ["commission_dispute"], ledger_adjust),
    _meta("LedgerReverseSkill", "1.0.0", "tool", "验证失败后以一次性能力令牌反向冲销",
          ["commission.reverse_adjustment"],
          ["auth_failed", "token_replayed", "idempotency_conflict"],
          {"write_permission": "commission_reverse"},
          ["commission_dispute", "any_reversible_write"], ledger_reverse),
    _meta("PostActionVerifySkill", "1.0.0", "tool", "独立查询验证执行结果",
          ["finance.get_commission_ledger"], ["tool_unavailable"],
          {"read_only": True}, ["any_executed_case"], post_action_verify),
    _meta("PostRollbackVerifySkill", "1.0.0", "tool", "独立确认回滚后恢复执行前净额",
          ["finance.get_commission_ledger"], ["tool_unavailable", "rollback_variance"],
          {"read_only": True}, ["any_reversible_write"], post_rollback_verify),
    _meta("CaseToDatasetSkill", "1.0.0", "deterministic", "案件轨迹沉淀为评测样本",
          [], ["incomplete_trace"], {"read_only": False},
          ["evaluation", "knowledge_base"], case_to_dataset),
]}


def list_skills() -> list[dict]:
    """输出 Skill 清单（不含 func，供 API / 文档使用）。"""
    return [{k: v for k, v in m.items() if k != "func"} for m in SKILL_REGISTRY.values()]
