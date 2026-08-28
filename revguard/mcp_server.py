"""Process-scoped MCP adapter for RevGuard Skills.

Each server instance represents exactly one AgentTeams Worker.  The actor is
fixed by process configuration, ``tools/list`` exposes only that actor's Skills,
and every ``tools/call`` must carry a server-issued StageTask binding.  MCP only
replaces transport; Skill schemas, authorization, deterministic money logic,
ToolGateway receipts and the audit trail remain server-side controls.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any

from mcp.server import Server, ServerRequestContext
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListToolsResult,
    PaginatedRequestParams,
    TextContent,
    Tool,
    ToolAnnotations,
)

from .agent_bridge import execute_agent_task
from .json_schema import SchemaValidationError, validate_json
from .models import new_id
from .security import redact_secrets
from .skill_runtime import SKILL_ACTORS, SkillContractError, SkillInvocationError
from .skills import SKILL_REGISTRY

MCP_SERVER_VERSION = "0.4.0"
MAX_CORRELATION_LENGTH = 256
SERVER_INJECTION_REF = "$REVGUARD_SERVER_INJECTION"


@dataclass(frozen=True)
class McpRuntime:
    actor: str
    store: Any
    gateway: Any


def allowed_skills(actor: str) -> tuple[str, ...]:
    """Return the stable, process-scoped Skill surface for one Worker."""
    return tuple(sorted(
        name for name, actors in SKILL_ACTORS.items() if actor in actors
    ))


def _input_schema(skill_name: str) -> dict:
    """Wrap the canonical Skill schema in a mandatory StageTask envelope."""
    return {
        "type": "object",
        "properties": {
            "case_id": {
                "type": "string", "minLength": 1,
                "description": "Existing RevGuard case identifier.",
            },
            "task_id": {
                "type": "string", "minLength": 1,
                "description": "Server-dispatched StageTask bound to this Worker and input.",
            },
            "input": SKILL_REGISTRY[skill_name]["input_schema"],
            "request_id": {
                "type": "string", "minLength": 1, "maxLength": MAX_CORRELATION_LENGTH,
            },
            "agentteams_message_id": {
                "type": "string", "minLength": 1, "maxLength": MAX_CORRELATION_LENGTH,
            },
            "traceparent": {
                "type": "string", "minLength": 1, "maxLength": MAX_CORRELATION_LENGTH,
            },
        },
        "required": ["case_id", "task_id", "input"],
        "additionalProperties": False,
    }


def _output_schema(skill_name: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "const": True},
            "data": SKILL_REGISTRY[skill_name]["output_schema"],
            "error": {"type": "null"},
            "skill_receipt": {"type": "string", "minLength": 1},
            "skill": {"type": "string", "const": skill_name},
            "version": {"type": "string", "minLength": 1},
            "request_id": {"type": "string", "minLength": 1},
            "task_id": {"type": "string", "minLength": 1},
        },
        "required": [
            "success", "data", "error", "skill_receipt", "skill", "version",
            "request_id", "task_id",
        ],
        "additionalProperties": False,
    }


def _tool(skill_name: str, actor: str) -> Tool:
    meta = SKILL_REGISTRY[skill_name]
    annotations = meta["annotations"]
    read_only = bool(annotations.get("readOnlyHint"))
    return Tool(
        name=skill_name,
        title=f"RevGuard · {skill_name}",
        description=(
            f"{meta['description']} Calls require a server-dispatched StageTask; "
            "the Worker identity is fixed by this scoped MCP server."
        ),
        input_schema=_input_schema(skill_name),
        output_schema=_output_schema(skill_name),
        annotations=ToolAnnotations(
            readOnlyHint=read_only,
            destructiveHint=bool(annotations.get("destructiveHint")),
            idempotentHint=read_only,
            openWorldHint=False,
        ),
        _meta={
            "revguard/assignedActor": actor,
            "revguard/skillVersion": meta["version"],
            "revguard/requiresTaskBinding": True,
            "revguard/exposesUnderlyingTools": False,
        },
    )


def _safe_error(error_type: str, message: str, *, request_id: str,
                retryable: bool = False) -> CallToolResult:
    detail = redact_secrets({
        "success": False,
        "error": {
            "type": error_type,
            "message": message,
            "retryable": retryable,
        },
        "request_id": request_id,
    })
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(detail, ensure_ascii=False))],
        is_error=True,
        _meta={"revguard/requestId": request_id},
    )


def _hydrate_server_secrets(skill_name: str, skill_input: dict, *,
                            case_id: str, store) -> tuple[dict, list[str]]:
    """Resolve secret references only after public StageTask validation."""
    private_input = copy.deepcopy(skill_input)
    injected: list[str] = []
    if skill_name == "PermissionCheckSkill":
        public_approval = private_input.get("approval") or {}
        if public_approval.get("approval_token") not in {None, SERVER_INJECTION_REF}:
            raise ValueError("MCP 输入禁止携带原始审批能力凭证")
        if public_approval.get("approval_token") == SERVER_INJECTION_REF:
            approval = store.get_approval(case_id) or {}
            if approval.get("approval_id") != public_approval.get("approval_id"):
                raise ValueError("审批引用与案件当前审批单不匹配")
            if not approval.get("approval_token"):
                raise ValueError("案件没有可注入的审批能力凭证")
            private_input["approval"] = approval
            injected.append("approval_token")
    elif skill_name == "LedgerAdjustSkill":
        if private_input.get("approval_token") != SERVER_INJECTION_REF:
            raise ValueError("LedgerAdjustSkill 必须使用服务端审批凭证引用")
        if private_input.get("approval_token") == SERVER_INJECTION_REF:
            approval = store.get_approval(case_id) or {}
            if approval.get("status") != "APPROVED" or not approval.get("approval_token"):
                raise ValueError("案件没有已批准的服务端能力凭证")
            private_input["approval_token"] = approval["approval_token"]
            injected.append("approval_token")
    elif skill_name == "LedgerReverseSkill":
        if private_input.get("rollback_token") != SERVER_INJECTION_REF:
            raise ValueError("LedgerReverseSkill 必须使用服务端回滚凭证引用")
        if private_input.get("rollback_token") == SERVER_INJECTION_REF:
            ledger_id = private_input.get("ledger_id")
            execution = next(
                (
                    item for item in store.list_executions(case_id)
                    if (item.get("ledger_entry") or {}).get("ledger_id") == ledger_id
                ),
                None,
            )
            if not execution or not execution.get("rollback_token"):
                raise ValueError("执行记录没有可注入的回滚能力凭证")
            private_input["rollback_token"] = execution["rollback_token"]
            injected.append("rollback_token")
    return private_input, injected


def build_scoped_server(*, actor: str, store, gateway) -> Server:
    """Build one A1-style server exposing only ``actor``'s registered Skills."""
    scoped = allowed_skills(actor)
    if not scoped:
        raise ValueError(f"未知或无 Skill 的 MCP actor: {actor}")
    runtime = McpRuntime(actor=actor, store=store, gateway=gateway)
    catalog = tuple(_tool(name, actor) for name in scoped)

    async def list_tools(
        _ctx: ServerRequestContext,
        _params: PaginatedRequestParams | None,
    ) -> ListToolsResult:
        return ListToolsResult(tools=list(catalog))

    async def call_tool(
        _ctx: ServerRequestContext,
        params: CallToolRequestParams,
    ) -> CallToolResult:
        args = params.arguments or {}
        request_id = str(args.get("request_id") or new_id("REQ-MCP"))
        if params.name not in scoped:
            runtime.store.audit(
                str(args.get("case_id") or "MCP-NO-CASE"), runtime.actor,
                "MCP_SCOPE_REJECTED", {"skill": params.name, "request_id": request_id},
            )
            return _safe_error(
                "SKILL_NOT_ALLOWED",
                f"{runtime.actor} 无权通过 MCP 调用 {params.name}",
                request_id=request_id,
            )
        try:
            validate_json(args, _input_schema(params.name), path=f"mcp.{params.name}")
            for field in ("request_id", "agentteams_message_id", "traceparent"):
                value = args.get(field)
                if value is not None and len(value) > MAX_CORRELATION_LENGTH:
                    raise SchemaValidationError(f"mcp.{params.name}.{field} 长度不能超过 256")
            correlation = {
                "transport": "mcp",
                "mcp_method": "tools/call",
                "request_id": request_id,
                "agentteams_message_id": args.get("agentteams_message_id"),
                "traceparent": args.get("traceparent"),
                "agent_task_id": args["task_id"],
            }
            execution_input, injected = _hydrate_server_secrets(
                params.name, args["input"], case_id=args["case_id"],
                store=runtime.store,
            )
            if injected:
                runtime.store.audit(
                    args["case_id"], runtime.actor, "MCP_SERVER_SECRET_INJECTED", {
                        "task_id": args["task_id"],
                        "skill": params.name,
                        "fields": injected,
                        "request_id": request_id,
                    },
                )
            result = execute_agent_task(
                task_id=args["task_id"], case_id=args["case_id"],
                skill_name=params.name, skill_input=args["input"],
                actor=runtime.actor, gateway=runtime.gateway, store=runtime.store,
                correlation=correlation,
                execution_input=execution_input,
            )
        except (SchemaValidationError, SkillInvocationError, SkillContractError,
                LookupError, ValueError) as exc:
            return _safe_error(
                type(exc).__name__, str(exc), request_id=request_id,
            )
        except Exception as exc:  # noqa: BLE001 - protocol boundary must redact failures.
            retryable = bool(getattr(exc, "retryable", False))
            error_type = str(getattr(exc, "error_type", type(exc).__name__))
            message = str(getattr(exc, "message", "Skill execution failed"))
            return _safe_error(
                error_type, message, request_id=request_id, retryable=retryable,
            )

        public_result = redact_secrets({
            **result,
            "request_id": request_id,
            "task_id": args["task_id"],
        })
        return CallToolResult(
            content=[TextContent(
                type="text", text=json.dumps(public_result, ensure_ascii=False),
            )],
            structured_content=public_result,
            _meta={
                "revguard/requestId": request_id,
                "revguard/taskId": args["task_id"],
                "revguard/skillReceipt": result["skill_receipt"],
            },
        )

    return Server(
        "RevGuard Scoped Skills",
        version=MCP_SERVER_VERSION,
        description="Task-bound, least-privilege MCP transport for RevGuard Skills.",
        instructions=(
            f"This process represents {actor}. Call only listed Skills with the exact "
            "case_id, task_id and input issued by the RevGuard dispatcher."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
