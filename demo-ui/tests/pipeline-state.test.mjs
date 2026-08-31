import assert from "node:assert/strict";
import test from "node:test";
import { externalValidationLabel, isVerifiedClosure, safetyRailState, securityRegressionSummary } from "../src/pipeline-state.js";

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
