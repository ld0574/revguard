# AgentTeams 集成说明

RevGuard 以 AgentTeams 为多 Agent 协同基点。本目录提供 1 个 Orchestrator 与 9 个
职能 Worker 的 SOUL；金额、政策、风险和权限仍由 RevGuard 确定性 Skill 执行。

## 两条互补路径

| 路径 | 用途 | 入口 |
|---|---|---|
| 确定性回放 | 评测、Golden Case、故障注入、回归测试 | `make verify && make demo` |
| AgentTeams 协同 | 任务拆解、Worker 协作、人工审批、现场展示 | Element / AgentTeams Team |

两条路径共享同一套 16 个 Skill、ToolGateway、状态机与 Trace 语义。AgentTeams Worker
只通过 `POST /api/v1/skills/{name}/invoke` 调用按身份允许的领域 Skill；底层 Tool 只由
Skill/状态机在服务端调用，不进入 Agent 可见清单。

## 部署

```bash
docker compose up -d --build

REVGUARD_HOME=/absolute/path/to/revguard \
REVGUARD_API_BASE_URL=http://revguard-api:9000 \
bash scripts/agentteams_setup.sh
```

SOUL 使用 `{{REVGUARD_API_BASE_URL}}`，setup 脚本在临时目录渲染后复制到 controller，
并把 `agentteams/skills/revguard-api/` skills-only Adapter 安装到 10 个 Worker 容器。
API key 不能出现在 SOUL、Prompt、聊天或 Trace 中，必须由 AgentTeams Secret/Tool
Adapter 注入 `Authorization: Bearer ...`。

## 协同映射

| 赛道关注 | RevGuard 实现 |
|---|---|
| ≥3 个不同职能 Agent | 1 Manager + 9 Worker，共 10 Agent |
| 任务拆解 | Orchestrator 按 Case 状态机拆成 10 个阶段 |
| 上下文传递 | Shared Case State，传递结构化 Artifact 而非聊天长文本 |
| 并行协作 | Evidence 7 路独立 I/O 真并行；政策查询等待合同依赖 |
| 人工节点 | L2 挂起等待独立 Approver Principal |
| 权限 | 每个 Worker 独立 actor/scopes；请求体不能自报身份 |
| 安全执行 | 签名能力令牌、gross 额度、幂等、前后快照 |
| 结果验证 | Verifier 独立新查询，不使用 Executor 回执自证 |
| 回滚 | 验证失败 → 反向台账 → 回滚后独立验证 |
| 经验沉淀 | Trace、Markdown 报告、Case Memory、Evaluation Dataset |

## 状态机与 Agent 桥接

1. Orchestrator 使用 `dispatcher` Principal 调用
   `POST /api/v1/cases/{case_id}/agent-tasks`；
2. 服务端按当前 Case 状态绑定 `skill_name`、唯一 `assigned_actor`、输入和
   `case_version`，返回 `task_id`；
3. Worker 用自己的 Principal 调 Skill，并携带 `X-RevGuard-Task-ID`；
4. 服务端拒绝错 Worker、错 Skill、输入被改、案件快照过期和已完成任务重放；
5. Skill 成功后 StageResult 与 `skill_receipt` 自动写入 `agent_tasks` 和 Audit。

聊天中的“已完成”不推进状态；只有服务端 `SUCCEEDED` StageTask 才是可验证完成信号。

## Worker 清单

| Worker | 职责 | 资金写权限 |
|---|---|---|
| revguard-orchestrator | 拆解、路由、状态推进 | 无 |
| revguard-intake | 标准化与实体消歧 | 无 |
| revguard-evidence | 并行证据采集 | 无 |
| revguard-policy | 政策版本与等级时点 | 无 |
| revguard-calculation | Decimal 规则复算 | 无 |
| revguard-rootcause | 差异与根因解释 | 无 |
| revguard-risk | 风险分级与审批单 | 仅审批工作流 |
| revguard-executor | 草稿、写入、反向冲销 | 唯一资金写主体 |
| revguard-verifier | 写后/回滚后独立验证 | 无，仅 ledger read |
| revguard-knowledge | 报告、回复草稿、数据集 | 无资金权限 |

## 现场验收

不要以“聊天室里出现最终文字”为闭环标准。必须看到同一个 case_id 的：

1. AgentTeams 任务拆解与 Worker 完成状态；
2. Tool/Skill receipts；
3. HumanApprovalGate 决策；
4. Executor 写入；
5. Verifier PASSED，或 CASE-0008 的 FAILED → ROLLED_BACK；
6. Trace、审计报告与 Case Memory 落盘。

完整部署与复录检查清单见 [`../docs/deployment.md`](../docs/deployment.md)。
