#!/usr/bin/env python3
"""Persist Matrix settings from the live AgentTeams resource inventory."""
from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
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

TEAM_NAME = "revguard-team"


def container_environment(container: str) -> dict[str, str]:
    # Fixed Docker command, operator-supplied container, no shell expansion.
    output = subprocess.check_output(  # nosec B603, B607
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


def worker_resources(controller: str) -> dict[str, dict]:
    """Read room IDs without waking sleeping Workers or invoking a model."""
    output = subprocess.check_output(  # nosec B603, B607
        ["docker", "exec", controller, "agt", "get", "workers", "-o", "json"],
        text=True,
    )
    payload = json.loads(output)
    resources = {
        str(item.get("name")): item for item in payload.get("workers", [])
    }
    required = {*ACTORS, "revguard-orchestrator"}
    missing = sorted(required.difference(resources))
    if missing:
        raise RuntimeError("AgentTeams Worker 资源缺失: " + ", ".join(missing))
    return resources


def team_resource(controller: str, team_name: str = TEAM_NAME) -> dict:
    """Read the actual Team room, separate from the orchestrator Worker room."""
    output = subprocess.check_output(  # nosec B603, B607
        ["docker", "exec", controller, "agt", "get", "teams", "-o", "json"],
        text=True,
    )
    payload = json.loads(output)
    teams = payload.get("teams", [])
    for item in teams:
        if str(item.get("name") or item.get("teamName") or "") != team_name:
            continue
        room_id = str(item.get("teamRoomID") or "")
        if not room_id:
            raise RuntimeError(f"AgentTeams Team {team_name} 房间缺失")
        return item
    raise RuntimeError(f"AgentTeams Team 不存在: {team_name}")


def runtime_homeserver_url(value: str, controller: str) -> str:
    """Turn a controller-local Matrix URL into a Compose-network URL."""
    parsed = urlsplit(value)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        return value.rstrip("/")
    host = controller
    if parsed.port:
        host += f":{parsed.port}"
    return urlunsplit((parsed.scheme or "http", host, parsed.path, "", "")).rstrip("/")


def collect_runtime(
    prefix: str,
    controller: str,
    team_name: str = TEAM_NAME,
) -> dict[str, str]:
    controller_env = container_environment(controller)
    orchestrator_env = container_environment(
        f"{prefix}revguard-orchestrator"
    )
    resources = worker_resources(controller)
    team = team_resource(controller, team_name)
    required = {
        "AGENTTEAMS_MATRIX_URL": controller_env.get("AGENTTEAMS_MATRIX_URL"),
        "AGENTTEAMS_MATRIX_DOMAIN": controller_env.get("AGENTTEAMS_MATRIX_DOMAIN"),
        "AGENTTEAMS_ADMIN_USER": controller_env.get("AGENTTEAMS_ADMIN_USER"),
        "AGENTTEAMS_ADMIN_PASSWORD": controller_env.get("AGENTTEAMS_ADMIN_PASSWORD"),
        "AGENTTEAMS_TEAM_ROOM_ID": team.get("teamRoomID"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise RuntimeError("AgentTeams Matrix 运行配置缺失: " + ", ".join(missing))
    rooms = {
        actor: str(resources[actor].get("roomID") or "") for actor in ACTORS
    }
    missing_rooms = sorted(actor for actor, room_id in rooms.items() if not room_id)
    if missing_rooms:
        raise RuntimeError("AgentTeams Worker 房间缺失: " + ", ".join(missing_rooms))
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
        # The Team room is the human-visible collaboration room.  The
        # orchestrator Worker room is only for leader/worker protocol traffic.
        "REVGUARD_MATRIX_ROOM_ID": required["AGENTTEAMS_TEAM_ROOM_ID"],
        "REVGUARD_MATRIX_WORKER_ROOMS_JSON": json.dumps(
            rooms, ensure_ascii=False, separators=(",", ":"),
        ),
        "REVGUARD_MATRIX_SERVER_NAME": required["AGENTTEAMS_MATRIX_DOMAIN"],
        "REVGUARD_MATRIX_USERNAME": required["AGENTTEAMS_ADMIN_USER"],
        "REVGUARD_MATRIX_PASSWORD": required["AGENTTEAMS_ADMIN_PASSWORD"],
        # clears cached credential so configured login is used
        "REVGUARD_MATRIX_ACCESS_TOKEN": "",  # nosec B105
        # The orchestrator Worker is already a member of the Team room.  Its
        # token is used only for the human-facing approval request/result;
        # StageTask delivery keeps the admin transport account because it must
        # be a member of every Worker room.
        "REVGUARD_MATRIX_APPROVAL_ACCESS_TOKEN": orchestrator_env.get(
            "AGENTTEAMS_WORKER_MATRIX_TOKEN", ""
        ),  # nosec B105
        "REVGUARD_MATRIX_APPROVAL_ALLOW_UNTHREADED_REPLY": "true",
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
        # A deployment rerun must never replace the operator's exact human
        # allow-list with the default recording admin account.
        if key == "REVGUARD_HITL_MATRIX_USERS_JSON" and line.split("=", 1)[1].strip():
            remaining.pop(key, None)
            updated.append(line)
            continue
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
    parser.add_argument("--team-name", default=TEAM_NAME)
    parser.add_argument("--controller", default="agentteams-controller")
    args = parser.parse_args()
    runtime = collect_runtime(args.container_prefix, args.controller, args.team_name)
    update_env(args.env, runtime)
    print("已配置 Matrix 登录、控制房间与 9 个 Worker 独立房间；.env 权限已设为 0600。")


if __name__ == "__main__":
    main()
