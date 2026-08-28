# 复赛 Case 与可观测证据包

## 主案：CASE-2026-0008（视频主线）

一笔 180,000 KES 的合成订单，旧批处理只记了 18,000 KES 销售佣金。按订单时点的
2026-Q3 政策，正确结果是销售 27,000 + 回款 5,400 = 32,400 KES，需调整 14,400。
L2 人审后受控写入；Verifier 首次读取被注入 1 KES 偏差，系统冲销两项调整并再次验证，
最终保留 `ROLLED_BACK`。

选择它作为主案，是因为一条链同时覆盖：跨系统取证、历史政策、确定性计算、人工审批、
资金写入、独立验证、故障检测、补偿事务与知识沉淀。

## 反例一：CASE-2026-0003（不编答案）

工单只有代理商名称且存在多笔候选订单。系统停在 `WAITING_FOR_EVIDENCE`，列出候选项，
不匹配政策、不计算金额、不执行。这证明多 Agent 不会为了“跑完流程”伪造唯一结论。

## 反例二：MCP 越权探针（不能绕边界）

Intake Worker 的 `tools/list` 只能看到 `CaseNormalizeSkill` 与 `EntityResolveSkill`。即使 MCP
客户端手写 `LedgerAdjustSkill`，服务端仍返回 model-visible error；错 task、改 input、旧
case version 和已完成 task 重放也都被拒绝。

## 数据与流程的证据等级

| 项目 | 当前证据 | 可宣称 | 不可宣称 |
|---|---|---|---|
| 业务记录 | 合成清单 + 关联/时序/币种校验 | 高保真合成数据 | 公司真实数据 |
| MCP Team | 官方 SDK Client/Server + 20 个持久化任务 | 真实可执行 MCP 工作流 | 已等同完整 Matrix 房间 |
| 人工审批 | WebUI 独立 Approver 点击 | 录屏中真实人工决策 | 自动排练是人工证据 |
| 数据库 | PostgreSQL 18.6 一次性库集成验证 | PG/PolarDB 兼容工程已验证 | 已部署云 PolarDB |
| AgentTeams | 已有双 Agent 桥接证据；完整房间待补 | 已验证部分 Matrix 对账 | 完整 10 Agent 外部闭环已完成 |

## 可直接提交的本地证据

- `docs/evidence/demo-rehearsal/manifest.json`：20/20 Task、9 Worker、16 Skill 与故障闭环摘要。
- `01-human-gate.json`：自动流程停在人审边界的快照。
- `02-agent-tasks.json`：脱敏后的任务、actor、attempt、result 和 receipt。
- `03-audit-events.json`：状态、审批、执行、验证与回滚事件。
- `04-trace-spans.json`：可回放 Skill/Tool span。
- `05-final-case.json` 与 `06-audit-report.md`：终态和人读报告。
- `docs/synthetic-data-validation.json`：合成数据完整性和源文件哈希。

自动证据包把审批明确标为 `simulated_human=true`，只用于排练与回归；录屏完成后将真实
人手点击截图/视频与 AgentTeams 房间证据放入提交附件，不改写现有证据的分类。
