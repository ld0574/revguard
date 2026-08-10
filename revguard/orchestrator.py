"""Case 状态机编排器（Orchestrator Agent 的确定性执行体）。

职责：按设计文档第 5/11 章串联 受理→证据→政策→复算→根因→风险→审批→执行→验证→沉淀，
每个阶段：
- 对应一个职能 Agent（actor 字段），职责与写权限严格分离（设计文档 6.1）；
- 产出结构化 Artifact 写入 Shared Case State（11.3）；
- 记录 Trace span 与审计事件，失败不产出虚假成功。

复赛时 LLM Worker 通过 API 调用同一 Skill 层，编排骨架保持一致。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import skills
from .mocks import ToolError, ToolGateway
from .models import CaseStatus, new_id, utc_now
from .report import render_audit_report
from .store import Store
from .state_machine import transition_case
from .trace import Tracer
from .security import secret_fingerprint

# 证据分低于该值即挂起补证（设计文档 3.3：不生成虚假确定性结论）
EVIDENCE_SCORE_THRESHOLD = 0.6


class Orchestrator:
    """单案件端到端闭环编排。"""

    def __init__(self, store: Store, gateway: ToolGateway, *,
                 output_dir: str | Path, report_dir: str | Path,
                 approval_mode: str = "auto",
                 simulated_approver: str = "finance.lead"):
        self.store = store
        self.gateway = gateway
        self.output_dir = Path(output_dir)
        self.report_dir = Path(report_dir)
        # approval_mode: auto=演示环境由"模拟审批人"完成人工节点；wait=挂起等待 API 审批
        self.approval_mode = approval_mode
        self.simulated_approver = simulated_approver

    # ------------------------------------------------------------------ 入口
    def run_case(self, case: dict) -> dict:
        """跑完整闭环（或在审批/补证节点挂起）。返回最终 Shared State。"""
        tracer = Tracer(self.store, case["case_id"])
        state: dict = {"case_id": case["case_id"], "facts": {}, "errors": []}
        try:
            with tracer.span("AGENT", "revguard-orchestrator", actor="revguard-orchestrator",
                             inputs={"case": case.get("case_id"), "case_type": case.get("case_type")}):
                self._stage_intake(case, state, tracer)
                if case["status"] == CaseStatus.WAITING_FOR_EVIDENCE.value:
                    return self._finalize(case, state, tracer, archived=False)
                self._stage_evidence(case, state, tracer)
                if case["status"] == CaseStatus.WAITING_FOR_EVIDENCE.value:
                    return self._finalize(case, state, tracer, archived=False)
                self._stage_policy(case, state, tracer)
                self._stage_calculate(case, state, tracer)
                self._stage_root_cause(case, state, tracer)
                self._stage_risk(case, state, tracer)
                if case["status"] == CaseStatus.WAITING_FOR_APPROVAL.value:
                    if self.approval_mode == "wait":
                        return self._finalize(case, state, tracer, archived=False)
                    self._simulate_human_approval(case, state, tracer)
                if case["status"] == CaseStatus.READY_TO_EXECUTE.value:
                    self.execute_and_verify(case, state, tracer)
                return self._finalize(case, state, tracer, archived=True)
        except Exception as exc:
            # 系统级失败：明确标记 FAILED，绝不在下游产出"成功"假象
            self._transition(case, CaseStatus.FAILED, f"{type(exc).__name__}: {exc}")
            state["errors"].append(str(exc))
            self.store.audit(case["case_id"], "revguard-orchestrator", "CASE_FAILED",
                             {"error": str(exc), "type": type(exc).__name__})
            self._finalize(case, state, tracer, archived=False)
            raise

    # ------------------------------------------------------------- 1. 受理
    def _stage_intake(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Case Intake Agent：解析申诉、解析实体、判断是否可进入调查。"""
        self._transition(case, CaseStatus.NORMALIZING, "案件受理，开始实体解析")
        with tracer.span("AGENT", "revguard-intake", actor="revguard-intake"):
            with tracer.span("SKILL", "CaseNormalizeSkill", actor="revguard-intake",
                             inputs={"raw_case": case.get("case_id")}) as span:
                normalized = skills.case_normalize(case)
                span["outputs"] = normalized
            case["entities"] = normalized["entities"]
            state["normalized"] = normalized

            with tracer.span("SKILL", "EntityResolveSkill", actor="revguard-intake") as span:
                resolved = skills.entity_resolve(self.gateway, tracer,
                                                 case_id=case["case_id"],
                                                 entities=case["entities"])
                span["outputs"] = {"partner_id": resolved["partner"]["partner_id"],
                                   "resolved_by": resolved["resolved_by"]}
            partner = resolved["partner"]
            case["partner_id"] = partner["partner_id"]
            case["partner_name"] = partner.get("name")
            state["partner"] = partner
            self.store.save_case(case)

            # 工单缺订单号（设计文档 3.3）：尝试用代理商订单消歧
            if not case["entities"].get("order_id"):
                resp = skills.call_tool(self.gateway, tracer, "crm.list_orders_by_partner",
                                        {"partner_id": partner["partner_id"]},
                                        case_id=case["case_id"], actor="revguard-intake",
                                        scope=["order:read"])
                candidates = resp["data"]["orders"]
                state["order_candidates"] = [o["order_id"] for o in candidates]
                if len(candidates) == 1:
                    case["entities"]["order_id"] = candidates[0]["order_id"]
                    self.store.audit(case["case_id"], "revguard-intake", "ORDER_AUTO_RESOLVED",
                                     {"order_id": candidates[0]["order_id"]})
                    self.store.save_case(case)
                else:
                    gap = (f"工单缺少订单号，代理商 {partner['partner_id']} 名下存在 "
                           f"{len(candidates)} 笔候选订单，无法唯一确定")
                    state["errors"].append(gap)
                    self.store.audit(case["case_id"], "revguard-intake", "EVIDENCE_GAP",
                                     {"gap": gap, "candidates": state["order_candidates"]})
                    self._escalate_to_human(case, state, tracer, gap)
                    return
            case["order_id"] = case["entities"]["order_id"]
            self.store.save_case(case)

    # ------------------------------------------------------------- 2. 证据
    def _stage_evidence(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Evidence Agent：跨系统采集证据并评分，不足则挂起补证。"""
        self._transition(case, CaseStatus.EVIDENCE_COLLECTING, "开始跨系统证据采集")
        with tracer.span("AGENT", "revguard-evidence", actor="revguard-evidence"):
            with tracer.span("SKILL", "EvidenceCollectSkill", actor="revguard-evidence",
                             inputs={"order_id": case["order_id"],
                                     "partner_id": case["partner_id"]}) as span:
                package = skills.collect_evidence(
                    self.gateway, tracer, case_id=case["case_id"],
                    partner=state["partner"], order_id=case["order_id"])
                span["outputs"] = {"evidence_count": len(package["evidence"]),
                                   "evidence_score": package["evidence_score"],
                                   "gaps": package["evidence_gaps"],
                                   "parallel": package["parallel"]}
        for ev in package["evidence"]:
            self.store.save_evidence(ev)
        state["evidence"] = package["collected"]
        state["evidence_gaps"] = package["evidence_gaps"]
        case["evidence_score"] = package["evidence_score"]
        self.store.audit(case["case_id"], "revguard-evidence", "EVIDENCE_COLLECTED",
                         {"score": package["evidence_score"], "gaps": package["evidence_gaps"]})
        self.store.save_case(case)

        if package["evidence_score"] < EVIDENCE_SCORE_THRESHOLD:
            gap = f"证据完整度 {package['evidence_score']} 低于阈值 {EVIDENCE_SCORE_THRESHOLD}"
            self._escalate_to_human(case, state, tracer, gap)

    # ------------------------------------------------------------- 3. 政策
    def _stage_policy(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Policy Agent：政策版本 Time Travel + 订单时点等级回溯。"""
        self._transition(case, CaseStatus.POLICY_MATCHING, "开始政策版本匹配")
        collected = state["evidence"]
        order = collected["ORDER"]
        contract = collected["CONTRACT"]
        time_basis = (contract.get("terms") or {}).get("time_basis", "order_date")

        with tracer.span("AGENT", "revguard-policy", actor="revguard-policy"):
            facts_base = {"order_date": order["order_date"],
                          "payment_date": collected["PAYMENT_RECORD"]["payment_date"]}
            with tracer.span("SKILL", "PolicyVersionMatchSkill", actor="revguard-policy",
                             inputs={"time_basis": time_basis}) as span:
                decision = skills.policy_version_match(
                    collected["POLICY_VERSIONS"]["versions"], facts_base, time_basis)
                span["outputs"] = asdict(decision)
            state["policy_decision"] = asdict(decision)
            self.store.audit(case["case_id"], "revguard-policy", "POLICY_MATCHED",
                             {"policy_version": decision.policy_version,
                              "time_basis": decision.time_basis,
                              "excluded": decision.excluded_versions,
                              "conflicts": decision.unresolved_conflicts})

            with tracer.span("SKILL", "TierAtOrderDateSkill", actor="revguard-policy",
                             inputs={"order_date": order["order_date"]}) as span:
                tier = skills.tier_at_order_date(
                    collected["TIER_HISTORY"]["tier_history"], order["order_date"])
                span["outputs"] = tier
            state["tier_resolution"] = tier
            if tier.get("conflict"):
                # 证据冲突显式留痕（Demo 必演项）：当前等级 vs 订单时点等级
                self.store.audit(case["case_id"], "revguard-policy", "EVIDENCE_CONFLICT",
                                 {"conflict": tier["conflict"]})
        # 关键 Artifact 随案件持久化，支持审批挂起后经 API 恢复执行
        case["policy_decision"] = state["policy_decision"]
        case["tier_resolution"] = state["tier_resolution"]
        self.store.save_case(case)

    # ------------------------------------------------------------- 4. 复算
    def _stage_calculate(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Calculation Agent：组装事实并调用规则引擎（ADR-001）。"""
        self._transition(case, CaseStatus.CALCULATING, "开始确定性佣金复算")
        collected = state["evidence"]
        order = collected["ORDER"]
        payment = collected["PAYMENT_RECORD"]

        with tracer.span("AGENT", "revguard-calculation", actor="revguard-calculation"):
            # 月度完成单数（月度激励条件）需独立统计
            resp = skills.call_tool(self.gateway, tracer, "crm.list_orders_by_partner",
                                    {"partner_id": case["partner_id"]},
                                    case_id=case["case_id"], actor="revguard-calculation",
                                    scope=["order:read"])
            order_month = str(order["order_date"])[:7]
            monthly_done = sum(
                1 for o in resp["data"]["orders"]
                if o.get("order_status") == "COMPLETED"
                and str(o.get("order_date", ""))[:7] == order_month)

            completed = order.get("completed_date") or order["order_date"]
            facts = {
                "order_amount": Decimal(str(order["order_amount"])),
                "payment_amount": Decimal(str(payment["payment_amount"])),
                "refund_amount": Decimal(str(collected["REFUND_RECORD"].get("refund_amount", 0))),
                "order_date": str(order["order_date"]),
                "payment_date": str(payment["payment_date"]),
                "payment_days": (date.fromisoformat(str(payment["payment_date"])[:10])
                                 - date.fromisoformat(str(completed)[:10])).days,
                "agent_tier": state["tier_resolution"]["tier"],
                "product_id": order["product_id"],
                "order_status": order["order_status"],
                "payment_status": payment["payment_status"],
                "monthly_completed_orders": monthly_done,
            }
            state["facts"] = {k: str(v) for k, v in facts.items()}
            with tracer.span("SKILL", "CommissionCalculateSkill", actor="revguard-calculation",
                             inputs={"facts": state["facts"]}) as span:
                result = skills.commission_calculate(
                    state["policy_decision"]["effective_rule_set"], facts, order["currency"])
                span["outputs"] = asdict(result)
            state["calculation_result"] = asdict(result)
            self.store.audit(case["case_id"], "revguard-calculation", "CALCULATED",
                             {"total": result.total_commission,
                              "hash": result.calculation_hash,
                              "eligible": result.eligible})
        case["facts"] = state["facts"]
        case["calculation_result"] = state["calculation_result"]
        self.store.save_case(case)

    # ------------------------------------------------------------- 5. 根因
    def _stage_root_cause(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Root Cause Agent：逐项解释差异，输出根因报告。"""
        self._transition(case, CaseStatus.ROOT_CAUSE_ANALYZING, "开始差异解释与根因分析")
        with tracer.span("AGENT", "revguard-rootcause", actor="revguard-rootcause"):
            with tracer.span("SKILL", "DifferenceExplainSkill", actor="revguard-rootcause") as span:
                report = skills.difference_explain(
                    calculation=_to_calculation(state["calculation_result"]),
                    ledger_entries=state["evidence"]["COMMISSION_LEDGER"]["entries"],
                    matched_policy_version=state["policy_decision"]["policy_version"],
                    tier_conflict=state["tier_resolution"].get("conflict"))
                span["outputs"] = report
            state["root_cause_report"] = report
            case["root_cause_report"] = report
            self.store.save_case(case)
            self.store.audit(case["case_id"], "revguard-rootcause", "ROOT_CAUSE",
                             {"root_causes": report["root_causes"],
                              "total_delta": report["total_delta"]})

    # ------------------------------------------------------------- 6. 风险
    def _stage_risk(self, case: dict, state: dict, tracer: Tracer) -> None:
        """Risk & Approval Agent：风险分级，决定自动处理或人工审批。"""
        self._transition(case, CaseStatus.RISK_REVIEW, "开始风险分级")
        report = state["root_cause_report"]
        delta = Decimal(report["total_delta"])
        action_deltas = [Decimal(item["delta"]) for item in report["diffs"]
                         if Decimal(item["delta"]) != 0]
        gross_amount = sum((abs(item) for item in action_deltas), Decimal("0"))
        # 任一扣回方向都按负向调整治理；额度使用 gross，避免组件间净额抵消风险。
        risk_amount = -gross_amount if any(item < 0 for item in action_deltas) else gross_amount
        with tracer.span("AGENT", "revguard-risk", actor="revguard-risk"):
            with tracer.span("SKILL", "RiskClassifySkill", actor="revguard-risk",
                             inputs={"total_delta": str(delta),
                                     "evidence_score": case["evidence_score"]}) as span:
                risk = skills.risk_classify(
                    action_type="READONLY" if not action_deltas else "LEDGER_ADJUST",
                    adjustment_amount=risk_amount,
                    currency=state["calculation_result"]["currency"],
                    evidence_score=case["evidence_score"],
                    case_type=case["case_type"],
                    policy_conflict=bool(
                        state["policy_decision"].get("unresolved_conflicts")),
                )
                span["outputs"] = asdict(risk)
            state["risk_decision"] = asdict(risk)
            case["risk_level"] = risk.risk_level
            case["risk_decision"] = state["risk_decision"]
            self.store.audit(case["case_id"], "revguard-risk", "RISK_CLASSIFIED",
                             asdict(risk))
            self.store.save_case(case)

            if not action_deltas:
                self.store.audit(case["case_id"], "revguard-risk", "NO_ACTION_NEEDED",
                                 {"note": "台账金额与政策复算一致"})
                self._transition(case, CaseStatus.RESOLVED, "无需调整")
                return
            if risk.risk_level == "L3":
                # L3 只生成方案，强制人工处理（设计文档 14.1）
                self.store.audit(case["case_id"], "revguard-risk", "ESCALATED_MANUAL",
                                 {"reason_codes": risk.reason_codes})
                self._transition(case, CaseStatus.CLOSED, "L3 高风险，转人工线下处理")
                return
            if risk.approval_required:
                # 总额度与逐组件额度同时绑定，禁止在 gross 范围内挪用到其它组件。
                component_quota: dict[str, Decimal] = {}
                for item in report["diffs"]:
                    component = item["component"]
                    amount = abs(Decimal(item["delta"]))
                    if amount:
                        component_quota[component] = component_quota.get(
                            component, Decimal("0")
                        ) + amount
                approval_amount = sum(component_quota.values(), Decimal("0"))
                with tracer.span("SKILL", "ApprovalRouteSkill", actor="revguard-risk") as span:
                    approval = skills.approval_route(
                        self.gateway, tracer, case_id=case["case_id"], risk=risk,
                        amount=approval_amount,
                        component_quota={key: str(value)
                                         for key, value in component_quota.items()},
                        currency=state["calculation_result"]["currency"],
                        action_summary=self._action_summary(state))
                    span["outputs"] = approval
                state["approval"] = approval
                self.store.save_approval({"approval_id": approval["approval_id"],
                                          "case_id": case["case_id"], **approval})
                self._transition(case, CaseStatus.WAITING_FOR_APPROVAL,
                                 f"等待 {risk.approver_role} 审批 {approval['approval_id']}")
            else:
                self._transition(case, CaseStatus.READY_TO_EXECUTE, "L1 低风险，可自动建草稿")

    # ------------------------------------------------------ 7. 审批（人工节点）
    def _simulate_human_approval(self, case: dict, state: dict, tracer: Tracer) -> None:
        """演示环境：模拟审批人完成人工审批节点（审计中明确标记为模拟人工）。"""
        approval = state["approval"]
        with tracer.span("APPROVAL", "HumanApprovalGate", actor=self.simulated_approver,
                         inputs={"approval_id": approval["approval_id"]}) as span:
            resp = skills.call_tool(self.gateway, tracer, "workflow.decide_approval", {
                "approval_id": approval["approval_id"],
                "decision": "APPROVED",
                "approver": self.simulated_approver,
                "comment": "证据完整，金额与政策复算一致，同意调整（演示环境模拟人工审批）",
            }, case_id=case["case_id"], actor=self.simulated_approver,
                scope=["approval:decide"])
            decided = resp["data"]
            span["outputs"] = decided
        state["approval"] = decided
        self.store.save_approval({"approval_id": decided["approval_id"],
                                  "case_id": case["case_id"], **decided})
        self.store.audit(case["case_id"], self.simulated_approver, "APPROVAL_DECIDED",
                         {"decision": decided["status"], "simulated_human": True})
        if decided["status"] == "APPROVED":
            self._transition(case, CaseStatus.READY_TO_EXECUTE, "审批通过")
        else:
            self._transition(case, CaseStatus.REJECTED, "审批驳回")

    # ------------------------------------------------------ 8/9. 执行 + 验证
    def execute_and_verify(self, case: dict, state: dict | None = None,
                           tracer: Tracer | None = None) -> dict:
        """Executor + Verifier：受控执行后独立验证（ADR-002）。

        审批挂起的案件经 API 恢复时也走这里，保证路径唯一。
        """
        tracer = tracer or Tracer(self.store, case["case_id"])
        if state is None:  # API 恢复路径：从持久化数据重建必要状态
            state = self._rebuild_state(case)
        approval = state.get("approval") or {}
        risk = _to_risk(state["risk_decision"])

        self._transition(case, CaseStatus.EXECUTING, "开始受控执行")
        executions: list[dict] = []
        with tracer.span("AGENT", "revguard-executor", actor="revguard-executor"):
            for diff in state["root_cause_report"]["diffs"]:
                delta = Decimal(diff["delta"])
                if delta == 0:
                    continue
                idem_key = f"{case['case_id']}:{diff['component']}"
                # 幂等守门：重复执行直接复用历史记录，不重复写台账
                existing = skills.idempotency_guard(self.store, idem_key)
                if existing:
                    self.store.audit(case["case_id"], "revguard-executor",
                                     "IDEMPOTENCY_SUPPRESSED", {"key": idem_key})
                    executions.append(existing)
                    continue
                if risk.execution_constraints.get("write") == "draft_only":
                    skills.permission_check(actor="revguard-executor", action_type="DRAFT",
                                            risk=risk, approval=None)
                    with tracer.span("SKILL", "AdjustmentDraftSkill",
                                     actor="revguard-executor") as span:
                        draft = skills.adjustment_draft(
                            self.gateway, tracer, case_id=case["case_id"],
                            order_id=case["order_id"], component=diff["component"],
                            delta=delta, currency=state["calculation_result"]["currency"],
                            reason=diff.get("explanation", "佣金差异调整"))
                        span["outputs"] = {"action_id": draft["action_id"],
                                           "status": draft["status"]}
                    execution = {
                        "action_id": draft["action_id"], "case_id": case["case_id"],
                        "action_type": "DRAFT", "status": "DRAFT",
                        "amount": str(delta), "currency": state["calculation_result"]["currency"],
                        "component": diff["component"], "idempotency_key": idem_key,
                        "before_snapshot": [], "after_snapshot": [],
                        "rollback_token": None, "ledger_entry": None,
                    }
                    self.store.save_execution(execution)
                    executions.append(execution)
                    self.store.audit(case["case_id"], "revguard-executor", "DRAFT_CREATED",
                                     {"action_id": draft["action_id"],
                                      "component": diff["component"], "amount": str(delta)})
                    continue
                # 权限守门：Executor 身份 + 审批凭证（设计文档 14.3）
                skills.permission_check(actor="revguard-executor",
                                        action_type="LEDGER_ADJUST",
                                        risk=risk, approval=approval)
                with tracer.span("SKILL", "AdjustmentDraftSkill",
                                 actor="revguard-executor") as span:
                    draft = skills.adjustment_draft(
                        self.gateway, tracer, case_id=case["case_id"],
                        order_id=case["order_id"], component=diff["component"],
                        delta=delta, currency=state["calculation_result"]["currency"],
                        reason=diff.get("explanation", "佣金差异调整"))
                    span["outputs"] = draft
                with tracer.span("SKILL", "LedgerAdjustSkill",
                                 actor="revguard-executor") as span:
                    submitted = skills.ledger_adjust(
                        self.gateway, tracer, case_id=case["case_id"],
                        action_id=draft["action_id"],
                        approval_token=approval.get("approval_token", ""),
                        policy_version=state["policy_decision"]["policy_version"],
                        idempotency_key=idem_key)
                    span["outputs"] = {"status": submitted["status"],
                                       "rollback_token_ref": secret_fingerprint(
                                           submitted.get("rollback_token", ""))}
                execution = {
                    "action_id": draft["action_id"], "case_id": case["case_id"],
                    "action_type": "LEDGER_ADJUST", "status": submitted["status"],
                    "amount": str(delta), "currency": state["calculation_result"]["currency"],
                    "component": diff["component"], "idempotency_key": idem_key,
                    "before_snapshot": submitted["before_snapshot"],
                    "after_snapshot": submitted["after_snapshot"],
                    "rollback_token": submitted.get("rollback_token"),
                    "ledger_entry": submitted.get("ledger_entry"),
                }
                self.store.save_execution(execution)
                executions.append(execution)
                self.store.audit(case["case_id"], "revguard-executor", "EXECUTED",
                                 {"action_id": draft["action_id"], "component": diff["component"],
                                  "amount": str(delta), "idempotency_key": idem_key})
        state["executions"] = executions

        if risk.execution_constraints.get("write") == "draft_only":
            state["verification"] = {
                "verification_status": "NOT_APPLICABLE_DRAFT_ONLY",
                "expected_amount": state["calculation_result"]["total_commission"],
                "actual_amount": state["root_cause_report"]["total_posted"],
                "variance": state["root_cause_report"]["total_delta"],
                "component_checks": [], "rollback_required": False,
                "checked_at": utc_now(),
            }
            self.store.save_verification(case["case_id"], state["verification"])
            self._transition(case, CaseStatus.RESOLVED,
                             "L1 仅创建不生效草稿，未写入资金台账")
            return state

        self._transition(case, CaseStatus.VERIFYING, "开始独立验证")
        with tracer.span("AGENT", "revguard-verifier", actor="revguard-verifier"):
            with tracer.span("SKILL", "PostActionVerifySkill",
                             actor="revguard-verifier") as span:
                verification = skills.post_action_verify(
                    self.gateway, tracer, case_id=case["case_id"],
                    order_id=case["order_id"],
                    expected_components=state["calculation_result"]["components"])
                span["outputs"] = verification
        state["verification"] = verification
        self.store.save_verification(case["case_id"], verification)
        self.store.audit(case["case_id"], "revguard-verifier", "VERIFIED", verification)
        if verification["verification_status"] == "PASSED":
            self._transition(case, CaseStatus.RESOLVED, "执行结果验证通过")
        else:
            self._transition(case, CaseStatus.ROLLBACK_REQUIRED,
                             f"验证失败，variance={verification['variance']}")
            self._rollback_executions(case, state, tracer)
        self.store.save_case(case)
        return state

    def _rollback_executions(self, case: dict, state: dict, tracer: Tracer) -> None:
        """验证失败后按执行逆序冲销，并由 Verifier 独立确认恢复执行前净额。"""
        executions = [e for e in state.get("executions", [])
                      if e.get("status") == "SUBMITTED" and e.get("ledger_entry")]
        if not executions:
            self._transition(case, CaseStatus.FAILED, "验证失败但没有可回滚执行记录")
            return
        expected_snapshot = executions[0].get("before_snapshot", [])
        reversals = []
        with tracer.span("AGENT", "revguard-executor-rollback", actor="revguard-executor"):
            for execution in reversed(executions):
                ledger_id = execution["ledger_entry"]["ledger_id"]
                rollback_key = f"{case['case_id']}:{execution['component']}:rollback"
                with tracer.span("SKILL", "LedgerReverseSkill",
                                 actor="revguard-executor") as span:
                    reversed_result = skills.ledger_reverse(
                        self.gateway, tracer, case_id=case["case_id"],
                        ledger_id=ledger_id,
                        rollback_token=execution["rollback_token"],
                        idempotency_key=rollback_key,
                    )
                    span["outputs"] = {
                        "ledger_id": reversed_result["reversal_entry"]["ledger_id"],
                        "reversal_of": ledger_id,
                    }
                execution["status"] = "ROLLED_BACK"
                execution["reversal"] = reversed_result["reversal_entry"]
                self.store.save_execution(execution)
                reversals.append(reversed_result["reversal_entry"])
                self.store.audit(case["case_id"], "revguard-executor", "ROLLED_BACK",
                                 {"action_id": execution["action_id"],
                                  "ledger_id": ledger_id,
                                  "reversal_id": reversed_result["reversal_entry"]["ledger_id"]})

        with tracer.span("AGENT", "revguard-verifier-rollback", actor="revguard-verifier"):
            with tracer.span("SKILL", "PostRollbackVerifySkill",
                             actor="revguard-verifier") as span:
                rollback_verification = skills.post_rollback_verify(
                    self.gateway, tracer, case_id=case["case_id"],
                    order_id=case["order_id"], expected_snapshot=expected_snapshot,
                )
                span["outputs"] = rollback_verification
        state["rollback"] = {"reversals": reversals,
                             "verification": rollback_verification}
        self.store.audit(case["case_id"], "revguard-verifier", "ROLLBACK_VERIFIED",
                         rollback_verification)
        if rollback_verification["verification_status"] == "PASSED":
            self._transition(case, CaseStatus.ROLLED_BACK, "冲销后已恢复执行前台账净额")
        else:
            self._transition(case, CaseStatus.FAILED, "冲销后独立验证仍存在偏差")

    # ------------------------------------------------------------- 10. 沉淀
    def _finalize(self, case: dict, state: dict, tracer: Tracer, *,
                  archived: bool) -> dict:
        """Knowledge Agent：沉淀案例、生成审计报告、导出 Trace、生成回复草稿。"""
        verification = state.get("verification") or {}
        if archived:
            terminal_status = case.get("status")
            with tracer.span("AGENT", "revguard-knowledge", actor="revguard-knowledge"):
                with tracer.span("SKILL", "CaseToDatasetSkill",
                                 actor="revguard-knowledge") as span:
                    dataset = skills.case_to_dataset(case, state, verification)
                    span["outputs"] = dataset
                mem_dir = self.output_dir / "case_memory"
                mem_dir.mkdir(parents=True, exist_ok=True)
                (mem_dir / f"{case['case_id']}.json").write_text(
                    json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
                # 生成代理商回复草稿（不直接发送，安全边界）
                skills.call_tool(self.gateway, tracer, "mail.create_reply_draft", {
                    "case_id": case["case_id"],
                    "partner_id": case.get("partner_id"),
                    "summary": (state.get("root_cause_report", {}).get("diffs") or []),
                    "resolution": case["status"],
                }, case_id=case["case_id"], actor="revguard-knowledge",
                    scope=["mail:draft"])
                skills.call_tool(self.gateway, tracer, "ticket.update_case", {
                    "ticket_ref": case.get("source_ref", "TICKET-MOCK"),
                    "case_id": case["case_id"], "status": case["status"],
                }, case_id=case["case_id"], actor="revguard-knowledge",
                    scope=["ticket:write"])
            if terminal_status in (
                CaseStatus.ROLLED_BACK.value,
                CaseStatus.FAILED.value,
                CaseStatus.CLOSED.value,
            ):
                # 保留关键失败/回滚终态，避免归档动作覆盖安全结论。
                self.store.audit(case["case_id"], "revguard-knowledge", "KNOWLEDGE_ARCHIVED",
                                 {"terminal_status_preserved": terminal_status})
            else:
                self._transition(case, CaseStatus.KNOWLEDGE_ARCHIVED, "案例与评测数据已沉淀")
                self._transition(case, CaseStatus.CLOSED, "案件关闭")

        # 无论成功与否都导出 Trace + 审计报告（失败案件更需要证据）
        trace_dir = self.output_dir / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_data = tracer.export()
        (trace_dir / f"{case['case_id']}.json").write_text(
            json.dumps(trace_data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")

        self.report_dir.mkdir(parents=True, exist_ok=True)
        report_md = render_audit_report(
            case=case, state=state,
            evidence=self.store.list_evidence(case["case_id"]),
            audit_events=self.store.list_audit(case["case_id"]),
            trace_summary={"span_count": trace_data["span_count"],
                           "total_duration_ms": trace_data["total_duration_ms"],
                           "error_spans": trace_data["error_spans"]})
        (self.report_dir / f"{case['case_id']}.md").write_text(report_md, encoding="utf-8")

        state["final_status"] = case["status"]
        self.store.save_case(case)
        return state

    # ------------------------------------------------------------------ 工具
    def _transition(self, case: dict, to: CaseStatus, reason: str) -> None:
        """兼容类内调用；实际校验、审计和落库由统一状态机负责。"""
        transition_case(self.store, case, to, reason)

    def _escalate_to_human(self, case: dict, state: dict, tracer: Tracer, gap: str) -> None:
        """证据不足：挂起补证 + 通知工单系统 + 升级人工（不生成虚假结论）。"""
        self._transition(case, CaseStatus.WAITING_FOR_EVIDENCE, gap)
        skills.call_tool(self.gateway, tracer, "ticket.update_case", {
            "ticket_ref": case.get("source_ref", "TICKET-MOCK"),
            "case_id": case["case_id"],
            "request": f"请补充信息后重新受理：{gap}",
        }, case_id=case["case_id"], actor="revguard-intake", scope=["ticket:write"])
        self.store.audit(case["case_id"], "revguard-orchestrator", "ESCALATED_TO_HUMAN",
                         {"gap": gap})

    def _action_summary(self, state: dict) -> str:
        report = state["root_cause_report"]
        return (f"佣金差异调整：应有 {report['total_expected']}，台账 {report['total_posted']}，"
                f"差额 {report['total_delta']}；根因 {', '.join(report['root_causes']) or '无'}")

    def _rebuild_state(self, case: dict) -> dict:
        """API 恢复路径：从持久化的证据/审批记录重建 Shared State。"""
        evidence = {e["type"]: e["payload"] for e in self.store.list_evidence(case["case_id"])}
        approval = self.store.get_approval(case["case_id"]) or {}
        return {
            "case_id": case["case_id"],
            "evidence": evidence,
            "approval": approval,
            "policy_decision": case.get("policy_decision", {}),
            "calculation_result": case.get("calculation_result", {}),
            "root_cause_report": case.get("root_cause_report", {}),
            "risk_decision": case.get("risk_decision", {}),
        }


def _to_calculation(d: dict):
    """dict -> CalculationResult（state 中存的是序列化形式）。"""
    from .models import CalculationResult
    return CalculationResult(**d)


def _to_risk(d: dict):
    from .models import RiskDecision
    return RiskDecision(**d)
