import { useCallback, useEffect, useMemo, useState } from "react";
import { externalValidationLabel, isVerifiedClosure, safetyRailState, securityRegressionSummary } from "./pipeline-state.js";
import {
  ArrowClockwise,
  ArrowCounterClockwise,
  ArrowRight,
  Calculator,
  CheckCircle,
  ClipboardText,
  Clock,
  Database,
  DownloadSimple,
  Fingerprint,
  FolderOpen,
  Gauge,
  GitBranch,
  Info,
  LockKey,
  Play,
  ShieldCheck,
  ShieldWarning,
  SpinnerGap,
  UserCheck,
  UsersThree,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";

const DEFAULT_CASE_ID = "CASE-2026-0008";
const API_KEYS = {
  viewer: "rg-demo-viewer-key-1",
  operator: "rg-demo-operator-key",
};

const STAGE_META = [
  { id: "evidence", label: "证据包", icon: FolderOpen },
  { id: "policy", label: "政策", icon: ShieldCheck },
  { id: "calculation", label: "计算预期佣金", icon: Calculator },
  { id: "approval", label: "人工审批边界", icon: UserCheck },
  { id: "execution", label: "执行（组件）", icon: Database },
  { id: "verification", label: "独立验证（不同主体）", icon: Fingerprint },
  { id: "rollback", label: "自动回滚", icon: ArrowCounterClockwise },
  { id: "postcheck", label: "回滚后状态", icon: ShieldCheck },
];

const STATUS_ORDER = [
  "CREATED", "NORMALIZING", "EVIDENCE_COLLECTING", "POLICY_MATCHING",
  "CALCULATING", "ROOT_CAUSE_ANALYZING", "RISK_REVIEW", "WAITING_FOR_APPROVAL",
  "READY_TO_EXECUTE", "EXECUTING", "VERIFYING", "ROLLBACK_REQUIRED", "ROLLED_BACK",
];

const AGENT_ROWS = [
  ["受理智能体", "revguard-intake", "识别订单与主体", "任务受理"],
  ["取证智能体", "revguard-evidence", "只读访问证据", "证据采集与验证"],
  ["政策智能体", "revguard-policy", "只读访问政策", "政策回溯与选择"],
  ["计算智能体", "revguard-calculation", "只读访问数据", "应有金额计算"],
  ["风险智能体", "revguard-risk", "审批路由", "风险判断与限额"],
  ["执行智能体", "revguard-executor", "受边界限制写入", "模拟记账（入账）"],
  ["验证智能体", "revguard-verifier", "独立只读验证", "独立验证与复核"],
  ["回滚智能体", "revguard-executor", "受控冲销", "自动回滚执行"],
];

const SKILL_LABELS = {
  OrchestratorHandshake: "协同任务编排",
  CaseNormalizeSkill: "整理案件信息",
  EntityResolveSkill: "匹配代理商与订单",
  EvidenceCollectSkill: "收集跨系统证据",
  PolicyVersionMatchSkill: "匹配业务时点政策",
  CommissionCalculateSkill: "重新计算应付佣金",
  DifferenceExplainSkill: "分析佣金差异原因",
  RiskClassifySkill: "判断案件风险等级",
  ApprovalRouteSkill: "确定审批流程",
  PermissionCheckSkill: "检查执行权限",
  IdempotencyGuardSkill: "防止重复执行",
  AdjustmentDraftSkill: "生成佣金调整草稿",
  LedgerAdjustSkill: "更新佣金台账",
  LedgerReverseSkill: "冲销佣金调整",
  PostActionVerifySkill: "独立核验调整结果",
  PostRollbackVerifySkill: "复核回滚结果",
  CaseToDatasetSkill: "归档案件经验",
};

const COMPONENT_LABELS = {
  SALES_COMMISSION: "销售佣金",
  COLLECTION_COMMISSION: "回款佣金",
  MONTHLY_INCENTIVE: "月度激励",
};

function sourceSystemLabel(sourceSystem) {
  return String(sourceSystem || "待识别").replace(/_MOCK$/, "");
}

const SKILL_STAGE = {
  CaseNormalizeSkill: "evidence",
  EntityResolveSkill: "evidence",
  EvidenceCollectSkill: "evidence",
  PolicyVersionMatchSkill: "policy",
  CommissionCalculateSkill: "calculation",
  DifferenceExplainSkill: "calculation",
  RiskClassifySkill: "approval",
  ApprovalRouteSkill: "approval",
  PermissionCheckSkill: "execution",
  IdempotencyGuardSkill: "execution",
  AdjustmentDraftSkill: "execution",
  LedgerAdjustSkill: "execution",
  PostActionVerifySkill: "verification",
  LedgerReverseSkill: "rollback",
  PostRollbackVerifySkill: "postcheck",
  CaseToDatasetSkill: "postcheck",
};

const TASK_STATUS_LABELS = {
  PENDING: "待处理",
  RUNNING: "执行中",
  SUCCEEDED: "已成功",
  FAILED_RETRYABLE: "失败待重试",
  FAILED_FINAL: "最终失败",
  CANCELLED: "已取消",
  ACKNOWLEDGED: "已确认",
};

const RUN_STATUS_LABELS = {
  QUEUED: "已排队",
  STARTING: "启动中",
  RUNNING: "运行中",
  WAITING_HUMAN: "等待人工审批",
  COMPLETED: "已完成",
  FAILED: "运行失败",
};

const ACTIVE_RUN_STATUSES = new Set(["QUEUED", "STARTING", "RUNNING"]);
const STALE_RUN_AFTER_MS = 10 * 60 * 1000;

function isStaleTeamRun(run = {}) {
  if (!ACTIVE_RUN_STATUSES.has(run.status)) return false;
  const timestamp = run.updated_at || run.started_at || run.queued_at;
  const updatedAt = timestamp ? new Date(timestamp).valueOf() : Number.NaN;
  return Number.isFinite(updatedAt) && Date.now() - updatedAt >= STALE_RUN_AFTER_MS;
}

function skillLabel(skillName) {
  if (!skillName) return "等待下一状态";
  return SKILL_LABELS[skillName] || skillName.replace(/Skill$/, "");
}

function componentLabel(component) {
  if (!component) return "未指定组件";
  return COMPONENT_LABELS[component] || component;
}

function money(value, currency = "KES") {
  if (value === undefined || value === null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return `${value} ${currency}`;
  return `${number.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
}

function approvalAmount(snapshot) {
  const approval = snapshot?.approval || {};
  const c = snapshot?.case || {};
  const rootCause = c.root_cause_report || {};
  const expected = Number(c.claim?.expected_amount);
  const actual = Number(c.claim?.actual_amount);
  if (approval.amount !== undefined && approval.amount !== null) return approval.amount;
  if (rootCause.total_delta !== undefined && rootCause.total_delta !== null) return rootCause.total_delta;
  if (Number.isFinite(expected) && Number.isFinite(actual)) return expected - actual;
  return null;
}

function signedMoney(value, currency = "KES") {
  return `${Number(value) > 0 ? "+" : ""}${money(value, currency)}`;
}

function shortId(value, length = 18) {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(date);
}

function percent(value, digits = 1) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${(number * 100).toFixed(digits)}%`;
}

function cny(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency", currency: "CNY", maximumFractionDigits: 0,
  }).format(number);
}

async function api(path, key, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.message || detail?.code || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return response.json();
}

function hasEvent(snapshot, name) {
  return snapshot?.audit_events?.some((item) => item.event === name);
}

function getStageState(snapshot, stageId) {
  const status = snapshot?.case?.status || "CREATED";
  const run = snapshot?.case?.team_run || {};
  const rank = STATUS_ORDER.indexOf(status);
  const terminal = ["ROLLED_BACK", "CLOSED", "FAILED"].includes(status);
  const draftOnly = snapshot?.verification?.verification_status === "NOT_APPLICABLE_DRAFT_ONLY";
  if (isVerifiedClosure(snapshot) && ["rollback", "postcheck"].includes(stageId)) return "skipped";
  if (run.status === "FAILED" && SKILL_STAGE[run.current_stage] === stageId) return "error";
  const checks = {
    evidence: hasEvent(snapshot, "EVIDENCE_COLLECTED"),
    policy: hasEvent(snapshot, "POLICY_MATCHED"),
    calculation: hasEvent(snapshot, "CALCULATED"),
    approval: hasEvent(snapshot, "APPROVAL_DECIDED") || snapshot?.case?.risk_decision?.approval_required === false,
    execution: hasEvent(snapshot, "EXECUTED") || hasEvent(snapshot, "DRAFT_CREATED"),
    verification: hasEvent(snapshot, "VERIFIED") || draftOnly,
    rollback: hasEvent(snapshot, "ROLLED_BACK") || (draftOnly && status === "CLOSED"),
    postcheck: hasEvent(snapshot, "ROLLBACK_VERIFIED") || (draftOnly && status === "CLOSED"),
  };
  if (checks[stageId]) {
    if (stageId === "verification" && snapshot?.verification?.verification_status === "FAILED") return "error";
    if (stageId === "rollback") return "rollback";
    return "done";
  }
  if (stageId === "approval" && status === "WAITING_FOR_APPROVAL") return "active";
  if (stageId === "evidence" && rank >= 1 && !terminal) return "active";
  return "pending";
}

function stageValue(snapshot, id) {
  const caseData = snapshot?.case || {};
  const approval = snapshot?.approval || {};
  const executions = snapshot?.executions || [];
  const verification = snapshot?.verification || {};
  const reversals = executions.filter((item) => item.reversal);
  const drafts = executions.filter((item) => item.action_type === "DRAFT" || item.status === "DRAFT");
  const postings = executions.filter((item) => item.action_type !== "DRAFT" && item.status !== "DRAFT");
  const draftOnly = verification.verification_status === "NOT_APPLICABLE_DRAFT_ONLY";
  const closedWithoutWrite = caseData.status === "CLOSED" && postings.length === 0;
  const verifiedClosure = isVerifiedClosure(snapshot);
  const values = {
    evidence: snapshot?.evidence?.length ? `${snapshot.evidence.length} 条` : "待收集",
    policy: caseData.policy_decision?.policy_version || "待匹配",
    calculation: money(caseData.calculation_result?.total_commission),
    approval: approval.amount !== undefined && approval.amount !== null ? money(approval.amount, approval.currency) : caseData.risk_decision?.approval_required === false ? "无需人工审批" : "等待风险判断",
    execution: postings.length
      ? postings.map((item) => `${componentLabel(item.component)} ${signedMoney(item.amount)}`).join("  ")
      : drafts.length
        ? `已生成 ${drafts.length} 份草稿`
      : "待授权",
    verification: draftOnly ? "草稿模式无需验证" : verification.actual_amount !== undefined && verification.actual_amount !== null ? `读取 ${money(verification.actual_amount)}` : closedWithoutWrite ? "无需写后验证" : "不同主体复核",
    rollback: reversals.length ? reversals.map((item) => money(item.reversal?.amount)).join("  ") : verifiedClosure ? "无需回滚（验证通过）" : draftOnly ? "无需回滚（未入账）" : closedWithoutWrite ? "未触发（无写入）" : "验证失败时触发",
    postcheck: caseData.status === "ROLLED_BACK" ? "已通过" : verifiedClosure ? "不适用（正常闭环）" : draftOnly && caseData.status === "CLOSED" ? "安全关闭（未入账）" : closedWithoutWrite ? "已关闭（无写入）" : "等待回滚复核",
  };
  return values[id];
}

function Header({ snapshot, cases, caseId, busy, onReset, onCaseChange }) {
  const status = snapshot?.case?.status || "CONNECTING";
  const mcpTeam = snapshot?.case?.execution_mode === "MCP_TEAM";
  const matrixTeam = snapshot?.case?.execution_mode === "AGENTTEAMS_MATRIX";
  const risk = snapshot?.case?.risk_level || "—";
  return (
    <header className="topbar">
      <div className="brand-group">
        <ShieldCheck className="brand-mark" weight="duotone" aria-hidden="true" />
        <span className="brand-name">RevGuard</span><span className="top-divider" />
        <select className="case-select" value={caseId} onChange={(event) => onCaseChange(event.target.value)} disabled={busy} aria-label="选择演示案件">
          {(cases.length ? cases : [{ case_id: caseId }]).map((item) => <option value={item.case_id} key={item.case_id}>{item.case_id} · {item.status || "CREATED"}</option>)}
        </select><span className="risk-pill">{risk}</span>{mcpTeam && <span className="mcp-pill">本地 MCP</span>}{matrixTeam && <span className="mcp-pill matrix-pill"><span />AgentTeams 已连接</span>}
        <span className="approval-label">人工审批</span>
      </div>
      <div className="disclosure">合成业务数据 · 真实运行链路</div>
      <div className="top-actions">
        <span className="health-pill"><span className="health-dot" />安全优先模式：已激活</span>
        <button className="icon-button" onClick={onReset} disabled={busy} title="重新准备演示案件">
          <ArrowClockwise className={busy ? "spin" : ""} weight="bold" /><span>重新准备</span>
        </button>
        <span className={`status-mini status-${status.toLowerCase()}`}>{status}</span>
      </div>
    </header>
  );
}

function SummaryStrip({ snapshot }) {
  const c = snapshot?.case || {};
  const rca = c.root_cause_report || {};
  const approval = snapshot?.approval || {};
  const currency = c.claim?.currency || "KES";
  const expected = rca.total_expected ?? c.calculation_result?.total_commission ?? c.claim?.expected_amount;
  const items = [
    ["代理商", c.partner_name || c.partner_id || "待解析", c.partner_id || "按名称解析"],
    ["订单号", c.order_id || "待解析", c.calculation_result?.facts_snapshot?.order_date ? `订单日期 ${c.calculation_result.facts_snapshot.order_date}` : "等待证据定位"],
    ["订单金额", money(c.calculation_result?.facts_snapshot?.order_amount, currency), "合成业务订单"],
    ["已入账金额", money(rca.total_posted ?? c.claim?.actual_amount, currency), "模拟佣金台账"],
    ["预期佣金（正确）", money(expected, currency), "确定性规则内核"],
    ["本次审批金额", money(approvalAmount(snapshot), currency), approval.status || "PENDING"],
    ["最终状态", c.status || "CREATED", "案件终态保留"],
    ["回滚后状态", c.status === "ROLLED_BACK" ? "已通过" : isVerifiedClosure(snapshot) ? "不适用" : "—", c.status === "ROLLED_BACK" ? "恢复安全基线" : isVerifiedClosure(snapshot) ? "验证通过，无需回滚" : "等待验证"],
  ];
  return (
    <section className="summary-strip" aria-label="案件摘要">
      {items.map(([label, value, sub], index) => (
        <div className={`summary-item summary-${index}`} key={label}>
          <span className="summary-label">{label}</span><strong>{value}</strong><small>{sub}</small>
        </div>
      ))}
    </section>
  );
}

function PrimaryAction({ snapshot, busy, onRun, onApprove, onInspect }) {
  const status = snapshot?.case?.status || "CREATED";
  const run = snapshot?.case?.team_run || {};
  const running = ["QUEUED", "STARTING", "RUNNING"].includes(run.status);
  const currency = snapshot?.case?.claim?.currency || "KES";
  if (status === "CREATED") {
    return <button className="primary-action" onClick={onRun} disabled={busy || running}>{busy || running ? <SpinnerGap className="spin" weight="bold" /> : <Play weight="fill" />}{running ? `AgentTeams 调度中 ${run.completed_tasks || 0}/${run.total_tasks || 8}` : busy ? "正在启动真实调查链路…" : "启动多智能体调查"}</button>;
  }
  if (status === "WAITING_FOR_APPROVAL") {
    return <button className="primary-action" onClick={onApprove} disabled={busy}>{busy ? <SpinnerGap className="spin" weight="bold" /> : <UserCheck weight="bold" />}{busy ? "正在执行并独立验证…" : `批准 ${money(approvalAmount(snapshot), currency)}`}</button>;
  }
  const rolledBack = status === "ROLLED_BACK";
  return <button className="primary-action evidence-action" onClick={onInspect}><ClipboardText weight="bold" />{rolledBack ? "查看回滚证据" : "查看审计证据"}</button>;
}

function Pipeline({ snapshot, busy, onRun, onApprove, onInspect }) {
  const c = snapshot?.case || {};
  const run = c.team_run || {};
  const approval = snapshot?.approval || {};
  const verification = snapshot?.verification || {};
  const executions = snapshot?.executions || [];
  const drafts = executions.filter((item) => item.action_type === "DRAFT" || item.status === "DRAFT");
  const postings = executions.filter((item) => item.action_type !== "DRAFT" && item.status !== "DRAFT");
  const reversals = executions.filter((item) => item.reversal);
  const reversalAmounts = reversals.map((item) => item.reversal.amount);
  const calculated = Boolean(snapshot?.case?.calculation_result);
  const currency = c.calculation_result?.currency || c.claim?.currency || "KES";
  const selectedPolicy = c.policy_decision?.policy_version;
  const excludedPolicies = (c.policy_decision?.excluded_versions || []).map((item) => item.version);
  const draftOnly = verification.verification_status === "NOT_APPLICABLE_DRAFT_ONLY";
  const closedWithoutWrite = c.status === "CLOSED" && postings.length === 0;
  const verifiedClosure = isVerifiedClosure(snapshot);
  const terminal = ["ROLLED_BACK", "CLOSED", "REJECTED", "FAILED"].includes(c.status);
  const postedTotal = postings.reduce((total, item) => total + Number(item.amount || 0), 0);
  const reversedTotal = reversalAmounts.reduce((total, amount) => total + Number(amount || 0), 0);
  return (
    <section className="pipeline-panel" aria-label="治理流水线">
      <div className="pipeline-stages">
        {STAGE_META.map((stage, index) => {
          const Icon = stage.icon;
          const state = getStageState(snapshot, stage.id);
          return (
            <div className={`stage stage-${state}`} key={stage.id}>
              <div className="stage-heading"><span className="stage-index">{index + 1}</span>{stage.label}</div>
              <strong className="stage-value">{stageValue(snapshot, stage.id)}</strong>
              <div className="stage-line"><span className="stage-node"><Icon weight="duotone" /></span>{index < STAGE_META.length - 1 && <ArrowRight className="stage-arrow" weight="bold" />}</div>
            </div>
          );
        })}
      </div>
      <div className="pipeline-details">
        <div className="pipeline-note calculation-note"><span>政策选择</span>{calculated ? <><strong>{excludedPolicies.length ? `${excludedPolicies.join(" / ")}：已排除` : "无冲突版本"}</strong><strong>{selectedPolicy || "规则集已选定"}：已采用</strong></> : <strong>等待政策匹配与确定性复算</strong>}</div>
        <div className="pipeline-note capability-note"><span>能力边界</span><div>总额度上限：{money(approval.amount, currency)}</div><div>本次金额：{money(approvalAmount(snapshot), currency)}</div><PrimaryAction snapshot={snapshot} busy={busy} onRun={onRun} onApprove={onApprove} onInspect={onInspect} /><small>{approval.amount !== undefined && approval.amount !== null ? "授权有效期：15 分钟" : draftOnly ? "低风险案件仅生成草稿" : closedWithoutWrite ? "风险策略禁止自动写入" : "审批后签发短时能力"}</small></div>
        <div className="pipeline-note execution-note"><span>{draftOnly ? "佣金调整草稿" : "模拟记账（入账）"}</span>{postings.length ? <>{postings.map((item) => <strong key={item.action_id || item.component}>{componentLabel(item.component)}：{signedMoney(item.amount, currency)}</strong>)}<div>合计：{signedMoney(postedTotal, currency)}</div></> : drafts.length ? <>{drafts.map((item) => <strong key={item.action_id || item.component}>{componentLabel(item.component)}：{signedMoney(item.amount, currency)}</strong>)}<div>共 {drafts.length} 份，未写入台账</div></> : <strong>{closedWithoutWrite ? "风险边界拦截，未发生写入" : "等待受限执行器写入"}</strong>}</div>
        <div className={`pipeline-note verify-note ${verification.verification_status === "FAILED" ? "is-failed" : ""}`}><span>验证结果</span>{draftOnly ? <><div>草稿未写入财务台账</div><div>实际写入：0.00 {currency}</div><strong>无需执行写后验证</strong></> : <><div>实际读取：{money(verification.actual_amount, currency)}</div><div>差异：{money(verification.variance, currency)}</div><strong>{verification.verification_status === "FAILED" ? "不匹配" : verification.verification_status === "PASSED" ? "验证通过" : closedWithoutWrite ? "无需写后验证" : "等待独立验证"}</strong></>}</div>
        <div className="pipeline-note rollback-note"><span>自动回滚执行</span>{reversals.length ? <>{reversals.map((item) => <strong key={item.action_id}>{componentLabel(item.component)}：{signedMoney(item.reversal.amount, currency)}</strong>)}<div>合计：{signedMoney(reversedTotal, currency)}</div></> : <strong>{verifiedClosure ? "独立验证通过，无需回滚" : draftOnly ? "无需回滚（草稿未入账）" : closedWithoutWrite ? "未触发（没有财务写入）" : "验证失败时由策略自动触发"}</strong>}</div>
        <div className={`pipeline-note result-note ${run.status === "FAILED" ? "is-failed" : ""}`}><span>最终结果</span><strong>{terminal ? c.status : "等待终态"}</strong><b>{run.status === "FAILED" ? "运行失败" : c.status === "ROLLED_BACK" ? "已通过" : verifiedClosure ? "正常闭环" : closedWithoutWrite ? "安全关闭" : "—"}</b><small>{run.status === "FAILED" ? `${skillLabel(run.current_stage)}：${run.error?.message || "未返回具体错误"}` : c.status === "ROLLED_BACK" ? "已恢复至安全基线" : verifiedClosure ? "独立验证通过，调整已完成" : draftOnly ? "仅形成调整草稿，未触碰台账" : closedWithoutWrite ? "风险策略阻止财务写入" : "尚未生成终态结论"}</small></div>
      </div>
    </section>
  );
}

function EvidenceTable({ snapshot }) {
  const evidence = snapshot?.evidence || [];
  const c = snapshot?.case || {};
  const orderRef = c.order_id || "等待订单解析";
  const partnerRef = c.partner_id || c.partner_name || "等待主体解析";
  const fallback = [
    ["ORDER", "CRM", orderRef], ["TIER_HISTORY", "CRM", partnerRef],
    ["CONTRACT", "CONTRACT", partnerRef], ["PAYMENT_RECORD", "FINANCE", orderRef],
    ["REFUND_RECORD", "FINANCE", orderRef], ["INVOICE", "FINANCE", orderRef],
    ["COMMISSION_LEDGER", "FINANCE", orderRef], ["POLICY_VERSIONS", "CONTRACT", c.case_type || "待匹配"],
  ].map(([type, source_system, source_ref]) => ({ type, source_system, source_ref, strength: "PENDING" }));
  const rows = evidence.length ? evidence : fallback;
  return (
    <section className="detail-section evidence-section">
      <div className="section-title"><FolderOpen weight="duotone" /><strong>证据来源链</strong><span>（{evidence.length} 条已采集强证据）</span></div>
      <div className="table-wrap"><table><thead><tr><th>证据类型</th><th>来源系统</th><th>证据 ID</th><th>强度</th><th>工具回执</th><th>校验</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.evidence_id || item.type}><td>{item.type}</td><td>{sourceSystemLabel(item.source_system)}</td><td>{item.source_ref}</td><td><span className={evidence.length ? "strong-cell" : "pending-cell"}>{evidence.length ? <ShieldCheck weight="fill" /> : <Clock weight="fill" />}{item.strength || "STRONG"}</span></td><td title={item.tool_receipt}>{shortId(item.tool_receipt || "等待运行", 13)}</td><td><span className={evidence.length ? "verified-cell" : "pending-cell"}>{evidence.length ? <CheckCircle weight="fill" /> : <Clock weight="fill" />}{evidence.length ? "已校验" : "待收集"}</span></td></tr>)}
      </tbody></table></div>
    </section>
  );
}

function CalculationLedger({ snapshot }) {
  const c = snapshot?.case || {};
  const rca = c.root_cause_report || {};
  const diffs = rca.diffs || [];
  const currency = c.calculation_result?.currency || c.claim?.currency || "KES";
  const components = c.calculation_result?.components || [];
  return (
    <section className="detail-section ledger-section">
      <div className="section-title"><Calculator weight="duotone" /><strong>计算账本</strong><span>（金额单位：{currency}）</span></div>
      <div className="table-wrap"><table><thead><tr><th>项目</th><th>公式</th><th>基数</th><th>比例</th><th>计算</th><th>应有金额</th><th>已发布</th><th>差异</th></tr></thead><tbody>
        {diffs.filter((item) => Number(item.delta) !== 0).map((item) => {
          const component = components.find((candidate) => candidate.type === item.component && candidate.applied) || {};
          const [base = "—", ratio = "—"] = String(component.substituted || "").split(" * ");
          return <tr key={item.component}><td title={item.component}>{componentLabel(item.component)}</td><td>{component.formula || "规则引擎"}</td><td>{base}</td><td>{ratio}</td><td>{component.substituted || "确定性复算"}</td><td>{Number(item.expected).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td><td>{Number(item.posted).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td><td className="negative-cell">{Number(item.delta).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td></tr>;
        })}{!diffs.length && <tr><td colSpan="8" className="table-empty">等待调查完成后生成逐组件确定性复算账本</td></tr>}
      </tbody></table></div>
      <div className="ledger-total"><span>应有 {money(rca.total_expected, currency)}</span><span>已记 {money(rca.total_posted, currency)}</span><strong>差额 {money(rca.total_delta, currency)}</strong></div>
    </section>
  );
}

function PolicyTimeline({ snapshot }) {
  const decision = snapshot?.case?.policy_decision || {};
  const selectedVersion = decision.policy_version;
  const excludedVersions = (decision.excluded_versions || []).map((item) => item.version);
  const selectedYear = selectedVersion?.match(/^(\d{4})-Q[1-4]$/)?.[1];
  const versions = selectedYear ? [1, 2, 3, 4].map((quarter) => `${selectedYear}-Q${quarter}`) : [...new Set([...excludedVersions, selectedVersion].filter(Boolean))];
  const tier = snapshot?.case?.calculation_result?.facts_snapshot?.agent_tier;
  const clauses = decision.cited_clauses || [];
  return (
    <section className="detail-section policy-section">
      <div className="section-title"><GitBranch weight="duotone" /><strong>政策时间线</strong><span>{selectedVersion ? `（排除 ${excludedVersions.join("、") || "无"}，选择 ${selectedVersion}）` : "（等待时点政策匹配）"}</span></div>
      <div className="policy-line">{(versions.length ? versions : ["待判定"]).map((version) => <div className={`policy-point ${version === selectedVersion ? "selected" : ""}`} key={version}><strong>{version}</strong><span /><small>{version === selectedVersion ? `已选中（业务日期 ${decision.decision_date || "—"}）` : selectedVersion ? "未选中" : "待判定"}</small></div>)}</div>
      <div className={`policy-selected ${selectedVersion ? "" : "policy-pending"}`}>{selectedVersion ? <CheckCircle weight="fill" /> : <Clock weight="fill" />}<div>{selectedVersion ? <><strong>{decision.policy_id}　|　{decision.policy_version}　|　{tier || "时点等级"}</strong><p>{clauses.slice(0, 2).map((item) => item.text).join("；") || "已按业务时点选择有效规则集。"}</p><small>引用条款：{clauses.map((item) => item.clause_id).join("、") || "规则集版本已绑定"}</small></> : <><strong>等待政策智能体读取订单时点并回溯政策版本</strong><p>运行后展示被排除版本、最终适用版本与条款级引用。</p></>}</div></div>
    </section>
  );
}

function AgentMatrix({ snapshot }) {
  const tasks = snapshot?.agent_tasks || [];
  const visible = tasks.slice().reverse();
  const workerCount = new Set(tasks.map((item) => item.assigned_actor)).size;
  const succeeded = tasks.filter((item) => item.status === "SUCCEEDED").length;
  const run = snapshot?.case?.team_run || {};
  const orchestrator = run.orchestrator;
  const isMatrix = snapshot?.case?.execution_mode === "AGENTTEAMS_MATRIX";
  const traceSpans = snapshot?.trace?.spans || [];
  const runStatus = run.status || "QUEUED";
  return (
    <section className="detail-section agent-section" id="agent-task-ledger">
      <div className="section-title"><UsersThree weight="duotone" /><strong>多智能体协同任务账本</strong><span>{tasks.length ? `${isMatrix ? "AgentTeams Matrix" : "本地 MCP"} · ${succeeded}/${tasks.length} 轮任务 · ${workerCount} 个执行者` : "责任与能力边界"}</span></div>
      {isMatrix && <div className={`team-runtime team-runtime-${runStatus.toLowerCase()}`}><div><span className="runtime-live-dot" /><strong title={runStatus}>{RUN_STATUS_LABELS[runStatus] || runStatus}</strong><small>{run.phase === "EXECUTION" ? "审批后受控执行" : "审批前调查"}</small></div><div><span>当前执行者</span><strong>{run.current_actor || "revguard-orchestrator"}</strong></div><div><span>当前阶段</span><strong title={run.current_stage || ""}>{skillLabel(run.current_stage)}</strong></div><div><span>进度</span><strong>{run.completed_tasks || 0} / {run.total_tasks || 8}</strong></div></div>}
      <div className="agent-task-ledger">
        {orchestrator && <details className="agent-task-card orchestrator-card" open>
          <summary><span className="task-seq">编排</span><div><strong title="OrchestratorHandshake">协同任务编排</strong><code>revguard-orchestrator</code></div><span className="transport-cell">Matrix</span><span className={`task-status task-${orchestrator.status?.toLowerCase()}`}>{TASK_STATUS_LABELS[orchestrator.status] || orchestrator.status}</span></summary>
          <div className="task-evidence-grid"><div><span>控制输入</span><pre>{JSON.stringify(orchestrator.input || {}, null, 2)}</pre></div><div><span>控制输出</span><pre>{JSON.stringify(orchestrator.output || { status: "WAITING" }, null, 2)}</pre></div></div>
          <div className="correlation-strip"><code>dispatch {shortId(orchestrator.dispatch_event_id, 30)}</code><code>trigger {shortId(orchestrator.trigger_event_id, 30)}</code><code>response {shortId(orchestrator.response_event_id, 30)}</code></div>
        </details>}
        {visible.length ? visible.map((task, reverseIndex) => {
          const span = traceSpans.find((item) => item.inputs?.correlation?.agent_task_id === task.task_id || item.outputs?.agent_task_id === task.task_id || item.inputs?.task_id === task.task_id);
          const isRunFailureTask = run.status === "FAILED" && task.task_id === run.current_task_id;
          const displayStatus = isRunFailureTask ? "FAILED_FINAL" : task.status;
          const displayOutput = task.result || task.error || (isRunFailureTask ? {
            status: "未完成",
            reason: run.error?.message || "AgentTeams 执行者未返回阶段结果",
          } : { status: task.status });
          return <details className={`agent-task-card ${isRunFailureTask ? "failed-task-card" : ""}`} key={task.task_id} open={isRunFailureTask || reverseIndex === 0}>
            <summary><span className="task-seq">{String(tasks.length - reverseIndex).padStart(2, "0")}</span><div><strong title={task.skill_name}>{skillLabel(task.skill_name)}</strong><code>{task.assigned_actor}</code></div><span className="transport-cell">{task.skill_transport === "higress-mcp" ? "MCP 网关" : task.transport === "agentteams-matrix" ? "Matrix" : task.transport === "mcp" ? "本地 MCP" : (task.transport || "—")}</span><span className={`task-status task-${displayStatus.toLowerCase()}`}>{isRunFailureTask ? "未完成" : TASK_STATUS_LABELS[task.status] || task.status} · 第 {task.attempt} 次</span></summary>
            {isRunFailureTask && <div className="task-failure-reason"><WarningCircle weight="fill" /><div><strong>执行者未提交阶段结果</strong><small>{run.error?.message || "AgentTeams Worker 未在时限内完成任务"}</small></div></div>}
            <div className="task-evidence-grid"><div><span>任务输入 · 不可变</span><pre>{JSON.stringify(task.input || {}, null, 2)}</pre></div><div><span>任务输出 · 阶段结果</span><pre>{JSON.stringify(displayOutput, null, 2)}</pre></div></div>
            <div className="correlation-strip"><code>任务 {shortId(task.task_id, 28)}</code><code>请求 {shortId(task.request_id, 28)}</code><code>房间 {shortId(task.matrix_room_id, 28)}</code><code>消息 {shortId(task.agentteams_message_id, 28)}</code><code>回执 {shortId(task.skill_receipt, 28)}</code><code>追踪 {shortId(span?.span_id, 28)}</code>{task.skill_transport === "higress-mcp" && <code>技能入口 Higress MCP</code>}</div>
          </details>;
        }) : <div className="compact-table">{AGENT_ROWS.map(([role, actor, access, duty]) => <div className="compact-row" key={`${role}-${duty}`}><strong>{role}</strong><code>{actor}</code><span>{access}</span><span>{duty}</span></div>)}</div>}
      </div>
      <p className="boundary-note"><ShieldCheck weight="fill" />{tasks.length ? "输入、输出、任务、请求、Matrix 消息、Higress MCP 网关、回执与追踪标识逐项关联；只有持久化的阶段结果才能推进状态。" : "执行智能体与验证智能体相互独立；验证结果不能由执行者自证。"}</p>
    </section>
  );
}

const IMPORTANT_EVENTS = new Set(["CASE_CREATED", "EVIDENCE_COLLECTED", "POLICY_MATCHED", "CALCULATED", "RISK_CLASSIFIED", "APPROVAL_DECIDED", "EXECUTED", "VERIFIED", "ROLLED_BACK", "ROLLBACK_VERIFIED", "KNOWLEDGE_ARCHIVED", "TEAM_RUN_FAILED", "DEMO_RESET"]);

function AuditTrail({ snapshot, full = false }) {
  const events = (snapshot?.audit_events || []).filter((item) => full || IMPORTANT_EVENTS.has(item.event));
  const shown = full ? events : events.slice(-10);
  return (
    <section className="detail-section audit-section" id="rollback-evidence">
      <div className="section-title"><Fingerprint weight="duotone" /><strong>不可篡改审计详情</strong></div>
      <div className="audit-list">{shown.length ? shown.map((item) => {
        const detail = item.detail || {};
        const isError = item.event.endsWith("_FAILED")
          || detail.verification_status === "FAILED"
          || detail.status === "FAILED"
          || detail.to === "FAILED";
        const isRollback = item.event === "ROLLED_BACK";
        return <div className={`audit-row ${isError ? "audit-error" : ""} ${isRollback ? "audit-rollback" : ""}`} key={item.seq}><span className="audit-icon">{isError ? <XCircle weight="fill" /> : isRollback ? <ArrowCounterClockwise weight="bold" /> : <CheckCircle weight="fill" />}</span><time>{formatTime(item.created_at)}</time><div><strong>{item.event}</strong><small>{item.actor}</small></div><code>{shortId(item.detail?.request_id || item.detail?.task_id || item.detail?.action_id || item.detail?.reversal_id || "recorded", 20)}</code></div>;
      }) : <div className="empty-state"><Info weight="duotone" />启动案件后，真实审计事件会出现在这里。</div>}</div>
      <div className="audit-summary"><span>执行摘要：</span><strong>{snapshot?.trace?.span_count || 0} 条追踪</strong><span className="error-dot" />错误 {snapshot?.trace?.error_spans?.length || 0}<span>以实际审计记录为准</span></div>
    </section>
  );
}

function TraceView({ snapshot }) {
  const spans = snapshot?.trace?.spans || [];
  const shown = spans.filter((span) => ["TOOL", "SKILL", "APPROVAL", "AGENT"].includes(span.kind)).slice(-28);
  const duration = (milliseconds) => {
    const value = Number(milliseconds);
    if (!Number.isFinite(value)) return "—";
    if (value < 1) return "<1 ms";
    if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} s`;
    return `${value} ms`;
  };
  return (
    <div className="trace-grid"><section className="detail-section trace-section"><div className="section-title"><Gauge weight="duotone" /><strong>本次运行追踪耗时</strong><span>AGENT 类型记录 Matrix 与执行者的端到端耗时；SKILL / TOOL 类型记录 API 进程内耗时</span></div><div className="span-list">{shown.map((span) => <div className={`span-row span-${span.status?.toLowerCase()}`} key={span.span_id}><span className="span-kind">{span.kind}</span><strong>{span.name}</strong><code>{span.actor || "system"}</code><span>{duration(span.duration_ms)}</span><span>{span.status}</span></div>)}{!shown.length && <div className="empty-state"><Play weight="duotone" />启动调查后显示真实的智能体、技能和工具调用记录。</div>}</div></section><AuditTrail snapshot={snapshot} full /></div>
  );
}

function Permissions({ snapshot, evidence }) {
  const c = snapshot?.case || {};
  const approval = snapshot?.approval || {};
  const quotas = approval.component_quota || {};
  const currency = approval.currency || c.claim?.currency || "KES";
  const human = approval.human_identity || {};
  const evaluation = evidence?.deterministic_evaluation;
  const security = securityRegressionSummary(evaluation);
  const quotaRows = Object.entries(quotas).map(([component, amount]) => [componentLabel(component), money(amount, currency)]);
  const rows = [["案件绑定", c.case_id || "—"], ["币种", currency], ["总额度上限", money(approvalAmount(snapshot), currency)], ...quotaRows, ["能力令牌有效期", "15 分钟"], ["审批角色", approval.approver_role || c.risk_decision?.approver_role || "等待风险判断"], ["人类审批人", human.display_name || "等待 AgentTeams 身份验证"], ["Matrix 身份", human.sub || "尚未绑定"], ["身份验证方式", human.auth_method === "matrix-password" ? "AgentTeams Matrix 密码验证" : human.auth_method || "尚未验证"], ["动作证明指纹", approval.human_assertion_id_ref || "提交审批后生成"], ["能力指纹", approval.approval_token_ref || "批准后生成"]];
  return (
    <div className="permissions-grid">
      <section className="detail-section permission-card"><div className="section-title"><LockKey weight="duotone" /><strong>审批与能力边界</strong></div><div className="permission-list">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div></section>
      <section className="detail-section permission-card"><div className="section-title"><ShieldWarning weight="duotone" /><strong>四重约束</strong></div><div className="constraint-list"><div><span>01</span><strong>任务不漂移</strong><small>状态、技能、执行者、输入快照与案件版本绑定</small></div><div><span>02</span><strong>审批不自签</strong><small>后端独立验证 AgentTeams Matrix 人类身份；证明只绑定当前案件、审批单和动作</small></div><div><span>03</span><strong>额度不外溢</strong><small>总额度与逐组件额度同时约束，防止重复写入</small></div><div><span>04</span><strong>权限不升级</strong><small>服务端身份、角色、权限范围与技能执行者白名单共同约束</small></div></div></section>
      <section className="detail-section permission-card"><div className="section-title"><Fingerprint weight="duotone" /><strong>安全回归证据</strong></div><div className="probe-list"><div>{security.passed ? <CheckCircle weight="fill" /> : <Clock weight="fill" />}<span>已存档回归快照</span><strong>{security.label}</strong></div></div><p className="boundary-note">来源：确定性评测 · {evaluation?.generated_at || "尚无时间记录"}。不是本次案件现场执行的安全探针。</p><p className="boundary-note">覆盖范围：伪造/过期令牌、跨案件调用、权限升级、组件额度、并发双写与回滚重放。</p></section>
    </div>
  );
}

function EngineeringEvidence({ evidence }) {
  const runtime = evidence?.runtime || {};
  const evaluation = evidence?.deterministic_evaluation || {};
  const value = evidence?.business_value || {};
  const synthetic = evidence?.synthetic_dataset || {};
  const rehearsal = evidence?.mcp_rehearsal || {};
  const postgres = evidence?.local_postgresql || {};
  const metrics = value.metrics || {};
  const external = evidence?.external_validation || {};
  const engineVersion = runtime.database_engine_version || "";
  const rows = [
    ["存储后端", runtime.storage_backend || "等待 API", engineVersion.includes("PolarDB") ? engineVersion.split(" on ")[0] : runtime.read_replica_enabled ? "主/只读已分流" : "本地 Demo / 单端点"],
    ["审计链", runtime.audit_chain?.enforced ? (runtime.audit_chain.valid ? "VALID" : "BROKEN") : "DEMO ONLY", runtime.audit_chain?.enforced ? `${runtime.audit_chain.rows_checked || 0} 条已校验` : "PolarDB 模式由 DB trigger 强制"],
    ["Trace 错误", String(runtime.trace_error_spans_total ?? 0), `${runtime.trace_spans_total || 0} spans 已持久化`],
    ["StageResult", String(runtime.agent_task_attempts_total ?? 0), "Task 终态与 Result 同事务"],
    ["本地 stdio 排练", `${rehearsal.outcome?.succeeded_tasks || 0}/${rehearsal.outcome?.task_count || 0}`, `${rehearsal.outcome?.worker_count || 0} 个执行者 · 存档快照，非本次 AgentTeams 运行`],
    ["合成数据校验", synthetic.validation_status || "等待生成", `${synthetic.record_counts?.orders || 0} orders · ${synthetic.record_counts?.golden_cases || 0} cases`],
    ["本地 PostgreSQL", postgres.checks?.audit_chain_valid ? "PASSED" : "PENDING", postgres.classification ? "PG 18.6 兼容验证；非云验收" : "等待本地验证"],
    ["确定性评测", `${evaluation.passed || 0}/${evaluation.total_scenarios || 0}`, `通过率 ${Number(evaluation.pass_rate || 0) * 100}%`],
    ["发布版本", evidence?.release || "—", "灰度流程有配置模板；此处不是线上灰度记录"],
  ];
  const valueRows = [
    ["处理时长中位数", `${metrics.median_manual_processing_minutes ?? "—"} → ${metrics.median_revguard_processing_minutes ?? "—"} min`],
    ["错付率口径", `${Number(metrics.wrong_payment_rate_before || 0) * 100}% → ${Number(metrics.wrong_payment_rate_after || 0) * 100}%`],
    ["追回成本口径", `${metrics.recovery_cost_before || "—"} → ${metrics.recovery_cost_after || "—"}`],
    ["人工升级率", `${Number(metrics.manual_escalation_rate || 0) * 100}%`],
    ["审计异常率口径", `${Number(metrics.audit_exception_rate_before || 0) * 100}% → ${Number(metrics.audit_exception_rate_after || 0) * 100}%`],
  ];
  return (
    <div className="engineering-grid">
      <section className="detail-section engineering-section">
        <div className="section-title"><Gauge weight="duotone" /><strong>可查询工程证据</strong><span>实时存储指标与已标注的存档评测</span></div>
        <div className="evidence-ledger">{rows.map(([label, primary, note]) => <div className="evidence-row" key={label}><span>{label}</span><strong>{primary}</strong><small>{note}</small></div>)}</div>
      </section>
      <section className="detail-section engineering-section">
        <div className="section-title"><ClipboardText weight="duotone" /><strong>业务价值口径</strong><span>时长 / 错付 / 追回 / 升级 / 审计</span></div>
        <div className="classification-banner"><WarningCircle weight="fill" /><div><strong>合成数据·仅验证指标方法</strong><small>{value.guardrail || "不得声称为企真实收益"}</small></div></div>
        <div className="value-ledger">{valueRows.map(([label, result]) => <div key={label}><span>{label}</span><strong>{result}</strong></div>)}</div>
      </section>
      <section className="detail-section pending-section">
        <div className="section-title"><Database weight="duotone" /><strong>外部环境验收</strong><span>不伪造完成状态</span></div>
        <div className="pending-checks">{[["企业真实基线", external.production_business_baseline], ["AgentTeams 通信配置", external.agentteams_room], ["开源 PolarDB-PG", external.self_hosted_polardb_pg], ["PolarDB 云端兼容", external.polardb_cloud_acceptance], ["PolarDB PITR 演练", external.polardb_pitr_drill]].map(([label, state]) => <div key={label}><Clock weight="fill" /><span>{label}</span><strong title={state}>{externalValidationLabel(state)}</strong></div>)}</div>
      </section>
    </div>
  );
}

function BusinessValueSimulator({ evidence }) {
  const value = evidence?.business_value || {};
  const metrics = value.metrics || {};
  const contract = value.simulation_contract || {};
  const defaults = contract.default_assumptions || {};
  const [monthlyCases, setMonthlyCases] = useState(defaults.monthly_case_volume || 500);
  const [hourlyCost, setHourlyCost] = useState(defaults.loaded_hourly_labor_cost || 100);
  const manualMinutes = Number(metrics.median_manual_processing_minutes || 0);
  const assistedMinutes = Number(metrics.median_revguard_processing_minutes || 0);
  const savedPerCase = Number(metrics.median_minutes_saved_per_case || Math.max(manualMinutes - assistedMinutes, 0));
  const monthlyHours = savedPerCase * Math.max(monthlyCases, 0) / 60;
  const monthlyLaborValue = monthlyHours * Math.max(hourlyCost, 0);
  const annualLaborValue = monthlyLaborValue * Number(defaults.months_per_year || 12);
  const fteEquivalent = monthlyHours / Number(defaults.working_hours_per_fte_month || 160);
  const throughput = Number(metrics.throughput_capacity_multiplier || (assistedMinutes ? manualMinutes / assistedMinutes : 0));
  const timeReduction = Number(metrics.median_processing_time_reduction_rate || 0);
  const recoveryBefore = Number(metrics.recovery_cost_before || 0);
  const recoveryAfter = Number(metrics.recovery_cost_after || 0);
  const recoveryReduction = Number(metrics.recovery_cost_reduction_rate || 0);
  const comparisons = [
    ["单案处理时长", `${manualMinutes} 分钟`, `${assistedMinutes} 分钟`, assistedMinutes / Math.max(manualMinutes, 1)],
    ["错付样本率", percent(metrics.wrong_payment_rate_before), percent(metrics.wrong_payment_rate_after), Number(metrics.wrong_payment_rate_after || 0) / Math.max(Number(metrics.wrong_payment_rate_before || 0), 0.01)],
    ["追回成本指数", recoveryBefore.toLocaleString(), recoveryAfter.toLocaleString(), recoveryAfter / Math.max(recoveryBefore, 1)],
  ];
  return (
    <div className="value-simulator">
      <section className="detail-section value-hero">
        <div className="value-hero-copy">
          <span className="value-eyebrow">合成数据价值情景</span>
          <h2>企业价值模拟器</h2>
          <p>把 8 个合成案件的可复算基线，与企业自行输入的业务量和人工成本组合，回答“可能释放多少工时、形成多少预算空间”。</p>
        </div>
        <div className="scenario-controls">
          <label><span>月均异常案件量</span><div><input type="number" min="1" max="100000" step="50" value={monthlyCases} onChange={(event) => setMonthlyCases(Number(event.target.value) || 0)} /><b>案/月</b></div></label>
          <label><span>综合人工成本</span><div><input type="number" min="1" max="10000" step="10" value={hourlyCost} onChange={(event) => setHourlyCost(Number(event.target.value) || 0)} /><b>元/小时</b></div></label>
          <div className="scenario-presets"><span>快速情景</span>{[100, 500, 1000].map((count) => <button className={monthlyCases === count ? "active" : ""} onClick={() => setMonthlyCases(count)} key={count}>{count} 案</button>)}</div>
        </div>
      </section>

      <section className="value-kpi-grid" aria-label="模拟价值关键指标">
        <article><span>处理时长下降</span><strong>{percent(timeReduction)}</strong><small>{manualMinutes} → {assistedMinutes} 分钟/案</small></article>
        <article><span>同等工时理论吞吐</span><strong>{throughput ? `${throughput.toFixed(2)}×` : "—"}</strong><small>基于合成样本中位数</small></article>
        <article><span>每月释放处理工时</span><strong>{monthlyHours.toLocaleString("zh-CN", { maximumFractionDigits: 0 })} 小时</strong><small>约 {fteEquivalent.toFixed(1)} 个全职人员月产能</small></article>
        <article className="value-kpi-accent"><span>模拟人工经费空间</span><strong>{cny(monthlyLaborValue)}<em>/月</em></strong><small>{cny(annualLaborValue)}/年 · 非现金承诺</small></article>
      </section>

      <div className="value-detail-grid">
        <section className="detail-section comparison-section">
          <div className="section-title"><Gauge weight="duotone" /><strong>合成样本前后对照</strong><span>样本数 {value.case_count || 0}</span></div>
          <div className="comparison-list">{comparisons.map(([label, before, after, ratio]) => <div className="comparison-row" key={label}><div><strong>{label}</strong><small>人工基线 {before}　→　RevGuard {after}</small></div><div className="comparison-track"><span className="before-bar" /><span className="after-bar" style={{ width: `${Math.max(Math.min(ratio * 100, 100), after === "0.0%" ? 0 : 2)}%` }} /></div></div>)}</div>
          <div className="sample-outcomes"><div><span>追回成本下降</span><strong>{percent(recoveryReduction)}</strong></div><div><span>审计异常样本</span><strong>{percent(metrics.audit_exception_rate_before)} → {percent(metrics.audit_exception_rate_after)}</strong></div><div><span>错付样本</span><strong>{percent(metrics.wrong_payment_rate_before)} → {percent(metrics.wrong_payment_rate_after)}</strong></div></div>
        </section>
        <section className="detail-section methodology-section">
          <div className="section-title"><Calculator weight="duotone" /><strong>计算口径与边界</strong><span>每个数字可复算</span></div>
          <div className="formula-callout"><span>月度人工经费空间</span><strong>{savedPerCase} 分钟 × {monthlyCases.toLocaleString()} 案 ÷ 60 × {cny(hourlyCost)}/小时</strong><b>= {cny(monthlyLaborValue)}</b></div>
          <div className="methodology-list"><div><span>数据分类</span><strong>{value.data_classifications?.join(", ") || "等待接口数据"}</strong></div><div><span>生产收益声明</span><strong>{value.production_claim_allowed ? "允许" : "不允许"}</strong></div><div><span>样本来源</span><strong>GOLDEN-001～008 合成案件</strong></div><div><span>企业接入后</span><strong>替换 CSV 基线即可复算</strong></div></div>
          <div className="claim-boundary"><WarningCircle weight="fill" /><span>{contract.claim_boundary || value.guardrail || "当前结果仅用于指标方法验证。"}</span></div>
        </section>
      </div>
    </div>
  );
}

function SafetyRail({ snapshot, onExport }) {
  const c = snapshot?.case || {};
  const approval = snapshot?.approval || {};
  const quotas = approval.component_quota || {};
  const currency = approval.currency || c.claim?.currency || "KES";
  const human = approval.human_identity || {};
  const rolledBack = c.status === "ROLLED_BACK";
  const safety = safetyRailState(snapshot);
  return (
    <aside className="safety-rail"><section className="rail-section"><span className="rail-label">当前安全状态</span><strong className={rolledBack ? "rail-state rollback-state" : "rail-state"}>{c.status || "CREATED"}</strong><span className="rail-label">{safety.label}</span><strong className={`rail-state ${safety.passed ? "passed-state" : ""}`}>{safety.result}</strong><span className="rail-label">{safety.balanceLabel}</span><b>{money(safety.amount, currency)}</b><small>{safety.note}</small></section>
      <section className="rail-section"><span className="rail-label">案例与审批边界</span>{[["绑定案件", c.case_id || "—"], ["币种", currency], ["总额度", money(approvalAmount(snapshot), currency)], ...Object.entries(quotas).map(([component, amount]) => [componentLabel(component), money(amount, currency)]), ["人类审批人", human.display_name || "尚未验证"], ["身份来源", human.sub ? "AgentTeams Matrix" : "等待验证"], ["令牌有效期", "15 分钟"], ["策略范围", c.policy_decision?.policy_version || "待匹配"]].map(([label, value]) => <div className="rail-kv" key={label}><span>{label}</span><strong>{value}</strong></div>)}</section>
      <section className="rail-section export-section"><span className="rail-label">导出证据包</span><button onClick={onExport} disabled={!snapshot?.report_available}><DownloadSimple weight="bold" />导出摘要报告</button><small>完整证据包包含追踪记录、审计日志、报告与校验清单。</small></section></aside>
  );
}

function DecisionView({ snapshot }) {
  return <div className="decision-grid"><div className="decision-left"><EvidenceTable snapshot={snapshot} /><CalculationLedger snapshot={snapshot} /><AuditTrail snapshot={snapshot} /></div><div className="decision-right"><PolicyTimeline snapshot={snapshot} /><AgentMatrix snapshot={snapshot} /></div></div>;
}

function HumanActionDialog({ intent, caseId, busy, onClose, onCommit }) {
  const fixedResume = intent?.kind === "resume";
  const [decision, setDecision] = useState("APPROVED");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [comment, setComment] = useState("证据完整，政策与金额复算一致，同意在当前风险边界内处理。");
  const [proof, setProof] = useState(null);
  const [dialogBusy, setDialogBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  useEffect(() => {
    if (!intent) return;
    setDecision("APPROVED");
    setUsername("");
    setPassword("");
    setComment(fixedResume ? "确认恢复未完成的安全链路，并继续执行幂等保护。" : "证据完整，政策与金额复算一致，同意在当前风险边界内处理。");
    setProof(null);
    setDialogBusy(false);
    setDialogError("");
  }, [intent, fixedResume]);

  if (!intent) return null;
  const action = fixedResume ? "RESUME" : decision;
  const actionLabel = action === "APPROVED" ? "批准" : action === "REJECTED" ? "驳回" : "恢复执行";

  const verifyIdentity = async (event) => {
    event.preventDefault();
    setDialogBusy(true); setDialogError(""); setProof(null);
    try {
      const verified = await api(`/api/v1/cases/${caseId}/human-action/assertion`, null, {
        method: "POST",
        body: JSON.stringify({ username, password, action }),
      });
      setPassword("");
      setProof(verified);
    } catch (err) {
      setPassword("");
      setDialogError(err.message);
    } finally {
      setDialogBusy(false);
    }
  };

  const commit = async () => {
    setDialogBusy(true); setDialogError("");
    try {
      await onCommit({ action, token: proof.assertion_token, comment });
    } catch (err) {
      setDialogError(err.message);
      setProof(null);
    } finally {
      setDialogBusy(false);
    }
  };

  return (
    <div className="human-modal-backdrop" role="presentation">
      <section className="human-modal" role="dialog" aria-modal="true" aria-labelledby="human-action-title">
        <div className="human-modal-header"><div><span>人工控制边界</span><h2 id="human-action-title">AgentTeams 审批人身份验证</h2></div><button type="button" onClick={onClose} disabled={busy || dialogBusy} aria-label="关闭">×</button></div>
        <div className="human-binding-strip"><LockKey weight="duotone" /><div><strong>证明只绑定本案与本次“{actionLabel}”动作</strong><small>密码由后端直接交给 AgentTeams Matrix 验证，智能体无法读取，也不会写入案件、日志或 Trace。</small></div></div>
        {!fixedResume && <div className="decision-switch" aria-label="选择审批结论"><button type="button" className={decision === "APPROVED" ? "active approve" : ""} onClick={() => { setDecision("APPROVED"); setProof(null); }} disabled={dialogBusy}>批准</button><button type="button" className={decision === "REJECTED" ? "active reject" : ""} onClick={() => { setDecision("REJECTED"); setProof(null); }} disabled={dialogBusy}>驳回</button></div>}
        {!proof ? <form className="human-login-form" onSubmit={verifyIdentity}>
          <label><span>AgentTeams 审批账号</span><input value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" placeholder="请输入 Matrix 账号" required /></label>
          <label><span>密码</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" placeholder="仅用于本次身份验证" required /></label>
          {dialogError && <div className="human-dialog-error"><WarningCircle weight="fill" />{dialogError}</div>}
          <button className="human-primary" type="submit" disabled={dialogBusy || busy}>{dialogBusy ? <SpinnerGap className="spin" weight="bold" /> : <Fingerprint weight="bold" />}验证审批账号</button>
        </form> : <div className="human-proof-panel">
          <div className="human-proof-success"><CheckCircle weight="fill" /><div><strong>{proof.identity?.display_name || proof.identity?.sub}</strong><small>{proof.identity?.sub} · AgentTeams Matrix 已验证</small></div><span>有效 {proof.expires_in_seconds} 秒</span></div>
          <div className="human-proof-binding"><div><span>绑定案件</span><code>{proof.binding?.case_id}</code></div><div><span>绑定审批单</span><code>{shortId(proof.binding?.approval_id, 24)}</code></div><div><span>绑定动作</span><strong>{actionLabel}</strong></div></div>
          {!fixedResume && <label className="human-comment"><span>审批意见</span><textarea value={comment} onChange={(event) => setComment(event.target.value)} rows="3" maxLength="500" /></label>}
          {dialogError && <div className="human-dialog-error"><WarningCircle weight="fill" />{dialogError}</div>}
          <div className="human-modal-actions"><button type="button" className="human-secondary" onClick={() => setProof(null)} disabled={dialogBusy || busy}>重新验证</button><button type="button" className={`human-primary ${action === "REJECTED" ? "danger" : ""}`} onClick={commit} disabled={dialogBusy || busy}>{dialogBusy || busy ? <SpinnerGap className="spin" weight="bold" /> : <UserCheck weight="bold" />}确认{actionLabel}</button></div>
        </div>}
      </section>
    </div>
  );
}

export function App() {
  const [caseId, setCaseId] = useState(() => new URLSearchParams(window.location.search).get("case") || DEFAULT_CASE_ID);
  const [cases, setCases] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
  const [engineering, setEngineering] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("decision");
  const [humanAction, setHumanAction] = useState(null);
  const teamRun = snapshot?.case?.team_run || {};
  const teamStale = isStaleTeamRun(teamRun);
  const teamRunning = ACTIVE_RUN_STATUSES.has(teamRun.status) && !teamStale;
  const teamFailure = snapshot?.case?.team_run?.status === "FAILED" ? snapshot.case.team_run : null;
  const rollbackRecoverable = Boolean(
    teamFailure
    && snapshot?.case?.status === "FAILED"
    && ["LedgerReverseSkill", "PostRollbackVerifySkill"].includes(teamFailure.current_stage)
    && snapshot?.verification?.rollback_required,
  );

  const loadCases = useCallback(async () => {
    const page = await api("/api/v1/cases?limit=200", API_KEYS.viewer);
    setCases(page.cases || []);
  }, []);

  const load = useCallback(async () => {
    try {
      const [data, evidence] = await Promise.all([
        api(`/api/v1/cases/${caseId}/dashboard`, API_KEYS.viewer),
        api("/api/v1/ops/evidence", API_KEYS.viewer),
      ]);
      setSnapshot(data); setEngineering(evidence); setError("");
      setCases((current) => current.map((item) => item.case_id === caseId ? { ...item, status: data.case?.status } : item));
    } catch (err) { setError(`无法连接 RevGuard API：${err.message}`); }
  }, [caseId]);
  useEffect(() => { loadCases().catch((err) => setError(`无法读取案件列表：${err.message}`)); }, [loadCases]);
  useEffect(() => { setSnapshot(null); load(); }, [load]);
  useEffect(() => {
    if (!teamRunning) return undefined;
    const timer = window.setInterval(load, 1400);
    return () => window.clearInterval(timer);
  }, [load, teamRunning]);

  const perform = useCallback(async (label, action) => {
    setBusy(true); setError(""); setNotice("");
    try { await action(); await load(); setNotice(label); } catch (err) { setError(err.message); }
    finally { setBusy(false); window.setTimeout(() => setNotice(""), 2200); }
  }, [load]);

  const onCaseChange = (nextCaseId) => {
    const url = new URL(window.location.href);
    url.searchParams.set("case", nextCaseId);
    window.history.replaceState({}, "", url);
    setError(""); setNotice(""); setCaseId(nextCaseId);
  };
  const onReset = () => perform("已恢复全部合成案件初始状态", async () => {
    await api("/api/v1/demo/reset", API_KEYS.operator, { method: "POST" });
    await loadCases();
  });
  const onRun = () => perform("多智能体调查已启动，真实执行者的输入输出将写入任务账本", () => api(`/api/v1/cases/${caseId}/team/run`, API_KEYS.operator, { method: "POST", headers: { "X-Request-ID": `REQ-WEBUI-${caseId}-RUN` } }));
  const onApprove = () => setHumanAction({ kind: "approval" });
  const onResume = () => setHumanAction({ kind: "resume" });
  const commitHumanAction = async ({ action, token, comment }) => {
    setBusy(true); setError(""); setNotice("");
    try {
      if (action === "RESUME") {
        await api(`/api/v1/cases/${caseId}/team/resume`, token, { method: "POST" });
        setNotice("人工身份与恢复动作已绑定，未完成链路正在幂等续跑");
      } else {
        await api(`/api/v1/cases/${caseId}/approval`, token, { method: "POST", body: JSON.stringify({ decision: action, comment }) });
        setNotice(action === "APPROVED" ? "人工审批已记录，执行与独立验证正在后台运行" : "人工驳回已记录，执行权限未签发");
      }
      await load();
      setHumanAction(null);
    } finally {
      setBusy(false);
      window.setTimeout(() => setNotice(""), 2600);
    }
  };
  const onInspect = () => { setTab("audit"); window.setTimeout(() => document.getElementById("rollback-evidence")?.scrollIntoView({ behavior: "smooth", block: "start" }), 60); };
  const onLocateFailure = () => { setTab("decision"); window.setTimeout(() => document.getElementById("agent-task-ledger")?.scrollIntoView({ behavior: "smooth", block: "center" }), 60); };
  const onExport = async () => {
    try {
      const data = await api(`/api/v1/cases/${caseId}/report`, API_KEYS.viewer);
      const blob = new Blob([data.markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${caseId}-audit-report.md`; anchor.click(); URL.revokeObjectURL(url);
    } catch (err) { setError(err.message); }
  };
  const tabs = useMemo(() => [["decision", "决策依据", ClipboardText], ["audit", "执行与审计", Fingerprint], ["permissions", "权限边界", LockKey], ["value", "价值模拟", Calculator], ["engineering", "工程证据", Gauge]], []);

  return (
    <div className="app-shell"><Header snapshot={snapshot} cases={cases} caseId={caseId} busy={busy || teamRunning} onReset={onReset} onCaseChange={onCaseChange} />
      {error && <div className="system-banner error-banner"><WarningCircle weight="fill" />{error}<button onClick={load}>重试</button></div>}
      {!error && teamFailure && <div className="system-banner run-failure-banner"><WarningCircle weight="fill" /><div><strong>{skillLabel(teamFailure.current_stage)}未完成</strong><small>{teamFailure.error?.message || "AgentTeams 未返回具体错误"}</small></div><button onClick={rollbackRecoverable ? onResume : onLocateFailure} disabled={busy}>{rollbackRecoverable ? "继续安全回滚" : "定位任务"}</button></div>}
      {!error && teamStale && <div className="system-banner run-failure-banner"><WarningCircle weight="fill" /><div><strong>执行已中断，不是仍在运行</strong><small>上次进度停在 {skillLabel(teamRun.current_stage)} · {teamRun.completed_tasks || 0}/{teamRun.total_tasks || 0}；续跑会重新授权并用幂等键跳过已完成写入。</small></div><button onClick={onResume} disabled={busy}>{busy ? "恢复中…" : "继续执行"}</button></div>}
      {notice && <div className="system-banner notice-banner"><CheckCircle weight="fill" />{notice}</div>}
      <main><SummaryStrip snapshot={snapshot} /><Pipeline snapshot={snapshot} busy={busy || teamRunning} onRun={onRun} onApprove={onApprove} onInspect={onInspect} />
        <div className="workspace"><section className="content-area"><nav className="tabs" aria-label="案件详情视图">{tabs.map(([id, label, Icon]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}><Icon weight="duotone" />{label}</button>)}</nav>{tab === "decision" && <DecisionView snapshot={snapshot} />}{tab === "audit" && <TraceView snapshot={snapshot} />}{tab === "permissions" && <Permissions snapshot={snapshot} evidence={engineering} />}{tab === "value" && <BusinessValueSimulator evidence={engineering} />}{tab === "engineering" && <EngineeringEvidence evidence={engineering} />}</section><SafetyRail snapshot={snapshot} onExport={onExport} /></div>
      </main><footer><span>RevGuard 面向企业渠道佣金结算异常的多智能体治理平台</span><span>合成业务数据，仅用于演示验证；不代表真实企业交易。</span><span><Clock weight="bold" />北京时间 · 2026-08-31</span></footer>
      <HumanActionDialog intent={humanAction} caseId={caseId} busy={busy} onClose={() => setHumanAction(null)} onCommit={commitHumanAction} />
    </div>
  );
}
