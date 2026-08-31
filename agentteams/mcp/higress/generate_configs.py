#!/usr/bin/env python3
"""Generate actor-scoped Higress REST-to-MCP server definitions."""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SERVERS = {
    "revguard-intake": ["CaseNormalizeSkill", "EntityResolveSkill"],
    "revguard-evidence": ["EvidenceCollectSkill"],
    "revguard-policy": ["PolicyVersionMatchSkill"],
    "revguard-calculation": ["CommissionCalculateSkill"],
    "revguard-rootcause": ["DifferenceExplainSkill"],
    "revguard-risk": ["RiskClassifySkill", "ApprovalRouteSkill"],
    "revguard-executor": [
        "PermissionCheckSkill", "IdempotencyGuardSkill",
        "AdjustmentDraftSkill", "LedgerAdjustSkill", "LedgerReverseSkill",
    ],
    "revguard-verifier": ["PostActionVerifySkill", "PostRollbackVerifySkill"],
    "revguard-knowledge": ["CaseToDatasetSkill"],
}

ARGUMENTS = """  args:
  - name: caseId
    type: string
    required: true
    description: 服务端已创建的案件编号
  - name: input
    type: object
    required: true
    description: 与当前 Skill 契约一致的输入对象
  - name: messageId
    type: string
    required: true
    description: AgentTeams Matrix 消息编号
  - name: requestId
    type: string
    required: true
    description: 端到端请求编号
  - name: taskId
    type: string
    required: true
    description: 服务端派发且绑定案件版本的 StageTask 编号
"""


def render_server(actor: str, skills: list[str]) -> str:
    parts = [
        "server:\n",
        f"  name: {actor}-mcp-server\n",
        "  config:\n",
        '    accessToken: ""\n\n',
        "tools:\n",
    ]
    for skill in skills:
        parts.extend([
            f"- name: {skill}\n",
            f"  description: 由 {actor} 在服务端任务边界内调用 {skill}\n",
            ARGUMENTS,
            "  requestTemplate:\n",
            f'    url: "http://revguard-api.internal:9000/api/v1/skills/{skill}/invoke"\n',
            "    method: POST\n",
            "    body: |\n",
            "      {\n",
            '        "case_id": "{{.args.caseId}}",\n',
            # GJSON Template already renders an object as raw JSON. Applying
            # Sprig toJson again encodes that JSON as a string on AgentTeams'
            # bundled Higress version and makes FastAPI reject the body (422).
            '        "input": {{.args.input}}\n',
            "      }\n",
            "    headers:\n",
            "    - key: Authorization\n",
            '      value: "Bearer {{.config.accessToken}}"\n',
            "    - key: Content-Type\n",
            '      value: "application/json"\n',
            "    - key: X-AgentTeams-Message-ID\n",
            '      value: "{{.args.messageId}}"\n',
            "    - key: X-Request-ID\n",
            '      value: "{{.args.requestId}}"\n',
            "    - key: X-RevGuard-Task-ID\n",
            '      value: "{{.args.taskId}}"\n',
            "    - key: X-RevGuard-Transport\n",
            '      value: "higress-mcp"\n\n',
        ])
    return "".join(parts).rstrip() + "\n"


def main() -> None:
    for actor, skills in SERVERS.items():
        (ROOT / f"{actor}.yaml").write_text(
            render_server(actor, skills), encoding="utf-8"
        )
    (ROOT / "manifest.json").write_text(
        json.dumps(SERVERS, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
