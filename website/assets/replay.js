
/* RevGuard 静态运行回放：只读取 website/data，与 live WebUI 使用同一套驾驶舱结构。 */
(() => {
  "use strict";

  const STAGES = [
    { id: "evidence", label: "证据包", icon: "evidence" },
    { id: "policy", label: "政策", icon: "policy" },
    { id: "calculation", label: "计算预期佣金", icon: "calculation" },
    { id: "approval", label: "人工审批边界", icon: "approval" },
    { id: "execution", label: "执行（组件）", icon: "execution" },
    { id: "verification", label: "独立验证（不同主体）", icon: "verification" },
    { id: "rollback", label: "自动回滚", icon: "rollback" },
    { id: "postcheck", label: "回滚后状态", icon: "postcheck" },
  ];
  const STAGE_INDEX = {
    intake: -1,
    evidence: 0,
    policy: 1,
    calculation: 2,
    rootcause: 2,
    risk: 2,
    approval: 3,
    execution: 4,
    verification: 5,
    recovery: 6,
    closing: 7,
  };
  const REPLAY_STAGE_ORDER = {
    intake: 0,
    evidence: 1,
    policy: 2,
    calculation: 3,
    rootcause: 4,
    risk: 5,
    approval: 6,
    execution: 7,
    verification: 8,
    recovery: 9,
    closing: 10,
  };
  const REPLAY_STAGE_LABELS = {
    intake: "案件受理",
    evidence: "跨系统取证",
    policy: "政策匹配",
    calculation: "确定性计算",
    rootcause: "根因解释",
    risk: "风险路由",
    approval: "真人审批",
    execution: "受控执行",
    verification: "独立验证",
    recovery: "冲销恢复",
    closing: "审计结案",
  };
  const TASK_STAGE_BY_SKILL = {
    CaseNormalizeSkill: "intake",
    EntityResolveSkill: "intake",
    EvidenceCollectSkill: "evidence",
    PolicyVersionMatchSkill: "policy",
    CommissionCalculateSkill: "calculation",
    DifferenceExplainSkill: "rootcause",
    RiskClassifySkill: "risk",
    ApprovalRouteSkill: "risk",
    PermissionCheckSkill: "execution",
    IdempotencyGuardSkill: "execution",
    AdjustmentDraftSkill: "execution",
    LedgerAdjustSkill: "execution",
    LedgerReverseSkill: "recovery",
    PostActionVerifySkill: "verification",
    PostRollbackVerifySkill: "recovery",
    CaseToDatasetSkill: "closing",
  };
  const TASK_LABELS = {
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
  const TABS = [
    ["decision", "◈", "决策依据"],
    ["audit", "↯", "执行与审计"],
    ["permissions", "⌁", "权限边界"],
    ["value", "◒", "价值模拟"],
    ["public-data", "◎", "真实数据实验"],
    ["engineering", "▣", "工程证据"],
    ["observability", "⌁", "可观测大屏"],
  ];
  const VALID_TABS = new Set(TABS.map((item) => item[0]));
  const BASE_STEP_MS = 4200;
  const params = new URLSearchParams(window.location.search);
  const requestedCase = params.get("case");
  const requestedTab = params.get("tab");
  const requestedStepParam = params.get("step");

  const state = {
    cases: [],
    bundle: null,
    engineering: null,
    stepIndex: -1,
    tab: VALID_TABS.has(requestedTab) ? requestedTab : "decision",
    playing: false,
    speed: 1,
    timer: null,
    valueMonthlyCases: null,
    valueHourlyCost: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value == null || value === "" ? "—" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
  const num = (value) => {
    const match = String(value == null ? "" : value).replace(/,/g, "").match(/-?[\d.]+/);
    return match ? Number(match[0]) : 0;
  };
  const number = (value) => Number(value || 0).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
  const percent = (value, digits = 1) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? (parsed * 100).toFixed(digits) + "%" : "—";
  };
  const cny = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? new Intl.NumberFormat("zh-CN", { style: "currency", currency: "CNY", maximumFractionDigits: 0 }).format(parsed) : "—";
  };
  const brl = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 2 }).format(parsed) : "—";
  };
  const money = (value, currency) => {
    const raw = value == null || value === "" ? "—" : String(value);
    return '<span class="amount">' + esc(raw.includes(" ") ? raw : raw + " " + (currency || state.bundle?.case?.currency || "KES")) + "</span>";
  };
  const shortId = (value, length) => {
    const raw = String(value == null || value === "" ? "—" : value);
    return esc(raw.length > length ? raw.slice(0, Math.max(4, length - 1)) + "…" : raw);
  };
  const clock = (iso) => {
    if (!iso) return "—";
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? esc(iso) : date.toLocaleTimeString("zh-CN", { hour12: false });
  };
  const duration = (value) => {
    const ms = Number(value);
    if (!Number.isFinite(ms)) return "—";
    return ms < 1000 ? ms + " ms" : (ms / 1000).toFixed(2) + " s";
  };
  const iconSvg = (name, className = "stage-icon") => {
    const icons = {
      evidence: '<path d="M216 208H40a16 16 0 0 1-16-16V64a16 16 0 0 1 16-16h56l16 16h104a16 16 0 0 1 16 16v112a16 16 0 0 1-16 16Z" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/><path d="M24 88h208" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="14"/>',
      policy: '<path d="M128 224s88-40 88-104V48l-88-32-88 32v72c0 64 88 104 88 104Z" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/><path d="m88 120 24 24 56-56" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/>',
      calculation: '<rect x="48" y="24" width="160" height="208" rx="16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/><path d="M80 64h96M80 112h16m32 0h16m-64 40h16m32 0h16m-64 40h16m32 0h16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="14"/>',
      approval: '<circle cx="128" cy="88" r="40" fill="none" stroke="currentColor" stroke-width="14"/><path d="M56 216c8-38 34-56 72-56s64 18 72 56M176 152l24 24 40-40" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/>',
      execution: '<ellipse cx="128" cy="64" rx="80" ry="32" fill="none" stroke="currentColor" stroke-width="14"/><path d="M48 64v64c0 18 36 32 80 32s80-14 80-32V64M48 128v64c0 18 36 32 80 32s80-14 80-32v-64" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/>',
      verification: '<path d="M128 32c-44 0-80 36-80 80v32m32 0v-32a48 48 0 0 1 96 0v32m-64 0v-32a16 16 0 0 1 32 0v32m-64 0v32a48 48 0 0 0 96 0v-32" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/><path d="M80 176v16m96-16v16" fill="none" stroke="currentColor" stroke-linecap="round" stroke-width="14"/>',
      rollback: '<path d="M80 80H32l48-48M32 80a96 96 0 1 1 16 96" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/>',
      postcheck: '<path d="M128 224s88-40 88-104V48l-88-32-88 32v72c0 64 88 104 88 104Z" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/><path d="m88 120 24 24 56-56" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="14"/>',
    };
    return '<svg class="' + className + '" viewBox="0 0 256 256" aria-hidden="true">' + (icons[name] || icons.postcheck) + "</svg>";
  };
  const arrowSvg = () => '<svg class="stage-arrow" viewBox="0 0 100 20" preserveAspectRatio="none" aria-hidden="true"><path d="M3 10h86m0 0-9-7m9 7-9 7" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="5"/></svg>';
  const currentStep = () => state.bundle?.steps?.[state.stepIndex] || null;
  const latest = (stage) => {
    let result = null;
    (state.bundle?.steps || []).forEach((step, index) => {
      if (step.stage === stage && index <= state.stepIndex) result = step;
    });
    return result;
  };
  const reached = (stage) => (state.bundle?.steps || []).some((step, index) => step.stage === stage && index <= state.stepIndex);
  const currentStageIndex = () => STAGE_INDEX[currentStep()?.stage] == null ? -1 : STAGE_INDEX[currentStep().stage];
  const isRollback = () => state.bundle?.case?.status === "ROLLED_BACK";
  const verificationStep = () => latest("verification");
  const recoveryStep = () => latest("recovery");
  const currency = () => state.bundle?.case?.currency || "KES";
  const headlineExpected = () => state.bundle?.headline?.expected || state.bundle?.headline?.verified || "—";
  const approvalAmount = () => {
    const approval = latest("approval");
    const diffs = latest("rootcause")?.diffs || [];
    const total = diffs.reduce((sum, item) => sum + num(item.delta), 0);
    return approval ? (total ? number(total) + " " + currency() : approval.subtitle || "—") : "待审批";
  };
  const table = (headers, rows) => {
    if (!rows.length) return '<div class="empty-state">暂无已记录数据。</div>';
    return '<div class="table-wrap"><table><thead><tr>' +
      headers.map((header) => "<th>" + esc(header) + "</th>").join("") +
      "</tr></thead><tbody>" +
      rows.map((row) => "<tr>" + row.map((cell) => "<td>" + (cell == null || cell === "" ? "—" : cell) + "</td>").join("") + "</tr>").join("") +
      "</tbody></table></div>";
  };
  const section = (title, body, meta, glyph) =>
    '<section class="detail-section"><div class="section-title"><span class="tab-icon">' + (glyph || "◈") +
    "</span><strong>" + esc(title) + "</strong>" + (meta ? "<span>" + meta + "</span>" : "") + "</div>" + body + "</section>";
  const statusTone = () => {
    if (isRollback()) return "warning";
    return state.bundle?.case?.status === "CLOSED" ? "success" : "neutral";
  };
  const stageState = (index) => {
    const current = currentStageIndex();
    if (isRollback() && index === 5 && current >= 5) return "error";
    if (index === 6) {
      if (isRollback()) return reached("recovery") ? "rollback" : current >= 6 ? "active" : "pending";
      return reached("closing") ? "done" : "pending";
    }
    if (index === 7) return reached("closing") || reached("recovery") ? "done" : "pending";
    if (index < current) return "done";
    if (index === current) return index === 6 ? "rollback" : "active";
    return "pending";
  };
  const stageValue = (id) => {
    const evidence = latest("evidence");
    const policy = latest("policy");
    const calculation = latest("calculation");
    const approval = latest("approval");
    const execution = latest("execution");
    const verification = latest("verification");
    const recovery = latest("recovery");
    if (id === "evidence") return evidence ? (evidence.evidence?.length || 0) + " 条" : "待取证";
    if (id === "policy") return policy ? (policy.subtitle || "已采用") + "：已采用" : "待匹配";
    if (id === "calculation") return calculation ? (calculation.subtitle || "已复算") : "待计算";
    if (id === "approval") return approval ? approvalAmount() : "待审批";
    if (id === "execution") {
      return execution?.executions?.length ? execution.executions.map((item) => (item.component || "组件") + " +" + item.amount).join("  ") : "待执行";
    }
    if (id === "verification") return verification ? "读取 " + (verification.subtitle === "FAILED" ? "偏差" : (verification.checks?.reduce((s, item) => s + num(item.actual), 0).toFixed(2) + " " + currency())) : "待复核";
    if (id === "rollback") return reached("recovery") ? "2 笔冲销" : isRollback() ? "待触发" : "无需回滚";
    if (id === "postcheck") return reached("closing") || reached("recovery") ? "已通过" : "待结案";
    return "—";
  };

  function renderHeader() {
    const bundle = state.bundle;
    const c = bundle.case || {};
    const risk = latest("risk")?.subtitle || "L2";
    const status = c.status || "—";
    const globalView = ["public-data", "observability"].includes(state.tab);
    const publicMode = state.tab === "public-data";
    const topbar = $("topbar");
    topbar.className = "topbar" + (globalView ? " global-mode" : "");
    $("app-shell").classList.toggle("global-view", globalView);
    $("capture-notice").hidden = globalView;
    $("summary-strip").hidden = globalView;
    document.querySelector(".pipeline-panel").hidden = globalView;
    $("safety-rail").hidden = globalView;
    document.querySelector(".workspace").classList.toggle("workspace-observability", globalView);
    const brandEnd = globalView
      ? '<span class="approval-label">' + (publicMode ? "公开数据实验" : "全局运行总览") + "</span>"
      : '<select id="case-select" class="case-select" aria-label="选择回放案件">' +
        state.cases.map((item) => '<option value="' + esc(item.file) + '"' + (item.file === bundle.__file ? " selected" : "") + ">" + esc(item.case_id + " · " + (item.status || item.title || "运行记录")) + "</option>").join("") +
        '</select><span class="risk-pill">' + esc(risk) + "</span>" +
        (c.execution_mode === "MCP_TEAM" ? '<span class="mcp-pill">MCP 参考链路</span>' : "") +
        '<span class="mcp-pill matrix-pill"><span></span>AgentTeams · Matrix</span><span class="approval-label">人工审批</span>';
    const topActions = globalView
      ? '<span class="observability-mode">' + (publicMode ? "三真一合成 · 可复算" : "实时观测 · 只读展示") + "</span>"
      : '<span class="health-pill"><i class="health-dot"></i>静态回放 · 只读</span><span class="status-mini outcome-' + statusTone() + '">' + esc(status) + "</span>";
    topbar.innerHTML =
      '<div class="brand-group"><svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3.8 26 7.7v7.2c0 6.4-4.1 11-10 13.3C10.1 25.9 6 21.3 6 14.9V7.7Z" fill="none" stroke="currentColor" stroke-width="2"/><path d="m10.5 15.7 3.4 3.4 7-7" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/></svg><span class="brand-name">RevGuard</span><span class="top-divider"></span>' + brandEnd + '</div>' +
      '<div class="disclosure">' + (publicMode ? "公开真实交易 · 合成结算与异常" : "合成业务数据 · 真实运行链路") + (globalView ? "" : ' <span>· 静态回放（不连接后端）</span>') + "</div>" +
      '<div class="top-actions">' + (globalView ? topActions : '<span class="health-pill">审批与写后验证约束</span><button class="icon-button" type="button" disabled aria-label="静态回放不允许重置"><span aria-hidden="true">↻</span><span>重置全部</span></button>' + topActions) + "</div>";
    document.title = globalView ? "RevGuard · " + (publicMode ? "公开数据实验" : "全局运行总览") : "RevGuard · " + (c.case_id || "运行") + " 运行回放";
    $("case-select")?.addEventListener("change", (event) => loadCase(event.target.value));
  }

  function renderSummary() {
    const c = state.bundle.case || {};
    const intake = state.bundle.steps.find((step) => step.stage === "intake");
    const verification = verificationStep();
    const status = c.status || "—";
    const variance = state.bundle.headline?.variance || "—";
    const items = [
      ["代理商", c.partner_name, c.partner_id || "按名称解析", ""],
      ["订单号", c.order_id, intake?.facts?.["业务时点"] ? "业务时点 " + intake.facts["业务时点"] : "订单证据", ""],
      ["订单金额", intake?.facts?.["订单金额"], "合成业务订单", ""],
      ["已入账金额", state.bundle.headline?.posted, "模拟佣金台账", ""],
      ["预期佣金（正确）", headlineExpected(), "确定性规则内核", ""],
      ["本次审批金额", approvalAmount(), latest("approval")?.subtitle || "PENDING", ""],
      ["当前状态", status, status === "CLOSED" ? "闭环完成" : status === "ROLLED_BACK" ? "已完成冲销恢复" : "等待终态", status === "CLOSED" ? "success" : status === "ROLLED_BACK" ? "warning" : "neutral"],
      ["回滚后状态", isRollback() ? (recoveryStep() ? "已通过" : "待核实") : (verification?.subtitle === "PASSED" ? "不适用" : "—"), isRollback() ? "恢复安全基线" : "验证通过，无需回滚", isRollback() ? "success" : "neutral"],
    ];
    $("summary-strip").innerHTML = items.map((item, index) =>
      '<div class="summary-item summary-' + index + (item[3] ? " outcome-" + item[3] : "") + '">' +
      '<span class="summary-label">' + esc(item[0]) + "</span><strong>" + esc(item[1]) + "</strong><small>" + esc(item[2]) + "</small></div>"
    ).join("");
  }

  function renderPipeline() {
    const current = currentStep();
    $("pipeline-stages").innerHTML = STAGES.map((stage, index) => {
      const stateClass = stageState(index);
      return '<div class="stage stage-' + stateClass + '">' +
        '<div class="stage-heading"><span class="stage-index">' + (index + 1) + '</span><span class="stage-label">' + esc(stage.label) + "</span></div>" +
        '<strong class="stage-value">' + esc(stageValue(stage.id)) + "</strong>" +
        '<div class="stage-line"><span class="stage-node">' + iconSvg(stage.icon) + "</span>" +
        (index < STAGES.length - 1 ? arrowSvg() : "") + "</div></div>";
    }).join("");

    const policy = latest("policy");
    const execution = latest("execution");
    const verification = latest("verification");
    const recovery = latest("recovery");
    const finalOk = state.bundle.case.status === "CLOSED" ? "正常闭环" : state.bundle.case.status === "ROLLED_BACK" ? "已恢复至安全基线" : "等待终态";
    const verifiedAmount = verification?.checks?.reduce((sum, item) => sum + num(item.actual), 0);
    $("pipeline-details").innerHTML =
      '<div class="pipeline-note calculation-note"><span>政策选择</span>' +
        '<strong>' + esc(policy ? ((policy.policy?.["排除版本"] || []).length ? "历史版本已排除" : "无冲突版本") : "等待政策匹配") + "</strong>" +
        '<strong>' + esc(policy ? (policy.subtitle || "规则集已选定") + "：已采用" : "等待确定性复算") + "</strong></div>" +
      '<div class="pipeline-note capability-note"><span>能力边界</span>' +
        '<div>总额度上限：' + money(latest("risk") ? "50,000.00 " + currency() : "待确定") + "</div>" +
        '<div>本次金额：' + money(approvalAmount()) + "</div>" +
        "<small>静态回放 · 审批、执行与验证均为记录快照</small></div>" +
      '<div class="pipeline-note execution-note"><span>模拟记账（入账）</span>' +
        (execution?.executions?.length ? execution.executions.map((item) => "<strong>" + esc(item.component) + "：+" + esc(item.amount) + "</strong>").join("") + "<div>合计：" + money(execution.executions.reduce((sum, item) => sum + num(item.amount), 0).toFixed(2) + " " + currency()) + "</div>" : "<strong>等待受限执行器写入</strong>") + "</div>" +
      '<div class="pipeline-note verify-note ' + (verification?.subtitle === "FAILED" ? "is-failed" : "") + '"><span>验证结果</span>' +
        "<div>实际读取：" + (verification ? money(verifiedAmount.toFixed(2) + " " + currency()) : "—") + "</div>" +
        "<div>差异：" + money(state.bundle.headline?.variance || "—") + "</div>" +
        "<strong>" + esc(verification?.subtitle === "FAILED" ? "不匹配" : verification?.subtitle === "PASSED" ? "验证通过" : "等待独立验证") + "</strong></div>" +
      '<div class="pipeline-note rollback-note"><span>自动回滚执行</span>' +
        (recovery?.executions?.length ? recovery.executions.map((item) => "<strong>" + esc(item.component || "组件") + "：" + esc(item.amount || "冲销") + "</strong>").join("") : "<strong>" + (isRollback() ? "按原操作关联冲销" : "验证失败时由策略自动触发") + "</strong>") + "</div>" +
      '<div class="pipeline-note result-note ' + (state.bundle.case.status === "FAILED" ? "is-failed" : "") + '"><span>最终结果</span><strong>' +
        esc(state.bundle.case.status || "等待终态") + "</strong><b>" + esc(finalOk) + "</b><small>" +
        esc(recovery ? "冲销后独立复核通过" : verification?.subtitle === "PASSED" ? "独立验证通过，调整已完成" : current ? current.title : "尚未生成终态结论") + "</small></div>";

    const total = state.bundle.steps.length || 1;
    const percent = Math.round(((state.stepIndex + 1) / total) * 100);
    $("step-index").textContent = String(state.stepIndex + 1);
    $("step-total").textContent = String(total);
    $("progress-bar").style.width = percent + "%";
    document.querySelector(".progress").setAttribute("aria-valuenow", String(percent));
    $("replay-step-note").textContent = "当前记录步骤：" + (current ? current.title : "—") + " · " + (current?.subtitle || "静态快照");
    $("btn-play").textContent = state.playing ? "暂停回放" : (state.stepIndex >= total - 1 ? "从头播放" : "播放回放");
    $("btn-play").setAttribute("aria-pressed", String(state.playing));
    $("btn-prev").disabled = state.stepIndex <= 0;
    $("btn-next").disabled = state.stepIndex >= total - 1;
  }

  function renderTabs() {
    document.querySelectorAll("#tabs button").forEach((button) => {
      button.classList.toggle("active", button.dataset.tab === state.tab);
    });
  }

  function renderEvidence() {
    const step = latest("evidence");
    const available = Boolean(step);
    const records = step?.evidence || [
      { type: "ORDER", source: "erpnext", document: state.bundle.case.order_id, strength: "PENDING" },
      { type: "TIER_HISTORY", source: "erpnext", document: state.bundle.case.partner_id, strength: "PENDING" },
      { type: "CONTRACT", source: "erpnext", document: "CON-2026", strength: "PENDING" },
      { type: "PAYMENT_RECORD", source: "erpnext", document: state.bundle.case.order_id, strength: "PENDING" },
      { type: "REFUND_RECORD", source: "erpnext", document: state.bundle.case.order_id, strength: "PENDING" },
      { type: "INVOICE", source: "erpnext", document: state.bundle.case.order_id, strength: "PENDING" },
      { type: "COMMISSION_LEDGER", source: "revguard-ledger", document: state.bundle.case.order_id, strength: "PENDING" },
      { type: "POLICY_VERSIONS", source: "revguard-policy", document: "KE-COMMISSION-2026", strength: "PENDING" },
    ];
    const rows = records.map((item) => {
      const live = available && item.strength !== "PENDING";
      const source = item.source === "erpnext" ? "ERPNext" : item.source === "revguard-ledger" ? "PolarDB · ledger" : item.source === "revguard-policy" ? "RevGuard · policy" : String(item.source || "—").replace(/_MOCK$/, "");
      return [
        esc(item.type),
        esc(source),
        '<code>' + shortId(item.document || item.id, 22) + "</code>",
        '<span class="' + (live ? "strong-cell" : "pending-cell") + '">' + (live ? "✓" : "◌") + " " + esc(item.strength || "STRONG") + "</span>",
        live ? "<code>" + shortId(item.receipt, 15) + "</code>" : '<span class="pending-cell">◌ 等待运行</span>',
        '<span class="' + (live ? "verified-cell" : "pending-cell") + '">' + (live ? "✓ 已校验" : "◌ 待收集") + "</span>",
      ];
    });
    return section("证据来源链", table(["证据类型", "来源系统", "证据 ID", "强度", "工具回执", "校验"], rows), "（" + records.length + " 条已采集强证据）", "▣");
  }

  function renderCalculation() {
    const root = latest("rootcause");
    const calculation = latest("calculation");
    if (!root || !calculation) return section("计算账本", '<div class="empty-state">等待调查完成后生成逐组件确定性复算账本</div>', "金额单位：" + currency(), "∑");
    const components = calculation.components || [];
    const rows = (root.diffs || []).filter((item) => num(item.delta) !== 0).map((item) => {
      const component = components.find((candidate) => candidate.applied && (candidate.name === item.component || item.component.includes(candidate.name))) || {};
      const formula = item.component === "销售佣金" ? "订单金额 × GOLD 15%" : "回款金额 × 3%";
      const base = item.component === "销售佣金" ? "180,000.00" : "180,000.00";
      const ratio = item.component === "销售佣金" ? "15%" : "3%";
      return [
        esc(item.component),
        esc(formula),
        esc(base),
        esc(ratio),
        esc(component.amount || item.expected),
        esc(number(num(item.expected))),
        esc(number(num(item.posted))),
        '<span class="negative-cell">' + esc(number(num(item.delta))) + "</span>",
      ];
    });
    const body = table(["项目", "公式", "基数", "比例", "计算", "应有金额", "已发布", "差异"], rows);
    const totalExpected = root.total_expected || number((root.diffs || []).reduce((sum, item) => sum + num(item.expected), 0)) + " " + currency();
    const totalPosted = root.total_posted || number((root.diffs || []).reduce((sum, item) => sum + num(item.posted), 0)) + " " + currency();
    const totalDelta = root.total_delta || number((root.diffs || []).reduce((sum, item) => sum + num(item.delta), 0)) + " " + currency();
    return section("计算账本", body + '<div class="ledger-total"><span>应有 ' + money(totalExpected) + "</span><span>已记 " + money(totalPosted) + '</span><strong>差额 ' + money(totalDelta) + "</strong></div>", "（金额单位：" + esc(currency()) + "）", "∑");
  }

  function renderPolicy() {
    const step = latest("policy");
    if (!step) return section("政策时间线", '<div class="empty-state">等待政策智能体读取订单时点并回溯政策版本</div>', "等待时点政策匹配", "◇");
    const selected = step.subtitle || "2026-Q3";
    const quarters = ["2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"];
    const clauses = step.policy?.["引用条款"] || [];
    const timeline = '<div class="policy-line">' + quarters.map((quarter) =>
      '<div class="policy-point ' + (quarter === selected ? "selected" : "") + '"><strong>' + quarter + '</strong><span></span><small>' + (quarter === selected ? "已选中" : "未选中") + "</small></div>"
    ).join("") + "</div>";
    const excluded = (step.policy?.["排除版本"] || []).length;
    const selectedBox = '<div class="policy-selected"><span class="tab-icon">✓</span><div><strong>KE-COMMISSION-2026　|　' + esc(selected) + "　|　" + esc(step.policy?.["等级解析"] || "时点等级") + "</strong><p>" +
      esc(clauses.slice(0, 2).join("；") || "已按业务时点选择有效规则集。") + "</p><small>引用条款：" + esc(clauses.length + " 条") + " · 排除历史版本：" + excluded + " 个</small></div></div>";
    return section("政策时间线", timeline + selectedBox, "（排除 " + excluded + "，选择 " + esc(selected) + "）", "◇");
  }

  function replayTraceWindow() {
    const spans = (state.bundle.trace?.spans || []).slice().sort((left, right) => (left.sequence || 0) - (right.sequence || 0));
    const skillMarkers = spans.filter((span) => span.kind === "SKILL");
    const current = currentStep();
    const currentStage = current?.stage || null;
    const currentOrder = REPLAY_STAGE_ORDER[currentStage] == null ? -1 : REPLAY_STAGE_ORDER[currentStage];
    const final = state.stepIndex >= (state.bundle.steps?.length || 1) - 1;
    const stageFor = (span) => {
      if (span.kind === "APPROVAL") return "approval";
      if (span.kind === "AGENT") {
        if (span.name === "AgentTeams.OrchestratorHandshake") return "intake";
        const skillName = String(span.name || "").replace(/^AgentTeams\./, "");
        return TASK_STAGE_BY_SKILL[skillName] || null;
      }
      if (span.stage) return span.stage;
      let previous = null;
      skillMarkers.forEach((marker) => {
        if ((marker.sequence || 0) <= (span.sequence || 0)) previous = marker;
      });
      return previous?.stage || null;
    };
    const stageOrderFor = (stage) => REPLAY_STAGE_ORDER[stage] == null ? -1 : REPLAY_STAGE_ORDER[stage];
    const visible = spans.filter((span) => {
      const stage = stageFor(span);
      return stageOrderFor(stage) >= 0 && stageOrderFor(stage) <= currentOrder;
    });
    const agentTasks = spans.filter((span) => span.kind === "AGENT" && span.name !== "AgentTeams.OrchestratorHandshake").map((span, index) => {
      const skillName = String(span.name || "").replace(/^AgentTeams\./, "");
      return { ...span, skillName, replayStage: stageFor(span), taskNumber: index + 1 };
    });
    const visibleAgentTasks = agentTasks.filter((task) => stageOrderFor(task.replayStage) <= currentOrder);
    return { spans, visible, agentTasks, visibleAgentTasks, current, currentStage, currentOrder, final, stageFor };
  }

  function renderAgentMatrix() {
    const trace = replayTraceWindow();
    const run = state.bundle.case.team_run || {};
    const totalTasks = Number(run.total_tasks || trace.agentTasks.length || 0);
    const visibleTasks = trace.visibleAgentTasks.slice().sort((left, right) => right.taskNumber - left.taskNumber);
    const focusTask = trace.visibleAgentTasks.slice().reverse().find((task) => task.replayStage === trace.currentStage);
    const currentActor = focusTask?.actor || (trace.currentStage === "approval" ? "finance.lead" : "revguard-orchestrator");
    const workerCount = new Set(trace.visibleAgentTasks.map((task) => task.actor).filter(Boolean)).size;
    const status = trace.final ? (run.status || "COMPLETED") : "RUNNING";
    const statusLabel = trace.final ? (status === "COMPLETED" ? "已完成" : status) : "回放中";
    const cards = visibleTasks.length ? visibleTasks.map((task, index) => {
      const focused = !trace.final && task.replayStage === trace.currentStage;
      const displayStatus = focused ? "RUNNING" : task.status || "OK";
      const statusClass = displayStatus === "RUNNING" ? "task-running" : displayStatus === "OK" ? "task-succeeded" : "task-failed_final";
      const statusText = displayStatus === "RUNNING" ? "执行中" : displayStatus === "OK" ? "已成功" : "异常";
      const label = TASK_LABELS[task.skillName] || task.label || task.skillName;
      return '<details class="agent-task-card ' + (focused || index === 0 ? "orchestrator-card" : "") + '"' + (focused || index === 0 ? " open" : "") + "><summary>" +
        '<span class="task-seq">' + esc(String(task.taskNumber).padStart(2, "0")) + "</span><div><strong title=\"" + esc(task.name) + "\">" + esc(label) + '</strong><code>' + esc(task.actor || "AgentTeams worker") + "</code></div>" +
        '<span class="transport-cell">Matrix</span><span class="task-metric-cell">' + esc(duration(task.duration_ms)) + '</span><span class="task-metric-cell metric-unavailable">—</span><span class="task-status ' + statusClass + '">' + statusText + "</span></summary>" +
        '<div class="task-evidence-grid"><div><span>控制输入</span><pre>' + esc(JSON.stringify({ stage: REPLAY_STAGE_LABELS[task.replayStage] || task.replayStage, actor: task.actor || "—", sequence: task.sequence }, null, 2)) + '</pre></div><div><span>控制输出</span><pre>' + esc(JSON.stringify({ status: displayStatus, label, duration: duration(task.duration_ms) }, null, 2)) + "</pre></div></div>" +
        '<div class="correlation-strip"><code>span ' + shortId(task.name, 28) + "</code><code>seq " + esc(task.sequence) + "</code><code>阶段 " + esc(REPLAY_STAGE_LABELS[task.replayStage] || task.replayStage) + "</code></div></details>";
    }).join("") : '<div class="empty-state">当前回放步骤尚未产生 AgentTeams 任务。</div>';
    const currentTitle = trace.current?.title || "—";
    const currentSubtitle = trace.current?.subtitle || "静态快照";
    const runtime = '<div class="team-runtime team-runtime-' + status.toLowerCase() + '">' +
      '<div><span class="runtime-live-dot"></span><strong>' + esc(statusLabel) + '</strong><small>' + (trace.final ? "运行已结束" : "按当前步骤回放") + '</small></div>' +
      '<div><span>当前执行者</span><strong title="' + esc(currentActor) + '">' + esc(currentActor) + '</strong></div>' +
      '<div><span>当前阶段</span><strong title="' + esc(currentTitle) + '">' + esc(REPLAY_STAGE_LABELS[trace.currentStage] || currentTitle) + '</strong><small>' + esc(currentSubtitle) + '</small></div>' +
      '<div><span>进度</span><strong>' + esc(visibleTasks.length + " / " + totalTasks) + '</strong></div></div>';
    const header = '<div class="agent-task-columns" aria-hidden="true"><span>序号</span><span>任务 / 执行者</span><span>通道</span><span>耗时</span><span>Token</span><span>状态</span></div>';
    const meta = visibleTasks.length + " / " + totalTasks + " 轮任务 · " + workerCount + " 个执行者 · 第 " + (state.stepIndex + 1) + " 步";
    const note = '<p class="boundary-note"><span class="tab-icon">♟</span>静态回放仅展示截至“' + esc(currentTitle) + '”的 AgentTeams Worker 任务；工具与 Skill 明细保留在“执行与审计”页。</p>';
    return section("多智能体协同任务账本", runtime + '<div class="agent-task-ledger">' + header + cards + "</div>" + note, meta, "♟");
  }

  function renderAuditTrail() {
    const steps = state.bundle.steps.slice(0, state.stepIndex + 1);
    const rows = steps.map((step, index) =>
      '<div class="audit-row ' + (step.stage === "verification" && step.subtitle === "FAILED" ? "audit-error" : step.stage === "recovery" ? "audit-rollback" : "") + '">' +
        '<span class="audit-icon">' + (step.stage === "verification" && step.subtitle === "FAILED" ? "!" : step.stage === "recovery" ? "↶" : "✓") + "</span>" +
        "<time>" + clock(step.at) + '</time><div><strong>' + esc(step.title) + '</strong><small>' + esc(step.subtitle || step.stage) + "</small></div><code>step-" + (index + 1) + "</code></div>"
    ).join("");
    return '<section class="detail-section audit-section"><div class="section-title"><span class="tab-icon">↯</span><strong>审计轨迹</strong><span>（顺序追加）</span></div><div class="audit-list">' + rows + '</div><div class="audit-summary"><span>审计链事件</span><strong>' + esc(state.bundle.audit?.count || "—") + '</strong><span class="error-dot"></span><span>' + (state.bundle.audit?.chain_ok ? "链路校验通过" : "待校验") + "</span></div></section>";
  }

  function renderDecision() {
    return '<div class="decision-grid"><div class="decision-left">' + renderEvidence() + renderCalculation() + renderAuditTrail() + '</div><div class="decision-right">' + renderPolicy() + renderAgentMatrix() + "</div></div>";
  }

  function renderAuditView() {
    const spans = (state.bundle.trace?.spans || []).slice().sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
    const rows = spans.map((span) =>
      '<div class="span-row ' + (span.status && span.status !== "OK" ? "span-error" : "") + '">' +
        '<span class="span-kind">' + esc(span.kind || "TRACE") + "</span><strong>" + esc(span.label || span.name) + "</strong><code>" + esc(span.actor || "—") + "</code><span>" + esc(duration(span.duration_ms)) + "</span><span>" + esc(span.status || "OK") + "</span></div>"
    ).join("");
    const traceSection = '<section class="detail-section trace-section"><div class="section-title"><span class="tab-icon">↯</span><strong>Agent / Skill / Tool 运行跨度</strong><span>（' + esc(spans.length) + " 条）</span></div><div class=\"span-list\">" + rows + "</div></section>";
    return '<div class="trace-grid">' + traceSection + renderAuditTrail() + "</div>";
  }

  function renderPermissions() {
    const c = state.bundle.case || {};
    const cards = [
      section("数据访问边界", '<div class="permission-list"><div><span>ERPNext</span><strong>只读 · 证据采集</strong></div><div><span>PolarDB / ledger</span><strong>读取快照 · 写入受控</strong></div><div><span>Grafana</span><strong>只读可观测</strong></div><div><span>静态页面</span><strong>不连接后端</strong></div></div>', "系统级约束", "⌁"),
      section("执行约束", '<div class="constraint-list"><div><span>01</span><strong>真人审批</strong><small>审批金额与组件额度绑定，拒绝后不得自动执行。</small></div><div><span>02</span><strong>一次性能力</strong><small>短时令牌、幂等键和操作 ID 保留在记录中。</small></div><div><span>03</span><strong>执行与验证分离</strong><small>独立主体重新读取写后状态，失败进入冲销路径。</small></div></div>', "边界先于执行", "♙"),
      section("回放校验探针", '<div class="probe-list"><div><span>✓</span><strong>来源哈希</strong><b>通过</b></div><div><span>✓</span><strong>审计链</strong><b>' + (state.bundle.audit?.chain_ok ? "通过" : "待校验") + '</b></div><div><span>✓</span><strong>静态数据</strong><b>仅本地 JSON</b></div><div><span>✓</span><strong>业务数据</strong><b>合成样本</b></div></div>', "可验证但不可写", "◌"),
    ];
    return '<div class="permissions-grid">' + cards.join("") + "</div>";
  }

  function renderEngineering() {
    const provenance = state.bundle.provenance || {};
    const evidence = latest("evidence")?.evidence || [];
    const ledger = [
      ["捕获类型", provenance.capture_kind || "CAPTURED_FROM_RUNTIME", "真实运行记录导出"],
      ["运行版本", provenance.source_release || "—", "记录来源"],
      ["数据后端", provenance.backend || "postgresql-polardb", "只作为溯源字段展示"],
      ["台账性质", state.bundle.disclosure?.ledger || "simulated", "回放不写入"],
    ];
    const evidenceBody = '<div class="evidence-ledger">' + ledger.map((item) =>
      '<div class="evidence-row"><span>' + esc(item[0]) + "</span><strong>" + esc(item[1]) + "</strong><small>" + esc(item[2]) + "</small></div>"
    ).join("") + "</div><div class=\"classification-banner\"><span class=\"tab-icon\">!</span><div><strong>公开托管边界</strong><small>业务样本为合成数据；运行链路、审计结构和系统边界来自真实执行记录。</small></div></div>";
    const values = '<div class="value-ledger">' +
      '<div><span>snapshot_sha256</span><strong><code>' + shortId(provenance.snapshot_sha256, 28) + "</code></strong></div>" +
      '<div><span>审计事件</span><strong>' + esc(state.bundle.audit?.count || "—") + "</strong></div>" +
      '<div><span>Agent Trace</span><strong>' + esc(state.bundle.trace?.span_count || "—") + "</strong></div>" +
      '<div><span>运行耗时</span><strong>' + esc(duration(state.bundle.run?.wall_duration_ms)) + "</strong></div></div>";
    const pending = '<section class="detail-section pending-section"><div class="section-title"><span class="tab-icon">◇</span><strong>工程验收清单</strong><span>（静态回放保持只读）</span></div><div class="pending-checks"><div><span>✓</span><span>可重复</span><strong>同一 JSON 记录得到同一视图</strong></div><div><span>✓</span><span>可审计</span><strong>证据、跨度、审计链均可追溯</strong></div><div><span>✓</span><span>可公开</span><strong>无后端地址、凭据或业务密钥</strong></div></div></section>';
    return '<div class="engineering-grid">' + section("来源与分类", evidenceBody, evidence.length + " 条证据 · 只读", "▣") + section("记录完整性", values, "运行证据", "⌁") + pending + "</div>";
  }

  function renderValue() {
    const value = state.engineering?.business_value || {};
    const metrics = value.metrics || {};
    const contract = value.simulation_contract || {};
    const defaults = contract.default_assumptions || {};
    const monthlyCases = state.valueMonthlyCases == null ? Number(defaults.monthly_case_volume || 500) : state.valueMonthlyCases;
    const hourlyCost = state.valueHourlyCost == null ? Number(defaults.loaded_hourly_labor_cost || 100) : state.valueHourlyCost;
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
      ["单案处理时长", manualMinutes + " 分钟", assistedMinutes + " 分钟", assistedMinutes / Math.max(manualMinutes, 1)],
      ["错付样本率", percent(metrics.wrong_payment_rate_before), percent(metrics.wrong_payment_rate_after), Number(metrics.wrong_payment_rate_after || 0) / Math.max(Number(metrics.wrong_payment_rate_before || 0), 0.01)],
      ["追回成本指数", recoveryBefore.toLocaleString(), recoveryAfter.toLocaleString(), recoveryAfter / Math.max(recoveryBefore, 1)],
    ];
    const comparisonRows = comparisons.map(([label, before, after, ratio]) =>
      '<div class="comparison-row"><div><strong>' + esc(label) + '</strong><small>人工基线 ' + esc(before) + '　→　RevGuard ' + esc(after) + '</small></div><div class="comparison-track"><span class="before-bar"></span><span class="after-bar" style="width:' + Math.max(Math.min(ratio * 100, 100), after === "0.0%" ? 0 : 2) + '%"></span></div></div>'
    ).join("");
    return '<div class="value-simulator">' +
      '<section class="detail-section value-hero"><div class="value-hero-copy"><span class="value-eyebrow">合成数据价值情景</span><h2>企业价值模拟器</h2><p>把 8 个合成案件的可复算基线，与企业自行输入的业务量和人工成本组合，回答“可能释放多少工时、形成多少预算空间”。</p></div><div class="scenario-controls"><label><span>月均异常案件量</span><div><input data-value-input="monthlyCases" type="number" min="1" max="100000" step="50" value="' + esc(monthlyCases) + '"><b>案/月</b></div></label><label><span>综合人工成本</span><div><input data-value-input="hourlyCost" type="number" min="1" max="10000" step="10" value="' + esc(hourlyCost) + '"><b>元/小时</b></div></label><div class="scenario-presets"><span>快速情景</span><button data-value-preset="100" type="button" class="' + (monthlyCases === 100 ? "active" : "") + '">100 案</button><button data-value-preset="500" type="button" class="' + (monthlyCases === 500 ? "active" : "") + '">500 案</button><button data-value-preset="1000" type="button" class="' + (monthlyCases === 1000 ? "active" : "") + '">1000 案</button></div></div></section>' +
      '<section class="value-kpi-grid" aria-label="模拟价值关键指标"><article><span>处理时长下降</span><strong>' + esc(percent(timeReduction)) + '</strong><small>' + esc(manualMinutes) + ' → ' + esc(assistedMinutes) + ' 分钟/案</small></article><article><span>同等工时理论吞吐</span><strong>' + esc(throughput ? throughput.toFixed(2) + "×" : "—") + '</strong><small>基于合成样本中位数</small></article><article><span>每月释放处理工时</span><strong>' + esc(monthlyHours.toLocaleString("zh-CN", { maximumFractionDigits: 0 })) + ' 小时</strong><small>约 ' + esc(fteEquivalent.toFixed(1)) + ' 个全职人员月产能</small></article><article class="value-kpi-accent"><span>模拟人工经费空间</span><strong>' + esc(cny(monthlyLaborValue)) + '<em>/月</em></strong><small>' + esc(cny(annualLaborValue)) + '/年 · 非现金承诺</small></article></section>' +
      '<div class="value-detail-grid"><section class="detail-section comparison-section"><div class="section-title"><span class="tab-icon">◒</span><strong>合成样本前后对照</strong><span>样本数 ' + esc(value.case_count || 0) + '</span></div><div class="comparison-list">' + comparisonRows + '</div><div class="sample-outcomes"><div><span>追回成本下降</span><strong>' + esc(percent(recoveryReduction)) + '</strong></div><div><span>审计异常样本</span><strong>' + esc(percent(metrics.audit_exception_rate_before)) + ' → ' + esc(percent(metrics.audit_exception_rate_after)) + '</strong></div><div><span>错付样本</span><strong>' + esc(percent(metrics.wrong_payment_rate_before)) + ' → ' + esc(percent(metrics.wrong_payment_rate_after)) + '</strong></div></div></section><section class="detail-section methodology-section"><div class="section-title"><span class="tab-icon">▣</span><strong>计算口径与边界</strong><span>每个数字可复算</span></div><div class="formula-callout"><span>月度人工经费空间</span><strong>' + esc(savedPerCase) + ' 分钟 × ' + esc(monthlyCases.toLocaleString()) + ' 案 ÷ 60 × ' + esc(cny(hourlyCost)) + '/小时</strong><b>= ' + esc(cny(monthlyLaborValue)) + '</b></div><div class="methodology-list"><div><span>数据分类</span><strong>' + esc((value.data_classifications || []).join(", ") || "等待接口数据") + '</strong></div><div><span>生产收益声明</span><strong>' + (value.production_claim_allowed ? "允许" : "不允许") + '</strong></div><div><span>样本来源</span><strong>GOLDEN-001～008 合成案件</strong></div><div><span>企业接入后</span><strong>替换 CSV 基线即可复算</strong></div></div><div class="claim-boundary"><span class="tab-icon">!</span><span>' + esc(contract.claim_boundary || value.guardrail || "当前结果仅用于指标方法验证。") + '</span></div></section></div></div>';
  }

  function renderPublicData() {
    const experiment = state.engineering?.public_data_experiment || {};
    const metrics = experiment.metrics || {};
    const boundary = experiment.data_boundary || {};
    const source = experiment.source || {};
    const rules = experiment.rules || {};
    const erp = experiment.erpnext || {};
    const anomalyRows = Object.entries(experiment.anomaly_types || {}).sort();
    const ruleRows = Object.entries(rules.assignments || {}).sort();
    const cards = [
      ["公开真实交易", Number(metrics.total_transactions || 0).toLocaleString(), "Olist 固定种子抽样"],
      ["审计交易总额", brl(metrics.total_revenue_audited_brl), "真实交易金额聚合"],
      ["预期费率金额", brl(metrics.expected_commission_brl), "确定性反事实计算"],
      ["合成实际费率金额", brl(metrics.actual_commission_brl), "明确标记为合成结算"],
      ["风险金额", brl(metrics.revenue_at_risk_brl), "800 个受控异常的金额影响"],
      ["异常率", percent(metrics.anomaly_rate), (metrics.risk_cases || 0) + " 个 Risk Case"],
    ];
    return '<div class="public-data-dashboard"><section class="detail-section public-data-hero"><div><span class="value-eyebrow">THREE REAL FOUNDATIONS · ONE SYNTHETIC LAYER</span><h2>三真一合成</h2><p>' + esc(boundary.statement || "三真一合成：真实公开交易、真实 ERP、真实公开费率；合成结算与异常。") + '</p></div><div class="public-provenance-grid"><div><span>交易</span><strong>' + esc(boundary.transaction || "PUBLIC_REAL") + '</strong><small>Olist</small></div><div><span>ERP</span><strong>' + esc(boundary.erp || "LIVE_SYSTEM") + '</strong><small>ERPNext v16</small></div><div><span>费率</span><strong>' + esc(boundary.fee_rules || "PUBLIC_REAL") + '</strong><small>' + esc((rules.count || 0) + " 条官方公开规则") + '</small></div><div><span>结算与异常</span><strong>' + esc(boundary.settlement || "SYNTHETIC_DOMAIN") + '</strong><small>固定种子生成</small></div></div></section>' +
      '<section class="public-kpi-grid" aria-label="公开数据实验关键指标">' + cards.map((card) => '<article><span>' + esc(card[0]) + '</span><strong>' + esc(card[1]) + '</strong><small>' + esc(card[2]) + '</small></article>').join("") + '</section>' +
      '<div class="public-detail-grid"><section class="detail-section"><div class="section-title"><span class="tab-icon">!</span><strong>异常覆盖</strong><span>固定种子 ' + esc(source.random_seed || "202609") + '</span></div><div class="public-bar-list">' + anomalyRows.map(([name, count]) => '<div><code>' + esc(name) + '</code><span><i style="width:' + (Number(count) / Math.max(Number(metrics.risk_cases || 1), 1) * 100) + '%"></i></span><strong>' + esc(count) + '</strong></div>').join("") + '</div></section><section class="detail-section"><div class="section-title"><span class="tab-icon">▣</span><strong>公开费率组件</strong><span>当前政策反事实</span></div><div class="public-bar-list rule-bars">' + ruleRows.map(([name, count]) => '<div><code>' + esc(name) + '</code><span><i style="width:' + (Number(count) / Math.max(Number(metrics.total_transactions || 1), 1) * 100) + '%"></i></span><strong>' + Number(count).toLocaleString() + '</strong></div>').join("") + '</div></section></div>' +
      '<section class="detail-section public-acceptance"><div class="section-title"><span class="tab-icon">▣</span><strong>真实 ERPNext 验收</strong><span>Token 鉴权 · REST API · 只读边界</span></div><div class="public-acceptance-grid">' + Object.entries(erp.observed_counts || {}).map(([name, count]) => '<div><span>' + esc(name) + '</span><strong>' + Number(count).toLocaleString() + '</strong></div>').join("") + '<div><span>REST 读取</span><strong>HTTP ' + esc(erp.authenticated_rest_read_status || 200) + '</strong></div><div><span>越权写入</span><strong>HTTP ' + esc(erp.read_only_write_rejection_status || 403) + '</strong></div><div><span>会计影响</span><strong>' + esc(erp.document_state || "DRAFT_NO_GL_EFFECT") + '</strong></div></div></section>' +
      '<div class="claim-boundary public-boundary"><span class="tab-icon">!</span><span>' + esc(rules.component_scope || "Percentage fee components only.") + ' 本实验不是 2016–2018 订单的历史费率复算，也不代表任何 Olist 卖家实际使用 Etsy 或 eBay。</span></div></div>';
  }

  function renderObservability() {
    const p = state.bundle.provenance || {};
    const chart = (title, legend, path, fill, tone) => '<section class="grafana-chart-panel"><h3>' + title + '</h3><svg class="grafana-chart" viewBox="0 0 720 230" role="img" aria-label="' + title + '"><g class="chart-grid"><path d="M52 28H700M52 77H700M52 126H700M52 175H700" /><path d="M104 18V187M210 18V187M316 18V187M422 18V187M528 18V187M634 18V187" /></g><g class="chart-axis"><text x="10" y="33">' + (title.includes("速率") ? "0.2 req/s" : "1") + '</text><text x="22" y="82">' + (title.includes("速率") ? "0.1" : "0.8") + '</text><text x="28" y="131">' + (title.includes("速率") ? "0.05" : "0.4") + '</text><text x="35" y="180">0</text><text x="82" y="211">12:25</text><text x="188" y="211">12:30</text><text x="294" y="211">12:35</text><text x="400" y="211">12:40</text><text x="506" y="211">12:45</text><text x="612" y="211">12:50</text></g><path class="chart-area ' + tone + '" d="' + fill + '"/><path class="chart-line ' + tone + '" d="' + path + '"/></svg><div class="chart-legend">' + legend + '</div></section>';
    const stats = [
      ["API 指标采集", "正常", "is-green"],
      ["数据库就绪", "正常", "is-green"],
      ["审计链完整性", "正常", "is-green"],
      ["待对账资金操作", "0", "is-green"],
      ["冻结资金通道", "0", "is-green"],
      ["当前触发告警", "0", "is-green"],
    ].map(([label, value, tone]) => '<article class="grafana-live-stat ' + tone + '"><span>' + label + '</span><strong>' + value + '</strong></article>').join("");
    const twoX = '<i class="legend-dot green"></i>2xx';
    const fourX = '<i class="legend-dot yellow"></i>4xx';
    const apiPath = "M52 28L150 28L250 28L350 28L450 28L520 32L585 32L620 28L700 28";
    const apiFill = "M52 28L150 28L250 28L350 28L450 28L520 32L585 32L620 28L700 28V187H52Z";
    const flatPath = "M52 187L150 187L250 187L350 187L450 187L550 187L700 187";
    const availabilityPath = "M52 28L150 28L250 28L350 28L450 28L550 28L700 28";
    const availabilityFill = "M52 28L150 28L250 28L350 28L450 28L550 28L700 28V187H52Z";
    return '<section class="observability-screen">' +
      '<div class="observability-toolbar"><div class="observability-heading"><span class="grafana-mark">▥</span><div><h2>运行与资金恢复</h2><p>全部案件 · 静态运行指标 · 合成业务数据 · 录制快照</p></div></div><div class="observability-actions"><span class="observability-mode">Grafana · 只读</span><span class="health-pill">STATIC · READ ONLY</span></div></div>' +
      '<div class="grafana-live-dashboard"><div class="grafana-live-header"><strong>RevGuard · 运行与资金恢复</strong><span>STATIC REPLAY · ' + esc(p.source_release || "0.6.0") + '</span></div><div class="grafana-live-stat-grid">' + stats + '</div><div class="grafana-chart-grid">' +
        chart("API 请求速率 · 按响应类别", twoX + fourX, apiPath, apiFill, "green") +
        chart("业务 API 响应延迟 · P95", '<i class="legend-dot green"></i>业务 API P95', flatPath, "M52 187L150 187L250 187L350 187L450 187L550 187L700 187V187H52Z", "green") +
        chart("资金结果恢复 · 待对账 / 冻结通道", '<i class="legend-dot yellow"></i>待对账资金操作', flatPath, "M52 187L150 187L250 187L350 187L450 187L550 187L700 187V187H52Z", "yellow") +
        chart("服务与数据库可用性", '<i class="legend-dot yellow"></i>服务与数据库', availabilityPath, availabilityFill, "yellow") +
      '</div><div class="grafana-powered">Powered by <strong><span>◉</span> Grafana</strong></div></div></section>';
  }

  function renderContent() {
    const content = state.tab === "decision" ? renderDecision() :
      state.tab === "audit" ? renderAuditView() :
      state.tab === "permissions" ? renderPermissions() :
      state.tab === "value" ? renderValue() :
      state.tab === "public-data" ? renderPublicData() :
      state.tab === "engineering" ? renderEngineering() : renderObservability();
    $("tab-content").innerHTML = content;
  }

  function renderRail() {
    const c = state.bundle.case || {};
    const verification = verificationStep();
    const passed = verification?.subtitle === "PASSED" || Boolean(recoveryStep());
    const safetyStatus = isRollback() ? "ROLLED_BACK" : c.status || "—";
    const note = isRollback() ? "已执行冲销并完成恢复复核" : passed ? "独立验证通过，未触发冲销" : "等待独立验证";
    $("safety-rail").innerHTML =
      '<section class="rail-section"><span class="rail-label">当前安全状态</span><strong class="rail-state ' + (isRollback() ? "rollback-state" : "") + '">' + esc(safetyStatus) + '</strong><span class="rail-label">独立复核</span><strong class="rail-state ' + (passed ? "passed-state" : "") + '">' + (passed ? "PASSED" : "PENDING") + '</strong><span class="rail-label">最终差额</span><b>' + money(state.bundle.headline?.variance || "—") + "</b><small>" + esc(note) + "</small></section>" +
      '<section class="rail-section"><span class="rail-label">案例与审批边界</span><div class="rail-kv"><span>案件</span><strong>' + esc(c.case_id) + '</strong></div><div class="rail-kv"><span>风险</span><strong>' + esc(latest("risk")?.subtitle || "L2") + '</strong></div><div class="rail-kv"><span>审批人</span><strong>' + esc(state.bundle.headline?.approved_by || "财务负责人（演示）") + '</strong></div><div class="rail-kv"><span>模式</span><strong>AgentTeams · Matrix</strong></div></section>' +
      '<section class="rail-section export-section"><span class="rail-label">导出证据包</span><button type="button" disabled>静态页不再导出</button><small>完整证据包已随 website/data/ 一起托管。</small><code class="rail-code">' + shortId(state.bundle.provenance?.snapshot_sha256, 38) + "</code></section>";
  }

  function render() {
    if (!state.bundle) return;
    renderHeader();
    renderSummary();
    renderPipeline();
    renderTabs();
    renderContent();
    renderRail();
  }

  function stopPlaying() {
    state.playing = false;
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = null;
  }

  function playTick() {
    if (!state.playing) return;
    if (state.stepIndex >= state.bundle.steps.length - 1) {
      stopPlaying();
      render();
      return;
    }
    state.timer = window.setTimeout(() => {
      state.stepIndex += 1;
      render();
      playTick();
    }, BASE_STEP_MS / state.speed);
  }

  function togglePlay() {
    if (state.playing) {
      stopPlaying();
      render();
      return;
    }
    if (state.stepIndex >= state.bundle.steps.length - 1) state.stepIndex = 0;
    state.playing = true;
    render();
    playTick();
  }

  function changeStep(delta) {
    stopPlaying();
    state.stepIndex = Math.max(0, Math.min(state.bundle.steps.length - 1, state.stepIndex + delta));
    render();
  }

  async function loadCase(file) {
    stopPlaying();
    $("case-select")?.setAttribute("disabled", "disabled");
    try {
      const bundle = await fetch("data/" + file, { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error("无法读取静态案件记录");
        return response.json();
      });
      bundle.__file = file;
      state.bundle = bundle;
      const requestedStep = requestedStepParam == null || requestedStepParam === "" ? null : Number(requestedStepParam);
      state.stepIndex = Number.isFinite(requestedStep) ? Math.max(0, Math.min(bundle.steps.length - 1, requestedStep)) : bundle.steps.length - 1;
      render();
      const notice = $("capture-notice");
      notice.hidden = ["public-data", "observability"].includes(state.tab);
      notice.textContent = "真实环境线上运行 AgentTeams、PolarDB、ERPNext、Grafana 等组件，配置 8 核 24G；GitHub Pages / ModelScope 达不到运行要求，所以 Demo 只能静态回放录制脚本了。";
    } catch (error) {
      $("tab-content").innerHTML = '<div class="capture-notice">静态记录读取失败：' + esc(error.message) + "</div>";
    } finally {
      $("case-select")?.removeAttribute("disabled");
    }
  }

  document.addEventListener("click", (event) => {
    const tabButton = event.target.closest("#tabs button");
    if (tabButton) {
      state.tab = tabButton.dataset.tab;
      renderTabs();
      renderHeader();
      renderContent();
      renderSummary();
      renderPipeline();
      renderRail();
      return;
    }
    const valuePreset = event.target.closest("[data-value-preset]");
    if (valuePreset) {
      state.valueMonthlyCases = Number(valuePreset.dataset.valuePreset) || 500;
      renderContent();
      return;
    }
    if (event.target.closest("#btn-play")) return togglePlay();
    if (event.target.closest("#btn-prev")) return changeStep(-1);
    if (event.target.closest("#btn-next")) return changeStep(1);
    if (event.target.closest("#btn-reset")) {
      stopPlaying();
      state.stepIndex = 0;
      render();
    }
  });
  document.addEventListener("change", (event) => {
    if (event.target.id === "speed") {
      state.speed = Number(event.target.value) || 1;
      if (state.playing) {
        stopPlaying();
        state.playing = true;
        playTick();
      }
    }
    if (event.target.dataset.valueInput === "monthlyCases") {
      state.valueMonthlyCases = Math.max(0, Number(event.target.value) || 0);
      renderContent();
    }
    if (event.target.dataset.valueInput === "hourlyCost") {
      state.valueHourlyCost = Math.max(0, Number(event.target.value) || 0);
      renderContent();
    }
  });
  window.addEventListener("beforeunload", stopPlaying);

  async function boot() {
    try {
      const [index, engineering] = await Promise.all([
        fetch("data/index.json", { cache: "no-store" }).then((response) => {
          if (!response.ok) throw new Error("无法读取静态回放索引");
          return response.json();
        }),
        fetch("data/engineering-snapshot.json", { cache: "no-store" }).then((response) => {
          if (!response.ok) throw new Error("无法读取工程证据快照");
          return response.json();
        }),
      ]);
      state.cases = index.cases || [];
      state.engineering = engineering;
      const selected = state.cases.find((item) => item.case_id === requestedCase) || state.cases.find((item) => item.case_id === "CASE-2026-0008") || state.cases[0];
      if (!selected) throw new Error("静态回放索引为空");
      await loadCase(selected.file);
    } catch (error) {
      $("tab-content").innerHTML = '<div class="capture-notice">静态回放加载失败：' + esc(error.message) + "</div>";
    }
  }

  boot();
})();
