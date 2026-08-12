# RevGuard API 参考

实现位于 `revguard/api.py`。除健康检查外，所有端点都要求 Bearer API key。
身份、角色和 scopes 由服务端 `REVGUARD_API_KEYS_JSON` 映射，请求体不能自报身份。

## 启动

```bash
make run
# Swagger UI: http://127.0.0.1:9000/docs
```

`make run` 显式启用本地 Demo keys。生产环境必须配置 `.env.example` 中的签名密钥和
Principal 映射；缺失时 API fail-closed，不会退回匿名模式。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `REVGUARD_DB_PATH` | `data/revguard.db` | SQLite 案件库 |
| `REVGUARD_GATEWAY_STATE_PATH` | DB 同目录 `.gateway.json` | Mock 台账、审批、幂等与回执 |
| `REVGUARD_FIXTURES_DIR` | `data/fixtures` | 合成 Fixture |
| `REVGUARD_OUTPUT_DIR` | `data/outputs` | Trace / Case Memory |
| `REVGUARD_REPORT_DIR` | `docs/reports` | 审计报告 |
| `REVGUARD_APPROVAL_MODE` | `wait` | `wait` 或仅限离线 Demo 的 `auto` |
| `REVGUARD_VERIFICATION_TAMPER_AMOUNT` | `0` | 故障演练开关；非零时仅偏移 Verifier 首次读取，验证失败后必须自动反向冲销，不改真实台账 |
| `REVGUARD_APPROVAL_SIGNING_KEY` | 无 | 至少 32 字节；缺失时拒绝启动 |
| `REVGUARD_API_KEYS_JSON` | 无 | API key → actor/roles/scopes |
| `REVGUARD_ALLOW_INSECURE_DEMO_KEYS` | `false` | 仅本地评测可设为 `true` |
| `REVGUARD_ENABLE_LEGACY_TOOL_API` | `false` | 仅复放旧 `/tools/call` 证据链时显式开启 |
| `REVGUARD_DEMO_PRINCIPALS_PATH` | `config/demo_principals.json` | 显式启用 Demo 模式时读取；生产不要启用 |

## 身份与角色

```http
Authorization: Bearer <api-key>
```

| 角色 | 允许动作 |
|---|---|
| `viewer` | 案件、报告、Trace、Skill Catalog 只读 |
| `operator` | 创建并运行案件 |
| `dispatcher` | 按当前 Case 状态派发绑定 Skill/Worker/case version 的 StageTask |
| `approver` | 对等待审批的案件作出决定 |
| `worker` | 调用与自身 actor 匹配的 Skill；底层 Tool 不进入 Agent 可见清单 |

Gateway 再次校验：工具所需 scope 必须同时存在于请求 Principal 与 actor 固有权限中。
即使调用者发送额外 JSON 字段 `actor` 或 `scope`，Pydantic 也会以 422 拒绝。

## 案件接口

### `POST /api/v1/cases`

需要 `operator`：

```json
{
  "source": "MANUAL",
  "case_type": "COMMISSION_UNDERPAYMENT",
  "partner_name": "Mombasa Coastal Distributors",
  "order_id": null,
  "description": "代理商申诉佣金少算",
  "claim": {"actual_amount": 18000, "expected_amount": 27000, "currency": "KES"}
}
```

### `POST /api/v1/cases/{case_id}/run`

需要 `operator`。仅 `CREATED` 可运行；L2 在 `wait` 模式停在
`WAITING_FOR_APPROVAL`，L1 只创建不生效草稿，L3 不执行。

### `POST /api/v1/cases/{case_id}/approval`

需要 `approver`。审批人来自 Bearer Principal：

```json
{"decision": "APPROVED", "comment": "证据充分，同意调整"}
```

批准后签发 15 分钟能力令牌，绑定 approval、case、currency、最大 gross 金额、逐组件
`component_quota`、用途和 JTI；Executor 自动续跑写入与独立验证。案件/组件不匹配、
超额、过期或伪造均返回
`AUTH_FAILED`。

### `POST /api/v1/cases/{case_id}/evidence/resume`

需要 `operator`，且案件必须处于 `WAITING_FOR_EVIDENCE`。可补充
`partner_id`、`partner_name`、`order_id`、`contract_id` 或 `claim`；服务端记录
`EVIDENCE_SUPPLIED` 后从合法的 `WAITING_FOR_EVIDENCE → NORMALIZING` 迁移重新运行。

```json
{"order_id": "EZ202607101"}
```

### Agent StageTask 桥接

Orchestrator 使用 `dispatcher` Principal 派发：

```http
POST /api/v1/cases/CASE-2026-0001/agent-tasks
X-AgentTeams-Message-ID: $matrix-event-id
X-Request-ID: REQ-DISPATCH-001
```

```json
{
  "skill_name": "CaseNormalizeSkill",
  "input": {"raw_case": {"partner_id": "AGT-10001", "order_id": "EZ202608001"}}
}
```

服务端返回 `task_id`、`assigned_actor`、`case_status`、`case_version` 和 `PENDING`。
Worker 必须使用完全相同的输入调用 Skill，并增加：

```http
X-RevGuard-Task-ID: TASK-...
```

成功后任务原子变为 `SUCCEEDED`，结果和 `skill_receipt` 存入 StageResult。错 Worker、错
Skill、改动输入、Case 状态/版本变化或重放已完成任务均被拒绝。任务可通过
`GET /api/v1/cases/{case_id}/agent-tasks` 查询；Worker 只能看分配给自己的任务。

### 只读接口

- `GET /api/v1/cases?limit=50&cursor=...`
- `GET /api/v1/cases/{case_id}`
- `GET /api/v1/cases/{case_id}/trace`
- `GET /api/v1/cases/{case_id}/report`

均需要 `viewer`。

案件列表使用 `(updated_at, case_id)` keyset 分页，返回：

```json
{"cases": [], "next_cursor": null, "limit": 50}
```

`limit` 范围为 1–200；继续请求时原样传回 `next_cursor`，无效 cursor 返回 400。

## Skill 运行时

### `GET /api/v1/skills`

返回 16 个 Skill 的版本、标准 `input_schema` / `output_schema`、MCP-compatible annotations、
依赖、失败处理、安全边界、复用场景、允许 actor 及调用入口。Catalog、运行时校验与
`docs/skills.md` 同源。

### `POST /api/v1/skills/{skill_name}/invoke`

需要 `worker`，并校验 Skill 与 actor 的绑定。例如 Intake 调用标准化：

```json
{
  "case_id": "CASE-SKILL-001",
  "input": {
    "raw_case": {"partner_id": "AGT-10001", "order_id": "EZ202608001"}
  }
}
```

返回：

```json
{
  "success": true,
  "data": {"entities": {}, "missing_fields": [], "claim": {}},
  "error": null,
  "skill_receipt": "SKR-...",
  "skill": "CaseNormalizeSkill",
  "version": "1.0.0"
}
```

输入调用前、输出返回前均执行 JSON Schema 校验。每次调用写入 Skill span 与
`SKILL_INVOKED` 审计事件；AgentTeams 调用可携带 `X-AgentTeams-Message-ID`、
`X-Request-ID`、`traceparent` 和 `X-RevGuard-Task-ID`，响应头返回
`X-Request-ID` 与 `X-Skill-Receipt`。

## 服务端 / 遗留 Tool Adapter

### `POST /api/v1/tools/call`

该端点为内部状态机和已有证据链保留兼容，不进入新版 Agent skills-only Adapter 的可见
清单，且默认返回 410；只有显式设置 `REVGUARD_ENABLE_LEGACY_TOOL_API=true` 才启用。
身份和 scope 不在请求体中；遗留跨 AgentTeams 调用还应携带：

```http
X-AgentTeams-Message-ID: $matrix-event-id
X-Request-ID: REQ-...
traceparent: 00-...   # 可选，透传 W3C 上下文
```

请求示例：

```json
{
  "tool_name": "crm.get_partner",
  "parameters": {"partner_id": "AGT-10001"},
  "case_id": "CASE-2026-0001",
  "idempotency_key": null
}
```

响应头返回同一个 `X-Request-ID` 与服务端 `X-Tool-Receipt`。当 `case_id` 存在时，服务端
额外写入 `REMOTE_TOOL` span 与 `AGENTTEAMS_TOOL_CALLED` 审计事件；两者包含 message ID、
request ID、receipt 和可选 traceparent，用于从 Matrix 事件反查完整调用。

返回统一信封：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "source_timestamp": "2026-08-09T00:00:00.000000Z",
  "tool_receipt": "RCPT-..."
}
```

结构化错误类型包括 `AUTH_FAILED`、`NOT_FOUND`、`DATA_CONFLICT`、
`IDEMPOTENCY_CONFLICT`、`INVALID_PARAMS`、`TOOL_UNAVAILABLE`。

## 健康检查

`GET /api/v1/health` 不要求认证，只返回服务状态与案件数，供容器编排健康探针使用。
