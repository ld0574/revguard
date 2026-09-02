"""Mock 企业系统与统一工具调用契约（ADR-003）。

所有外部系统（CRM / 合同 / 政策库 / 财务 / 佣金台账 / 审批流 / 工单 / 邮件）
都通过 ToolGateway.call() 访问，契约与设计文档 13.1 一致：

    请求: {tool_name, request_id, case_id, trace_id, actor, parameters, auth_context, idempotency_key}
    响应: {success, data, error, source_timestamp, tool_receipt}

关键设计：
- Mock 与真实系统共用同一 Schema，复赛只需替换 Adapter 实现；
- 支持故障注入（财务接口前 N 次返回 TOOL_UNAVAILABLE），用于演示重试；
- 佣金台账是唯一可写系统：所有写操作要求幂等键，重复提交返回幂等冲突；
- 每次调用生成 tool_receipt，证据链可溯源。
"""
from __future__ import annotations

import copy
import json
import os
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import ClassVar

from .models import new_id, utc_now
from .security import CapabilityTokenSigner, SecurityError, authorize_tool


class ToolError(Exception):
    """工具调用失败，error_type 必须是约定枚举（设计文档 13.3）。"""

    RETRYABLE: ClassVar[frozenset[str]] = frozenset({
        "TIMEOUT", "TOOL_UNAVAILABLE", "RATE_LIMITED",
    })
    FATAL: ClassVar[frozenset[str]] = frozenset({
        "AUTH_FAILED", "NOT_FOUND", "DATA_CONFLICT", "IDEMPOTENCY_CONFLICT",
        "INVALID_PARAMS",
    })

    def __init__(self, error_type: str, message: str):
        super().__init__(f"[{error_type}] {message}")
        self.error_type = error_type
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.error_type in self.RETRYABLE


def _load_fixtures(fixtures_dir: str | Path) -> dict:
    """加载全部 fixture 文件，文件名即数据集名。"""
    base = Path(fixtures_dir)
    data: dict = {}
    for fp in sorted(base.glob("*.json")):
        data[fp.stem] = json.loads(fp.read_text(encoding="utf-8"))
    return data


class ToolGateway:
    """Mock 系统集合 + 统一调用入口。

    读工具可并发；可变状态、故障计数、回执与持久化由可重入锁保护。
    ``state_path`` 可选，用于 Docker/API 重启后恢复 Mock 台账和幂等状态。
    """

    def __init__(self, fixtures_dir: str | Path, finance_fail_times: int = 0,
                 *, signing_key: str | None = None,
                 state_path: str | Path | None = None,
                 verification_tamper_amount: str | Decimal = "0"):
        self.fixtures = _load_fixtures(fixtures_dir)
        self._lock = RLock()
        self._state_path = Path(state_path) if state_path else None
        secret = signing_key or os.getenv(
            "REVGUARD_APPROVAL_SIGNING_KEY",
            "revguard-demo-signing-key-change-before-production-2026",
        )
        self._token_signer = CapabilityTokenSigner(secret)
        # 深拷贝可变系统状态，避免污染磁盘 fixtures，且支持重复运行
        self._ledger: list[dict] = copy.deepcopy(self.fixtures.get("ledger", {}).get("entries", []))
        self._adjustments: dict[str, dict] = {}       # action_id -> 调整单
        self._approvals: dict[str, dict] = {}          # approval_id -> 审批单
        self._idempotency: dict[str, str] = {}         # idempotency_key -> action_id
        self._outbox: list[dict] = []                  # 工单更新 / 邮件草稿
        self._receipts: list[dict] = []                # 全部调用回执
        self._token_consumed_amount: dict[str, str] = {}  # approval jti -> 已执行绝对金额
        self._token_consumed_by_component: dict[str, dict[str, str]] = {}
        self._used_rollback_tokens: set[str] = set()
        self._finance_fail_left = finance_fail_times   # 故障注入计数
        self._verification_tamper_amount = Decimal(str(verification_tamper_amount))
        self._verification_tamper_used = False
        self._load_state()

    # ------------------------------------------------------------------ 入口
    def call(self, tool_name: str, parameters: dict, *, case_id: str = "",
             actor: str = "", scope: list[str] | None = None,
             idempotency_key: str | None = None) -> dict:
        """统一工具调用契约（设计文档 13.1）。"""
        handler = getattr(self, f"_tool_{tool_name.replace('.', '_')}", None)
        receipt = {
            "tool_receipt": new_id("RCPT"),
            "tool_name": tool_name,
            "case_id": case_id,
            "actor": actor,
            "called_at": utc_now(),
        }
        try:
            if handler is None:
                raise ToolError("NOT_FOUND", f"未知工具: {tool_name}")
            try:
                required_scope = authorize_tool(actor, scope or [], tool_name)
            except SecurityError as exc:
                raise ToolError("AUTH_FAILED", str(exc)) from exc
            receipt["required_scope"] = required_scope
            data = handler(parameters or {}, scope=scope or [], idempotency_key=idempotency_key,
                           actor=actor, case_id=case_id)
            receipt["success"] = True
            with self._lock:
                self._receipts.append(receipt)
                self._persist_state()
            return {
                "success": True,
                "data": data,
                "error": None,
                "source_timestamp": utc_now(),
                "tool_receipt": receipt["tool_receipt"],
            }
        except ToolError as exc:
            receipt["success"] = False
            receipt["error_type"] = exc.error_type
            with self._lock:
                self._receipts.append(receipt)
                self._persist_state()
            return {
                "success": False,
                "data": None,
                "error": {"type": exc.error_type, "message": exc.message, "retryable": exc.retryable},
                "source_timestamp": utc_now(),
                "tool_receipt": receipt["tool_receipt"],
            }

    @property
    def receipts(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._receipts)

    @property
    def outbox(self) -> list[dict]:
        with self._lock:
            return copy.deepcopy(self._outbox)

    def reset_case(self, case_id: str) -> None:
        """Reset mutable mock-side effects for one recording case only."""
        source = f"REVGUARD:{case_id}"
        with self._lock:
            action_ids = {
                action_id for action_id, draft in self._adjustments.items()
                if draft.get("case_id") == case_id
            }
            self._adjustments = {
                action_id: draft
                for action_id, draft in self._adjustments.items()
                if draft.get("case_id") != case_id
            }
            self._ledger = [
                entry for entry in self._ledger
                if entry.get("source") != source
            ]
            self._idempotency = {
                key: action_id
                for key, action_id in self._idempotency.items()
                if not key.startswith(f"{case_id}:")
                and action_id not in action_ids
            }
            # Keep receipts and outbox entries as gateway-level history; they
            # are not reused for authorization and remain useful when auditing
            # the rejected attempt before the new run.
            self._persist_state()

    # ------------------------------------------------------------------- CRM
    def _tool_crm_get_order(self, p: dict, **_kw) -> dict:
        order = self._find("orders", "order_id", p.get("order_id"))
        if not order:
            raise ToolError("NOT_FOUND", f"订单不存在: {p.get('order_id')}")
        return copy.deepcopy(order)

    def _tool_crm_get_partner(self, p: dict, **_kw) -> dict:
        partner = self._find("partners", "partner_id", p.get("partner_id"))
        if not partner and p.get("partner_name"):
            partner = next((x for x in self.fixtures.get("partners", [])
                            if x.get("name") == p["partner_name"]), None)
        if not partner:
            raise ToolError("NOT_FOUND", f"代理商不存在: {p}")
        return copy.deepcopy(partner)

    def _tool_crm_get_partner_tier_history(self, p: dict, **_kw) -> dict:
        partner = self._tool_crm_get_partner(p)
        return {"partner_id": partner["partner_id"], "tier_history": partner.get("tier_history", [])}

    def _tool_crm_list_orders_by_partner(self, p: dict, **_kw) -> dict:
        orders = [o for o in self.fixtures.get("orders", [])
                  if o.get("partner_id") == p.get("partner_id")]
        return {"orders": copy.deepcopy(orders), "count": len(orders)}

    # ------------------------------------------------------------------ 合同
    def _tool_contract_get_contract(self, p: dict, **_kw) -> dict:
        contract = self._find("contracts", "contract_id", p.get("contract_id"))
        if not contract and p.get("partner_id"):
            contract = next((c for c in self.fixtures.get("contracts", [])
                             if c.get("partner_id") == p["partner_id"]), None)
        if not contract:
            raise ToolError("NOT_FOUND", f"合同不存在: {p}")
        return copy.deepcopy(contract)

    def _tool_contract_get_effective_terms(self, p: dict, **_kw) -> dict:
        contract = self._tool_contract_get_contract(p)
        return {"contract_id": contract["contract_id"], "terms": contract.get("terms", {})}

    # ------------------------------------------------------------------ 政策
    def _tool_policy_search_versions(self, p: dict, **_kw) -> dict:
        versions = [v for v in self.fixtures.get("policies", {}).get("versions", [])
                    if v.get("policy_id") == p.get("policy_id")]
        return {"versions": copy.deepcopy(versions)}

    def _tool_policy_get_rule_dsl(self, p: dict, **_kw) -> dict:
        for v in self.fixtures.get("policies", {}).get("versions", []):
            if v.get("policy_id") == p.get("policy_id") and v.get("version") == p.get("version"):
                return copy.deepcopy(v.get("rule_dsl", {}))
        raise ToolError("NOT_FOUND", f"政策版本不存在: {p}")

    # ------------------------------------------------------------------- 财务
    def _maybe_fail_finance(self) -> None:
        """故障注入：前 N 次财务调用返回可重试错误，用于演示工具重试链路。"""
        with self._lock:
            if self._finance_fail_left > 0:
                self._finance_fail_left -= 1
                raise ToolError("TOOL_UNAVAILABLE", "财务系统暂时不可用（故障注入）")

    def _tool_finance_get_payment(self, p: dict, **_kw) -> dict:
        self._maybe_fail_finance()
        payment = next((x for x in self.fixtures.get("payments", [])
                        if x.get("order_id") == p.get("order_id")), None)
        if not payment:
            raise ToolError("NOT_FOUND", f"回款记录不存在: {p}")
        return copy.deepcopy(payment)

    def _tool_finance_get_refund(self, p: dict, **_kw) -> dict:
        self._maybe_fail_finance()
        refunds = [x for x in self.fixtures.get("refunds", []) if x.get("order_id") == p.get("order_id")]
        total = sum((Decimal(str(x.get("refund_amount", 0))) for x in refunds), Decimal("0"))
        return {"refunds": copy.deepcopy(refunds), "refund_amount": str(total)}

    def _tool_finance_get_invoice(self, p: dict, **_kw) -> dict:
        self._maybe_fail_finance()
        invoice = next((x for x in self.fixtures.get("invoices", [])
                        if x.get("order_id") == p.get("order_id")), None)
        if not invoice:
            raise ToolError("NOT_FOUND", f"发票不存在: {p}")
        return copy.deepcopy(invoice)

    def _tool_finance_get_commission_ledger(self, p: dict, *, actor="", **_kw) -> dict:
        self._maybe_fail_finance()
        with self._lock:
            entries = copy.deepcopy([
                e for e in self._ledger if e.get("order_id") == p.get("order_id")
            ])
            if (actor == "revguard-verifier" and not self._verification_tamper_used
                    and self._verification_tamper_amount != 0 and entries):
                # 仅篡改一次“查询结果”，不污染真实台账；用于验证失败→回滚闭环评测。
                entries[-1]["amount"] = str(
                    Decimal(str(entries[-1].get("amount", 0))) + self._verification_tamper_amount
                )
                self._verification_tamper_used = True
            total = sum((Decimal(str(e.get("amount", 0))) for e in entries
                         if e.get("status") == "POSTED"), Decimal("0"))
        return {"entries": entries, "posted_total": str(total)}

    # -------------------------------------------------------------- 佣金执行
    def _tool_commission_create_adjustment_draft(self, p: dict, **kw) -> dict:
        action_id = new_id("ACT")
        draft = {
            "action_id": action_id,
            "order_id": p.get("order_id"),
            "case_id": p.get("case_id"),
            "component": p.get("component", "ADJUSTMENT"),
            "amount": str(p.get("amount")),
            "currency": p.get("currency"),
            "reason": p.get("reason", ""),
            "status": "DRAFT",  # 草稿不生效（L1 安全边界）
            "created_at": utc_now(),
        }
        with self._lock:
            self._adjustments[action_id] = draft
            self._persist_state()
        return copy.deepcopy(draft)

    def _tool_commission_submit_adjustment(self, p: dict, *, idempotency_key=None,
                                           actor="", case_id="", **_kw) -> dict:
        """提交调整单写入台账。强制幂等键 + 审批凭证校验。"""
        if not idempotency_key:
            raise ToolError("INVALID_PARAMS", "写操作必须携带幂等键")
        token = p.get("approval_token")
        if not token:
            raise ToolError("AUTH_FAILED", "提交调整必须携带有效审批凭证")
        with self._lock:
            if idempotency_key in self._idempotency:
                raise ToolError("IDEMPOTENCY_CONFLICT",
                                f"幂等键已使用: {idempotency_key} -> {self._idempotency[idempotency_key]}")
            draft = self._adjustments.get(p.get("action_id", ""))
            if not draft:
                raise ToolError("NOT_FOUND", f"调整草稿不存在: {p.get('action_id')}")
            if draft["status"] != "DRAFT":
                raise ToolError("DATA_CONFLICT", f"调整单状态不允许提交: {draft['status']}")
            try:
                claims = self._token_signer.verify(token, purpose="ledger_adjust")
            except SecurityError as exc:
                raise ToolError("AUTH_FAILED", str(exc)) from exc
            if actor != "revguard-executor":
                raise ToolError("AUTH_FAILED", "只有 revguard-executor 可提交调整")
            if claims.get("case_id") != draft.get("case_id") or case_id != draft.get("case_id"):
                raise ToolError("AUTH_FAILED", "审批凭证与案件不匹配")
            if claims.get("currency") != draft.get("currency"):
                raise ToolError("AUTH_FAILED", "审批凭证与币种不匹配")
            approval = self._approvals.get(str(claims.get("approval_id", "")))
            if not approval or approval.get("status") != "APPROVED":
                raise ToolError("AUTH_FAILED", "审批单不存在或未批准")
            jti = str(claims.get("jti", ""))
            approved_amount = Decimal(str(claims.get("max_amount", "0")))
            consumed = Decimal(self._token_consumed_amount.get(jti, "0"))
            requested = abs(Decimal(str(draft["amount"])))
            if requested <= 0 or consumed + requested > approved_amount:
                raise ToolError("AUTH_FAILED", "提交金额超过审批凭证授权额度")
            component = str(draft.get("component", ""))
            quotas = claims.get("component_quota")
            if not isinstance(quotas, dict) or component not in quotas:
                raise ToolError("AUTH_FAILED", "审批凭证未授权该佣金组件")
            component_limit = Decimal(str(quotas[component]))
            component_usage = self._token_consumed_by_component.get(jti, {})
            component_consumed = Decimal(str(component_usage.get(component, "0")))
            if component_consumed + requested > component_limit:
                raise ToolError("AUTH_FAILED", "提交金额超过该组件审批额度")

            # 执行前快照 -> 写台账 -> 执行后快照（设计文档 7.6）
            before = [e for e in self._ledger if e.get("order_id") == draft["order_id"]]
            entry = {
                "ledger_id": new_id("LED"),
                "order_id": draft["order_id"],
                "component": draft["component"],
                "amount": draft["amount"],
                "currency": draft["currency"],
                "policy_version": p.get("policy_version"),
                "status": "POSTED",
                "source": f"REVGUARD:{draft.get('case_id')}",
                "posted_at": utc_now(),
            }
            self._ledger.append(entry)
            draft["status"] = "SUBMITTED"
            self._idempotency[idempotency_key] = draft["action_id"]
            self._token_consumed_amount[jti] = str(consumed + requested)
            self._token_consumed_by_component.setdefault(jti, {})[component] = str(
                component_consumed + requested
            )
            rollback_token = self._token_signer.issue("ledger_reverse", {
                "case_id": draft["case_id"],
                "ledger_id": entry["ledger_id"],
                "action_id": draft["action_id"],
                "currency": draft["currency"],
            }, ttl_seconds=3600)
            after = [e for e in self._ledger if e.get("order_id") == draft["order_id"]]
            self._persist_state()
            return {
                "action_id": draft["action_id"],
                "status": "SUBMITTED",
                "ledger_entry": copy.deepcopy(entry),
                "before_snapshot": copy.deepcopy(before),
                "after_snapshot": copy.deepcopy(after),
                "rollback_token": rollback_token,
            }

    def _tool_commission_reverse_adjustment(self, p: dict, *, idempotency_key=None,
                                            actor="", case_id="", **_kw) -> dict:
        """冲销：新增一笔反向台账（不物理删除，保证可审计）。"""
        if not idempotency_key:
            raise ToolError("INVALID_PARAMS", "冲销必须携带幂等键")
        rollback_token = p.get("rollback_token")
        if not rollback_token:
            raise ToolError("AUTH_FAILED", "冲销必须携带回滚能力令牌")
        with self._lock:
            if idempotency_key in self._idempotency:
                raise ToolError("IDEMPOTENCY_CONFLICT", f"幂等键已使用: {idempotency_key}")
            try:
                claims = self._token_signer.verify(rollback_token, purpose="ledger_reverse")
            except SecurityError as exc:
                raise ToolError("AUTH_FAILED", str(exc)) from exc
            if actor != "revguard-executor":
                raise ToolError("AUTH_FAILED", "只有 revguard-executor 可执行冲销")
            if claims.get("case_id") != case_id or claims.get("ledger_id") != p.get("ledger_id"):
                raise ToolError("AUTH_FAILED", "回滚令牌与案件或台账记录不匹配")
            jti = str(claims.get("jti", ""))
            if jti in self._used_rollback_tokens:
                raise ToolError("AUTH_FAILED", "回滚令牌已使用")
            target = self._find_ledger(p.get("ledger_id"))
            if not target:
                raise ToolError("NOT_FOUND", f"台账记录不存在: {p.get('ledger_id')}")
            if target.get("reversed_by"):
                raise ToolError("DATA_CONFLICT", "台账记录已经冲销")
            reversal = copy.deepcopy(target)
            reversal["ledger_id"] = new_id("LED")
            reversal["amount"] = str(-Decimal(str(target["amount"])))
            reversal["status"] = "POSTED"
            reversal["reversal_of"] = target["ledger_id"]
            reversal["source"] = f"REVGUARD:{case_id}"
            reversal["posted_at"] = utc_now()
            self._ledger.append(reversal)
            target["reversed_by"] = reversal["ledger_id"]
            self._idempotency[idempotency_key] = reversal["ledger_id"]
            self._used_rollback_tokens.add(jti)
            self._persist_state()
            return {"reversal_entry": copy.deepcopy(reversal),
                    "reversed_entry": copy.deepcopy(target)}

    # ------------------------------------------------------------------ 审批
    def _tool_workflow_create_approval(self, p: dict, **_kw) -> dict:
        raw_quota = p.get("component_quota")
        if not isinstance(raw_quota, dict) or not raw_quota:
            raise ToolError("INVALID_PARAMS", "审批必须绑定非空 component_quota")
        try:
            component_quota = {
                str(component): str(Decimal(str(amount)))
                for component, amount in raw_quota.items()
                if Decimal(str(amount)) > 0
            }
            approved_amount = Decimal(str(p.get("amount")))
        except (ArithmeticError, ValueError, TypeError) as exc:
            raise ToolError("INVALID_PARAMS", "审批金额或组件额度不是有效数字") from exc
        if len(component_quota) != len(raw_quota):
            raise ToolError("INVALID_PARAMS", "组件额度必须全部大于 0")
        if sum((Decimal(value) for value in component_quota.values()), Decimal("0")) != approved_amount:
            raise ToolError("INVALID_PARAMS", "组件额度之和必须等于审批总额度")
        approval_id = new_id("APR")
        approval = {
            "approval_id": approval_id,
            "case_id": p.get("case_id"),
            "action_summary": p.get("action_summary"),
            "amount": str(p.get("amount")),
            "component_quota": component_quota,
            "currency": p.get("currency"),
            "risk_level": p.get("risk_level"),
            "approver_role": p.get("approver_role"),
            "status": "PENDING",
            "created_at": utc_now(),
        }
        with self._lock:
            self._approvals[approval_id] = approval
            self._persist_state()
        return copy.deepcopy(approval)

    def _tool_workflow_get_approval_status(self, p: dict, **_kw) -> dict:
        with self._lock:
            approval = self._approvals.get(p.get("approval_id", ""))
            if not approval:
                raise ToolError("NOT_FOUND", f"审批单不存在: {p.get('approval_id')}")
            return copy.deepcopy(approval)

    def _tool_workflow_decide_approval(self, p: dict, *, actor="", **_kw) -> dict:
        """模拟人工审批动作（演示环境专用；生产对接真实审批系统）。"""
        with self._lock:
            approval = self._approvals.get(p.get("approval_id", ""))
            if not approval:
                raise ToolError("NOT_FOUND", f"审批单不存在: {p.get('approval_id')}")
            if approval["status"] != "PENDING":
                raise ToolError("DATA_CONFLICT", f"审批单已处理: {approval['status']}")
            decision = p.get("decision", "REJECTED")
            approval["status"] = "APPROVED" if decision == "APPROVED" else "REJECTED"
            # 审批人身份由可信 actor 决定，请求参数仅作兼容，不参与授权。
            approval["approver"] = actor
            human_subject = str(p.get("human_subject") or "")
            if human_subject:
                approval["human_identity"] = {
                    "sub": human_subject,
                    "display_name": str(p.get("human_display_name") or human_subject),
                    "auth_time": p.get("human_auth_time"),
                    "auth_method": str(p.get("human_auth_method") or "matrix-password"),
                }
            approval["comment"] = p.get("comment", "")
            approval["decided_at"] = utc_now()
            if approval["status"] == "APPROVED":
                approval["approval_token"] = self._token_signer.issue("ledger_adjust", {
                    "approval_id": approval["approval_id"],
                    "case_id": approval["case_id"],
                    "max_amount": approval["amount"],
                    "component_quota": approval["component_quota"],
                    "currency": approval["currency"],
                    "risk_level": approval["risk_level"],
                    "approver": actor,
                    "approver_role": approval["approver_role"],
                    "human_subject": human_subject,
                    "human_auth_time": p.get("human_auth_time"),
                }, ttl_seconds=900)
            self._persist_state()
            return copy.deepcopy(approval)

    def _tool_workflow_renew_approval_capability(
        self, p: dict, *, actor="", **_kw,
    ) -> dict:
        """Re-authorise only the unconsumed portion of an approved decision.

        A process restart must not silently extend a short-lived capability.
        Recovery therefore requires the trusted approver again, and the new
        token is bounded to components that have not already been submitted.
        """
        with self._lock:
            approval = self._approvals.get(p.get("approval_id", ""))
            if not approval:
                raise ToolError("NOT_FOUND", f"审批单不存在: {p.get('approval_id')}")
            if approval.get("status") != "APPROVED":
                raise ToolError("DATA_CONFLICT", "只有已批准审批单可重新授权")
            if p.get("case_id") and p["case_id"] != approval.get("case_id"):
                raise ToolError("AUTH_FAILED", "审批单与案件不匹配")

            consumed: dict[str, Decimal] = {}
            for draft in self._adjustments.values():
                if (draft.get("case_id") != approval.get("case_id")
                        or draft.get("status") != "SUBMITTED"):
                    continue
                component = str(draft.get("component", ""))
                consumed[component] = consumed.get(component, Decimal("0")) + abs(
                    Decimal(str(draft.get("amount", "0")))
                )
            remaining_quota = {
                component: str(max(
                    Decimal(str(limit)) - consumed.get(component, Decimal("0")),
                    Decimal("0"),
                ))
                for component, limit in approval.get("component_quota", {}).items()
            }
            remaining_quota = {
                component: amount for component, amount in remaining_quota.items()
                if Decimal(amount) > 0
            }
            remaining_amount = sum(
                (Decimal(amount) for amount in remaining_quota.values()),
                Decimal("0"),
            )
            token = ""
            if remaining_amount > 0:
                token = self._token_signer.issue("ledger_adjust", {
                    "approval_id": approval["approval_id"],
                    "case_id": approval["case_id"],
                    "max_amount": str(remaining_amount),
                    "component_quota": remaining_quota,
                    "currency": approval["currency"],
                    "risk_level": approval["risk_level"],
                    "approver": actor,
                    "approver_role": approval["approver_role"],
                    "renewal": True,
                }, ttl_seconds=900)
            approval["approval_token"] = token
            approval["capability_renewed_at"] = utc_now()
            approval["capability_renewed_by"] = actor
            approval["remaining_component_quota"] = remaining_quota
            self._persist_state()
            return copy.deepcopy(approval)

    def _tool_workflow_renew_rollback_capability(
        self, p: dict, *, actor="", **_kw,
    ) -> dict:
        """Re-authorise reversal of one still-active RevGuard ledger write."""
        with self._lock:
            case_id = str(p.get("case_id", ""))
            ledger_id = str(p.get("ledger_id", ""))
            action_id = str(p.get("action_id", ""))
            entry = next(
                (item for item in self._ledger if item.get("ledger_id") == ledger_id),
                None,
            )
            draft = self._adjustments.get(action_id)
            if not entry or not draft:
                raise ToolError("NOT_FOUND", "待回滚台账或调整单不存在")
            if (draft.get("case_id") != case_id
                    or entry.get("source") != f"REVGUARD:{case_id}"
                    or draft.get("action_id") != action_id):
                raise ToolError("AUTH_FAILED", "回滚对象与案件不匹配")
            if entry.get("reversed_by"):
                raise ToolError("DATA_CONFLICT", "该台账记录已回滚")
            token = self._token_signer.issue("ledger_reverse", {
                "case_id": case_id,
                "ledger_id": ledger_id,
                "action_id": action_id,
                "currency": entry.get("currency"),
                "renewal": True,
                "authorised_by": actor,
            }, ttl_seconds=3600)
            return {
                "case_id": case_id,
                "ledger_id": ledger_id,
                "action_id": action_id,
                "rollback_token": token,
                "renewed_at": utc_now(),
                "renewed_by": actor,
            }

    # -------------------------------------------------------------- 工单/邮件
    def _tool_ticket_update_case(self, p: dict, **_kw) -> dict:
        record = {"system": "TICKET", "payload": copy.deepcopy(p), "at": utc_now()}
        with self._lock:
            self._outbox.append(record)
            self._persist_state()
        return {"updated": True, "ticket_ref": p.get("ticket_ref", "TICKET-MOCK")}

    def _tool_mail_create_reply_draft(self, p: dict, **_kw) -> dict:
        record = {"system": "MAIL", "payload": copy.deepcopy(p), "at": utc_now()}
        with self._lock:
            self._outbox.append(record)
            self._persist_state()
        return {"draft_id": new_id("MAIL"), "status": "DRAFT"}

    # ------------------------------------------------------------------ 内部
    def _find(self, dataset: str, key: str, value):
        if value is None:
            return None
        return next((x for x in self.fixtures.get(dataset, []) if x.get(key) == value), None)

    def _find_ledger(self, ledger_id: str | None):
        return next((e for e in self._ledger if e.get("ledger_id") == ledger_id), None)

    def _load_state(self) -> None:
        if not self._state_path or not self._state_path.exists():
            return
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"无法加载 ToolGateway 状态 {self._state_path}: {exc}") from exc
        self._ledger = state.get("ledger", self._ledger)
        self._adjustments = state.get("adjustments", {})
        self._approvals = state.get("approvals", {})
        self._idempotency = state.get("idempotency", {})
        self._outbox = state.get("outbox", [])
        self._receipts = state.get("receipts", [])
        self._token_consumed_amount = state.get("token_consumed_amount", {})
        self._token_consumed_by_component = state.get("token_consumed_by_component", {})
        self._used_rollback_tokens = set(state.get("used_rollback_tokens", []))
        # Older state files predate this field.  If a Verifier ledger read was
        # already receipted while tamper injection is enabled, infer that the
        # one-shot fault was consumed so a process restart cannot inject it again.
        self._verification_tamper_used = bool(state.get(
            "verification_tamper_used",
            self._verification_tamper_amount != 0 and any(
                item.get("actor") == "revguard-verifier"
                and item.get("tool_name") == "finance.get_commission_ledger"
                for item in self._receipts
            ),
        ))

    def _persist_state(self) -> None:
        if not self._state_path:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "ledger": self._ledger,
            "adjustments": self._adjustments,
            "approvals": self._approvals,
            "idempotency": self._idempotency,
            "outbox": self._outbox,
            "receipts": self._receipts,
            "token_consumed_amount": self._token_consumed_amount,
            "token_consumed_by_component": self._token_consumed_by_component,
            "used_rollback_tokens": sorted(self._used_rollback_tokens),
            "verification_tamper_used": self._verification_tamper_used,
        }
        tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)
