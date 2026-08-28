# 复赛录制素材与分工清单

## 建议分工

| 角色 | 负责录制 | 输出文件 | 验收点 |
|---|---|---|---|
| A：主录屏 | Web 驾驶舱从重置到最终回滚的完整无剪辑操作 | `A01-cockpit-full.mp4` | 2560×1440 或 1920×1080；浏览器缩放 90–100%；清晰看到人工点击 |
| B：AgentTeams | Matrix 房间中 Orchestrator 派发、至少 3 个 Worker 接单与回执 | `B01-agentteams-room.mp4`、3 张 PNG | 同一 case/task/message ID 可与驾驶舱对账；不拍 Secret |
| C：工程证据 | 测试、MCP scope、PostgreSQL 迁移/触发器证据 | `C01-engineering.mp4`、终端截图 | 只展示命令与结论，不滚动大段日志；云 PolarDB 未完成就保留 PENDING |
| D：剪辑/旁白 | 按 `demo-script.md` 合并、配字幕、音量和片尾 | `RevGuard-semifinal-v1.mp4` | 字幕统一；不改变运行顺序；关键状态不靠后期伪造 |

没有四位组员时，A/B 可由一人承担，C/D 可由一人承担。

## A：Web 驾驶舱必须录到

1. 页面顶部“合成业务数据 · 真实运行链路”。
2. CASE-2026-0008 初始数据：180,000 / 18,000 / 32,400 KES。
3. 点击“启动多 Agent 调查”前后状态变化。
4. MCP Task 表：task ID、Skill、assigned actor、SUCCEEDED，至少停留 4 秒。
5. 证据表、政策版本、公式、根因和 calculation hash。
6. `WAITING_FOR_APPROVAL` 静止 3 秒，证明流程确实暂停。
7. 审批人亲自点击；不要用脚本代点、跳帧或预先批准。
8. `VERIFIED=FAILED`、1 KES variance、自动冲销、`ROLLBACK_VERIFIED=PASSED`。
9. 最终 `ROLLED_BACK`，以及完整 Audit / Trace / 工程证据页。

## B：AgentTeams 需要补的外部证据

- Team/Room 名称与成员列表：1 Manager + 至少 3 个不同职责 Worker。
- Orchestrator 发出的 StageTask，含 case ID、task ID、Skill、case version。
- Worker 通过 MCP 返回 request ID、skill receipt 和成功状态。
- 一个失败/重试消息，或 CASE-0003 补证挂起消息。
- 若 AgentTeams MCP Host 暂未跑通，保留 `PENDING_EXTERNAL_CAPTURE`，不要用本地终端冒充房间。

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
- 所有金额保留两位小数和 KES；所有 case ID 使用 CASE-2026-0008。
- 每个关键镜头前后多录 3 秒，方便剪辑；原始素材只剪切，不做状态合成。

## 交片前核对

- [ ] 第 10 秒内讲清业务问题，第 30 秒内讲清差异化。
- [ ] 明确标出 synthetic，不暗示真实公司数据。
- [ ] 看到 ≥3 个不同职责 Agent 的真实任务证据。
- [ ] 看到 MCP scope 和 StageResult，而非只有聊天气泡。
- [ ] 看到真实暂停与人工点击。
- [ ] 看到验证失败、回滚、回滚后再次验证。
- [ ] 看到 PostgreSQL 已验证 / PolarDB 待部署的真实边界。
- [ ] 画面与旁白中没有任何 Secret 或原始能力令牌。
