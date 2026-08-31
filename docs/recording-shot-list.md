# 复赛录制素材与分工清单

## 建议分工

| 角色 | 负责录制 | 输出文件 | 验收点 |
|---|---|---|---|
| A：主录屏 | Web 驾驶舱从重置到最终回滚的完整无剪辑操作 | `A01-cockpit-full.mp4` | 2560×1440 或 1920×1080；浏览器缩放 90–100%；清晰看到人工点击 |
| B：AgentTeams | Team room 的 Orchestrator 握手，以及 3 个 Worker 独立 room 的接单与回执 | `B01-agentteams-room.mp4`、3 张 PNG | 同一 room/message/request/task/receipt 可与驾驶舱对账；不拍 Secret |
| C：工程证据 | 测试、Higress 工具发现/隔离、开源 PolarDB-PG 版本/触发器证据 | `C01-engineering.mp4`、终端截图 | 展示真实网关拒绝与审计链 VALID；本地 stdio 和云 PITR 分清边界 |
| D：剪辑/旁白 | 按 `demo-script.md` 合并、配字幕、音量和片尾 | `RevGuard-semifinal-v1.mp4` | 字幕统一；不改变运行顺序；关键状态不靠后期伪造 |

没有四位组员时，A/B 可由一人承担，C/D 可由一人承担。

## A：Web 驾驶舱必须录到

本单采用“重置后先跑 0008”的回滚主线。偏差注入为整个演示网关一次性，当前已由 0002
消耗；当前 0008 是正常闭环，不能配回滚旁白。重置会清空全部演示记录，须组员确认。
不重置时，使用 0008 正常闭环 + 0002 故障演练，显式区分案件和金额。详见 `demo-script.md`。

1. 页面顶部“合成业务数据 · 真实运行链路”。
2. CASE-2026-0008 初始数据：180,000 / 18,000 / 32,400 KES。
3. 点击“启动多智能体调查”前后状态变化。
4. “AgentTeams 已连接”与任务账本：展开真实 input/output，拍到 room、message、request、task、receipt、trace，至少停留 6 秒。
5. 证据表、政策版本、公式、根因和 calculation hash。
6. `WAITING_FOR_APPROVAL` 静止 3 秒，证明流程确实暂停。
7. 审批人亲自完成 Matrix 账号验证，拍 120 秒动作证明的案件/审批单/决定绑定，再提交；不要用脚本代点、跳帧或预先批准。密码不入镜。
8. `VERIFIED=FAILED`、1 KES variance、自动冲销、`ROLLBACK_VERIFIED=PASSED`。
9. 最终 `ROLLED_BACK`，以及完整 Audit / Trace / 工程证据页；Trace 同框拍到 AGENT 秒级耗时和 SKILL/TOOL `<1ms` 口径说明。
10. 打开“价值模拟”，依次切换 100 / 500 / 1000 案情景；停在 500 案、100 元/小时，清楚录到 84.7%、6.54×、900 小时/月、90,000 元/月及“非现金承诺”。

## B：AgentTeams 需要补的外部证据

- Team room 名称与成员列表：1 Orchestrator + 9 个不同职责 Worker。
- Team room 中的 Orchestrator 控制面握手，含 case ID 与 run ID。
- Worker 独立 room 中的 StageTask，含 case ID、task ID、Skill 和 request ID。
- Worker 通过 skills-only Adapter → Higress MCP 返回 skill receipt；任务账本显示“MCP 网关”。
- 一个失败/重试消息，或 CASE-0003 补证挂起消息。
- 驾驶舱已展示服务器捕获的 Matrix event；Element 补录只用于让评委直观看到聊天室，不得用本地终端画面冒充。

## C：工程证据命令

```bash
make verify-ci
make evidence-bundle
make postgres-integration REVGUARD_TEST_POSTGRES_DSN='postgresql://...disposable...'
```

另录两个 MCP scope 画面：Intake 的 `tools/list` 只能看到 Intake Skills；手写调用
`LedgerAdjustSkill` 返回拒绝。不得在终端历史中出现数据库密码或 Bearer key。

## 统一素材规格

- 视频：H.264、16:9、30 fps；UI 画面尽量无鼠标乱晃。
- 图片：PNG 原图；命名 `角色-序号-内容.png`。
- 浏览器只保留一个 RevGuard 标签；关闭通知、书签栏、密码管理器提示。
- 主视频金额保留两位小数和 KES，主线使用 CASE-2026-0008；可补一帧案件下拉或 `?case=CASE-2026-0007`，证明非单案写死。
- 每个关键镜头前后多录 3 秒，方便剪辑；原始素材只剪切，不做状态合成。

## 交片前核对

- [ ] 第 10 秒内讲清业务问题，第 30 秒内讲清差异化。
- [ ] 明确标出 synthetic，不暗示真实公司数据。
- [ ] 看到 ≥3 个不同职责 Agent 的真实任务证据。
- [ ] 看到 AgentTeams/Matrix 调度、独立 Worker room 和服务端 StageResult，而非只有聊天气泡。
- [ ] 看到真实暂停与人工点击。
- [ ] 看到验证失败、回滚、回滚后再次验证。
- [ ] 看到开源 PolarDB-PG local_instance 已验证 / 云 PolarDB 高可用与 PITR 待验收的真实边界。
- [ ] 价值模拟同时拍到输入假设、公式、8 个合成样本和 `NOT ALLOWED` 生产声明边界。
- [ ] 画面与旁白中没有任何 Secret 或原始能力令牌。
