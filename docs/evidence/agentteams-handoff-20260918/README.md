# Element 多 Agent 任务交接证据（2026-09-18）

**对应反馈**：复赛答辩第 2 问——"要展示 Element 中多 Agent 的协作过程：前一个 Agent 调查得到什么结果、
在 room 发一条消息，下一个 Agent 利用这条消息继续做下一步任务，也就是任务交接的过程"；
以及"决赛应补充同一案件的真人批准、后续任务继续执行和最终状态"。

**结论**：彩排栈（19088，`0.6.0-rc3`）的真实 Matrix 通道中，每推进一个 Stage 都会在编排房间
（orchestration room `!NCserup…:8086`）发布一条 `REVGUARD_STAGE_HANDOFF` 消息，字段包括：

| 字段 | 含义 |
|---|---|
| `previous_stage_task_id` / `previous_skill` / `previous_actor` | 上一步是谁、用哪个 Skill 做完的 StageTask |
| `previous_artifact_hash`（`sha256:…`） | 上一步产物的规范化哈希，下一步据此对齐 |
| `previous_matrix_response_event_id` | 上一步 Worker 在房间里回消息的 Event ID |
| `previous_skill_receipt` | 上一步的 Skill 回执（`SKR-…`） |
| `previous_result_summary` | 上一步结果的安全摘要（如 `policy_version`、`total_commission`、`risk_level`） |
| `next_stage_task_id` / `next_skill` / `next_actor` | 下一步由谁承接、执行哪个 Skill |
| `next_input_keys` / `next_input_hash` | 下一步的输入键与输入哈希 |
| `case_version` | 案件版本（状态机推进依据） |
| `traceparent` | 与 Trace 对齐的 W3C traceparent |
| `failure_requirement` | 缺证或失败必须返回结构化错误并停止推进，不得猜测或把失败写成成功 |

## 截图

| 文件 | 内容 |
|---|---|
| `element-orchestration-room.png` | Element 编排房间：`RUN_ACCEPTED` 之后逐条 `REVGUARD_STAGE_HANDOFF`，左侧是 10 个 Worker 房间 |
| `element-handoff-tile.png` | 单条交接消息（含上一任务/下一任务、产物哈希、案件版本、失败要求） |
| `element-worker-room.png` | 承接方 Worker 房间（`revguard-risk`）：收到编排方派发（含上一步交接上下文与 adapter_command），执行后回 `{"success": true, …, "skill_receipt": "SKR-…"}` |

## 机读清单

`handoff-chain.json` 是编排房间最近 60 条消息里的交接记录（本轮实测 47 条，覆盖
`CASE-2026-0004` 8 条、`CASE-2026-0002` 8 条、`CASE-2026-0007` 8 条、`CASE-2026-0008` 23 条），
仅保留任务 ID、Skill 名、产物哈希前缀、事件 ID、案件版本与时间戳，不含任何凭据。

`browser-result.json` 是本次抓取的机器可读结果：编排房间可见 16 条交接消息文本、
56 个交接事件元素，承接方 Worker 房间回执含 `skill_receipt`。

## 复现

```bash
# 1) AgentTeams 的 Element Web 在 10.10.10.202:8088
# 2) 浏览器镜像内登录并抓取（凭据从 API 容器环境读取，只挂载进容器，不落仓库）
docker run --rm --net host \
  -v /root/rgops/element-room-shot2.py:/probe.py:ro \
  -v /root/rgops/element-login.json:/creds.json:ro \
  -v /root/rgops/element-shots2:/evidence \
  -e ELEMENT_URL=http://10.10.10.202:8088/ \
  -e ELEMENT_ROOM_ID='!NCserupQgE5k9AW8rP:matrix-local.agentteams.io:8086' \
  -e ELEMENT_WORKER_ROOM_ID='!QmdcIdAibbhLHE4Rn3:matrix-local.agentteams.io:8086' \
  revguard-grafana-browser:20260918 python3 /probe.py
```

抓取脚本见 `capture-element-handoff.py`（同一个脚本）。凭据文件在抓取后删除，仓库与截图不含
Token、Cookie、密码或内部管理接口。

## 边界

- 截图来自 10.10.10.202 上真实运行的 AgentTeams（Matrix/Element）与 RevGuard 彩排栈；
- 案件业务数据（渠道、合同、政策、佣金争议）为**合成**，工作流为真实可执行；
- 这不是"排练画面拼接"：同一个 run 的 StageTask、Matrix 事件、审批记录、审计链都可在
  案件详情与数据库中按 `run_id` 复核；
- 真人批准 → 后续任务继续执行 → 终态，见 `CASE-2026-0001`（`CLOSED`）与
  `CASE-2026-0008`（`ROLLED_BACK`）的归档运行记录。
