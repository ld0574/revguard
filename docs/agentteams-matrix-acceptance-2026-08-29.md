# AgentTeams / Matrix 录制环境验收（2026-08-29）

## 结论

录制环境 `http://10.10.10.202:19000/demo/` 已完成一次干净的真实
AgentTeams/Matrix + 开源 PolarDB-PG 端到端回放。业务记录为高保真合成数据；Agent
调度、StageTask、Skill 调用、人工关口、写入、验证、冲销、再验证和证据落盘均为
可执行工程链路。

这次批准由自动化部署验收使用独立 Approver Principal 调用，不作为“真人点击”证据。
正式录屏前必须先点“重置录制演示”，再由组员在画面中亲自点击批准。

## 验收结果

| 项目 | 结果 |
|---|---|
| Run | `RUN-AGT-D3409E51` |
| 存储 | `PostgreSQL 15.19 (PolarDB 15.19.5.0)` 官方 local_instance；8 个案件已持久化 |
| Team room / Orchestrator | `ACKNOWLEDGED`，控制面输入、触发与响应 event 均已捕获 |
| Worker | 9 个独立职责 Worker，9 个独立 Matrix room |
| StageTask | 20/20 `SUCCEEDED` |
| Matrix 回执 | 20/20 response event 已捕获 |
| 传输标记 | 20/20 `agentteams-matrix`；Worker 内部 Skill 适配跳转另记为 20/20 `rest`，两层不再混淆 |
| 自动补救 | 0 次 retry nudge；补救机制仍保留用于模型偶发格式失败 |
| Trace | 63 spans：21 个真实 Matrix / Worker 端到端 `AGENT` spans + 42 个进程内 Skill / Tool spans |
| 实际耗时 | AGENT span 最短 1.414s、中位 9.654s、最长 23.146s；`<1ms` 仅代表进程内确定性计算 |
| 首次写后验证 | `FAILED`，期望 32,400 KES，实际读数 32,401 KES，差额 1 KES |
| 反向冲销 | 两个组件均生成 reversal，执行记录为 `ROLLED_BACK` |
| 回滚后验证 | `ROLLBACK_VERIFIED / PASSED`，销售恢复 18,000 KES，回款恢复 0 KES |
| 终态 | `ROLLED_BACK`；Knowledge Agent 已完成 `CaseToDatasetSkill` |
| 报告 | HTTP 200，约 15 KB |
| 容器 / Team | `revguard-api` healthy；`revguard-team` Active 9/9 |
| 数据库审计 | 148 条审计事件，DB trigger 哈希链 `VALID`，broken links = 0 |

## 三组可对账样本

| Actor / Skill | task | request | dispatch / response | receipt |
|---|---|---|---|---|
| Intake / CaseNormalize | `TASK-770122A6` | `REQ-AGT-9A827444` | `$WCvnPI…` / `$PA7jOw…` | `SKR-4B245719` |
| Risk / RiskClassify | `TASK-9C5F8216` | `REQ-AGT-C50700B8` | `$_Loork…` / `$GGza8p…` | `SKR-8F28B4BE` |
| Knowledge / CaseToDataset | `TASK-0047C292` | `REQ-AGT-B4671A9D` | `$h4-E8K…` / `$VIYRT9…` | `SKR-2FF949AF` |

完整 room ID、event ID、不可变输入、StageResult 输出与 Trace span 都可在 Web 驾驶舱的
“Agent 任务账本”逐项展开。本文只保留便于口头对账的短标识，不包含密码、API key、
approval token 或 rollback token。

## 非固定案件验证

驾驶舱支持下拉选择或 `?case=CASE-2026-0007`。最新版部署后，0007 以
`RUN-AGT-4387781F` 在同一 PolarDB-PG 实例上再次通过真实 AgentTeams/Matrix 运行：
8/8 StageTask 与 8/8 response event 成功，风险为 `L3`，确定性复算 72,000 KES，终态
`CLOSED`，执行记录为 0，符合“超额只出方案不执行”。该次运行产生 30 spans，其中 9 个
是真实 AGENT spans，最短 1.598s、中位 6.183s、最长 100.965s。Calculation Worker 首次
响应过慢，45 秒后触发 1 次同任务 retry nudge，最终仍由原 task 形成唯一 StageResult；
这次时延抖动与补救记录也说明页面展示的是实际 Matrix/Worker 链路，而非固定动画。

## 录屏前 60 秒检查

1. 打开驾驶舱并点“重置录制演示”，看到 `CREATED` 和“合成业务数据 · 真实运行链路”。
2. 确认右上角为 `AGENTTEAMS LIVE`，Team 状态为 Active 9/9。
3. 点击“启动多 Agent 调查”，先拍 Orchestrator `ACKNOWLEDGED`，再展开 Intake、Policy、
   Risk 三项，拍清 input、output 与六类关联标识。
4. 等到 `WAITING_FOR_APPROVAL` 后静止至少 3 秒，再由组员亲自点击批准。
5. 终态必须同时看到首次验证 `FAILED / 1 KES`、`ROLLBACK_VERIFIED / PASSED`、
   `ROLLED_BACK` 和 20/20 StageTask。

主旁白见 [`demo-script.md`](demo-script.md)，组员素材分工见
[`recording-shot-list.md`](recording-shot-list.md)。
