// A completed write is distinct from draft-only closure and safe rejection.
export function isVerifiedClosure(snapshot) {
  return snapshot?.case?.status === "CLOSED"
    && snapshot?.verification?.verification_status === "PASSED"
    && (snapshot?.executions || []).some(
      (item) => item.action_type !== "DRAFT" && item.status !== "DRAFT",
    );
}

export function safetyRailState(snapshot) {
  const status = snapshot?.case?.status;
  const original = snapshot?.case?.claim?.actual_amount;
  if (status === "ROLLED_BACK") {
    const proof = (snapshot?.audit_events || []).findLast(
      (event) => event.event === "ROLLBACK_VERIFIED",
    )?.detail;
    const checks = proof?.component_checks || [];
    const amounts = checks.map((item) => Number(item.actual));
    const measured = proof?.actual_amount ?? (
      checks.length && checks.every((item) => item.actual !== null && item.actual !== undefined && item.actual !== "") && amounts.every(Number.isFinite)
        ? amounts.reduce((sum, amount) => sum + amount, 0) : null
    );
    return {
      label: "回滚后验证结果", passed: proof?.verification_status === "PASSED",
      result: proof?.verification_status === "PASSED" ? "已通过" : "待核实回滚证据",
      balanceLabel: "回滚复核金额", amount: measured,
      note: "来自独立回滚复核，不使用原始金额冒充实测值",
    };
  }
  if (isVerifiedClosure(snapshot)) {
    return {
      label: "独立验证结果", passed: true, result: "已通过，无需回滚",
      balanceLabel: "验证后台账", amount: snapshot.verification.actual_amount,
      note: "与预期佣金一致，正常闭环",
    };
  }
  return {
    label: "回滚后验证结果", passed: false,
    result: status === "CLOSED" || status === "REJECTED" ? "不适用（未执行回滚）" : "尚未执行回滚复核",
    balanceLabel: "原始台账基线", amount: original,
    note: "原始金额，仅作为对照，不代表已完成恢复",
  };
}

export function securityRegressionSummary(evaluation) {
  const probes = evaluation?.categories?.security_probes;
  if (!probes || !Number.isInteger(probes.scenarios) || probes.scenarios <= 0
      || !Number.isInteger(probes.passed) || !Array.isArray(probes.failures)) {
    return { label: "未加载回归证据", passed: false };
  }
  return {
    label: `${probes.passed}/${probes.scenarios} 项通过`,
    passed: probes.passed === probes.scenarios && probes.failures.length === 0,
  };
}

export function externalValidationLabel(state) {
  const labels = {
    CONFIGURED_MATRIX_NOT_LIVENESS_CHECK: "已配置（非在线探测）",
    PENDING_COMPANY_DATA: "待接入企业真实基线",
    PENDING_EXTERNAL_CAPTURE: "待采集现场证据",
    PASSED_LOCAL_INSTANCE: "本机实例已验收",
    PENDING_DEPLOYMENT: "待部署验收",
    PENDING_CLOUD_INSTANCE: "待云端实例验收",
  };
  return labels[state] || "待核实";
}

export function formatTaskDuration(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || milliseconds === "") return "未采集";
  const value = Number(milliseconds);
  if (!Number.isFinite(value)) return "未采集";
  if (value < 1) return "<1 ms";
  if (value >= 1000) return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)} s`;
  return `${Math.round(value)} ms`;
}

export function taskTokenCount(usage) {
  if (!usage || typeof usage !== "object") return null;
  const total = Number(usage.total_tokens);
  if (Number.isFinite(total) && total >= 0) return total;
  const input = Number(usage.input_tokens ?? usage.prompt_tokens);
  const output = Number(usage.output_tokens ?? usage.completion_tokens);
  if (!Number.isFinite(input) && !Number.isFinite(output)) return null;
  return Math.max(0, Number.isFinite(input) ? input : 0)
    + Math.max(0, Number.isFinite(output) ? output : 0);
}

export function formatTaskTokens(usage) {
  const total = taskTokenCount(usage);
  return total === null ? "未采集" : Math.round(total).toLocaleString("en-US");
}

export function taskTelemetry(tasks = [], spans = []) {
  const agentSpans = spans.filter((span) => span.kind === "AGENT");
  const usedSpanIds = new Set();
  const result = {};
  for (const task of tasks) {
    const expectedName = `AgentTeams.${task.skill_name}`;
    const createdAt = new Date(task.created_at || 0).valueOf();
    const candidates = agentSpans.filter((span) => (
      !usedSpanIds.has(span.span_id)
      && span.name === expectedName
      && (!task.assigned_actor || !span.actor || span.actor === task.assigned_actor)
    ));
    const span = candidates.sort((left, right) => {
      if (!Number.isFinite(createdAt)) return Number(left.sequence || 0) - Number(right.sequence || 0);
      const leftAt = new Date(left.started_at || 0).valueOf();
      const rightAt = new Date(right.started_at || 0).valueOf();
      return Math.abs(leftAt - createdAt) - Math.abs(rightAt - createdAt);
    })[0];
    if (span?.span_id) usedSpanIds.add(span.span_id);
    const usage = task.token_usage
      || task.telemetry?.token_usage
      || span?.outputs?.token_usage
      || null;
    result[task.task_id] = {
      duration_ms: span?.duration_ms ?? task.telemetry?.duration_ms ?? null,
      duration_source: span ? "agent_trace" : task.telemetry?.duration_source || null,
      token_usage: usage,
      token_source: usage ? task.token_usage_source || task.telemetry?.token_source || "task_usage" : null,
    };
  }
  return result;
}
