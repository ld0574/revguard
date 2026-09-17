# 执行引用监视器：未读取即不可执行

## 它解决什么问题

评审和审计真正会问的不是"金额是多少"，而是"这个金额依据的事实，是谁、在什么时间、
从哪个系统读到的"。如果资金分录只有一个数字，事后无法证明它来自真实读取，而不是被
拼接出来的结论。RevGuard 因此在工具网关层面加了执行引用监视器（Execution Reference Monitor）。

## 机制

**读取时**（任何成功的只读工具调用）：

- 回执写入 `fact_digest`：返回数据的规范化 `SHA-256`（键排序、无多余空白）；
- 回执写入 `reference_keys`：只提取业务绑定键 `order_id` / `partner_id` / `currency`，
  不记录凭据、Cookie 与自由文本。

**执行时**（`commission.submit_adjustment`，写台账之前）：

| 必备事实槽位 | 可接受的读取工具 | 绑定键 |
|---|---|---|
| `ORDER` | `crm.get_order` | `order_id` |
| `CONTRACT` | `contract.get_contract`、`contract.get_effective_terms` | `partner_id`（期望值取自同案读到的订单事实，不是执行请求自报） |
| `COMMISSION_LEDGER` | `finance.get_commission_ledger` | `order_id` |

- 缺任一槽位 → `EVIDENCE_GAP`，台账不写入；
- 读了别的订单 → `EVIDENCE_GAP`（`事实引用与执行依据不一致`）；
- 币种不一致 → `EVIDENCE_GAP`；
- 命中绑定键记为 `FACT_BOUND`（强绑定）；监视器上线前的历史回执没有键位与摘要，
  记为 `CASE_ONLY`（弱绑定，摘要为 `null`），不冒充强绑定。

**落账**：台账分录新增 `execution_references`（槽位、工具、回执号、读取者、读取时间、
绑定强度、事实摘要）与 `reference_anchor`（整组引用的折叠摘要）。第三方可以用同一份回执
复核该分录依据的事实没有被替换。

## 配置

```bash
# 默认关闭（保持历史回归不变）；演示与生产验收建议打开
REVGUARD_REQUIRE_EXECUTION_REFERENCES=true
```

`docker-compose.yml` / `docker-compose.dev.yml` 都已声明该变量，默认 `false`。
打开后若要执行一笔资金调整，同案必须已经真实读取过订单、合同与佣金台账。

## 验证方式

```bash
# 5 场景对抗探针（任一"未读取却执行成功"即非零退出）
python3 scripts/execution_reference_probe.py \
  --output docs/evidence/execution-reference-20260918/probe-output.json

# 单元测试：缺读取被拦、强绑定落账、跨订单被拒、历史回执弱绑定、关闭后兼容
python3 -m unittest tests.test_execution_reference -v
```

运行证据：`docs/evidence/execution-reference-20260918/`。

**界面/接口查看**：`GET /api/v1/cases/{case_id}/money-operations` 返回资金操作及其
`result.ledger_entry.execution_references` / `reference_anchor`；完整案件证据包与审计链
同样携带这些字段。

## 边界

- 监视器保证"执行依据的事实确实被读过"，**不重算金额**：金额由确定性规则内核计算
  （`revguard/rule_engine.py`），并由人工审批的参数承诺摘要锁定（`revguard/commitment.py`）。
  三者互补：内核算金额、真人承诺金额、引用监视器证明依据。
- 监视器上线前写入的回执只能给出 `CASE_ONLY` 弱绑定；强绑定只对开启后新产生的回执有效。
- 未完成：跨系统的外部 ERP 会计写入 Saga 仍未验收；监视器覆盖 RevGuard 受控台账与真实
  ERPNext 只读集成。
