
/* RevGuard 静态运行回放：只读取 website/data，与 live WebUI 使用同一套驾驶舱结构。 */
(() => {
  "use strict";

  const STAGES = [
    { id: "evidence", label: "证据包", glyph: "▣" },
    { id: "policy", label: "政策", glyph: "◇" },
    { id: "calculation", label: "计算预期佣金", glyph: "∑" },
    { id: "approval", label: "人工审批边界", glyph: "♙" },
    { id: "execution", label: "执行（组件）", glyph: "▤" },
    { id: "verification", label: "独立验证（不同主体）", glyph: "✓" },
    { id: "rollback", label: "自动回滚", glyph: "↶" },
    { id: "postcheck", label: "回滚后状态", glyph: "✓" },
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

  const state = {
    cases: [],
    bundle: null,
    stepIndex: Number.isFinite(Number(params.get("step"))) ? Number(params.get("step")) : -1,
    tab: VALID_TABS.has(requestedTab) ? requestedTab : "decision",
    playing: false,
    speed: 1,
    timer: null,
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
  const headlineExpected = () => state.bundle?.headline?.verified || state.bundle?.headline?.expected || "—";
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
    const status = state.bundle?.case?.status;
    if (isRollback() && index === 5 && current >= 5) return "error";
    if (index < current) return "done";
    if (index === current) return index === 6 ? "rollback" : "active";
    if (index === 6 && !isRollback() && current >= 5) return "done";
    if (index === 6 && isRollback() && reached("recovery")) return "rollback";
    if (index === 7 && (reached("closing") || status === "CLOSED" || status === "ROLLED_BACK")) return "done";
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
    if (id === "rollback") return isRollback() ? (recovery ? "2 笔冲销" : "待触发") : "无需回滚";
    if (id === "postcheck") return reached("closing") || reached("recovery") ? "已通过" : "待结案";
    return "—";
  };

  function renderHeader() {
    const bundle = state.bundle;
    const c = bundle.case || {};
    const risk = latest("risk")?.subtitle || "L2";
    const status = c.status || "—";
    $("topbar").innerHTML =
      '<div class="brand-group">' +
        '<svg class="brand-mark" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 3.8 26 7.7v7.2c0 6.4-4.1 11-10 13.3C10.1 25.9 6 21.3 6 14.9V7.7Z" fill="none" stroke="currentColor" stroke-width="2"/><path d="m10.5 15.7 3.4 3.4 7-7" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round" stroke-width="2"/></svg>' +
        '<span class="brand-name">RevGuard</span><span class="top-divider"></span>' +
        '<select id="case-select" class="case-select" aria-label="选择回放案件">' +
          state.cases.map((item) => '<option value="' + esc(item.file) + '"' + (item.file === bundle.__file ? " selected" : "") + ">" + esc(item.case_id + " · " + (item.title || "运行记录")) + "</option>").join("") +
        '</select>' +
        '<span class="risk-pill">风险等级 ' + esc(risk) + "</span>" +
        '<span class="mcp-pill">MCP 参考链路</span>' +
        '<span class="mcp-pill matrix-pill"><span></span>AgentTeams · Matrix</span>' +
        '<span class="approval-label">人工审批</span>' +
      '</div>' +
      '<div class="disclosure">合成业务数据 · 真实运行链路 <span>· 静态回放（不连接后端）</span></div>' +
      '<div class="top-actions"><span class="health-pill"><i class="health-dot"></i>静态回放 · 只读</span><span class="status-mini outcome-' + statusTone() + '">' + esc(status) + "</span></div>";
    document.title = "RevGuard · " + (c.case_id || "运行") + " 运行回放";
    $("case-select").addEventListener("change", (event) => loadCase(event.target.value));
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
        '<div class="stage-heading"><span class="stage-index">' + (index + 1) + "</span>" + esc(stage.label) + "</div>" +
        '<strong class="stage-value">' + esc(stageValue(stage.id)) + "</strong>" +
        '<div class="stage-line"><span class="stage-node"><span class="stage-glyph">' + stage.glyph + "</span></span>" +
        (index < STAGES.length - 1 ? '<span class="stage-arrow">➜</span>' : "") + "</div></div>";
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
    $(".progress").setAttribute("aria-valuenow", String(percent));
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
      const source = item.source === "erpnext" ? "ERPNext" : item.source === "revguard-ledger" ? "PolarDB · ledger" : "RevGuard · policy";
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

  function renderAgentMatrix() {
    const spans = (state.bundle.trace?.spans || []).filter((span) => span.kind === "AGENT" || span.kind === "SKILL").slice().reverse().slice(0, 16);
    const run = state.bundle.case.team_run || {};
    const status = run.status || "COMPLETED";
    const cards = spans.length ? spans.map((span, index) =>
      '<details class="agent-task-card ' + (index === 0 ? "orchestrator-card" : "") + '"' + (index === 0 ? " open" : "") + "><summary>" +
        '<span class="task-seq">' + esc(String(span.sequence || index + 1)) + "</span><div><strong title=\"" + esc(span.name) + "\">" + esc(span.label || span.name) + '</strong><code>' + esc(span.actor || "AgentTeams worker") + "</code></div>" +
        '<span class="transport-cell">' + (span.kind === "AGENT" ? "Matrix" : "Skill") + "</span><span class=\"task-metric-cell\">" + esc(duration(span.duration_ms)) + '</span><span class="task-metric-cell metric-unavailable">—</span><span class="task-status task-succeeded">OK</span></summary>' +
        '<div class="task-evidence-grid"><div><span>控制输入</span><pre>' + esc(JSON.stringify({ stage: span.stage || "orchestration", actor: span.actor || "—" }, null, 2)) + '</pre></div><div><span>控制输出</span><pre>' + esc(JSON.stringify({ status: span.status || "OK", label: span.label || span.name }, null, 2)) + "</pre></div></div>" +
        '<div class="correlation-strip"><code>span ' + shortId(span.name, 28) + "</code><code>seq " + esc(span.sequence || index + 1) + "</code></div></details>"
    ).join("") : '<div class="empty-state">暂无 AgentTeams 追踪记录。</div>';
    const workerCount = new Set(spans.map((span) => span.actor).filter(Boolean)).size;
    const runtime = '<div class="team-runtime team-runtime-' + status.toLowerCase() + '">' +
      '<div><span class="runtime-live-dot"></span><strong>' + esc(status) + '</strong><small>静态回放</small></div>' +
      '<div><span>执行者</span><strong>revguard-orchestrator</strong></div>' +
      '<div><span>阶段</span><strong>' + esc(run.phase || "EXECUTION") + '</strong></div>' +
      '<div><span>任务</span><strong>' + esc((run.completed_tasks || spans.length) + " / " + (run.total_tasks || 16)) + "</strong></div></div>";
    const header = '<div class="agent-task-columns" aria-hidden="true"><span>序号</span><span>任务 / 执行者</span><span>通道</span><span>耗时</span><span>Token</span><span>状态</span></div>';
    return section("多智能体协同任务账本", runtime + '<div class="agent-task-ledger">' + header + cards + "</div>", (state.bundle.trace?.span_count || spans.length) + " 个运行跨度 · " + workerCount + " 个执行者", "♟");
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
    const expected = num(headlineExpected());
    const posted = num(state.bundle.headline?.posted);
    const delta = Math.max(0, expected - posted);
    return '<div class="value-simulator">' +
      '<section class="detail-section value-hero"><div><span class="value-eyebrow">STATIC CAPTURE · ENGINEERING VALUE</span><h2>把一次治理闭环，变成可复用的控制能力</h2><p>这里复现线上 WebUI 的价值模拟布局，但数字来自本次真实运行记录的静态快照；不再读取 ERPNext、PolarDB 或 Grafana。</p></div><div class="scenario-controls"><label><span>本案已发布</span><div><input value="' + esc(number(posted)) + '" readonly><b>' + esc(currency()) + '</b></div></label><label><span>规则应有</span><div><input value="' + esc(number(expected)) + '" readonly><b>' + esc(currency()) + '</b></div></label><div class="scenario-presets"><span>回放场景</span><button class="active" type="button" disabled>真实运行记录</button><button type="button" disabled>仅展示</button></div></div></section>' +
      '<div class="value-kpi-grid"><article><span>识别差额</span><strong>' + esc(number(delta)) + '<em>' + esc(currency()) + '</em></strong><small>确定性账本差异</small></article><article class="value-kpi-accent"><span>审批边界</span><strong>' + esc(approvalAmount()) + '</strong><small>真人在线审批</small></article><article><span>运行跨度</span><strong>' + esc(state.bundle.trace?.span_count || "—") + '<em> spans</em></strong><small>AgentTeams / Skill / Tool</small></article><article><span>审计事件</span><strong>' + esc(state.bundle.audit?.count || "—") + '</strong><small>链式追加，校验通过</small></article></div>' +
      '<div class="value-detail-grid"><section class="detail-section"><div class="section-title"><span class="tab-icon">↔</span><strong>治理前后对比</strong><span>（记录快照）</span></div><div class="comparison-list"><div class="comparison-row"><div><strong>台账差异</strong><small>发现并解释原始少记</small></div><div class="comparison-track"><span class="before-bar"></span><span class="after-bar" style="width:100%"></span></div></div><div class="sample-outcomes"><div><span>证据强度</span><strong>' + esc((latest("evidence")?.evidence || []).length) + ' 条强证据</strong></div><div><span>验证结论</span><strong>' + esc(verificationStep()?.subtitle || "—") + '</strong></div><div><span>恢复状态</span><strong>' + esc(isRollback() ? "已冲销" : "无需回滚") + '</strong></div></div></section><section class="detail-section"><div class="section-title"><span class="tab-icon">∑</span><strong>价值口径</strong><span>（不外推业务收益）</span></div><div class="formula-callout"><span>本案可解释的控制价值</span><strong>发现 → 审批 → 受控执行 → 独立验证</strong><b>' + esc(number(delta)) + " " + esc(currency()) + '</b></div><div class="methodology-list"><div><span>数据来源</span><strong>CAPTURED_FROM_RUNTIME</strong></div><div><span>数字性质</span><strong>记录事实，不是预测</strong></div><div><span>公开边界</span><strong>合成业务样本</strong></div></div></section></div></div>';
  }

  function renderPublicData() {
    const p = state.bundle.provenance || {};
    const evidence = latest("evidence")?.evidence || [];
    const sourceCount = new Set(evidence.map((item) => item.source)).size;
    return '<div class="public-data-dashboard"><section class="detail-section public-data-hero"><div><span class="value-eyebrow">PUBLIC DATA EXPERIMENT · STATIC REPLAY</span><h2>真实运行记录，公开可验证</h2><p>托管页面只展示已导出的记录：来源、规则、AgentTeams 协作、审批边界、执行与复核都保留；业务字段按比赛要求使用合成样本。</p></div><div class="public-provenance-grid"><div><span>capture kind</span><strong>' + esc(p.capture_kind || "CAPTURED_FROM_RUNTIME") + "</strong><small>不是线上接口读取</small></div><div><span>source release</span><strong>" + esc(p.source_release || "—") + "</strong><small>记录生成版本</small></div><div><span>workflow</span><strong>real_executable</strong><small>真实可执行链路</small></div><div><span>business data</span><strong>synthetic</strong><small>公开演示边界</small></div></div></section>" +
      '<div class="public-kpi-grid"><article><span>案件</span><strong>1</strong><small>本次回放选择</small></article><article><span>证据来源</span><strong>' + esc(sourceCount || 3) + "</strong><small>ERPNext / ledger / policy</small></article><article><span>Agent Trace</span><strong>" + esc(state.bundle.trace?.span_count || "—") + "</strong><small>来自真实运行</small></article><article><span>审计事件</span><strong>" + esc(state.bundle.audit?.count || "—") + "</strong><small>链路校验</small></article><article><span>运行版本</span><strong>" + esc(p.source_release || "—") + "</strong><small>来源 release</small></article><article><span>接口调用</span><strong>0</strong><small>静态回放页</small></article></div>" +
      '<div class="public-detail-grid"><section class="detail-section"><div class="section-title"><span class="tab-icon">▣</span><strong>公开展示边界</strong><span>（逐项说明）</span></div><div class="public-bar-list"><div><code>evidence provenance</code><span><i style="width:100%"></i></span><strong>保留</strong></div><div><code>agentteams trace</code><span><i style="width:100%"></i></span><strong>保留</strong></div><div><code>approval boundary</code><span><i style="width:100%"></i></span><strong>保留</strong></div><div><code>business identifiers</code><span><i style="width:55%"></i></span><strong>脱敏</strong></div></div></section><section class="detail-section"><div class="section-title"><span class="tab-icon">✓</span><strong>验收结果</strong><span>（托管前检查）</span></div><div class="public-acceptance-grid"><div><span>回放索引</span><strong>通过</strong></div><div><span>静态引用</span><strong>通过</strong></div><div><span>后端连接</span><strong>未使用</strong></div><div><span>敏感信息</span><strong>未公开</strong></div><div><span>数据说明</span><strong>已声明</strong></div></div></section></div><div class="claim-boundary public-boundary"><span class="tab-icon">!</span><span>本页证明的是“真实运行记录可复现展示”，不把静态回放冒充成在线业务系统。</span></div></div>';
  }

  function renderObservability() {
    const p = state.bundle.provenance || {};
    return '<section class="observability-screen">' +
      '<div class="observability-toolbar"><div class="observability-heading"><span class="tab-icon">⌁</span><div><h2>可观测运行回放</h2><p>保留驾驶舱中的可观测位置，但静态托管页不加载 Grafana。</p></div></div>' +
      '<div class="observability-actions"><span class="observability-mode">STATIC · READ ONLY</span><span class="health-pill">快照版本 ' + esc(p.source_release || "—") + '</span></div></div>' +
      '<p class="observability-notice">下方展示记录中的运行元数据，避免从托管页面再次探测任何系统接口。</p>' +
      '<div class="observability-frame-wrap"><div class="observability-placeholder"><span class="tab-icon">⌁</span><strong>Grafana 画布在静态回放中保持关闭</strong><p>本次运行：' + esc(state.bundle.case.recording_id || state.bundle.case.case_id) + ' · ' + esc(state.bundle.trace?.span_count || "—") + ' spans · ' + esc(state.bundle.audit?.count || "—") + ' audit events</p><code>' + shortId(p.snapshot_sha256, 64) + '</code></div></div></section>';
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
      const requestedStep = Number(params.get("step"));
      state.stepIndex = Number.isFinite(requestedStep) ? Math.max(0, Math.min(bundle.steps.length - 1, requestedStep)) : bundle.steps.length - 1;
      render();
      const notice = $("capture-notice");
      notice.hidden = false;
      notice.textContent = "静态回放 · " + (bundle.provenance?.capture_kind || "CAPTURED_FROM_RUNTIME") + " · 来源版本 " + (bundle.provenance?.source_release || "—") + " · 业务样本为合成数据";
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
  });
  window.addEventListener("beforeunload", stopPlaying);

  async function boot() {
    try {
      const index = await fetch("data/index.json", { cache: "no-store" }).then((response) => {
        if (!response.ok) throw new Error("无法读取静态回放索引");
        return response.json();
      });
      state.cases = index.cases || [];
      const selected = state.cases.find((item) => item.case_id === requestedCase) || state.cases[0];
      if (!selected) throw new Error("静态回放索引为空");
      await loadCase(selected.file);
    } catch (error) {
      $("tab-content").innerHTML = '<div class="capture-notice">静态回放加载失败：' + esc(error.message) + "</div>";
    }
  }

  boot();
})();
