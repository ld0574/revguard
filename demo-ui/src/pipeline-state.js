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
