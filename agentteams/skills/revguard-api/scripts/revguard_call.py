#!/usr/bin/env python3
"""Least-privilege AgentTeams → RevGuard Skills-only API adapter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
import uuid


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
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                try:
                    value = json.loads(str(item.get("text") or ""))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    return value
        result = raw.get("result")
        if isinstance(result, dict):
            return _unwrap_mcporter_result(result)
    raise ValueError("MCP response does not contain a RevGuard Skill envelope")


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
) -> dict:
    arguments = {
        "caseId": case_id,
        "input": skill_input,
        "messageId": message_id,
        "requestId": request_id,
        "taskId": task_id,
    }
    completed = subprocess.run(
        [
            "mcporter", "call", f"mcp-{worker}.{skill}",
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
    result = _unwrap_mcporter_result(json.loads(completed.stdout))
    result.setdefault("success", True)
    result.setdefault("request_id", request_id)
    result["transport"] = "higress-mcp"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--skill")
    action.add_argument("--dispatch-skill")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--input", required=True)
    message = parser.add_mutually_exclusive_group(required=True)
    message.add_argument("--message-id")
    message.add_argument("--message-id-hex")
    parser.add_argument("--request-id")
    parser.add_argument("--task-id")
    args = parser.parse_args()

    try:
        message_id = (
            bytes.fromhex(args.message_id_hex).decode("utf-8")
            if args.message_id_hex else args.message_id
        )
    except (ValueError, UnicodeDecodeError):
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": "message-id-hex must encode UTF-8"}}))
        return 2

    try:
        skill_input = json.loads(args.input)
        if not isinstance(skill_input, dict):
            raise ValueError("input must be an object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": str(exc)}}))
        return 2

    worker = _worker_name()
    if args.dispatch_skill:
        if worker != "revguard-orchestrator":
            print(json.dumps({"success": False, "error": {"type": "DISPATCH_NOT_ALLOWED",
                             "message": f"{worker or '<unknown>'} cannot dispatch tasks"}}))
            return 4
    else:
        allowed = ALLOWED_SKILLS_BY_WORKER.get(worker)
        if allowed is None or args.skill not in allowed:
            print(json.dumps({"success": False, "error": {"type": "SKILL_NOT_ALLOWED",
                             "message": f"{worker or '<unknown>'} cannot invoke {args.skill}"}}))
            return 4
        if not args.task_id:
            print(json.dumps({"success": False, "error": {"type": "TASK_ID_REQUIRED",
                             "message": "Worker Skill invocation requires --task-id"}}))
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
                config_path=mcp_config,
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

    secret_path = _credential_path()
    try:
        api_key = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                         "message": "RevGuard Worker Principal is unavailable"}}))
        return 3
    if not api_key:
        print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                         "message": "RevGuard Worker Principal is empty"}}))
        return 3

    api_base = os.getenv("REVGUARD_API_BASE_URL", "http://revguard-api:9000").rstrip("/")
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
        "X-Request-ID": request_id,
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
