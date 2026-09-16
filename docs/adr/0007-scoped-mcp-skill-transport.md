# ADR-0007：以 Worker-scoped MCP 暴露 Skill，而非底层 Tool

- 状态：Accepted
- 日期：2026-08-27

## 背景

AgentTeams Worker 需要标准化能力发现与调用，但把 CRM、财务写入、审批和冲销等底层 Tool
直接暴露给模型，会绕开 Case 状态、任务版本、额度和人审约束。只在 HTTP 接口外套一层 MCP
名称也不能证明多 Agent 实际协作。

## 决策

采用官方 MCP Python SDK 的 low-level Server，并把 MCP 定位为受控 Skill transport：

1. 每个 server 进程固定一个 `REVGUARD_MCP_ACTOR`；actor 不进入模型可控参数。
2. `tools/list` 只返回该 actor 被 Skill Registry 允许的 Skill，并直接复用现有 JSON Schema。
3. 每次调用必须绑定服务端已创建的 `case_id + task_id + case_version + exact input`。
4. MCP 与 REST 共用 `execute_agent_task()`，所以状态、重试、StageResult 原子落库与审计语义一致。
5. 底层 ToolGateway 不进入 MCP 工具清单；敏感 Skill 的公开 Task 输入只含服务端引用，
   原始审批/回滚能力令牌在 task binding 校验后才注入执行副本，不持久化到 Task 输入，
   也不返回 Worker/模型。
6. 状态机只接受持久化的 `SUCCEEDED` StageResult，不接受聊天中的完成声明。

## 结果

优势是协议标准化但治理边界不变，Worker 权限可由 `tools/list` 直接检查，MCP task/receipt
也能与 Matrix message ID、Trace 和 Audit 对账。代价是需要为每个 Worker 启动独立配置，
并要求共享持久层才能跨进程保持任务状态。

本地 `McpTeamRunner` 用官方 MCP Client/Server 跑同一契约，属于可执行参考证据；未部署前
不会把它表述为真实 AgentTeams Matrix 房间证据。
