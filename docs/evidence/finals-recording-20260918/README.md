# 决赛录制代次重跑：两条真实 AgentTeams Matrix 完整运行（2026-09-18）

全部操作在 `10.10.10.202` Docker 彩排栈（`http://10.10.10.202:19088`，release
`0.6.0-rc3`）内完成。本目录是这一次"录制用运行记录"的可核验事实快照。

## 1. 为什么要重跑

上一代次的 `CASE-2026-0001/0008` 虽然走完了审批、执行、独立复核与冲销，但
`workflow_provenance.transport` 是 `mcp`（reference harness），房间证据为
`PENDING_EXTERNAL_CAPTURE`；而真正跑过 AgentTeams Matrix 的 `CASE-82822305` 因为
台账已经是 32,400（与政策复算一致）被判成 `L0 / READONLY_OR_ZERO_AMOUNT`，
没有资金动作。决赛要的是**同一条运行里**同时具备真实 AgentTeams 协作、真人审批、
真实资金写入与独立复核，因此本轮把两条 Golden Case 按真实 Matrix 路径重跑。

## 2. 两条运行记录

| | CASE-2026-0001 | CASE-2026-0008 |
|---|---|---|
| 订单 | `EZ202608001` | `EZ202608008` |
| 录制代次 | `REC-36B14AC3` | `REC-63A0C9EC` |
| 终态 | `CLOSED` | `ROLLED_BACK` |
| 风险等级 | L2（FINANCE_LEAD 审批） | L2（FINANCE_LEAD 审批） |
| 传输 | `agentteams-matrix` | `agentteams-matrix` |
| 房间证据 | `CAPTURED_FROM_RUNTIME` | `CAPTURED_FROM_RUNTIME` |
| 任务账本 | 16/16 `SUCCEEDED`，9 个 Worker | 18/18 `SUCCEEDED`，9 个 Worker |
| 审批单 | `APR-4A648C1D`，14,400.00 KES | `APR-8DE30A08`，14,400.00 KES |
| 资金结果 | 台账 18,000 → 32,400（+9,000 +5,400） | 写入 32,401，发现 1.00 KES 偏差 |
| 独立复核 | `variance 0.00`，actual = expected = 32,400.00 | `variance 1.00` → 2 笔反向冲销 → 净影响归零 |
| 审计事件 | 109 条 | 120 条 |
| 运行墙钟 | ≈ 6.8 分钟（405,961 ms） | ≈ 4.7 分钟（280,786 ms） |

两条运行使用不同订单、不同任务、不同审批、不同执行与不同 Trace，分别由
`POST /api/v1/cases/{id}/team/run` 启动，中途在 `WAITING_FOR_APPROVAL` 由操作者在
Web 端完成 **AgentTeams Matrix 身份验证（`matrix-password`）→ 批准**，
随后 Executor 用一次性能力令牌写入、Verifier 独立重查。

审批人是**演示审批账号**（`@admin:matrix-local.agentteams.io:8086`，显示名
"财务负责人（演示）"），不是真实企业员工，也不声称代表任何机构；它证明的是
"模型不能自签、后端独立验证人类身份并绑定案件与审批单"这条流程约束。

`case-facts.json` 是只读事实快照（由 `/api/v1/cases/{id}` 与 `/dashboard` 导出，
脚本见 `scripts` 说明下方），`replay-index.json` 是同步发布的官网回放索引。

## 3. 本轮修掉的录制缺陷

`REVGUARD_POSTING_TAMPER_*` 注入的"写后偏差"原本是**全局一次性开关**
（`posting_tamper_used`）。整库 reset 会重新武装它，但单案
`POST /api/v1/cases/{id}/reprepare`（WebUI 的"重新准备当前案件"）不会，于是：

- 第一次录制：偏差注入成功，Verifier 报 `variance 1.00`，走冲销 → `ROLLED_BACK`；
- 第二次录制：偏差不再注入，Verifier 报 `variance 0.00` → 直接 `CLOSED`，
  **冲销与恢复演示静默消失**（本轮在 202 彩排栈实测复现：20:38 的 `CASE-2026-0008`
  即为该退化记录）。

修复：把消费状态改为按案件记录（`posting_tamper_used_cases`），
`reprepare` / `reset_case` 为新代次重新武装该案，同时保留旧状态的保守兼容读取。
回归用例 `tests/test_api.py::test_15z_reprepare_rearms_scoped_posting_fault_for_next_generation`
连续两代次都要求 `ROLLED_BACK`。修复后重跑的同一条案件（本节表格中的
`CASE-2026-0008`）已恢复 `variance 1.00 → 冲销 → ROLLED_BACK`。

## 4. 复现方式

```bash
# 在 10.10.10.202 Docker 内（彩排栈，release 0.6.0-rc3）
# 1) 单案重新准备：只清本案的派生台账与网关副作用，台账回到 18,000 基线
curl -s -X POST -H "Authorization: Bearer <operator-key>" \
  http://127.0.0.1:19088/api/v1/cases/CASE-2026-0001/reprepare
# 2) 启动真实 AgentTeams Matrix 运行
curl -s -X POST -H "Authorization: Bearer <operator-key>" \
  http://127.0.0.1:19088/api/v1/cases/CASE-2026-0001/team/run
# 3) 在 WebUI 完成 Matrix 真人身份验证并批准；随后 Executor/Verifier 自动续跑
# 4) 导出官网回放
python scripts/export_case_replay.py --base-url http://127.0.0.1:9000 \
  --api-key-file <viewer-key-file> --case CASE-2026-0001 --case CASE-2026-0008 \
  --output-dir /app/data/outputs/replay-rc3
```

事实快照的生成脚本不在仓库内（一次性 202 运维脚本）；同一份数据可由上面两个
只读接口重新导出，字段与 `case-facts.json` 一致。

## 5. 边界

- 业务数据仍是**合成**渠道/合同/政策/争议；ERPNext、Matrix、PostgreSQL 是真实运行系统。
- 审批账号是演示身份；不声称存在企业客户采用或真实企业审批人。
- 墙钟时长是本次真机运行的真实值（未加速）；官网回放页的"回放"展示为加速演示，
  不等同于真实耗时。
