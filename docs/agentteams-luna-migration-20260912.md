# AgentTeams Luna 与空闲成本治理（2026-09-12）

根据用户要求，将 Manager、Orchestrator 与 9 个职能 Worker 从 `gpt-5.6-sol` 切换为 `gpt-5.6-luna`。所有构建、运行验证与配置操作均在 10.10.10.202 Docker 完成；本地只编辑、同步与处理 Git。

## 空闲调用来源与处理

管理端 `active_tasks=0`、定时任务列表为空，但默认 Agent 的模型 heartbeat 仍启用，每 30 分钟读取 HEARTBEAT.md 并进入模型巡检。这个心跳与容器健康检查、Prometheus 指标采集不同，会消耗 LLM token。调查时管理端本地累计记录 188 次调用，其中 Sol 143 次、输入 17,374,359 token、输出 21,152 token；这是历史累计记录，不能将全部用量归因于心跳，也不能按它直接推算实际账单或缓存计费。

已通过 CoPaw API 关闭 Manager 心跳，并将启动策略写入派生镜像：每次桥接配置都强制 `heartbeat.enabled=false`，防止 Manager 或 Worker 重建后继承启用心跳的官方模板。人工 Matrix 消息通道保留；不再通过模型进行空闲巡检，服务健康继续交给现有 Prometheus/Grafana。

## 模型与持久化

- 11 个 Controller 资源显式指定 Luna。当前 Manager 使用 `revguard-agentteams-manager:luna-20260912`，Worker 使用 `revguard-agentteams-worker:luna-20260912`。
- 保留现有上游 Provider 和授权凭证，不复制 Manager 凭证给 Worker。Higress 的 `revguard-sol` 是原 Provider 名称，模型随请求指定；没有必要为了名称轮换上游或凭证。
- CoPaw 继续使用 Chat Completions、流式函数工具和 `reasoning_effort=none`。桥接补丁覆盖 Sol/Luna，其他模型条目不添加这一参数。
- 10 个 Worker 的 MinIO `active_model.json` 与自定义 Provider 的 Luna 条目均持久化更新；原内部网关密钥逐项核对保持不变。
- 部署脚本及服务器默认模型改为 Luna。构建入口为 `scripts/build_agentteams_images.sh`；旧 Sol 构建入口保留为兼容包装，历史 Sol 证据不改写。

## 实际容器问题

发现 Controller 更新 Worker 的 image 字段后，唤醒既有休眠容器时可能仍启动旧 Sol 镜像。该容器模型虽已是 Luna，但心跳仍开启，说明仅看 Controller 资源配置不足以证明镜像更新。

先确认没有活动业务，备份全部 Worker 工作区、Controller 资源及 MinIO Provider 文件，再移除已停止的旧容器。保留 Worker/Team 资源、MinIO 内容与数据库，让 Controller 在下一次按需启动时使用指定的新镜像。抽查 Intake 的首次重建和再次唤醒，均确认实际镜像 ID、Luna 激活模型、`none` 参数与关闭的心跳；完成后恢复 Sleeping。其余 Worker 维持 Sleeping，未为验证而触发业务或模型请求。

## 验证与边界

派生镜像构建内，直接对实际安装的 CoPaw bridge 做隔离检查，覆盖 Manager/Worker 首次启动、已有配置重启、Luna/Sol 参数、其他模型不受影响、Matrix 通道保留及关闭心跳。Worker 的 lite/standard 两套运行时与 Manager 运行时均通过。

只在 Manager 实际 CoPaw 客户端发出两次合成短请求，每次最大输出 128 token：一次流式只读工具调用，一次提交工具结果并要求回答 `MODEL_READY`。两次均成功。未发送 Matrix 消息、未调用财务 Skill、未重跑完整比赛案例，不把协议兼容性等同于模型业务质量一致。

OpenAI Docs 的在线 Luna 页面在两个官方域名均返回 403；参数兼容判断使用随附 GPT-5.6 迁移说明及当前获授权网关的实际验证，没有据此编造价格或优惠。

管理端关闭心跳后的多次观察中，累计调用数保持 188；该运行计数不包含直接调用 CoPaw 客户端的两次合成探针。全体 Worker 最终为 Sleeping，Manager 接收人工请求，定时任务列表为空。原 8 个案件、审批/验证/证据/任务、执行和账务哈希不变，9 条 ledger 与 261 行有效审计链完整保留。API 继续为 0.5.10，未重建业务 API 或重置演示数据。

证据见 [agentteams-luna-20260912](evidence/agentteams-luna-20260912/README.md)。私密备份位于 202 的 `/root/revguard-backups/agentteams-luna-20260912`，不进入 Git。
