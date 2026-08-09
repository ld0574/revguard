"""审计报告渲染器：把案件全量 Artifact 渲染为 Markdown。

报告即「执行证据沉淀」的落地物（设计文档 8.3-6 / 15），
每个案件一份，包含证据链、政策匹配、计算明细、审批、执行快照与验证结果。
"""
from __future__ import annotations

from decimal import Decimal
import hashlib


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
    add(f"- 案件类型：`{case.get('case_type')}`　来源：`{case.get('source')}`")
    add(f"- 最终状态：**{case.get('status')}**　风险等级：**{case.get('risk_level') or '-'}**")
    add(f"- 代理商：{case.get('partner_name') or '-'}（`{case.get('partner_id') or '-'}`）")
    add(f"- 订单：`{case.get('order_id') or '-'}`")
    claim = case.get("claim") or {}
    if claim:
        add(f"- 申诉主张：实收 {_money(claim.get('actual_amount'), claim.get('currency'))}，"
            f"主张应有 {_money(claim.get('expected_amount'), claim.get('currency'))}")
    add(f"- 证据完整度：**{case.get('evidence_score', 0)}**")
    add("")

    # ---------------------------------------------------------- 证据链
    add("## 1. 证据链（Evidence Package）")
    add("")
    if evidence:
        add("| 证据 | 来源系统 | 引用 | 强度 | 工具回执 |")
        add("|---|---|---|---|---|")
        for ev in evidence:
            add(f"| {ev['type']} | {ev['source_system']} | {ev['source_ref']} "
                f"| {ev.get('strength', '-')} | `{ev.get('tool_receipt') or '-'}` |")
    else:
        add("（无证据记录）")
    gaps = state.get("evidence_gaps") or []
    if gaps:
        add("")
        add("**Evidence Gap：**")
        for g in gaps:
            add(f"- {g}")
    add("")

    # ---------------------------------------------------------- 政策匹配
    add("## 2. 政策版本匹配（Policy Time Travel）")
    add("")
    if policy:
        add(f"- 选中版本：**{policy.get('policy_id')} {policy.get('policy_version')}**")
        add(f"- 判断时点：{policy.get('decision_date')}（依据字段 `{policy.get('time_basis')}`）")
        for clause in policy.get("cited_clauses") or []:
            add(f"- 引用条款 {clause.get('clause_id')}：{clause.get('text')}")
        for ex in policy.get("excluded_versions") or []:
            add(f"- 排除版本 {ex.get('version')}：{ex.get('reason')}")
        for conflict in policy.get("unresolved_conflicts") or []:
            add(f"- ⚠️ 未消解冲突：{conflict}")
        tier = state.get("tier_resolution") or {}
        if tier:
            add(f"- 订单时点等级：**{tier.get('tier')}**（自 {tier.get('effective_from')} 生效）")
            if tier.get("conflict"):
                add(f"- ⚠️ 等级冲突：{tier['conflict']}")
    else:
        add("（未执行政策匹配）")
    add("")

    # ---------------------------------------------------------- 复算明细
    add("## 3. 确定性复算（规则引擎，非 LLM）")
    add("")
    if calc:
        add("| 组件 | 公式 | 代入 | 金额 | 是否适用 |")
        add("|---|---|---|---|---|")
        for c in calc.get("components", []):
            applied = "✅" if c.get("applied") else "—"
            note = "" if c.get("applied") else "<br>跳过：" + "；".join(c.get("skip_reasons") or [])
            add(f"| {c['type']} | `{c.get('formula', '')}` | `{c.get('substituted', '')}` "
                f"| {_money(c.get('amount'), currency)} | {applied}{note} |")
        add("")
        add(f"**复算合计：{_money(calc.get('total_commission'), currency)}**　"
            f"舍入：{calc.get('rounding_rule')}　"
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
            add(f"| {d['component']} | {_money(d['expected'], currency)} "
                f"| {_money(d['posted'], currency)} | {_money(d['delta'], currency)} "
                f"| {d.get('root_cause') or '一致'} |")
        add("")
        add(f"**总差额：{_money(rca.get('total_delta'), currency)}**　"
            f"根因分类：**{', '.join(rca.get('root_causes') or ['无']) or '无'}**")
        for d in rca.get("diffs", []):
            if d.get("explanation"):
                add(f"- {d['explanation']}")
    else:
        add("（未执行根因分析）")
    add("")

    # ---------------------------------------------------------- 风险与审批
    add("## 5. 风险分级与审批")
    add("")
    if risk:
        add(f"- 风险等级：**{risk.get('risk_level')}**（{'; '.join(risk.get('reason_codes') or [])}）")
        add(f"- 需要审批：{'是' if risk.get('approval_required') else '否'}"
            + (f"，审批角色：{risk.get('approver_role')}" if risk.get("approver_role") else ""))
    if approval:
        add(f"- 审批单：`{approval.get('approval_id')}`　状态：**{approval.get('status')}**")
        if approval.get("approver"):
            add(f"- 审批人：{approval.get('approver')}（{approval.get('decided_at', '-')}）"
                f"　意见：{approval.get('comment', '-')}")
    add("")

    # ---------------------------------------------------------- 执行
    add("## 6. 受控执行")
    add("")
    if executions:
        for ex in executions:
            add(f"### 动作 `{ex['action_id']}`（{ex['component']}，{_money(ex['amount'], ex.get('currency', currency))}）")
            add(f"- 幂等键：`{ex.get('idempotency_key')}`　状态：{ex.get('status')}"
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
    add("## 7. 独立验证（Verifier 重新查询，非 Executor 自证）")
    add("")
    if verification:
        add(f"- 验证结论：**{verification.get('verification_status')}**")
        add(f"- 应有合计：{_money(verification.get('expected_amount'), currency)}　"
            f"实际合计：{_money(verification.get('actual_amount'), currency)}　"
            f"偏差：{_money(verification.get('variance'), currency)}")
        for chk in verification.get("component_checks") or []:
            mark = "✅" if chk.get("passed") else "❌"
            add(f"  - {mark} {chk['component']}：应有 {chk['expected']}，实际 {chk['actual']}")
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
        add(f"- 回滚后独立验证：**{rb_verify.get('verification_status', '-')}**")
        add("")

    # ---------------------------------------------------------- Trace 与审计
    add("## 9. Trace 与审计摘要" if rollback else "## 8. Trace 与审计摘要")
    add("")
    add(f"- Trace span 数：{trace_summary.get('span_count')}　"
        f"总耗时：{trace_summary.get('total_duration_ms')}ms　"
        f"错误 span：{len(trace_summary.get('error_spans') or [])}")
    add("")
    add("### 关键审计事件")
    add("")
    add("| 时间 | 操作者 | 事件 |")
    add("|---|---|---|")
    for ev in audit_events:
        add(f"| {ev['created_at']} | {ev['actor']} | {ev['event']} |")
    add("")
    add("---")
    add(f"*报告由 RevGuard 自动生成；完整 Trace 见 data/outputs/traces/{case['case_id']}.json*")
    return "\n".join(lines)
