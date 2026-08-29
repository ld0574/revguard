#!/usr/bin/env python3
"""Persist AgentTeams Worker DM room IDs without exposing Matrix credentials."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

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


def update_env(path: Path, rooms: dict[str, str]) -> None:
    key = "REVGUARD_MATRIX_WORKER_ROOMS_JSON"
    value = json.dumps(rooms, ensure_ascii=False, separators=(",", ":"))
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = []
    replaced = False
    for line in lines:
        if line.startswith(key + "="):
            updated.append(f"{key}={value}")
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        updated.append(f"{key}={value}")
    path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument(
        "--container-prefix", default="agentteams-worker-",
    )
    args = parser.parse_args()
    rooms = collect_rooms(args.container_prefix)
    update_env(args.env, rooms)
    print(f"已配置 {len(rooms)} 个 Worker 独立 Matrix 房间；.env 权限已设为 0600。")


if __name__ == "__main__":
    main()
