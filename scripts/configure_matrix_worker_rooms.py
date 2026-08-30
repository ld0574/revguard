#!/usr/bin/env python3
"""Persist AgentTeams Matrix runtime settings without exposing credentials."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ACTORS = (
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


def container_environment(container: str) -> dict[str, str]:
    output = subprocess.check_output(
        [
            "docker",
            "inspect",
            "-f",
            "{{range .Config.Env}}{{println .}}{{end}}",
            container,
        ],
        text=True,
    )
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def collect_rooms(prefix: str) -> dict[str, str]:
    rooms = {}
    for actor in ACTORS:
        room_id = container_environment(prefix + actor).get(
            "AGENTTEAMS_WORKER_ROOM_ID"
        )
        if not room_id:
            raise RuntimeError(f"Worker 缺少 AGENTTEAMS_WORKER_ROOM_ID: {actor}")
        rooms[actor] = room_id
    return rooms


def runtime_homeserver_url(value: str, controller: str) -> str:
    """Turn a controller-local Matrix URL into a Compose-network URL."""
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return value.rstrip("/")
    host = controller
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme or "http", host, parsed.path, "", "")).rstrip("/")


def collect_runtime(prefix: str, controller: str) -> dict[str, str]:
    controller_env = container_environment(controller)
    orchestrator_env = container_environment(prefix + "revguard-orchestrator")
    required = {
        "AGENTTEAMS_MATRIX_URL": controller_env.get("AGENTTEAMS_MATRIX_URL"),
        "AGENTTEAMS_MATRIX_DOMAIN": controller_env.get("AGENTTEAMS_MATRIX_DOMAIN"),
        "AGENTTEAMS_ADMIN_USER": controller_env.get("AGENTTEAMS_ADMIN_USER"),
        "AGENTTEAMS_ADMIN_PASSWORD": controller_env.get("AGENTTEAMS_ADMIN_PASSWORD"),
        "AGENTTEAMS_WORKER_ROOM_ID": orchestrator_env.get("AGENTTEAMS_WORKER_ROOM_ID"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("AgentTeams Matrix 运行配置缺失: " + ", ".join(missing))
    rooms = collect_rooms(prefix)
    matrix_username = required["AGENTTEAMS_ADMIN_USER"]
    matrix_subject = (
        matrix_username
        if matrix_username.startswith("@")
        else f"@{matrix_username}:{required['AGENTTEAMS_MATRIX_DOMAIN']}"
    )
    homeserver_url = runtime_homeserver_url(
        required["AGENTTEAMS_MATRIX_URL"], controller,
    )
    return {
        "REVGUARD_TEAM_TRANSPORT": "matrix",
        "REVGUARD_MATRIX_HOMESERVER_URL": homeserver_url,
        "REVGUARD_MATRIX_ROOM_ID": required["AGENTTEAMS_WORKER_ROOM_ID"],
        "REVGUARD_MATRIX_WORKER_ROOMS_JSON": json.dumps(
            rooms, ensure_ascii=False, separators=(",", ":"),
        ),
        "REVGUARD_MATRIX_SERVER_NAME": required["AGENTTEAMS_MATRIX_DOMAIN"],
        "REVGUARD_MATRIX_USERNAME": required["AGENTTEAMS_ADMIN_USER"],
        "REVGUARD_MATRIX_PASSWORD": required["AGENTTEAMS_ADMIN_PASSWORD"],
        "REVGUARD_MATRIX_ACCESS_TOKEN": "",
        "REVGUARD_HITL_MATRIX_HOMESERVER_URL": homeserver_url,
        "REVGUARD_HITL_MATRIX_USERS_JSON": json.dumps({
            matrix_subject: {
                "actor": "finance.lead",
                "display_name": "财务负责人（演示）",
            },
        }, ensure_ascii=False, separators=(",", ":")),
    }


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = []
    remaining = dict(values)
    for line in lines:
        key = line.split("=", 1)[0] if "=" in line else ""
        if key in remaining:
            updated.append(f"{key}={remaining.pop(key)}")
            continue
        updated.append(line)
    updated.extend(f"{key}={value}" for key, value in remaining.items())
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument(
        "--container-prefix", default="agentteams-worker-",
    )
    parser.add_argument("--controller", default="agentteams-controller")
    args = parser.parse_args()
    runtime = collect_runtime(args.container_prefix, args.controller)
    update_env(args.env, runtime)
    print("已配置 Matrix 登录、控制房间与 9 个 Worker 独立房间；.env 权限已设为 0600。")


if __name__ == "__main__":
    main()
