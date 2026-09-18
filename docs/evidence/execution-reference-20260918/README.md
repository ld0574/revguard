# 执行引用监视器对抗验证（2026-09-18）

## 结论

资金执行不再只依赖审批凭证，还必须能引用**同案实际读取过的事实**：
`commission.submit_adjustment` 在写台账前检查 `ORDER` / `CONTRACT` / `COMMISSION_LEDGER`
三个事实槽位，缺任一槽位或引用了别的订单即 `EVIDENCE_GAP`，台账零写入；
通过时把引用清单与折叠锚点写进台账分录。

实现：`revguard/execution_reference.py`、`revguard/mocks.py`（读取写入回执引用、
`_submit_single` 执行前校验、分录落账）。

## 探针结果（2026-09-18，202 Docker 验收镜像）

`scripts/execution_reference_probe.py` **5/5 通过**：

| 场景 | 输入 | 观测结果 |
|---|---|---|
| `missing_reads_blocked` | 无任何事实读取 | `EVIDENCE_GAP`：未读取必备事实槽位 ORDER / CONTRACT / COMMISSION_LEDGER，台账无写入 |
| `fact_bound_references` | 读取订单、合同、佣金台账 | 执行成功，分录带 3 条 `FACT_BOUND` 引用与可复算 `reference_anchor` |
| `foreign_order_rejected` | 读的是另一个订单 | `EVIDENCE_GAP`：事实引用与执行依据不一致（ORDER），台账无写入 |
| `legacy_receipts_case_only` | 只有监视器上线前的历史回执 | 允许执行，但标注 `CASE_ONLY` 弱绑定、摘要 `null` |
| `monitor_off_control` | 关闭监视器 | 行为与历史版本一致（兼容性对照） |

## 复跑方式

```bash
python3 scripts/execution_reference_probe.py \
  --output docs/evidence/execution-reference-20260918/probe-output.json
python3 -m unittest tests.test_execution_reference -v   # 5 项
```

`probe-output.json` 为机器可读结果（含 `passed`，失败返回非零退出码）；
`probe-console.txt` 为同一次运行的完整控制台输出。

## 制品哈希（SHA-256）

| 文件 | SHA-256 |
|---|---|
| `probe-console.txt` | `8bd025652b2f74f898e0752ed0a6500d72342107eede2eb1ff90083e65f2ad3d` |
| `probe-output.json` | `9a47b2edd375c1fa46300fce284551acb947221440b2ccbdef061e1ca12cf138` |

## 边界

- 监视器证明"事实被真实读取过"，不重算金额；金额由 `revguard/rule_engine.py` 确定性内核计算、
  由 `revguard/commitment.py` 的人工审批参数承诺锁定；
- 历史回执只能给出 `CASE_ONLY` 弱绑定，强绑定只对开关打开后新产生的回执有效；
- 开关默认关闭（`REVGUARD_REQUIRE_EXECUTION_REFERENCES=false`）；可在演示/验收栈通过环境变量显式打开，本轮演示栈保持默认关闭以固定单一口径，能力验证在验收镜像内由对抗探针 5/5 与单测完成；
- 覆盖范围为 RevGuard 受控台账与真实 ERPNext 只读集成，不含外部 ERP 会计写入。
