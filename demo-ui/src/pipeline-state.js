// A completed write is distinct from draft-only closure and safe rejection.
export function isVerifiedClosure(snapshot) {
  return snapshot?.case?.status === "CLOSED"
    && snapshot?.verification?.verification_status === "PASSED"
    && (snapshot?.executions || []).some(
      (item) => item.action_type !== "DRAFT" && item.status !== "DRAFT",
    );
}
