# AgentTeams 切换 GPT-5.6 Sol（2026-09-12）

已使用用户当前配置的模型网关和凭证，将管理端、Orchestrator 及 9 个职能 Worker 切换为 `gpt-5.6-sol`。所有调用、镜像构建、重建与验证均在 10.10.10.202 Docker 执行。

## 原因与范围

旧上游对 MiniMax-M3 的实际请求返回 HTTP 429、错误码 2056：Token Plan 用量已达上限。日志中同时出现 `MODEL_QUOTA_EXCEEDED`。现有证据指向套餐配额耗尽，并非密钥过期或 Higress 的 Worker 鉴权失败。

| 使用位置 | 原配置 | 当前配置 |
| --- | --- | --- |
| Orchestrator + 9 个职能 Worker | MiniMax-M3，CoPaw OpenAIChatModel | gpt-5.6-sol |
| 管理端 default | moonshotai/kimi-k3 | gpt-5.6-sol |
| RevGuard 部署脚本默认模型 | MiniMax-M3 | gpt-5.6-sol |
| Higress default-ai-route | openai-compat Provider | revguard-sol Provider，使用当前获授权网关 |

Worker 继续用各自的内部网关凭证。上游凭证仅由 Higress 托管，没有下发到业务 Worker、写进仓库或验收记录。旧 Provider 留存供排查；历史证据及录制脚本中的旧模型不做批量替换。

## 接口与重启兼容

当前用户配置采用 Responses，但实际自定义网关也接受 Chat Completions；在 202 容器进行了真实请求验证，响应模型为 `gpt-5.6-sol`。CoPaw 继续使用现有 Chat Completions 和函数工具契约。该模型对这一组合明确设置 `reasoning_effort=none`，避免继承不兼容的默认推理参数；没有改写 SOUL、工具定义、账务逻辑或业务输出协议，也没有添加 Pro、缓存或多 Agent API 特性。

发现 CoPaw Worker 1.0.3 启动时会把 openclaw.json 重新投影为 legacy providers.json，丢掉 API 保存的 per-model kwargs。因此除了调用模型配置 API，还新增 `agentteams/copaw-sol/` 派生镜像补丁，只在生成 `gpt-5.6-sol` 条目时补入上述参数；上游转换代码结构变化时构建会失败，避免静默漏补。

控制器中的 Worker/Manager 资源已持久化模型及镜像：

- `revguard-agentteams-worker:sol-20260912`
- `revguard-agentteams-manager:sol-20260912`

构建入口是 `scripts/build_agentteams_sol_images.sh`，仅在 202 执行。`agentteams_setup.sh` 选用 Sol 时会使用派生 Worker 镜像，同时显式设置运行时参数；无需用全量部署脚本或 reset 来轮换上游凭证。现有控制器环境中的旧默认值不影响这些已显式指定模型的资源；新建资源也应显式指定模型。

## 验证

1. 当前网关直连：Sol 返回合成只读工具调用，接受 tool result 并回答 `MODEL_READY`。
2. 实际 Higress：使用 Worker 自身的内部凭证，完成同样的调用闭环。
3. 派生镜像重建后：11 个容器分别从持久化配置加载真正的 CoPaw 流式模型客户端，全部完成工具参数解析及结果续接，参数均为 `none`。团队 Active、9/9 Ready，管理端 Running。

原始结果及派生镜像 ID 见 [`evidence/finals-acceptance-20260912/agentteams-sol.json`](evidence/finals-acceptance-20260912/agentteams-sol.json)。可复现探针是 `scripts/verify_agentteams_model_runtime.py`，需在相应 CoPaw 容器设置工作目录、秘密目录及 API 端口后执行。探针仅声明合成只读工具，不发 Matrix 消息、不调用财务 Skill。

本次验证覆盖鉴权、明确模型、流式解析、工具调用及续接。没有重跑完整 Matrix 业务案例、测试多模态/长上下文，或据此声称新模型业务质量与旧模型相同。资金与观测的原验收记录保持独立。

官方参考：[GPT-5.6 Sol 模型](https://developers.openai.com/api/docs/models/gpt-5.6-sol)。兼容性判断同时依据已读取的 OpenAI Docs 迁移说明和本次真实 API/CoPaw 验证。
