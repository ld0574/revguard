# RevGuard Demo 剧本

> 对照设计文档 §20.3「Demo 必须展示」逐项映射到 7 个 Golden Case 的实际运行产物，
> 并避开 §20.4「Demo 不应展示」的全部反模式。
>
> 本地一键复现：`cd revguard && python3 scripts/seed_demo.py && python3 scripts/run_demo.py`
> （纯标准库，无需安装任何依赖）。产物位置：
> - Trace：`data/outputs/traces/CASE-*.json`
> - 审计报告：`docs/reports/CASE-*.md`
> - 沉淀样本：`data/outputs/case_memory/CASE-*.json`

## 案件一览

| 案件 | 场景 | 关键看点 | 结局 |
|------|------|----------|------|
| CASE-2026-0001（GOLDEN-001） | 佣金少算：政策版本用错 + 回款佣金漏算 | 失败重试、版本 Time Travel、L2 审批、受控执行、独立验证 | 差额 14,400 补付，PASSED → CLOSED |
| CASE-2026-0002（GOLDEN-002） | 佣金多算：等级 7/15 才生效，订单在 7/10 | 证据冲突（等级时点）、负向调整强制 L2 | 多付 4,000 冲销，PASSED → CLOSED |
| CASE-2026-0003（GOLDEN-003） | 工单只有代理商名、无订单号，候选 3 笔 | 证据不足挂起，不猜不编 | WAITING_FOR_EVIDENCE 升级人工 |
| CASE-2026-0004（GOLDEN-004） | 申诉不成立：台账与政策复算完全一致 | 零差异直接关闭，不误判、不产生多余调整动作 | L0 无需调整 → CLOSED |
| CASE-2026-0005（GOLDEN-005） | 整单漏算：已回款订单在结算单中完全缺失 | 全额补付（销售 4,800 + 回款 1,800），L2 审批后执行 | 差额 6,600 补付，PASSED → CLOSED |
| CASE-2026-0006（GOLDEN-006） | L1 小额免审批：差额 ≤5,000 且证据充分 | 系统签发自动授权凭证（AUTO-L1），免人工审批直通执行，全程留痕 | 差额 4,180 自动补付，PASSED → CLOSED |
| CASE-2026-0007（GOLDEN-007） | L3 超额强制人工：差额 72,000 超过 50,000 红线 | 只生成处理方案，禁止系统自动执行，转人工线下处理 | L3 → CLOSED（无任何执行记录） |

## §20.3 必演项逐项映射

| # | 必演项 | 演示位置 | 证据 |
|---|--------|----------|------|
| 1 | AgentTeams 任务编排 | 编排器把案件拆成 受理→证据→政策→复算→根因→风险→审批→执行→验证→沉淀 10 环节 | `data/outputs/traces/CASE-2026-0001.json` 的 Span 序列（45 个 Span）；AgentTeams 侧见 `agentteams/README.md` |
| 2 | 至少 3 个不同 Agent | 实际定义 10 个不同职能 Agent | `docs/agents.md` 登记总表；`agentteams/workers/*.md` |
| 3 | 一个并行任务 | Evidence Agent 并行采集 8 类证据（订单/合同/政策/回款/退款/发票/台账/等级历史） | CASE-2026-0001 报告中「证据链」一节，每条带 tool_receipt |
| 4 | 一个条件分支 | CASE-2026-0003 实体消歧不唯一 → 挂起补证分支（而非继续主链路）；CASE-2026-0002 负向调整 → 强制 L2 审批分支 | `docs/reports/CASE-2026-0003.md`；risk 决策的 reason_codes |
| 5 | 一个工具调用失败和重试 | 故障注入：`finance.get_payment` 首次返回 TOOL_UNAVAILABLE，按退避重试成功 | CASE-2026-0001 Trace 中连续的 fail → retry → success Span（receipt 可查） |
| 6 | 一个证据冲突 | 代理商等级 GOLD 2026-07-15 生效，但订单创建于 2026-07-10 —— Policy Agent 回溯订单时点等级为 SILVER 8%，并显式标记冲突 | `docs/reports/CASE-2026-0002.md`「政策 Time Travel」一节：选中/排除版本 + 引用条款 |
| 7 | 一个人工审批 | CASE-2026-0001 差额 14,400 → L2 → 创建审批单 → `POST /cases/{id}/approval` 人工批准后自动续跑 | 审计事件 `APPROVAL_DECIDED`；审批单 APR-* 全量入审计 |
| 8 | 一个受控写操作 | Executor 携带审批凭证 + 幂等键提交两笔调整（补销售佣金 9,000 + 补回款佣金 5,400）；凭证/幂等/快照/回滚令牌六项前置校验 | CASE-2026-0001 报告「受控执行」一节：执行前后快照、idempotency_key、rollback_token |
| 9 | 一个执行后验证 | Verifier **独立重新查询**台账（不复用 Executor 回执），逐项核对应有 vs 实有 | 报告「独立验证」一节：component_checks 全 passed，variance=0 |
| 10 | 一份 Trace | 每案一份完整 Trace | `data/outputs/traces/CASE-2026-000{1,2,3}.json`；API：`GET /api/v1/cases/{id}/trace` |
| 11 | 一份最终报告 | 每案一份审计报告（证据链带回执、政策匹配依据、代入公式、根因、审批、快照、验证、审计事件表） | `docs/reports/CASE-2026-000{1,2,3}.md`；API：`GET /api/v1/cases/{id}/report` |

## §20.4 反模式规避声明

| 反模式 | 本项目的规避方式 |
|--------|------------------|
| Agent 只在聊天框互相对话 | 链路产物全是结构化 Artifact（证据/政策决策/复算结果/根因/审批/验证），落库并可回放 |
| LLM 直接计算佣金 | ADR-001：金额一律由 Decimal 规则引擎产出；CASE-2026-0001 的 27,000 = `180000 * 0.15`、5,400 = `180000 * 0.03` 均为代入式留痕 |
| 无审批直接修改资金数据 | L2/L3 必经 HumanApprovalGate；L1 小额免人工审批但须持系统签发的自动授权凭证（`AUTO-L1:*`，全程审计）；无凭证写操作被工具层拒绝并审计 |
| 只输出结论，没有证据 | 报告含逐项证据引用 + 工具回执 + 代入计算式；CASE-2026-0003 证明证据不足时系统宁可挂起 |
| 只有成功路径 | 故障注入重试（CASE-2026-0001）、证据不足挂起（CASE-2026-0003）、申诉不成立零调整（CASE-2026-0004）、L3 超额强制人工（CASE-2026-0007）、审批拒绝路径（API 支持 REJECTED） |
| 堆叠云组件无法解释必要性 | 全部件本地化：SQLite + 标准库 Python，每个组件在 README「技术选型」中给出存在理由 |

## 现场演示建议动线（约 8 分钟）

1. **30 秒**：讲痛点 —— 渠道佣金算错，人工对账要翻 4 个系统，错了还说不清谁改的。
2. **1 分钟**：`python3 scripts/run_demo.py` 现场跑 CASE-2026-0001，看控制台状态机流转。
3. **2 分钟**：打开 `docs/reports/CASE-2026-0001.md`，从证据链讲到独立验证，重点翻「政策 Time Travel」与「受控执行快照」。
4. **1 分钟**：展示 Trace JSON 中 `finance.get_payment` 的 fail→retry→success 三个连续 Span。
5. **1 分钟**：CASE-2026-0002 —— 等级冲突与负向调整强制审批（多付的钱也能安全追回）。
6. **1 分钟**：CASE-2026-0003 —— 证据不足挂起，强调「不猜、不编、不降低证据标准」。
7. **1 分钟**：风险分级全景 —— CASE-2026-0004（L0 零误伤）→ CASE-2026-0006（L1 系统自动授权直通）→ CASE-2026-0005（L2 人工审批）→ CASE-2026-0007（L3 强制人工，零执行记录）。
8. **1 分钟**：API 审批演示 —— `POST /cases/{id}/approval` 批准一个挂起案件，看它自动续跑执行与验证。
9. **30 秒**：收尾 —— 10 个 Agent SOUL 已可 `agt apply` 到 AgentTeams，同一套工具契约，无缝切换。
