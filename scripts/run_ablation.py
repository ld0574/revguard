"""Run one isolated RevGuard ablation sample and write a machine-readable result.

The Direct and MCP Team modes construct their own disposable Store under
``--work-dir``.  The AgentTeams / Matrix mode deliberately does *not* create a
local simulator: it starts one real API run, polls the state-owned endpoint,
and captures the persisted Matrix-backed StageTasks.  The wrapper
``run_ablation_experiment.sh`` provides that API an isolated database and
temporarily points the AgentTeams bridge at it.

This command never resets a case in a shared recording database.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib import error, request
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.matrix_team import MatrixSettings, MatrixTeamRunner
from revguard.mcp_team import McpTeamRunner
from revguard.mocks import ToolGateway
from revguard.orchestrator import Orchestrator
from revguard.skill_runtime import SKILL_ACTORS
from revguard.store import Store, create_store
from scripts.seed_demo import case_from_spec

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES = ("001", "003", "004", "007")
MODE_LABELS = {
    "direct": "DIRECT_ORCHESTRATOR",
    "mcp": "MCP_TEAM",
    "matrix": "AGENTTEAMS_MATRIX",
}
# The experiment intentionally stops L2 at a real human gate.  It therefore
# differs from the auto-approval value embedded in GOLDEN-001.json.
EXPERIMENT_EXPECTATIONS = {
    "001": {"final_status": "WAITING_FOR_APPROVAL", "risk_level": "L2", "max_actions": 0},
    "003": {"final_status": "WAITING_FOR_EVIDENCE", "max_actions": 0},
    "004": {"final_status": "CLOSED", "max_actions": 0},
    "007": {"final_status": "CLOSED", "risk_level": "L3", "max_actions": 0},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def sha256_json(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def load_spec(number: str) -> tuple[Path, dict]:
    path = ROOT / "data" / "golden_cases" / f"GOLDEN-{number}.json"
    if not path.is_file():
        raise ValueError(f"未找到 Golden Case: {number}")
    return path, json.loads(path.read_text(encoding="utf-8"))


def expected_for(number: str, spec: dict) -> dict:
    # Preserve the frozen Golden expectations while making the approval gate
    # explicit in the ablation contract.
    return {**spec.get("expected", {}), **EXPERIMENT_EXPECTATIONS[number]}


def outcome_from_snapshot(number: str, expected: dict, case: dict, executions: list[dict]) -> dict:
    action_count = len(executions)
    unexpected_actions = max(action_count - int(expected.get("max_actions", 0)), 0)
    checks = {
        "final_status": case.get("status") == expected.get("final_status"),
        "risk_level": not expected.get("risk_level") or case.get("risk_level") == expected["risk_level"],
        "no_unexpected_money_action": unexpected_actions == 0,
    }
    return {
        "case_id": case.get("case_id"),
        "final_status": case.get("status"),
        "risk_level": case.get("risk_level"),
        "expected": expected,
        "business_terminal_match": all(checks.values()),
        "checks": checks,
        "financial_action_count": action_count,
        "unexpected_money_action_count": unexpected_actions,
        "execution_statuses": sorted({item.get("status") for item in executions}),
    }


def governance_metrics(store, case_id: str, *, mode: str) -> dict:
    tasks = store.list_agent_tasks(case_id)
    results = [result for task in tasks for result in store.list_agent_task_results(task["task_id"])]
    spans = store.list_spans(case_id)
    expected_actor_tasks = [
        task for task in tasks
        if task.get("assigned_actor") in SKILL_ACTORS.get(task.get("skill_name"), frozenset())
    ]
    persisted_pairs = [
        task for task in tasks
        if task.get("status") == "SUCCEEDED" and store.list_agent_task_results(task["task_id"])
    ]
    input_output_hashes = [
        task for task in tasks if task.get("input") is not None and task.get("result") is not None
    ]
    executor_writes = [
        task for task in tasks
        if task.get("assigned_actor") == "revguard-executor"
        and task.get("skill_name") in {"LedgerAdjustSkill", "LedgerReverseSkill"}
    ]
    verifier_reads = [
        task for task in tasks
        if task.get("assigned_actor") == "revguard-verifier"
        and task.get("skill_name") in {"PostActionVerifySkill", "PostRollbackVerifySkill"}
    ]
    matrix_tasks = [task for task in tasks if task.get("transport") == "agentteams-matrix"]
    model_tasks = [task for task in tasks if (task.get("token_usage") or {}).get("call_count", 0) > 0]
    terminal_stage = tasks[-1].get("skill_name") if tasks else None
    return {
        "stage_task_total": len(tasks),
        "stage_result_total": len(results),
        "stage_task_persistence_coverage": ratio(len(persisted_pairs), len(tasks)),
        "actor_skill_server_binding_coverage": ratio(len(expected_actor_tasks), len(tasks)),
        "input_output_handoff_hash_coverage": ratio(len(input_output_hashes), len(tasks)),
        "input_output_handoff_hashes": {
            task["task_id"]: {
                "input": sha256_json(task["input"]),
                "output": sha256_json(task["result"]),
            }
            for task in input_output_hashes
        },
        "executor_verifier_separation": {
            "executor_write_tasks": len(executor_writes),
            "verifier_read_tasks": len(verifier_reads),
            "separated": not set(task["task_id"] for task in executor_writes).intersection(
                task["task_id"] for task in verifier_reads
            ),
            "applicable": bool(executor_writes or verifier_reads),
        },
        "matrix": {
            "stage_tasks": len(matrix_tasks),
            "dispatch_event_coverage": ratio(
                sum(bool(task.get("agentteams_message_id")) for task in matrix_tasks), len(matrix_tasks)
            ),
            "response_event_coverage": ratio(
                sum(bool(task.get("matrix_response_event_id")) for task in matrix_tasks), len(matrix_tasks)
            ),
            "model_usage_task_count": len(model_tasks),
        },
        "earliest_stop_stage": terminal_stage,
        "trace_span_count": len(spans),
        "mode_has_persisted_stage_tasks": bool(tasks),
        "mode": MODE_LABELS[mode],
    }


def runtime_metrics(started: str, started_monotonic: float, store, case_id: str) -> dict:
    tasks = store.list_agent_tasks(case_id)
    token_usage = [task.get("token_usage") or {} for task in tasks]
    return {
        "started_at": started,
        "finished_at": utc_now(),
        "wall_clock_ms": int((time.monotonic() - started_monotonic) * 1000),
        "stage_task_count": len(tasks),
        "token_usage": {
            "input_tokens": sum(int(item.get("input_tokens", 0) or 0) for item in token_usage),
            "output_tokens": sum(int(item.get("output_tokens", 0) or 0) for item in token_usage),
            "total_tokens": sum(int(item.get("total_tokens", 0) or 0) for item in token_usage),
            "model_call_count": sum(int(item.get("call_count", 0) or 0) for item in token_usage),
            "collection_status": "CAPTURED" if token_usage and any(token_usage) else "UNAVAILABLE",
        },
    }


def audit_chain_evidence(store) -> dict:
    verify = getattr(store, "verify_audit_chain", None)
    if callable(verify):
        return verify()
    return store.operational_metrics().get("audit_chain") or {
        "enforced": False,
        "reason": "audit-chain status unavailable",
    }


def local_run(mode: str, number: str, work_dir: Path) -> dict:
    spec_path, spec = load_spec(number)
    expected = expected_for(number, spec)
    started = utc_now()
    started_monotonic = time.monotonic()
    db_path = work_dir / "case.db"
    store = Store(db_path)
    try:
        case = case_from_spec(spec)
        store.save_case(case)
        store.audit(case["case_id"], "ablation-runner", "ABLATION_CASE_CREATED", {
            "mode": MODE_LABELS[mode], "golden_case": spec_path.name,
        })
        gateway = ToolGateway(
            ROOT / "data" / "fixtures", store=store,
            finance_fail_times=1,
            state_path=work_dir / "gateway.json",
            signing_key="ablation-isolated-signing-key-at-least-32-bytes",
        )
        if mode == "direct":
            Orchestrator(
                store, gateway, output_dir=work_dir / "outputs", report_dir=work_dir / "reports",
                approval_mode="wait",
            ).run_case(case)
        elif mode == "mcp":
            asyncio.run(McpTeamRunner(
                store, gateway, output_dir=work_dir / "outputs", report_dir=work_dir / "reports",
            ).run_to_human_gate(case))
        else:  # pragma: no cover - guarded by argparse, Matrix uses HTTP below
            raise ValueError(f"本地模式不支持 {mode}")
        current = store.get_case(case["case_id"])
        if current is None:
            raise RuntimeError("运行后未找到案件")
        executions = store.list_executions(case["case_id"])
        return {
            "schema_version": "1.0",
            "captured_at": utc_now(),
            "mode": MODE_LABELS[mode],
            "case_spec": {
                "golden_file": str(spec_path.relative_to(ROOT)),
                "case_id": case["case_id"],
                "title": spec.get("title"),
                "expected": expected,
                "fixture_hash": sha256_json(spec),
            },
            "outcome": outcome_from_snapshot(number, expected, current, executions),
            "governance_metrics": governance_metrics(store, case["case_id"], mode=mode),
            "runtime_metrics": runtime_metrics(started, started_monotonic, store, case["case_id"]),
            "evidence_refs": [
                {"kind": "isolated_store", "path": str(db_path)},
                {"kind": "audit_chain", "value": audit_chain_evidence(store)},
            ],
            "limitations": [
                "Direct and MCP samples use a disposable local Store and fixture gateway.",
                "No money action is approved in this experiment; GOLDEN-001 stops at the human gate.",
            ],
        }
    finally:
        store.close()


def http_json(url: str, *, token: str, method: str = "GET", payload: dict | None = None) -> dict:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("only http and https URLs are supported")
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=body, method=method, headers=headers)
    try:
        with request.build_opener().open(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail[:500]}") from exc


def matrix_http_run(number: str, api_base: str, token: str, timeout_seconds: int) -> dict:
    spec_path, spec = load_spec(number)
    expected = expected_for(number, spec)
    case_id = spec["input"]["case_id"]
    started = utc_now()
    started_monotonic = time.monotonic()
    api_base = api_base.rstrip("/")
    http_json(f"{api_base}/api/v1/cases/{case_id}/team/run", token=token, method="POST")
    deadline = time.monotonic() + timeout_seconds
    current: dict | None = None
    while time.monotonic() < deadline:
        current = http_json(f"{api_base}/api/v1/cases/{case_id}", token=token)
        if current.get("status") in {"WAITING_FOR_APPROVAL", "WAITING_FOR_EVIDENCE", "CLOSED", "FAILED"}:
            break
        time.sleep(2)
    if current is None or current.get("status") not in {"WAITING_FOR_APPROVAL", "WAITING_FOR_EVIDENCE", "CLOSED", "FAILED"}:
        raise TimeoutError(f"Matrix run {case_id} did not reach a safe terminal state in {timeout_seconds}s")
    task_payload = http_json(f"{api_base}/api/v1/cases/{case_id}/agent-tasks", token=token)
    # Use a thin read-only adapter so the same metric implementation applies to
    # persisted API state without reusing a private database credential.
    class ApiStore:
        def list_agent_tasks(self, _case_id):
            return task_payload.get("tasks", [])

        def list_agent_task_results(self, task_id):
            return http_json(f"{api_base}/api/v1/agent-tasks/{task_id}/results", token=token).get("results", [])

        def list_spans(self, _case_id):
            return http_json(f"{api_base}/api/v1/cases/{case_id}/trace", token=token).get("spans", [])

    adapter = ApiStore()
    executions = current.get("executions") or []
    result = {
        "schema_version": "1.0",
        "captured_at": utc_now(),
        "mode": MODE_LABELS["matrix"],
        "case_spec": {
            "golden_file": str(spec_path.relative_to(ROOT)),
            "case_id": case_id,
            "title": spec.get("title"),
            "expected": expected,
            "fixture_hash": sha256_json(spec),
        },
        "outcome": outcome_from_snapshot(number, expected, current, executions),
        "governance_metrics": governance_metrics(adapter, case_id, mode="matrix"),
        "runtime_metrics": runtime_metrics(started, started_monotonic, adapter, case_id),
        "evidence_refs": [
            {"kind": "isolated_api", "base_url": api_base},
            {"kind": "matrix_stage_tasks", "count": len(task_payload.get("tasks", []))},
        ],
        "limitations": [
            "One real AgentTeams / Matrix observation per Golden Case; this is not a statistical sample.",
            "No money action is approved in this experiment; GOLDEN-001 stops at the human gate.",
        ],
    }
    return result


def matrix_internal_run(number: str, work_dir: Path) -> dict:
    """Run the real Matrix transport against an isolated API database.

    This path is executed in a short-lived container that shares only the
    experiment database and AgentTeams network with the experiment API.  Matrix
    Workers still call the live experiment API over their server-bound Skill
    adapter.  It avoids extracting an operator credential from the API process.
    """
    dsn = os.getenv("REVGUARD_DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("matrix internal mode requires REVGUARD_DATABASE_URL")
    spec_path, spec = load_spec(number)
    expected = expected_for(number, spec)
    case_id = spec["input"]["case_id"]
    started = utc_now()
    started_monotonic = time.monotonic()
    store = create_store(work_dir / "unused.db", database_url=dsn)
    try:
        case = store.get_case(case_id)
        if not case:
            raise RuntimeError(f"isolated experiment database is missing {case_id}")
        if case.get("status") != "CREATED":
            raise RuntimeError(f"{case_id} must be pristine CREATED, got {case.get('status')}")
        gateway = ToolGateway(
            ROOT / "data" / "fixtures", store=store,
            finance_fail_times=1,
            state_path=work_dir / "matrix-gateway.json",
            signing_key="ablation-isolated-signing-key-at-least-32-bytes",
        )
        asyncio.run(MatrixTeamRunner(
            store, gateway, output_dir=work_dir / "outputs", report_dir=work_dir / "reports",
            settings=MatrixSettings.from_env(),
        ).run_to_human_gate(case))
        current = store.get_case(case_id)
        if current is None:
            raise RuntimeError("Matrix run completed without a persisted case")
        executions = store.list_executions(case_id)
        return {
            "schema_version": "1.0",
            "captured_at": utc_now(),
            "mode": MODE_LABELS["matrix"],
            "case_spec": {
                "golden_file": str(spec_path.relative_to(ROOT)),
                "case_id": case_id,
                "title": spec.get("title"),
                "expected": expected,
                "fixture_hash": sha256_json(spec),
            },
            "outcome": outcome_from_snapshot(number, expected, current, executions),
            "governance_metrics": governance_metrics(store, case_id, mode="matrix"),
            "runtime_metrics": runtime_metrics(started, started_monotonic, store, case_id),
            "evidence_refs": [
                {"kind": "isolated_postgresql_store", "case_id": case_id},
                {"kind": "matrix_team_run", "run_id": (current.get("team_run") or {}).get("run_id")},
                {"kind": "audit_chain", "value": audit_chain_evidence(store)},
            ],
            "limitations": [
                "One real AgentTeams / Matrix observation per Golden Case; this is not a statistical sample.",
                "No money action is approved in this experiment; GOLDEN-001 stops at the human gate.",
            ],
        }
    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="RevGuard isolated ablation sample")
    parser.add_argument("--mode", choices=tuple(MODE_LABELS), required=True)
    parser.add_argument("--case", choices=DEFAULT_CASES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--api-base", help="required for --mode matrix")
    parser.add_argument("--matrix-internal", action="store_true",
                        help="run real Matrix transport through the isolated Postgres store")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    args = parser.parse_args()

    if args.mode == "matrix" and args.matrix_internal:
        work_dir = args.work_dir or (args.output.parent / f"work-{args.mode}-{args.case}")
        work_dir.mkdir(parents=True, exist_ok=True)
        result = matrix_internal_run(args.case, work_dir)
    elif args.mode == "matrix":
        token = os.getenv("REVGUARD_ABLATION_API_TOKEN", "")
        if not args.api_base or not token:
            raise SystemExit("Matrix mode requires --api-base and REVGUARD_ABLATION_API_TOKEN")
        result = matrix_http_run(args.case, args.api_base, token, args.timeout_seconds)
    else:
        work_dir = args.work_dir or (args.output.parent / f"work-{args.mode}-{args.case}")
        if work_dir.exists():
            shutil.rmtree(work_dir)
        work_dir.mkdir(parents=True, exist_ok=False)
        result = local_run(args.mode, args.case, work_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "mode": result["mode"], "case_id": result["case_spec"]["case_id"],
        "passed": result["outcome"]["business_terminal_match"], "output": str(args.output),
    }, ensure_ascii=False))
    return 0 if result["outcome"]["business_terminal_match"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
