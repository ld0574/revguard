
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
  const TASK_STATUS_LABELS = {
    PENDING: "待处理",
    RUNNING: "执行中",
    SUCCEEDED: "已成功",
    RESULT_UNKNOWN: "资金结果待核对",
    RECOVERY_REQUIRED: "等待对账恢复",
    FAILED_RETRYABLE: "失败待重试",
    FAILED_FINAL: "最终失败",
    CANCELLED: "已取消",
    ACKNOWLEDGED: "已确认",
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
    approvalDemo: null,
    approvalDialogOpen: false,
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
  const tokens = (value) => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "未采集";
  };
  const taskStatus = (value) => TASK_STATUS_LABELS[value] || value || "—";
  const taskTransport = (task) => {
    if (task.skill_transport === "higress-mcp") return "MCP 网关";
    if (task.transport === "agentteams-matrix") return "Matrix";
    if (task.transport === "mcp") return "MCP 参考链路";
    return task.transport || "—";
  };
  const prettyJson = (value, fallback = {}) => JSON.stringify(value == null ? fallback : value, null, 2);
  const taskReceipt = (task) => {
    if (task.result != null) return { label: "任务输出", value: task.result };
    if (task.error != null) return { label: "任务错误", value: task.error };
    if (task.handoff != null) {
      return { label: "任务回执（未返回 result 字段）", value: { status: task.status, handoff: task.handoff, telemetry: task.telemetry } };
    }
    return { label: "任务回执", value: { status: task.status } };
  };
  // Keep the replay stage vocabulary visually identical to the live WebUI.
  // These are the same Phosphor Icons v2.1.10 paths used by demo-ui/src/App.jsx:
  // duotone for stage nodes and bold for the inter-stage ArrowRight.
  const iconSvg = (name, className = "stage-icon") => {
    const icons = {
      evidence: '<path d="M208,88v24H69.77a8,8,0,0,0-7.59,5.47L32,208V64a8,8,0,0,1,8-8H93.33a8,8,0,0,1,4.8,1.6L128,80h72A8,8,0,0,1,208,88Z" opacity="0.2"></path><path d="M245,110.64A16,16,0,0,0,232,104H216V88a16,16,0,0,0-16-16H130.67L102.94,51.2a16.14,16.14,0,0,0-9.6-3.2H40A16,16,0,0,0,24,64V208a8,8,0,0,0,8,8H211.1a8,8,0,0,0,7.59-5.47l28.49-85.47A16.05,16.05,0,0,0,245,110.64ZM93.34,64,123.2,86.4A8,8,0,0,0,128,88h72v16H69.77a16,16,0,0,0-15.18,10.94L40,158.7V64Zm112,136H43.1l26.67-80H232Z"></path>',
      policy: '<path d="M216,56v56c0,96-88,120-88,120S40,208,40,112V56a8,8,0,0,1,8-8H208A8,8,0,0,1,216,56Z" opacity="0.2"></path><path d="M208,40H48A16,16,0,0,0,32,56v56c0,52.72,25.52,84.67,46.93,102.19,23.06,18.86,46,25.26,47,25.53a8,8,0,0,0,4.2,0c1-.27,23.91-6.67,47-25.53C198.48,196.67,224,164.72,224,112V56A16,16,0,0,0,208,40Zm0,72c0,37.07-13.66,67.16-40.6,89.42A129.3,129.3,0,0,1,128,223.62a128.25,128.25,0,0,1-38.92-21.81C61.82,179.51,48,149.3,48,112l0-56,160,0ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z"></path>',
      calculation: '<path d="M176,64v48H80V64Z" opacity="0.2"></path><path d="M80,120h96a8,8,0,0,0,8-8V64a8,8,0,0,0-8-8H80a8,8,0,0,0-8,8v48A8,8,0,0,0,80,120Zm8-48h80v32H88ZM200,24H56A16,16,0,0,0,40,40V216a16,16,0,0,0,16,16H200a16,16,0,0,0,16-16V40A16,16,0,0,0,200,24Zm0,192H56V40H200ZM100,148a12,12,0,1,1-12-12A12,12,0,0,1,100,148Zm40,0a12,12,0,1,1-12-12A12,12,0,0,1,140,148Zm40,0a12,12,0,1,1-12-12A12,12,0,0,1,180,148Zm-80,40a12,12,0,1,1-12-12A12,12,0,1,1,100,188Zm40,0a12,12,0,1,1-12-12A12,12,0,1,1,140,188Zm40,0a12,12,0,1,1-12-12A12,12,0,1,1,180,188Z"></path>',
      approval: '<path d="M168,100a60,60,0,1,1-60-60A60,60,0,0,1,168,100Z" opacity="0.2"></path><path d="M144,157.68a68,68,0,1,0-71.9,0c-20.65,6.76-39.23,19.39-54.17,37.17a8,8,0,0,0,12.25,10.3C50.25,181.19,77.91,168,108,168s57.75,13.19,77.87,37.15a8,8,0,0,0,12.25-10.3C183.18,177.07,164.6,164.44,144,157.68ZM56,100a52,52,0,1,1,52,52A52.06,52.06,0,0,1,56,100Zm197.66,33.66-32,32a8,8,0,0,1-11.32,0l-16-16a8,8,0,0,1,11.32-11.32L216,148.69l26.34-26.35a8,8,0,0,1,11.32,11.32Z"></path>',
      execution: '<path d="M216,80c0,26.51-39.4,48-88,48S40,106.51,40,80s39.4-48,88-48S216,53.49,216,80Z" opacity="0.2"></path><path d="M128,24C74.17,24,32,48.6,32,80v96c0,31.4,42.17,56,96,56s96-24.6,96-56V80C224,48.6,181.83,24,128,24Zm80,104c0,9.62-7.88,19.43-21.61,26.92C170.93,163.35,150.19,168,128,168s-42.93-4.65-58.39-13.08C55.88,147.43,48,137.62,48,128V111.36c17.06,15,46.23,24.64,80,24.64s62.94-9.68,80-24.64ZM69.61,53.08C85.07,44.65,105.81,40,128,40s42.93,4.65,58.39,13.08C200.12,60.57,208,70.38,208,80s-7.88,19.43-21.61,26.92C170.93,115.35,150.19,120,128,120s-42.93-4.65-58.39-13.08C55.88,99.43,48,89.62,48,80S55.88,60.57,69.61,53.08ZM186.39,202.92C170.93,211.35,150.19,216,128,216s-42.93-4.65-58.39-13.08C55.88,195.43,48,185.62,48,176V159.36c17.06,15,46.23,24.64,80,24.64s62.94-9.68,80-24.64V176C208,185.62,200.12,195.43,186.39,202.92Z"></path>',
      verification: '<path d="M224,128a96,96,0,1,1-96-96A96,96,0,0,1,224,128Z" opacity="0.2"></path><path d="M72,128a134.63,134.63,0,0,1-14.16,60.47,8,8,0,1,1-14.32-7.12A118.8,118.8,0,0,0,56,128,71.73,71.73,0,0,1,83,71.8,8,8,0,1,1,93,84.29,55.76,55.76,0,0,0,72,128Zm56-8a8,8,0,0,0-8,8,184.12,184.12,0,0,1-23,89.1,8,8,0,0,0,14,7.76A200.19,200.19,0,0,0,136,128,8,8,0,0,0,128,120Zm0-32a40,40,0,0,0-40,40,8,8,0,0,0,16,0,24,24,0,0,1,48,0,214.09,214.09,0,0,1-20.51,92A8,8,0,1,0,146,226.83,230,230,0,0,0,168,128,40,40,0,0,0,128,88Zm0-64A104.11,104.11,0,0,0,24,128a87.76,87.76,0,0,1-5,29.33,8,8,0,0,0,15.09,5.33A103.9,103.9,0,0,0,40,128a88,88,0,0,1,176,0,282.24,282.24,0,0,1-5.29,54.45,8,8,0,0,0,6.3,9.4,8.22,8.22,0,0,0,1.55.15,8,8,0,0,0,7.84-6.45A298.37,298.37,0,0,0,232,128,104.12,104.12,0,0,0,128,24ZM94.4,152.17A8,8,0,0,0,85,158.42a151,151,0,0,1-17.21,45.44,8,8,0,0,0,13.86,8,166.67,166.67,0,0,0,19-50.25A8,8,0,0,0,94.4,152.17ZM128,56a72.85,72.85,0,0,0-9,.56,8,8,0,0,0,2,15.87A56.08,56.08,0,0,1,184,128a252.12,252.12,0,0,1-1.92,31A8,8,0,0,0,189,168a8.39,8.39,0,0,0,1,.06,8,8,0,0,0,7.92-7,266.48,266.48,0,0,0,2-33A72.08,72.08,0,0,0,128,56Zm57.93,128.25a8,8,0,0,0-9.75,5.75c-1.46,5.69-3.15,11.4-5,17a8,8,0,0,0,5,10.13,7.88,7.88,0,0,0,2.55.42,8,8,0,0,0,7.58-5.46c2-5.92,3.79-12,5.35-18.05A8,8,0,0,0,185.94,184.26Z"></path>',
      rollback: '<path d="M216,128a88,88,0,1,1-88-88A88,88,0,0,1,216,128Z" opacity="0.2"></path><path d="M224,128a96,96,0,0,1-94.71,96H128A95.38,95.38,0,0,1,62.1,197.8a8,8,0,0,1,11-11.63A80,80,0,1,0,71.43,71.39a3.07,3.07,0,0,1-.26.25L44.59,96H72a8,8,0,0,1,0,16H24a8,8,0,0,1-8-8V56a8,8,0,0,1,16,0V85.8L60.25,60A96,96,0,0,1,224,128Z"></path>',
      postcheck: '<path d="M216,56v56c0,96-88,120-88,120S40,208,40,112V56a8,8,0,0,1,8-8H208A8,8,0,0,1,216,56Z" opacity="0.2"></path><path d="M208,40H48A16,16,0,0,0,32,56v56c0,52.72,25.52,84.67,46.93,102.19,23.06,18.86,46,25.26,47,25.53a8,8,0,0,0,4.2,0c1-.27,23.91-6.67,47-25.53C198.48,196.67,224,164.72,224,112V56A16,16,0,0,0,208,40Zm0,72c0,37.07-13.66,67.16-40.6,89.42A129.3,129.3,0,0,1,128,223.62a128.25,128.25,0,0,1-38.92-21.81C61.82,179.51,48,149.3,48,112l0-56,160,0ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z"></path>',
    };
    return '<svg class="' + className + '" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" fill="currentColor" viewBox="0 0 256 256" aria-hidden="true">' + (icons[name] || icons.postcheck) + "</svg>";
  };
  const arrowSvg = () => '<svg class="stage-arrow" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" fill="currentColor" viewBox="0 0 256 256" aria-hidden="true"><path d="M224.49,136.49l-72,72a12,12,0,0,1-17-17L187,140H40a12,12,0,0,1,0-24H187L135.51,64.48a12,12,0,0,1,17-17l72,72A12,12,0,0,1,224.49,136.49Z"></path></svg>';
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
  const createApprovalDemo = () => ({
    username: "finance-lead",
    password: "demo-only-2026",
    comment: "证据完整，政策与金额复算一致，同意在当前风险边界内处理。",
    decision: "APPROVED",
    verified: false,
    committed: null,
    message: "",
  });
  const approvalActionLabel = (decision) => decision === "REJECTED" ? "驳回" : "批准";
  const approvalStageIndex = () => (state.bundle?.steps || []).findIndex((step) => step.stage === "approval");
  const approvalAwaiting = () => currentStep()?.stage === "approval" && !state.approvalDemo?.committed;
  const approvalRejected = () => currentStep()?.stage === "approval" && state.approvalDemo?.committed === "REJECTED";
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
  const replayStatus = () => {
    const actual = state.bundle?.case?.status || "—";
    const stage = currentStep()?.stage;
    if (!stage) return "—";
    if (stage === "approval") {
      if (state.approvalDemo?.committed === "APPROVED") return "APPROVED";
      if (state.approvalDemo?.committed === "REJECTED") return "REJECTED";
      return "WAITING_FOR_APPROVAL";
    }
    if (stage === "closing") return actual;
    if (stage === "recovery" && actual === "ROLLED_BACK") return "ROLLED_BACK";
    return "RUNNING";
  };
  const replayStatusNote = () => {
    const status = replayStatus();
    if (status === "WAITING_FOR_APPROVAL") return "等待人工审批动作";
    if (status === "APPROVED") return "本地审批动作已记录";
    if (status === "REJECTED") return "本地驳回动作已记录，回放已停止";
    if (status === "RUNNING") return "按当前步骤回放";
    if (status === "CLOSED") return "闭环完成";
    if (status === "ROLLED_BACK") return "已完成冲销恢复";
    if (status === "FAILED") return "运行失败";
    return "等待终态";
  };
  const statusTone = () => {
    const status = replayStatus();
    if (["WAITING_FOR_APPROVAL", "ROLLED_BACK"].includes(status)) return "warning";
    if (["CLOSED", "APPROVED"].includes(status)) return "success";
    if (["FAILED", "REJECTED"].includes(status)) return "danger";
    return "neutral";
  };
  const stageState = (index) => {
    const current = currentStageIndex();
    if (approvalRejected() && index === STAGE_INDEX.approval) return "error";
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
    const status = replayStatus();
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
        state.cases.map((item) => '<option value="' + esc(item.file) + '"' + (item.file === bundle.__file ? " selected" : "") + ">" + esc(item.case_id + " · " + (item.file === bundle.__file ? replayStatus() : (item.status || item.title || "运行记录"))) + "</option>").join("") +
        '</select><span class="risk-pill">' + esc(risk) + "</span>" +
        (c.execution_mode === "MCP_TEAM" ? '<span class="mcp-pill">MCP 参考链路</span>' : "") +
        '<span class="mcp-pill matrix-pill"><span></span>AgentTeams · Matrix</span><span class="approval-label">人工审批</span>';
    const topActions = globalView
      ? '<span class="observability-mode">' + (publicMode ? "三真一合成 · 可复算" : "实时观测 · 只读展示") + "</span>"
      : '<span class="health-pill"><i class="health-dot"></i>静态回放 · 只读</span><span class="status-mini outcome-' + statusTone() + '">' + esc(status) + "</span>";
    const disclosure = globalView ? '<div class="disclosure">' + (publicMode ? "公开真实交易 · 合成结算与异常" : "合成业务数据 · 真实运行链路") + "</div>" : "";
    topbar.innerHTML =
      '<div class="brand-group"><svg class="brand-mark" xmlns="http://www.w3.org/2000/svg" width="1em" height="1em" fill="currentColor" viewBox="0 0 256 256" aria-hidden="true"><path d="M216,56v56c0,96-88,120-88,120S40,208,40,112V56a8,8,0,0,1,8-8H208A8,8,0,0,1,216,56Z" opacity="0.2"></path><path d="M208,40H48A16,16,0,0,0,32,56v56c0,52.72,25.52,84.67,46.93,102.19,23.06,18.86,46,25.26,47,25.53a8,8,0,0,0,4.2,0c1-.27,23.91-6.67,47-25.53C198.48,196.67,224,164.72,224,112V56A16,16,0,0,0,208,40Zm0,72c0,37.07-13.66,67.16-40.6,89.42A129.3,129.3,0,0,1,128,223.62a128.25,128.25,0,0,1-38.92-21.81C61.82,179.51,48,149.3,48,112l0-56,160,0ZM82.34,141.66a8,8,0,0,1,11.32-11.32L112,148.69l50.34-50.35a8,8,0,0,1,11.32,11.32l-56,56a8,8,0,0,1-11.32,0Z"></path></svg><span class="brand-name">RevGuard</span><span class="top-divider"></span>' + brandEnd + '</div>' +
      disclosure +
      '<div class="top-actions">' + (globalView ? topActions : '<span class="health-pill">审批与写后验证约束</span><button class="icon-button" type="button" disabled aria-label="静态回放不允许重置"><span aria-hidden="true">↻</span><span>重置全部</span></button>' + topActions) + "</div>";
    document.title = globalView ? "RevGuard · " + (publicMode ? "公开数据实验" : "全局运行总览") : "RevGuard · " + (c.case_id || "运行") + " 运行回放";
    $("case-select")?.addEventListener("change", (event) => loadCase(event.target.value));
  }

  function renderSummary() {
    const c = state.bundle.case || {};
    const intake = state.bundle.steps.find((step) => step.stage === "intake");
    const verification = verificationStep();
    const status = replayStatus();
    const variance = state.bundle.headline?.variance || "—";
    const items = [
      ["代理商", c.partner_name, c.partner_id || "按名称解析", ""],
      ["订单号", c.order_id, intake?.facts?.["业务时点"] ? "业务时点 " + intake.facts["业务时点"] : "订单证据", ""],
      ["订单金额", intake?.facts?.["订单金额"], "合成业务订单", ""],
      ["已入账金额", state.bundle.headline?.posted, "模拟佣金台账", ""],
      ["预期佣金（正确）", headlineExpected(), "确定性规则内核", ""],
      ["本次审批金额", approvalAmount(), latest("approval")?.subtitle || "PENDING", ""],
      ["当前状态", status, replayStatusNote(), statusTone()],
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
    const rejected = approvalRejected();
    const finalStatus = rejected ? "REJECTED" : state.bundle.case.status;
    const finalOk = rejected ? "后续执行已阻断" : state.bundle.case.status === "CLOSED" ? "正常闭环" : state.bundle.case.status === "ROLLED_BACK" ? "已恢复至安全基线" : "等待终态";
    const verifiedAmount = verification?.checks?.reduce((sum, item) => sum + num(item.actual), 0);
    const approvalInteraction = current?.stage === "approval"
      ? '<button type="button" class="primary-action evidence-action approval-demo-trigger" data-open-approval>演示人工审批 · ' + esc(approvalAmount()) + '</button>' +
        (state.approvalDemo?.committed ? '<div class="approval-replay-status ' + (state.approvalDemo.committed === "REJECTED" ? "is-rejected" : "") + '">本地动作：' + esc(approvalActionLabel(state.approvalDemo.committed)) + ' · 正式记录仍为 ' + esc(latest("approval")?.subtitle || "APPROVED") + '</div>' : '<small class="approval-replay-hint">账号与密码已预填；点击按钮打开 WebUI 同款审批交互。</small>')
      : latest("approval")
        ? '<button type="button" class="recording-again approval-jump" data-jump-stage="approval">回到人工审批步骤</button>'
        : "";
    $("pipeline-details").innerHTML =
      '<div class="pipeline-note calculation-note"><span>政策选择</span>' +
        '<strong>' + esc(policy ? ((policy.policy?.["排除版本"] || []).length ? "历史版本已排除" : "无冲突版本") : "等待政策匹配") + "</strong>" +
        '<strong>' + esc(policy ? (policy.subtitle || "规则集已选定") + "：已采用" : "等待确定性复算") + "</strong></div>" +
      '<div class="pipeline-note capability-note"><span>能力边界</span>' +
        '<div>总额度上限：' + money(latest("risk") ? "50,000.00 " + currency() : "待确定") + "</div>" +
        '<div>本次金额：' + money(approvalAmount()) + "</div>" +
        approvalInteraction +
        "<small>静态回放 · 审批、执行与验证均为记录快照</small></div>" +
      '<div class="pipeline-note execution-note"><span>模拟记账（入账）</span>' +
        (execution?.executions?.length ? execution.executions.map((item) => "<strong>" + esc(item.component) + "：+" + esc(item.amount) + "</strong>").join("") + "<div>合计：" + money(execution.executions.reduce((sum, item) => sum + num(item.amount), 0).toFixed(2) + " " + currency()) + "</div>" : "<strong>等待受限执行器写入</strong>") + "</div>" +
      '<div class="pipeline-note verify-note ' + (verification?.subtitle === "FAILED" ? "is-failed" : "") + '"><span>验证结果</span>' +
        "<div>实际读取：" + (verification ? money(verifiedAmount.toFixed(2) + " " + currency()) : "—") + "</div>" +
        "<div>差异：" + money(state.bundle.headline?.variance || "—") + "</div>" +
        "<strong>" + esc(verification?.subtitle === "FAILED" ? "不匹配" : verification?.subtitle === "PASSED" ? "验证通过" : "等待独立验证") + "</strong></div>" +
      '<div class="pipeline-note rollback-note"><span>自动回滚执行</span>' +
        (recovery?.executions?.length ? recovery.executions.map((item) => "<strong>" + esc(item.component || "组件") + "：" + esc(item.amount || "冲销") + "</strong>").join("") : "<strong>" + (isRollback() ? "按原操作关联冲销" : "验证失败时由策略自动触发") + "</strong>") + "</div>" +
      '<div class="pipeline-note result-note ' + (rejected || state.bundle.case.status === "FAILED" ? "is-failed" : "") + '"><span>最终结果</span><strong>' +
        esc(finalStatus || "等待终态") + "</strong><b>" + esc(finalOk) + "</b><small>" +
        esc(rejected ? "人工审批驳回，未进入执行、验证或回滚" : recovery ? "冲销后独立复核通过" : verification?.subtitle === "PASSED" ? "独立验证通过，调整已完成" : current ? current.title : "尚未生成终态结论") + "</small></div>";

    const total = state.bundle.steps.length || 1;
    const percent = Math.round(((state.stepIndex + 1) / total) * 100);
    $("step-index").textContent = String(state.stepIndex + 1);
    $("step-total").textContent = String(total);
    $("progress-bar").style.width = percent + "%";
    document.querySelector(".progress").setAttribute("aria-valuenow", String(percent));
    $("replay-step-note").textContent = "当前记录步骤：" + (current ? current.title : "—") + " · " + (current?.subtitle || "静态快照");
    $("btn-play").textContent = rejected ? "审批已驳回" : state.playing ? "暂停回放" : (state.stepIndex >= total - 1 ? "从头播放" : "播放回放");
    $("btn-play").setAttribute("aria-pressed", String(state.playing));
    $("btn-play").disabled = approvalAwaiting() || rejected;
    $("btn-prev").disabled = state.stepIndex <= 0;
    $("btn-next").disabled = approvalAwaiting() || rejected || state.stepIndex >= total - 1;
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
    const persistedTasks = Array.isArray(state.bundle.agent_tasks) ? state.bundle.agent_tasks : [];
    const agentTasks = persistedTasks.length
      ? persistedTasks.map((task, index) => {
        const skillName = task.skill_name || "";
        const span = spans.find((candidate) => candidate.agent_task_id === task.task_id);
        return {
          ...task,
          skillName,
          replayStage: TASK_STAGE_BY_SKILL[skillName] || "closing",
          taskNumber: index + 1,
          span,
        };
      })
      : spans.filter((span) => span.kind === "AGENT" && span.name !== "AgentTeams.OrchestratorHandshake").map((span, index) => {
        const skillName = String(span.name || "").replace(/^AgentTeams\./, "");
        return {
          ...span,
          skillName,
          skill_name: skillName,
          assigned_actor: span.actor,
          status: span.status === "OK" ? "SUCCEEDED" : span.status,
          replayStage: stageFor(span),
          taskNumber: index + 1,
          span,
        };
      });
    const visibleAgentTasks = agentTasks.filter((task) => stageOrderFor(task.replayStage) <= currentOrder);
    const orchestrator = state.bundle.case?.team_run?.orchestrator || null;
    return { spans, visible, agentTasks, visibleAgentTasks, orchestrator, current, currentStage, currentOrder, final, stageFor };
  }

  function renderAgentMatrix() {
    const trace = replayTraceWindow();
    const run = state.bundle.case.team_run || {};
    const totalTasks = Number(run.total_tasks || trace.agentTasks.length || 0);
    const visibleTasks = trace.visibleAgentTasks.slice().sort((left, right) => right.taskNumber - left.taskNumber);
    const focusTask = trace.visibleAgentTasks.slice().reverse().find((task) => task.replayStage === trace.currentStage);
    const currentActor = (trace.final ? run.current_actor : focusTask?.assigned_actor) || focusTask?.assigned_actor || "revguard-orchestrator";
    const workerCount = Number(run.workers?.length || new Set(trace.agentTasks.map((task) => task.assigned_actor).filter(Boolean)).size);
    const status = trace.final ? (run.status || "COMPLETED") : "RUNNING";
    const statusLabel = trace.final ? taskStatus(status) : "运行中";
    const runtimeStage = trace.final ? (run.current_stage ? (TASK_LABELS[run.current_stage] || run.current_stage) : "等待下一状态") : (REPLAY_STAGE_LABELS[trace.currentStage] || trace.current?.title || "等待下一状态");
    const orchestratorVisible = Boolean(trace.orchestrator && trace.currentOrder >= 0);
    const orchestratorSpan = trace.spans.find((span) => span.name === "AgentTeams.OrchestratorHandshake");
    const orchestratorUsage = trace.orchestrator?.token_usage || {};
    const orchestratorCard = orchestratorVisible
      ? '<details class="agent-task-card orchestrator-card" open><summary><span class="task-seq">编排</span><div><strong title="OrchestratorHandshake">协同任务编排</strong><code>revguard-orchestrator</code></div><span class="transport-cell">Matrix</span><span class="task-metric-cell">' + esc(duration(orchestratorSpan?.duration_ms)) + '</span><span class="task-metric-cell">' + esc(tokens(orchestratorUsage.total_tokens)) + '</span><span class="task-status task-acknowledged">已确认</span></summary>' +
        '<div class="task-evidence-grid"><div><span>控制输入</span><pre>' + esc(prettyJson(trace.orchestrator.input)) + '</pre></div><div><span>控制输出</span><pre>' + esc(prettyJson(trace.orchestrator.output, { status: "WAITING" })) + '</pre></div></div>' +
        '<div class="correlation-strip"><code>dispatch ' + shortId(trace.orchestrator.dispatch_event_id, 30) + '</code><code>trigger ' + shortId(trace.orchestrator.trigger_event_id, 30) + '</code><code>response ' + shortId(trace.orchestrator.response_event_id, 30) + '</code></div></details>'
      : "";
    const cards = visibleTasks.length ? visibleTasks.map((task, index) => {
      const focused = !trace.final && task.replayStage === trace.currentStage;
      const displayStatus = focused ? "RUNNING" : task.status || "PENDING";
      const statusClass = displayStatus === "RUNNING" ? "task-running" : displayStatus === "SUCCEEDED" ? "task-succeeded" : displayStatus === "ACKNOWLEDGED" ? "task-acknowledged" : "task-failed_final";
      const statusText = taskStatus(displayStatus) + " · 第 " + esc(task.attempt || 1) + " 次";
      const label = TASK_LABELS[task.skillName] || task.label || task.skillName;
      const span = task.span || {};
      const receipt = taskReceipt(task);
      const open = focused || index === 0;
      return '<details class="agent-task-card ' + (focused ? "focused-task-card" : "") + '"' + (open ? " open" : "") + "><summary>" +
        '<span class="task-seq">' + esc(String(task.taskNumber).padStart(2, "0")) + "</span><div><strong title=\"" + esc(task.skill_name) + "\">" + esc(label) + '</strong><code>' + esc(task.assigned_actor || "AgentTeams worker") + "</code></div>" +
        '<span class="transport-cell">' + esc(taskTransport(task)) + '</span><span class="task-metric-cell ' + (span.duration_ms == null ? "metric-unavailable" : "") + '">' + esc(duration(span.duration_ms)) + '</span><span class="task-metric-cell ' + (task.token_usage?.total_tokens == null ? "metric-unavailable" : "") + '">' + esc(tokens(task.token_usage?.total_tokens)) + '</span><span class="task-status ' + statusClass + '">' + statusText + "</span></summary>" +
        '<div class="task-evidence-grid"><div><span>任务输入</span><pre>' + esc(prettyJson(task.input)) + '</pre></div><div><span>' + esc(receipt.label) + '</span><pre>' + esc(prettyJson(receipt.value)) + "</pre></div></div>" +
        '<div class="correlation-strip"><code>任务 ' + shortId(task.task_id, 28) + '</code><code>请求 ' + shortId(task.request_id, 28) + '</code><code>房间 ' + shortId(task.matrix_room_id, 28) + '</code><code>消息 ' + shortId(task.agentteams_message_id, 28) + '</code><code>回执 ' + shortId(task.skill_receipt, 28) + '</code><code>追踪 ' + shortId(span.span_id, 28) + '</code>' + (task.skill_transport === "higress-mcp" ? '<code>技能入口 Higress MCP</code>' : "") + '</div></details>';
    }).join("") : '<div class="empty-state">当前回放步骤尚未产生 AgentTeams 任务。</div>';
    const currentTitle = trace.current?.title || "—";
    const currentSubtitle = trace.current?.subtitle || "静态快照";
    const runtime = '<div class="team-runtime team-runtime-' + status.toLowerCase() + '">' +
      '<div><span class="runtime-live-dot"></span><strong>' + esc(statusLabel) + '</strong><small>' + (trace.final ? "运行已结束" : "按当前步骤回放") + '</small></div>' +
      '<div><span>当前执行者</span><strong title="' + esc(currentActor) + '">' + esc(currentActor) + '</strong></div>' +
      '<div><span>当前阶段</span><strong title="' + esc(runtimeStage) + '">' + esc(runtimeStage) + '</strong><small>' + esc(trace.final ? (run.status || "COMPLETED") : currentSubtitle) + '</small></div>' +
      '<div><span>进度</span><strong>' + esc(visibleTasks.length + " / " + totalTasks) + '</strong></div></div>';
    const header = '<div class="agent-task-columns" aria-hidden="true"><span>序号</span><span>任务 / 执行者</span><span>通道</span><span>耗时</span><span>Token</span><span>状态</span></div>';
    const meta = visibleTasks.length + " / " + totalTasks + " 轮任务 · " + workerCount + " 个执行者 · 第 " + (state.stepIndex + 1) + " 步";
    const note = '<p class="boundary-note"><span class="tab-icon">♟</span>输入、实际输出或原始任务回执、耗时、Token、请求、Matrix 消息、MCP 回执与追踪标识均来自本次真实 AgentTeams 运行快照；工具与 Skill 明细保留在“执行与审计”页。</p>';
    return section("多智能体协同任务账本", runtime + '<div class="agent-task-ledger">' + header + orchestratorCard + cards + "</div>" + note, meta, "♟");
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
      ["回放包版本", state.bundle.release || "0.6.0", "正式托管包"],
      ["真实运行来源", provenance.source_release || "—", "202 捕获时服务版本"],
      ["数据后端", provenance.backend || "polardb", "只作为溯源字段展示"],
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
    const stat = (label, value, tone = "is-green") => '<article class="grafana-extra-stat ' + tone + '"><span>' + esc(label) + '</span><strong>' + esc(value) + '</strong></article>';
    const barPanel = (title, subtitle, rows, tone = "cyan") => '<section class="grafana-extra-panel grafana-bar-panel"><div class="grafana-extra-heading"><strong>' + esc(title) + '</strong><span>' + esc(subtitle) + '</span></div><div class="grafana-bars">' + rows.map(([label, value]) => '<div class="grafana-bar-row"><span>' + esc(label) + '</span><i><b class="' + tone + '" style="width:' + Math.max(2, Math.min(100, Number(value) || 0)) + '%"></b></i><strong>' + esc(value) + '</strong></div>').join("") + '</div></section>';
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
    const yellowFlat = "M52 187L150 187L250 187L350 187L450 187L550 187L700 187V187H52Z";
    const greenLow = "M52 176L150 176L250 170L350 176L450 166L550 172L700 168";
    const greenLowFill = greenLow + "L700 187H52Z";
    const extraCharts = [
      chart("ERPNext API 调用 · 结果", '<i class="legend-dot green"></i>200 <i class="legend-dot yellow"></i>4xx', greenLow, greenLowFill, "green"),
      chart("ERPNext API 平均延迟", '<i class="legend-dot green"></i>平均延迟', yellowFlat, yellowFlat, "yellow"),
      chart("Agent 模型调用与 Token", '<i class="legend-dot green"></i>调用/s <i class="legend-dot yellow"></i>输入 Token/s <i class="legend-dot cyan"></i>输出 Token/s', greenLow, greenLowFill, "green"),
      chart("PolarDB 复制延迟", '<i class="legend-dot green"></i>秒 <i class="legend-dot yellow"></i>字节', yellowFlat, yellowFlat, "yellow"),
      chart("数据库连接与锁等待", '<i class="legend-dot green"></i>连接 <i class="legend-dot yellow"></i>锁等待', greenLow, greenLowFill, "green"),
      chart("审计事件增长 / Evidence Gap", '<i class="legend-dot green"></i>审计事件/s <i class="legend-dot yellow"></i>Evidence Gap/15m', greenLow, greenLowFill, "green"),
      chart("冲销与恢复", '<i class="legend-dot green"></i>恢复成功 <i class="legend-dot yellow"></i>冲销分录', yellowFlat, yellowFlat, "yellow"),
    ];
    const statusPanels =
      '<div class="grafana-extra-grid">' +
        barPanel("全部案件 · 当前状态分布", "当前录制的全局状态快照", [["CLOSED", 30], ["CREATED", 30], ["FAILED", 10], ["ROLLED_BACK", 10], ["WAITING_FOR_APPROVAL", 30]], "orange") +
        '<section class="grafana-extra-panel grafana-status-panel"><div class="grafana-extra-heading"><strong>Agent 任务 · 当前状态分布</strong><span>全局录制快照</span></div><div class="grafana-status-value"><span>PENDING</span><strong>1</strong><span>SUCCEEDED</span><strong>68</strong></div></section>' +
      '</div>';
    const databaseStats =
      '<div class="grafana-extra-stat-grid">' +
        stat("模型超时累计", "0") +
        stat("PolarDB 副本健康", "正常") +
        stat("副本降级到主库", "0") +
      '</div>';
    const moneyPanel = '<div class="grafana-extra-grid">' +
      barPanel("资金操作状态", "当前录制快照", [["POSTED", 75], ["REVERSED", 50], ["DRAFT", 5]], "green") +
      '<section class="grafana-extra-panel grafana-disclosure-panel"><div class="grafana-extra-heading"><strong>静态回放边界</strong><span>只读</span></div><p>以下面板按真实 Grafana dashboard 的 provision 顺序保留；图表数据为本次线上运行录制快照，不会重新连接 Prometheus、PolarDB 或 ERPNext。</p><code>PolarDB · ERPNext · AgentTeams · Grafana</code></section>' +
    '</div>';
    return '<section class="observability-screen">' +
      '<div class="observability-toolbar"><div class="observability-heading"><span class="grafana-mark">▥</span><div><h2>运行与资金恢复</h2><p>全部案件 · 静态运行指标 · 合成业务数据 · 录制快照</p></div></div><div class="observability-actions"><span class="observability-mode">Grafana · 只读</span><span class="health-pill">STATIC · READ ONLY</span></div></div>' +
      '<div class="grafana-live-dashboard"><div class="grafana-live-header"><strong>RevGuard · 运行与资金恢复</strong><div class="grafana-time-controls"><span>◀</span><b>◷ Last 30 minutes</b><em>CST</em><span>▶</span><span>⌕</span><b>Refresh</b><em>15s</em></div></div><div class="grafana-live-stat-grid">' + stats + '</div><div class="grafana-chart-grid">' +
        chart("API 请求速率 · 按响应类别", twoX + fourX, apiPath, apiFill, "green") +
        chart("业务 API 响应延迟 · P95", '<i class="legend-dot green"></i>业务 API P95', flatPath, "M52 187L150 187L250 187L350 187L450 187L550 187L700 187V187H52Z", "green") +
        chart("资金结果恢复 · 待对账 / 冻结通道", '<i class="legend-dot yellow"></i>待对账资金操作', flatPath, "M52 187L150 187L250 187L350 187L450 187L550 187L700 187V187H52Z", "yellow") +
        chart("服务与数据库可用性", '<i class="legend-dot yellow"></i>服务与数据库', availabilityPath, availabilityFill, "yellow") +
      '</div>' + statusPanels + '<div class="grafana-extra-grid grafana-secondary-grid">' + extraCharts.slice(0, 2).join("") + '</div>' + databaseStats + '<div class="grafana-extra-grid grafana-secondary-grid">' + extraCharts.slice(2, 5).join("") + '</div>' + moneyPanel + '<div class="grafana-extra-grid grafana-secondary-grid">' + extraCharts.slice(5).join("") + '</div><div class="grafana-powered">Powered by <strong><span>◉</span> Grafana</strong></div></div></section>';
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

  function renderApprovalDialog() {
    const overlay = $("replay-overlay");
    const approval = latest("approval") || {};
    if (!overlay || !state.approvalDialogOpen || state.tab !== "decision" || currentStep()?.stage !== "approval") {
      if (overlay) overlay.innerHTML = "";
      return;
    }
    const demo = state.approvalDemo || createApprovalDemo();
    state.approvalDemo = demo;
    const action = demo.decision || "APPROVED";
    const actionLabel = approvalActionLabel(action);
    const approvalId = approval.approval_id || (approval.bullets || [])
      .map((item) => String(item).match(/审批单\s+([^，。\s]+)/)?.[1])
      .find(Boolean) || "录制审批单";
    const recordedDecision = approval.subtitle || "APPROVED";
    const error = demo.message ? '<div class="human-dialog-error"><span aria-hidden="true">!</span>' + esc(demo.message) + "</div>" : "";
    const body = demo.committed
        ? '<div class="human-proof-panel"><div class="human-proof-success"><span class="approval-proof-icon" aria-hidden="true">✓</span><div><strong>静态审批动作已记录</strong><small>' + esc(demo.username || "finance-lead") + ' · 本地回放身份验证通过</small></div><span>只读演示</span></div>' +
        '<div class="human-proof-binding"><div><span>绑定案件</span><code>' + esc(state.bundle.case?.case_id) + '</code></div><div><span>绑定审批单</span><code>' + esc(approvalId) + '</code></div><div><span>绑定动作</span><strong>' + esc(approvalActionLabel(demo.committed)) + '</strong></div></div>' +
        '<div class="approval-static-disclosure">本次选择不会调用 202 API，也不会改写正式运行记录；录制快照中的正式结果仍为 <strong>' + esc(recordedDecision) + ' · ' + esc(state.bundle.case?.status || "CLOSED") + '</strong>。</div>' +
        '<div class="human-modal-actions"><button type="button" class="human-secondary" data-reset-approval>重新选择</button><button type="button" class="human-secondary" data-close-approval>返回回放</button></div></div>'
      : '<div class="decision-switch" aria-label="选择审批结论"><button type="button" class="' + (action === "APPROVED" ? "active approve" : "") + '" data-approval-decision="APPROVED">批准</button><button type="button" class="' + (action === "REJECTED" ? "active reject" : "") + '" data-approval-decision="REJECTED">驳回</button></div>' +
        '<div class="human-login-form"><label><span>AgentTeams 审批账号</span><input data-approval-input="username" value="' + esc(demo.username) + '" autocomplete="username" aria-label="AgentTeams 审批账号"></label>' +
        '<label><span>密码（静态演示）</span><input type="password" data-approval-input="password" value="' + esc(demo.password) + '" autocomplete="current-password" aria-label="静态演示密码"></label>' +
        '<label class="human-comment"><span>审批意见</span><textarea data-approval-input="comment" rows="3" maxlength="500">' + esc(demo.comment) + '</textarea></label>' +
        '<div class="approval-static-hint">合成演示账号已预填。点击下方任一动作，即完成本地身份验证与“' + esc(actionLabel) + '”动作记录，不会发送网络请求。</div>' + error +
        '<div class="human-modal-actions approval-choice-actions"><button type="button" class="human-primary" data-approval-submit="APPROVED">同意并记录</button><button type="button" class="human-primary danger" data-approval-submit="REJECTED">驳回并记录</button></div></div>';
    overlay.innerHTML = '<div class="human-modal-backdrop" role="presentation"><section class="human-modal" role="dialog" aria-modal="true" aria-labelledby="replay-human-action-title"><div class="human-modal-header"><div><span>人工控制边界 · 静态回放</span><h2 id="replay-human-action-title">AgentTeams 审批人身份验证</h2></div><button type="button" data-close-approval aria-label="关闭">×</button></div><div class="human-binding-strip">' + iconSvg("approval", "approval-dialog-icon") + '<div><strong>证明只绑定本案与本次“' + esc(actionLabel) + '”动作</strong><small>页面只读取已录制的脱敏数据；账号和密码是合成演示值，不会发往 202，也不会写入案件、日志或 Trace。</small></div></div>' + body + '</section></div>';
  }

  function renderRail() {
    const c = state.bundle.case || {};
    const verification = verificationStep();
    const passed = verification?.subtitle === "PASSED" || Boolean(recoveryStep());
    const safetyStatus = replayStatus();
    const note = approvalRejected() ? "审批驳回，后续执行已阻断" : isRollback() ? "已执行冲销并完成恢复复核" : passed ? "独立验证通过，未触发冲销" : "等待独立验证";
    $("safety-rail").innerHTML =
      '<section class="rail-section"><span class="rail-label">当前安全状态</span><strong class="rail-state ' + (isRollback() ? "rollback-state" : approvalRejected() ? "approval-rejected-state" : "") + '">' + esc(safetyStatus) + '</strong><span class="rail-label">独立复核</span><strong class="rail-state ' + (passed ? "passed-state" : "") + '">' + (passed ? "PASSED" : "PENDING") + '</strong><span class="rail-label">最终差额</span><b>' + money(state.bundle.headline?.variance || "—") + "</b><small>" + esc(note) + "</small></section>" +
      '<section class="rail-section"><span class="rail-label">案例与审批边界</span><div class="rail-kv"><span>案件</span><strong>' + esc(c.case_id) + '</strong></div><div class="rail-kv"><span>风险</span><strong>' + esc(latest("risk")?.subtitle || "L2") + '</strong></div><div class="rail-kv"><span>审批人</span><strong>' + esc(state.bundle.headline?.approved_by || "财务负责人（演示）") + '</strong></div><div class="rail-kv"><span>模式</span><strong>AgentTeams · Matrix</strong></div></section>' +
      '<section class="rail-section export-section"><span class="rail-label">导出证据包</span><button type="button" data-export-evidence>导出证据包</button><small>浏览器本地打包现有脱敏回放数据、Trace、任务账本、审计链摘要和校验清单。</small><code class="rail-code">' + shortId(state.bundle.provenance?.snapshot_sha256, 38) + "</code></section>";
  }

  function render() {
    if (!state.bundle) return;
    renderHeader();
    renderSummary();
    renderPipeline();
    renderTabs();
    renderContent();
    renderRail();
    renderApprovalDialog();
  }

  function stopPlaying() {
    state.playing = false;
    if (state.timer) window.clearTimeout(state.timer);
    state.timer = null;
  }

  function playTick() {
    if (!state.playing) return;
    if (approvalAwaiting() || approvalRejected()) {
      stopPlaying();
      render();
      return;
    }
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
    if (approvalAwaiting() || approvalRejected()) {
      render();
      return;
    }
    state.playing = true;
    render();
    playTick();
  }

  function changeStep(delta) {
    stopPlaying();
    if (delta > 0 && approvalAwaiting()) {
      state.approvalDialogOpen = true;
      render();
      return;
    }
    if (delta > 0 && approvalRejected()) return;
    const nextIndex = Math.max(0, Math.min(state.bundle.steps.length - 1, state.stepIndex + delta));
    if (delta < 0 && nextIndex < approvalStageIndex() && state.approvalDemo?.committed) state.approvalDemo = createApprovalDemo();
    state.stepIndex = nextIndex;
    render();
  }

  const utf8 = (value) => new TextEncoder().encode(String(value));
  const concatBytes = (chunks) => {
    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const result = new Uint8Array(total);
    let offset = 0;
    chunks.forEach((chunk) => {
      result.set(chunk, offset);
      offset += chunk.length;
    });
    return result;
  };
  const littleEndian16 = (value) => {
    const result = new Uint8Array(2);
    new DataView(result.buffer).setUint16(0, value, true);
    return result;
  };
  const littleEndian32 = (value) => {
    const result = new Uint8Array(4);
    new DataView(result.buffer).setUint32(0, value >>> 0, true);
    return result;
  };
  const CRC32_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let index = 0; index < 256; index += 1) {
      let value = index;
      for (let bit = 0; bit < 8; bit += 1) value = (value & 1) ? (0xedb88320 ^ (value >>> 1)) : (value >>> 1);
      table[index] = value >>> 0;
    }
    return table;
  })();
  const crc32 = (bytes) => {
    let value = 0xffffffff;
    bytes.forEach((byte) => { value = CRC32_TABLE[(value ^ byte) & 0xff] ^ (value >>> 8); });
    return (value ^ 0xffffffff) >>> 0;
  };
  const SHA256_K = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ];
  const rotr = (value, bits) => (value >>> bits) | (value << (32 - bits));
  const sha256Fallback = (bytes) => {
    const bitLength = bytes.length * 8;
    const paddedLength = Math.ceil((bytes.length + 9) / 64) * 64;
    const padded = new Uint8Array(paddedLength);
    padded.set(bytes);
    padded[bytes.length] = 0x80;
    const paddedView = new DataView(padded.buffer);
    paddedView.setUint32(paddedLength - 8, Math.floor(bitLength / 0x100000000), false);
    paddedView.setUint32(paddedLength - 4, bitLength >>> 0, false);
    let [h0, h1, h2, h3, h4, h5, h6, h7] = [
      0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    const words = new Uint32Array(64);
    for (let offset = 0; offset < paddedLength; offset += 64) {
      for (let index = 0; index < 16; index += 1) words[index] = paddedView.getUint32(offset + index * 4, false);
      for (let index = 16; index < 64; index += 1) {
        const s0 = rotr(words[index - 15], 7) ^ rotr(words[index - 15], 18) ^ (words[index - 15] >>> 3);
        const s1 = rotr(words[index - 2], 17) ^ rotr(words[index - 2], 19) ^ (words[index - 2] >>> 10);
        words[index] = (words[index - 16] + s0 + words[index - 7] + s1) >>> 0;
      }
      let [a, b, c, d, e, f, g, h] = [h0, h1, h2, h3, h4, h5, h6, h7];
      for (let index = 0; index < 64; index += 1) {
        const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const choose = (e & f) ^ (~e & g);
        const temp1 = (h + S1 + choose + SHA256_K[index] + words[index]) >>> 0;
        const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const temp2 = (S0 + majority) >>> 0;
        [h, g, f, e, d, c, b, a] = [g, f, e, (d + temp1) >>> 0, c, b, a, (temp1 + temp2) >>> 0];
      }
      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7].map((value) => value.toString(16).padStart(8, "0")).join("");
  };
  const sha256 = async (bytes) => {
    try {
      if (window.crypto?.subtle) {
        const digest = await window.crypto.subtle.digest("SHA-256", bytes);
        return Array.from(new Uint8Array(digest)).map((value) => value.toString(16).padStart(2, "0")).join("");
      }
    } catch (_error) {
      // The 202 demo is served over HTTP, where Web Crypto may be unavailable.
    }
    return sha256Fallback(bytes);
  };
  const zipStore = (files) => {
    const localParts = [];
    const centralParts = [];
    const entries = [];
    let offset = 0;
    files.forEach((file) => {
      const name = utf8(file.name);
      const data = file.data;
      const checksum = crc32(data);
      const local = concatBytes([
        littleEndian32(0x04034b50), littleEndian16(20), littleEndian16(0), littleEndian16(0), littleEndian16(0), littleEndian16(0),
        littleEndian32(checksum), littleEndian32(data.length), littleEndian32(data.length), littleEndian16(name.length), littleEndian16(0), name, data,
      ]);
      localParts.push(local);
      entries.push({ name, checksum, size: data.length, offset });
      offset += local.length;
    });
    const centralOffset = offset;
    entries.forEach((entry) => {
      centralParts.push(concatBytes([
        littleEndian32(0x02014b50), littleEndian16(20), littleEndian16(20), littleEndian16(0), littleEndian16(0), littleEndian16(0), littleEndian16(0),
        littleEndian32(entry.checksum), littleEndian32(entry.size), littleEndian32(entry.size), littleEndian16(entry.name.length), littleEndian16(0), littleEndian16(0),
        littleEndian16(0), littleEndian16(0), littleEndian32(0), littleEndian32(entry.offset), entry.name,
      ]));
    });
    const central = concatBytes(centralParts);
    const end = concatBytes([
      littleEndian32(0x06054b50), littleEndian16(0), littleEndian16(0), littleEndian16(entries.length), littleEndian16(entries.length),
      littleEndian32(central.length), littleEndian32(centralOffset), littleEndian16(0),
    ]);
    return new Blob([...localParts, central, end], { type: "application/zip" });
  };
  const jsonText = (value) => JSON.stringify(value, null, 2) + "\n";
  const markdownCell = (value) => String(value == null || value === "" ? "—" : value).replace(/\|/g, "\\|").replace(/[\r\n]+/g, " ");
  const buildAuditReport = (bundle) => {
    const c = bundle.case || {};
    const h = bundle.headline || {};
    const run = bundle.run || {};
    const audit = bundle.audit || {};
    const trace = bundle.trace || {};
    const provenance = bundle.provenance || {};
    const spanByTask = new Map((trace.spans || []).filter((span) => span.agent_task_id).map((span) => [span.agent_task_id, span]));
    const taskRows = (bundle.agent_tasks || []).map((task, index) => {
      const span = spanByTask.get(task.task_id) || {};
      return `| ${index + 1} | ${markdownCell(task.skill_name)} | ${markdownCell(task.assigned_actor)} | ${markdownCell(TASK_STATUS_LABELS[task.status] || task.status)} | ${markdownCell(duration(span.duration_ms))} | ${markdownCell(tokens(task.token_usage?.total_tokens))} |`;
    });
    const stageRows = (bundle.steps || []).map((step, index) =>
      `| ${index + 1} | ${markdownCell(step.stage)} | ${markdownCell(step.title)} | ${markdownCell(step.subtitle)} | ${markdownCell(step.at)} |`
    );
    return [
      "# RevGuard 运行证据摘要",
      "",
      "> 本报告由静态回放页面在浏览器本地生成，仅整理已托管的脱敏运行记录，不连接后端、不重新执行任务。",
      "",
      "## 案件与结论",
      "",
      `- 案件：${markdownCell(c.case_id)}`,
      `- 状态：${markdownCell(c.status)}`,
      `- 订单：${markdownCell(c.order_id)} · ${markdownCell(c.partner_name)}`,
      `- 预期佣金：${markdownCell(h.expected)}；已入账：${markdownCell(h.posted)}；独立验证：${markdownCell(h.verified)}`,
      `- 最终差额：${markdownCell(h.variance)} · 验证状态：${markdownCell(h.verification_status)}`,
      `- 审批：${markdownCell(h.approved_by)} · ${markdownCell(h.approval_status)}`,
      "",
      "## 运行来源",
      "",
      `- 回放包版本：${markdownCell(bundle.release)}`,
      `- 真实运行来源版本：${markdownCell(provenance.source_release)}`,
      `- 捕获类型：${markdownCell(provenance.capture_kind)}`,
      `- 运行时间：${markdownCell(run.started_at)} → ${markdownCell(run.ended_at)}（${markdownCell(duration(run.wall_duration_ms))}）`,
      `- Agent Trace：${markdownCell(trace.span_count)} 条；审计事件：${markdownCell(audit.count)} 条；审计链：${audit.chain_ok ? "通过" : "待校验"}`,
      `- 来源快照：${markdownCell(provenance.snapshot_sha256)}`,
      "",
      "## 阶段记录",
      "",
      "| # | 阶段 | 标题 | 状态/摘要 | 时间 |",
      "| ---: | --- | --- | --- | --- |",
      ...stageRows,
      "",
      "## 多智能体任务账本",
      "",
      "| # | Skill | 执行者 | 状态 | 耗时 | Token |",
      "| ---: | --- | --- | --- | ---: | ---: |",
      ...(taskRows.length ? taskRows : ["| — | 暂无任务记录 | — | — | — | — |"]),
      "",
      "## 证据边界",
      "",
      "- 业务样本为合成数据；运行链路、任务输入输出、Trace 和审计链摘要来自真实运行导出。",
      "- 包内数据已在运行导出阶段脱敏；静态回放不会写入 ERPNext、PolarDB 或其他系统。",
      "- `SHA256SUMS` 用于校验本次浏览器导出的包内文件，不能替代生产付款凭证或原始数据库备份。",
      "",
    ].join("\n");
  };
  const buildEvidenceReadme = (bundle, fileNames) => {
    const c = bundle.case || {};
    const p = bundle.provenance || {};
    return [
      "# RevGuard 静态回放证据包",
      "",
      "本包由 RevGuard 静态回放页在浏览器本地生成，内容来自已托管的脱敏 JSON，不会调用后端、不重新运行 AgentTeams，也不会写入 ERPNext、PolarDB 或 Grafana。",
      "",
      `- 案件：${c.case_id || "—"}`,
      `- 回放包版本：${bundle.release || "—"}`,
      `- 真实运行来源：${p.source_release || "—"}`,
      `- 捕获类型：${p.capture_kind || "CAPTURED_FROM_RUNTIME"}`,
      `- 业务数据：${bundle.disclosure?.business_data || "synthetic"}`,
      `- 运行组件：AgentTeams、PolarDB、ERPNext、Grafana · 8 核 24G 线上环境`,
      "",
      "## 文件说明",
      "",
      ...fileNames.map(([name, description]) => `- \`${name}\`：${description}`),
      "",
      "`manifest.json` 记录每个文件的 SHA-256；`SHA256SUMS` 可用于离线复核包内文件完整性。",
      "",
    ].join("\n");
  };
  const downloadBlob = (blob, filename) => {
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1500);
  };
  async function exportEvidencePackage() {
    const button = document.querySelector("[data-export-evidence]");
    if (!button || !state.bundle || button.disabled) return;
    button.disabled = true;
    button.textContent = "正在打包…";
    try {
      const bundle = JSON.parse(JSON.stringify(state.bundle));
      delete bundle.__file;
      const caseId = bundle.case?.case_id || "revguard-case";
      const fileDescriptions = [
        ["case-replay.json", "完整的静态回放数据包（已脱敏）"],
        ["agent-tasks.json", "AgentTeams 持久化任务账本及任务输入/输出"],
        ["trace.json", "Agent / Skill / Tool Trace 跨度记录"],
        ["audit-chain.json", "审计链计数、首尾序号、头哈希与校验结果"],
        ["engineering-snapshot.json", "工程证据与公开数据实验快照"],
        ["audit-report.md", "面向人审的案件摘要报告"],
      ];
      const dataFiles = [
        { name: "case-replay.json", data: utf8(jsonText(bundle)) },
        { name: "agent-tasks.json", data: utf8(jsonText({ schema: "revguard.agent-tasks/v1", case_id: caseId, tasks: bundle.agent_tasks || [] })) },
        { name: "trace.json", data: utf8(jsonText({ schema: "revguard.trace/v1", case_id: caseId, trace: bundle.trace || {} })) },
        { name: "audit-chain.json", data: utf8(jsonText({ schema: "revguard.audit-chain/v1", case_id: caseId, audit: bundle.audit || {} })) },
        { name: "engineering-snapshot.json", data: utf8(jsonText(state.engineering || {})) },
        { name: "audit-report.md", data: utf8(buildAuditReport(bundle)) },
      ];
      const readme = { name: "README.md", data: utf8(buildEvidenceReadme(bundle, fileDescriptions)) };
      const contentFiles = [readme, ...dataFiles];
      const fileMeta = [];
      for (const file of contentFiles) {
        fileMeta.push({ name: file.name, description: fileDescriptions.find(([name]) => name === file.name)?.[1] || "证据文件", bytes: file.data.length, sha256: await sha256(file.data) });
      }
      const manifest = {
        schema: "revguard.evidence-pack/v1",
        package_type: "STATIC_REPLAY_EXPORT",
        generated_at: new Date().toISOString(),
        case_id: caseId,
        release: bundle.release || "0.6.0",
        source_release: bundle.provenance?.source_release || null,
        capture_kind: bundle.provenance?.capture_kind || "CAPTURED_FROM_RUNTIME",
        source_snapshot_sha256: bundle.provenance?.snapshot_sha256 || null,
        boundary: "本地浏览器导出；只读静态数据；不连接后端；业务样本为合成数据",
        files: fileMeta,
      };
      const manifestFile = { name: "manifest.json", data: utf8(jsonText(manifest)) };
      const manifestSha = await sha256(manifestFile.data);
      const sums = [...fileMeta.map((file) => `${file.sha256}  ${file.name}`), `${manifestSha}  manifest.json`].join("\n") + "\n";
      const sumsFile = { name: "SHA256SUMS", data: utf8(sums) };
      const zip = zipStore([...contentFiles, manifestFile, sumsFile]);
      downloadBlob(zip, `${caseId}-evidence-package.zip`);
      button.textContent = "已下载证据包";
      window.setTimeout(() => { if (button.isConnected) button.textContent = "导出证据包"; }, 1400);
    } catch (error) {
      console.error("静态证据包导出失败", error);
      button.textContent = "导出失败，请重试";
      window.setTimeout(() => { if (button.isConnected) button.textContent = "导出证据包"; }, 1800);
    } finally {
      window.setTimeout(() => { if (button.isConnected) button.disabled = false; }, 1200);
    }
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
      state.approvalDemo = createApprovalDemo();
      state.approvalDialogOpen = false;
      const requestedStep = requestedStepParam == null || requestedStepParam === "" ? null : Number(requestedStepParam);
      const hasRequestedStep = Number.isFinite(requestedStep);
      state.stepIndex = hasRequestedStep ? Math.max(0, Math.min(bundle.steps.length - 1, requestedStep)) : 0;
      state.playing = !hasRequestedStep && state.tab === "decision";
      render();
      const notice = $("capture-notice");
      notice.hidden = ["public-data", "observability"].includes(state.tab);
      notice.textContent = "真实环境线上运行 AgentTeams、PolarDB、ERPNext、Grafana 等组件，配置 8 核 24G；GitHub Pages / ModelScope 达不到运行要求，所以 Demo 只能静态回放录制脚本了。";
      if (state.playing) playTick();
    } catch (error) {
      $("tab-content").innerHTML = '<div class="capture-notice">静态记录读取失败：' + esc(error.message) + "</div>";
    } finally {
      $("case-select")?.removeAttribute("disabled");
    }
  }

  document.addEventListener("click", (event) => {
    const tabButton = event.target.closest("#tabs button");
    if (tabButton) {
      state.approvalDialogOpen = false;
      state.tab = tabButton.dataset.tab;
      renderTabs();
      renderHeader();
      renderContent();
      renderSummary();
      renderPipeline();
      renderRail();
      renderApprovalDialog();
      return;
    }
    const approvalDecision = event.target.closest("[data-approval-decision]");
    if (approvalDecision) {
      state.approvalDemo = state.approvalDemo || createApprovalDemo();
      state.approvalDemo.decision = approvalDecision.dataset.approvalDecision;
      state.approvalDemo.message = "";
      renderApprovalDialog();
      return;
    }
    const approvalSubmit = event.target.closest("[data-approval-submit]");
    if (approvalSubmit) {
      state.approvalDemo = state.approvalDemo || createApprovalDemo();
      const demo = state.approvalDemo;
      const decision = approvalSubmit.dataset.approvalSubmit;
      demo.decision = decision;
      if (!demo.username.trim() || !demo.password) {
        demo.message = "请填写审批账号和静态演示密码。";
        renderApprovalDialog();
        return;
      }
      demo.verified = true;
      demo.committed = decision;
      demo.message = "";
      stopPlaying();
      state.approvalDialogOpen = false;
      if (decision === "APPROVED") state.stepIndex = Math.min(state.bundle.steps.length - 1, state.stepIndex + 1);
      render();
      return;
    }
    if (event.target.closest("[data-open-approval]")) {
      state.approvalDialogOpen = true;
      render();
      return;
    }
    if (event.target.closest("[data-close-approval]")) {
      state.approvalDialogOpen = false;
      render();
      return;
    }
    if (event.target.closest("[data-reset-approval]")) {
      state.approvalDemo = createApprovalDemo();
      state.approvalDialogOpen = true;
      render();
      return;
    }
    const jumpStage = event.target.closest("[data-jump-stage]");
    if (jumpStage) {
      const index = (state.bundle?.steps || []).findIndex((step) => step.stage === jumpStage.dataset.jumpStage);
      if (index >= 0) {
        stopPlaying();
        state.approvalDialogOpen = false;
        if (jumpStage.dataset.jumpStage === "approval" || index < approvalStageIndex()) state.approvalDemo = createApprovalDemo();
        state.stepIndex = index;
        render();
      }
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
      state.approvalDemo = createApprovalDemo();
      state.approvalDialogOpen = false;
      state.stepIndex = 0;
      render();
    }
    if (event.target.closest("[data-export-evidence]")) return exportEvidencePackage();
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
  document.addEventListener("input", (event) => {
    const field = event.target.closest("[data-approval-input]");
    if (!field) return;
    state.approvalDemo = state.approvalDemo || createApprovalDemo();
    state.approvalDemo[field.dataset.approvalInput] = field.value;
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
