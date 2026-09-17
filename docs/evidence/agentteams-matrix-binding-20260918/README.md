# 真实 AgentTeams Matrix 链路恢复：StageTask 绑定与后端别名唯一性

日期：2026-09-18。全部复现在 10.10.10.202 Docker 内完成。

## 1. 现象

彩排栈（19088，`REVGUARD_TEAM_TRANSPORT=matrix`）启动一个案件后：

- 前两个 StageTask（`CaseNormalizeSkill`、`EntityResolveSkill`）由真实
  `revguard-intake` Worker 在 Matrix 房间中完成并形成服务端 StageResult；
- 第三个 StageTask（`EvidenceCollectSkill`）在 45s / 120s 重试后仍无结果，
  240 秒 Stage 超时后整个 run 以 `MatrixTransportError` 结束。

Worker 每次都按 `failure_requirement` 返回结构化错误并停止推进，没有把失败写成成功。

## 2. 根因（两条，均已定位到具体行为）

### 2.1 短命令 `--from-task` 需要后端凭据，而业务 Worker 按设计不持有凭据

Worker 在房间里返回：

```json
{"success": false, "error": {"type": "ADAPTER_CONFIG", "message": "Bound StageTask lookup failed (ValueError)"}}
```

`agentteams/skills/revguard-api/scripts/revguard_call.py` 的 `--from-task` 分支先用
`_load_api_key()` 读 `/root/.copaw-worker/<worker>/.copaw.secret/revguard_api_key`，
再 `GET /api/v1/agent-tasks/{task_id}`。而
`scripts/setup_higress_mcp_gateway.sh` 的第 4 步会**主动删除** 9 个业务 Worker 的这份
凭据（"业务 Worker 不再持有直连 RevGuard API 的 Key"），只给 Orchestrator 保留。

结果：在"Higress 注入凭据"的现行架构下，**短命令重试永远无法解析自己的绑定输入**；
而长内联 `adapter_command` 又会因为模型转义截断而失败（2026-09-17 已记录）。

### 2.2 两个 RevGuard 栈同时占用 `revguard-api.internal`

同一 Worker 的两次 MCP 调用会落到不同的后端栈：

- `EvidenceCollectSkill` 调用落到 19088 dev API（返回 200/422）；
- `BoundStageTask` 调用落到 19000 prod API（返回 401 `API key 无效`）。

原因是 `docker-compose.agentteams.yml`（19000 常驻栈）与
`docker-compose.finals.yml`（19088 彩排栈）**都**声明了别名
`revguard-api.internal`，Docker DNS 在两个容器之间轮询解析。这会让一次运行的技能
调用在两条栈之间随机分流，属于会污染运行记录的错误配置。

## 3. 修复

| # | 改动 | 文件 |
|---|---|---|
| 1 | 每个 Worker 的 scoped Higress MCP server 增加只读工具 `BoundStageTask`，后端凭据仍只在 Higress 侧注入 | `agentteams/mcp/higress/generate_configs.py` + 9 份 `revguard-*.yaml` |
| 2 | `--from-task` 有凭据走 REST（Orchestrator），无凭据走本 Worker 的 `BoundStageTask` MCP 工具；无 MCP 配置时 fail-closed | `agentteams/skills/revguard-api/scripts/revguard_call.py` |
| 3 | 让 19088 唯一持有 `revguard-api.internal`，19000 释放 | `scripts/switch_agentteams_api_target.sh dev` |
| 4 | MCP 隔离校验的期望工具集纳入 `BoundStageTask` | `scripts/verify_higress_isolation.py` |

工具语义：`BoundStageTask` 只接受 `taskId`，服务端校验
`assigned_actor == principal.actor` 与案件版本后才返回该 StageTask 的绑定输入；
凭据由 Higress 按其 consumer allow-list 注入。

## 4. 复验

| 项 | 命令 / 证据 | 结果 |
|---|---|---|
| 适配器单测 | `python -m unittest tests.test_agentteams_adapter` | 11 项通过（含"无凭据走 MCP 绑定"与"无配置 fail-closed"） |
| MCP 隔离 | `python3 scripts/verify_higress_isolation.py` | 9 个 own-server 探针 200 + 72 项跨角色拒绝 403，每个 server 暴露 `BoundStageTask` |
| 绑定取回 | 业务 Worker 内直接调用 `BoundStageTask` | 返回案件快照校验结果（不再是 401 或 `ADAPTER_CONFIG`） |
| 后端唯一性 | `bash scripts/switch_agentteams_api_target.sh status` | `revguard-api.internal -> 172.29.0.4`，`release 0.6.0-rc2`，owner 仅 `revguard-api-dev` |
| 真实运行 | `run-CASE-82822305.json` | 8 个 StageTask 全部 `SUCCEEDED`，`transport=agentteams-matrix`，逐任务带 Matrix 房间与事件 ID，案件 `CLOSED` |

真实运行中的 8 个 StageTask 分别由 `revguard-intake`（2 次）、`revguard-evidence`、
`revguard-policy`、`revguard-calculation`、`revguard-rootcause`、`revguard-risk`、
`revguard-knowledge` 完成，证据 8 条来自 `erpnext` / `revguard-ledger` / `revguard-policy`。
该案件重算后确认无需付款，因此正常结案且不产生资金操作。

## 5. 残余风险

- **MCP 工具入参大小**：实测约 5.5 KB 的调用可以到达后端，约 16 KB 的调用被
  Higress REST-to-MCP 桥拒绝（`503 upstream connect error ... remote connection
  failure`），阈值未精确测定。`BoundStageTask` 只传 `taskId`，不受影响；大负载
  的 Skill 输入应继续走服务端绑定取回而不是内联下发。
- **别名唯一性是部署约束**：两个栈不能同时持有 `revguard-api.internal`。切换栈后
  必须执行 `scripts/switch_agentteams_api_target.sh status` 确认 owner，否则一次运行
  的技能调用会在两条栈之间随机分流。
- Controller 注册表对 `glm-5.3-flash` 仍写 `maxTokens: 512`，Worker 容器重建后需重跑
  `scripts/apply_agentteams_model_budget.py`（已集成进 `scripts/agentteams_setup.sh`）。
