#!/usr/bin/env python3
"""Least-privilege AgentTeams → RevGuard Skills-only API adapter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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


def main() -> int:
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--skill")
    action.add_argument("--dispatch-skill")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--request-id")
    parser.add_argument("--task-id")
    args = parser.parse_args()

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

    request_id = args.request_id or f"REQ-AGT-{uuid.uuid4().hex[:12].upper()}"
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
        "X-AgentTeams-Message-ID": args.message_id,
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
