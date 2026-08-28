# 案件审计报告：CASE-2026-0008

- 案件类型：`COMMISSION_UNDERPAYMENT`　来源：`EVALUATION`
- 最终状态：**ROLLED_BACK**　风险等级：**L2**
- 代理商：Nairobi Solar Solutions Ltd（`AGT-10001`）
- 订单：`EZ202608001`
- 申诉主张：实收 18000 KES，主张应有 32400 KES
- 证据完整度：**1.0**

## 1. 证据链（Evidence Package）

| 证据 | 来源系统 | 引用 | 强度 | 工具回执 |
|---|---|---|---|---|
| ORDER | CRM_MOCK | EZ202608001 | STRONG | `RCPT-69526F7B` |
| TIER_HISTORY | CRM_MOCK | AGT-10001 | STRONG | `RCPT-C1BCF8B6` |
| CONTRACT | CONTRACT_MOCK | AGT-10001 | STRONG | `RCPT-F313A444` |
| PAYMENT_RECORD | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-FD5E5FD6` |
| REFUND_RECORD | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-11DFF98F` |
| INVOICE | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-6D493B24` |
| COMMISSION_LEDGER | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-F51381E6` |
| POLICY_VERSIONS | CONTRACT_MOCK | KE-COMMISSION-2026 | STRONG | `RCPT-FFE006F1` |

## 2. 政策版本匹配（Policy Time Travel）

- 选中版本：**KE-COMMISSION-2026 2026-Q3**
- 判断时点：2026-07-10（依据字段 `order_date`）
- 引用条款 Q3-C1：GOLD 级代理商销售佣金按订单金额 15% 计算
- 引用条款 Q3-C2：SILVER 级代理商销售佣金按订单金额 8% 计算
- 引用条款 Q3-C3：BRONZE 级代理商销售佣金按订单金额 5% 计算
- 引用条款 Q3-C4：订单完成后 30 天内回款的，按回款金额 3% 计回款佣金
- 引用条款 Q3-C5：代理商当月完成订单满 20 笔的，按订单金额 2% 计月度激励
- 排除版本 2026-Q1：有效期 2026-01-01~2026-03-31 不覆盖业务时点 2026-07-10
- 排除版本 2026-Q2：有效期 2026-04-01~2026-06-30 不覆盖业务时点 2026-07-10
- 订单时点等级：**GOLD**（自 2026-06-01 生效）

## 3. 确定性复算（规则引擎，非 LLM）

| 组件 | 公式 | 代入 | 金额 | 是否适用 |
|---|---|---|---|---|
| SALES_COMMISSION | `order_amount * 0.15` | `180000 * 0.15` | 27000.00 KES | ✅ |
| SALES_COMMISSION | `order_amount * 0.08` | `` | 0 KES | —<br>跳过：agent_tier 期望 'SILVER'，实际 'GOLD' |
| SALES_COMMISSION | `order_amount * 0.05` | `` | 0 KES | —<br>跳过：agent_tier 期望 'BRONZE'，实际 'GOLD' |
| COLLECTION_COMMISSION | `payment_amount * 0.03` | `180000 * 0.03` | 5400.00 KES | ✅ |
| MONTHLY_INCENTIVE | `order_amount * 0.02` | `` | 0 KES | —<br>跳过：monthly_completed_orders='1' 不满足 monthly_completed_orders_gte 20 |

**复算合计：32400.00 KES**　舍入：scale=2,mode=HALF_UP　哈希：`sha256:3f5cad3a50827c822…`

## 4. 差异解释与根因

| 组件 | 应有 | 台账实有 | 差额 | 根因 |
|---|---|---|---|---|
| SALES_COMMISSION | 27000.00 KES | 18000.00 KES | 9000.00 KES | WRONG_POLICY_VERSION |
| COLLECTION_COMMISSION | 5400.00 KES | 0 KES | 5400.00 KES | MISSING_COMPONENT |

**总差额：14400.00 KES**　根因分类：**MISSING_COMPONENT, WRONG_POLICY_VERSION**
- SALES_COMMISSION 台账按版本 ['2026-Q2'] 计算为 18000.00，但业务时点应适用 2026-Q3，应为 27000.00
- COLLECTION_COMMISSION 在台账中不存在，应为 5400.00（180000 * 0.03）

## 5. 风险分级与审批

- 风险等级：**L2**（REQUIRES_HUMAN_APPROVAL）
- 需要审批：是，审批角色：FINANCE_LEAD
- 审批单：`APR-EF696B51`　状态：**APPROVED**
- 审批人：finance.lead（2026-08-28T00:21:36.414714Z）　意见：

## 6. 受控执行

### 动作 `ACT-314A117B`（SALES_COMMISSION，9000.00 KES）
- 幂等键：`CASE-2026-0008:SALES_COMMISSION`　状态：ROLLED_BACK　回滚令牌指纹：`sha256:a609e683dafa9b19`
- 执行前台账合计：18000.00　执行后台账合计：27000.00
### 动作 `ACT-8BF7F41E`（COLLECTION_COMMISSION，5400.00 KES）
- 幂等键：`CASE-2026-0008:COLLECTION_COMMISSION`　状态：ROLLED_BACK　回滚令牌指纹：`sha256:ca953a3874f8d840`
- 执行前台账合计：27000.00　执行后台账合计：32400.00

## 7. 独立验证（Verifier 重新查询，非 Executor 自证）

- 验证结论：**FAILED**
- 应有合计：32400.00 KES　实际合计：32401.00 KES　偏差：1.00 KES
  - ✅ SALES_COMMISSION：应有 27000.00，实际 27000.00
  - ❌ COLLECTION_COMMISSION：应有 5400.00，实际 5401.00

## 8. 回滚与冲销验证

- 冲销记录 `LED-9D10A320` 对冲 `LED-0C1A0E71`，金额 -5400.00 KES
- 冲销记录 `LED-DE8DC04C` 对冲 `LED-64C2A5F8`，金额 -9000.00 KES
- 回滚后独立验证：**PASSED**

## 9. Trace 与审计摘要

- Trace span 数：42　总耗时：69ms　错误 span：1

### 关键审计事件

| 时间 | 操作者 | 事件 |
|---|---|---|
| 2026-08-28T00:21:36.218986Z | evidence-rehearsal | CASE_CREATED |
| 2026-08-28T00:21:36.219352Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.225158Z | revguard-intake | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.225622Z | revguard-intake | SKILL_INVOKED |
| 2026-08-28T00:21:36.225899Z | revguard-intake | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.280671Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.281075Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.282201Z | revguard-intake | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.283196Z | revguard-intake | SKILL_INVOKED |
| 2026-08-28T00:21:36.283417Z | revguard-intake | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.287762Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.288043Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.288910Z | revguard-evidence | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.347894Z | revguard-evidence | SKILL_INVOKED |
| 2026-08-28T00:21:36.348552Z | revguard-evidence | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.356230Z | revguard-evidence | EVIDENCE_COLLECTED |
| 2026-08-28T00:21:36.356389Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.356739Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.358132Z | revguard-policy | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.359063Z | revguard-policy | SKILL_INVOKED |
| 2026-08-28T00:21:36.359470Z | revguard-policy | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.366772Z | revguard-policy | POLICY_MATCHED |
| 2026-08-28T00:21:36.367059Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.368380Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.369515Z | revguard-calculation | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.370177Z | revguard-calculation | SKILL_INVOKED |
| 2026-08-28T00:21:36.370451Z | revguard-calculation | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.377193Z | revguard-calculation | CALCULATED |
| 2026-08-28T00:21:36.377642Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.378289Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.379925Z | revguard-rootcause | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.380823Z | revguard-rootcause | SKILL_INVOKED |
| 2026-08-28T00:21:36.381156Z | revguard-rootcause | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.387120Z | revguard-rootcause | ROOT_CAUSE |
| 2026-08-28T00:21:36.387369Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.387814Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.388845Z | revguard-risk | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.389162Z | revguard-risk | SKILL_INVOKED |
| 2026-08-28T00:21:36.389402Z | revguard-risk | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.395317Z | revguard-risk | RISK_CLASSIFIED |
| 2026-08-28T00:21:36.396043Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.397588Z | revguard-risk | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.400365Z | revguard-risk | SKILL_INVOKED |
| 2026-08-28T00:21:36.400639Z | revguard-risk | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.407197Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.416014Z | evidence-rehearsal | APPROVAL_DECIDED |
| 2026-08-28T00:21:36.416198Z | evidence-rehearsal | STATE_TRANSITION |
| 2026-08-28T00:21:36.416826Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.417955Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T00:21:36.418315Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.418668Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.418885Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.423101Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.423586Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.424650Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.424956Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.425187Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.430217Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.431449Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.433182Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.433407Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.439971Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.440914Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T00:21:36.441334Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.443319Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.443600Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.450942Z | revguard-executor | EXECUTED |
| 2026-08-28T00:21:36.451296Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.452421Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.452709Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.452916Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.457186Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.458408Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.460545Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.460792Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.467757Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.468604Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T00:21:36.468961Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.471154Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.471471Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.478858Z | revguard-executor | EXECUTED |
| 2026-08-28T00:21:36.478953Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.479596Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.481124Z | revguard-verifier | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.482594Z | revguard-verifier | SKILL_INVOKED |
| 2026-08-28T00:21:36.482848Z | revguard-verifier | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.489314Z | revguard-verifier | VERIFIED |
| 2026-08-28T00:21:36.489396Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.489842Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.490917Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T00:21:36.491287Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.493329Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.493585Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.499402Z | revguard-executor | ROLLED_BACK |
| 2026-08-28T00:21:36.499743Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.500659Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T00:21:36.501009Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.503057Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T00:21:36.503318Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.508667Z | revguard-executor | ROLLED_BACK |
| 2026-08-28T00:21:36.509043Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.510383Z | revguard-verifier | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.512040Z | revguard-verifier | SKILL_INVOKED |
| 2026-08-28T00:21:36.512353Z | revguard-verifier | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.517875Z | revguard-verifier | ROLLBACK_VERIFIED |
| 2026-08-28T00:21:36.517968Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T00:21:36.519464Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T00:21:36.522211Z | revguard-knowledge | AGENT_TASK_STARTED |
| 2026-08-28T00:21:36.524567Z | revguard-knowledge | SKILL_INVOKED |
| 2026-08-28T00:21:36.525383Z | revguard-knowledge | AGENT_TASK_SUCCEEDED |
| 2026-08-28T00:21:36.537803Z | revguard-knowledge | KNOWLEDGE_ARCHIVED |

---
*报告由 RevGuard 自动生成；完整 Trace 见 data/outputs/traces/CASE-2026-0008.json*