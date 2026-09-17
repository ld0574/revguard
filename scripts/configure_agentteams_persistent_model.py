#!/usr/bin/env python3
"""Persist bounded model kwargs in AgentTeams' MinIO source of truth.

The CoPaw container is ephemeral.  Updating only its local model API is lost
after the Worker sleeps, so this utility edits the authoritative encrypted
provider object through ``mc`` without printing credentials or provider JSON.

The object contains the internal Higress gateway credential.  Upstream provider
credentials belong to AgentTeams' installation environment and must never be
written here.
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404

WORKERS = (
    "revguard-orchestrator",
    "revguard-intake",
    "revguard-evidence",
    "revguard-policy",
    "revguard-calculation",
    "revguard-rootcause",
    "revguard-risk",
    "revguard-executor",
    "revguard-verifier",
    "revguard-knowledge",
)
PROVIDER = "agentteams-gateway"


def object_path(worker: str) -> str:
    return (
        "agentteams/agentteams-storage/agents/"
        f"{worker}/.copaw.secret/providers/custom/{PROVIDER}.json"
    )


def read_object(controller: str, path: str) -> dict:
    raw = subprocess.check_output(  # nosec B603, B607
        ["docker", "exec", controller, "mc", "cat", path],
    )
    return json.loads(raw)


def write_object(controller: str, path: str, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    subprocess.run(  # nosec B603, B607
        ["docker", "exec", "-i", controller, "mc", "pipe", path],
        input=raw,
        stdout=subprocess.DEVNULL,
        check=True,
    )


def update_provider(
    payload: dict,
    model: str,
    max_tokens: int,
) -> None:
    candidates = [
        *(payload.get("models") or []),
        *(payload.get("extra_models") or []),
    ]
    selected = next((item for item in candidates if item.get("id") == model), None)
    if selected is None:
        selected = {"id": model, "name": model}
        payload.setdefault("extra_models", []).append(selected)
    kwargs = dict(selected.get("generate_kwargs") or {})
    if model in {"gpt-5.6-luna", "gpt-5.6-sol"}:
        kwargs.update(
            reasoning_effort="none",
            max_completion_tokens=max_tokens,
        )
        kwargs.pop("max_tokens", None)
    else:
        # glm-5.3-flash 是思考模型：缺少 reasoning_effort 时会按默认长思考把
        # max_tokens 全部烧在 reasoning_content 上，最终 content 为空，
        # Worker 表现为"没有产生命令"。上游探针确认 low 能降低思考量，
        # 但 512 预算仍可能被思考耗尽，因此必须同时给足 completion budget；
        # thinking.type=enabled 会进一步增加思考量，所以只下发 low。
        kwargs["max_tokens"] = max_tokens
        kwargs["reasoning_effort"] = "low"
        kwargs.pop("max_completion_tokens", None)
    selected["generate_kwargs"] = kwargs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", default="agentteams-controller")
    parser.add_argument("--model", default="glm-5.3-flash")
    parser.add_argument("--max-completion-tokens", type=int, default=2048)
    args = parser.parse_args()
    if args.model not in {"glm-5.3-flash", "gpt-5.6-luna", "gpt-5.6-sol"}:
        raise SystemExit("只允许持久化已验收的 glm-5.3-flash / Luna / Sol")
    if not 64 <= args.max_completion_tokens <= 4096:
        raise SystemExit("max-completion-tokens 必须在 64..4096")

    for worker in WORKERS:
        path = object_path(worker)
        payload = read_object(args.controller, path)
        update_provider(payload, args.model, args.max_completion_tokens)
        write_object(args.controller, path, payload)
    print(
        f"已持久化 {len(WORKERS)} 个 Agent 的 {args.model} 成本上限："
        f"limit={args.max_completion_tokens}。"
    )


if __name__ == "__main__":
    main()
