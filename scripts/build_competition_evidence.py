#!/usr/bin/env python3
"""Build a sanitized, reproducible evidence bundle from the MCP Team rehearsal."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.mcp_team import McpTeamRunner
from revguard.mocks import ToolGateway
from revguard.models import Case, CaseStatus
from revguard.security import redact_secrets
from revguard.state_machine import transition_case
from revguard.store import Store

ROOT = Path(__file__).resolve().parent.parent
CASE_ID = "CASE-2026-0008"


def write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(redact_secrets(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def create_case() -> dict:
    spec = json.loads(
        (ROOT / "data" / "golden_cases" / "GOLDEN-008.json")
        .read_text(encoding="utf-8")
    )
    raw = spec["input"]
    return Case(
        case_id=raw["case_id"], case_type=raw["case_type"], source=raw["source"],
        partner_id=raw.get("partner_id"), partner_name=raw.get("partner_name"),
        order_id=raw.get("order_id"), description=raw.get("description", ""),
        claim=raw.get("claim", {}),
        entities={
            "partner_id": raw.get("partner_id"),
            "partner_name": raw.get("partner_name"),
            "order_id": raw.get("order_id"),
            "contract_id": None,
        },
    ).to_dict()


async def build(output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="revguard-evidence-") as temp_name:
        temp = Path(temp_name)
        store = Store(temp / "rehearsal.db")
        try:
            gateway = ToolGateway(
                ROOT / "data" / "fixtures",
                finance_fail_times=1,
                signing_key="competition-evidence-signing-key-at-least-32-bytes",
                state_path=temp / "gateway.json",
                verification_tamper_amount="1",
            )
            runner = McpTeamRunner(
                store, gateway,
                output_dir=temp / "outputs", report_dir=temp / "reports",
            )
            case = create_case()
            store.save_case(case)
            store.audit(case["case_id"], "evidence-rehearsal", "CASE_CREATED", {
                "dataset": "revguard-ke-commission-v1",
                "classification": "SYNTHETIC",
            })
            await runner.run_to_human_gate(case)
            waiting = store.get_case(CASE_ID)
            gate_tasks = store.list_agent_tasks(CASE_ID)
            write_json(output / "01-human-gate.json", {
                "case": waiting,
                "approval": store.get_approval(CASE_ID),
                "task_count": len(gate_tasks),
                "task_statuses": sorted({item["status"] for item in gate_tasks}),
                "evidence_statement": (
                    "This snapshot proves the executable workflow paused at the gate. "
                    "A screen-recorded click is still required as human-interaction evidence."
                ),
            })

            approval = store.get_approval(CASE_ID)
            decided = gateway.call(
                "workflow.decide_approval",
                {"approval_id": approval["approval_id"], "decision": "APPROVED"},
                case_id=CASE_ID, actor="finance.lead", scope=["approval:decide"],
            )["data"]
            store.save_approval({"case_id": CASE_ID, **decided})
            store.audit(CASE_ID, "evidence-rehearsal", "APPROVAL_DECIDED", {
                "decision": decided["status"],
                "simulated_human": True,
                "evidence_note": "Automated rehearsal; not a claim of a human UI click",
            })
            waiting = store.get_case(CASE_ID)
            transition_case(
                store, waiting, CaseStatus.READY_TO_EXECUTE,
                "自动化证据排练批准；正式录屏必须由组员点击",
                actor="evidence-rehearsal",
            )
            final_state = await runner.execute_after_approval(waiting)
            final_case = store.get_case(CASE_ID)
            tasks = store.list_agent_tasks(CASE_ID)
            audits = store.list_audit(CASE_ID)
            for item in audits:
                if item.get("detail"):
                    item["detail"] = json.loads(item["detail"])
            spans = store.list_spans(CASE_ID)
            write_json(output / "02-agent-tasks.json", tasks)
            write_json(output / "03-audit-events.json", audits)
            write_json(output / "04-trace-spans.json", spans)
            write_json(output / "05-final-case.json", final_case)
            report_source = temp / "reports" / f"{CASE_ID}.md"
            (output / "06-audit-report.md").write_text(
                redact_secrets(report_source.read_text(encoding="utf-8")),
                encoding="utf-8",
            )
            provenance = json.loads(
                (ROOT / "data" / "synthetic-provenance.json")
                .read_text(encoding="utf-8")
            )
            write_json(output / "00-data-provenance.json", provenance)

            artifacts = sorted(
                path for path in output.iterdir() if path.name != "manifest.json"
            )
            manifest = {
                "schema_version": "1.0",
                "case_id": CASE_ID,
                "release": "0.4.0",
                "business_data": "SYNTHETIC",
                "workflow": "REAL_EXECUTABLE",
                "transport": "MCP",
                "orchestration": "STATE_DRIVEN",
                "approval_evidence": {
                    "gate_pause": "CAPTURED_IN_01-human-gate.json",
                    "rehearsal_decision": "SCRIPTED_AND_EXPLICITLY_LABELLED",
                    "human_ui_click": "PENDING_RECORDING_CAPTURE"
                },
                "database_evidence": {
                    "local_postgresql": "see docs/polardb-local-verification-2026-08-27.json",
                    "cloud_polardb": "PENDING_DEPLOYMENT_CAPTURE"
                },
                "agentteams_room_evidence": "PENDING_EXTERNAL_CAPTURE",
                "outcome": {
                    "gate_status": "WAITING_FOR_APPROVAL",
                    "final_status": final_case["status"],
                    "verification": final_state["verification"]["verification_status"],
                    "rollback_verification": final_state["rollback"]["verification"]["verification_status"],
                    "task_count": len(tasks),
                    "succeeded_tasks": sum(item["status"] == "SUCCEEDED" for item in tasks),
                    "worker_count": len({item["assigned_actor"] for item in tasks}),
                    "skill_count": len({item["skill_name"] for item in tasks}),
                },
                "security": {
                    "credentials_redacted": True,
                },
                "artifact_sha256": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in artifacts
                },
            }
            manifest["security"]["raw_capability_token_pattern_absent"] = True
            write_json(output / "manifest.json", manifest)
            if "RGC1." in "".join(
                path.read_text(encoding="utf-8") for path in output.iterdir()
            ):
                raise RuntimeError("raw capability token leaked into evidence bundle")
            return manifest
        finally:
            store.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "docs" / "evidence" / "demo-rehearsal",
    )
    args = parser.parse_args()
    manifest = asyncio.run(build(args.output))
    print(json.dumps(manifest["outcome"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
