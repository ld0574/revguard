import assert from "node:assert/strict";
import test from "node:test";
import { isVerifiedClosure } from "../src/pipeline-state.js";

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
