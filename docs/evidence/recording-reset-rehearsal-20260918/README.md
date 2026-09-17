# 录制栈重置预演（2026-09-18）

## 目的

决赛要在录制栈（10.10.10.202 的 19088 合成录制库）重新跑两条完整案件：
主案例 `CASE-2026-0001`（订单 EZ202608001）与错误恢复案例 `CASE-2026-0008`（订单 EZ202608008）。
两者都要求“台账初始值 → 真人审批 → 受控写入 → 独立复核”的真实差异，因此必须先确认
产品自身的录制库重置能把资金台账恢复到基线。

重置只在合成录制库执行（`REVGUARD_ALLOW_DATABASE_RESET=true`），
由 `seed_store(reset=True)` 走 `store.reset()`：同一事务里重建 `public` schema、写入网关基线
（含 `money_ledger` 基线分录）并播种 8 个 Golden Case。真实 ERPNext 与 19000 展示栈不受影响。

## 预演方法

在 202 上用独立容器与独立网络做隔离预演，不触碰 19088 与 19000：

1. 新建一次性 Postgres 16 容器（独立网络、独立卷、`revguard-rehearsal-only` 口令）；
2. 用 19088 的 API 镜像、`REVGUARD_AUTO_MIGRATE=true` 建 schema；
3. 执行与部署脚本相同的代码路径：

```python
from revguard.api import store, gateway
from scripts.seed_demo import seed_store

seed_store(store, reset=True, quiet=False, gateway=gateway, reset_actor="reset-rehearsal")
```

4. 重置后用 psql 直接查库，核对案件状态、`money_ledger` 基线、审批与执行表。

预演结束后删除一次性容器与网络。

## 结果

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 重置 + 播种 | `reset and seeded 8 cases atomically` | [seed.txt](seed.txt) |
| 案件状态 | 8 个 Golden Case，全部 `CREATED` | [cases.txt](cases.txt) |
| 资金台账基线 | 仅剩批处理基线分录：EZ202608001 = 18,000.00、EZ202608008 = 18,000.00，全部 `POSTED` | [ledger.txt](ledger.txt) |
| 审批 / 执行 / 审计 | approvals = 0、executions = 0、audit_events = 16（8 个 CASE_CREATED + 8 个 DEMO_RESET） | [counts.txt](counts.txt) |

预演确认了录制前需要的初始条件：两条案件的佣金台账都回到 18,000.00 基线，
因此复算应付 32,400.00 会产生真实的 +14,400.00 调整（销售佣金 9,000 + 回款佣金 5,400），
进入 L2“人工审批后执行”分级（`write=true`、`max_amount=50000`、`rollback_plan_required=true`），
而不是只读的“无需动作”结果。

## 边界

- 预演使用 `REVGUARD_ENTERPRISE_PROVIDER=mock`，只验证资金台账基线与案件播种；真实 ERPNext 取证链路在正式运行时才参与。
- 预演不改变 19088/19000 的任何数据；正式重置属于录制前的运维动作，执行记录会写入重置后的审计链（`DEMO_RESET`）。
- 资金台账是 append-only：重置通过重建 schema 完成，不存在对既有分录的删改。
