import assert from "node:assert/strict";
import test from "node:test";
import {
  externalValidationLabel,
  formatBeijingDateTime,
  formatTaskDuration,
  formatTaskTokens,
  isVerifiedClosure,
  safetyRailState,
  securityRegressionSummary,
  taskTelemetry,
  taskTokenCount,
} from "../src/pipeline-state.js";

const completed = {
  case: { status: "CLOSED" },
  verification: { verification_status: "PASSED" },
  executions: [{ action_type: "ADJUSTMENT", status: "POSTED", amount: "5400" }],
};

test("completed verified writes do not keep waiting for rollback", () => {
  assert.equal(isVerifiedClosure(completed), true);
});

test("rollback, pending, draft-only and rejected writes are not normal closures", () => {
  for (const status of ["CREATED", "EXECUTING", "VERIFYING", "ROLLED_BACK", "FAILED", "REJECTED"]) {
    assert.equal(isVerifiedClosure({ ...completed, case: { status } }), false);
  }
  for (const verification_status of ["FAILED", "NOT_APPLICABLE_DRAFT_ONLY", undefined]) {
    assert.equal(isVerifiedClosure({ ...completed, verification: { verification_status } }), false);
  }
  assert.equal(isVerifiedClosure({ ...completed, executions: [] }), false);
  assert.equal(isVerifiedClosure({ ...completed, executions: [{ action_type: "DRAFT" }] }), false);
  assert.equal(isVerifiedClosure({ ...completed, executions: [{ status: "DRAFT" }] }), false);
  assert.equal(isVerifiedClosure(null), false);
});

test("safety rail distinguishes measured balances from original baselines", () => {
  const clean = safetyRailState({ ...completed, verification: { ...completed.verification, actual_amount: "32400.00" } });
  assert.equal(clean.result, "已通过，无需回滚");
  assert.equal(clean.amount, "32400.00");
  const original = { case: { status: "CREATED", claim: { actual_amount: 18000 } } };
  assert.equal(safetyRailState(original).balanceLabel, "原始台账基线");
  assert.equal(safetyRailState(original).passed, false);
  const rolled = { ...original, case: { ...original.case, status: "ROLLED_BACK" } };
  assert.equal(safetyRailState(rolled).amount, null);
  assert.equal(safetyRailState(rolled).passed, false);
  const proof = { event: "ROLLBACK_VERIFIED", detail: { verification_status: "PASSED", component_checks: [{ actual: "15000" }, { actual: "0" }] } };
  assert.equal(safetyRailState({ ...rolled, audit_events: [proof] }).amount, 15000);
  assert.equal(safetyRailState({ ...rolled, audit_events: [proof] }).passed, true);
  assert.equal(safetyRailState({ ...rolled, audit_events: [{ ...proof, detail: { ...proof.detail, component_checks: [{ actual: null }] } }] }).amount, null);
});

test("security regression never defaults to a fabricated pass", () => {
  assert.equal(securityRegressionSummary(null).passed, false);
  assert.equal(securityRegressionSummary({ categories: {} }).label, "未加载回归证据");
  const evaluation = { categories: { security_probes: { scenarios: 9, passed: 9, failures: [] } } };
  assert.equal(securityRegressionSummary(evaluation).passed, true);
  evaluation.categories.security_probes.failures = ["cross-case"];
  assert.equal(securityRegressionSummary(evaluation).passed, false);
  evaluation.categories.security_probes = { scenarios: 0, passed: 0, failures: [] };
  assert.equal(securityRegressionSummary(evaluation).passed, false);
});

test("external validation labels separate configuration from live verification", () => {
  assert.equal(externalValidationLabel("CONFIGURED_MATRIX_NOT_LIVENESS_CHECK"), "已配置（非在线探测）");
  assert.equal(externalValidationLabel("PASSED_LOCAL_INSTANCE"), "本机实例已验收");
  assert.equal(externalValidationLabel("PENDING_CLOUD_INSTANCE"), "待云端实例验收");
  assert.equal(externalValidationLabel("UNKNOWN"), "待核实");
  assert.equal(externalValidationLabel(undefined), "待核实");
});

test("task telemetry uses Agent Trace duration and never invents token usage", () => {
  const tasks = [
    { task_id: "TASK-1", skill_name: "EvidenceCollectSkill", assigned_actor: "revguard-evidence", created_at: "2026-09-01T12:00:00Z" },
    { task_id: "TASK-2", skill_name: "EvidenceCollectSkill", assigned_actor: "revguard-evidence", created_at: "2026-09-01T12:01:00Z", token_usage: { prompt_tokens: 1200, completion_tokens: 34 } },
  ];
  const spans = [
    { span_id: "SPAN-2", kind: "AGENT", name: "AgentTeams.EvidenceCollectSkill", actor: "revguard-evidence", started_at: "2026-09-01T12:01:01Z", duration_ms: 2050 },
    { span_id: "SPAN-1", kind: "AGENT", name: "AgentTeams.EvidenceCollectSkill", actor: "revguard-evidence", started_at: "2026-09-01T12:00:01Z", duration_ms: 50140 },
  ];
  const telemetry = taskTelemetry(tasks, spans);
  assert.equal(telemetry["TASK-1"].duration_ms, 50140);
  assert.equal(telemetry["TASK-2"].duration_ms, 2050);
  assert.equal(formatTaskTokens(telemetry["TASK-1"].token_usage), "未采集");
  assert.equal(formatTaskTokens(telemetry["TASK-2"].token_usage), "1,234");
});

test("duration and token formatting preserve zero and reject missing data", () => {
  assert.equal(formatTaskDuration(0), "<1 ms");
  assert.equal(formatTaskDuration(3558), "3.56 s");
  assert.equal(formatTaskDuration(50140), "50.1 s");
  assert.equal(formatTaskDuration(null), "未采集");
  assert.equal(taskTokenCount({ total_tokens: 0 }), 0);
  assert.equal(taskTokenCount({ input_tokens: 800, output_tokens: 20 }), 820);
  assert.equal(taskTokenCount(null), null);
});

test("Beijing clock formats the configured timezone instead of a fixed date", () => {
  assert.equal(
    formatBeijingDateTime("2026-09-02T00:00:00Z"),
    "北京时间 · 2026-09-02 08:00:00",
  );
  assert.equal(formatBeijingDateTime("not-a-date"), "北京时间 · —");
});
