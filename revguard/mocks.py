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
import hashlib
import json
from pathlib import Path

from .models import new_id, utc_now


class ToolError(Exception):
    """工具调用失败，error_type 必须是约定枚举（设计文档 13.3）。"""

    RETRYABLE = {"TIMEOUT", "TOOL_UNAVAILABLE", "RATE_LIMITED"}
    FATAL = {"AUTH_FAILED", "NOT_FOUND", "DATA_CONFLICT", "IDEMPOTENCY_CONFLICT", "INVALID_PARAMS"}

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
    """Mock 系统集合 + 统一调用入口。线程不安全（Demo 单进程足够）。"""

    def __init__(self, fixtures_dir: str | Path, finance_fail_times: int = 0):
        self.fixtures = _load_fixtures(fixtures_dir)
        # 深拷贝可变系统状态，避免污染磁盘 fixtures，且支持重复运行
        self._ledger: list[dict] = copy.deepcopy(self.fixtures.get("ledger", {}).get("entries", []))
        self._adjustments: dict[str, dict] = {}       # action_id -> 调整单
        self._approvals: dict[str, dict] = {}          # approval_id -> 审批单
        self._idempotency: dict[str, str] = {}         # idempotency_key -> action_id
        self._outbox: list[dict] = []                  # 工单更新 / 邮件草稿
        self._receipts: list[dict] = []                # 全部调用回执
        self._finance_fail_left = finance_fail_times   # 故障注入计数

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
            data = handler(parameters or {}, scope=scope or [], idempotency_key=idempotency_key)
            receipt["success"] = True
            self._receipts.append(receipt)
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
            self._receipts.append(receipt)
            return {
                "success": False,
                "data": None,
                "error": {"type": exc.error_type, "message": exc.message, "retryable": exc.retryable},
                "source_timestamp": utc_now(),
                "tool_receipt": receipt["tool_receipt"],
            }

    @property
    def receipts(self) -> list[dict]:
        return list(self._receipts)

    @property
    def outbox(self) -> list[dict]:
        return list(self._outbox)

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
        total = sum(float(x.get("refund_amount", 0)) for x in refunds)
        return {"refunds": copy.deepcopy(refunds), "refund_amount": total}

    def _tool_finance_get_invoice(self, p: dict, **_kw) -> dict:
        self._maybe_fail_finance()
        invoice = next((x for x in self.fixtures.get("invoices", [])
                        if x.get("order_id") == p.get("order_id")), None)
        if not invoice:
            raise ToolError("NOT_FOUND", f"发票不存在: {p}")
        return copy.deepcopy(invoice)

    def _tool_finance_get_commission_ledger(self, p: dict, **_kw) -> dict:
        self._maybe_fail_finance()
        entries = [e for e in self._ledger if e.get("order_id") == p.get("order_id")]
        total = sum(float(e.get("amount", 0)) for e in entries if e.get("status") == "POSTED")
        return {"entries": copy.deepcopy(entries), "posted_total": total}

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
        self._adjustments[action_id] = draft
        return copy.deepcopy(draft)

    def _tool_commission_submit_adjustment(self, p: dict, *, idempotency_key=None, **_kw) -> dict:
        """提交调整单写入台账。强制幂等键 + 审批凭证校验。"""
        if not idempotency_key:
            raise ToolError("INVALID_PARAMS", "写操作必须携带幂等键")
        if idempotency_key in self._idempotency:
            raise ToolError("IDEMPOTENCY_CONFLICT",
                            f"幂等键已使用: {idempotency_key} -> {self._idempotency[idempotency_key]}")
        if not p.get("approval_token"):
            raise ToolError("AUTH_FAILED", "提交调整必须携带有效审批凭证")
        draft = self._adjustments.get(p.get("action_id", ""))
        if not draft:
            raise ToolError("NOT_FOUND", f"调整草稿不存在: {p.get('action_id')}")
        if draft["status"] != "DRAFT":
            raise ToolError("DATA_CONFLICT", f"调整单状态不允许提交: {draft['status']}")

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
        after = [e for e in self._ledger if e.get("order_id") == draft["order_id"]]
        return {
            "action_id": draft["action_id"],
            "status": "SUBMITTED",
            "ledger_entry": copy.deepcopy(entry),
            "before_snapshot": copy.deepcopy(before),
            "after_snapshot": copy.deepcopy(after),
            "rollback_token": new_id("RBK"),
        }

    def _tool_commission_reverse_adjustment(self, p: dict, *, idempotency_key=None, **_kw) -> dict:
        """冲销：新增一笔反向台账（不物理删除，保证可审计）。"""
        if not idempotency_key:
            raise ToolError("INVALID_PARAMS", "冲销必须携带幂等键")
        if idempotency_key in self._idempotency:
            raise ToolError("IDEMPOTENCY_CONFLICT", f"幂等键已使用: {idempotency_key}")
        target = self._find_ledger(p.get("ledger_id"))
        if not target:
            raise ToolError("NOT_FOUND", f"台账记录不存在: {p.get('ledger_id')}")
        reversal = copy.deepcopy(target)
        reversal["ledger_id"] = new_id("LED")
        reversal["amount"] = str(-float(target["amount"]))
        reversal["status"] = "POSTED"
        reversal["reversal_of"] = target["ledger_id"]
        reversal["source"] = f"REVGUARD:{p.get('case_id', '')}"
        reversal["posted_at"] = utc_now()
        self._ledger.append(reversal)
        # 会计惯例：原记录保留 POSTED 并标记被冲销，由冲销记录对冲（净额为 0），全程可审计
        target["reversed_by"] = reversal["ledger_id"]
        self._idempotency[idempotency_key] = reversal["ledger_id"]
        return {"reversal_entry": copy.deepcopy(reversal), "reversed_entry": copy.deepcopy(target)}

    # ------------------------------------------------------------------ 审批
    def _tool_workflow_create_approval(self, p: dict, **_kw) -> dict:
        approval_id = new_id("APR")
        approval = {
            "approval_id": approval_id,
            "case_id": p.get("case_id"),
            "action_summary": p.get("action_summary"),
            "amount": str(p.get("amount")),
            "currency": p.get("currency"),
            "risk_level": p.get("risk_level"),
            "approver_role": p.get("approver_role"),
            "status": "PENDING",
            "created_at": utc_now(),
        }
        self._approvals[approval_id] = approval
        return copy.deepcopy(approval)

    def _tool_workflow_get_approval_status(self, p: dict, **_kw) -> dict:
        approval = self._approvals.get(p.get("approval_id", ""))
        if not approval:
            raise ToolError("NOT_FOUND", f"审批单不存在: {p.get('approval_id')}")
        return copy.deepcopy(approval)

    def _tool_workflow_decide_approval(self, p: dict, **_kw) -> dict:
        """模拟人工审批动作（演示环境专用；生产对接真实审批系统）。"""
        approval = self._approvals.get(p.get("approval_id", ""))
        if not approval:
            raise ToolError("NOT_FOUND", f"审批单不存在: {p.get('approval_id')}")
        if approval["status"] != "PENDING":
            raise ToolError("DATA_CONFLICT", f"审批单已处理: {approval['status']}")
        decision = p.get("decision", "REJECTED")
        approval["status"] = "APPROVED" if decision == "APPROVED" else "REJECTED"
        approval["approver"] = p.get("approver", "unknown")
        approval["comment"] = p.get("comment", "")
        approval["decided_at"] = utc_now()
        if approval["status"] == "APPROVED":
            # 审批凭证：执行时校验，伪造凭证会被拒绝（设计文档 14.3）
            token_src = f"{approval['approval_id']}:{approval['approver']}:{approval['decided_at']}"
            approval["approval_token"] = "ATK-" + hashlib.sha256(token_src.encode()).hexdigest()[:24]
        return copy.deepcopy(approval)

    # -------------------------------------------------------------- 工单/邮件
    def _tool_ticket_update_case(self, p: dict, **_kw) -> dict:
        record = {"system": "TICKET", "payload": copy.deepcopy(p), "at": utc_now()}
        self._outbox.append(record)
        return {"updated": True, "ticket_ref": p.get("ticket_ref", "TICKET-MOCK")}

    def _tool_mail_create_reply_draft(self, p: dict, **_kw) -> dict:
        record = {"system": "MAIL", "payload": copy.deepcopy(p), "at": utc_now()}
        self._outbox.append(record)
        return {"draft_id": new_id("MAIL"), "status": "DRAFT"}

    # ------------------------------------------------------------------ 内部
    def _find(self, dataset: str, key: str, value):
        if value is None:
            return None
        return next((x for x in self.fixtures.get(dataset, []) if x.get(key) == value), None)

    def _find_ledger(self, ledger_id: str | None):
        return next((e for e in self._ledger if e.get("ledger_id") == ledger_id), None)
