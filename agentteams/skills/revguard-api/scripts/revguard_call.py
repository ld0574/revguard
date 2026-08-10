#!/usr/bin/env python3
"""Least-privilege AgentTeams → RevGuard Evidence API adapter."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid


ALLOWED_TOOLS = frozenset({
    "crm.get_order",
    "crm.get_partner",
    "crm.get_partner_tier_history",
    "contract.get_contract",
    "policy.search_versions",
    "finance.get_payment",
    "finance.get_refund",
    "finance.get_invoice",
    "finance.get_commission_ledger",
})


def _credential_path() -> Path:
    worker = os.getenv("AGENTTEAMS_WORKER_NAME", "revguard-evidence")
    return Path(f"/root/.copaw-worker/{worker}/.copaw.secret/revguard_api_key")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool", required=True, choices=sorted(ALLOWED_TOOLS))
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--parameters", required=True)
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--request-id")
    args = parser.parse_args()

    try:
        parameters = json.loads(args.parameters)
        if not isinstance(parameters, dict):
            raise ValueError("parameters must be an object")
    except (json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"success": False, "error": {"type": "INVALID_PARAMS",
                         "message": str(exc)}}))
        return 2

    secret_path = _credential_path()
    try:
        api_key = secret_path.read_text(encoding="utf-8").strip()
    except OSError:
        print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                         "message": "RevGuard Evidence Principal is unavailable"}}))
        return 3
    if not api_key:
        print(json.dumps({"success": False, "error": {"type": "ADAPTER_CONFIG",
                         "message": "RevGuard Evidence Principal is empty"}}))
        return 3

    request_id = args.request_id or f"REQ-AGT-{uuid.uuid4().hex[:12].upper()}"
    body = json.dumps({
        "tool_name": args.tool,
        "parameters": parameters,
        "case_id": args.case_id,
    }).encode("utf-8")
    request = urllib.request.Request(
        os.getenv("REVGUARD_API_URL", "http://revguard-api:9000/api/v1/tools/call"),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-AgentTeams-Message-ID": args.message_id,
            "X-Request-ID": request_id,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
            result["request_id"] = response.headers.get("X-Request-ID", request_id)
            result["tool_receipt"] = response.headers.get(
                "X-Tool-Receipt", result.get("tool_receipt")
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
