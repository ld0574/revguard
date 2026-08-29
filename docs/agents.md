# RevGuard Agent Identity 清单

> 对齐《Agent Infra 多 Agent 协同开发手册》附录 A 的 8 字段身份模型
> （Name / Role / Capabilities / Inputs / Outputs / Dependencies / Decision Boundary / Trace）。
> 每个 Agent 的完整 SOUL 定义见 `agentteams/workers/revguard-*.md`，本文档为登记总表。

## 协同链路

```
申诉文本
   │
   ▼
revguard-intake ──► revguard-evidence ──► revguard-policy ──► revguard-calculation
   │ （实体消歧）      （7 路 I/O 真并行）      （版本 Time Travel）   （规则引擎复算）
   │                                                              │
   ▼                                                              ▼
revguard-orchestrator ◄── revguard-knowledge ◄── revguard-verifier ◄── revguard-executor
   ▲  （状态机编排）         （沉淀+报告）          （独立复核）          ▲ （受控写操作）
   │                                                              │
   └────────────── revguard-rootcause ──► revguard-risk ──► HumanApprovalGate
                      （差异归因）          （L0-L3 分级）   （L2 审批；L3 转人工）
```

编排原则：`revguard-orchestrator` 只确认控制面并派发结构化 StageTask；
`revguard/orchestrator.py` 是完整业务闭环的服务端确定性参考实现。录制服务器使用真实
AgentTeams/Matrix：Orchestrator 在 Team room 握手，9 个 Worker 在各自独立 room 调用
skills-only Adapter；只有服务端 `SUCCEEDED` StageResult 能推进状态机。

## 登记总表

| # | Name | Role | Inputs | Outputs | Dependencies | Decision Boundary |
|---|------|------|--------|---------|--------------|-------------------|
| 1 | revguard-orchestrator | 任务拆解、Agent 调度、状态流转、冲突与升级裁决 | Case、各 Agent Artifact、审批结果 | Task Plan、State Transition、Escalation Decision | 全体 Worker；HumanApprovalGate | 只读链路自主推进；任何写操作必经 Risk + 人工审批；不可恢复异常 → FAILED 并保留现场 |
| 2 | revguard-intake | 申诉解析与实体消歧，标准化为可调查案件 | 原始申诉文本、case_id | entities + claim + missing_fields | `crm.get_partner` / `crm.list_orders_by_partner` | 实体唯一 → 移交 Evidence；候选不唯一/关键字段缺失 → 输出 Evidence Gap 升级，**不得猜** |
| 3 | revguard-evidence | 跨系统真实并行采集证据，构建 Evidence Package | partner、order_id、case_id | evidence[]（含 receipt/hash）、evidence_score、parallel metrics、gaps | 7 个独立 I/O 工具并行；政策查询依赖合同结果 | score ≥ 0.6 → 移交 Policy；< 0.6 → 挂起补证升级；只读权限，不得脑补数据 |
| 4 | revguard-policy | 政策版本 Time Travel 与订单时点等级回溯 | policy_versions、order/contract 关键日期、tier_history | 选中版本 + 排除版本及原因 + tier_resolution + confidence | `policy.search_versions` / `policy.get_rule_dsl` / `contract.get_effective_terms` | 无生效版本 → 报错升级，不得就近选用；冲突必须显式输出，不得静默消解 |
| 5 | revguard-calculation | 组装 facts 并调用确定性规则引擎复算 | effective_rule_set、证据中的金额/日期/等级 | components（逐项金额+代入式）、calculation_hash、facts_snapshot | CommissionCalculateSkill | **禁止心算/估算**（ADR-001）；规则缺失返回明确错误，不降级 |
| 6 | revguard-rootcause | 应有 vs 实有逐项对账，输出确定性根因分类 | calculation_result、台账条目、policy_decision | diffs[]（含 root_cause + explanation）、total_delta | DifferenceExplainSkill | 归因不出 → AMOUNT_MISMATCH 强制人工，不得编造；不承诺处理决策 |
| 7 | revguard-risk | L0-L3 风险分级与审批路由 | root_cause_report、evidence_score、policy_conflicts | risk_level、approval_required、approver_role、审批单 | RiskClassifySkill / ApprovalRouteSkill；`workflow.create_approval` | 负向调整/超阈值/冲突未消解/证据不足强制升级；不得代替审批人批准 |
| 8 | revguard-executor | **唯一**持有资金写 scope 的受控执行 Agent | L1 Draft Plan 或已审批 L2 Action Plan | action_id、快照、签名 rollback token、reversal | Draft/Adjust/Reverse 三个 Skill | Draft 不改台账；仅 Adjust/Reverse 改台账；L2 必须签名审批凭证；L3 禁止执行；不得自行宣布成功 |
| 9 | revguard-verifier | 写后与回滚后独立重新查询源系统复核 | order_id、expected components/snapshot | verification、rollback verification | PostActionVerifySkill / PostRollbackVerifySkill | 不复用 Executor 回执；任一偏差触发真实反向冲销，回滚仍失败则 FAILED |
| 10 | revguard-knowledge | 沉淀 Case Memory、生成评测样本与回复草稿 | 完整案件轨迹 | GOLDEN/BAD/SAFE_ROLLBACK 样本、审计报告、回复草稿 | `mail.create_reply_draft` / `ticket.update_case` | 只生成草稿不直接发送；保留 ROLLED_BACK/FAILED 终态；沉淀动作入审计 |

## 已采纳 ADR

1. 确定性金额内核；
2. Executor / Verifier 分离；
3. ToolGateway 统一授权与回执契约；
4. 19 状态、24 条普通迁移的显式白名单；
5. 业务绑定、短期、一次性的能力令牌；
6. Skill Registry / JSON Schema 单一事实源。

决策全文见 [`adr/`](adr/README.md)。当前 Demo 的 Normalize 和 Explain 也是确定性实现，
不将模型路线图写成已落地能力。

## 上下文、冲突与恢复协议

| 问题 | 服务端裁决 |
|---|---|
| 上下文怎样传 | Orchestrator 持久化 StageTask，绑定 case ID、case status、完整 case version 哈希、Skill、唯一 Worker actor 和 JSON-Schema 校验后的输入快照。Worker 不从聊天历史自由推断资金操作参数。 |
| 政策冲突 | 重叠生效版本会被显式记入 `unresolved_conflicts`，系统可为说明性输出选取最新生效版本，但风险一律升为 L3，禁止自动写入。 |
| 金额冲突 | 一律重新通过 Decimal 规则引擎计算，并对照台账逐组件输出差异。无法归因时不允许 Agent 投票，而是升级人工。 |
| 谁能批准 | 只有独立 `approver` Principal 能决策；Risk 只路由，Executor 只验签后写，Verifier 不拥有写 scope。 |
| 证据不足 | 案件挂起在 `WAITING_FOR_EVIDENCE`，记录 gap；补证后仅能经白名单迁移回 `NORMALIZING`。 |
| 重复写 | 写工具同时校验签名能力令牌、幂等键、案件/币种/金额/组件限额和一次性 JTI。重放返回已有结果或显式冲突，不再写第二笔。 |
| Worker 失败/离线 | 每次 attempt 的 StageTask 终态与 StageResult 同事务提交。retryable 可原任务重试；final/retryable 任务也可由 dispatcher 显式重派，旧新 task ID 双向留痕。 |
| 人工驳回 | 不执行写入，按 `REJECTED → KNOWLEDGE_ARCHIVED → CLOSED` 生成 Trace、报告与 Case Memory。 |
| 写后异常 | Verifier 独立重查来源台账；差额非零则使用一次性 rollback token 反向冲销，再由 Verifier 复核，失败则保留 FAILED/现场。 |

## Trace 约定

- API 从 Bearer Principal 派生 `actor` + 最小 `scope`，请求体不得自报；每次调用产生 `tool_receipt`；
- 状态迁移、重试、升级、审批、执行、验证全部写入 `audit_events`；本地为 SQLite，正式 PolarDB 由库层 append-only 哈希链约束，并导出 `data/outputs/traces/CASE-*.json`；
- 复算输入保存 facts 快照与 `calculation_hash`（sha256），同输入同规则必得同结果，评委可逐笔复算。
- JSON Trace 同时导出保守的 OpenTelemetry GenAI 属性映射；内部 kind 继续保留，不虚构模型、
  Token 或供应商字段。详见 [`observability.md`](observability.md)。
