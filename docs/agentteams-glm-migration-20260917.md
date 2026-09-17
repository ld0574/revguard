# AgentTeams GLM 通道迁移（2026-09-17）

根据最新授权配置，AgentTeams 的 Controller、Manager、Orchestrator 与 9 个职能 Worker 已从 `gpt-5.6-luna` 切换到 `glm-5.3-flash`。上游 Base URL 与 API Key 只保存在服务器权限受限的安装环境文件中，不写入仓库、日志或证据包。

Worker 继续访问 AgentTeams 内部 Higress Gateway。外部模型凭据由 Controller 和 Manager 注入网关，不能写入 Worker 的 `.copaw.secret/providers`；该目录保存的是内部网关凭据。每个 Worker 的模型配置固定 `max_tokens=512`，空闲心跳关闭，金额、政策、权限、状态迁移和资金操作仍由确定性代码决定。

迁移时完成了以下无敏感信息核验：

- 新凭据的 `/models` 鉴权返回 HTTP 200，且列出 `glm-5.3-flash`；
- Controller、Dashboard 和安装环境文件使用同一份新配置；
- Manager 与 10 个 Worker 的 AgentTeams 资源模型均为 `glm-5.3-flash`；
- 10 个业务 Worker 在无任务时全部为 `Sleeping`；
- Matrix、Higress、MinIO、Manager 和 Dashboard 在保留原数据卷后恢复就绪；
- 修正 AgentTeams v1.2.0 遗留的模型路由：Provider 虽已更新，但 DNS Service Source 与 `default-ai-route` 仍指向旧上游；现已对齐到 `openai-compat` 并保留 11 个消费者白名单；
- 真实最小生成请求因上游五小时使用上限返回 429，并明确给出恢复时间 `2026-09-17 18:02:57 +08:00`。因此本次记录不把模型生成链路标为通过，额度恢复后必须重新执行工具调用与结果续接探针。

历史 Luna/Sol 证据保持原样，仅代表其记录日期当时的运行状态。
