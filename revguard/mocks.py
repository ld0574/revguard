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
import time
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import ClassVar

from .adapters import (
    ADAPTER_METRICS,
    ENTERPRISE_QUERY_TOOLS,
    EnterpriseAdapterError,
    ProviderRegistry,
)
from .commitment import approval_commitment, approval_digest
from .execution_reference import (
    attach_receipt_reference,
    reference_anchor,
    require_execution_references_from_env,
    verify_execution_references,
)
from .models import CaseStatus, new_id, utc_now
from .money_journal import MONEY_TOOLS, MoneyJournal, RecoveryRequired, request_hash
from .security import CapabilityTokenSigner, SecurityError, authorize_tool
from .state_machine import StaleCaseTransition, locked_case, persist_case_transition
from .task_guard import assert_active_claim


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
                 verification_tamper_amount: str | Decimal = "0",
                 store=None, posting_tamper_amount: str | Decimal = "0",
                 posting_tamper_case_ids: str | list[str] | tuple[str, ...] = "",
                 provider_registry: ProviderRegistry | None = None,
                 require_execution_references: bool | None = None):
        self.fixtures = _load_fixtures(fixtures_dir)
        self.providers = provider_registry or ProviderRegistry.from_env()
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
        self._posting_tamper_amount = Decimal(str(posting_tamper_amount))
        self._posting_tamper_used = False
        if isinstance(posting_tamper_case_ids, str):
            tamper_cases = posting_tamper_case_ids.split(",")
        else:
            tamper_cases = posting_tamper_case_ids
        self._posting_tamper_case_ids = frozenset(
            str(case_id).strip() for case_id in tamper_cases if str(case_id).strip()
        )
        self._require_execution_references = (
            require_execution_references_from_env()
            if require_execution_references is None
            else bool(require_execution_references)
        )
        self._execution_results: dict[str, dict] = {}
        self._recording_epochs: dict[str, int] = {}
        self._in_transaction = False
        self._initial_state = copy.deepcopy(self._state_snapshot())
        self._initial_finance_fail_times = finance_fail_times
        self._owns_store = store is None
        if store is None:
            from .store import Store
            store = Store(str(self._state_path) + ".money.sqlite3" if self._state_path else ":memory:")
        self.journal = MoneyJournal(store)
        with self.journal.transaction() as tx:
            durable_state = tx.state()
        if durable_state is None:
            self._load_state()
        else:
            self._apply_state(durable_state)
        if durable_state is None and not self._execution_results and not self._owns_store:
            for case in store.list_cases():
                for execution in store.list_executions(case["case_id"]):
                    if execution.get("idempotency_key"):
                        self._execution_results[execution["idempotency_key"]] = execution
        self.journal.initialize(self._state_snapshot())
        with self.journal.transaction() as tx:
            self._apply_state(tx.state())

    # ------------------------------------------------------------------ 入口
    def attach_store(self, store) -> None:
        """Bind fresh reference harnesses to the same transaction database."""
        if self.journal.store is store:
            return
        if not self._owns_store:
            raise RuntimeError("Gateway is already bound to another Store")
        with self.journal.transaction() as tx:
            if tx.execute("SELECT operation_id FROM money_operations LIMIT 1").fetchone():
                raise RuntimeError("Cannot move a live money journal between databases")
            state = tx.state()
        self.journal.store.close()
        self.journal = MoneyJournal(store)
        self.journal.initialize(state)
        self._owns_store = False

    def call(self, tool_name: str, parameters: dict, *, case_id: str = "",
             actor: str = "", scope: list[str] | None = None,
             idempotency_key: str | None = None) -> dict:
        started = time.monotonic()
        receipt = {"tool_receipt": new_id("RCPT"), "tool_name": tool_name,
                   "case_id": case_id, "actor": actor, "called_at": utc_now()}
        operation = None
        source_metadata: dict = {}
        provider_hint = (
            self.providers.enterprise_provider
            if tool_name in ENTERPRISE_QUERY_TOOLS
            else "revguard"
        )
        try:
            try:
                receipt["required_scope"] = authorize_tool(actor, scope or [], tool_name)
            except SecurityError as exc:
                raise ToolError("AUTH_FAILED", str(exc)) from exc
            use_adapter = self.providers.handles(tool_name)
            handler = getattr(self, f"_tool_{tool_name.replace('.', '_')}", None)
            if handler is None and not use_adapter:
                raise ToolError("NOT_FOUND", f"未知工具: {tool_name}")
            with self._lock:
                with self.journal.transaction() as tx:
                    assert_active_claim(tx, case_id)
                    self._apply_state(tx.state())
                    epoch = self._recording_epochs.get(case_id, 0)
                    if tool_name in MONEY_TOOLS and epoch and idempotency_key:
                        idempotency_key = f"{idempotency_key}:recording-{epoch}"
                    if tool_name in MONEY_TOOLS:
                        if not idempotency_key:
                            raise ToolError("INVALID_PARAMS", "资金操作必须携带幂等键")
                        if tool_name == "commission.submit_adjustment":
                            target = self._adjustments.get(parameters.get("action_id"), {})
                        else:
                            target = self._find_ledger(parameters.get("ledger_id")) or {}
                        channel = "order:" + str(target.get("order_id") or case_id)
                if tool_name in MONEY_TOOLS:
                    operation = self.journal.prepare(
                        idempotency_key, case_id, channel, tool_name,
                        request_hash(tool_name, case_id, parameters),
                    )
                with self.journal.transaction() as tx:
                    assert_active_claim(tx, case_id)
                    self._apply_state(tx.state())
                    self._in_transaction = True
                    try:
                        if operation:
                            current = tx.operation(idempotency_key)
                            self.journal.check_recovery_lock()
                            if current["generation"] != operation["generation"]:
                                raise RecoveryRequired("旧执行者已被恢复操作隔离")
                            if current["status"] == "COMMITTED":
                                data = current["result"]
                            elif current["status"] == "PREPARED":
                                before_ids = {e["ledger_id"] for e in self._ledger}
                                data = handler(parameters, scope=scope or [], idempotency_key=idempotency_key,
                                               actor=actor, case_id=case_id)
                                for entry in self._ledger:
                                    if entry["ledger_id"] not in before_ids:
                                        entry["operation_id"] = idempotency_key
                                source_metadata = self.providers.local_metadata(
                                    tool_name, data, utc_now(), receipt["tool_receipt"]
                                )
                                receipt.update(source_metadata)
                                receipt["success"] = True
                                self._receipts.append(receipt)
                                tx.commit_result(current, data, self._state_snapshot())
                            else:
                                raise RecoveryRequired("资金操作需要重新核对")
                        else:
                            if use_adapter:
                                adapter_result = self.providers.call(tool_name, parameters or {})
                                data = adapter_result.data
                                source_metadata = adapter_result.metadata()
                            else:
                                data = handler(parameters or {}, scope=scope or [], idempotency_key=idempotency_key,
                                               actor=actor, case_id=case_id)
                                timestamp = utc_now()
                                source_metadata = self.providers.local_metadata(
                                    tool_name, data, timestamp, receipt["tool_receipt"]
                                )
                            receipt.update(source_metadata)
                            receipt["success"] = True
                            # 执行引用监视器：成功读取即留下事实引用，供执行时比对。
                            attach_receipt_reference(
                                receipt, tool_name=tool_name,
                                parameters=parameters or {}, data=data,
                            )
                            self._receipts.append(receipt)
                            tx.save_state(self._state_snapshot())
                    finally:
                        self._in_transaction = False
            if not source_metadata:
                timestamp = utc_now()
                source_metadata = self.providers.local_metadata(
                    tool_name, data, timestamp, receipt["tool_receipt"]
                )
                receipt.update(source_metadata)
            ADAPTER_METRICS.observe(
                source_metadata.get("provider", provider_hint), tool_name, "success",
                source_metadata.get("latency_ms", int((time.monotonic() - started) * 1000)),
            )
            return {"success": True, "data": data, "error": None,
                    "tool_receipt": receipt["tool_receipt"], **source_metadata}
        except (ToolError, EnterpriseAdapterError, SecurityError, RecoveryRequired, ValueError) as exc:
            if operation and isinstance(exc, ToolError):
                # A handler validation failure rolls back the entire batch.
                with self.journal.transaction() as tx:
                    tx.execute("UPDATE money_operations SET status='NOT_COMMITTED',generation=generation+1 "
                               "WHERE operation_id=? AND status='PREPARED'", (idempotency_key,))
            if isinstance(exc, (ToolError, EnterpriseAdapterError)):
                error_type = exc.error_type
            elif isinstance(exc, RecoveryRequired):
                error_type = "RESULT_UNKNOWN"
            else:
                error_type = "DATA_CONFLICT"
            elapsed_ms = int((time.monotonic() - started) * 1000)
            ADAPTER_METRICS.observe(provider_hint, tool_name, error_type.lower(), elapsed_ms)
            return {"success": False, "data": None,
                    "error": {"type": error_type, "message": str(exc),
                              "retryable": error_type in ToolError.RETRYABLE and tool_name not in MONEY_TOOLS},
                    "source_timestamp": utc_now(), "tool_receipt": receipt["tool_receipt"],
                    "provider": provider_hint,
                    "external_document_type": None, "external_document_id": None,
                    "http_status": None, "latency_ms": elapsed_ms,
                    "payload_hash": None, "correlation_id": receipt["tool_receipt"],
                    "record_url": None,
                    "provenance_kind": (
                        "LIVE_SYSTEM" if provider_hint == "erpnext" else "SYNTHETIC_DOMAIN"
                    )}
        except Exception:
            if operation:
                self.journal.mark_unknown(idempotency_key)
            raise

    def _tool_commission_submit_adjustment(self, p: dict, *, idempotency_key=None,
                                           actor="", case_id="", **kw) -> dict:
        items = p.get("items") or [{"action_id": p.get("action_id"), "idempotency_key": idempotency_key}]
        if len({i.get("action_id") for i in items}) != len(items) or len({i.get("idempotency_key") for i in items}) != len(items):
            raise ToolError("INVALID_PARAMS", "批次包含重复资金操作")
        drafts = [self._adjustments.get(i.get("action_id"), {}) for i in items]
        if len({(d.get("case_id"), d.get("order_id"), d.get("currency")) for d in drafts}) != 1:
            raise ToolError("INVALID_PARAMS", "同一批次必须属于同案件、订单和币种")
        if p.get("action_id") != items[0].get("action_id"):
            raise ToolError("INVALID_PARAMS", "批次主操作与分项不匹配")
        baseline = None
        results, executions = [], []
        for item in items:
            result = self._submit_single({**p, "action_id": item["action_id"]},
                                         idempotency_key=item["idempotency_key"], actor=actor, case_id=case_id, **kw)
            draft = self._adjustments[item["action_id"]]
            if baseline is None:
                baseline = result["before_snapshot"]
            execution = {
                **result, "case_id": case_id, "action_type": "LEDGER_ADJUST",
                "amount": draft["amount"], "currency": draft["currency"], "component": draft["component"],
                "idempotency_key": item["idempotency_key"], "operation_id": idempotency_key,
                "before_snapshot": copy.deepcopy(baseline),
            }
            self._execution_results[item["idempotency_key"]] = execution
            executions.append(copy.deepcopy(execution))
            results.append(result)
        return {**results[0], "executions": executions, "operation_id": idempotency_key}

    def _tool_commission_reverse_adjustment(self, p: dict, *, idempotency_key=None,
                                            actor="", case_id="", **kw) -> dict:
        items = p.get("items") or [{"ledger_id": p.get("ledger_id"),
                                   "rollback_token": p.get("rollback_token"), "idempotency_key": idempotency_key}]
        if len({i.get("ledger_id") for i in items}) != len(items):
            raise ToolError("INVALID_PARAMS", "冲销批次包含重复分录")
        targets = [self._find_ledger(i.get("ledger_id")) or {} for i in items]
        if len({(e.get("source"), e.get("order_id"), e.get("currency")) for e in targets}) != 1:
            raise ToolError("INVALID_PARAMS", "冲销批次必须属于同一资金通道")
        if p.get("ledger_id") != items[0].get("ledger_id"):
            raise ToolError("INVALID_PARAMS", "冲销主操作与分项不匹配")
        results, executions = [], []
        for item in items:
            result = self._reverse_single(item, idempotency_key=item["idempotency_key"],
                                          actor=actor, case_id=case_id, **kw)
            execution = next((e for e in self._execution_results.values()
                              if (e.get("ledger_entry") or {}).get("ledger_id") == item["ledger_id"]), None)
            if execution:
                execution.update({"status": "ROLLED_BACK", "reversal": result["reversal_entry"],
                                  "compensation_operation_id": idempotency_key})
                executions.append(copy.deepcopy(execution))
            results.append(result)
        return {**results[0], "executions": executions,
                "reversals": [r["reversal_entry"] for r in results], "operation_id": idempotency_key}

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
        with self._lock:
            with self.journal.transaction() as tx:
                state = self._reset_case_state(tx.state(), case_id)
                if case_id in self._posting_tamper_case_ids:
                    state["posting_tamper_used"] = False
                tx.save_state(state)
            self._apply_state(state)

    @staticmethod
    def _reset_case_state(state: dict, case_id: str) -> dict:
        state = copy.deepcopy(state)
        epochs = state.setdefault("recording_epochs", {})
        epochs[case_id] = epochs.get(case_id, 0) + 1
        state["execution_results"] = {k: v for k, v in state.get("execution_results", {}).items()
                                      if v.get("case_id") != case_id}
        actions = {k for k, v in state.get("adjustments", {}).items() if v.get("case_id") == case_id}
        state["adjustments"] = {k: v for k, v in state.get("adjustments", {}).items() if k not in actions}
        state["ledger"] = [v for v in state["ledger"] if v.get("source") != f"REVGUARD:{case_id}"]
        state["idempotency"] = {k: v for k, v in state.get("idempotency", {}).items()
                                 if not k.startswith(f"{case_id}:") and v not in actions}
        return state

    def decide_case_approval(self, case: dict, parameters: dict, *, actor: str,
                             assertion_ref: str) -> dict:
        """Commit a verified human decision and its workflow handoff atomically.

        HTTP authenticates and binds the human proof before entering here.
        This transaction owns only our approval adapter/database; it is not a
        distributed commit with an external enterprise approval system.
        """
        authorize_tool(actor, ["approval:decide"], "workflow.decide_approval")
        if parameters.get("decision") not in {"APPROVED", "REJECTED"}:
            raise ToolError("INVALID_PARAMS", "无效的审批决定")
        case_id = case["case_id"]
        before = None
        with self._lock:
            try:
                with self.journal.transaction() as tx:
                    current = locked_case(tx, case_id)
                    if current != case or case.get("status") != "WAITING_FOR_APPROVAL":
                        raise StaleCaseTransition("审批案件已变化，请刷新后操作")
                    before = tx.state()
                    self._apply_state(copy.deepcopy(before))
                    approval = self._approvals.get(parameters.get("approval_id"), {})
                    if approval.get("case_id") != case_id:
                        raise ToolError("AUTH_FAILED", "审批单与案件不匹配")
                    self._in_transaction = True
                    try:
                        decided = self._tool_workflow_decide_approval(parameters, actor=actor)
                    finally:
                        self._in_transaction = False
                    decided["human_assertion_id_ref"] = assertion_ref
                    self._approvals[decided["approval_id"]] = copy.deepcopy(decided)
                    self._receipts.append({
                        "tool_receipt": new_id("RCPT"), "tool_name": "workflow.decide_approval",
                        "case_id": case_id, "actor": actor, "called_at": utc_now(),
                        "required_scope": "approval:decide", "success": True,
                    })
                    tx.save_state(self._state_snapshot())
                    store = self.journal.store
                    store._save_approval_with_conn(tx.conn, decided)
                    tx.audit(case_id, "APPROVAL_DECIDED", {
                        "decision": decided["status"], "identity_verified": True,
                        "human_subject": parameters.get("human_subject"),
                        "human_display_name": parameters.get("human_display_name"),
                        "human_auth_time": parameters.get("human_auth_time"),
                        "human_auth_method": parameters.get("human_auth_method"),
                        "matrix_event_id": parameters.get("matrix_event_id"),
                        "recording_id": parameters.get("recording_id"),
                        "assertion_id_ref": assertion_ref,
                    }, actor=actor)
                    approved = decided["status"] == "APPROVED"
                    pending = copy.deepcopy(case)
                    if approved and case.get("execution_mode") in {"MCP_TEAM", "AGENTTEAMS_MATRIX"}:
                        now = utc_now()
                        pending["team_run"] = {
                            **(case.get("team_run") or {}), "status": "QUEUED", "phase": "EXECUTION",
                            "current_stage": None, "total_tasks": 20, "queued_at": now,
                            "updated_at": now, "error": None,
                        }
                    if not approved:
                        store._cancel_open_agent_tasks_with_conn(
                            tx.conn, case_id, actor=actor, reason="人工审批驳回，禁止继续执行",
                        )
                    updated = persist_case_transition(
                        store, tx, pending, CaseStatus.READY_TO_EXECUTE if approved else CaseStatus.REJECTED,
                        "人工审批通过" if approved else "人工审批驳回", actor=actor,
                    )
            except BaseException:
                if before is not None:
                    self._apply_state(before)
                raise
            case.clear()
            case.update(updated)
            return decided

    def reprepare_case(self, fresh_case: dict, *, expected_case: dict, actor: str) -> None:
        """One database commit for the recording generation, gateway and audit.

        The HTTP caller also holds the exclusive runtime lease. Files belong
        to their recording generation and require no deletion for this commit.
        """
        case_id = fresh_case["case_id"]
        with self._lock:
            with self.journal.transaction() as tx:
                row = tx.execute("SELECT data FROM cases WHERE case_id=?", (case_id,)).fetchone()
                current = json.loads(row["data"]) if row and isinstance(row["data"], str) else row["data"] if row else None
                if current != expected_case or fresh_case.get("status") != "CREATED":
                    raise ValueError("案件快照已变化，拒绝重新准备")
                state = self._reset_case_state(tx.state(), case_id)
                tasks = tx.execute("SELECT task_id FROM agent_tasks WHERE case_id=? AND status IN "
                                   "('PENDING','RUNNING','WAITING_TOOL','WAITING_HUMAN','FAILED_RETRYABLE')", (case_id,)).fetchall()
                tx.save_state(state)
                self.journal.store._reset_case_with_conn(tx.conn, case_id)
                # The snapshot equality above authorizes a new generation;
                # carry its predecessor's revision into the guarded replace.
                self.journal.store._save_case_with_conn(
                    tx.conn, {**fresh_case, "_case_revision": current.get("_case_revision", 0)},
                    recording_replace=True,
                )
                tx.audit(case_id, "DEMO_CASE_REPREPARED", {
                    "previous_status": current["status"],
                    "previous_run_id": (current.get("team_run") or {}).get("run_id"),
                    "previous_recording_id": current.get("recording_id"),
                    "recording_id": fresh_case["recording_id"],
                    "cancelled_task_ids": [r["task_id"] for r in tasks],
                    "synthetic_business_data": True, "audit_history_preserved": True,
                }, actor=actor)
            self._apply_state(state)

    def reset_recording(self, seed_cases: list[tuple[dict, str]], *, actor: str | None = None) -> None:
        """Commit the full fixture and gateway baseline before updating cache."""
        with self._lock:
            baseline = copy.deepcopy(self._initial_state)
            reset_audit = (actor, {"synthetic_business_data": True,
                                   "verification_tamper_amount": str(self._verification_tamper_amount)}) if actor else None
            self.journal.store.reset(seed_cases=seed_cases, gateway_state=baseline, reset_audit=reset_audit)
            self._apply_state(baseline)
            self._finance_fail_left = self._initial_finance_fail_times

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

    def _tool_finance_get_commission_ledger(self, p: dict, *, actor="", case_id="", **_kw) -> dict:
        self._maybe_fail_finance()
        with self._lock:
            entries = copy.deepcopy([
                e for e in self._ledger if e.get("order_id") == p.get("order_id")
            ])
            if p.get("operation_scope") and actor == "revguard-verifier":
                executions = [e for e in self._execution_results.values() if e.get("case_id") == case_id]
                if executions:
                    baseline = copy.deepcopy(executions[0].get("before_snapshot", []))
                    own_ids = {(e.get("ledger_entry") or {}).get("ledger_id") for e in executions}
                    entries = baseline + [e for e in entries if e["ledger_id"] in own_ids or e.get("reversal_of") in own_ids]
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
        for existing in self._adjustments.values():
            if all(str(existing.get(k)) == str(p.get(k)) for k in
                   ("case_id", "order_id", "component", "amount", "currency")):
                if existing["status"] == "DRAFT":
                    return copy.deepcopy(existing)
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

    def _submit_single(self, p: dict, *, idempotency_key=None,
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
            # 审批 = 参数承诺：审批单摘要、令牌摘要与执行时重算摘要必须三方一致。
            committed = str(approval.get("parameters_digest") or "")
            if not committed:
                raise ToolError("AUTH_FAILED", "审批缺少参数承诺，拒绝执行")
            if str(claims.get("parameters_digest") or "") != committed:
                raise ToolError("AUTH_FAILED", "审批凭证与审批单参数承诺不一致（参数漂移）")
            if approval_digest(approval) != committed:
                raise ToolError("AUTH_FAILED", "审批参数已被改动（参数漂移）")
            approved_release = str(approval.get("release_version") or "")
            current_release = os.getenv("REVGUARD_RELEASE_VERSION", "")
            if approved_release and current_release and approved_release != current_release:
                raise ToolError(
                    "AUTH_FAILED",
                    f"审批版本 {approved_release} 与当前运行版本 {current_release} 不一致",
                )
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

            # 执行引用监视器：金额必须依据同案实际读取过的事实（可在执行前 fail-open 关闭）。
            execution_references: list[dict] = []
            if self._require_execution_references:
                execution_references, problems = verify_execution_references(
                    case_id=case_id, order_id=str(draft.get("order_id") or ""),
                    currency=str(draft.get("currency") or ""), receipts=self._receipts,
                )
                if problems:
                    raise ToolError(
                        "EVIDENCE_GAP",
                        "执行缺少事实引用：" + "；".join(problems),
                    )

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
                "execution_references": copy.deepcopy(execution_references),
                "reference_anchor": (
                    reference_anchor(execution_references) if execution_references else None
                ),
            }
            tamper_targets_case = (
                not self._posting_tamper_case_ids
                or draft["case_id"] in self._posting_tamper_case_ids
            )
            if (
                self._posting_tamper_amount
                and tamper_targets_case
                and not self._posting_tamper_used
            ):
                entry["amount"] = str(Decimal(entry["amount"]) + self._posting_tamper_amount)
                self._posting_tamper_used = True
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

    def _reverse_single(self, p: dict, *, idempotency_key=None,
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
            if target.get("reversal_of"):
                raise ToolError("DATA_CONFLICT", "禁止对冲销分录再次冲销")
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
            if p.get("matrix_event_id"):
                approval["matrix_event_id"] = str(p["matrix_event_id"])
            if p.get("recording_id"):
                approval["recording_id"] = str(p["recording_id"])
            approval["comment"] = p.get("comment", "")
            approval["decided_at"] = utc_now()
            if approval["status"] == "APPROVED":
                # 审批 = 参数承诺：把此刻批准的参数规范化成摘要，执行时二次比对。
                approval["release_version"] = os.getenv("REVGUARD_RELEASE_VERSION", "")
                approval["parameters_commitment"] = approval_commitment(approval)
                approval["parameters_digest"] = approval_digest(approval)
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
                    "parameters_digest": approval["parameters_digest"],
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
            # 重新授权同样受参数承诺约束：审批记录被改动后不得再签发能力令牌。
            committed = str(approval.get("parameters_digest") or "")
            if not committed:
                raise ToolError("AUTH_FAILED", "审批缺少参数承诺，拒绝重新授权")
            if approval_digest(approval) != committed:
                raise ToolError("AUTH_FAILED", "审批参数已被改动（参数漂移），拒绝重新授权")

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
            # no capability issued when the remaining quota is zero
            token = ""  # nosec B105
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
                    "parameters_digest": approval.get("parameters_digest"),
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
        self._apply_state(state)

    def _apply_state(self, state: dict) -> None:
        self._execution_results = state.get("execution_results", {})
        self._recording_epochs = state.get("recording_epochs", {})
        self._posting_tamper_used = bool(state.get("posting_tamper_used", False))
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
        if self._in_transaction or not hasattr(self, "journal"):
            return
        with self.journal.transaction() as tx:
            tx.save_state(self._state_snapshot())

    def _state_snapshot(self) -> dict:
        return {
            "execution_results": self._execution_results,
            "recording_epochs": self._recording_epochs,
            "posting_tamper_used": self._posting_tamper_used,
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
