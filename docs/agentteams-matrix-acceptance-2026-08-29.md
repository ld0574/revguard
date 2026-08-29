# AgentTeams / Matrix 录制环境验收（2026-08-29）

## 结论

录制环境 `http://10.10.10.202:19000/demo/` 已完成一次干净的真实
AgentTeams/Matrix 端到端回放。业务记录为高保真合成数据；Agent 调度、StageTask、Skill
调用、人工关口、写入、验证、冲销、再验证和证据落盘均为可执行工程链路。

这次批准由自动化部署验收使用独立 Approver Principal 调用，不作为“真人点击”证据。
正式录屏前必须先点“重置录制演示”，再由组员在画面中亲自点击批准。

## 验收结果

| 项目 | 结果 |
|---|---|
| Run | `RUN-AGT-12227186` |
| Team room / Orchestrator | `ACKNOWLEDGED`，控制面输入、触发与响应 event 均已捕获 |
| Worker | 9 个独立职责 Worker，9 个独立 Matrix room |
| StageTask | 20/20 `SUCCEEDED` |
| Matrix 回执 | 20/20 response event 已捕获 |
| 传输标记 | 20/20 `agentteams-matrix`；Worker 内部 Skill 适配跳转另记为 20/20 `rest`，两层不再混淆 |
| 自动补救 | 0 次 retry nudge；补救机制仍保留用于模型偶发格式失败 |
| Trace | 42 spans |
| 首次写后验证 | `FAILED`，期望 32,400 KES，实际读数 32,401 KES，差额 1 KES |
| 反向冲销 | 两个组件均生成 reversal，执行记录为 `ROLLED_BACK` |
| 回滚后验证 | `ROLLBACK_VERIFIED / PASSED`，销售恢复 18,000 KES，回款恢复 0 KES |
| 终态 | `ROLLED_BACK`；Knowledge Agent 已完成 `CaseToDatasetSkill` |
| 报告 | HTTP 200，15,253 bytes |
| 容器 / Team | `revguard-api` healthy；`revguard-team` Active 9/9 |

## 三组可对账样本

| Actor / Skill | task | request | dispatch / response | receipt |
|---|---|---|---|---|
| Intake / CaseNormalize | `TASK-899E9D1C` | `REQ-AGT-6A294695` | `$JHrkX_…` / `$HMxBZ6…` | `SKR-A1509BD1` |
| Risk / RiskClassify | `TASK-EDFC07B9` | `REQ-AGT-C4F11167` | `$zJMPGW…` / `$1ycnAK…` | `SKR-144445BD` |
| Knowledge / CaseToDataset | `TASK-5D0EA2A5` | `REQ-AGT-41E9DF06` | `$_0tv0i…` / `$gkh29z…` | `SKR-4D792222` |

完整 room ID、event ID、不可变输入、StageResult 输出与 Trace span 都可在 Web 驾驶舱的
“Agent 任务账本”逐项展开。本文只保留便于口头对账的短标识，不包含密码、API key、
approval token 或 rollback token。

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
