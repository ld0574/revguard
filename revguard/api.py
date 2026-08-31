"""RevGuard FastAPI 服务（可选层，供前端与 AgentTeams Worker 调用）。

设计约定：
- 编排骨架与 run_demo.py 完全一致，API 只是触发入口；
- 审批模式为 wait 时，案件在 WAITING_FOR_APPROVAL 挂起，
  由 POST /cases/{id}/approval 人工审批后自动续跑执行与验证；
- /skills/{name}/invoke 是 Agent 可见入口；/tools/call 仅保留内部/遗留兼容；
- /agent-tasks 把外部 Worker 调用绑定到 Case 状态、版本、Skill 与 actor；
- 所有端点只返回结构化数据，错误带明确类型。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from .agent_bridge import create_agent_task, execute_agent_task
from .demo_dashboard import build_dashboard_snapshot
from .hitl import (
    HumanActionProof,
    MatrixHumanIdentityProvider,
    issue_human_action_assertion,
    load_human_approvers,
    verify_human_action_assertion,
)
from .matrix_team import MatrixSettings, MatrixTeamRunner
from .mcp_server import hydrate_server_secrets
from .mcp_team import McpTeamRunner
from .mocks import ToolGateway
from .models import Case, CaseStatus, new_id, utc_now
from .observability import configure_structured_logging, prometheus_text
from .orchestrator import Orchestrator
from .security import (
    TOOL_REQUIRED_SCOPES,
    ApiPrincipal,
    CapabilityTokenSigner,
    SecurityError,
    constant_time_lookup,
    load_api_principals,
    redact_secrets,
    secret_fingerprint,
)
from .skill_runtime import SKILL_ACTORS, SkillInvocationError, invoke_skill
from .skills import list_skills
from .state_machine import transition_case
from .store import create_store
from .trace import Tracer

try:
    from fastapi import (
        Depends,
        FastAPI,
        Header,
        HTTPException,
        Query,
        Request,
        Response,
    )
    from fastapi.responses import PlainTextResponse
    from pydantic import BaseModel, ConfigDict, Field, SecretStr
except ImportError as exc:  # 本地纯标准库跑 Demo 时允许不安装 FastAPI
    raise ImportError("API 服务需要安装依赖：pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parent.parent
LOGGER = logging.getLogger("revguard.api")
configure_structured_logging(LOGGER)
DB_PATH = os.getenv("REVGUARD_DB_PATH", str(ROOT / "data" / "revguard.db"))
DATABASE_URL = os.getenv("REVGUARD_DATABASE_URL")
READ_DATABASE_URL = os.getenv("REVGUARD_READ_DATABASE_URL")
RELEASE_VERSION = os.getenv("REVGUARD_RELEASE_VERSION", "0.4.0")
FIXTURES = os.getenv("REVGUARD_FIXTURES_DIR", str(ROOT / "data" / "fixtures"))
OUTPUT_DIR = os.getenv("REVGUARD_OUTPUT_DIR", str(ROOT / "data" / "outputs"))
REPORT_DIR = os.getenv("REVGUARD_REPORT_DIR", str(ROOT / "docs" / "reports"))
APPROVAL_MODE = os.getenv("REVGUARD_APPROVAL_MODE", "wait")  # 服务端默认等待人工审批
FINANCE_FAIL_TIMES = int(os.getenv("REVGUARD_FINANCE_FAIL_TIMES", "1"))
VERIFICATION_TAMPER_AMOUNT = os.getenv("REVGUARD_VERIFICATION_TAMPER_AMOUNT", "0")
GATEWAY_STATE_PATH = os.getenv(
    "REVGUARD_GATEWAY_STATE_PATH", str(Path(DB_PATH).with_suffix(".gateway.json"))
)
SIGNING_KEY = os.getenv("REVGUARD_APPROVAL_SIGNING_KEY", "")
ALLOW_INSECURE_DEMO_KEYS = os.getenv("REVGUARD_ALLOW_INSECURE_DEMO_KEYS", "false").lower() == "true"
ENABLE_LEGACY_TOOL_API = os.getenv(
    "REVGUARD_ENABLE_LEGACY_TOOL_API", "false"
).lower() == "true"
ENABLE_RECORDING_UI = os.getenv(
    "REVGUARD_ENABLE_RECORDING_UI", "false"
).lower() == "true"
TEAM_TRANSPORT = os.getenv("REVGUARD_TEAM_TRANSPORT", "mcp").lower()
TEAM_RUN_STALE_AFTER_SECONDS = float(os.getenv(
    "REVGUARD_TEAM_RUN_STALE_AFTER_SECONDS", "600"
))
HITL_ASSERTION_TTL_SECONDS = int(os.getenv(
    "REVGUARD_HITL_ASSERTION_TTL_SECONDS", "120"
))
HITL_MAX_AUTH_AGE_SECONDS = int(os.getenv(
    "REVGUARD_HITL_MAX_AUTH_AGE_SECONDS", "300"
))
HITL_MATRIX_HOMESERVER_URL = os.getenv(
    "REVGUARD_HITL_MATRIX_HOMESERVER_URL",
    os.getenv("REVGUARD_MATRIX_HOMESERVER_URL", ""),
)
HITL_MATRIX_SERVER_NAME = os.getenv(
    "REVGUARD_MATRIX_SERVER_NAME", "matrix"
)
DEFAULT_DEMO_CASE_ID = os.getenv(
    "REVGUARD_DEFAULT_DEMO_CASE_ID", "CASE-2026-0008"
)
LEGACY_EVIDENCE_ACTOR = "revguard-evidence"
DEMO_PRINCIPALS_PATH = Path(os.getenv(
    "REVGUARD_DEMO_PRINCIPALS_PATH", str(ROOT / "config" / "demo_principals.json")
))

raw_api_keys = os.getenv("REVGUARD_API_KEYS_JSON", "")
if not raw_api_keys and ALLOW_INSECURE_DEMO_KEYS:
    try:
        raw_api_keys = DEMO_PRINCIPALS_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"无法读取显式启用的 Demo Principal 配置: {DEMO_PRINCIPALS_PATH}") from exc
try:
    API_PRINCIPALS = load_api_principals(raw_api_keys) if raw_api_keys else {}
except SecurityError as exc:
    raise RuntimeError(f"API 鉴权配置无效: {exc}") from exc
if not SIGNING_KEY:
    if ALLOW_INSECURE_DEMO_KEYS:
        SIGNING_KEY = "revguard-demo-signing-key-change-before-production-2026"
    else:
        raise RuntimeError("必须配置 REVGUARD_APPROVAL_SIGNING_KEY（至少 32 字节）")
try:
    HITL_APPROVERS = load_human_approvers(os.getenv(
        "REVGUARD_HITL_MATRIX_USERS_JSON", ""
    ))
except SecurityError as exc:
    raise RuntimeError(f"HITL 人类审批身份配置无效: {exc}") from exc
HITL_SIGNER = CapabilityTokenSigner(SIGNING_KEY, issuer="revguard-hitl")
HITL_IDENTITY_PROVIDER = MatrixHumanIdentityProvider(
    HITL_MATRIX_HOMESERVER_URL,
    HITL_APPROVERS,
    server_name=HITL_MATRIX_SERVER_NAME,
)
if ENABLE_LEGACY_TOOL_API:
    LOGGER.warning(
        "REVGUARD_ENABLE_LEGACY_TOOL_API=true：仅允许历史 Evidence Principal 复放只读工具"
    )

app = FastAPI(title="RevGuard API", version=RELEASE_VERSION,
              description="面向企业渠道佣金结算异常的多智能体治理平台")

# Demo 单进程即可：共享一份 Store / Mock 系统状态
store = create_store(
    DB_PATH, database_url=DATABASE_URL, read_database_url=READ_DATABASE_URL
)


def _new_gateway() -> ToolGateway:
    return ToolGateway(
        FIXTURES,
        finance_fail_times=FINANCE_FAIL_TIMES,
        signing_key=SIGNING_KEY,
        state_path=GATEWAY_STATE_PATH,
        verification_tamper_amount=VERIFICATION_TAMPER_AMOUNT,
    )


gateway = _new_gateway()


@app.middleware("http")
async def structured_access_log(request: Request, call_next):
    """Log correlation and latency only; never log bodies or credentials."""
    started = time.monotonic()
    request_id = request.headers.get("X-Request-ID") or new_id("REQ")
    try:
        response = await call_next(request)
    except Exception:
        LOGGER.exception("http_request_failed", extra={"revguard_fields": {
            "request_id": request_id, "method": request.method,
            "path": request.url.path,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }})
        raise
    response.headers.setdefault("X-Request-ID", request_id)
    # State/identity responses and the UI entry point must not survive a run or
    # deployment in browser caches. Hashed static assets remain cacheable.
    if request.url.path.startswith("/api/") or (
        request.url.path.startswith("/demo")
        and "text/html" in response.headers.get("content-type", "")
    ):
        response.headers["Cache-Control"] = "no-store"
    LOGGER.info("http_request", extra={"revguard_fields": {
        "request_id": request_id, "method": request.method,
        "path": request.url.path, "status_code": response.status_code,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }})
    return response


def authenticate(authorization: str | None = Header(default=None)) -> ApiPrincipal:
    if not API_PRINCIPALS:
        raise HTTPException(503, "API 身份配置为空，拒绝受保护请求")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "缺少 Bearer API key",
                            headers={"WWW-Authenticate": "Bearer"})
    principal = constant_time_lookup(token, API_PRINCIPALS)
    if principal is None:
        raise HTTPException(401, "API key 无效", headers={"WWW-Authenticate": "Bearer"})
    return principal


def require_roles(*roles: str):
    required = set(roles)

    def dependency(principal: ApiPrincipal = Depends(authenticate)) -> ApiPrincipal:
        if not required.intersection(principal.roles):
            raise HTTPException(403, f"需要角色之一: {sorted(required)}")
        return principal

    return dependency


def require_human_action(
    authorization: str | None,
    *,
    case_id: str,
    approval_id: str,
    action: str,
) -> HumanActionProof:
    """Validate the short-lived proof created by an out-of-band human login."""
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            401,
            "需要先验证 AgentTeams 人类审批身份",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        return verify_human_action_assertion(
            HITL_SIGNER,
            token,
            HITL_APPROVERS,
            case_id=case_id,
            approval_id=approval_id,
            action=action,
            max_auth_age_seconds=HITL_MAX_AUTH_AGE_SECONDS,
        )
    except SecurityError as exc:
        raise HTTPException(
            401,
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def _orchestrator() -> Orchestrator:
    return Orchestrator(store, gateway, output_dir=OUTPUT_DIR,
                        report_dir=REPORT_DIR, approval_mode=APPROVAL_MODE)


def _mcp_team() -> McpTeamRunner:
    return McpTeamRunner(
        store, gateway, output_dir=OUTPUT_DIR, report_dir=REPORT_DIR,
    )


def _matrix_team() -> MatrixTeamRunner:
    return MatrixTeamRunner(
        store, gateway, output_dir=OUTPUT_DIR, report_dir=REPORT_DIR,
        settings=MatrixSettings.from_env(),
    )


def _team_runner_for_case(case: dict | None = None):
    mode = (case or {}).get("execution_mode")
    if mode == "AGENTTEAMS_MATRIX" or (not mode and TEAM_TRANSPORT == "matrix"):
        return _matrix_team()
    return _mcp_team()


BACKGROUND_TEAM_TASKS: dict[str, asyncio.Task] = {}
ACTIVE_TEAM_RUN_STATUSES = frozenset({"QUEUED", "STARTING", "RUNNING"})


def _team_run_age_seconds(case: dict) -> float | None:
    run = case.get("team_run") or {}
    raw = run.get("updated_at") or run.get("started_at") or run.get("queued_at")
    if not raw:
        return None
    try:
        updated = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return max((datetime.now(UTC) - updated).total_seconds(), 0)


def _has_live_team_task(case_id: str) -> bool:
    task = BACKGROUND_TEAM_TASKS.get(case_id)
    return bool(task and not task.done())


async def _run_team_background(case_id: str, phase: str) -> None:
    case = store.get_case(case_id)
    if not case:
        return
    try:
        runner = _team_runner_for_case(case)
        if phase == "INVESTIGATION":
            await runner.run_to_human_gate(case)
        elif phase == "ROLLBACK":
            await runner.resume_rollback(case)
        else:
            await runner.execute_after_approval(case)
    except Exception as exc:
        latest = store.get_case(case_id) or case
        if latest.get("status") not in {
            CaseStatus.CLOSED.value, CaseStatus.ROLLED_BACK.value,
            CaseStatus.FAILED.value,
        }:
            transition_case(
                store, latest, CaseStatus.FAILED,
                f"AgentTeams background failure: {type(exc).__name__}",
            )
        store.audit(case_id, "revguard-orchestrator", "TEAM_RUN_FAILED", {
            "phase": phase,
            "transport": "agentteams-matrix",
            "error_type": type(exc).__name__,
        })
        LOGGER.exception("team_run_failed", extra={"revguard_fields": {
            "case_id": case_id, "phase": phase,
            "error_type": type(exc).__name__,
        }})


def _spawn_team_background(case_id: str, phase: str) -> None:
    if _has_live_team_task(case_id):
        raise RuntimeError(f"案件 {case_id} 已有本机 AgentTeams 运行")
    task = asyncio.create_task(_run_team_background(case_id, phase))
    BACKGROUND_TEAM_TASKS[case_id] = task

    def discard(completed: asyncio.Task) -> None:
        if BACKGROUND_TEAM_TASKS.get(case_id) is completed:
            BACKGROUND_TEAM_TASKS.pop(case_id, None)

    task.add_done_callback(discard)


# --------------------------------------------------------------------- 模型
class CaseCreate(BaseModel):
    source: str = "MANUAL"
    case_type: str = "COMMISSION_UNDERPAYMENT"
    partner_id: str | None = None
    partner_name: str | None = None
    order_id: str | None = None
    description: str = ""
    claim: dict = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVED", "REJECTED"]
    comment: str = ""


class HumanActionAssertionRequest(BaseModel):
    """Transient AgentTeams login used only to bind a human action proof."""

    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    password: SecretStr
    action: Literal["APPROVED", "REJECTED", "RESUME"]


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    parameters: dict = Field(default_factory=dict)
    case_id: str = ""
    idempotency_key: str | None = None


class SkillInvoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: dict = Field(default_factory=dict)
    case_id: str = ""


class EvidenceResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    partner_id: str | None = None
    partner_name: str | None = None
    order_id: str | None = None
    contract_id: str | None = None
    claim: dict | None = None


class AgentTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    skill_name: str
    input: dict = Field(default_factory=dict)


class AgentTaskReassign(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


# ---------------------------------------------------------- Human approval IDP
@app.post("/api/v1/cases/{case_id}/human-action/assertion")
async def create_human_action_assertion(
    case_id: str,
    payload: HumanActionAssertionRequest,
):
    """Verify a Matrix user and mint a two-minute, action-bound proof.

    The submitted password is forwarded only to the configured Matrix
    homeserver for authentication.  RevGuard neither stores it nor returns the
    Matrix access token.
    """
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    approval = store.get_approval(case_id) or {}
    approval_id = str(approval.get("approval_id") or "")
    if not approval_id:
        raise HTTPException(409, "案件尚未生成审批单")
    if payload.action in {"APPROVED", "REJECTED"}:
        if case.get("status") != CaseStatus.WAITING_FOR_APPROVAL.value:
            raise HTTPException(409, f"案件状态 {case.get('status')} 不在等待审批节点")
    elif case.get("execution_mode") != "AGENTTEAMS_MATRIX":
        raise HTTPException(409, "只能为 AgentTeams Matrix 运行签发恢复证明")

    with Tracer(store, case_id).span(
        "APPROVAL",
        "HumanIdentityVerification",
        actor="human-identity-provider",
        inputs={
            "provider": "agentteams-matrix",
            "action": payload.action,
            "approval_id": approval_id,
        },
    ) as span:
        try:
            identity = await HITL_IDENTITY_PROVIDER.authenticate(
                payload.username,
                payload.password.get_secret_value(),
            )
        except SecurityError as exc:
            raise HTTPException(
                401, str(exc), headers={"WWW-Authenticate": "Matrix"},
            ) from exc
        assertion = issue_human_action_assertion(
            HITL_SIGNER, identity, case_id=case_id, approval_id=approval_id,
            action=payload.action, ttl_seconds=HITL_ASSERTION_TTL_SECONDS,
        )
        span["outputs"] = {
            **identity.public(),
            "assertion_ref": secret_fingerprint(assertion),
            "expires_in_seconds": HITL_ASSERTION_TTL_SECONDS,
        }
    store.audit(case_id, identity.actor, "HUMAN_IDENTITY_VERIFIED", {
        **identity.public(),
        "provider": "agentteams-matrix",
        "action": payload.action,
        "approval_id": approval_id,
        "assertion_ref": secret_fingerprint(assertion),
    })
    return {
        "assertion_token": assertion,
        "expires_in_seconds": HITL_ASSERTION_TTL_SECONDS,
        "identity": identity.public(),
        "binding": {
            "case_id": case_id,
            "approval_id": approval_id,
            "action": payload.action,
        },
    }


# --------------------------------------------------------------------- 案件
@app.post("/api/v1/cases", status_code=201)
def create_case(payload: CaseCreate,
                principal: ApiPrincipal = Depends(require_roles("operator"))):
    from .models import new_id
    case = Case(
        case_id=new_id("CASE"),
        case_type=payload.case_type, source=payload.source,
        partner_id=payload.partner_id, partner_name=payload.partner_name,
        order_id=payload.order_id, description=payload.description,
        claim=payload.claim,
        entities={"partner_id": payload.partner_id,
                  "partner_name": payload.partner_name,
                  "order_id": payload.order_id, "contract_id": None},
    ).to_dict()
    store.save_case(case)
    store.audit(case["case_id"], principal.actor, "CASE_CREATED", {"source": payload.source})
    return case


@app.get("/api/v1/cases")
def list_cases(limit: int = Query(default=50, ge=1, le=200),
               cursor: str | None = Query(default=None),
               _principal: ApiPrincipal = Depends(require_roles("viewer"))):
    try:
        page = store.list_cases_page(limit=limit, cursor=cursor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**page, "limit": limit}


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str, _principal: ApiPrincipal = Depends(require_roles("viewer"))):
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    case["evidence"] = store.list_evidence(case_id)
    case["audit_events"] = store.list_audit(case_id)
    return case


@app.get("/api/v1/cases/{case_id}/dashboard")
def get_case_dashboard(
    case_id: str,
    _principal: ApiPrincipal = Depends(require_roles("viewer")),
):
    """Sanitized projection for the recording cockpit and finance review UI."""
    snapshot = build_dashboard_snapshot(store, case_id, report_dir=REPORT_DIR)
    if snapshot is None:
        raise HTTPException(404, f"案件不存在: {case_id}")
    return snapshot


@app.post("/api/v1/demo/reset")
def reset_recording_demo(
    principal: ApiPrincipal = Depends(require_roles("operator")),
):
    """Reset deterministic fixtures for another local recording take.

    This endpoint is absent by default and can only be enabled explicitly with
    ``REVGUARD_ENABLE_RECORDING_UI=true``.
    """
    if not ENABLE_RECORDING_UI:
        raise HTTPException(404, "录制模式未启用")
    from scripts.seed_demo import seed_store

    global gateway
    state_path = Path(GATEWAY_STATE_PATH)
    if state_path.exists() and state_path.is_file():
        state_path.unlink()
    seeded_cases = seed_store(store, reset=True, quiet=True)
    gateway = _new_gateway()
    for seeded_case in seeded_cases:
        store.audit(seeded_case["case_id"], principal.actor, "DEMO_RESET", {
            "synthetic_business_data": True,
            "verification_tamper_amount": VERIFICATION_TAMPER_AMOUNT,
        })
    case_ids = [item["case_id"] for item in seeded_cases]
    default_case_id = (
        DEFAULT_DEMO_CASE_ID
        if DEFAULT_DEMO_CASE_ID in case_ids else case_ids[0]
    )
    snapshot = build_dashboard_snapshot(
        store, default_case_id, report_dir=REPORT_DIR
    )
    return {
        "case_id": default_case_id,
        "case_ids": case_ids,
        "snapshot": snapshot,
    }


@app.post("/api/v1/cases/{case_id}/run")
def run_case(case_id: str, response: Response,
             principal: ApiPrincipal = Depends(require_roles("operator")),
             request_id: str | None = Header(default=None, alias="X-Request-ID")):
    correlation_id = request_id or new_id("REQ")
    if len(correlation_id) > 256:
        raise HTTPException(400, "X-Request-ID 长度不能超过 256")
    response.headers["X-Request-ID"] = correlation_id
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] not in (CaseStatus.CREATED.value,):
        raise HTTPException(409, f"案件状态 {case['status']} 不允许从头运行")
    try:
        state = _orchestrator().run_case(case)
    except Exception as exc:
        store.audit(case_id, principal.actor, "CASE_RUN_FAILED", redact_secrets({
            "request_id": correlation_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }))
        raise HTTPException(500, {
            "code": "CASE_RUN_FAILED",
            "request_id": correlation_id,
        }, headers={"X-Request-ID": correlation_id}) from exc
    return {"case": store.get_case(case_id), "state_status": state.get("final_status")}


@app.post("/api/v1/cases/{case_id}/team/run")
async def run_case_via_team(
    case_id: str,
    response: Response,
    principal: ApiPrincipal = Depends(require_roles("operator")),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
):
    """Run state-driven Workers through MCP or real AgentTeams Matrix."""
    correlation_id = request_id or new_id("REQ-TEAM")
    if len(correlation_id) > 256:
        raise HTTPException(400, "X-Request-ID 长度不能超过 256")
    response.headers["X-Request-ID"] = correlation_id
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] != CaseStatus.CREATED.value:
        raise HTTPException(409, f"案件状态 {case['status']} 不允许启动 Agent Team")
    transport = "agentteams-matrix" if TEAM_TRANSPORT == "matrix" else "mcp"
    store.audit(case_id, principal.actor, "TEAM_RUN_STARTED", {
        "request_id": correlation_id,
        "business_data": "synthetic",
        "workflow": "real_executable",
        "transport": transport,
    })
    if TEAM_TRANSPORT == "matrix":
        case["execution_mode"] = "AGENTTEAMS_MATRIX"
        case["workflow_provenance"] = {
            "business_data": "synthetic",
            "workflow": "real_executable",
            "transport": transport,
            "orchestration": "state-driven",
            "agentteams_room_evidence": "CAPTURED_FROM_RUNTIME",
        }
        case["team_run"] = {
            "status": "QUEUED", "phase": "INVESTIGATION",
            "current_stage": None, "completed_tasks": 0, "total_tasks": 8,
            "queued_at": utc_now(),
        }
        store.save_case(case)
        _spawn_team_background(case_id, "INVESTIGATION")
        response.status_code = 202
        return {
            "case": store.get_case(case_id),
            "state_status": "QUEUED",
            "agent_tasks": [],
        }
    try:
        state = await _mcp_team().run_to_human_gate(case)
    except Exception as exc:
        raise HTTPException(500, {
            "code": "TEAM_RUN_FAILED",
            "request_id": correlation_id,
            "error_type": type(exc).__name__,
        }) from exc
    return {
        "case": store.get_case(case_id),
        "state_status": state.get("final_status"),
        "agent_tasks": [redact_secrets(item)
                        for item in store.list_agent_tasks(case_id)],
    }


@app.post("/api/v1/cases/{case_id}/team/resume")
async def resume_interrupted_team_run(
    case_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
):
    """Resume a stale post-approval Matrix run through idempotent replay.

    Durable StageResults and execution idempotency keys remain authoritative.
    L2 recovery requires a trusted approver to renew only the unconsumed part
    of the expired short-lived capability.
    """
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    run = case.get("team_run") or {}
    if case.get("execution_mode") != "AGENTTEAMS_MATRIX":
        raise HTTPException(409, "只能恢复 AgentTeams Matrix 运行")
    recovering_execution = (
        case.get("status") == CaseStatus.EXECUTING.value
        and run.get("status") in ACTIVE_TEAM_RUN_STATUSES
    )
    verification = store.get_verification(case_id) or {}
    recovering_rollback = (
        case.get("status") == CaseStatus.FAILED.value
        and run.get("status") == "FAILED"
        and run.get("current_stage") in {
            "LedgerReverseSkill", "PostRollbackVerifySkill",
        }
        and verification.get("rollback_required") is True
    )
    if not recovering_execution and not recovering_rollback:
        raise HTTPException(409, {
            "code": "TEAM_RUN_NOT_RECOVERABLE",
            "message": (
                f"案件状态 {case.get('status')} / 运行状态 "
                f"{run.get('status')} 不支持安全续跑"
            ),
        })
    age_seconds = _team_run_age_seconds(case)
    if recovering_execution and (
        age_seconds is None or age_seconds < TEAM_RUN_STALE_AFTER_SECONDS
    ):
        raise HTTPException(409, {
            "code": "TEAM_RUN_NOT_STALE",
            "message": "运行仍在活跃时限内，拒绝重复调度",
            "age_seconds": age_seconds,
        })
    if _has_live_team_task(case_id):
        raise HTTPException(409, {
            "code": "TEAM_RUN_STILL_LOCAL",
            "message": "本机后台任务仍存活，拒绝重复调度",
        })

    approval = store.get_approval(case_id) or {}
    proof = require_human_action(
        authorization,
        case_id=case_id,
        approval_id=str(approval.get("approval_id") or ""),
        action="RESUME",
    )
    human = proof.identity
    risk = case.get("risk_decision") or {}
    if recovering_execution and risk.get("approval_required"):
        renewed = gateway.call(
            "workflow.renew_approval_capability", {
                "approval_id": approval.get("approval_id"),
                "case_id": case_id,
            },
            case_id=case_id,
            actor=human.actor,
            scope=["approval:decide"],
        )
        if not renewed["success"]:
            raise HTTPException(409, {
                "code": "APPROVAL_CAPABILITY_RENEWAL_FAILED",
                "message": renewed["error"]["message"],
            })
        approval = renewed["data"]
        store.save_approval({
            "approval_id": approval["approval_id"],
            "case_id": case_id,
            **approval,
        })
        store.audit(case_id, human.actor, "APPROVAL_CAPABILITY_RENEWED", {
            "approval_id": approval["approval_id"],
            "human_subject": human.sub,
            "remaining_component_quota": approval.get(
                "remaining_component_quota", {}
            ),
            "previous_run_id": run.get("run_id"),
        })

    phase = "EXECUTION"
    strategy = "idempotent-execution-replay"
    if recovering_rollback:
        phase = "ROLLBACK"
        strategy = "resume-unreversed-executions"
        renewed_ledgers = []
        for execution in store.list_executions(case_id):
            ledger = execution.get("ledger_entry") or {}
            if execution.get("status") != "SUBMITTED" or not ledger.get("ledger_id"):
                continue
            renewed = gateway.call(
                "workflow.renew_rollback_capability", {
                    "case_id": case_id,
                    "ledger_id": ledger["ledger_id"],
                    "action_id": execution.get("action_id"),
                },
                case_id=case_id,
                actor=human.actor,
                scope=["approval:decide"],
            )
            if not renewed["success"]:
                raise HTTPException(409, {
                    "code": "ROLLBACK_CAPABILITY_RENEWAL_FAILED",
                    "message": renewed["error"]["message"],
                })
            execution["rollback_token"] = renewed["data"]["rollback_token"]
            store.save_execution(execution)
            renewed_ledgers.append(ledger["ledger_id"])
        store.audit(case_id, human.actor, "ROLLBACK_CAPABILITY_RENEWED", {
            "ledger_ids": renewed_ledgers,
            "human_subject": human.sub,
            "previous_run_id": run.get("run_id"),
        })
        transition_case(
            store, case, CaseStatus.ROLLBACK_REQUIRED,
            "审批人确认恢复未完成的安全回滚",
            actor=human.actor,
        )

    recovered_at = utc_now()
    case["team_run"] = {
        **run,
        "status": "QUEUED",
        "phase": phase,
        "queued_at": recovered_at,
        "updated_at": recovered_at,
        "error": None,
        "recovery": {
            "reason": "api-process-interrupted",
            "strategy": strategy,
            "requested_by": human.actor,
            "requested_subject": human.sub,
            "requested_at": recovered_at,
            "stale_for_seconds": int(age_seconds or 0),
        },
    }
    store.save_case(case)
    store.audit(case_id, human.actor, "TEAM_RUN_RESUME_REQUESTED", {
        "run_id": run.get("run_id"),
        "phase": phase,
        "human_subject": human.sub,
        "assertion_id_ref": secret_fingerprint(proof.assertion_id),
        "stale_for_seconds": int(age_seconds or 0),
    })
    _spawn_team_background(case_id, phase)
    response.status_code = 202
    return {
        "case": redact_secrets(store.get_case(case_id) or case),
        "state_status": "QUEUED",
        "recovery": case["team_run"]["recovery"],
    }


@app.post("/api/v1/cases/{case_id}/approval")
async def decide_approval(case_id: str, payload: ApprovalDecision,
                          response: Response,
                          authorization: str | None = Header(default=None)):
    """人工审批节点：审批通过后自动续跑执行与独立验证。"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] != CaseStatus.WAITING_FOR_APPROVAL.value:
        raise HTTPException(409, f"案件状态 {case['status']} 不在等待审批节点")
    approval = store.get_approval(case_id)
    if not approval:
        raise HTTPException(409, "未找到审批单")

    proof = require_human_action(
        authorization,
        case_id=case_id,
        approval_id=str(approval["approval_id"]),
        action=payload.decision,
    )
    human = proof.identity

    with Tracer(store, case_id).span(
        "APPROVAL",
        "HumanApprovalGate",
        actor=human.actor,
        inputs={
            "approval_id": approval["approval_id"],
            "decision": payload.decision,
            "case_id": case_id,
            "human_subject": human.sub,
            "auth_method": human.auth_method,
        },
    ) as span:
        resp = gateway.call("workflow.decide_approval", {
            "approval_id": approval["approval_id"],
            "decision": payload.decision,
            "comment": payload.comment,
            "human_subject": human.sub,
            "human_display_name": human.display_name,
            "human_auth_time": human.auth_time,
            "human_auth_method": human.auth_method,
        }, case_id=case_id, actor=human.actor, scope=["approval:decide"])
        span["outputs"] = {
            "status": (resp.get("data") or {}).get("status"),
            "human_subject": human.sub,
            "assertion_id_ref": secret_fingerprint(proof.assertion_id),
        }
    if not resp["success"]:
        raise HTTPException(400, resp["error"])
    decided = resp["data"]
    decided["human_assertion_id_ref"] = secret_fingerprint(proof.assertion_id)
    store.save_approval({"approval_id": decided["approval_id"], "case_id": case_id, **decided})
    store.audit(case_id, human.actor, "APPROVAL_DECIDED", {
        "decision": decided["status"],
        "identity_verified": True,
        "human_subject": human.sub,
        "human_display_name": human.display_name,
        "human_auth_time": human.auth_time,
        "human_auth_method": human.auth_method,
        "assertion_id_ref": secret_fingerprint(proof.assertion_id),
    })

    public_approval = {key: value for key, value in decided.items()
                       if key != "approval_token"}
    public_approval["approval_token_ref"] = secret_fingerprint(
        decided.get("approval_token", "")
    )

    if decided["status"] != "APPROVED":
        store.cancel_open_agent_tasks(
            case_id, actor=human.actor, reason="人工审批驳回，禁止继续执行"
        )
        transition_case(store, case, CaseStatus.REJECTED, "人工审批驳回", actor=human.actor)
        if case.get("execution_mode") in {"MCP_TEAM", "AGENTTEAMS_MATRIX"}:
            await _team_runner_for_case(case).finalize_terminal(case, approval=decided)
        else:
            orch = _orchestrator()
            state = orch._rebuild_state(case)
            state["approval"] = decided
            orch._finalize(case, state, Tracer(store, case_id), archived=True)
        return {"case": store.get_case(case_id), "approval": public_approval,
                "verification": None}

    transition_case(store, case, CaseStatus.READY_TO_EXECUTE,
                    "人工审批通过", actor=human.actor)
    if case.get("execution_mode") == "AGENTTEAMS_MATRIX":
        case["team_run"] = {
            **(case.get("team_run") or {}),
            "status": "QUEUED", "phase": "EXECUTION",
            "current_stage": None, "total_tasks": 20,
            "queued_at": utc_now(), "error": None,
        }
        store.save_case(case)
        _spawn_team_background(case_id, "EXECUTION")
        response.status_code = 202
        return {"case": store.get_case(case_id), "approval": public_approval,
                "verification": None}
    if case.get("execution_mode") == "MCP_TEAM":
        state = await _mcp_team().execute_after_approval(case)
    else:
        orch = _orchestrator()
        state = orch.execute_and_verify(case)
        orch._finalize(case, state, Tracer(store, case_id), archived=True)
    return {"case": store.get_case(case_id), "approval": public_approval,
            "verification": state.get("verification")}


@app.post("/api/v1/cases/{case_id}/evidence/resume")
def resume_with_evidence(case_id: str, payload: EvidenceResume,
                         principal: ApiPrincipal = Depends(require_roles("operator"))):
    """补充案件定位字段后，从 WAITING_FOR_EVIDENCE 重新进入确定性状态机。"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] != CaseStatus.WAITING_FOR_EVIDENCE.value:
        raise HTTPException(409, f"案件状态 {case['status']} 不在等待补证节点")
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(422, "至少提交一个补证字段")
    claim = updates.pop("claim", None)
    entities = dict(case.get("entities") or {})
    for field, value in updates.items():
        case[field] = value
        entities[field] = value
    if claim is not None:
        case.setdefault("claim", {}).update(claim)
    case["entities"] = entities
    store.save_case(case)
    store.audit(case_id, principal.actor, "EVIDENCE_SUPPLIED", {
        "fields": sorted(payload.model_fields_set),
    })
    state = _orchestrator().run_case(case)
    return {"case": store.get_case(case_id), "state_status": state.get("final_status")}


def _public_agent_task(task: dict) -> dict:
    return redact_secrets(task)


@app.post("/api/v1/cases/{case_id}/agent-tasks", status_code=201)
def dispatch_agent_task(case_id: str, payload: AgentTaskCreate,
                        response: Response,
                        principal: ApiPrincipal = Depends(
                            require_roles("operator", "dispatcher")
                        ),
                        request_id: str | None = Header(default=None, alias="X-Request-ID"),
                        agentteams_message_id: str | None = Header(
                            default=None, alias="X-AgentTeams-Message-ID"
                        )):
    """把一个状态绑定的 Skill 工作项派发给服务端指定的 Worker actor。"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    try:
        task = create_agent_task(case, payload.skill_name, payload.input)
    except ValueError as exc:
        status_code = 409 if "案件状态" in str(exc) else 422
        raise HTTPException(status_code, str(exc)) from exc
    correlation = {
        "request_id": request_id or new_id("REQ"),
        "agentteams_message_id": agentteams_message_id,
        "transport": "rest",
    }
    if any(value is not None and len(value) > 256 for value in correlation.values()):
        raise HTTPException(400, "关联请求头长度不能超过 256")
    task.update(correlation)
    store.save_agent_task(task)
    store.audit(case_id, principal.actor, "AGENT_TASK_DISPATCHED", {
        "task_id": task["task_id"], "skill": task["skill_name"],
        "assigned_actor": task["assigned_actor"],
        "case_version": task["case_version"],
        **correlation,
    })
    response.headers["X-Request-ID"] = correlation["request_id"]
    response.headers["X-RevGuard-Task-ID"] = task["task_id"]
    return _public_agent_task(task)


@app.get("/api/v1/cases/{case_id}/agent-tasks")
def list_case_agent_tasks(case_id: str,
                          principal: ApiPrincipal = Depends(authenticate)):
    if not store.get_case(case_id):
        raise HTTPException(404, f"案件不存在: {case_id}")
    tasks = store.list_agent_tasks(case_id)
    privileged = bool({"operator", "dispatcher"}.intersection(principal.roles))
    visible = tasks if privileged else [
        task for task in tasks if task["assigned_actor"] == principal.actor
    ]
    if not privileged and not visible:
        raise HTTPException(403, "无权查看该案件的 Agent tasks")
    return {"tasks": [_public_agent_task(task) for task in visible]}


@app.get("/api/v1/agent-tasks/{task_id}/results")
def list_agent_task_results(
    task_id: str,
    principal: ApiPrincipal = Depends(authenticate),
):
    task = store.get_agent_task(task_id)
    if not task:
        raise HTTPException(404, f"Agent task 不存在: {task_id}")
    privileged = bool({"operator", "dispatcher"}.intersection(principal.roles))
    if not privileged and task["assigned_actor"] != principal.actor:
        raise HTTPException(403, "无权查看该 Agent task 结果")
    return {
        "task_id": task_id,
        "results": [redact_secrets(item)
                    for item in store.list_agent_task_results(task_id)],
    }


@app.post("/api/v1/agent-tasks/{task_id}/reassign", status_code=201)
def reassign_agent_task(
    task_id: str,
    payload: AgentTaskReassign,
    response: Response,
    principal: ApiPrincipal = Depends(require_roles("operator", "dispatcher")),
):
    """Cancel a failed StageTask and create a fresh case-version-bound task."""
    old = store.get_agent_task(task_id)
    if not old:
        raise HTTPException(404, f"Agent task 不存在: {task_id}")
    case = store.get_case(old["case_id"])
    if not case:
        raise HTTPException(404, f"案件不存在: {old['case_id']}")
    try:
        replacement = create_agent_task(case, old["skill_name"], old["input"])
        _, replacement = store.replace_agent_task(
            task_id, replacement, actor=principal.actor, reason=payload.reason
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    response.headers["X-RevGuard-Task-ID"] = replacement["task_id"]
    return _public_agent_task(replacement)


@app.get("/api/v1/cases/{case_id}/trace")
def get_trace(case_id: str, _principal: ApiPrincipal = Depends(require_roles("viewer"))):
    if not store.get_case(case_id):
        raise HTTPException(404, f"案件不存在: {case_id}")
    return Tracer(store, case_id).export()


@app.get("/api/v1/cases/{case_id}/report")
def get_report(case_id: str, _principal: ApiPrincipal = Depends(require_roles("viewer"))):
    report_path = Path(REPORT_DIR) / f"{case_id}.md"
    if not report_path.exists():
        raise HTTPException(404, "审计报告尚未生成")
    return {"case_id": case_id, "markdown": report_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------- 工具与 Skill
@app.post("/api/v1/tools/call")
def call_tool(payload: ToolCall,
              response: Response,
              principal: ApiPrincipal = Depends(require_roles("worker")),
              request_id: str | None = Header(default=None, alias="X-Request-ID"),
              agentteams_message_id: str | None = Header(
                  default=None, alias="X-AgentTeams-Message-ID"
              ),
              traceparent: str | None = Header(default=None, alias="traceparent")):
    """AgentTeams Worker 工具入口；关联消息、HTTP 请求、回执、Trace 与 Audit。"""
    if not ENABLE_LEGACY_TOOL_API:
        raise HTTPException(410, "Agent 裸 Tool API 已关闭；请调用注册 Skill")
    required_scope = TOOL_REQUIRED_SCOPES.get(payload.tool_name)
    if principal.actor != LEGACY_EVIDENCE_ACTOR or not (
        required_scope and required_scope.endswith(":read")
    ):
        raise HTTPException(403, "遗留 Tool API 仅允许 Evidence Principal 复放只读工具")

    correlation = {
        "request_id": request_id or new_id("REQ"),
        "agentteams_message_id": agentteams_message_id,
        "traceparent": traceparent,
    }
    if any(value is not None and len(value) > 256 for value in correlation.values()):
        raise HTTPException(400, "关联请求头长度不能超过 256")

    case_exists = bool(payload.case_id and store.get_case(payload.case_id))
    if case_exists:
        tracer = Tracer(store, payload.case_id)
        with tracer.span(
            "REMOTE_TOOL",
            payload.tool_name,
            actor=principal.actor,
            inputs={"parameters": payload.parameters, **correlation},
        ) as span:
            resp = gateway.call(
                payload.tool_name, payload.parameters,
                case_id=payload.case_id, actor=principal.actor,
                scope=list(principal.scopes), idempotency_key=payload.idempotency_key,
            )
            span["outputs"] = {
                "success": resp["success"],
                "tool_receipt": resp["tool_receipt"],
                "error_type": (resp.get("error") or {}).get("type"),
                **correlation,
            }
        store.audit(payload.case_id, principal.actor, "AGENTTEAMS_TOOL_CALLED", {
            "tool_name": payload.tool_name,
            "success": resp["success"],
            "tool_receipt": resp["tool_receipt"],
            **correlation,
        })
    else:
        resp = gateway.call(
            payload.tool_name, payload.parameters,
            case_id=payload.case_id, actor=principal.actor,
            scope=list(principal.scopes), idempotency_key=payload.idempotency_key,
        )
    response.headers["X-Request-ID"] = correlation["request_id"]
    response.headers["X-Tool-Receipt"] = resp["tool_receipt"]
    return resp


@app.get("/api/v1/skills")
def get_skills(_principal: ApiPrincipal = Depends(require_roles("viewer"))):
    """Skill 清单（设计文档 9.1：输入输出/依赖/失败处理/安全边界/复用价值）。"""
    catalog = []
    for item in list_skills():
        catalog.append({
            **item,
            "invoke_endpoint": f"/api/v1/skills/{item['name']}/invoke",
            "allowed_actors": sorted(SKILL_ACTORS.get(item["name"], [])),
        })
    return {"skills": catalog}


@app.post("/api/v1/skills/{skill_name}/invoke")
def invoke_registered_skill(skill_name: str, payload: SkillInvoke,
                            response: Response,
                            principal: ApiPrincipal = Depends(require_roles("worker")),
                            request_id: str | None = Header(default=None, alias="X-Request-ID"),
                            agentteams_message_id: str | None = Header(
                                default=None, alias="X-AgentTeams-Message-ID"
                            ),
                            traceparent: str | None = Header(
                                default=None, alias="traceparent"
                            ),
                            agent_task_id: str | None = Header(
                                default=None, alias="X-RevGuard-Task-ID"
                            ),
                            gateway_transport: str | None = Header(
                                default=None, alias="X-RevGuard-Transport"
                            )):
    """调用版本化 Skill；身份来自 Bearer principal，不接受自报 actor/scope。"""
    transport = "higress-mcp" if gateway_transport == "higress-mcp" else "rest"
    correlation = {
        "request_id": request_id or new_id("REQ"),
        "agentteams_message_id": agentteams_message_id,
        "traceparent": traceparent,
        "agent_task_id": agent_task_id,
        "transport": transport,
    }
    if any(value is not None and len(value) > 256 for value in correlation.values()):
        raise HTTPException(400, "关联请求头长度不能超过 256")
    if agent_task_id:
        active_task = store.get_agent_task(agent_task_id)
        if not active_task:
            raise HTTPException(404, f"Agent task 不存在: {agent_task_id}")
        if active_task["assigned_actor"] != principal.actor:
            raise HTTPException(403, "Agent task 不属于当前 Worker")
    try:
        if agent_task_id:
            execution_input, injected = hydrate_server_secrets(
                skill_name, payload.input, case_id=payload.case_id, store=store,
            )
            result = execute_agent_task(
                task_id=agent_task_id, case_id=payload.case_id,
                skill_name=skill_name, skill_input=payload.input,
                actor=principal.actor, gateway=gateway, store=store,
                correlation=correlation,
                execution_input=execution_input,
            )
            if injected:
                store.audit(payload.case_id, principal.actor,
                            "SERVER_CAPABILITY_INJECTED", {
                                "task_id": agent_task_id,
                                "skill": skill_name,
                                "injected": injected,
                                "request_id": correlation["request_id"],
                            })
        else:
            result = invoke_skill(
                skill_name, payload.input, actor=principal.actor,
                case_id=payload.case_id, gateway=gateway, store=store,
                correlation=correlation,
            )
        response.headers["X-Request-ID"] = correlation["request_id"]
        response.headers["X-Skill-Receipt"] = result["skill_receipt"]
        return result
    except SkillInvocationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        from .mocks import ToolError
        if isinstance(exc, ToolError) and exc.error_type == "AUTH_FAILED":
            raise HTTPException(403, exc.message) from exc
        if isinstance(exc, ToolError):
            raise HTTPException(400, {"type": exc.error_type, "message": exc.message}) from exc
        raise


@app.get("/api/v1/health")
def health():
    ready = store.readiness()
    return {"status": "ok", "release": RELEASE_VERSION,
            "cases": store.count_cases(), **ready}


@app.get("/api/v1/health/live")
def liveness():
    return {"status": "alive", "release": RELEASE_VERSION}


@app.get("/api/v1/health/ready")
def readiness():
    try:
        return {"status": "ready", **store.readiness()}
    except Exception as exc:
        raise HTTPException(503, {"status": "not_ready",
                                  "error_type": type(exc).__name__}) from exc


@app.get("/api/v1/ops/metrics")
def operational_metrics(
    _principal: ApiPrincipal = Depends(require_roles("viewer")),
):
    return store.operational_metrics()


@app.get("/api/v1/ops/metrics/prometheus", response_class=PlainTextResponse)
def prometheus_metrics(
    _principal: ApiPrincipal = Depends(require_roles("viewer")),
):
    return prometheus_text(store.operational_metrics())


@app.get("/api/v1/ops/evidence")
def engineering_evidence(
    _principal: ApiPrincipal = Depends(require_roles("viewer")),
):
    """Recording-safe engineering and value evidence with data provenance."""
    def read_json(name: str) -> dict | None:
        path = ROOT / "docs" / name
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    return {
        "release": RELEASE_VERSION,
        "runtime": store.operational_metrics(),
        "deterministic_evaluation": read_json("evaluation-summary.json"),
        "business_value": read_json("value-evaluation-synthetic.json"),
        "synthetic_dataset": read_json("synthetic-data-validation.json"),
        "mcp_rehearsal": read_json("evidence/demo-rehearsal/manifest.json"),
        "local_postgresql": read_json("polardb-local-verification-2026-08-27.json"),
        "self_hosted_polardb": read_json(
            "polardb-local-instance-acceptance-2026-08-29.json"
        ),
        "external_validation": {
            "production_business_baseline": "PENDING_COMPANY_DATA",
            "agentteams_room": (
                "CONFIGURED_MATRIX_NOT_LIVENESS_CHECK"
                if TEAM_TRANSPORT == "matrix" else "PENDING_EXTERNAL_CAPTURE"
            ),
            "self_hosted_polardb_pg": (
                "PASSED_LOCAL_INSTANCE"
                if store.backend == "postgresql-polardb" else "PENDING_DEPLOYMENT"
            ),
            "polardb_cloud_acceptance": "PENDING_CLOUD_INSTANCE",
            "polardb_pitr_drill": "PENDING_CLOUD_INSTANCE",
        },
    }


if ENABLE_RECORDING_UI:
    try:
        from fastapi.staticfiles import StaticFiles
    except ImportError:  # pragma: no cover - FastAPI already guards this path
        StaticFiles = None
    ui_dir = ROOT / "demo-ui" / "dist" / "client"
    if StaticFiles is not None and ui_dir.exists():
        app.mount("/demo", StaticFiles(directory=ui_dir, html=True), name="recording-ui")
    else:
        LOGGER.warning("录制 WebUI 尚未构建：%s", ui_dir)
