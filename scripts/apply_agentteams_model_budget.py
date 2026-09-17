#!/usr/bin/env python3
"""Apply the verified glm-5.3-flash generation budget to running AgentTeams agents.

AgentTeams' controller regenerates each agent's provider defaults from its own
model registry when the container (re)starts; for ``glm-5.3-flash`` that
registry yields a 512-token completion budget without a reasoning-effort hint,
which makes the thinking model burn the whole budget inside
``reasoning_content`` and return an empty answer.

RevGuard therefore re-applies the verified budget (``max_tokens=2048`` plus
``reasoning_effort=low``) through CoPaw's own model configuration API right
after an agent is up, and verifies the effective kwargs that the agent will
send upstream.  The synthetic probe performs no business action.

The Manager runs the same CoPaw model API on port 18799 inside
``agentteams-manager``; pass ``--include-manager`` (or ``--manager-only``) to
cover it as well.

Usage (on the AgentTeams host):

    python3 scripts/apply_agentteams_model_budget.py
    python3 scripts/apply_agentteams_model_budget.py --workers intake policy  # 自动补全 revguard- 前缀
    python3 scripts/apply_agentteams_model_budget.py --include-manager

Exit code is non-zero when any running agent fails verification.
"""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404 - fixed docker/CLI calls only
import sys
import time
from typing import NamedTuple

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
DEFAULT_PROVIDER = "agentteams-gateway"
DEFAULT_MODEL = "glm-5.3-flash"
DEFAULT_MAX_TOKENS = 2048
DEFAULT_REASONING_EFFORT = "low"
WORKER_CONTAINER_PREFIX = "agentteams-worker-"
WORKER_PYTHON = "/opt/venv/standard/bin/python"
WORKER_API_PORT = 8088
MANAGER_CONTAINER = "agentteams-manager"
MANAGER_PYTHON = "/opt/copaw-venv/bin/python3"
MANAGER_API_PORT = 18799


class Target(NamedTuple):
    name: str
    container: str
    python: str
    port: int


AGENT_PROBE = r'''
import json
import sys
import urllib.error
import urllib.request

provider_id, model_id, raw_kwargs, raw_port = (
    sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],
)
kwargs = json.loads(raw_kwargs)
port = int(raw_port)


def call(method, path, body=None):
    base = f"http://127.0.0.1:{port}/api/models"
    url = f"{base}/{path}" if path else base
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, {}


status, _ = call(
    "PUT",
    f"{provider_id}/models/{model_id}/config",
    {"generate_kwargs": kwargs},
)
read_status, providers = call("GET", "")
effective = None
if read_status == 200:
    for provider in providers:
        if provider.get("id") != provider_id:
            continue
        for key in ("models", "extra_models"):
            for model in provider.get(key) or []:
                if model.get("id") == model_id and model.get("generate_kwargs"):
                    effective = model["generate_kwargs"]
print(json.dumps({
    "put_status": status,
    "read_status": read_status,
    "effective": effective,
}))
'''


def run_docker(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, **kwargs)  # nosec B603


def apply_to_target(
    target: Target,
    provider: str,
    model: str,
    kwargs: dict,
    timeout: float,
) -> dict:
    state = run_docker(
        ["docker", "inspect", "-f", "{{.State.Running}}", target.container],
        timeout=30,
    )
    if state.returncode != 0:
        return {"target": target.name, "status": "missing", "passed": False}
    if state.stdout.strip() != "true":
        return {"target": target.name, "status": "not-running", "passed": False}

    deadline = time.monotonic() + timeout
    attempt = 0
    while True:
        attempt += 1
        result = run_docker(
            [
                "docker",
                "exec",
                "-i",
                target.container,
                target.python,
                "-",
                provider,
                model,
                json.dumps(kwargs),
                str(target.port),
            ],
            input=AGENT_PROBE,
            timeout=180,
        )
        payload: dict = {}
        if result.returncode == 0 and result.stdout.strip():
            try:
                payload = json.loads(result.stdout.strip().splitlines()[-1])
            except json.JSONDecodeError:
                payload = {}
        if payload.get("read_status") == 200:
            passed = payload.get("effective") == kwargs
            return {
                "target": target.name,
                "status": "verified" if passed else "mismatch",
                "attempt": attempt,
                "effective": payload.get("effective"),
                "passed": passed,
            }
        if time.monotonic() >= deadline:
            return {
                "target": target.name,
                "status": "timeout",
                "attempt": attempt,
                "passed": False,
            }
        time.sleep(5)


def worker_targets(names: list[str]) -> list[Target]:
    targets = []
    for name in names:
        actor = name if name.startswith("revguard-") else f"revguard-{name}"
        targets.append(
            Target(actor, f"{WORKER_CONTAINER_PREFIX}{actor}", WORKER_PYTHON, WORKER_API_PORT)
        )
    return targets


def manager_target() -> Target:
    return Target("manager", MANAGER_CONTAINER, MANAGER_PYTHON, MANAGER_API_PORT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", nargs="*", default=list(WORKERS))
    parser.add_argument(
        "--include-manager",
        action="store_true",
        help="同时应用并验证 agentteams-manager（端口 18799）",
    )
    parser.add_argument(
        "--manager-only",
        action="store_true",
        help="只应用并验证 agentteams-manager",
    )
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--reasoning-effort", default=DEFAULT_REASONING_EFFORT)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    kwargs = {
        "max_tokens": args.max_tokens,
        "reasoning_effort": args.reasoning_effort,
    }
    targets = [] if args.manager_only else worker_targets(args.workers)
    if args.include_manager or args.manager_only:
        targets.append(manager_target())

    failed: list[str] = []
    skipped: list[str] = []
    verified = 0
    for target in targets:
        outcome = apply_to_target(
            target, args.provider, args.model, kwargs, args.timeout
        )
        detail = outcome.get("effective")
        suffix = f" effective={json.dumps(detail, ensure_ascii=False)}" if detail else ""
        print(f"{target.name}: {outcome['status']}{suffix}", flush=True)
        if outcome.get("passed"):
            verified += 1
        elif outcome.get("status") in {"not-running", "missing"}:
            skipped.append(target.name)
        else:
            failed.append(target.name)

    if failed:
        print("模型预算应用失败: " + ", ".join(failed), file=sys.stderr)
        return 1
    print(
        f"已应用并验证 {verified} 个 Agent 的 {args.model} 预算: {kwargs}；"
        f"未运行跳过 {len(skipped)} 个（启动后需重跑本脚本）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
