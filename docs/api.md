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
| `REVGUARD_DB_PATH` | `data/revguard.db` | 未配置 PolarDB 时的本地 SQLite Demo 库 |
| `REVGUARD_DATABASE_URL` | 无 | PostgreSQL/PolarDB primary DSN；配置后禁用 SQLite |
| `REVGUARD_READ_DATABASE_URL` | 无 | 可选只读端点；列表、Trace、Metrics 走该连接池 |
| `REVGUARD_AUTO_MIGRATE` | `false` | 生产保持 false；使用独立迁移账号 |
| `REVGUARD_DB_POOL_MIN/MAX` | `1/10` | PolarDB 主/只读连接池上下限 |
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
| `dispatcher` | 按当前 Case 状态派发 StageTask，并查看案件内全量任务 |
| `approver` | 对等待审批的案件作出决定 |
| `worker` | 调用与自身 actor 匹配的 Skill；只能查看分配给自己的任务 |

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
`WAITING_FOR_APPROVAL`，L1 只创建不生效草稿，L3 不执行。调用方可携带
`X-Request-ID`，成功和失败响应均原样返回。意外错误只暴露稳定结构：

```json
{"detail": {"code": "CASE_RUN_FAILED", "request_id": "REQ-..."}}
```

异常类型与脱敏详情只写入 `CASE_RUN_FAILED` Audit，不返回客户端。

### `POST /api/v1/cases/{case_id}/approval`

需要 `approver`。审批人来自 Bearer Principal：

```json
{"decision": "APPROVED", "comment": "证据充分，同意调整"}
```

批准后签发 15 分钟能力令牌，绑定 approval、case、currency、最大 gross 金额、逐组件
`component_quota`、用途和 JTI；Executor 自动续跑写入与独立验证。案件/组件不匹配、
超额、过期或伪造均返回
`AUTH_FAILED`。

驳回不会停在中间状态：服务端重建 Shared State 并生成 Trace、报告和 case memory，路径为
`REJECTED → KNOWLEDGE_ARCHIVED → CLOSED`。批准与驳回响应都经过递归脱敏。

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

成功后 StageTask 状态与 StageResult 在同一个数据库事务中提交为 `SUCCEEDED`，结果和 `skill_receipt` 按 attempt 存档。错 Worker、错
Skill、改动输入、Case 状态/版本变化或重放已完成任务均被拒绝。任务可通过
`GET /api/v1/cases/{case_id}/agent-tasks` 查询；只有 `operator|dispatcher` 能看全量，Worker
只能看分配给自己的任务，纯 `viewer` 无权读取任何任务输入或结果。由于 `case_version`
包含状态和 `updated_at`，任何状态变化都会使先前派发的 pending task 失效，必须重新派发。

任务结果历史使用 `GET /api/v1/agent-tasks/{task_id}/results`。失败任务可由
`operator|dispatcher` 调用 `POST /api/v1/agent-tasks/{task_id}/reassign`，请求体必须说明
`reason`；旧任务变为 `CANCELLED`，新任务保留 `supersedes_task_id` 和新的 case version。

### 只读接口

- `GET /api/v1/cases?limit=50&cursor=...`
- `GET /api/v1/cases/{case_id}`
- `GET /api/v1/cases/{case_id}/trace`
- `GET /api/v1/cases/{case_id}/report`
- `GET /api/v1/ops/metrics`
- `GET /api/v1/ops/metrics/prometheus`
- `GET /api/v1/ops/evidence`

均需要 `viewer`。

`/api/v1/health/live`与 `/api/v1/health/ready` 不需要身份，分别用于进程存活和数据库就绪探测。
`/api/v1/ops/evidence` 将当前 Store/Trace/StageResult 指标、105 场景评测和带数据分类的价值报告组合为录制安全视图；外部环境未验收项始终返回 `PENDING_*`。

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

机器可读快照位于 `docs/openapi.json`：它是 FastAPI OpenAPI 3.1 文档，并附
`x-revguard-skill-registry`。运行 `make openapi` 可重新生成；CI 的 `generated-check`
会拒绝注册表、文档与运行时漂移。

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
清单，且默认返回 410；只有显式设置 `REVGUARD_ENABLE_LEGACY_TOOL_API=true` 才启用，
启动日志会输出醒目警告。启用后也只接受 `revguard-evidence` 身份调用 `*:read`
工具；所有写工具及其它 Worker 一律拒绝。
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
