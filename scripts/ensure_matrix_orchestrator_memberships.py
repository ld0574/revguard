#!/usr/bin/env python3
"""Ensure the orchestrator service identity can dispatch to every Worker room.

The human admin account is used only to grant room membership.  All runtime
StageTask messages are then sent with the revguard-orchestrator access token.
No credential or access token is printed.
"""
from __future__ import annotations

import asyncio

from revguard.matrix_team import MatrixClient, MatrixSettings, MatrixTransportError


async def ensure_memberships() -> None:
    settings = MatrixSettings.from_env()
    settings.validate()
    orchestrator = MatrixClient(settings)
    admin = MatrixClient(settings.for_admin_transport())
    await orchestrator.authenticate()
    await admin.authenticate()

    expected_subject = f"@revguard-orchestrator:{settings.server_name}"
    actual_subject = str((await orchestrator.whoami()).get("user_id") or "")
    if actual_subject != expected_subject:
        raise MatrixTransportError(
            "REVGUARD_MATRIX_ACCESS_TOKEN 不属于 revguard-orchestrator"
        )

    required_rooms = set(settings.worker_rooms.values())
    joined = await orchestrator.joined_room_ids()
    for room_id in sorted(required_rooms.difference(joined)):
        await admin.invite_user(room_id, expected_subject)
        await orchestrator.join_room(room_id)

    missing = required_rooms.difference(await orchestrator.joined_room_ids())
    if missing:
        raise MatrixTransportError(
            f"revguard-orchestrator 仍缺少 {len(missing)} 个 Worker 房间成员关系"
        )
    print(f"revguard-orchestrator 已加入 {len(required_rooms)} 个 Worker 房间。")


if __name__ == "__main__":
    asyncio.run(ensure_memberships())
