"""Executable multi-Worker case flow using scoped MCP servers.

This is the local reference harness for the same StageTasks that AgentTeams
Workers consume in Matrix.  It does not simulate chat messages or preselect an
outcome: every stage is dispatched from the persisted Case state, invoked over
the official MCP client/server protocol, validated against the Skill contract,
and applied to the next state only after a persisted SUCCEEDED StageResult.
"""
from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from mcp import Client

from . import skills
from .agent_bridge import create_agent_task
from .mcp_server import SERVER_INJECTION_REF, build_scoped_server
from .models import CaseStatus, RiskDecision, new_id, utc_now
from .orchestrator import EVIDENCE_SCORE_THRESHOLD, Orchestrator
from .security import redact_secrets
from .state_machine import transition_case
from .trace import Tracer


class McpStageError(RuntimeError):
    """A scoped Worker returned an MCP tool error; no downstream state is applied."""


class McpTeamRunner:
    execution_mode = "MCP_TEAM"
    transport = "mcp"
    display_name = "MCP Team"
    runner_name = "agentteams-compatible-reference-harness"
    room_evidence = "PENDING_EXTERNAL_CAPTURE"

    def __init__(self, store, gateway, *, output_dir: str | Path,
                 report_dir: str | Path):
        self.store = store
        self.gateway = gateway
        self.output_dir = Path(output_dir)
        self.report_dir = Path(report_dir)

    async def _invoke(self, case: dict, skill_name: str, skill_input: dict, *,
                      message_id: str | None = None) -> dict:
        """Dispatch and execute exactly one state-bound Worker task over MCP."""
        task = create_agent_task(case, skill_name, skill_input)
        request_id = new_id("REQ-MCP")
        message_id = message_id or f"$mcp-{case['case_id']}-{task['task_id']}"
        task.update({
            "request_id": request_id,
            "agentteams_message_id": message_id,
            "transport": self.transport,
        })
        self.store.save_agent_task(task)
        self.store.audit(case["case_id"], "revguard-orchestrator",
                         "AGENT_TASK_DISPATCHED", {
                             "task_id": task["task_id"],
                             "skill": skill_name,
                             "assigned_actor": task["assigned_actor"],
                             "case_version": task["case_version"],
                             "request_id": request_id,
                             "agentteams_message_id": message_id,
                             "transport": self.transport,
                             "runner": self.runner_name,
                         })
        server = build_scoped_server(
            actor=task["assigned_actor"], store=self.store, gateway=self.gateway,
        )
        async with Client(server) as client:
            result = await client.call_tool(skill_name, {
                "case_id": case["case_id"],
                "task_id": task["task_id"],
                "input": skill_input,
                "request_id": request_id,
                "agentteams_message_id": message_id,
            })
        if result.is_error or not result.structured_content:
            message = result.content[0].text if result.content else "MCP stage failed"
            raise McpStageError(message)
        # The MCP result is redacted for the Worker/model.  The coordinator reads
        # the atomically persisted StageResult so one-time capability tokens can
        # remain server-side for the later rollback branch.
        persisted = self.store.get_agent_task(task["task_id"])
        if not persisted or persisted.get("status") != "SUCCEEDED":
            raise McpStageError("MCP returned success without a persisted StageResult")
        return persisted["result"]

    async def run_to_human_gate(self, case: dict) -> dict:
        """Run dynamic Worker stages until a real human gate or a safe terminal state."""
        if case.get("status") not in {
            CaseStatus.CREATED.value, CaseStatus.WAITING_FOR_EVIDENCE.value,
        }:
            raise ValueError(
                f"案件状态 {case.get('status')} 不允许启动 {self.display_name}"
            )
        case["execution_mode"] = self.execution_mode
        case["workflow_provenance"] = {
            "business_data": "synthetic",
            "workflow": "real_executable",
            "transport": self.transport,
            "orchestration": "state-driven",
            "agentteams_room_evidence": self.room_evidence,
        }
        self.store.save_case(case)
        state: dict = {"case_id": case["case_id"], "facts": {}, "errors": []}
        try:
            normalized = await self._invoke(
                case, "CaseNormalizeSkill", {"raw_case": case},
            )
            state["normalized"] = normalized
            case["entities"] = normalized["entities"]
            transition_case(
                self.store, case, CaseStatus.NORMALIZING,
                f"{self.display_name} Intake Worker 完成标准化",
            )

            resolved = await self._invoke(
                case, "EntityResolveSkill", {"entities": case["entities"]},
            )
            partner = resolved["partner"]
            state["partner"] = partner
            case["partner_id"] = partner["partner_id"]
            case["partner_name"] = partner.get("name")
            if not case["entities"].get("order_id"):
                response = skills.call_tool(
                    self.gateway, Tracer(self.store, case["case_id"]),
                    "crm.list_orders_by_partner", {"partner_id": partner["partner_id"]},
                    case_id=case["case_id"], actor="revguard-intake",
                    scope=["order:read"],
                )
                candidates = response["data"]["orders"]
                state["order_candidates"] = [item["order_id"] for item in candidates]
                if len(candidates) != 1:
                    gap = (
                        f"工单缺少订单号，代理商 {partner['partner_id']} 名下存在 "
                        f"{len(candidates)} 笔候选订单，无法唯一确定"
                    )
                    state["errors"].append(gap)
                    self.store.audit(case["case_id"], "revguard-intake", "EVIDENCE_GAP", {
                        "gap": gap, "candidates": state["order_candidates"],
                    })
                    transition_case(
                        self.store, case, CaseStatus.WAITING_FOR_EVIDENCE, gap,
                    )
                    self.store.cancel_open_agent_tasks(
                        case["case_id"], actor="revguard-orchestrator", reason=gap,
                    )
                    return self._export(case, state)
                case["entities"]["order_id"] = candidates[0]["order_id"]
            case["order_id"] = case["entities"]["order_id"]
            self.store.save_case(case)
            transition_case(
                self.store, case, CaseStatus.EVIDENCE_COLLECTING,
                f"{self.display_name} Intake Worker 已解析唯一业务实体",
            )

            package = await self._invoke(case, "EvidenceCollectSkill", {
                "partner": partner,
                "order_id": case["order_id"],
            })
            for evidence in package["evidence"]:
                self.store.save_evidence(evidence)
            state["evidence"] = package["collected"]
            state["evidence_gaps"] = package["evidence_gaps"]
            case["evidence_score"] = package["evidence_score"]
            self.store.audit(case["case_id"], "revguard-evidence", "EVIDENCE_COLLECTED", {
                "score": package["evidence_score"],
                "gaps": package["evidence_gaps"],
                "parallel": package["parallel"],
                "transport": self.transport,
            })
            self.store.save_case(case)
            if package["evidence_score"] < EVIDENCE_SCORE_THRESHOLD:
                gap = (
                    f"证据完整度 {package['evidence_score']} 低于阈值 "
                    f"{EVIDENCE_SCORE_THRESHOLD}"
                )
                transition_case(
                    self.store, case, CaseStatus.WAITING_FOR_EVIDENCE, gap,
                )
                return self._export(case, state)
            transition_case(
                self.store, case, CaseStatus.POLICY_MATCHING,
                f"{self.display_name} Evidence Worker 完成跨系统证据包",
            )

            collected = state["evidence"]
            order = collected["ORDER"]
            contract = collected["CONTRACT"]
            time_basis = (contract.get("terms") or {}).get("time_basis", "order_date")
            policy = await self._invoke(case, "PolicyVersionMatchSkill", {
                "versions": collected["POLICY_VERSIONS"]["versions"],
                "facts": {
                    "order_date": order["order_date"],
                    "payment_date": collected["PAYMENT_RECORD"]["payment_date"],
                },
                "time_basis": time_basis,
            })
            tier = skills.tier_at_order_date(
                collected["TIER_HISTORY"]["tier_history"], order["order_date"],
            )
            state["policy_decision"] = policy
            state["tier_resolution"] = tier
            case["policy_decision"] = policy
            case["tier_resolution"] = tier
            self.store.audit(case["case_id"], "revguard-policy", "POLICY_MATCHED", {
                "policy_version": policy["policy_version"],
                "time_basis": policy["time_basis"],
                "excluded": policy["excluded_versions"],
                "conflicts": policy["unresolved_conflicts"],
                "transport": self.transport,
            })
            if tier.get("conflict"):
                self.store.audit(case["case_id"], "revguard-policy",
                                 "EVIDENCE_CONFLICT", {"conflict": tier["conflict"]})
            self.store.save_case(case)
            transition_case(
                self.store, case, CaseStatus.CALCULATING,
                f"{self.display_name} Policy Worker 完成政策时点匹配",
            )

            facts = self._build_calculation_facts(case, state)
            calculation = await self._invoke(case, "CommissionCalculateSkill", {
                "rule_dsl": policy["effective_rule_set"],
                "facts": facts,
                "currency": order["currency"],
            })
            state["facts"] = facts
            state["calculation_result"] = calculation
            case["facts"] = facts
            case["calculation_result"] = calculation
            self.store.audit(case["case_id"], "revguard-calculation", "CALCULATED", {
                "total": calculation["total_commission"],
                "hash": calculation["calculation_hash"],
                "eligible": calculation["eligible"],
                "transport": self.transport,
            })
            self.store.save_case(case)
            transition_case(
                self.store, case, CaseStatus.ROOT_CAUSE_ANALYZING,
                f"{self.display_name} Calculation Worker 完成确定性金额复算",
            )

            root_cause = await self._invoke(case, "DifferenceExplainSkill", {
                "calculation": calculation,
                "ledger_entries": collected["COMMISSION_LEDGER"]["entries"],
                "matched_policy_version": policy["policy_version"],
                "tier_conflict": tier.get("conflict"),
            })
            state["root_cause_report"] = root_cause
            case["root_cause_report"] = root_cause
            self.store.audit(case["case_id"], "revguard-rootcause", "ROOT_CAUSE", {
                "root_causes": root_cause["root_causes"],
                "total_delta": root_cause["total_delta"],
                "transport": self.transport,
            })
            self.store.save_case(case)
            transition_case(
                self.store, case, CaseStatus.RISK_REVIEW,
                f"{self.display_name} RootCause Worker 完成逐组件差异解释",
            )

            action_deltas = [
                Decimal(item["delta"]) for item in root_cause["diffs"]
                if Decimal(item["delta"]) != 0
            ]
            gross_amount = sum((abs(item) for item in action_deltas), Decimal("0"))
            risk_amount = -gross_amount if any(item < 0 for item in action_deltas) else gross_amount
            risk = await self._invoke(case, "RiskClassifySkill", {
                "action_type": "READONLY" if not action_deltas else "LEDGER_ADJUST",
                "adjustment_amount": str(risk_amount),
                "currency": calculation["currency"],
                "evidence_score": case["evidence_score"],
                "case_type": case["case_type"],
                "policy_conflict": bool(policy.get("unresolved_conflicts")),
            })
            state["risk_decision"] = risk
            case["risk_level"] = risk["risk_level"]
            case["risk_decision"] = risk
            self.store.audit(case["case_id"], "revguard-risk", "RISK_CLASSIFIED", {
                **risk, "transport": self.transport,
            })
            self.store.save_case(case)

            if not action_deltas:
                self.store.audit(case["case_id"], "revguard-risk", "NO_ACTION_NEEDED", {
                    "note": "台账金额与政策复算一致",
                })
                transition_case(self.store, case, CaseStatus.RESOLVED, "无需调整")
                await self._archive(case, state)
                return self._export(case, state)
            if risk["risk_level"] == "L3":
                self.store.audit(case["case_id"], "revguard-risk", "ESCALATED_MANUAL", {
                    "reason_codes": risk["reason_codes"],
                })
                transition_case(
                    self.store, case, CaseStatus.CLOSED,
                    "L3 高风险，转人工线下处理",
                )
                await self._archive(case, state)
                return self._export(case, state)
            if risk["approval_required"]:
                component_quota = self._component_quota(root_cause)
                approval_amount = sum(
                    (Decimal(value) for value in component_quota.values()), Decimal("0")
                )
                approval = await self._invoke(case, "ApprovalRouteSkill", {
                    "risk": risk,
                    "amount": str(approval_amount),
                    "component_quota": component_quota,
                    "currency": calculation["currency"],
                    "action_summary": self._action_summary(root_cause),
                })
                state["approval"] = approval
                self.store.save_approval({
                    "approval_id": approval["approval_id"],
                    "case_id": case["case_id"],
                    **approval,
                })
                transition_case(
                    self.store, case, CaseStatus.WAITING_FOR_APPROVAL,
                    f"{self.display_name} 等待 {risk['approver_role']} "
                    f"审批 {approval['approval_id']}",
                )
                return self._export(case, state)

            transition_case(
                self.store, case, CaseStatus.READY_TO_EXECUTE,
                "L1 低风险，可自动建草稿",
            )
            await self.execute_after_approval(case, state=state)
            return self._export(case, state)
        except Exception as exc:
            if case.get("status") not in {
                CaseStatus.CLOSED.value, CaseStatus.ROLLED_BACK.value,
                CaseStatus.FAILED.value,
            }:
                transition_case(
                    self.store, case, CaseStatus.FAILED,
                    f"{self.display_name} failure: {type(exc).__name__}",
                )
            state["errors"].append(str(exc))
            self.store.audit(case["case_id"], "revguard-orchestrator", "CASE_FAILED", {
                "error_type": type(exc).__name__, "transport": self.transport,
            })
            self._export(case, state)
            raise

    async def execute_after_approval(self, case: dict, *,
                                     state: dict | None = None) -> dict:
        """Continue an approved MCP Team case through write, verify and rollback."""
        if case.get("status") != CaseStatus.READY_TO_EXECUTE.value:
            raise ValueError(f"案件状态 {case.get('status')} 不允许执行")
        state = state or self._rebuild_state(case)
        approval = state.get("approval") or self.store.get_approval(case["case_id"]) or {}
        state["approval"] = approval
        risk = RiskDecision(**state["risk_decision"])
        await self._invoke(case, "PermissionCheckSkill", {
            "action_type": (
                "DRAFT" if risk.execution_constraints.get("write") == "draft_only"
                else "LEDGER_ADJUST"
            ),
            "risk": state["risk_decision"],
            "approval": (
                {**approval, "approval_token": SERVER_INJECTION_REF}
                if approval.get("approval_token") else approval
            ),
        })
        transition_case(
            self.store, case, CaseStatus.EXECUTING,
            f"{self.display_name} Executor 通过服务端权限检查，开始受控执行",
        )
        executions = []
        for diff in state["root_cause_report"]["diffs"]:
            delta = Decimal(diff["delta"])
            if delta == 0:
                continue
            idempotency_key = f"{case['case_id']}:{diff['component']}"
            existing = await self._invoke(case, "IdempotencyGuardSkill", {
                "idempotency_key": idempotency_key,
            })
            if existing:
                executions.append(existing)
                self.store.audit(case["case_id"], "revguard-executor",
                                 "IDEMPOTENCY_SUPPRESSED", {"key": idempotency_key})
                continue
            draft = await self._invoke(case, "AdjustmentDraftSkill", {
                "order_id": case["order_id"],
                "component": diff["component"],
                "delta": str(delta),
                "currency": state["calculation_result"]["currency"],
                "reason": diff.get("explanation", "佣金差异调整"),
            })
            if risk.execution_constraints.get("write") == "draft_only":
                execution = {
                    "action_id": draft["action_id"], "case_id": case["case_id"],
                    "action_type": "DRAFT", "status": "DRAFT",
                    "amount": str(delta),
                    "currency": state["calculation_result"]["currency"],
                    "component": diff["component"],
                    "idempotency_key": idempotency_key,
                    "before_snapshot": [], "after_snapshot": [],
                    "ledger_entry": None,
                }
                execution["rollback_token"] = None
                self.store.save_execution(execution)
                executions.append(execution)
                self.store.audit(case["case_id"], "revguard-executor", "DRAFT_CREATED", {
                    "action_id": draft["action_id"], "component": diff["component"],
                    "amount": str(delta), "transport": self.transport,
                })
                continue
            submitted = await self._invoke(case, "LedgerAdjustSkill", {
                "action_id": draft["action_id"],
                "approval_token": SERVER_INJECTION_REF,
                "policy_version": state["policy_decision"]["policy_version"],
                "idempotency_key": idempotency_key,
            })
            execution = {
                "action_id": draft["action_id"], "case_id": case["case_id"],
                "action_type": "LEDGER_ADJUST", "status": submitted["status"],
                "amount": str(delta),
                "currency": state["calculation_result"]["currency"],
                "component": diff["component"],
                "idempotency_key": idempotency_key,
                "before_snapshot": submitted["before_snapshot"],
                "after_snapshot": submitted["after_snapshot"],
                "rollback_token": submitted.get("rollback_token"),
                "ledger_entry": submitted.get("ledger_entry"),
            }
            self.store.save_execution(execution)
            executions.append(execution)
            self.store.audit(case["case_id"], "revguard-executor", "EXECUTED", {
                "action_id": draft["action_id"], "component": diff["component"],
                "amount": str(delta), "idempotency_key": idempotency_key,
                "transport": self.transport,
            })
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
            transition_case(
                self.store, case, CaseStatus.RESOLVED,
                "L1 仅创建不生效草稿，未写入资金台账",
            )
            await self._archive(case, state)
            return self._export(case, state)

        transition_case(
            self.store, case, CaseStatus.VERIFYING,
            f"{self.display_name} Executor 完成写入，移交独立 Verifier",
        )
        verification = await self._invoke(case, "PostActionVerifySkill", {
            "order_id": case["order_id"],
            "expected_components": state["calculation_result"]["components"],
        })
        state["verification"] = verification
        self.store.save_verification(case["case_id"], verification)
        self.store.audit(case["case_id"], "revguard-verifier", "VERIFIED", {
            **verification, "transport": self.transport,
        })
        if verification["verification_status"] == "PASSED":
            transition_case(
                self.store, case, CaseStatus.RESOLVED,
                f"{self.display_name} Verifier 独立查询验证通过",
            )
        else:
            transition_case(
                self.store, case, CaseStatus.ROLLBACK_REQUIRED,
                f"{self.display_name} Verifier 发现 "
                f"variance={verification['variance']}，触发冲销",
            )
            await self._rollback(case, state)
        self.store.save_case(case)
        await self._archive(case, state)
        return self._export(case, state)

    async def finalize_terminal(self, case: dict, *, approval: dict | None = None) -> dict:
        """Archive a human-rejected or otherwise terminal MCP Team case."""
        state = self._rebuild_state(case)
        if approval is not None:
            state["approval"] = approval
        await self._archive(case, state)
        return self._export(case, state)

    async def _rollback(self, case: dict, state: dict) -> None:
        executions = [
            item for item in state.get("executions", [])
            if item.get("status") == "SUBMITTED" and item.get("ledger_entry")
        ]
        if not executions:
            transition_case(
                self.store, case, CaseStatus.FAILED,
                "验证失败但没有可回滚执行记录",
            )
            return
        expected_snapshot = executions[0].get("before_snapshot", [])
        reversals = []
        for execution in reversed(executions):
            reversed_result = await self._invoke(case, "LedgerReverseSkill", {
                "ledger_id": execution["ledger_entry"]["ledger_id"],
                "rollback_token": SERVER_INJECTION_REF,
                "idempotency_key": (
                    f"{case['case_id']}:{execution['component']}:rollback"
                ),
            })
            execution["status"] = "ROLLED_BACK"
            execution["reversal"] = reversed_result["reversal_entry"]
            self.store.save_execution(execution)
            reversals.append(reversed_result["reversal_entry"])
            self.store.audit(case["case_id"], "revguard-executor", "ROLLED_BACK", {
                "action_id": execution["action_id"],
                "ledger_id": execution["ledger_entry"]["ledger_id"],
                "reversal_id": reversed_result["reversal_entry"]["ledger_id"],
                "transport": self.transport,
            })
        rollback_verification = await self._invoke(
            case, "PostRollbackVerifySkill", {
                "order_id": case["order_id"],
                "expected_snapshot": expected_snapshot,
            },
        )
        state["rollback"] = {
            "reversals": reversals, "verification": rollback_verification,
        }
        self.store.audit(case["case_id"], "revguard-verifier", "ROLLBACK_VERIFIED", {
            **rollback_verification, "transport": self.transport,
        })
        if rollback_verification["verification_status"] == "PASSED":
            transition_case(
                self.store, case, CaseStatus.ROLLED_BACK,
                f"{self.display_name} Verifier 确认反向冲销恢复执行前净额",
            )
        else:
            transition_case(
                self.store, case, CaseStatus.FAILED,
                "冲销后独立验证仍存在偏差",
            )

    async def _archive(self, case: dict, state: dict) -> None:
        # The authoritative full trace already lives in Store/Trace.  The
        # Knowledge Worker only needs the fields consumed by CaseToDatasetSkill;
        # sending the full evidence package and all prior results through Matrix
        # makes the final Agent turn unnecessarily large and model-dependent.
        # Keep the StageTask exact and auditable, but project a compact archive
        # manifest that points to the same case/run.
        archive_case = {
            key: case.get(key) for key in (
                "case_id", "case_type", "status", "claim", "entities",
            )
        }
        archive_state = redact_secrets({
            "policy_decision": {
                "policy_version": (state.get("policy_decision") or {}).get(
                    "policy_version"
                ),
            },
            "calculation_result": {
                "total_commission": (state.get("calculation_result") or {}).get(
                    "total_commission"
                ),
            },
            "root_cause_report": {
                "root_causes": (state.get("root_cause_report") or {}).get(
                    "root_causes", []
                ),
            },
            "trace_ref": f"case:{case['case_id']}",
            "run_id": (case.get("team_run") or {}).get("run_id"),
        })
        dataset = await self._invoke(case, "CaseToDatasetSkill", {
            "case": archive_case,
            "shared_state": archive_state,
            "verification": state.get("verification") or {},
        })
        memory_dir = self.output_dir / "case_memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        (memory_dir / f"{case['case_id']}.json").write_text(
            json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8",
        )
        tracer = Tracer(self.store, case["case_id"])
        skills.call_tool(self.gateway, tracer, "mail.create_reply_draft", {
            "case_id": case["case_id"], "partner_id": case.get("partner_id"),
            "summary": (state.get("root_cause_report", {}).get("diffs") or []),
            "resolution": case["status"],
        }, case_id=case["case_id"], actor="revguard-knowledge", scope=["mail:draft"])
        skills.call_tool(self.gateway, tracer, "ticket.update_case", {
            "ticket_ref": case.get("source_ref", "TICKET-MOCK"),
            "case_id": case["case_id"], "status": case["status"],
        }, case_id=case["case_id"], actor="revguard-knowledge", scope=["ticket:write"])
        terminal = case["status"]
        self.store.audit(case["case_id"], "revguard-knowledge", "KNOWLEDGE_ARCHIVED", {
            "terminal_status_preserved": terminal,
            "transport": self.transport,
        })
        if terminal == CaseStatus.RESOLVED.value:
            transition_case(
                self.store, case, CaseStatus.KNOWLEDGE_ARCHIVED,
                f"{self.display_name} Knowledge Worker 已沉淀可回放样本",
            )
            transition_case(
                self.store, case, CaseStatus.CLOSED, "案件关闭",
            )

    def _build_calculation_facts(self, case: dict, state: dict) -> dict:
        evidence = state["evidence"]
        order = evidence["ORDER"]
        payment = evidence["PAYMENT_RECORD"]
        response = skills.call_tool(
            self.gateway, Tracer(self.store, case["case_id"]),
            "crm.list_orders_by_partner", {"partner_id": case["partner_id"]},
            case_id=case["case_id"], actor="revguard-calculation",
            scope=["order:read"],
        )
        order_month = str(order["order_date"])[:7]
        monthly_done = sum(
            1 for item in response["data"]["orders"]
            if item.get("order_status") == "COMPLETED"
            and str(item.get("order_date", ""))[:7] == order_month
        )
        completed = order.get("completed_date") or order["order_date"]
        return {
            "order_amount": str(order["order_amount"]),
            "payment_amount": str(payment["payment_amount"]),
            "refund_amount": str(evidence["REFUND_RECORD"].get("refund_amount", 0)),
            "order_date": str(order["order_date"]),
            "payment_date": str(payment["payment_date"]),
            "payment_days": str(
                (date.fromisoformat(str(payment["payment_date"])[:10])
                 - date.fromisoformat(str(completed)[:10])).days
            ),
            "agent_tier": state["tier_resolution"]["tier"],
            "product_id": order["product_id"],
            "order_status": order["order_status"],
            "payment_status": payment["payment_status"],
            "monthly_completed_orders": str(monthly_done),
        }

    @staticmethod
    def _component_quota(root_cause: dict) -> dict[str, str]:
        quotas: dict[str, Decimal] = {}
        for item in root_cause["diffs"]:
            amount = abs(Decimal(item["delta"]))
            if amount:
                quotas[item["component"]] = quotas.get(
                    item["component"], Decimal("0")
                ) + amount
        return {key: str(value) for key, value in quotas.items()}

    @staticmethod
    def _action_summary(root_cause: dict) -> str:
        return (
            f"佣金差异调整：应有 {root_cause['total_expected']}，台账 "
            f"{root_cause['total_posted']}，差额 {root_cause['total_delta']}；根因 "
            f"{', '.join(root_cause['root_causes']) or '无'}"
        )

    def _rebuild_state(self, case: dict) -> dict:
        evidence = {
            item["type"]: item["payload"]
            for item in self.store.list_evidence(case["case_id"])
        }
        return {
            "case_id": case["case_id"],
            "facts": case.get("facts", {}),
            "evidence": evidence,
            "approval": self.store.get_approval(case["case_id"]) or {},
            "policy_decision": case.get("policy_decision", {}),
            "tier_resolution": case.get("tier_resolution", {}),
            "calculation_result": case.get("calculation_result", {}),
            "root_cause_report": case.get("root_cause_report", {}),
            "risk_decision": case.get("risk_decision", {}),
            "errors": [],
        }

    def _export(self, case: dict, state: dict) -> dict:
        """Export trace/report without running a hidden second business workflow."""
        Orchestrator(
            self.store, self.gateway,
            output_dir=self.output_dir, report_dir=self.report_dir,
            approval_mode="wait",
        )._finalize(case, state, Tracer(self.store, case["case_id"]), archived=False)
        state["final_status"] = case["status"]
        return state
