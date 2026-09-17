# 0.6.0-rc2 决赛准备证据（2026-09-17 晚）

本目录保存 AgentTeams 模型通道切换与决赛彩排栈复跑的无敏感信息证据。所有动作都在 10.10.10.202 Docker 内执行。

| 文件 | 内容 |
|---|---|
| `agentteams-glm-rotation.log` | luna → `glm-5.3-flash` 切换时的核验记录（当时上游 429） |
| `agentteams-glm-quota-probe.json` | 额度恢复后的鉴权、模型列表、最小生成与真实 StageTask 探针 |
| `rerun-CASE-2026-0001-*.json` | 19088 彩排栈 CASE-2026-0001 复跑摘要（终态、任务账本、Trace 标记、资金操作） |
| `rerun-CASE-2026-0008-*.json` | 19088 彩排栈 CASE-2026-0008 复跑摘要（偏差 1.00 → 反向冲销 → 净额归零） |
| `rehearsal-smoke-19088.log` | `scripts/rehearsal_smoke.sh` 在 202 本机的 7/7 PASS 输出 |

要点：

- 凭据只以 sha256 前 12 位指纹和长度出现，仓库、日志与证据包均不含明文 Key、Token 或 Cookie。
- 19088 彩排栈的两条记录由 `REVGUARD_TEAM_TRANSPORT=mcp` 参考执行器驱动：同一套 StageTask 与 Skill 契约、真实 ERPNext 读取、真实 Matrix 真人身份验证与审批、真实 PostgreSQL 资金写入与冲销。
- 真实 AgentTeams Worker 的 Matrix 协作记录见 19000 常驻栈与决赛视频；本轮 dev 栈 Matrix 路径的失败原因与整改建议见 `docs/agentteams-glm-migration-20260917.md`。
