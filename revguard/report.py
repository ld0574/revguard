"""审计报告渲染器：把案件全量 Artifact 渲染为 Markdown。

报告即「执行证据沉淀」的落地物（设计文档 8.3-6 / 15），
每个案件一份，包含证据链、政策匹配、计算明细、审批、执行快照与验证结果。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

CASE_TYPE_LABELS = {
    "COMMISSION_UNDERPAYMENT": "佣金少付",
    "COMMISSION_OVERPAYMENT": "佣金多付",
    "COLLECTION_MISSING": "回款佣金缺失",
    "POLICY_MISCONFIG": "政策配置错误",
    "WRONG_POLICY_VERSION": "政策版本错误",
    "TIER_EFFECTIVE_DATE_CONFLICT": "等级生效日期冲突",
    "REFUND_NOT_REVERSED": "退款未冲销",
    "EVIDENCE_INSUFFICIENT": "证据不足",
}

SOURCE_LABELS = {
    "EMAIL": "邮件",
    "TICKET": "工单",
    "MANUAL": "人工录入",
    "API": "接口",
    "EVALUATION": "评测数据",
}

SOURCE_SYSTEM_LABELS = {
    "CRM": "CRM",
    "CRM_MOCK": "CRM",
    "CONTRACT": "合同系统",
    "CONTRACT_MOCK": "合同系统",
    "FINANCE": "财务系统",
    "FINANCE_MOCK": "财务系统",
    "COMMISSION": "佣金台账",
    "COMMISSION_MOCK": "佣金台账",
}

EVIDENCE_TYPE_LABELS = {
    "ORDER": "订单",
    "TIER_HISTORY": "等级历史",
    "CONTRACT": "合同",
    "PAYMENT_RECORD": "回款记录",
    "REFUND_RECORD": "退款记录",
    "INVOICE": "发票",
    "COMMISSION_LEDGER": "佣金台账",
    "POLICY_VERSIONS": "政策版本",
}

STRENGTH_LABELS = {
    "STRONG": "强",
    "MEDIUM": "中",
    "WEAK": "弱",
    "PENDING": "待采集",
}

STATUS_LABELS = {
    "CREATED": "已创建",
    "NORMALIZING": "信息整理中",
    "EVIDENCE_COLLECTING": "证据采集中",
    "WAITING_FOR_EVIDENCE": "等待补充证据",
    "POLICY_MATCHING": "政策匹配中",
    "CALCULATING": "佣金复算中",
    "ROOT_CAUSE_ANALYZING": "根因分析中",
    "RISK_REVIEW": "风险审核中",
    "WAITING_FOR_APPROVAL": "等待审批",
    "READY_TO_EXECUTE": "准备执行",
    "EXECUTING": "执行中",
    "VERIFYING": "验证中",
    "RESOLVED": "已解决",
    "ROLLBACK_REQUIRED": "需要回滚",
    "ROLLED_BACK": "已回滚",
    "REJECTED": "已拒绝",
    "KNOWLEDGE_ARCHIVED": "已归档",
    "CLOSED": "已关闭",
    "FAILED": "失败",
    "PASSED": "通过",
    "APPROVED": "已批准",
    "SUBMITTED": "已提交",
    "DRAFT": "草稿",
    "DRAFT_CREATED": "草稿已创建",
    "POSTED": "已入账",
    "REVERSED": "已冲销",
    "PENDING": "待处理",
    "NOT_APPLICABLE_DRAFT_ONLY": "草稿模式，无需验证",
}

RISK_LEVEL_LABELS = {
    "L0": "L0（只读诊断）",
    "L1": "L1（低风险草稿）",
    "L2": "L2（审批后执行）",
    "L3": "L3（高风险人工处理）",
}

ROLE_LABELS = {
    "FINANCE_LEAD": "财务负责人",
    "FINANCE_HEAD": "财务主管",
    "OPS_HEAD": "运营主管",
}

TIER_LABELS = {
    "GOLD": "金牌",
    "SILVER": "银牌",
    "BRONZE": "铜牌",
}

TIME_BASIS_LABELS = {
    "order_date": "订单日期",
    "payment_date": "回款日期",
    "completion_date": "完成日期",
}

COMPONENT_LABELS = {
    "SALES_COMMISSION": "销售佣金",
    "COLLECTION_COMMISSION": "回款佣金",
    "MONTHLY_INCENTIVE": "月度激励",
}

ROOT_CAUSE_LABELS = {
    "WRONG_POLICY_VERSION": "使用错误政策版本",
    "MISSING_COMPONENT": "缺少佣金组件",
    "AMOUNT_MISMATCH": "金额不一致",
    "TIER_CONFLICT": "等级冲突",
    "EVIDENCE_GAP": "证据缺口",
    "NO_DIFFERENCE": "无金额差异",
}

REASON_CODE_LABELS = {
    "READONLY_OR_ZERO_AMOUNT": "只读操作或金额为零",
    "POLICY_CONFLICT": "存在政策冲突",
    "NEGATIVE_ADJUSTMENT_REQUIRES_APPROVAL": "负向调整需要人工审批",
    "REQUIRES_HUMAN_APPROVAL": "需要人工审批",
    "BATCH_OPERATION": "批量操作",
}

EVENT_LABELS = {
    "CASE_CREATED": "案件已创建",
    "STATE_TRANSITION": "状态已变更",
    "ORDER_AUTO_RESOLVED": "订单已自动匹配",
    "EVIDENCE_GAP": "发现证据缺口",
    "EVIDENCE_COLLECTED": "证据已采集",
    "EVIDENCE_SUPPLIED": "补充证据已提交",
    "EVIDENCE_CONFLICT": "发现证据冲突",
    "POLICY_MATCHED": "政策版本已匹配",
    "CALCULATED": "佣金已复算",
    "ROOT_CAUSE": "根因已分析",
    "RISK_CLASSIFIED": "风险已分级",
    "NO_ACTION_NEEDED": "无需处理",
    "ESCALATED_MANUAL": "已转人工处理",
    "ESCALATED_TO_HUMAN": "已升级人工处理",
    "APPROVAL_DECIDED": "审批已完成",
    "DRAFT_CREATED": "调整草稿已创建",
    "EXECUTED": "调整已执行",
    "VERIFIED": "独立验证已完成",
    "ROLLED_BACK": "调整已回滚",
    "ROLLBACK_VERIFIED": "回滚验证已完成",
    "KNOWLEDGE_ARCHIVED": "案件已归档",
    "CASE_FAILED": "案件处理失败",
    "CASE_RUN_FAILED": "案件运行失败",
    "TEAM_RUN_FAILED": "协同运行失败",
    "TEAM_RUN_STARTED": "协同运行已开始",
    "DEMO_RESET": "演示数据已重置",
    "AGENT_TASK_DISPATCHED": "智能体任务已派发",
    "AGENT_TASK_STARTED": "智能体任务已开始",
    "AGENT_TASK_FAILED": "智能体任务失败",
    "AGENT_TASK_SUCCEEDED": "智能体任务成功",
    "AGENT_TASK_REASSIGNED": "智能体任务已重派",
    "AGENT_TASKS_CANCELLED": "智能体任务已取消",
    "SKILL_INVOKED": "技能已调用",
    "IDEMPOTENCY_SUPPRESSED": "重复操作已拦截",
    "AGENTTEAMS_TOOL_CALLED": "智能体团队工具已调用",
}

FORMULA_VARIABLE_LABELS = {
    "monthly_completed_orders": "当月完成订单数",
    "payment_amount": "回款金额",
    "order_amount": "订单金额",
}


def _label(value, labels: dict[str, str], default: str = "-") -> str:
    if value is None or value == "":
        return default
    return labels.get(str(value), str(value))


def _case_type_label(value) -> str:
    return _label(value, CASE_TYPE_LABELS)


def _source_label(value) -> str:
    return _label(value, SOURCE_LABELS)


def _source_system_label(value) -> str:
    return _label(value, SOURCE_SYSTEM_LABELS)


def _status_label(value) -> str:
    return _label(value, STATUS_LABELS)


def _risk_level_label(value) -> str:
    return _label(value, RISK_LEVEL_LABELS)


def _role_label(value) -> str:
    if value is None or value == "":
        return "-"
    return " + ".join(ROLE_LABELS.get(part, part) for part in str(value).split("+"))


def _component_label(value) -> str:
    return _label(value, COMPONENT_LABELS)


def _evidence_type_label(value) -> str:
    return _label(value, EVIDENCE_TYPE_LABELS)


def _strength_label(value) -> str:
    return _label(value, STRENGTH_LABELS)


def _tier_label(value) -> str:
    return _label(value, TIER_LABELS)


def _event_label(value) -> str:
    return _label(value, EVENT_LABELS)


def _localize_text(value) -> str:
    text = str(value)
    mappings = (
        COMPONENT_LABELS,
        ROOT_CAUSE_LABELS,
        TIER_LABELS,
        SOURCE_SYSTEM_LABELS,
        STATUS_LABELS,
    )
    for labels in mappings:
        for raw, label in sorted(labels.items(), key=lambda item: len(item[0]), reverse=True):
            text = text.replace(raw, label)
    return text


def _reason_label(value) -> str:
    if value is None or value == "":
        return "-"
    text = str(value)
    if text in REASON_CODE_LABELS:
        return REASON_CODE_LABELS[text]
    if text.startswith("AMOUNT_EXCEEDS_"):
        return "调整金额超过审批上限"
    if text.startswith("AMOUNT_WITHIN_"):
        return "调整金额在低风险限额内"
    if text.startswith("EVIDENCE_SCORE_") and text.endswith("_OK"):
        return "证据完整度满足自动生成草稿要求"
    if text.startswith("EVIDENCE_SCORE_") and "_BELOW_AUTO_" in text:
        return "证据完整度低于自动处理要求"
    if text.startswith("EVIDENCE_SCORE_") and "_BELOW_" in text:
        return "证据完整度低于最低要求"
    return _localize_text(text)


def _formula_label(value) -> str:
    text = "" if value is None else str(value)
    for raw, label in sorted(FORMULA_VARIABLE_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(raw, label)
    return text


def _skip_reason_label(value) -> str:
    text = "" if value is None else str(value)
    replacements = {
        "agent_tier": "代理商等级",
        "monthly_completed_orders_gte": "当月完成订单数至少",
        "payment_days_lte": "回款天数不超过",
    }
    for raw, label in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(raw, label)
    return _localize_text(_formula_label(text))


def _reason_list(values) -> str:
    return "；".join(_reason_label(value) for value in values) or "无"


def _rounding_label(value) -> str:
    if not value:
        return "-"
    parts = dict(part.split("=", 1) for part in str(value).split(",") if "=" in part)
    mode_labels = {
        "HALF_UP": "四舍五入",
        "DOWN": "向下取整",
        "UP": "向上取整",
    }
    if "scale" in parts and "mode" in parts:
        return f"保留 {parts['scale']} 位小数，舍入方式：{mode_labels.get(parts['mode'], parts['mode'])}"
    return str(value)


def _money(amount, currency="") -> str:
    value = "-" if amount is None else amount
    return f"{value} {currency}".strip()


def _token_ref(token: str | None) -> str:
    if not token:
        return "-"
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def render_audit_report(*, case: dict, state: dict, evidence: list[dict],
                        audit_events: list[dict], trace_summary: dict) -> str:
    """生成单案件审计报告（Markdown）。"""
    calc = state.get("calculation_result") or {}
    policy = state.get("policy_decision") or {}
    rca = state.get("root_cause_report") or {}
    risk = state.get("risk_decision") or {}
    approval = state.get("approval") or {}
    verification = state.get("verification") or {}
    rollback = state.get("rollback") or {}
    executions = state.get("executions") or []
    currency = calc.get("currency") or (case.get("claim") or {}).get("currency", "")
    lines: list[str] = []
    add = lines.append

    add(f"# 案件审计报告：{case['case_id']}")
    add("")
    add(f"- 案件类型：**{_case_type_label(case.get('case_type'))}**　来源：**{_source_label(case.get('source'))}**")
    add(f"- 最终状态：**{_status_label(case.get('status'))}**　风险等级：**{_risk_level_label(case.get('risk_level'))}**")
    add(f"- 代理商：{case.get('partner_name') or '-'}（`{case.get('partner_id') or '-'}`）")
    add(f"- 订单：`{case.get('order_id') or '-'}`")
    claim = case.get("claim") or {}
    if claim:
        add(f"- 申诉主张：实收 {_money(claim.get('actual_amount'), claim.get('currency'))}，"
            f"主张应有 {_money(claim.get('expected_amount'), claim.get('currency'))}")
    add(f"- 证据完整度：**{case.get('evidence_score', 0)}**")
    add("")

    # ---------------------------------------------------------- 证据链
    add("## 1. 证据链")
    add("")
    if evidence:
        add("| 证据 | 来源系统 | 引用 | 强度 | 工具回执 |")
        add("|---|---|---|---|---|")
        for ev in evidence:
            add(f"| {_evidence_type_label(ev['type'])} | {_source_system_label(ev['source_system'])} | {ev['source_ref']} "
                f"| {_strength_label(ev.get('strength'))} | `{ev.get('tool_receipt') or '-'}` |")
    else:
        add("（无证据记录）")
    gaps = state.get("evidence_gaps") or []
    if gaps:
        add("")
        add("**证据缺口：**")
        for g in gaps:
            add(f"- {g}")
    add("")

    # ---------------------------------------------------------- 政策匹配
    add("## 2. 按业务时点匹配政策版本")
    add("")
    if policy:
        add(f"- 选中版本：**{policy.get('policy_id')} {policy.get('policy_version')}**")
        add(f"- 判断时点：{policy.get('decision_date')}（依据：{_label(policy.get('time_basis'), TIME_BASIS_LABELS)}）")
        for clause in policy.get("cited_clauses") or []:
            add(f"- 引用条款 {clause.get('clause_id')}：{_localize_text(clause.get('text'))}")
        for ex in policy.get("excluded_versions") or []:
            add(f"- 排除版本 {ex.get('version')}：{_localize_text(ex.get('reason'))}")
        for conflict in policy.get("unresolved_conflicts") or []:
            add(f"- ⚠️ 未消解冲突：{_localize_text(conflict)}")
        tier = state.get("tier_resolution") or {}
        if tier:
            add(f"- 订单时点等级：**{_tier_label(tier.get('tier'))}**（自 {tier.get('effective_from')} 生效）")
            if tier.get("conflict"):
                add(f"- ⚠️ 等级冲突：{_localize_text(tier['conflict'])}")
    else:
        add("（未执行政策匹配）")
    add("")

    # ---------------------------------------------------------- 复算明细
    add("## 3. 确定性复算（规则引擎）")
    add("")
    if calc:
        add("| 组件 | 公式 | 代入 | 金额 | 是否适用 |")
        add("|---|---|---|---|---|")
        for c in calc.get("components", []):
            applied = "✅" if c.get("applied") else "—"
            note = "" if c.get("applied") else "<br>跳过：" + "；".join(
                _skip_reason_label(reason) for reason in c.get("skip_reasons") or []
            )
            add(f"| {_component_label(c['type'])} | `{_formula_label(c.get('formula', ''))}` | `{_formula_label(c.get('substituted', ''))}` "
                f"| {_money(c.get('amount'), currency)} | {applied}{note} |")
        add("")
        add(f"**复算合计：{_money(calc.get('total_commission'), currency)}**　"
            f"舍入：{_rounding_label(calc.get('rounding_rule'))}　"
            f"哈希：`{calc.get('calculation_hash', '')[:24]}…`")
    else:
        add("（未执行复算）")
    add("")

    # ---------------------------------------------------------- 差异与根因
    add("## 4. 差异解释与根因")
    add("")
    if rca:
        add("| 组件 | 应有 | 台账实有 | 差额 | 根因 |")
        add("|---|---|---|---|---|")
        for d in rca.get("diffs", []):
            add(f"| {_component_label(d['component'])} | {_money(d['expected'], currency)} "
                f"| {_money(d['posted'], currency)} | {_money(d['delta'], currency)} "
                f"| {_label(d.get('root_cause'), ROOT_CAUSE_LABELS, '一致')} |")
        add("")
        add(f"**总差额：{_money(rca.get('total_delta'), currency)}**　"
            f"根因分类：**{_reason_list(rca.get('root_causes') or [])}**")
        for d in rca.get("diffs", []):
            if d.get("explanation"):
                add(f"- {_localize_text(d['explanation'])}")
    else:
        add("（未执行根因分析）")
    add("")

    # ---------------------------------------------------------- 风险与审批
    add("## 5. 风险分级与审批")
    add("")
    if risk:
        add(f"- 风险等级：**{_risk_level_label(risk.get('risk_level'))}**（{_reason_list(risk.get('reason_codes') or [])}）")
        add(f"- 需要审批：{'是' if risk.get('approval_required') else '否'}"
            + (f"，审批角色：{_role_label(risk.get('approver_role'))}" if risk.get("approver_role") else ""))
    if approval:
        add(f"- 审批单：`{approval.get('approval_id')}`　状态：**{_status_label(approval.get('status'))}**")
        if approval.get("approver"):
            add(f"- 审批人：{approval.get('approver')}（{approval.get('decided_at', '-')}）"
                f"　意见：{approval.get('comment', '-')}")
    add("")

    # ---------------------------------------------------------- 执行
    add("## 6. 受控执行")
    add("")
    if executions:
        for ex in executions:
            add(f"### 动作 `{ex['action_id']}`（{_component_label(ex['component'])}，{_money(ex['amount'], ex.get('currency', currency))}）")
            add(f"- 幂等键：`{ex.get('idempotency_key')}`　状态：{_status_label(ex.get('status'))}"
                f"　回滚令牌指纹：`{_token_ref(ex.get('rollback_token'))}`")
            before = sum((Decimal(str(e.get("amount", 0))) for e in ex.get("before_snapshot", [])
                          if e.get("status") == "POSTED"), Decimal("0"))
            after = sum((Decimal(str(e.get("amount", 0))) for e in ex.get("after_snapshot", [])
                         if e.get("status") == "POSTED"), Decimal("0"))
            if ex.get("action_type") == "DRAFT":
                add("- L1 安全边界：仅生成不生效草稿，未写入资金台账")
            else:
                add(f"- 执行前台账合计：{before:.2f}　执行后台账合计：{after:.2f}")
    else:
        add("（无执行动作）")
    add("")

    # ---------------------------------------------------------- 验证
    add("## 7. 独立验证（重新查询，非执行主体自证）")
    add("")
    if verification:
        add(f"- 验证结论：**{_status_label(verification.get('verification_status'))}**")
        add(f"- 应有合计：{_money(verification.get('expected_amount'), currency)}　"
            f"实际合计：{_money(verification.get('actual_amount'), currency)}　"
            f"偏差：{_money(verification.get('variance'), currency)}")
        for chk in verification.get("component_checks") or []:
            mark = "✅" if chk.get("passed") else "❌"
            add(f"  - {mark} {_component_label(chk['component'])}：应有 {chk['expected']}，实际 {chk['actual']}")
    else:
        add("（未执行验证）")
    add("")

    if rollback:
        add("## 8. 回滚与冲销验证")
        add("")
        for reversal in rollback.get("reversals") or []:
            add(f"- 冲销记录 `{reversal.get('ledger_id')}` 对冲 "
                f"`{reversal.get('reversal_of')}`，金额 "
                f"{_money(reversal.get('amount'), reversal.get('currency', currency))}")
        rb_verify = rollback.get("verification") or {}
        add(f"- 回滚后独立验证：**{_status_label(rb_verify.get('verification_status'))}**")
        add("")

    # ---------------------------------------------------------- 调用链与审计
    add("## 9. 调用链与审计摘要" if rollback else "## 8. 调用链与审计摘要")
    add("")
    add(f"- 调用链记录数：{trace_summary.get('span_count')}　"
        f"总耗时：{trace_summary.get('total_duration_ms')} 毫秒　"
        f"错误记录数：{len(trace_summary.get('error_spans') or [])}")
    add("")
    add("### 关键审计事件")
    add("")
    add("| 时间 | 操作者 | 事件 |")
    add("|---|---|---|")
    for ev in audit_events:
        add(f"| {ev['created_at']} | {ev['actor']} | {_event_label(ev['event'])} |")
    add("")
    add("---")
    add(f"*报告由 RevGuard 自动生成；完整调用链文件见 `data/outputs/traces/{case['case_id']}.json`。*")
    return "\n".join(lines)
