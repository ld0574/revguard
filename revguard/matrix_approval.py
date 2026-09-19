"""Human approval replies carried by the authoritative AgentTeams room.

The room is treated as a transport for an explicitly addressed approval
request, not as a general chat command channel.  A valid decision must be a
reply to the request event, must come from the server-side human allow-list,
and must arrive before the request expires.  The API still owns the approval
transaction and the case state transition.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .matrix_team import MatrixClient, MatrixSettings, MatrixTransportError

LOGGER = logging.getLogger("revguard.matrix_approval")

APPROVAL_REQUEST_PREFIX = "【待审批】佣金差额调整"
APPROVAL_RESULT_PREFIX = "【审批结果】"

_COMPONENT_LABELS = {
    "SALES_COMMISSION": "销售佣金",
    "COLLECTION_COMMISSION": "回款佣金",
    "MONTHLY_INCENTIVE": "月度激励",
}
_ROOT_CAUSE_LABELS = {
    "WRONG_POLICY_VERSION": "使用了错误的政策版本",
    "MISSING_COMPONENT": "漏算佣金项目",
    "AMOUNT_MISMATCH": "入账金额与复算结果不一致",
    "TIER_EFFECTIVE_DATE_CONFLICT": "代理等级生效日期不匹配",
}
_ROLE_LABELS = {
    "FINANCE_LEAD": "财务负责人",
    "RISK_OWNER": "风险负责人",
}
_CASE_STATUS_LABELS = {
    "WAITING_FOR_APPROVAL": "等待审批",
    "READY_TO_EXECUTE": "等待执行",
    "EXECUTING": "正在执行",
    "VERIFYING": "正在独立复核",
    "REJECTED": "已驳回",
    "CLOSED": "已完成",
    "ROLLED_BACK": "已自动回滚",
}

_DECISION_RE = re.compile(
    r"^\s*(批准|同意|approve|approved|驳回|拒绝|reject|rejected)"
    r"(?:\s*[:：-]?\s*(.*))?\s*$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class PendingApprovalRequest:
    """The durable facts required to bind a room reply to one approval."""

    case_id: str
    approval_id: str
    request_event_id: str
    expires_at: int


@dataclass(frozen=True)
class MatrixApprovalReply:
    """A parsed, allow-listed reply to a pending approval request."""

    event_id: str
    sender: str
    request_event_id: str
    case_id: str
    approval_id: str
    decision: str
    comment: str
    origin_server_ts: int | None = None
    binding_mode: str = "reply"


def build_approval_request_message(
    case: Mapping[str, object],
    approval: Mapping[str, object],
    *,
    expires_at: int,
) -> str:
    """Build a human-readable, reply-addressed approval request for Element.

    Correlation data remains in the durable case/audit records and is not
    dumped into the room.  The room message carries only the business context
    a finance approver needs to make a decision without opening WebUI.
    """
    def text(value: object, fallback: str = "未提供", limit: int = 240) -> str:
        rendered = " ".join(str(value or "").split())
        return rendered[:limit] if rendered else fallback

    def money(value: object, currency: object) -> str:
        if value in (None, ""):
            return "未提供"
        try:
            rendered = f"{Decimal(str(value)):,.2f}"
        except (InvalidOperation, ValueError):
            rendered = text(value)
        return f"{rendered} {text(currency)}"

    def expiry(value: int) -> str:
        beijing = timezone(timedelta(hours=8))
        return datetime.fromtimestamp(value, tz=beijing).strftime("%Y-%m-%d %H:%M（北京时间）")

    claim = case.get("claim") if isinstance(case.get("claim"), Mapping) else {}
    facts = case.get("facts") if isinstance(case.get("facts"), Mapping) else {}
    policy = (
        case.get("policy_decision")
        if isinstance(case.get("policy_decision"), Mapping)
        else {}
    )
    calculation = (
        case.get("calculation_result")
        if isinstance(case.get("calculation_result"), Mapping)
        else {}
    )
    root_cause = (
        case.get("root_cause_report")
        if isinstance(case.get("root_cause_report"), Mapping)
        else {}
    )
    risk = (
        case.get("risk_decision")
        if isinstance(case.get("risk_decision"), Mapping)
        else {}
    )
    tier = case.get("tier_resolution")
    tier = tier.get("tier") if isinstance(tier, Mapping) else None
    run = case.get("team_run") if isinstance(case.get("team_run"), Mapping) else {}
    currency = approval.get("currency") or claim.get("currency") or calculation.get("currency")
    actual = root_cause.get("total_posted") or claim.get("actual_amount")
    expected = root_cause.get("total_expected") or calculation.get("total_commission") or claim.get("expected_amount")
    delta = root_cause.get("total_delta") or approval.get("amount")
    causes = root_cause.get("root_causes") or []
    cause_text = "；".join(
        _ROOT_CAUSE_LABELS.get(str(item), text(item)) for item in causes
    ) if causes else "未发现需要调整的差异"
    applied_components = []
    for component in calculation.get("components") or []:
        if not isinstance(component, Mapping) or not component.get("applied"):
            continue
        amount = component.get("amount")
        if amount in (None, "", "0", "0.00"):
            continue
        component_name = _COMPONENT_LABELS.get(
            str(component.get("type")), text(component.get("type")),
        )
        applied_components.append(f"• {component_name}：{money(amount, currency)}")
    component_text = "\n".join(applied_components) if applied_components else "• 暂无分项明细"
    constraints = risk.get("execution_constraints")
    constraints = constraints if isinstance(constraints, Mapping) else {}
    role = _ROLE_LABELS.get(
        str(approval.get("approver_role")), text(approval.get("approver_role")),
    )
    risk_level = text(approval.get("risk_level") or case.get("risk_level"))
    summary = [
        f"多智能体已完成调查，请{role}决定是否调整 {money(delta or approval.get('amount'), currency)}。",
        "",
        f"案件：{text(case.get('case_id'))}",
        f"订单：{text(case.get('order_id'))}",
        f"合作伙伴：{text(case.get('partner_name'))}（{text(case.get('partner_id'))}）",
        f"业务情况：{text(case.get('description'))}",
        "",
        "金额核对",
        f"• 当前已记账：{money(actual, currency)}",
        f"• 正确应付：{money(expected, currency)}",
        f"• 本次拟调整：{money(delta or approval.get('amount'), currency)}",
        "",
        "调整构成",
        component_text,
        "",
        f"判断依据：{text(policy.get('policy_version'))} 政策；代理等级 {text(facts.get('agent_tier') or tier)}",
        f"发现问题：{cause_text}",
        f"风险与权限：{risk_level}；单次调整上限 {money(constraints.get('max_amount'), currency)}",
        "执行说明：批准后系统将受控执行并由另一智能体独立复核；复核不通过会自动冲销。",
        "",
        "角色分工",
        "• 编排与调度：revguard-orchestrator",
        f"• 人工审批：{role}（只作授权决定，不调度 Worker）",
        "• 受控执行：revguard-executor",
        "• 独立复核：revguard-verifier",
        "",
        f"请在 {expiry(expires_at)} 前决定：",
        "• 批准：回复“批准”",
        "• 驳回：回复“驳回：原因”",
        "",
        f"追溯号：{text(approval.get('approval_id'))} · {text(run.get('run_id'))}",
    ]
    return f"{APPROVAL_REQUEST_PREFIX}\n" + "\n".join(summary)


def build_approval_result_message(
    *,
    case_id: str,
    approval_id: str,
    decision: str,
    case_status: str,
    state_status: str = "COMMITTED",
    detail: str = "",
) -> str:
    """Build the room-visible result without exposing proof or capability data."""
    committed = state_status in {"QUEUED", "COMMITTED"}
    decision_label = "批准" if decision == "APPROVED" else "驳回"
    title = f"{APPROVAL_RESULT_PREFIX}{'已受理' if committed else '未生效'}"
    if not committed:
        progress = "本次决定未提交，请返回 WebUI 核对案件状态。"
    elif decision == "REJECTED":
        progress = "revguard-orchestrator 已停止后续 Worker 调度，不会执行资金调整。"
    elif state_status == "QUEUED":
        progress = (
            "revguard-orchestrator 已收到人工授权，正在调度 revguard-executor；"
            "完成后由 revguard-verifier 独立复核。WebUI 将自动更新进度。"
        )
    else:
        progress = "人工决定已记录，后续流程仍由 revguard-orchestrator 编排。"
    lines = [
        title,
        f"案件：{case_id}",
        f"人工决定：{decision_label}",
        "流程负责人：revguard-orchestrator",
        f"当前状态：{_CASE_STATUS_LABELS.get(case_status, case_status)}",
        f"后续处理：{progress}",
    ]
    if detail:
        lines.append(f"说明：{' '.join(detail.split())[:240]}")
    lines.append(f"追溯号：{approval_id}")
    return "\n".join(lines)


def _parse_decision(body: str) -> tuple[str, str] | None:
    lines = body.strip().splitlines()
    # Element includes a plain-text fallback quote in ``body`` for replies;
    # the structured m.in_reply_to relation remains the binding anchor.
    while lines and lines[0].lstrip().startswith(">"):
        lines.pop(0)
    while lines and not lines[0].strip():
        lines.pop(0)
    match = _DECISION_RE.match("\n".join(lines).strip())
    if not match:
        return None
    command = match.group(1).lower()
    comment = (match.group(2) or "").strip()
    if command in {"批准", "同意", "approve", "approved"}:
        return "APPROVED", comment
    if not comment:
        # A rejection without a reason is ambiguous in a shared room and is
        # not useful evidence for the audit record.
        return None
    return "REJECTED", comment


def parse_approval_reply(
    event: Mapping[str, object],
    *,
    room_id: str,
    pending_requests: Mapping[str, PendingApprovalRequest],
    allowed_senders: Mapping[str, object] | set[str] | frozenset[str],
    allow_unthreaded_fallback: bool = False,
    now: int | None = None,
) -> MatrixApprovalReply | None:
    """Parse a bound reply to a pending request.

    A Matrix reply relation is preferred.  For the recording/demo room we can
    also accept a plain decision when exactly one approval is pending.  The
    fallback is still bound by the allow-list, room and expiry checks; it never
    guesses between multiple live approvals.
    """
    if event.get("type") != "m.room.message":
        return None
    if event.get("room_id") not in (None, room_id):
        return None
    sender = str(event.get("sender") or "")
    if not sender or sender not in allowed_senders:
        return None
    content = event.get("content")
    if not isinstance(content, Mapping) or content.get("msgtype") != "m.text":
        return None
    binding_mode = "reply"
    relates_to = content.get("m.relates_to")
    request_event_id = ""
    request = None
    if isinstance(relates_to, Mapping):
        in_reply_to = relates_to.get("m.in_reply_to")
        if isinstance(in_reply_to, Mapping):
            request_event_id = str(in_reply_to.get("event_id") or "")
            request = pending_requests.get(request_event_id)
    if request is None and allow_unthreaded_fallback:
        if len(pending_requests) != 1:
            return None
        request_event_id, request = next(iter(pending_requests.items()))
        binding_mode = "single_pending_direct"
    if request is None:
        return None
    current = int(time.time() if now is None else now)
    if request.expires_at < current:
        return None
    event_id = str(event.get("event_id") or "")
    if not event_id or event_id == request_event_id:
        return None
    body = str(content.get("body") or "")
    parsed = _parse_decision(body)
    if parsed is None:
        return None
    decision, comment = parsed
    raw_ts = event.get("origin_server_ts")
    origin_server_ts = raw_ts if isinstance(raw_ts, int) else None
    return MatrixApprovalReply(
        event_id=event_id,
        sender=sender,
        request_event_id=request_event_id,
        case_id=request.case_id,
        approval_id=request.approval_id,
        decision=decision,
        comment=comment,
        origin_server_ts=origin_server_ts,
        binding_mode=binding_mode,
    )


PendingRequestProvider = Callable[[], Mapping[str, PendingApprovalRequest]]
ReplyHandler = Callable[[MatrixApprovalReply], Awaitable[str | None]]


class MatrixApprovalBridge:
    """Long-poll the authoritative room and hand valid replies to the API."""

    def __init__(
        self,
        settings: MatrixSettings,
        *,
        pending_requests: PendingRequestProvider,
        on_reply: ReplyHandler,
        allowed_senders: Mapping[str, object] | set[str] | frozenset[str],
        client: MatrixClient | None = None,
    ):
        self.settings = settings
        self.pending_requests = pending_requests
        self.on_reply = on_reply
        self.allowed_senders = allowed_senders
        self.client = client or MatrixClient(settings)
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Run until shutdown; transient Matrix failures are retried safely."""
        self.settings.validate()
        while not self._stop.is_set():
            try:
                await self.client.authenticate()
                bot_subject = str((await self.client.whoami()).get("user_id") or "")
                cursor = await self.client.cursor()
                break
            except asyncio.CancelledError:
                raise
            except (MatrixTransportError, OSError, ValueError):
                LOGGER.exception("matrix_approval_bridge_auth_failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        else:
            return
        LOGGER.info("matrix_approval_bridge_started", extra={
            "revguard_fields": {"room_id_configured": bool(self.settings.room_id)},
        })
        while not self._stop.is_set():
            try:
                response = await self.client.sync(
                    since=cursor,
                    timeout_ms=5000,
                )
                next_cursor = str(response.get("next_batch") or cursor)
                rooms = response.get("rooms")
                joined = rooms.get("join", {}) if isinstance(rooms, Mapping) else {}
                room = joined.get(self.settings.room_id, {})
                timeline = room.get("timeline", {}) if isinstance(room, Mapping) else {}
                events = timeline.get("events", []) if isinstance(timeline, Mapping) else []
                if isinstance(events, list):
                    await self._handle_events(events, bot_subject=bot_subject)
                cursor = next_cursor
            except asyncio.CancelledError:
                raise
            except (MatrixTransportError, OSError, ValueError):
                LOGGER.exception("matrix_approval_bridge_sync_failed")
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass

    async def _handle_events(self, events: list[object], *, bot_subject: str) -> None:
        pending = self.pending_requests()
        for raw_event in events:
            if not isinstance(raw_event, Mapping):
                continue
            # The recording deployment may deliberately use one Matrix account
            # for AgentTeams transport and the human approver's Element
            # session. Do not discard that account before protocol parsing:
            # the reply relation, pending-request map, allow-list, expiry and
            # decision grammar are the actual gates. Requests/results emitted
            # by the bot are not replies and are therefore ignored below.
            reply = parse_approval_reply(
                raw_event,
                room_id=self.settings.room_id,
                pending_requests=pending,
                allowed_senders=self.allowed_senders,
                allow_unthreaded_fallback=self.settings.approval_allow_unthreaded_reply,
            )
            if reply is None:
                continue
            try:
                result = await self.on_reply(reply)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Do not turn a malformed or racing room message into a
                # process-wide failure.  The API callback records the decision
                # outcome; this log is deliberately secret-free.
                LOGGER.exception("matrix_approval_reply_failed", extra={
                    "revguard_fields": {
                        "case_id": reply.case_id,
                        "approval_id": reply.approval_id,
                        "event_id": reply.event_id,
                    },
                })
                continue
            if result:
                try:
                    await self.client.send_text(
                        result,
                        room_id=self.settings.room_id,
                        txn_id=(
                            "approval-result-"
                            + hashlib.sha256(reply.event_id.encode()).hexdigest()[:32]
                        ),
                    )
                except (MatrixTransportError, OSError, ValueError):
                    LOGGER.exception("matrix_approval_result_publish_failed", extra={
                        "revguard_fields": {
                            "case_id": reply.case_id,
                            "approval_id": reply.approval_id,
                        },
                    })
