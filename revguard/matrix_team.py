"""Real AgentTeams/Matrix transport for the deterministic RevGuard workflow.

The RevGuard state machine remains authoritative.  Matrix is the delivery layer:
each persisted StageTask is mentioned to exactly one AgentTeams Worker, and the
workflow advances only after that Worker invokes the server-bound Skill and a
SUCCEEDED StageResult is committed.  Chat prose is presentation evidence, not a
state transition command.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib import error, parse, request

from .agent_bridge import create_agent_task
from .mcp_team import McpTeamRunner
from .models import TaskStatus, new_id, utc_now
from .security import redact_secrets
from .skill_runtime import SKILL_ACTORS
from .trace import Tracer


class MatrixTransportError(RuntimeError):
    """Matrix delivery or AgentTeams Worker completion failed."""


@dataclass(frozen=True)
class MatrixSettings:
    homeserver_url: str
    room_id: str
    server_name: str
    username: str = ""
    password: str = ""
    access_token: str = ""
    worker_rooms: dict[str, str] = field(default_factory=dict)
    stage_timeout_seconds: float = 240.0
    response_timeout_seconds: float = 90.0
    retry_nudge_seconds: tuple[float, ...] = (45.0, 120.0)
    orchestrator_timeout_seconds: float = 120.0
    require_orchestrator_ack: bool = True

    @classmethod
    def from_env(cls) -> MatrixSettings:
        worker_rooms_raw = os.getenv("REVGUARD_MATRIX_WORKER_ROOMS_JSON", "{}")
        try:
            worker_rooms = json.loads(worker_rooms_raw)
        except json.JSONDecodeError as exc:
            raise MatrixTransportError(
                "REVGUARD_MATRIX_WORKER_ROOMS_JSON 不是有效 JSON"
            ) from exc
        if not isinstance(worker_rooms, dict) or not all(
            isinstance(actor, str) and isinstance(room_id, str)
            for actor, room_id in worker_rooms.items()
        ):
            raise MatrixTransportError(
                "REVGUARD_MATRIX_WORKER_ROOMS_JSON 必须是 actor 到 room_id 的对象"
            )
        return cls(
            homeserver_url=os.getenv(
                "REVGUARD_MATRIX_HOMESERVER_URL",
                "http://agentteams-controller:6167",
            ).rstrip("/"),
            room_id=os.getenv("REVGUARD_MATRIX_ROOM_ID", ""),
            server_name=os.getenv(
                "REVGUARD_MATRIX_SERVER_NAME",
                "matrix-local.agentteams.io:8086",
            ),
            username=os.getenv("REVGUARD_MATRIX_USERNAME", ""),
            password=os.getenv("REVGUARD_MATRIX_PASSWORD", ""),
            access_token=os.getenv("REVGUARD_MATRIX_ACCESS_TOKEN", ""),
            worker_rooms=worker_rooms,
            stage_timeout_seconds=float(
                os.getenv("REVGUARD_MATRIX_STAGE_TIMEOUT_SECONDS", "240")
            ),
            response_timeout_seconds=float(
                os.getenv("REVGUARD_MATRIX_RESPONSE_TIMEOUT_SECONDS", "90")
            ),
            orchestrator_timeout_seconds=float(
                os.getenv("REVGUARD_MATRIX_ORCHESTRATOR_TIMEOUT_SECONDS", "120")
            ),
            require_orchestrator_ack=os.getenv(
                "REVGUARD_MATRIX_REQUIRE_ORCHESTRATOR_ACK", "true"
            ).lower() == "true",
        )

    def validate(self) -> None:
        missing = []
        if not self.homeserver_url:
            missing.append("REVGUARD_MATRIX_HOMESERVER_URL")
        if not self.room_id:
            missing.append("REVGUARD_MATRIX_ROOM_ID")
        if not self.access_token and not (self.username and self.password):
            missing.append(
                "REVGUARD_MATRIX_ACCESS_TOKEN or REVGUARD_MATRIX_USERNAME/PASSWORD"
            )
        if missing:
            raise MatrixTransportError("Matrix 配置缺失: " + ", ".join(missing))


class MatrixClient:
    """Small Matrix Client-Server API client with no additional runtime dependency."""

    def __init__(self, settings: MatrixSettings):
        self.settings = settings
        self._access_token = settings.access_token

    async def authenticate(self) -> None:
        self.settings.validate()
        if self._access_token:
            return
        response = await self._request("POST", "/_matrix/client/v3/login", {
            "type": "m.login.password",
            "identifier": {
                "type": "m.id.user",
                "user": self.settings.username,
            },
            "password": self.settings.password,
        }, authenticated=False)
        self._access_token = str(response.get("access_token") or "")
        if not self._access_token:
            raise MatrixTransportError("Matrix 登录未返回 access_token")

    async def cursor(self) -> str:
        response = await self.sync(timeout_ms=0)
        return str(response.get("next_batch") or "")

    async def send_text(
        self,
        body: str,
        *,
        mentions: list[str] | None = None,
        room_id: str | None = None,
    ) -> str:
        txn_id = uuid.uuid4().hex
        room = parse.quote(room_id or self.settings.room_id, safe="")
        payload: dict = {"msgtype": "m.text", "body": body}
        if mentions:
            payload["m.mentions"] = {"user_ids": mentions}
        response = await self._request(
            "PUT",
            f"/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn_id}",
            payload,
        )
        event_id = str(response.get("event_id") or "")
        if not event_id:
            raise MatrixTransportError("Matrix send 未返回 event_id")
        return event_id

    async def sync(self, *, since: str = "", timeout_ms: int = 0) -> dict:
        query = {"timeout": str(max(timeout_ms, 0))}
        if since:
            query["since"] = since
        return await self._request(
            "GET", "/_matrix/client/v3/sync?" + parse.urlencode(query), None,
            timeout=max(timeout_ms / 1000 + 8, 10),
        )

    async def wait_for_event(
        self,
        *,
        since: str,
        predicate: Callable[[dict], bool],
        timeout_seconds: float,
        room_id: str | None = None,
    ) -> dict | None:
        deadline = time.monotonic() + timeout_seconds
        cursor = since
        while time.monotonic() < deadline:
            remaining = max(deadline - time.monotonic(), 0)
            response = await self.sync(
                since=cursor,
                timeout_ms=int(min(remaining, 10.0) * 1000),
            )
            cursor = str(response.get("next_batch") or cursor)
            joined = response.get("rooms", {}).get("join", {})
            room = joined.get(room_id or self.settings.room_id, {})
            events = room.get("timeline", {}).get("events", [])
            for event in events:
                if predicate(event):
                    return event
        return None

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None,
        *,
        authenticated: bool = True,
        timeout: float = 20.0,
    ) -> dict:
        if authenticated:
            await self.authenticate()
        url = f"{self.settings.homeserver_url}{path}"
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["Authorization"] = f"Bearer {self._access_token}"
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def perform() -> dict:
            req = request.Request(url, data=data, method=method, headers=headers)
            try:
                with request.urlopen(req, timeout=timeout) as response:
                    return json.load(response)
            except error.HTTPError as exc:
                try:
                    detail = json.load(exc)
                    code = detail.get("errcode") or f"HTTP_{exc.code}"
                except (ValueError, AttributeError):
                    code = f"HTTP_{exc.code}"
                raise MatrixTransportError(f"Matrix 请求失败: {code}") from exc
            except (OSError, ValueError) as exc:
                raise MatrixTransportError(
                    f"Matrix 请求不可用: {type(exc).__name__}"
                ) from exc

        return await asyncio.to_thread(perform)


class MatrixTeamRunner(McpTeamRunner):
    """Execute the existing workflow through real AgentTeams Matrix Workers."""

    execution_mode = "AGENTTEAMS_MATRIX"
    transport = "agentteams-matrix"
    display_name = "AgentTeams Matrix"
    runner_name = "agentteams-matrix-stage-runner"
    room_evidence = "CAPTURED_FROM_RUNTIME"

    def __init__(self, store, gateway, *, output_dir, report_dir,
                 settings: MatrixSettings | None = None,
                 client: MatrixClient | None = None):
        super().__init__(
            store, gateway, output_dir=output_dir, report_dir=report_dir,
        )
        self.settings = settings or MatrixSettings.from_env()
        self.client = client or MatrixClient(self.settings)
        self.run_id = ""
        self._run_phase = "INVESTIGATION"

    def _mxid(self, actor: str) -> str:
        return f"@{actor}:{self.settings.server_name}"

    def _update_run(self, case: dict, **updates) -> None:
        run = dict(case.get("team_run") or {})
        run.update(updates)
        run["updated_at"] = utc_now()
        case["team_run"] = run
        self.store.save_case(case)

    async def run_to_human_gate(self, case: dict) -> dict:
        self.settings.validate()
        self.run_id = new_id("RUN-AGT")
        self._run_phase = "INVESTIGATION"
        self._update_run(
            case,
            run_id=self.run_id,
            status="STARTING",
            phase=self._run_phase,
            current_stage="OrchestratorHandshake",
            completed_tasks=0,
            total_tasks=8,
            room_id=self.settings.room_id,
            started_at=utc_now(),
            error=None,
        )
        try:
            await self._orchestrator_handshake(case)
            self._update_run(case, status="RUNNING")
            state = await super().run_to_human_gate(case)
        except Exception as exc:
            self._update_run(
                case, status="FAILED", error={
                    "type": type(exc).__name__, "message": str(exc),
                },
            )
            raise
        final_status = (
            "WAITING_HUMAN"
            if case.get("status") == "WAITING_FOR_APPROVAL"
            else "COMPLETED"
        )
        self._update_run(
            case, status=final_status, current_stage=None,
            completed_tasks=len([
                item for item in self.store.list_agent_tasks(case["case_id"])
                if item.get("status") == TaskStatus.SUCCEEDED.value
            ]),
        )
        return state

    async def execute_after_approval(self, case: dict, *, state: dict | None = None) -> dict:
        self.settings.validate()
        self.run_id = (case.get("team_run") or {}).get("run_id") or new_id("RUN-AGT")
        self._run_phase = "EXECUTION"
        completed = len([
            item for item in self.store.list_agent_tasks(case["case_id"])
            if item.get("status") == TaskStatus.SUCCEEDED.value
        ])
        self._update_run(
            case, status="RUNNING", phase=self._run_phase,
            current_stage="PermissionCheckSkill", completed_tasks=completed,
            total_tasks=20, error=None,
        )
        try:
            result = await super().execute_after_approval(case, state=state)
        except Exception as exc:
            self._update_run(
                case, status="FAILED", error={
                    "type": type(exc).__name__, "message": str(exc),
                },
            )
            raise
        self._update_run(
            case, status="COMPLETED", current_stage=None,
            completed_tasks=len([
                item for item in self.store.list_agent_tasks(case["case_id"])
                if item.get("status") == TaskStatus.SUCCEEDED.value
            ]),
        )
        return result

    async def _orchestrator_handshake(self, case: dict) -> None:
        started_at = utc_now()
        started = time.monotonic()
        status = "OK"
        error_text = None
        try:
            await self._orchestrator_handshake_transport(case)
        except Exception as exc:
            status = "ERROR"
            error_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            orchestrator = (case.get("team_run") or {}).get("orchestrator") or {}
            Tracer(self.store, case["case_id"]).record_completed_span(
                "AGENT",
                "AgentTeams.OrchestratorHandshake",
                actor="revguard-orchestrator",
                inputs={
                    "run_id": self.run_id,
                    "transport": self.transport,
                    "room_id": self.settings.room_id,
                },
                outputs={
                    "status": orchestrator.get("status"),
                    "response_event_id": orchestrator.get("response_event_id"),
                },
                status=status,
                error=error_text,
                started_at=started_at,
                ended_at=utc_now(),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def _orchestrator_handshake_transport(self, case: dict) -> None:
        await self.client.authenticate()
        cursor = await self.client.cursor()
        orchestrator = self._mxid("revguard-orchestrator")
        input_summary = {
            "run_id": self.run_id,
            "case_id": case["case_id"],
            "goal": "execute state-bound RevGuard StageTasks through assigned Workers",
            "authority": "state-machine",
        }
        header_id = await self.client.send_text(
            "REVGUARD_CONTROL_INPUT\n" + json.dumps(
                input_summary, ensure_ascii=False, separators=(",", ":"),
            )
        )
        trigger_id = await self.client.send_text(
            f"{orchestrator}\n"
            f"RevGuard 控制面握手。run_id={self.run_id} case_id={case['case_id']}。"
            "请不要执行领域 Skill；仅确认你理解：案件状态只能由服务端状态机推进，"
            "Worker 必须使用绑定 task_id 的 Adapter。"
            f"只回复：RUN_ACCEPTED {self.run_id}",
            mentions=[orchestrator],
        )
        self._update_run(case, orchestrator={
            "actor": "revguard-orchestrator",
            "input": redact_secrets(input_summary),
            "dispatch_event_id": header_id,
            "trigger_event_id": trigger_id,
            "status": "WAITING",
            "output": None,
        })
        event = await self.client.wait_for_event(
            since=cursor,
            timeout_seconds=self.settings.orchestrator_timeout_seconds,
            predicate=lambda item: (
                item.get("sender") == orchestrator
                and self.run_id in str(item.get("content", {}).get("body", ""))
            ),
        )
        if not event:
            self._update_run(case, orchestrator={
                **(case.get("team_run") or {}).get("orchestrator", {}),
                "status": "TIMEOUT",
                "output": None,
            })
            if self.settings.require_orchestrator_ack:
                raise MatrixTransportError("AgentTeams Orchestrator 未在时限内确认控制面握手")
            return
        self._update_run(case, orchestrator={
            **(case.get("team_run") or {}).get("orchestrator", {}),
            "status": "ACKNOWLEDGED",
            "output": {"decision": "RUN_ACCEPTED", "run_id": self.run_id},
            "response_event_id": event.get("event_id"),
        })
        self.store.audit(case["case_id"], "revguard-orchestrator",
                         "AGENTTEAMS_ORCHESTRATOR_ACKNOWLEDGED", {
                             "run_id": self.run_id,
                             "matrix_dispatch_event_id": header_id,
                             "matrix_trigger_event_id": trigger_id,
                             "matrix_response_event_id": event.get("event_id"),
                             "transport": self.transport,
                         })

    async def _invoke(self, case: dict, skill_name: str, skill_input: dict, *,
                      message_id: str | None = None) -> dict:
        actors = SKILL_ACTORS.get(skill_name, frozenset())
        actor = next(iter(actors)) if len(actors) == 1 else ""
        started_at = utc_now()
        started = time.monotonic()
        status = "OK"
        error_text = None
        result = None
        try:
            result = await self._invoke_transport(
                case, skill_name, skill_input, message_id=message_id,
            )
            return result
        except Exception as exc:
            status = "ERROR"
            error_text = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            Tracer(self.store, case["case_id"]).record_completed_span(
                "AGENT",
                f"AgentTeams.{skill_name}",
                actor=actor,
                inputs={
                    "run_id": self.run_id,
                    "skill_name": skill_name,
                    "transport": self.transport,
                },
                outputs={
                    "status": "SUCCEEDED" if result is not None else "FAILED",
                    "worker_room": self.settings.worker_rooms.get(
                        actor, self.settings.room_id,
                    ),
                },
                status=status,
                error=error_text,
                started_at=started_at,
                ended_at=utc_now(),
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def _invoke_transport(
        self,
        case: dict,
        skill_name: str,
        skill_input: dict,
        *,
        message_id: str | None = None,
    ) -> dict:
        del message_id
        task = create_agent_task(case, skill_name, skill_input)
        request_id = new_id("REQ-AGT")
        cursor = await self.client.cursor()
        actor = task["assigned_actor"]
        worker_mxid = self._mxid(actor)
        worker_room_id = self.settings.worker_rooms.get(
            actor, self.settings.room_id,
        )
        envelope = {
            "run_id": self.run_id,
            "case_id": case["case_id"],
            "task_id": task["task_id"],
            "skill_name": skill_name,
            "assigned_actor": actor,
            "request_id": request_id,
            "input": skill_input,
        }
        dispatch_event_id = await self.client.send_text(
            "REVGUARD_STAGE_INPUT\n" + json.dumps(
                envelope, ensure_ascii=False, separators=(",", ":"), default=str,
            ),
            room_id=worker_room_id,
        )
        task.update({
            "request_id": request_id,
            "agentteams_message_id": dispatch_event_id,
            "matrix_dispatch_event_id": dispatch_event_id,
            "matrix_room_id": worker_room_id,
            "transport": self.transport,
            "run_id": self.run_id,
        })
        self.store.save_agent_task(task)
        adapter_command = " ".join(shlex.quote(item) for item in [
            "python3",
            f"/root/.copaw-worker/{actor}/skills/revguard-api/scripts/revguard_call.py",
            "--skill", skill_name,
            "--task-id", task["task_id"],
            "--case-id", case["case_id"],
            "--input", json.dumps(
                skill_input, ensure_ascii=False, separators=(",", ":"), default=str,
            ),
            # Matrix event ids are untrusted random text.  Passing them raw
            # through a Worker shell command can accidentally match CoPaw's
            # command guard (for example an id ending in "-SU").  Hex keeps
            # the argument shell/tool-guard safe; the adapter restores the
            # exact event id before sending the correlation header.
            "--message-id-hex", dispatch_event_id.encode("utf-8").hex(),
            "--request-id", request_id,
        ])
        trigger_body = (
            f"{worker_mxid}\n"
            "执行一个已由 RevGuard 服务端绑定的 StageTask。不要创建 taskflow，不要查看 "
            "shared/tasks，不要读取 Secret，不要向其他 Agent 发消息，也不要只用聊天文字声称完成。"
            "只执行下方 adapter_command 一次；命令成功后立即回复 task_id、success=true、"
            "request_id、skill_receipt。\n"
            f"case_id={case['case_id']}\n"
            f"task_id={task['task_id']}\n"
            f"skill_name={skill_name}\n"
            f"request_id={request_id}\n"
            f"message_id={dispatch_event_id}\n"
            "input=" + json.dumps(
                skill_input, ensure_ascii=False, separators=(",", ":"), default=str,
            ) + "\n"
            "adapter_command=" + adapter_command
        )
        trigger_event_id = await self.client.send_text(
            trigger_body, mentions=[worker_mxid], room_id=worker_room_id,
        )
        # A fast Worker may finish before Matrix returns the trigger event id.
        # Merge into the latest persisted task so a stale PENDING snapshot never
        # overwrites an already committed StageResult.
        latest_task = self.store.get_agent_task(task["task_id"]) or task
        latest_task["matrix_trigger_event_id"] = trigger_event_id
        self.store.save_agent_task(latest_task)
        self.store.audit(case["case_id"], "revguard-orchestrator",
                         "AGENT_TASK_DISPATCHED", {
                             "run_id": self.run_id,
                             "task_id": task["task_id"],
                             "skill": skill_name,
                             "assigned_actor": actor,
                             "case_version": task["case_version"],
                             "request_id": request_id,
                             "agentteams_message_id": dispatch_event_id,
                             "matrix_dispatch_event_id": dispatch_event_id,
                             "matrix_trigger_event_id": trigger_event_id,
                             "matrix_room_id": worker_room_id,
                             "transport": self.transport,
                             "runner": self.runner_name,
                         })
        succeeded = len([
            item for item in self.store.list_agent_tasks(case["case_id"])
            if item.get("status") == TaskStatus.SUCCEEDED.value
        ])
        self._update_run(
            case, status="RUNNING", current_stage=skill_name,
            current_actor=actor, current_task_id=task["task_id"],
            completed_tasks=succeeded,
        )

        deadline = time.monotonic() + self.settings.stage_timeout_seconds
        started_waiting = time.monotonic()
        pending_nudges = list(self.settings.retry_nudge_seconds)
        persisted = task
        while time.monotonic() < deadline:
            persisted = self.store.get_agent_task(task["task_id"]) or task
            if persisted.get("status") in {
                TaskStatus.SUCCEEDED.value,
                TaskStatus.FAILED_FINAL.value,
                TaskStatus.CANCELLED.value,
            }:
                break
            elapsed = time.monotonic() - started_waiting
            if pending_nudges and elapsed >= pending_nudges[0]:
                nudge_after = pending_nudges.pop(0)
                retry_event_id = await self.client.send_text(
                    f"{worker_mxid}\n"
                    f"重试同一 StageTask：task_id={task['task_id']}。"
                    "上次尚未形成服务端 StageResult。不要依赖会话历史，不要规划、不要读文件、"
                    "不要使用 taskflow；只执行下方 adapter_command 一次，成功后立即回复关联标识。\n"
                    "adapter_command=" + adapter_command,
                    mentions=[worker_mxid], room_id=worker_room_id,
                )
                persisted = self.store.get_agent_task(task["task_id"]) or task
                persisted.setdefault("matrix_retry_event_ids", []).append(retry_event_id)
                self.store.save_agent_task(persisted)
                self.store.audit(case["case_id"], "revguard-orchestrator",
                                 "AGENT_TASK_RETRY_NUDGED", {
                                     "task_id": task["task_id"],
                                     "assigned_actor": actor,
                                     "after_seconds": nudge_after,
                                     "matrix_retry_event_id": retry_event_id,
                                     "matrix_room_id": worker_room_id,
                                 })
            await asyncio.sleep(0.8)
        if persisted.get("status") != TaskStatus.SUCCEEDED.value:
            raise MatrixTransportError(
                f"AgentTeams Worker {actor} 未完成 {task['task_id']}: "
                f"{persisted.get('status')}"
            )

        response_event = await self.client.wait_for_event(
            since=cursor,
            timeout_seconds=self.settings.response_timeout_seconds,
            room_id=worker_room_id,
            predicate=lambda item: (
                item.get("sender") == worker_mxid
                and task["task_id"] in str(item.get("content", {}).get("body", ""))
            ),
        )
        if response_event:
            persisted["matrix_response_event_id"] = response_event.get("event_id")
            self.store.save_agent_task(persisted)
            self.store.audit(case["case_id"], actor,
                             "AGENTTEAMS_MATRIX_RESPONSE_CAPTURED", {
                                 "task_id": task["task_id"],
                                 "matrix_response_event_id": response_event.get("event_id"),
                                 "matrix_room_id": worker_room_id,
                                 "transport": self.transport,
                             })
        succeeded += 1
        self._update_run(case, completed_tasks=succeeded)
        return persisted["result"]
