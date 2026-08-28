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
| ORDER | CRM_MOCK | EZ202608001 | STRONG | `RCPT-ECEEFE63` |
| TIER_HISTORY | CRM_MOCK | AGT-10001 | STRONG | `RCPT-8844F857` |
| CONTRACT | CONTRACT_MOCK | AGT-10001 | STRONG | `RCPT-01765C40` |
| PAYMENT_RECORD | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-F399020F` |
| REFUND_RECORD | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-3F4990BC` |
| INVOICE | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-938DEB86` |
| COMMISSION_LEDGER | FINANCE_MOCK | EZ202608001 | STRONG | `RCPT-191C1167` |
| POLICY_VERSIONS | CONTRACT_MOCK | KE-COMMISSION-2026 | STRONG | `RCPT-F562D13C` |

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
- 审批单：`APR-A8B074AF`　状态：**APPROVED**
- 审批人：finance.lead（2026-08-28T10:07:13.678003Z）　意见：

## 6. 受控执行

### 动作 `ACT-8A6730F1`（SALES_COMMISSION，9000.00 KES）
- 幂等键：`CASE-2026-0008:SALES_COMMISSION`　状态：ROLLED_BACK　回滚令牌指纹：`sha256:fd8f47415eba6586`
- 执行前台账合计：18000.00　执行后台账合计：27000.00
### 动作 `ACT-C0769C87`（COLLECTION_COMMISSION，5400.00 KES）
- 幂等键：`CASE-2026-0008:COLLECTION_COMMISSION`　状态：ROLLED_BACK　回滚令牌指纹：`sha256:7a8f8c7428768063`
- 执行前台账合计：27000.00　执行后台账合计：32400.00

## 7. 独立验证（Verifier 重新查询，非 Executor 自证）

- 验证结论：**FAILED**
- 应有合计：32400.00 KES　实际合计：32401.00 KES　偏差：1.00 KES
  - ✅ SALES_COMMISSION：应有 27000.00，实际 27000.00
  - ❌ COLLECTION_COMMISSION：应有 5400.00，实际 5401.00

## 8. 回滚与冲销验证

- 冲销记录 `LED-63B47999` 对冲 `LED-0EE3564C`，金额 -5400.00 KES
- 冲销记录 `LED-BEE46026` 对冲 `LED-01BBB830`，金额 -9000.00 KES
- 回滚后独立验证：**PASSED**

## 9. Trace 与审计摘要

- Trace span 数：42　总耗时：76ms　错误 span：1

### 关键审计事件

| 时间 | 操作者 | 事件 |
|---|---|---|
| 2026-08-28T10:07:13.490767Z | evidence-rehearsal | CASE_CREATED |
| 2026-08-28T10:07:13.491094Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.495984Z | revguard-intake | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.496346Z | revguard-intake | SKILL_INVOKED |
| 2026-08-28T10:07:13.496568Z | revguard-intake | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.544679Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.545047Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.546014Z | revguard-intake | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.546918Z | revguard-intake | SKILL_INVOKED |
| 2026-08-28T10:07:13.547113Z | revguard-intake | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.551741Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.552074Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.553209Z | revguard-evidence | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.620364Z | revguard-evidence | SKILL_INVOKED |
| 2026-08-28T10:07:13.620955Z | revguard-evidence | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.627758Z | revguard-evidence | EVIDENCE_COLLECTED |
| 2026-08-28T10:07:13.627899Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.628284Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.629780Z | revguard-policy | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.630514Z | revguard-policy | SKILL_INVOKED |
| 2026-08-28T10:07:13.630866Z | revguard-policy | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.637458Z | revguard-policy | POLICY_MATCHED |
| 2026-08-28T10:07:13.637716Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.638934Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.639969Z | revguard-calculation | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.640558Z | revguard-calculation | SKILL_INVOKED |
| 2026-08-28T10:07:13.640794Z | revguard-calculation | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.646530Z | revguard-calculation | CALCULATED |
| 2026-08-28T10:07:13.646754Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.647192Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.648241Z | revguard-rootcause | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.648889Z | revguard-rootcause | SKILL_INVOKED |
| 2026-08-28T10:07:13.649143Z | revguard-rootcause | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.654859Z | revguard-rootcause | ROOT_CAUSE |
| 2026-08-28T10:07:13.655125Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.655538Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.656515Z | revguard-risk | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.656791Z | revguard-risk | SKILL_INVOKED |
| 2026-08-28T10:07:13.657000Z | revguard-risk | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.661821Z | revguard-risk | RISK_CLASSIFIED |
| 2026-08-28T10:07:13.662261Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.663175Z | revguard-risk | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.665587Z | revguard-risk | SKILL_INVOKED |
| 2026-08-28T10:07:13.665822Z | revguard-risk | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.672266Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.679260Z | evidence-rehearsal | APPROVAL_DECIDED |
| 2026-08-28T10:07:13.679426Z | evidence-rehearsal | STATE_TRANSITION |
| 2026-08-28T10:07:13.679984Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.681015Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T10:07:13.681334Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.681648Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.681840Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.686007Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.686526Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.687555Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.687810Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.687987Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.691819Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.692709Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.694213Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.694402Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.700795Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.701739Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T10:07:13.702087Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.703911Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.704162Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.710155Z | revguard-executor | EXECUTED |
| 2026-08-28T10:07:13.710427Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.711286Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.711518Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.711687Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.715354Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.716219Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.718272Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.718500Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.724779Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.725493Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T10:07:13.725796Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.727651Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.727921Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.734547Z | revguard-executor | EXECUTED |
| 2026-08-28T10:07:13.734635Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.735100Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.736173Z | revguard-verifier | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.737460Z | revguard-verifier | SKILL_INVOKED |
| 2026-08-28T10:07:13.737671Z | revguard-verifier | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.743388Z | revguard-verifier | VERIFIED |
| 2026-08-28T10:07:13.743453Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.743831Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.744692Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T10:07:13.744976Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.746611Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.746825Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.751692Z | revguard-executor | ROLLED_BACK |
| 2026-08-28T10:07:13.752014Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.752952Z | revguard-executor | MCP_SERVER_SECRET_INJECTED |
| 2026-08-28T10:07:13.753277Z | revguard-executor | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.755183Z | revguard-executor | SKILL_INVOKED |
| 2026-08-28T10:07:13.755415Z | revguard-executor | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.759997Z | revguard-executor | ROLLED_BACK |
| 2026-08-28T10:07:13.760281Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.761166Z | revguard-verifier | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.762289Z | revguard-verifier | SKILL_INVOKED |
| 2026-08-28T10:07:13.762485Z | revguard-verifier | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.767310Z | revguard-verifier | ROLLBACK_VERIFIED |
| 2026-08-28T10:07:13.767413Z | revguard-orchestrator | STATE_TRANSITION |
| 2026-08-28T10:07:13.768827Z | revguard-orchestrator | AGENT_TASK_DISPATCHED |
| 2026-08-28T10:07:13.771531Z | revguard-knowledge | AGENT_TASK_STARTED |
| 2026-08-28T10:07:13.773483Z | revguard-knowledge | SKILL_INVOKED |
| 2026-08-28T10:07:13.774164Z | revguard-knowledge | AGENT_TASK_SUCCEEDED |
| 2026-08-28T10:07:13.783832Z | revguard-knowledge | KNOWLEDGE_ARCHIVED |

---
*报告由 RevGuard 自动生成；完整 Trace 见 data/outputs/traces/CASE-2026-0008.json*