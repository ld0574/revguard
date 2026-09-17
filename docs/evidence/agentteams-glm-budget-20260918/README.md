# AgentTeams glm-5.3-flash 生成预算修复复验（2026-09-18）

## 背景

复赛答辩后复盘发现：`glm-5.3-flash` 是思考模型，若单次 completion 预算过小，
`reasoning_content` 会吃掉全部额度，最终 `content` 为空，Worker 表现为
"没有产生命令"。按决赛口径统一为 **`reasoning_effort: low` + `max_tokens: 2048`**。

## 根因（2026-09-18 复查）

AgentTeams Controller 内置模型注册表对 `glm-5.3-flash` 给出的
`maxTokens` 为 512，且 **Worker 容器重建时 Controller 会按注册表重新生成
provider 默认值**，把 `{max_tokens: 512}`（无 `reasoning_effort`）写回
Worker 的 `.copaw.secret/providers/custom/agentteams-gateway.json`。
只在存储（MinIO）或容器文件里离线改值会被下一次重建覆盖。

## 修复

1. Controller 环境变量 `AGENTTEAMS_MODEL_MAX_TOKENS=2048`
   （202 上以保留全部网络别名、端口与卷的方式重建 Controller 容器；
   `openclaw.json` 的 `maxTokens` 由 512 变为 2048）。
2. 新增 `scripts/apply_agentteams_model_budget.py`：Worker 启动后通过
   CoPaw 官方模型配置 API（`PUT /api/models/{provider}/models/{model}/config`）
   覆盖 `generate_kwargs = {max_tokens: 2048, reasoning_effort: low}`，
   并逐个回读 `effective` 值校验。
3. `scripts/agentteams_setup.sh` 在 Team Ready 后自动执行上述脚本，
   容器重建后部署流程会自动纠正。
4. `REVGUARD_AGENTTEAMS_MAX_COMPLETION_TOKENS` 在 prod 与 dev 均统一为 2048。

## 复验证据

| 文件 | 说明 |
|---|---|
| `controller-model-env.txt` | Controller 生效的模型参数与 RevGuard 上限 |
| `apply-model-budget.txt` | 10/10 Worker 应用并回读校验通过 |
| `worker-model-probe-{orchestrator,intake,evidence,policy}.json` | 真实流式工具调用 + 工具结果续接 + `MODEL_READY` |
| `team-status.txt` | Team `revguard-team` 9/9 Ready |

生产栈（19000）部署后快照与部署前一致：`cases=8`、`audit=261`、
台账 `9|74160.00` 未变；`release=0.6.0rc2`、`ready=true`。

## 已知边界

- AgentTeams Controller 注册表本身仍是 512；RevGuard 通过官方模型配置
  API 在 Worker 启动后覆盖，因此 **Worker 容器每次重建后需重跑
  `scripts/apply_agentteams_model_budget.py`**（已集成到
  `scripts/agentteams_setup.sh` 与部署流程）。
- `reasoning_effort=low` 只降低思考量，不关闭思考；金额、政策、权限与
  资金操作仍由确定性代码决定。
