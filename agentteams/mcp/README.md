# AgentTeams Worker 的 scoped MCP 接入

RevGuard 为每个 Worker 启动一个独立的 MCP stdio 进程。`REVGUARD_MCP_ACTOR` 由部署配置
绑定，不能由模型在参数中自报。MCP `tools/list` 只返回该 Worker 被允许使用的 Skill；
即使客户端手写未授权的 `tools/call`，服务端仍会拒绝。

## 连接原则

- 生产/联调使用共享 `REVGUARD_DATABASE_URL`，让 AgentTeams 与 Web 驾驶舱看到同一批
  Case、StageTask、StageResult 和 Audit；SQLite 仅适合单机录制。
- 为不同 Worker 建不同 server 配置，唯一差异是 `REVGUARD_MCP_ACTOR`；签名密钥和
  数据库凭据由 Secret 注入，不能出现在 SOUL、Matrix 消息或录屏里。
- Orchestrator 先创建绑定案件版本、Skill、Worker 和输入快照的 StageTask；Worker 调用
  MCP Skill 时必须回传 `case_id`、`task_id`、原始 `input` 和 Matrix message ID。
- 只有服务端持久化的 `SUCCEEDED` StageResult 才允许推进 Case 状态。聊天文本没有状态机权限。
- LedgerAdjust/Reverse 等敏感 Skill 的模型可见参数只含服务端 Secret 引用；原始能力令牌
  在 task binding 通过后才注入临时执行副本，模型手写原始令牌会被拒绝。

## 配置

复制 [`servers.example.json`](servers.example.json)，把绝对路径与 Secret 环境变量替换为
实际部署值。示例只展示 Intake；其余 Worker 按相同方式创建进程，actor 与
[`../README.md`](../README.md) 的 Worker 清单一致。

本地协议自测：

```bash
REVGUARD_MCP_ACTOR=revguard-intake \
REVGUARD_ALLOW_INSECURE_DEMO_KEYS=true \
.venv/bin/python scripts/run_mcp_server.py
```

该命令使用标准 stdio transport，通常由 MCP Host 启动，不是给人手工交互的 CLI。

## 证据边界

仓库中的 `McpTeamRunner` 是与 AgentTeams StageTask 契约一致的可执行参考编排，可证明
多 Worker、MCP、暂停、审批后续跑、失败恢复和证据落盘。真正 Matrix 房间的 message ID、
Worker 完成截图仍需在部署后单独采集，不能由本地 harness 冒充。
