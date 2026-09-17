#!/usr/bin/env python3
"""Least-privilege AgentTeams → RevGuard Skills-only API adapter."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ALLOWED_SKILLS_BY_WORKER = {
    "revguard-intake": frozenset({"CaseNormalizeSkill", "EntityResolveSkill"}),
    "revguard-evidence": frozenset({"EvidenceCollectSkill"}),
    "revguard-policy": frozenset({"PolicyVersionMatchSkill"}),
    "revguard-calculation": frozenset({"CommissionCalculateSkill"}),
    "revguard-rootcause": frozenset({"DifferenceExplainSkill"}),
    "revguard-risk": frozenset({"RiskClassifySkill", "ApprovalRouteSkill"}),
    "revguard-executor": frozenset({
        "PermissionCheckSkill", "IdempotencyGuardSkill", "AdjustmentDraftSkill",
        "LedgerAdjustSkill", "LedgerReverseSkill",
    }),
    "revguard-verifier": frozenset({"PostActionVerifySkill", "PostRollbackVerifySkill"}),
    "revguard-knowledge": frozenset({"CaseToDatasetSkill"}),
}


def _credential_path() -> Path:
    worker = os.getenv("AGENTTEAMS_WORKER_NAME", "revguard-evidence")
    return Path(f"/root/.copaw-worker/{worker}/.copaw.secret/revguard_api_key")


def _worker_name() -> str:
    return os.getenv("AGENTTEAMS_WORKER_NAME", "").strip()


def _mcporter_config(worker: str) -> Path | None:
    candidates = [
        Path.cwd() / "config" / "mcporter.json",
        Path(f"/root/agentteams-fs/agents/{worker}/config/mcporter.json"),
        Path(f"/root/.copaw-worker/{worker}/config/mcporter.json"),
    ]
    server = f"mcp-{worker}"
    for path in candidates:
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if server in (config.get("mcpServers") or {}):
            return path
    return None


def _unwrap_mcporter_result(raw: object) -> dict:
    """Normalize mcporter JSON output to the RevGuard Skill response body."""
    if isinstance(raw, dict) and "success" in raw:
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, list):
            errors = []
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                message = str(item.get("text") or "")
                try:
                    value = json.loads(message)
                except json.JSONDecodeError:
                    if raw.get("isError"):
                        errors.append(_mcp_error(message))
                    continue
                if isinstance(value, dict) and "success" in value:
                    return value
                if raw.get("isError"):
                    errors.append(_mcp_error(message))
            if errors:
                return {"success": False, "error": errors[0]}
        result = raw.get("result")
        if isinstance(result, dict):
            return _unwrap_mcporter_result(result)
    raise ValueError("MCP response does not contain a RevGuard Skill envelope")


def _mcp_error(message: str) -> dict:
    """Expose actionable upstream failures without echoing request inputs/secrets."""
    match = re.search(r"call failed, status: (\d+), response: (.*)", message, re.S)
    error_type = f"HTTP_{match.group(1)}" if match else "MCP_TOOL_ERROR"
    body = match.group(2) if match else message
    try:
        detail = json.loads(body).get("detail")
    except (json.JSONDecodeError, AttributeError):
        detail = None
    if isinstance(detail, list):
        summary = "; ".join(
            f"{'.'.join(str(part) for part in item.get('loc', []))}: {item.get('msg', '')}"
            for item in detail if isinstance(item, dict)
        )
    elif isinstance(detail, str):
        summary = detail
    else:
        summary = "MCP 工具调用被拒绝，请按请求编号查看网关日志"
    return {"type": error_type, "message": summary[:800]}


def _mcporter_call(worker: str, tool: str, arguments: dict, config_path: Path) -> object:
    """Call one tool on the Worker's own scoped MCP server.

    The Worker holds no RevGuard backend credential; Higress injects it
    server-side after the per-Worker consumer allow-list check.
    """
    completed = subprocess.run(
        [
            "mcporter", "call", f"mcp-{worker}.{tool}",
            "--args", json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            "--output", "json", "--timeout", "20000",
        ],
        cwd=config_path.parent.parent,
        text=True,
        capture_output=True,
        timeout=25,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"mcporter exited with status {completed.returncode}")
    return json.loads(completed.stdout)


def _invoke_higress_mcp(
    worker: str,
    skill: str,
    *,
    case_id: str,
    skill_input: dict,
    message_id: str,
    request_id: str,
    task_id: str,
    config_path: Path,
    traceparent: str = "",
) -> dict:
    arguments = {
        "caseId": case_id,
        "input": skill_input,
        "messageId": message_id,
        "requestId": request_id,
        "taskId": task_id, "traceparent": traceparent,
    }
    result = _unwrap_mcporter_result(
        _mcporter_call(worker, skill, arguments, config_path)
    )
    result.setdefault("success", True)
    result.setdefault("request_id", request_id)
    result["transport"] = "higress-mcp"
    return result


def _bound_task_payload(raw: object) -> dict:
    """Accept both a raw REST body and an MCP content envelope."""
    if isinstance(raw, dict) and str(raw.get("task_id") or ""):
        return raw
    if isinstance(raw, dict):
        content = raw.get("content")
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                try:
                    value = json.loads(str(item.get("text") or ""))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and value.get("task_id"):
                    return value
    raise ValueError("MCP binding response does not contain a StageTask")


def _mcp_bound_task(worker: str, task_id: str) -> dict:
    """Resolve one StageTask binding through the scoped MCP server.

    Business Workers deliberately carry no RevGuard backend key, so the binding
    lookup travels the same Higress path as the Skill call itself.
    """
    config_path = _mcporter_config(worker)
    if config_path is None:
        raise ValueError("Worker MCP binding lookup is unavailable")
    return _bound_task_payload(
        _mcporter_call(worker, "BoundStageTask", {"taskId": task_id}, config_path)
    )


def _api_json(api_base: str, path: str, api_key: str) -> dict:
    request = urllib.request.Request(
        f"{api_base}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("API response must be an object")
    return value


def _load_api_key() -> str:
    try:
        api_key = _credential_path().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("RevGuard Worker Principal is unavailable") from exc
    if not api_key:
        raise ValueError("RevGuard Worker Principal is empty")
    return api_key


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--skill")
    action.add_argument("--dispatch-skill")
    parser.add_argument("--case-id")
    parser.add_argument("--input")
    parser.add_argument("--from-task", action="store_true")
    message = parser.add_mutually_exclusive_group()
    message.add_argument("--message-id")
    message.add_argument("--message-id-hex")
    parser.add_argument("--request-id")
    parser.add_argument("--task-id")
    parser.add_argument("--traceparent", default="")
    args = parser.parse_args()

    if not args.from_task and not (args.skill or args.dispatch_skill):
        parser.error("必须提供 --skill、--dispatch-skill 或 --from-task")
    if args.skill and args.dispatch_skill:
        parser.error("--skill 与 --dispatch-skill 不能同时提供")

    try:
        message_id = (
            bytes.fromhex(args.message_id_hex).decode("utf-8")
            if args.message_id_hex else (args.message_id or "")
        )
    except (ValueError, UnicodeDecodeError):
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": "message-id-hex must encode UTF-8"}}))
        return 2

    worker = _worker_name()
    if args.dispatch_skill:
        if worker != "revguard-orchestrator":
            print(json.dumps({"success": False, "error": {"type": "DISPATCH_NOT_ALLOWED",
                             "message": f"{worker or '<unknown>'} cannot dispatch tasks"}}))
            return 4
    elif not args.from_task:
        allowed = ALLOWED_SKILLS_BY_WORKER.get(worker)
        if allowed is None or args.skill not in allowed:
            print(json.dumps({"success": False, "error": {"type": "SKILL_NOT_ALLOWED",
                             "message": f"{worker or '<unknown>'} cannot invoke {args.skill}"}}))
            return 4
        if not args.task_id:
            print(json.dumps({"success": False, "error": {"type": "TASK_ID_REQUIRED",
                             "message": "Worker Skill invocation requires --task-id"}}))
            return 4

    api_base = os.getenv("REVGUARD_API_BASE_URL", "http://revguard-api:9000").rstrip("/")
    api_key = ""
    if args.from_task:
        if args.dispatch_skill or not args.task_id:
            print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                             "message": "--from-task requires a Worker --task-id"}}))
            return 2
        if _credential_path().exists():
            try:
                api_key = _load_api_key()
                bound = _api_json(api_base, f"/api/v1/agent-tasks/{args.task_id}", api_key)
            except urllib.error.HTTPError as exc:
                print(json.dumps({"success": False, "error": {"type": f"HTTP_{exc.code}",
                                 "message": "Bound StageTask is unavailable"}}))
                return 1
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                                 "message": f"Bound StageTask lookup failed ({type(exc).__name__})"}}))
                return 3
        else:
            # Business Workers hold no backend key; the binding is read through
            # this Worker's own scoped MCP server (Higress injects credentials).
            try:
                bound = _mcp_bound_task(worker, args.task_id)
            except urllib.error.HTTPError as exc:
                print(json.dumps({"success": False, "error": {"type": f"HTTP_{exc.code}",
                                 "message": "Bound StageTask is unavailable"}}))
                return 1
            except (OSError, ValueError, json.JSONDecodeError, RuntimeError,
                    subprocess.TimeoutExpired) as exc:
                print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                                 "message": f"Bound StageTask lookup failed ({type(exc).__name__})"}}))
                return 3
        args.skill = bound.get("skill_name")
        args.case_id = bound.get("case_id")
        args.input = json.dumps(bound.get("input"), ensure_ascii=False, separators=(",", ":"))
        args.request_id = bound.get("request_id") or args.request_id
        message_id = bound.get("agentteams_message_id") or message_id
        args.traceparent = bound.get("traceparent") or args.traceparent

    if not args.case_id or args.input is None or not message_id:
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": "case, input and message correlation are required"}}))
        return 2
    try:
        skill_input = json.loads(args.input)
        if not isinstance(skill_input, dict):
            raise ValueError("input must be an object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": str(exc)}}))
        return 2

    if not args.dispatch_skill:
        allowed = ALLOWED_SKILLS_BY_WORKER.get(worker)
        if allowed is None or args.skill not in allowed:
            print(json.dumps({"success": False, "error": {"type": "SKILL_NOT_ALLOWED",
                             "message": f"{worker or '<unknown>'} cannot invoke {args.skill}"}}))
            return 4

    request_id = args.request_id or f"REQ-AGT-{uuid.uuid4().hex[:12].upper()}"
    mcp_config = None if args.dispatch_skill else _mcporter_config(worker)
    if mcp_config is not None:
        try:
            result = _invoke_higress_mcp(
                worker,
                args.skill,
                case_id=args.case_id,
                skill_input=skill_input,
                message_id=message_id,
                request_id=request_id,
                task_id=args.task_id,
                config_path=mcp_config, traceparent=args.traceparent,
            )
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            result = {
                "success": False,
                "request_id": request_id,
                "transport": "higress-mcp",
                "error": {
                    "type": "MCP_GATEWAY_UNAVAILABLE",
                    "message": f"Higress MCP 调用失败（{type(exc).__name__}）",
                },
            }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("success") else 1

    if not api_key:
        try:
            api_key = _load_api_key()
        except ValueError as exc:
            print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                             "message": str(exc)}}))
            return 3

    if args.dispatch_skill:
        body_data = {"skill_name": args.dispatch_skill, "input": skill_input}
        url = f"{api_base}/api/v1/cases/{args.case_id}/agent-tasks"
    else:
        body_data = {"input": skill_input, "case_id": args.case_id}
        url = f"{api_base}/api/v1/skills/{args.skill}/invoke"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-AgentTeams-Message-ID": message_id,
        "X-Request-ID": request_id, "traceparent": args.traceparent,
    }
    if args.task_id:
        headers["X-RevGuard-Task-ID"] = args.task_id
    request = urllib.request.Request(
        url,
        data=json.dumps(body_data).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
            result.setdefault("success", True)
            result["request_id"] = response.headers.get("X-Request-ID", request_id)
            if not args.dispatch_skill:
                result["skill_receipt"] = response.headers.get(
                    "X-Skill-Receipt", result.get("skill_receipt")
                )
            result["transport"] = "rest-fallback"
    except urllib.error.HTTPError as exc:
        result = {
            "success": False,
            "request_id": request_id,
            "error": {"type": f"HTTP_{exc.code}", "message": "RevGuard request rejected"},
        }
    except (OSError, ValueError) as exc:
        result = {
            "success": False,
            "request_id": request_id,
            "error": {"type": "ADAPTER_UNAVAILABLE", "message": type(exc).__name__},
        }

    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
