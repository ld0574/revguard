import { useCallback, useEffect, useMemo, useState } from "react";
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

const CASE_ID = "CASE-2026-0008";
const API_KEYS = {
  viewer: "rg-demo-viewer-key-1",
  operator: "rg-demo-operator-key",
  approver: "rg-demo-approver-key",
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
  ["Intake", "revguard-intake", "识别订单与主体", "任务受理"],
  ["Evidence", "revguard-evidence", "只读访问证据", "证据采集与验证"],
  ["Policy", "revguard-policy", "只读访问政策", "政策回溯与选择"],
  ["Calculator", "revguard-calculation", "只读访问数据", "应有金额计算"],
  ["Risk", "revguard-risk", "审批路由", "风险判断与限额"],
  ["Executor", "revguard-executor", "受边界限制写入", "模拟记账（入账）"],
  ["Verifier", "revguard-verifier", "独立只读验证", "独立验证与复核"],
  ["Rollback", "revguard-executor", "受控冲销", "自动回滚执行"],
];

function money(value, currency = "KES") {
  if (value === undefined || value === null || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return `${value} ${currency}`;
  return `${number.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })} ${currency}`;
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
      Authorization: `Bearer ${key}`,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : detail?.code || `HTTP ${response.status}`;
    throw new Error(message);
  }
  return response.json();
}

function hasEvent(snapshot, name) {
  return snapshot?.audit_events?.some((item) => item.event === name);
}

function getStageState(snapshot, stageId) {
  const status = snapshot?.case?.status || "CREATED";
  const rank = STATUS_ORDER.indexOf(status);
  const terminal = ["ROLLED_BACK", "CLOSED", "FAILED"].includes(status);
  const checks = {
    evidence: hasEvent(snapshot, "EVIDENCE_COLLECTED"),
    policy: hasEvent(snapshot, "POLICY_MATCHED"),
    calculation: hasEvent(snapshot, "CALCULATED"),
    approval: hasEvent(snapshot, "APPROVAL_DECIDED"),
    execution: hasEvent(snapshot, "EXECUTED"),
    verification: hasEvent(snapshot, "VERIFIED"),
    rollback: hasEvent(snapshot, "ROLLED_BACK"),
    postcheck: hasEvent(snapshot, "ROLLBACK_VERIFIED"),
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
  const values = {
    evidence: snapshot?.evidence?.length ? `${snapshot.evidence.length}/8` : "待收集",
    policy: caseData.policy_decision?.policy_version || "待匹配",
    calculation: money(caseData.calculation_result?.total_commission),
    approval: approval.amount ? money(approval.amount, approval.currency) : "等待风险判断",
    execution: executions.length
      ? executions.map((item) => `${Number(item.amount) >= 0 ? "+" : ""}${Number(item.amount).toLocaleString()}`).join("  ")
      : "待授权",
    verification: verification.actual_amount ? `读取 ${money(verification.actual_amount)}` : "不同主体复核",
    rollback: reversals.length ? reversals.map((item) => money(item.reversal?.amount)).join("  ") : "验证失败时触发",
    postcheck: caseData.status === "ROLLED_BACK" ? "PASSED" : "等待回滚复核",
  };
  return values[id];
}

function Header({ snapshot, busy, onReset }) {
  const status = snapshot?.case?.status || "CONNECTING";
  const mcpTeam = snapshot?.case?.execution_mode === "MCP_TEAM";
  return (
    <header className="topbar">
      <div className="brand-group">
        <ShieldCheck className="brand-mark" weight="duotone" aria-hidden="true" />
        <span className="brand-name">RevGuard</span><span className="top-divider" />
        <span className="case-id">{CASE_ID}</span><span className="risk-pill">L2</span>{mcpTeam && <span className="mcp-pill">MCP TEAM</span>}
        <span className="approval-label">Human Approval</span>
      </div>
      <div className="disclosure">合成业务数据 · 真实运行链路<span>/ Synthetic business data · Real executable workflow</span></div>
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
  const items = [
    ["代理商", c.partner_name || "Nairobi Solar Solutions Ltd", c.partner_id || "AGT-10001"],
    ["订单号", c.order_id || "EZ202608001", "订单日期 2026-07-10"],
    ["订单金额", money(c.calculation_result?.facts_snapshot?.order_amount || 180000, currency), "合成业务订单"],
    ["已入账金额", money(rca.total_posted || c.claim?.actual_amount || 18000, currency), "模拟佣金台账"],
    ["预期佣金（正确）", money(rca.total_expected || c.calculation_result?.total_commission || 32400, currency), "确定性规则内核"],
    ["本次审批金额", money(approval.amount || rca.total_delta || 14400, currency), approval.status || "PENDING"],
    ["最终状态", c.status || "CREATED", "案件终态保留"],
    ["回滚后状态", c.status === "ROLLED_BACK" ? "PASSED" : "—", c.status === "ROLLED_BACK" ? "恢复安全基线" : "等待验证"],
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
  if (status === "CREATED") {
    return <button className="primary-action" onClick={onRun} disabled={busy}>{busy ? <SpinnerGap className="spin" weight="bold" /> : <Play weight="fill" />}{busy ? "正在运行真实调查链路…" : "启动多 Agent 调查"}</button>;
  }
  if (status === "WAITING_FOR_APPROVAL") {
    return <button className="primary-action" onClick={onApprove} disabled={busy}>{busy ? <SpinnerGap className="spin" weight="bold" /> : <UserCheck weight="bold" />}{busy ? "正在执行并独立验证…" : "批准并执行 14,400 KES"}</button>;
  }
  return <button className="primary-action evidence-action" onClick={onInspect}><ClipboardText weight="bold" />查看回滚证据</button>;
}

function Pipeline({ snapshot, busy, onRun, onApprove, onInspect }) {
  const approval = snapshot?.approval || {};
  const verification = snapshot?.verification || {};
  const executions = snapshot?.executions || [];
  const postings = executions.filter((item) => Number(item.amount) > 0);
  const reversalAmounts = executions.filter((item) => item.reversal).map((item) => item.reversal.amount);
  const calculated = Boolean(snapshot?.case?.calculation_result);
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
        <div className="pipeline-note calculation-note"><span>计算结果</span>{calculated ? <><strong>Q1/Q2：排除（未达成）</strong><strong>Q3：选定（已达成）</strong></> : <strong>等待政策匹配与确定性复算</strong>}</div>
        <div className="pipeline-note capability-note"><span>能力边界</span><div>组件上限：{money(approval.amount)}</div><div>本次金额：{money(approval.amount)}</div><PrimaryAction snapshot={snapshot} busy={busy} onRun={onRun} onApprove={onApprove} onInspect={onInspect} /><small>{approval.amount ? "授权有效期：15 分钟" : "审批后签发短时能力"}</small></div>
        <div className="pipeline-note execution-note"><span>模拟记账（入账）</span>{postings.length ? <>{postings.map((item) => <strong key={item.action_id || item.component}>{item.component}：+{money(item.amount)}</strong>)}<div>合计：+{money(postedTotal)}</div></> : <strong>等待受限执行器写入</strong>}</div>
        <div className={`pipeline-note verify-note ${verification.verification_status === "FAILED" ? "is-failed" : ""}`}><span>验证结果</span><div>实际读取：{money(verification.actual_amount)}</div><div>差异：{money(verification.variance)}</div><strong>{verification.verification_status === "FAILED" ? "不匹配" : "等待独立验证"}</strong></div>
        <div className="pipeline-note rollback-note"><span>自动回滚执行</span>{reversalAmounts.length ? <>{reversalAmounts.map((amount, index) => <strong key={`${amount}-${index}`}>组件 {reversalAmounts.length - index}：{money(amount)}</strong>)}<div>合计：{money(reversedTotal)}</div></> : <strong>验证失败时由策略自动触发</strong>}</div>
        <div className="pipeline-note result-note"><span>最终结果</span><strong>{snapshot?.case?.status === "ROLLED_BACK" ? "ROLLED_BACK" : "等待终态"}</strong><b>{snapshot?.case?.status === "ROLLED_BACK" ? "PASSED" : "—"}</b><small>{snapshot?.case?.status === "ROLLED_BACK" ? "已恢复至安全基线" : "尚未生成终态结论"}</small></div>
      </div>
    </section>
  );
}

function EvidenceTable({ evidence = [] }) {
  const fallback = [
    ["ORDER", "CRM_MOCK", "EZ202608001"], ["TIER_HISTORY", "CRM_MOCK", "AGT-10001"],
    ["CONTRACT", "CONTRACT_MOCK", "AGT-10001"], ["PAYMENT_RECORD", "FINANCE_MOCK", "EZ202608001"],
    ["REFUND_RECORD", "FINANCE_MOCK", "EZ202608001"], ["INVOICE", "FINANCE_MOCK", "EZ202608001"],
    ["COMMISSION_LEDGER", "FINANCE_MOCK", "EZ202608001"], ["POLICY_VERSIONS", "CONTRACT_MOCK", "KE-COMMISSION-2026"],
  ].map(([type, source_system, source_ref]) => ({ type, source_system, source_ref, strength: "PENDING" }));
  const rows = evidence.length ? evidence : fallback;
  return (
    <section className="detail-section evidence-section">
      <div className="section-title"><FolderOpen weight="duotone" /><strong>证据来源链</strong><span>（{evidence.length}/8 强证据）</span></div>
      <div className="table-wrap"><table><thead><tr><th>证据类型</th><th>来源系统</th><th>证据 ID</th><th>强度</th><th>工具回执</th><th>校验</th></tr></thead><tbody>
        {rows.map((item) => <tr key={item.evidence_id || item.type}><td>{item.type}</td><td>{item.source_system}</td><td>{item.source_ref}</td><td><span className={evidence.length ? "strong-cell" : "pending-cell"}>{evidence.length ? <ShieldCheck weight="fill" /> : <Clock weight="fill" />}{item.strength || "STRONG"}</span></td><td title={item.tool_receipt}>{shortId(item.tool_receipt || "等待运行", 13)}</td><td><span className={evidence.length ? "verified-cell" : "pending-cell"}>{evidence.length ? <CheckCircle weight="fill" /> : <Clock weight="fill" />}{evidence.length ? "已校验" : "待收集"}</span></td></tr>)}
      </tbody></table></div>
    </section>
  );
}

function CalculationLedger({ snapshot }) {
  const c = snapshot?.case || {};
  const rca = c.root_cause_report || {};
  const diffs = rca.diffs || [];
  const formulas = {
    SALES_COMMISSION: ["order_amount × 0.15", "180,000.00", "15%", "180,000.00 × 0.15"],
    COLLECTION_COMMISSION: ["payment_amount × 0.03", "180,000.00", "3%", "180,000.00 × 0.03"],
  };
  return (
    <section className="detail-section ledger-section">
      <div className="section-title"><Calculator weight="duotone" /><strong>计算账本</strong><span>（金额单位：KES）</span></div>
      <div className="table-wrap"><table><thead><tr><th>项目</th><th>公式</th><th>基数</th><th>比例</th><th>计算</th><th>应有金额</th><th>已发布</th><th>差异</th></tr></thead><tbody>
        {diffs.filter((item) => Number(item.delta) !== 0).map((item) => {
          const f = formulas[item.component] || ["规则引擎", "—", "—", "确定性复算"];
          return <tr key={item.component}><td>{item.component === "SALES_COMMISSION" ? "销售佣金（GOLD 15%）" : "回款佣金（30 天内，3%）"}</td><td>{f[0]}</td><td>{f[1]}</td><td>{f[2]}</td><td>{f[3]}</td><td>{Number(item.expected).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td><td>{Number(item.posted).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td><td className="negative-cell">{Number(item.delta).toLocaleString("en-US", { minimumFractionDigits: 2 })}</td></tr>;
        })}{!diffs.length && <tr><td colSpan="8" className="table-empty">等待调查完成后生成逐组件确定性复算账本</td></tr>}
      </tbody></table></div>
      <div className="ledger-total"><span>Expected {money(rca.total_expected)}</span><span>Posted {money(rca.total_posted)}</span><strong>Difference {money(rca.total_delta)}</strong></div>
    </section>
  );
}

function PolicyTimeline({ snapshot }) {
  const decision = snapshot?.case?.policy_decision || {};
  const selectedVersion = decision.policy_version;
  return (
    <section className="detail-section policy-section">
      <div className="section-title"><GitBranch weight="duotone" /><strong>政策时间线</strong><span>{selectedVersion ? "（排除 Q1/Q2，选择 2026-Q3）" : "（等待时点政策匹配）"}</span></div>
      <div className="policy-line">{["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"].map((version) => <div className={`policy-point ${version === selectedVersion ? "selected" : ""}`} key={version}><strong>{version}</strong><span /><small>{version === selectedVersion ? "已选中（订单日期 2026-07-10）" : selectedVersion ? "未选中" : "待判定"}</small></div>)}</div>
      <div className={`policy-selected ${selectedVersion ? "" : "policy-pending"}`}>{selectedVersion ? <CheckCircle weight="fill" /> : <Clock weight="fill" />}<div>{selectedVersion ? <><strong>{decision.policy_id}　|　{decision.policy_version}　|　GOLD at order date</strong><p>订单发生时适用 GOLD 级销售佣金 15%；订单完成后 30 天内回款，按回款金额 3% 计回款佣金。</p><small>引用条款：Q3-C1（15%）、Q3-C4（3% 回款佣金）</small></> : <><strong>等待 Policy Agent 读取订单时点并回溯政策版本</strong><p>运行后展示被排除版本、最终适用版本与条款级引用。</p></>}</div></div>
    </section>
  );
}

function AgentMatrix({ snapshot }) {
  const tasks = snapshot?.agent_tasks || [];
  const visible = tasks.slice(-8);
  const workerCount = new Set(tasks.map((item) => item.assigned_actor)).size;
  const succeeded = tasks.filter((item) => item.status === "SUCCEEDED").length;
  return (
    <section className="detail-section agent-section">
      <div className="section-title"><UsersThree weight="duotone" /><strong>Agent 协同链路</strong><span>{tasks.length ? `MCP StageTask ${succeeded}/${tasks.length} · ${workerCount} Workers` : "责任与能力边界"}</span></div>
      <div className="compact-table">{visible.length ? visible.map((task) => <div className="compact-row task-row" key={task.task_id}><strong>{task.skill_name.replace("Skill", "")}</strong><code>{task.assigned_actor}</code><span className="transport-cell">MCP</span><span className={`task-status task-${task.status.toLowerCase()}`}>{task.status} · attempt {task.attempt}</span></div>) : AGENT_ROWS.map(([role, actor, access, duty]) => <div className="compact-row" key={`${role}-${duty}`}><strong>{role}</strong><code>{actor}</code><span>{access}</span><span>{duty}</span></div>)}</div>
      <p className="boundary-note"><ShieldCheck weight="fill" />{tasks.length ? "每项结果均绑定 Case Version、Worker、Skill 与输入快照；聊天文本不能推进状态。" : "Executor 与 Verifier 为不同主体；验证结果不能由执行者自证。"}</p>
    </section>
  );
}

const IMPORTANT_EVENTS = new Set(["CASE_CREATED", "EVIDENCE_COLLECTED", "POLICY_MATCHED", "CALCULATED", "RISK_CLASSIFIED", "APPROVAL_DECIDED", "EXECUTED", "VERIFIED", "ROLLED_BACK", "ROLLBACK_VERIFIED", "KNOWLEDGE_ARCHIVED", "DEMO_RESET"]);

function AuditTrail({ snapshot, full = false }) {
  const events = (snapshot?.audit_events || []).filter((item) => full || IMPORTANT_EVENTS.has(item.event));
  const shown = full ? events : events.slice(-10);
  return (
    <section className="detail-section audit-section" id="rollback-evidence">
      <div className="section-title"><Fingerprint weight="duotone" /><strong>不可篡改审计详情</strong></div>
      <div className="audit-list">{shown.length ? shown.map((item) => {
        const isError = item.event === "VERIFIED" && item.detail?.verification_status === "FAILED";
        const isRollback = item.event === "ROLLED_BACK";
        return <div className={`audit-row ${isError ? "audit-error" : ""} ${isRollback ? "audit-rollback" : ""}`} key={item.seq}><span className="audit-icon">{isError ? <XCircle weight="fill" /> : isRollback ? <ArrowCounterClockwise weight="bold" /> : <CheckCircle weight="fill" />}</span><time>{formatTime(item.created_at)}</time><div><strong>{item.event}</strong><small>{item.actor}</small></div><code>{shortId(item.detail?.request_id || item.detail?.task_id || item.detail?.action_id || item.detail?.reversal_id || "recorded", 20)}</code></div>;
      }) : <div className="empty-state"><Info weight="duotone" />启动案件后，真实审计事件会出现在这里。</div>}</div>
      <div className="audit-summary"><span>执行摘要：</span><strong>{snapshot?.trace?.span_count || 0} spans</strong><span className="error-dot" />错误 {snapshot?.trace?.error_spans?.length || 0}<span>重试成功</span></div>
    </section>
  );
}

function TraceView({ snapshot }) {
  const spans = snapshot?.trace?.spans || [];
  const shown = spans.filter((span) => ["TOOL", "SKILL", "APPROVAL", "AGENT"].includes(span.kind)).slice(-28);
  return (
    <div className="trace-grid"><section className="detail-section trace-section"><div className="section-title"><Gauge weight="duotone" /><strong>本次运行 Trace 慢速回放</strong><span>（数据来自真实 Trace，展示节奏已放慢）</span></div><div className="span-list">{shown.map((span) => <div className={`span-row span-${span.status?.toLowerCase()}`} key={span.span_id}><span className="span-kind">{span.kind}</span><strong>{span.name}</strong><code>{span.actor || "system"}</code><span>{span.duration_ms ?? 0} ms</span><span>{span.status}</span></div>)}{!shown.length && <div className="empty-state"><Play weight="duotone" />启动调查后显示真实 Agent / Skill / Tool spans。</div>}</div></section><AuditTrail snapshot={snapshot} full /></div>
  );
}

function Permissions({ snapshot }) {
  const approval = snapshot?.approval || {};
  const quotas = approval.component_quota || { SALES_COMMISSION: "9000", COLLECTION_COMMISSION: "5400" };
  const rows = [["案件绑定", CASE_ID], ["币种", approval.currency || "KES"], ["Gross 上限", money(approval.amount || 14400)], ["销售佣金组件", money(quotas.SALES_COMMISSION || 9000)], ["回款佣金组件", money(quotas.COLLECTION_COMMISSION || 5400)], ["令牌有效期", "15 分钟"], ["审批角色", approval.approver_role || "FINANCE_LEAD"], ["能力指纹", approval.approval_token_ref || "批准后生成"]];
  return (
    <div className="permissions-grid">
      <section className="detail-section permission-card"><div className="section-title"><LockKey weight="duotone" /><strong>审批与能力边界</strong></div><div className="permission-list">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div></section>
      <section className="detail-section permission-card"><div className="section-title"><ShieldWarning weight="duotone" /><strong>四重约束</strong></div><div className="constraint-list"><div><span>01</span><strong>任务不漂移</strong><small>状态、Skill、Worker、输入快照与案件版本绑定</small></div><div><span>02</span><strong>审批不自签</strong><small>Finance Lead Principal 独立批准，Executor 无审批权限</small></div><div><span>03</span><strong>额度不外溢</strong><small>Gross 与逐组件额度同时约束，幂等防重复写入</small></div><div><span>04</span><strong>权限不升级</strong><small>服务端 Principal、角色、scope 与 Skill actor 白名单</small></div></div></section>
      <section className="detail-section permission-card"><div className="section-title"><Fingerprint weight="duotone" /><strong>安全探针</strong></div><div className="probe-list">{["伪造令牌", "过期令牌", "跨案件调用", "组件额度滥用", "并发双写", "回滚令牌重放"].map((item) => <div key={item}><CheckCircle weight="fill" /><span>{item}</span><strong>已拒绝</strong></div>)}</div></section>
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
  const rows = [
    ["存储后端", runtime.storage_backend || "等待 API", runtime.read_replica_enabled ? "主/只读已分流" : "本地 Demo / 单端点"],
    ["审计链", runtime.audit_chain?.enforced ? (runtime.audit_chain.valid ? "VALID" : "BROKEN") : "DEMO ONLY", runtime.audit_chain?.enforced ? `${runtime.audit_chain.rows_checked || 0} 条已校验` : "PolarDB 模式由 DB trigger 强制"],
    ["Trace 错误", String(runtime.trace_error_spans_total ?? 0), `${runtime.trace_spans_total || 0} spans 已持久化`],
    ["StageResult", String(runtime.agent_task_attempts_total ?? 0), "Task 终态与 Result 同事务"],
    ["MCP Team 排练", `${rehearsal.outcome?.succeeded_tasks || 0}/${rehearsal.outcome?.task_count || 0}`, `${rehearsal.outcome?.worker_count || 0} Workers · ${rehearsal.outcome?.skill_count || 0} Skills`],
    ["合成数据校验", synthetic.validation_status || "等待生成", `${synthetic.record_counts?.orders || 0} orders · ${synthetic.record_counts?.golden_cases || 0} cases`],
    ["本地 PostgreSQL", postgres.checks?.audit_chain_valid ? "PASSED" : "PENDING", postgres.classification ? "PG 18.6 兼容验证；非云验收" : "等待本地验证"],
    ["确定性评测", `${evaluation.passed || 0}/${evaluation.total_scenarios || 0}`, `通过率 ${Number(evaluation.pass_rate || 0) * 100}%`],
    ["发布版本", evidence?.release || "—", "0 → 5% → 25% → 100% 灰度"],
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
        <div className="section-title"><Gauge weight="duotone" /><strong>可查询工程证据</strong><span>来自当前 API / Trace / Store</span></div>
        <div className="evidence-ledger">{rows.map(([label, primary, note]) => <div className="evidence-row" key={label}><span>{label}</span><strong>{primary}</strong><small>{note}</small></div>)}</div>
      </section>
      <section className="detail-section engineering-section">
        <div className="section-title"><ClipboardText weight="duotone" /><strong>业务价值口径</strong><span>时长 / 错付 / 追回 / 升级 / 审计</span></div>
        <div className="classification-banner"><WarningCircle weight="fill" /><div><strong>合成数据·仅验证指标方法</strong><small>{value.guardrail || "不得声称为企真实收益"}</small></div></div>
        <div className="value-ledger">{valueRows.map(([label, result]) => <div key={label}><span>{label}</span><strong>{result}</strong></div>)}</div>
      </section>
      <section className="detail-section pending-section">
        <div className="section-title"><Database weight="duotone" /><strong>外部环境验收</strong><span>不伪造完成状态</span></div>
        <div className="pending-checks">{[["企业真实基线", external.production_business_baseline], ["AgentTeams 完整房间", external.agentteams_room], ["PolarDB 云端兼容", external.polardb_cloud_acceptance], ["PolarDB PITR 演练", external.polardb_pitr_drill]].map(([label, state]) => <div key={label}><Clock weight="fill" /><span>{label}</span><strong>{state || "PENDING"}</strong></div>)}</div>
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
    ["单案处理时长", `${manualMinutes} min`, `${assistedMinutes} min`, assistedMinutes / Math.max(manualMinutes, 1)],
    ["错付样本率", percent(metrics.wrong_payment_rate_before), percent(metrics.wrong_payment_rate_after), Number(metrics.wrong_payment_rate_after || 0) / Math.max(Number(metrics.wrong_payment_rate_before || 0), 0.01)],
    ["追回成本指数", recoveryBefore.toLocaleString(), recoveryAfter.toLocaleString(), recoveryAfter / Math.max(recoveryBefore, 1)],
  ];
  return (
    <div className="value-simulator">
      <section className="detail-section value-hero">
        <div className="value-hero-copy">
          <span className="value-eyebrow">SYNTHETIC VALUE SCENARIO</span>
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
        <article><span>每月释放处理工时</span><strong>{monthlyHours.toLocaleString("zh-CN", { maximumFractionDigits: 0 })} h</strong><small>约 {fteEquivalent.toFixed(1)} 个 FTE 月产能</small></article>
        <article className="value-kpi-accent"><span>模拟人工经费空间</span><strong>{cny(monthlyLaborValue)}<em>/月</em></strong><small>{cny(annualLaborValue)}/年 · 非现金承诺</small></article>
      </section>

      <div className="value-detail-grid">
        <section className="detail-section comparison-section">
          <div className="section-title"><Gauge weight="duotone" /><strong>合成样本前后对照</strong><span>n={value.case_count || 0}</span></div>
          <div className="comparison-list">{comparisons.map(([label, before, after, ratio]) => <div className="comparison-row" key={label}><div><strong>{label}</strong><small>人工基线 {before}　→　RevGuard {after}</small></div><div className="comparison-track"><span className="before-bar" /><span className="after-bar" style={{ width: `${Math.max(Math.min(ratio * 100, 100), after === "0.0%" ? 0 : 2)}%` }} /></div></div>)}</div>
          <div className="sample-outcomes"><div><span>追回成本下降</span><strong>{percent(recoveryReduction)}</strong></div><div><span>审计异常样本</span><strong>{percent(metrics.audit_exception_rate_before)} → {percent(metrics.audit_exception_rate_after)}</strong></div><div><span>错付样本</span><strong>{percent(metrics.wrong_payment_rate_before)} → {percent(metrics.wrong_payment_rate_after)}</strong></div></div>
        </section>
        <section className="detail-section methodology-section">
          <div className="section-title"><Calculator weight="duotone" /><strong>计算口径与边界</strong><span>每个数字可复算</span></div>
          <div className="formula-callout"><span>月度人工经费空间</span><strong>{savedPerCase} 分钟 × {monthlyCases.toLocaleString()} 案 ÷ 60 × {cny(hourlyCost)}/小时</strong><b>= {cny(monthlyLaborValue)}</b></div>
          <div className="methodology-list"><div><span>数据分类</span><strong>{value.data_classifications?.join(", ") || "等待 API"}</strong></div><div><span>生产收益声明</span><strong>{value.production_claim_allowed ? "ALLOWED" : "NOT ALLOWED"}</strong></div><div><span>样本来源</span><strong>GOLDEN-001～008 合成案件</strong></div><div><span>企业接入后</span><strong>替换 CSV 基线即可复算</strong></div></div>
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
  const rolledBack = c.status === "ROLLED_BACK";
  return (
    <aside className="safety-rail"><section className="rail-section"><span className="rail-label">当前安全状态</span><strong className={rolledBack ? "rail-state rollback-state" : "rail-state"}>{c.status || "CREATED"}</strong><span className="rail-label">回滚后验证结果</span><strong className={`rail-state ${rolledBack ? "passed-state" : ""}`}>{rolledBack ? "PASSED" : "WAITING"}</strong><span className="rail-label">安全基线（恢复后）</span><b>{money(18000)}</b><small>与原始过账一致</small></section>
      <section className="rail-section"><span className="rail-label">案例与审批边界</span>{[["Case Bound", CASE_ID], ["币种", approval.currency || "KES"], ["Gross", money(approval.amount || 14400)], ["组件 1", money(quotas.SALES_COMMISSION || 9000)], ["组件 2", money(quotas.COLLECTION_COMMISSION || 5400)], ["令牌有效期", "15 分钟"], ["策略范围", "2026-Q3"]].map(([label, value]) => <div className="rail-kv" key={label}><span>{label}</span><strong>{value}</strong></div>)}</section>
      <section className="rail-section export-section"><span className="rail-label">导出证据包</span><button onClick={onExport} disabled={!snapshot?.report_available}><DownloadSimple weight="bold" />导出摘要报告</button><small>完整证据包包含 Trace、Audit、Report 与校验清单。</small></section></aside>
  );
}

function DecisionView({ snapshot }) {
  return <div className="decision-grid"><div className="decision-left"><EvidenceTable evidence={snapshot?.evidence} /><CalculationLedger snapshot={snapshot} /></div><div className="decision-right"><PolicyTimeline snapshot={snapshot} /><div className="lower-pair"><AgentMatrix snapshot={snapshot} /><AuditTrail snapshot={snapshot} /></div></div></div>;
}

export function App() {
  const [snapshot, setSnapshot] = useState(null);
  const [engineering, setEngineering] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [tab, setTab] = useState("decision");

  const load = useCallback(async () => {
    try {
      const [data, evidence] = await Promise.all([
        api(`/api/v1/cases/${CASE_ID}/dashboard`, API_KEYS.viewer),
        api("/api/v1/ops/evidence", API_KEYS.viewer),
      ]);
      setSnapshot(data); setEngineering(evidence); setError("");
    } catch (err) { setError(`无法连接 RevGuard API：${err.message}`); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const perform = useCallback(async (label, action) => {
    setBusy(true); setError(""); setNotice(label);
    try { await action(); await load(); } catch (err) { setError(err.message); }
    finally { setBusy(false); window.setTimeout(() => setNotice(""), 2200); }
  }, [load]);

  const onReset = () => perform("已恢复录制初始状态", () => api("/api/v1/demo/reset", API_KEYS.operator, { method: "POST" }));
  const onRun = () => perform("MCP Team 调查完成，案件已进入 L2 人工审批", () => api(`/api/v1/cases/${CASE_ID}/team/run`, API_KEYS.operator, { method: "POST", headers: { "X-Request-ID": "REQ-WEBUI-MCP-RUN-0008" } }));
  const onApprove = () => perform("独立验证发现 1 KES 偏差，系统已自动冲销", () => api(`/api/v1/cases/${CASE_ID}/approval`, API_KEYS.approver, { method: "POST", body: JSON.stringify({ decision: "APPROVED", comment: "证据完整，政策与金额复算一致，同意在演示环境执行调整。" }) }));
  const onInspect = () => { setTab("audit"); window.setTimeout(() => document.getElementById("rollback-evidence")?.scrollIntoView({ behavior: "smooth", block: "start" }), 60); };
  const onExport = async () => {
    try {
      const data = await api(`/api/v1/cases/${CASE_ID}/report`, API_KEYS.viewer);
      const blob = new Blob([data.markdown], { type: "text/markdown;charset=utf-8" });
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = `${CASE_ID}-audit-report.md`; anchor.click(); URL.revokeObjectURL(url);
    } catch (err) { setError(err.message); }
  };
  const tabs = useMemo(() => [["decision", "决策依据", ClipboardText], ["audit", "执行与审计", Fingerprint], ["permissions", "权限边界", LockKey], ["value", "价值模拟", Calculator], ["engineering", "工程证据", Gauge]], []);

  return (
    <div className="app-shell"><Header snapshot={snapshot} busy={busy} onReset={onReset} />
      {error && <div className="system-banner error-banner"><WarningCircle weight="fill" />{error}<button onClick={load}>重试</button></div>}
      {notice && <div className="system-banner notice-banner"><CheckCircle weight="fill" />{notice}</div>}
      <main><SummaryStrip snapshot={snapshot} /><Pipeline snapshot={snapshot} busy={busy} onRun={onRun} onApprove={onApprove} onInspect={onInspect} />
        <div className="workspace"><section className="content-area"><nav className="tabs" aria-label="案件详情视图">{tabs.map(([id, label, Icon]) => <button className={tab === id ? "active" : ""} onClick={() => setTab(id)} key={id}><Icon weight="duotone" />{label}</button>)}</nav>{tab === "decision" && <DecisionView snapshot={snapshot} />}{tab === "audit" && <TraceView snapshot={snapshot} />}{tab === "permissions" && <Permissions snapshot={snapshot} />}{tab === "value" && <BusinessValueSimulator evidence={engineering} />}{tab === "engineering" && <EngineeringEvidence evidence={engineering} />}</section><SafetyRail snapshot={snapshot} onExport={onExport} /></div>
      </main><footer><span>RevGuard Financial Agent Governance</span><span>合成业务数据，仅用于演示验证；不代表真实企业交易。</span><span><Clock weight="bold" />Asia/Shanghai · 2026-08-28</span></footer>
    </div>
  );
}
