"""RevGuard FastAPI 服务（可选层，供前端与 AgentTeams Worker 调用）。

设计约定：
- 编排骨架与 run_demo.py 完全一致，API 只是触发入口；
- 审批模式为 wait 时，案件在 WAITING_FOR_APPROVAL 挂起，
  由 POST /cases/{id}/approval 人工审批后自动续跑执行与验证；
- /tools/call 是 AgentTeams Worker 访问 Skill 层的统一契约入口；
- 所有端点只返回结构化数据，错误带明确类型。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from .models import Case, CaseStatus
from .mocks import ToolGateway
from .orchestrator import Orchestrator
from .skills import list_skills
from .skill_runtime import SKILL_ACTORS, SkillInvocationError, invoke_skill
from .store import Store
from .state_machine import transition_case
from .trace import Tracer
from .security import (ApiPrincipal, SecurityError, constant_time_lookup,
                       load_api_principals, secret_fingerprint)

try:
    from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
    from pydantic import BaseModel, ConfigDict, Field
except ImportError as exc:  # 本地纯标准库跑 Demo 时允许不安装 FastAPI
    raise ImportError("API 服务需要安装依赖：pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("REVGUARD_DB_PATH", str(ROOT / "data" / "revguard.db"))
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

app = FastAPI(title="RevGuard API", version="0.2.0",
              description="企业渠道佣金与结算异常多 Agent 协同平台")

# Demo 单进程即可：共享一份 Store / Mock 系统状态
store = Store(DB_PATH)
gateway = ToolGateway(FIXTURES, finance_fail_times=FINANCE_FAIL_TIMES,
                      signing_key=SIGNING_KEY, state_path=GATEWAY_STATE_PATH,
                      verification_tamper_amount=VERIFICATION_TAMPER_AMOUNT)


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


def _orchestrator() -> Orchestrator:
    return Orchestrator(store, gateway, output_dir=OUTPUT_DIR,
                        report_dir=REPORT_DIR, approval_mode=APPROVAL_MODE)


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


@app.post("/api/v1/cases/{case_id}/run")
def run_case(case_id: str, _principal: ApiPrincipal = Depends(require_roles("operator"))):
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] not in (CaseStatus.CREATED.value,):
        raise HTTPException(409, f"案件状态 {case['status']} 不允许从头运行")
    try:
        state = _orchestrator().run_case(case)
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
    return {"case": store.get_case(case_id), "state_status": state.get("final_status")}


@app.post("/api/v1/cases/{case_id}/approval")
def decide_approval(case_id: str, payload: ApprovalDecision,
                    principal: ApiPrincipal = Depends(require_roles("approver"))):
    """人工审批节点：审批通过后自动续跑执行与独立验证。"""
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    if case["status"] != CaseStatus.WAITING_FOR_APPROVAL.value:
        raise HTTPException(409, f"案件状态 {case['status']} 不在等待审批节点")
    approval = store.get_approval(case_id)
    if not approval:
        raise HTTPException(409, "未找到审批单")

    resp = gateway.call("workflow.decide_approval", {
        "approval_id": approval["approval_id"],
        "decision": payload.decision,
        "comment": payload.comment,
    }, case_id=case_id, actor=principal.actor, scope=["approval:decide"])
    if not resp["success"]:
        raise HTTPException(400, resp["error"])
    decided = resp["data"]
    store.save_approval({"approval_id": decided["approval_id"], "case_id": case_id, **decided})
    store.audit(case_id, principal.actor, "APPROVAL_DECIDED",
                {"decision": decided["status"], "simulated_human": False})

    if decided["status"] != "APPROVED":
        transition_case(store, case, CaseStatus.REJECTED, "人工审批驳回", actor=principal.actor)
        return {"case": case, "approval": decided}

    transition_case(store, case, CaseStatus.READY_TO_EXECUTE,
                    "人工审批通过", actor=principal.actor)
    orch = _orchestrator()
    state = orch.execute_and_verify(case)
    orch._finalize(case, state, Tracer(store, case_id), archived=True)
    public_approval = {k: v for k, v in decided.items() if k != "approval_token"}
    public_approval["approval_token_ref"] = secret_fingerprint(
        decided.get("approval_token", "")
    )
    return {"case": store.get_case(case_id), "approval": public_approval,
            "verification": state.get("verification")}


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
    from .models import new_id

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
                            principal: ApiPrincipal = Depends(require_roles("worker"))):
    """调用版本化 Skill；身份来自 Bearer principal，不接受自报 actor/scope。"""
    try:
        return invoke_skill(
            skill_name, payload.input, actor=principal.actor, case_id=payload.case_id,
            gateway=gateway, store=store,
        )
    except SkillInvocationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        from .mocks import ToolError
        if isinstance(exc, ToolError) and exc.error_type == "AUTH_FAILED":
            raise HTTPException(403, exc.message) from exc
        if isinstance(exc, ToolError):
            raise HTTPException(400, {"type": exc.error_type, "message": exc.message}) from exc
        raise


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "cases": store.count_cases()}
