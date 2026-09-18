#!/usr/bin/env python3
"""导出官网静态回放数据包（revguard.replay/v1）。

在 202 Docker 内对真实运行栈执行：读取 ``/api/v1/cases/{id}/dashboard`` 快照与
``/api/v1/health``，生成 ``revguard/website/data/`` 下供 GitHub Pages 使用的脱敏 JSON。
官网只做回放，不调用任何 RevGuard API，因此评委看到的每一步都取自一次真实运行记录。

安全边界（导出即脱敏，回放页不再二次处理）：

- 不写入任何凭据、Token、Cookie；
- 内部主机名、IP 与容器地址替换为稳定占位符；
- Matrix 用户 ID / 房间 ID 不进入数据包，只保留角色与事件标识的哈希前缀；
- ERPNext 单据只保留系统类型与单据编号，不保留可反推内网拓扑的 URL。

用法::

  python scripts/export_case_replay.py \
    --base-url http://localhost:19088 \
    --api-key-file /root/revguard-0.6.0-dev/.runtime/demo-viewer-key \
    --case CASE-2026-0001 --case CASE-2026-0008 \
    --output-dir website/data
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "revguard.replay/v1"

# 回放页标签按案件终态生成：正常闭环与偏差恢复用不同配色，避免两条记录混在一起。
DISPLAY_BY_STATUS: dict[str, dict[str, str]] = {
    "CLOSED": {"title": "正常闭环", "summary": "取证 → 复算 → 真人审批 → 执行 → 独立复核", "tone": "normal"},
    "ROLLED_BACK": {"title": "偏差与冲销恢复", "summary": "复核发现偏差 → 禁止重试 → 冲销 → 恢复复核", "tone": "alert"},
}

# 脱敏规则：先替换 URL 与主机名，再兜底替换残留的 IPv4 与内部域名。
URL_RE = re.compile(r"https?://[^\s\"'<>]+")
HOST_RE = re.compile(
    r"\b(?:revguard|agentteams|matrix|erpnext|grafana|prometheus|tempo|loki|alloy)"
    r"[A-Za-z0-9._-]*\.(?:internal|local|io|net|cn)\b",
    re.IGNORECASE,
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
MATRIX_ID_RE = re.compile(r"@[A-Za-z0-9._=+/-]+:[A-Za-z0-9.\-:]+")
MATRIX_ROOM_RE = re.compile(r"![A-Za-z0-9._=+/-]+:[A-Za-z0-9.\-:]+")
SECRET_KEY_RE = re.compile(r"(token|secret|password|cookie|api[_-]?key|credential)", re.IGNORECASE)

# 回放页使用的阶段定义；与 demo-ui 的治理流水线保持同一套命名。
STAGES: list[dict[str, Any]] = [
    {"id": "intake", "title": "受理", "actor": "revguard-intake"},
    {"id": "evidence", "title": "取证", "actor": "revguard-evidence"},
    {"id": "policy", "title": "政策", "actor": "revguard-policy"},
    {"id": "calculation", "title": "计算", "actor": "revguard-calculation"},
    {"id": "rootcause", "title": "根因", "actor": "revguard-rootcause"},
    {"id": "risk", "title": "风险", "actor": "revguard-risk"},
    {"id": "approval", "title": "真人审批", "actor": "human"},
    {"id": "execution", "title": "受限执行", "actor": "revguard-executor"},
    {"id": "verification", "title": "独立复核", "actor": "revguard-verifier"},
    {"id": "recovery", "title": "冲销与恢复", "actor": "revguard-executor"},
    {"id": "closing", "title": "结案", "actor": "revguard-orchestrator"},
]

STAGE_BY_SKILL: dict[str, str] = {
    "OrchestratorHandshake": "intake",
    "CaseNormalizeSkill": "intake",
    "EntityResolveSkill": "intake",
    "EvidenceCollectSkill": "evidence",
    "PolicyVersionMatchSkill": "policy",
    "CommissionCalculateSkill": "calculation",
    "DifferenceExplainSkill": "rootcause",
    "RiskClassifySkill": "risk",
    "ApprovalRouteSkill": "risk",
    "PermissionCheckSkill": "execution",
    "IdempotencyGuardSkill": "execution",
    "AdjustmentDraftSkill": "execution",
    "LedgerAdjustSkill": "execution",
    "LedgerReverseSkill": "recovery",
    "PostActionVerifySkill": "verification",
    "PostRollbackVerifySkill": "recovery",
    "CaseToDatasetSkill": "closing",
}

SKILL_LABELS = {
    "OrchestratorHandshake": "协同任务编排",
    "CaseNormalizeSkill": "整理案件信息",
    "EntityResolveSkill": "匹配代理商与订单",
    "EvidenceCollectSkill": "收集跨系统证据",
    "PolicyVersionMatchSkill": "匹配业务时点政策",
    "CommissionCalculateSkill": "重新计算应付佣金",
    "DifferenceExplainSkill": "分析佣金差异原因",
    "RiskClassifySkill": "判断案件风险等级",
    "ApprovalRouteSkill": "确定审批流程",
    "PermissionCheckSkill": "检查执行权限",
    "IdempotencyGuardSkill": "防止重复执行",
    "AdjustmentDraftSkill": "生成佣金调整草稿",
    "LedgerAdjustSkill": "更新佣金台账",
    "LedgerReverseSkill": "冲销佣金调整",
    "PostActionVerifySkill": "独立核验调整结果",
    "PostRollbackVerifySkill": "复核回滚结果",
    "CaseToDatasetSkill": "归档案件经验",
    "HumanIdentityVerification": "验证真人身份",
    "HumanApprovalGate": "人工审批门禁",
}

COMPONENT_LABELS = {
    "SALES_COMMISSION": "销售佣金",
    "COLLECTION_COMMISSION": "回款佣金",
    "MONTHLY_INCENTIVE": "月度激励",
}

JOB_LABELS = {
    "revguard-intake": "受理智能体",
    "revguard-evidence": "取证智能体",
    "revguard-policy": "政策智能体",
    "revguard-calculation": "计算智能体",
    "revguard-rootcause": "根因智能体",
    "revguard-risk": "风险智能体",
    "revguard-executor": "执行智能体",
    "revguard-verifier": "验证智能体",
    "revguard-orchestrator": "编排智能体",
    "revguard-knowledge": "知识智能体",
}


def sanitize(value: Any, *, key: str = "") -> Any:
    """递归脱敏：凭据字段整体丢弃，字符串按规则替换内部标识。"""
    if SECRET_KEY_RE.search(key):
        return "<已脱敏>"
    if isinstance(value, dict):
        return {item_key: sanitize(item, key=item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, key=key) for item in value]
    if isinstance(value, str):
        text = URL_RE.sub("<内部地址已脱敏>", value)
        text = MATRIX_ID_RE.sub("<matrix-user>", text)
        text = MATRIX_ROOM_RE.sub("<matrix-room>", text)
        text = HOST_RE.sub("<internal-host>", text)
        return IPV4_RE.sub("<internal-ip>", text)
    return value


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def short_hash(value: str | None) -> str | None:
    if not value:
        return None
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if text.endswith("+00"):
        text += ":00"
    elif text.endswith("-00"):
        text += ":00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def iso(value: str | None) -> str | None:
    parsed = parse_time(value)
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z") if parsed else None


def get_json(base_url: str, path: str, api_key: str) -> Any:
    """只允许对显式传入的 http/https 演示栈地址发起只读请求。"""
    url = base_url.rstrip("/") + path
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"仅支持 http/https 地址：{url}")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - 协议已白名单校验
        return json.loads(response.read().decode("utf-8"))


def money(value: Any, currency: str) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"{value} {currency}"


def build_steps(payload: dict[str, Any], window: dict[str, Any]) -> list[dict[str, Any]]:
    case = payload.get("case") or {}
    currency = (case.get("claim") or {}).get("currency") or ""
    facts = case.get("facts") or {}
    claim = case.get("claim") or {}
    approval = payload.get("approval") or {}
    verification = payload.get("verification") or {}
    executions = payload.get("executions") or []
    evidence = payload.get("evidence") or []
    policy = case.get("policy_decision") or {}
    calculation = case.get("calculation_result") or {}
    root_cause = case.get("root_cause_report") or {}
    risk = case.get("risk_decision") or {}
    tier = case.get("tier_resolution") or {}
    audit_events = window["events"]
    stage_times = window["stage_times"]

    def event_time(name: str, stage: str | None = None) -> str | None:
        """阶段时点优先取本次运行的真实 Span，其次取同一代审计事件。"""
        if stage and stage_times.get(stage):
            return stage_times[stage]
        for item in audit_events:
            if item.get("event") == name:
                return iso(item.get("created_at"))
        return stage_times.get(stage) if stage else None

    def event_detail(name: str) -> dict[str, Any]:
        for item in audit_events:
            if item.get("event") == name:
                detail = item.get("detail") or {}
                return detail if isinstance(detail, dict) else {"value": detail}
        return {}

    steps: list[dict[str, Any]] = []
    steps.append({
        "stage": "intake",
        "title": "受理佣金差异申诉",
        "subtitle": case.get("case_id"),
        "at": event_time("CASE_CREATED", "intake"),
        "bullets": [
            f"来源 {case.get('source') or '—'} · 优先级 {case.get('priority') or '—'}",
            f"订单 {case.get('order_id') or '—'} · 代理商 {case.get('partner_name') or case.get('partner_id') or '—'}",
            f"申诉口径：台账 {money(claim.get('actual_amount'), currency)}，声明应为 {money(claim.get('expected_amount'), currency)}",
        ],
        "facts": {
            "订单金额": money(facts.get("order_amount"), currency),
            "回款": money(facts.get("payment_amount"), currency),
            "回款天数": facts.get("payment_days"),
            "业务时点": facts.get("order_date"),
        },
    })

    evidence_rows = []
    for item in evidence:
        metadata = item.get("source_metadata") or {}
        evidence_rows.append({
            "id": item.get("evidence_id"),
            "type": item.get("type"),
            "source": item.get("source_system"),
            "document": item.get("source_ref"),
            "provenance": item.get("provenance_kind"),
            "strength": item.get("strength"),
            "hash": item.get("content_hash"),
            "receipt": item.get("tool_receipt"),
            "provider": metadata.get("provider"),
            "latency_ms": metadata.get("latency_ms"),
            "http_status": metadata.get("http_status"),
            "collected_at": iso(item.get("collected_at")),
            "collected_by": JOB_LABELS.get(item.get("collected_by"), item.get("collected_by")),
        })
    steps.append({
        "stage": "evidence",
        "title": "跨系统只读取证",
        "subtitle": f"{len(evidence_rows)} 条强证据",
        "at": event_time("EVIDENCE_COLLECTED", "evidence"),
        "bullets": [
            f"来源系统：{', '.join(sorted({str(row['source']) for row in evidence_rows if row['source']})) or '—'}",
            "每条证据都带来源系统、单据编号、内容哈希与工具回执，可逐条复核",
        ],
        "evidence": evidence_rows,
    })

    excluded = policy.get("excluded_versions") or []
    clauses = policy.get("cited_clauses") or []
    steps.append({
        "stage": "policy",
        "title": "按业务时点回溯政策版本",
        "subtitle": policy.get("policy_version"),
        "at": event_time("POLICY_MATCHED", "policy"),
        "bullets": [
            f"业务时点 {policy.get('decision_date')}，时点依据 {policy.get('time_basis')}",
            f"采用 {policy.get('policy_id')} / {policy.get('policy_version')}，置信度 {policy.get('confidence')}",
            f"排除 {len(excluded)} 个不覆盖业务时点的历史版本" if excluded else "无冲突版本需要排除",
        ],
        "policy": {
            "排除版本": [
                f"{item.get('version')}（{item.get('reason')}）" if isinstance(item, dict) else str(item)
                for item in excluded
            ],
            "引用条款": [item.get("text") if isinstance(item, dict) else str(item) for item in clauses],
            "等级解析": f"{tier.get('tier')}（生效 {tier.get('effective_from')}）" if tier else None,
        },
    })

    components = calculation.get("components") or []
    steps.append({
        "stage": "calculation",
        "title": "确定性金额内核复算",
        "subtitle": money(calculation.get("total_commission"), currency),
        "at": event_time("CALCULATED", "calculation"),
        "bullets": [
            f"合计应付佣金 {money(calculation.get('total_commission'), currency)}，台账 {money(claim.get('actual_amount'), currency)}",
            f"取整规则 {calculation.get('rounding_rule')}，金额与政策规则分离于模型之外",
            f"计算哈希 {calculation.get('calculation_hash')}",
        ],
        "components": [
            {
                "name": COMPONENT_LABELS.get(item.get("type"), item.get("type")),
                "amount": money(item.get("amount"), currency),
                "applied": item.get("applied"),
            }
            for item in components
        ],
    })

    diffs = root_cause.get("diffs") or []
    steps.append({
        "stage": "rootcause",
        "title": "解释差异根因",
        "subtitle": "、".join(root_cause.get("root_causes") or []) or None,
        "at": event_time("ROOT_CAUSE", "rootcause"),
        "bullets": [
            f"总差额 {money(root_cause.get('total_delta'), currency)}",
            f"根因 {', '.join(root_cause.get('root_causes') or []) or '—'}，置信度 {root_cause.get('confidence')}",
        ],
        "diffs": [
            {
                "component": COMPONENT_LABELS.get(item.get("component"), item.get("component")),
                "posted": money(item.get("posted"), currency),
                "expected": money(item.get("expected"), currency),
                "delta": money(item.get("delta"), currency),
            }
            for item in diffs
        ],
    })

    steps.append({
        "stage": "risk",
        "title": "风险分级与审批路由",
        "subtitle": risk.get("risk_level"),
        "at": event_time("RISK_CLASSIFIED", "risk"),
        "bullets": [
            f"风险等级 {risk.get('risk_level')}，原因码 {', '.join(risk.get('reason_codes') or []) or '—'}",
            f"审批角色 {approval.get('approver_role') or risk.get('approver_role')}，执行约束 max_amount={money((risk.get('execution_constraints') or {}).get('max_amount'), currency)}",
            "风险策略只决定边界，不参与金额计算",
        ],
    })

    identity = approval.get("human_identity") or {}
    steps.append({
        "stage": "approval",
        "title": "真人在线审批",
        "subtitle": approval.get("status"),
        "at": event_time("APPROVAL_DECIDED", "approval") or iso(approval.get("decided_at")),
        "bullets": [
            f"审批人 {identity.get('display_name') or approval.get('approver')}（{approval.get('approver_role')}），身份认证方式 {identity.get('auth_method')}",
            f"认证时间 {iso(datetime.fromtimestamp(identity['auth_time'], tz=UTC).isoformat()) if identity.get('auth_time') else '—'}",
            f"批准金额上限 {money(approval.get('amount'), approval.get('currency') or currency)}，审批单 {approval.get('approval_id')}",
            f"Matrix 事件哈希 {short_hash(approval.get('matrix_event_id'))}，身份断言 {approval.get('human_assertion_id_ref')}",
        ],
        "quota": [
            {"component": COMPONENT_LABELS.get(key, key), "limit": money(value, approval.get("currency") or currency)}
            for key, value in (approval.get("component_quota") or {}).items()
        ],
        "comment": approval.get("comment"),
        "action_summary": approval.get("action_summary"),
    })

    execution_rows = []
    for item in executions:
        ledger = item.get("ledger_entry") or {}
        execution_rows.append({
            "action_id": item.get("action_id"),
            "component": COMPONENT_LABELS.get(item.get("component"), item.get("component")),
            "action_type": item.get("action_type"),
            "amount": money(item.get("amount"), item.get("currency") or currency),
            "status": item.get("status"),
            "operation_id": item.get("operation_id"),
            "idempotency_key": item.get("idempotency_key"),
            "ledger_status": ledger.get("status"),
            "before": item.get("before_snapshot"),
            "after": item.get("after_snapshot"),
        })
    steps.append({
        "stage": "execution",
        "title": "受控执行写入资金台账",
        "subtitle": f"{len(execution_rows)} 笔操作",
        "at": event_time("EXECUTED", "execution"),
        "bullets": [
            "执行前校验权限范围与幂等键，写入使用一次性的短时能力令牌",
            "每笔操作保留操作 ID、幂等键与写入前后台账快照",
        ],
        "executions": execution_rows,
    })

    steps.append({
        "stage": "verification",
        "title": "独立主体复核",
        "subtitle": verification.get("verification_status"),
        "at": event_time("VERIFIED", "verification") or iso(verification.get("checked_at")),
        "bullets": [
            f"复核读取到的台账金额 {money(verification.get('actual_amount'), currency)}，预期 {money(verification.get('expected_amount'), currency)}",
            f"差异 {money(verification.get('variance'), currency)}，是否需要回滚：{'是' if verification.get('rollback_required') else '否'}",
            "复核由独立验证智能体发起，不复用执行阶段的会话",
        ],
        "checks": [
            {
                "component": COMPONENT_LABELS.get(item.get("component"), item.get("component")),
                "expected": money(item.get("expected"), currency),
                "actual": money(item.get("actual"), currency),
                "passed": item.get("passed"),
            }
            for item in (verification.get("component_checks") or [])
        ],
    })

    rollback_detail = event_detail("ROLLED_BACK")
    rollback_verify = event_detail("ROLLBACK_VERIFIED")
    if rollback_detail or rollback_verify:
        steps.append({
            "stage": "recovery",
            "title": "冲销与恢复复核",
            "subtitle": rollback_verify.get("verification_status"),
            "at": event_time("ROLLBACK_VERIFIED", "recovery") or event_time("ROLLED_BACK"),
            "bullets": [
                f"发现偏差后不直接重试，保留原操作 ID {rollback_detail.get('operation_id')}",
                f"冲销分录 {rollback_detail.get('entries')} 条，与原操作关联",
                f"恢复后复核结论 {rollback_verify.get('verification_status')}，运输方式 {rollback_verify.get('transport')}",
            ],
            "checks": [
                {
                    "component": COMPONENT_LABELS.get(item.get("component"), item.get("component")),
                    "expected": money(item.get("expected"), currency),
                    "actual": money(item.get("actual"), currency),
                    "passed": item.get("passed"),
                }
                for item in (rollback_verify.get("component_checks") or [])
            ],
        })

    steps.append({
        "stage": "closing",
        "title": "终态与审计留痕",
        "subtitle": case.get("status"),
        "at": event_time("STATE_TRANSITION", "closing") or iso(case.get("updated_at")),
        "bullets": [
            f"案件终态 {case.get('status')}，状态版本 {case.get('_state_version')}",
            f"本案件审计事件 {len(audit_events)} 条，记录编号 {case.get('recording_id')}",
            "审计链按顺序追加，任何一条被改动都会破坏后续哈希",
        ],
    })
    return steps


def build_window(payload: dict[str, Any], case_id: str) -> dict[str, Any]:
    """把回放范围收敛到「最后一次真实运行」。

    审计链是跨排练累积的：同一案件可能保留多代事件。这里以本次运行 Trace 的最早
    Span 作为下界，只保留该时刻之后的事件，避免把不同排练的画面拼成一条时间线。
    """
    trace = payload.get("trace") or {}
    spans = trace.get("spans") or []
    starts = [parsed for parsed in (parse_time(span.get("started_at")) for span in spans) if parsed]
    run_start = min(starts) if starts else None
    ends = [parsed for parsed in (parse_time(span.get("ended_at")) for span in spans) if parsed]
    run_end = max(ends) if ends else None

    stage_times: dict[str, str] = {}
    for span in spans:
        name = span.get("name")
        stage = STAGE_BY_SKILL.get(name)
        if stage is None and span.get("kind") == "AGENT":
            stage = "approval" if "Human" in str(name) else None
        if not stage:
            continue
        started = parse_time(span.get("started_at"))
        if started and (stage not in stage_times or started < parse_time(stage_times[stage])):
            stage_times[stage] = iso(span.get("started_at"))

    events = [item for item in (payload.get("audit_events") or []) if item.get("case_id") == case_id]
    events.sort(key=lambda item: item.get("seq") or 0)
    if run_start:
        events = [item for item in events if (parse_time(item.get("created_at")) or run_start) >= run_start]
    return {"run_start": run_start, "run_end": run_end, "events": events, "stage_times": stage_times}


def build_bundle(case_id: str, payload: dict[str, Any], health: dict[str, Any]) -> dict[str, Any]:
    case = payload.get("case") or {}
    currency = (case.get("claim") or {}).get("currency") or ""
    claim = case.get("claim") or {}
    verification = payload.get("verification") or {}
    approval = payload.get("approval") or {}
    trace = payload.get("trace") or {}
    tasks = payload.get("agent_tasks") or []
    window = build_window(payload, case_id)
    audit_events = window["events"]
    chain_ok = all(
        audit_events[index].get("previous_hash") == audit_events[index - 1].get("row_hash")
        for index in range(1, len(audit_events))
    )

    spans = []
    for span in trace.get("spans") or []:
        name = span.get("name")
        stage = STAGE_BY_SKILL.get(name)
        if stage is None and span.get("kind") == "AGENT":
            stage = "approval" if "Human" in str(name) else None
        spans.append({
            "sequence": span.get("trace_sequence"),
            "kind": span.get("kind"),
            "name": name,
            "label": SKILL_LABELS.get(name, name),
            "actor": JOB_LABELS.get(span.get("actor"), span.get("actor")),
            "status": span.get("status"),
            "duration_ms": span.get("duration_ms"),
            "started_at": iso(span.get("started_at")),
            "stage": stage,
        })

    workers = sorted({JOB_LABELS.get(task.get("assigned_actor"), task.get("assigned_actor")) for task in tasks})
    team_run = case.get("team_run") or {}
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "release": health.get("release"),
        "case": {
            "case_id": case.get("case_id"),
            "type": case.get("case_type"),
            "status": case.get("status"),
            "recording_id": case.get("recording_id"),
            "state_version": case.get("_state_version"),
            "order_id": case.get("order_id"),
            "partner_id": case.get("partner_id"),
            "partner_name": case.get("partner_name"),
            "currency": currency,
            "created_at": iso(case.get("created_at")),
            "updated_at": iso(case.get("updated_at")),
            "description": case.get("description"),
            "execution_mode": case.get("execution_mode"),
            "team_run": {
                "status": team_run.get("status"),
                "phase": team_run.get("phase"),
                "total_tasks": team_run.get("total_tasks"),
                "completed_tasks": team_run.get("completed_tasks"),
                "workers": [worker for worker in workers if worker],
            },
        },
        "headline": {
            "expected": money(claim.get("expected_amount"), currency),
            "posted": money(claim.get("actual_amount"), currency),
            "verified": money(verification.get("actual_amount"), currency),
            "variance": money(verification.get("variance"), currency),
            "verification_status": verification.get("verification_status"),
            "approved_by": (approval.get("human_identity") or {}).get("display_name") or approval.get("approver"),
            "approval_status": approval.get("status"),
        },
        "run": {
            "started_at": iso(window["run_start"].isoformat()) if window["run_start"] else None,
            "ended_at": iso(window["run_end"].isoformat()) if window["run_end"] else None,
            "wall_duration_ms": trace.get("wall_duration_ms"),
            "span_count": trace.get("span_count"),
        },
        "steps": build_steps(payload, window),
        "trace": {
            "span_count": trace.get("span_count"),
            "wall_duration_ms": trace.get("wall_duration_ms"),
            "spans": spans,
        },
        "audit": {
            "count": len(audit_events),
            "first_seq": audit_events[0].get("seq") if audit_events else None,
            "last_seq": audit_events[-1].get("seq") if audit_events else None,
            "head_hash": audit_events[-1].get("row_hash") if audit_events else None,
            "chain_ok": chain_ok,
        },
        "provenance": {
            "snapshot_sha256": digest(payload),
            "source_release": health.get("release"),
            "capture_kind": "CAPTURED_FROM_RUNTIME",
            "health_release": health.get("release"),
            "read_replica": health.get("read_replica"),
            "backend": health.get("backend"),
        },
        "disclosure": payload.get("disclosure") or {},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 RevGuard 官网静态回放数据包")
    parser.add_argument("--base-url", default="http://localhost:19088")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--case", action="append", dest="cases", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("website/data"))
    args = parser.parse_args(argv)

    api_key = args.api_key
    if not api_key and args.api_key_file:
        api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        parser.error("必须提供 --api-key 或 --api-key-file")
    cases = args.cases or ["CASE-82822305", "CASE-3083A688"]

    health = get_json(args.base_url, "/api/v1/health", api_key)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for case_id in cases:
        payload = sanitize(get_json(args.base_url, f"/api/v1/cases/{case_id}/dashboard", api_key))
        if not payload.get("case"):
            print(f"案件 {case_id} 无数据，跳过", file=sys.stderr)
            continue
        bundle = sanitize(build_bundle(case_id, payload, health))
        # 回放页只按文件名取数据，保持与案例编号一一对应。
        path = args.output_dir / f"{case_id.lower()}.json"
        path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        case = bundle.get("case") or {}
        display = DISPLAY_BY_STATUS.get(
            str(case.get("status") or ""), {"title": "运行记录", "summary": "", "tone": "normal"}
        )
        written.append(
            {
                "case_id": case_id,
                "file": path.name,
                "status": case.get("status"),
                "order_id": case.get("order_id"),
                "title": display["title"],
                "summary": display["summary"],
                "tone": display["tone"],
                "steps": len(bundle["steps"]),
                "spans": len(bundle["trace"]["spans"]),
                "audit_events": bundle["audit"]["count"],
                "wall_duration_ms": (bundle.get("run") or {}).get("wall_duration_ms"),
            }
        )
        print(json.dumps(written[-1], ensure_ascii=False))
    if not written:
        print("没有任何案件被导出", file=sys.stderr)
        return 1
    written.sort(key=lambda item: 0 if item.get("status") == "CLOSED" else 1)
    index = args.output_dir / "index.json"
    index.write_text(json.dumps({
        "schema": SCHEMA,
        "release": health.get("release"),
        "capture": {
            "kind": "CAPTURED_FROM_RUNTIME",
            "source_release": health.get("release"),
            "exported_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        },
        "cases": written,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
