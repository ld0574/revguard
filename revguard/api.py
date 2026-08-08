"""RevGuard FastAPI 服务（可选层，供前端与 AgentTeams Worker 调用）。

设计约定：
- 编排骨架与 run_demo.py 完全一致，API 只是触发入口；
- 审批模式为 wait 时，案件在 WAITING_FOR_APPROVAL 挂起，
  由 POST /cases/{id}/approval 人工审批后自动续跑执行与验证；
- /tools/call 是 AgentTeams Worker 访问 Skill 层的统一契约入口；
- 所有端点只返回结构化数据，错误带明确类型。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Case, CaseStatus
from .mocks import ToolGateway
from .orchestrator import Orchestrator
from .skills import list_skills
from .store import Store
from .trace import Tracer

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError as exc:  # 本地纯标准库跑 Demo 时允许不安装 FastAPI
    raise ImportError("API 服务需要安装依赖：pip install -r requirements.txt") from exc

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = os.getenv("REVGUARD_DB_PATH", str(ROOT / "data" / "revguard.db"))
FIXTURES = os.getenv("REVGUARD_FIXTURES_DIR", str(ROOT / "data" / "fixtures"))
OUTPUT_DIR = os.getenv("REVGUARD_OUTPUT_DIR", str(ROOT / "data" / "outputs"))
REPORT_DIR = os.getenv("REVGUARD_REPORT_DIR", str(ROOT / "docs" / "reports"))
APPROVAL_MODE = os.getenv("REVGUARD_APPROVAL_MODE", "wait")  # 服务端默认等待人工审批
FINANCE_FAIL_TIMES = int(os.getenv("REVGUARD_FINANCE_FAIL_TIMES", "1"))

app = FastAPI(title="RevGuard API", version="0.1.0",
              description="企业渠道佣金与结算异常多 Agent 协同平台")

# Demo 单进程即可：共享一份 Store / Mock 系统状态
store = Store(DB_PATH)
gateway = ToolGateway(FIXTURES, finance_fail_times=FINANCE_FAIL_TIMES)


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
    claim: dict = {}


class ApprovalDecision(BaseModel):
    decision: str            # APPROVED / REJECTED
    approver: str
    comment: str = ""


class ToolCall(BaseModel):
    tool_name: str
    parameters: dict = {}
    case_id: str = ""
    actor: str = "api"
    scope: list[str] = []
    idempotency_key: str | None = None


# --------------------------------------------------------------------- 案件
@app.post("/api/v1/cases", status_code=201)
def create_case(payload: CaseCreate):
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
    store.audit(case["case_id"], "api", "CASE_CREATED", {"source": payload.source})
    return case


@app.get("/api/v1/cases")
def list_cases():
    return {"cases": store.list_cases()}


@app.get("/api/v1/cases/{case_id}")
def get_case(case_id: str):
    case = store.get_case(case_id)
    if not case:
        raise HTTPException(404, f"案件不存在: {case_id}")
    case["evidence"] = store.list_evidence(case_id)
    case["audit_events"] = store.list_audit(case_id)
    return case


@app.post("/api/v1/cases/{case_id}/run")
def run_case(case_id: str):
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
def decide_approval(case_id: str, payload: ApprovalDecision):
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
        "approver": payload.approver,
        "comment": payload.comment,
    }, case_id=case_id, actor=payload.approver, scope=["approval:decide"])
    if not resp["success"]:
        raise HTTPException(400, resp["error"])
    decided = resp["data"]
    store.save_approval({"approval_id": decided["approval_id"], "case_id": case_id, **decided})
    store.audit(case_id, payload.approver, "APPROVAL_DECIDED",
                {"decision": decided["status"], "simulated_human": False})

    if decided["status"] != "APPROVED":
        case["status"] = CaseStatus.REJECTED.value
        store.save_case(case)
        return {"case": case, "approval": decided}

    case["status"] = CaseStatus.READY_TO_EXECUTE.value
    store.save_case(case)
    orch = _orchestrator()
    state = orch.execute_and_verify(case)
    orch._finalize(case, state, Tracer(store, case_id), archived=True)
    return {"case": store.get_case(case_id), "approval": decided,
            "verification": state.get("verification")}


@app.get("/api/v1/cases/{case_id}/trace")
def get_trace(case_id: str):
    if not store.get_case(case_id):
        raise HTTPException(404, f"案件不存在: {case_id}")
    return Tracer(store, case_id).export()


@app.get("/api/v1/cases/{case_id}/report")
def get_report(case_id: str):
    report_path = Path(REPORT_DIR) / f"{case_id}.md"
    if not report_path.exists():
        raise HTTPException(404, "审计报告尚未生成")
    return {"case_id": case_id, "markdown": report_path.read_text(encoding="utf-8")}


# ---------------------------------------------------------------- 工具与 Skill
@app.post("/api/v1/tools/call")
def call_tool(payload: ToolCall):
    """AgentTeams Worker 访问 Skill 层的统一工具契约入口（设计文档 13.1）。"""
    resp = gateway.call(payload.tool_name, payload.parameters,
                        case_id=payload.case_id, actor=payload.actor,
                        scope=payload.scope, idempotency_key=payload.idempotency_key)
    return resp


@app.get("/api/v1/skills")
def get_skills():
    """Skill 清单（设计文档 9.1：输入输出/依赖/失败处理/安全边界/复用价值）。"""
    return {"skills": list_skills()}


@app.get("/api/v1/health")
def health():
    return {"status": "ok", "cases": len(store.list_cases())}
