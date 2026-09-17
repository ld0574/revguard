# AgentTeams GLM 通道迁移（2026-09-17）

根据最新授权配置，AgentTeams 的 Controller、Manager、Orchestrator 与 9 个职能 Worker 已从 `gpt-5.6-luna` 切换到 `glm-5.3-flash`。上游 Base URL 与 API Key 只保存在服务器权限受限的安装环境文件中，不写入仓库、日志或证据包。

Worker 继续访问 AgentTeams 内部 Higress Gateway。外部模型凭据由 Controller 和 Manager 注入网关，不能写入 Worker 的 `.copaw.secret/providers`；该目录保存的是内部网关凭据。每个 Worker 的模型配置固定 `reasoning_effort=low`、`max_tokens=2048`，空闲心跳关闭，金额、政策、权限、状态迁移和资金操作仍由确定性代码决定。

迁移时完成了以下无敏感信息核验：

- 新凭据的 `/models` 鉴权返回 HTTP 200，且列出 `glm-5.3-flash`；
- Controller、Dashboard 和安装环境文件使用同一份新配置；
- Manager 与 10 个 Worker 的 AgentTeams 资源模型均为 `glm-5.3-flash`；
- 10 个业务 Worker 在无任务时全部为 `Sleeping`；
- Matrix、Higress、MinIO、Manager 和 Dashboard 在保留原数据卷后恢复就绪；
- 修正 AgentTeams v1.2.0 遗留的模型路由：Provider 虽已更新，但 DNS Service Source 与 `default-ai-route` 仍指向旧上游；现已对齐到 `openai-compat` 并保留 11 个消费者白名单；
- 真实最小生成请求因上游五小时使用上限返回 429，并明确给出恢复时间 `2026-09-17 18:02:57 +08:00`。因此本次记录不把模型生成链路标为通过，额度恢复后必须重新执行工具调用与结果续接探针。

历史 Luna/Sol 证据保持原样，仅代表其记录日期当时的运行状态。

## 额度恢复后的复验（2026-09-17 20:30 起，202）

上游五小时使用上限在 18:02 之后恢复，20:30 重新执行了无需业务动作的探针，并观察到真实 Worker 调用：

- `GET /models` → HTTP 200，返回 13 个模型且包含 `glm-5.3-flash`；
- `POST /chat/completions`（`max_tokens=16`）→ HTTP 200，`model=glm-5.3-flash`，usage 34 tokens；
- Controller、Dashboard、Manager 注入的 API Key 指纹（sha256 前 12 位 `1b78d38e8b9b`、长度 51）与 AgentTeams 安装环境文件完全一致，`AGENTTEAMS_OPENAI_BASE_URL` 为 `https://www.tokens1688.com/v1`；
- 20:33 与 20:55 两次真实案件运行把 10 个 Worker 重新拉起，CoPaw 侧出现 `CoPawAgent.reply` 与 session 落盘，生成链路已恢复，不再是 429。

因此本文件上一条“额度恢复后必须重新执行工具调用与结果续接探针”的前半部分已完成；工具调用结果续接由真实 StageTask 回执验证（见下）。

## 202 双栈路由与 Worker 凭据（本轮定位并修复）

同一台 202 上有两套互不干扰的 RevGuard 栈，但共用一个 AgentTeams 网络：

| 栈 | 容器 | 端口 | 用途 |
|---|---|---|---|
| prod | `revguard-api` | 19000 | 常驻演示栈 |
| dev | `revguard-api-dev` | 19088 | 0.6.0 研发 / 验收 / 决赛彩排栈 |

Higress 的 REST-to-MCP Server 全部指向 `revguard-api.internal:9000`，Docker DNS 同时只能解析到一个容器，因此**任一时刻只有一套栈能被 Worker 回调**。本轮遇到的两个失败都由这里产生：

1. **404 `Agent task 不存在`**：`revguard-api.internal` 由 prod 容器持有（`docker-compose.agentteams.yml` 的 network alias），dev 运行产生的 StageTask 被 Worker 回调到 prod，prod 自然查不到该任务。表现为 `MatrixTransportError`，且 prod 侧日志没有任何该任务记录。
2. **401 `API key 无效`**：把别名切到 dev 后，Higress 里注册的 Worker 后端 Principal 是部署时生成的服务端密钥，dev 的 `.env` 尚未持有这些密钥。本轮把 Higress 中已注册的 9 个 Worker Principal（只读取指纹后落盘，不打印明文）合并进 dev 的 `REVGUARD_API_KEYS_JSON`，dev 即可校验同一批 Worker 凭据。

修复后的运维入口：`bash scripts/switch_agentteams_api_target.sh {dev|prod|status}`，脚本负责别名归属、解析校验与 release 断言（dev=0.6.0*、prod=0.5*）。每次切换都必须确认目标栈已持有当前 Higress 的 Worker Principal 密钥，否则 Worker 会以 401 结束 StageTask。

边界与代价：prod 容器一旦被 `docker compose up` 重新创建，`docker-compose.agentteams.yml` 里的别名会回到 prod，需要重新执行一次切换脚本；本轮决赛彩排栈为 dev（19088），因此把别名留在 dev，prod 的既有案件与审计记录保持只读不变。

## reasoning_effort 整改与复验（2026-09-17 21:40 起，202）

复核“glm-5.3-flash 是否调用成功”时确认：**HTTP 调用一直是成功的（200、额度正常）**，失败发生在输出预算上——glm-5.3-flash 恒为思考模型，默认长思考会把 `max_tokens` 全部写进 `reasoning_content`，留给 `content` 的预算为 0，Worker 侧表现为“没有产生命令”。

直接对上游 `/v1/chat/completions` 的参数矩阵（`docs/evidence/finals-0.6.0-rc2-20260917/agentteams-glm-parameter-probe.txt`）：

| 请求形态 | 结果 |
|---|---|
| 基线（不传 thinking/reasoning 参数） | HTTP 200，`content` 为空，`reasoning_tokens` 吃满预算 |
| `reasoning_effort=max` / `low` | HTTP 200，均被接受；不报参数错误 |
| `thinking.type=enabled` | HTTP 200，但思考更长，512 预算下 3/3 仍为空 |
| `thinking.type=disabled` | 上游忽略该值，仍返回 `reasoning_content` |

结论：`thinking.type` 不能用于关闭思考，必须用 `reasoning_effort: low` 降低思考量，并同时给足 `max_tokens`。`reasoning_effort=low` 并不稳定地把思考压到 512 以内（重复探针在同一 512 预算下仍出现 3/3 触顶，见 `agentteams-glm-reasoning-low-probe.txt`），因此最终口径是 **low + 2048**。

已实施：

- `scripts/configure_agentteams_persistent_model.py`：glm 分支写入 `{"max_tokens": <limit>, "reasoning_effort": "low"}`，默认上限为 2048；
- `scripts/agentteams_setup.sh`：安装/重建 Worker 用同一口径，AI 网关预检请求同样带 `reasoning_effort: low`；
- `agentteams/copaw-runtime/patch_bridge.py` + `verify_bridge.py`：镜像内置默认改为 `{"max_tokens": 2048, "reasoning_effort": "low"}`；
- `scripts/verify_agentteams_model_runtime.py`：断言 `reasoning_effort == "low"`、`max_tokens == REVGUARD_EXPECTED_MAX_TOKENS`（默认 2048），并继续校验流式工具调用与工具结果续接。

**落盘顺序（关键，首次尝试即踩坑）**：运行中的 Worker 会把自身 `.copaw.secret/providers/custom/agentteams-gateway.json` 反向同步回 MinIO，直接改 MinIO 会被覆盖回旧配置。正确顺序是“先停 Worker → 改 MinIO → 再拉起”，并核对容器内本地文件。

复验结果（证据在 `docs/evidence/finals-0.6.0-rc2-20260917/`）：

- Worker 内部经 Higress 网关的真实调用（`worker-model-probe-intake.json` / `worker-model-probe-policy.json`）：`bounded_value=2048`、`reasoning_effort=low`、流式工具调用 + 工具结果续接 + `MODEL_READY`，8.32 秒；
- 内联 `adapter_command` 压力探针（`worker-adapter-command-probe.txt`）：low + 2048 预算下 4/4 产出完整未截断的单行 JSON，`inputs` 与输入完全一致（278 字符）；
- 残余风险：上游思考长度会波动（同参数下 30–50 秒、思考 500–1000 tokens 均出现过），长内联命令仍有偶发“改以自然语言作答”。因此前文“短命令 + 按 task_id 取回输入”的整改项继续保留。

## dev 栈 Matrix 路径的实测结果与整改项

把别名与 Worker 凭据都对齐之后，19088 彩排栈用 `REVGUARD_TEAM_TRANSPORT=matrix` 真实跑了一次 CASE-2026-0001：

- OrchestratorHandshake 与 CaseNormalizeSkill 两个 StageTask 由真实 Worker 完成，任务账本推进到 3/8；
- 第三个阶段 `PolicyVersionMatchSkill` 在 240 秒 Stage 超时后以 `MatrixTransportError` 结束；
- 房间记录显示原因不是额度、不是鉴权，而是 **`glm-5.3-flash` 无法稳定复现触发器里那段内联 `adapter_command`**：模型先截断 JSON、再自我纠正“上一条命令因我截断 JSON 而未执行成功”，把两三次生成额度耗在转义上，始终没有形成服务端 StageResult。

已做的低成本缓解：曾临时把 Worker 模型 `max_tokens` 提到 4096 并把 `REVGUARD_AGENTTEAMS_MAX_MODEL_CALLS_PER_STAGE` 由 2 提到 3，但根因仍是长内联命令的 JSON 转义。现行配置回到经探针确认的 `reasoning_effort=low` + `max_tokens=2048`，并通过短 `task_id` 命令消除业务 JSON 转义。

建议的下一步整改（新增功能，本轮未实施）：

1. 触发消息只下发短命令，例如 `python3 .../revguard_call.py --task-id TASK-XXXX --from-task`，把 `--input` 从命令行移除；
2. Adapter 改为向 RevGuard 取该 StageTask 已绑定的输入（新增一个 Worker 可读、按 actor 与 task 绑定的只读接口，或复用 `GET /api/v1/cases/{case_id}/agent-tasks`）；
3. 或者保留内联输入但改为 base64 传递（`--input-b64`），彻底消除引号转义。

在整改完成前，19088 彩排栈的双案记录使用 `REVGUARD_TEAM_TRANSPORT=mcp` 参考执行器产生（同一 StageTask / Skill 契约 / 真实 ERPNext / 真实 Matrix 真人审批 / 真实 PostgreSQL 资金写入与冲销），现场只展示已存档记录；Element 里的真实 AgentTeams 多 Agent 交接仍以 19000 常驻栈的运行与决赛视频为准。
