# RevGuard 现场 Demo 剧本

一键复现：

```bash
make demo
make evaluate
```

## 8 个端到端案件

| 案件 | 场景 | 可验证结局 |
|---|---|---|
| CASE-2026-0001 | 旧政策版本 + 回款佣金漏算 | L2 审批，补付 14,400，独立验证通过 |
| CASE-2026-0002 | 订单时点等级冲突，多算佣金 | 负向 gross 调整强制 L2，验证通过 |
| CASE-2026-0003 | 缺订单号且存在多个候选 | 挂起补证，不计算、不执行 |
| CASE-2026-0004 | 申诉不成立 | L0 零动作关闭 |
| CASE-2026-0005 | 整单漏算 | L2 审批，补付 6,600，验证通过 |
| CASE-2026-0006 | 小额且证据充分 | L1 仅生成不生效草稿，台账零写入 |
| CASE-2026-0007 | gross 金额超过 50,000 | L3 只出方案，系统零执行 |
| CASE-2026-0008 | Verifier 首次查询注入 1 KES 偏差 | FAILED → 反向冲销 → 回滚验证 PASSED → ROLLED_BACK |

## 8 分钟演示动线

1. **场景与输入（40 秒）**：展示一条佣金申诉，说明人工需跨 CRM、合同、政策和财务核对。
2. **AgentTeams 拆解（50 秒）**：Manager 将任务拆给 Intake、Evidence、Policy、Calculation、
   RootCause、Risk、Executor、Verifier、Knowledge；强调 Executor/Verifier 分离。
3. **真实并行取证（50 秒）**：展示 7 个独立 I/O Tool span 重叠执行，政策查询等待合同结果后继续；
   每项证据含 receipt 和 SHA-256 content hash。
4. **政策与金额（60 秒）**：展示订单时点版本、等级回溯、Decimal 公式与 calculation hash。
5. **人工审批（60 秒）**：CASE-0001 停在 `WAITING_FOR_APPROVAL`；Approver Bearer Principal 批准，
   系统签发绑定案件/币种/gross 金额/有效期的能力令牌。
6. **写入与独立验证（60 秒）**：Executor 提交两项调整；Verifier 重新查询，不复用执行回执。
7. **故障与真实回滚（90 秒）**：运行 CASE-0008，展示首次验证 FAILED、两笔反向台账、
   `PostRollbackVerifySkill=PASSED`，最终状态保留 `ROLLED_BACK`。
8. **评测与复现（50 秒）**：运行 `make verify-ci`，展示 70 项测试、89% 覆盖率和
   105/105 场景评测；
   指向 `docs/evaluation-summary.json`、Trace、报告和 Case Memory。

## 评分项—证据映射

| 评审关注 | 演示证据 |
|---|---|
| AgentTeams 协同 | Matrix 事件 → Evidence Worker → API → Trace/Audit → Matrix 回执的正式证据 |
| 真实并行任务 | `EvidenceCollectSkill` 的 7 路 ThreadPool I/O 与并行耗时字段 |
| 工具失败重试 | 首次 Finance `TOOL_UNAVAILABLE` 为 ERROR span，随后 retry 成功 |
| 证据冲突 | CASE-0002 的等级生效时点回溯与冲突说明 |
| 人工审批 | CASE-0001/API 的 WAIT → Approver → signed capability token |
| 权限边界 | forged/cross-case/expired/scope-escalation/组件串用等 9 个攻击探针 |
| 受控写入 | signed token + gross/component quota + idempotency + snapshot + receipt |
| 执行后验证 | Verifier 新查询与逐组件 comparison |
| 回滚 | CASE-0008 的 reversal entries、一次性 rollback token、回滚后验证 |
| Skill 工程 | 16 个版本化 Skill + `/skills/{name}/invoke` + allowed actor |
| 可复现性 | `make verify`、干净 reset、重复 seed、Gateway 重启持久化测试 |

## 明确边界

- Demo 业务数据均为合成 Fixture，不代表真实企业生产数据。
- 并行基准为 7 个工具各注入 50ms 固定 I/O 延迟，用于证明并发实现；不代表生产网络 SLA。
- `APPROVAL_MODE=auto` 只用于离线 Golden 回放，API/Docker 默认 `wait`。
- L1 从不写资金台账；L3 从不自动执行。
- AgentTeams LLM 不计算金额，也不持有 API key；Bearer key 由只读 Skill Adapter 的 Secret 注入。
