# AgentTeams → Higress MCP → RevGuard

`--full` 的录制路径是：Matrix 派发 → 职能 Worker → `mcporter tools/call`
→ Higress REST-to-MCP → RevGuard Skill API → PolarDB StageResult。
不是 Worker 持后端 key 直连 REST，也不是把本地 stdio 测试称为 AgentTeams 实链。

## 9 个独立 MCP Server

[`higress/manifest.json`](higress/manifest.json) 是 actor/Skill 对照表，9 份 YAML 合计暴露
16 个 Skill。每个 `mcp-revguard-<role>` 仅授权同名 Worker consumer；Worker 的
`config/mcporter.json` 也只含自己的 Server。Higress 持有后端 Principal，业务 Worker
只持网关 consumer token；Orchestrator 仅保留创建 StageTask 的 dispatcher 凭证。

```bash
# 已安装 AgentTeams、已创建 Worker 的宿主机
bash scripts/setup_higress_mcp_gateway.sh

# 只看到 CaseNormalizeSkill / EntityResolveSkill
docker exec agentteams-worker-revguard-intake \
  mcporter list mcp-revguard-intake --schema --output json
```

总部署入口 `bash scripts/deploy_demo.sh --full` 会自动执行此步骤。官方通用 setup 会给
所有 Worker 授权，因此本项目通过 Console API 直接注册精确 allowlist，再同步 MinIO
和运行容器。兼容旧部署时，只撤销这 9 个 RevGuard Server 上多余的授权，不影响其他服务。
注意 `PUT /v1/mcpServer/consumers` 是**追加**，不是替换；删除授权须使用带 server 与
consumer 列表的 DELETE 请求体。参见 [Higress 官方控制器源码](https://github.com/higress-group/higress-console/blob/main/backend/console/src/main/java/com/alibaba/higress/console/controller/mcp/McpServerController.java)。

部署末尾自动运行 `python3 scripts/verify_higress_isolation.py`：9 次自己的 tools/list
必须成功且工具集合完全相符，72 次跨角色请求必须为 401/403。不能只凭“列表里看不到”
断言隔离有效；404、5xx、网络失败也不能算权限拒绝。

## 权威边界

- 每次调用携带 case、task、request、Matrix message 和原始 input；服务端检查 Skill、
  actor、状态、案件版本、输入快照与幂等约束。
- 唯一推进依据是持久化的 StageResult，不是模型聊天中的“成功”。
- 审批与恢复不属于任何 Worker 的 MCP 工具；只能通过带外页面的 Matrix 身份证明。
- 能力令牌在 task binding 校验后由服务端注入，Worker 只看到 Secret 引用。
- 已发现 MCP 配置时调用失败明确报错，**不回退到 REST**。
- 任务账本保留 `transport=agentteams-matrix` 与 `skill_transport=higress-mcp` 两层证据。

AgentTeams 自带版本的 GJSON Template 已将对象输出为原始 JSON，所以 YAML 使用
`"input": {{.args.input}}`；再套 `toJson` 会变成字符串，导致 HTTP 422。
这是经实际网关调用验证的兼容约束。模板机制参见
[Higress 官方文档](https://higress.ai/docs/ai/mcp-server/)。

## 本地 stdio 仅作可复现测试

`--local` 使用 `McpTeamRunner` 和 [`servers.example.json`](servers.example.json) 的
scoped stdio Server，不需要 AgentTeams/模型，不证明真实 Matrix 或 Higress 接入。

```bash
REVGUARD_MCP_ACTOR=revguard-intake \
REVGUARD_ALLOW_INSECURE_DEMO_KEYS=true \
.venv/bin/python scripts/run_mcp_server.py
```

两条路径共享业务契约、安全校验和持久化逻辑；演示时必须区分证据来源。
