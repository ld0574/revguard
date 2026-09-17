#!/usr/bin/env python3
"""Recreate the AgentTeams manager container from its live inspect state.

The manager container is created by the AgentTeams installer, not by this
repository's Compose files.  When the manager image is upgraded (for example
after rebuilding it with the CoPaw runtime-bridge patch), the container must be
replaced while preserving its network, bind mounts, published console port,
restart policy, log options, entrypoint and environment.

This helper reads ``docker inspect agentteams-manager`` and replays those
settings against the new image.  It is a dry run unless ``--execute`` is given;
on failure it renames the previous container back and starts it again.  Secret
environment values are never printed.

Usage (on the AgentTeams host):

    python3 scripts/recreate_agentteams_manager.py
    python3 scripts/recreate_agentteams_manager.py --execute \
        --image revguard-agentteams-manager:glm-20260918
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

CONTAINER = "agentteams-manager"
DEFAULT_IMAGE = "revguard-agentteams-manager:glm-20260918"
SECRET_ENV_NAMES = (
    "AGENTTEAMS_AUTH_TOKEN",
    "AGENTTEAMS_MANAGER_GATEWAY_KEY",
    "AGENTTEAMS_REGISTRATION_TOKEN",
    "AGENTTEAMS_MANAGER_MATRIX_TOKEN",
    "AGENTTEAMS_ADMIN_PASSWORD",
)


def run_docker(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 - fixed docker CLI calls only
        ["docker", *args], capture_output=True, text=True, check=check
    )


def redact(argument: str) -> str:
    name = argument.split("=", 1)[0]
    if name in SECRET_ENV_NAMES:
        return f"{name}=***"
    return argument


def build_run_command(info: dict, image: str) -> tuple[list[str], int]:
    config = info["Config"]
    host = info["HostConfig"]

    run = [
        "run",
        "-d",
        "--name",
        CONTAINER,
        "--restart",
        (host.get("RestartPolicy") or {}).get("Name") or "no",
        "--network",
        host["NetworkMode"],
    ]
    for bind in host.get("Binds") or []:
        run += ["-v", bind]
    for container_port, bindings in (host.get("PortBindings") or {}).items():
        port = container_port.split("/", 1)[0]
        for binding in bindings:
            host_ip = binding.get("HostIp", "")
            run += ["-p", f"{host_ip}:{binding['HostPort']}:{port}"]
    env = config.get("Env") or []
    for item in env:
        run += ["-e", item]
    for key, value in ((host.get("LogConfig") or {}).get("Config") or {}).items():
        run += ["--log-opt", f"{key}={value}"]
    entrypoint = config.get("Entrypoint") or []
    if entrypoint:
        run += ["--entrypoint", entrypoint[0]]
    if config.get("WorkingDir"):
        run += ["-w", config["WorkingDir"]]
    run.append(image)
    run += list(config.get("Cmd") or [])
    return run, len(env)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image",
        default=os.environ.get("REVGUARD_MANAGER_IMAGE", DEFAULT_IMAGE),
        help=f"目标镜像（默认 {DEFAULT_IMAGE}）",
    )
    parser.add_argument("--execute", action="store_true", help="实际停止并替换容器")
    parser.add_argument(
        "--keep-previous",
        default="agentteams-manager-prev",
        help="替换前重命名的旧容器名",
    )
    args = parser.parse_args()

    if run_docker("inspect", CONTAINER, check=False).returncode != 0:
        print(f"未找到容器 {CONTAINER}", file=sys.stderr)
        return 1
    info = json.loads(run_docker("inspect", CONTAINER).stdout)[0]
    run, env_count = build_run_command(info, args.image)
    preview = " ".join(redact(item) for item in run[:12])
    print(
        f"planned: docker {preview} ... "
        f"env={env_count} binds={len(info['HostConfig'].get('Binds') or [])} "
        f"image={args.image}"
    )
    if not args.execute:
        print("dry-run only; pass --execute to recreate")
        return 0

    previous = args.keep_previous
    if run_docker("inspect", previous, check=False).returncode == 0:
        previous = f"{previous}-{int(time.time())}"
    run_docker("stop", CONTAINER)
    run_docker("rename", CONTAINER, previous)
    result = run_docker(*run, check=False)
    if result.returncode != 0:
        print("新容器启动失败，回滚旧容器:", result.stderr.strip()[:500], file=sys.stderr)
        run_docker("rename", previous, CONTAINER, check=False)
        run_docker("start", CONTAINER, check=False)
        return 1
    time.sleep(5)
    state = run_docker("inspect", "-f", "{{.State.Status}}", CONTAINER).stdout.strip()
    print(
        f"recreated: {result.stdout.strip()[:12]} status={state} previous={previous}"
    )
    return 0 if state == "running" else 1


if __name__ == "__main__":
    raise SystemExit(main())
