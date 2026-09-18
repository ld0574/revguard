# 0.6.0-rc2 决赛准备证据（2026-09-17 晚）

本目录保存 AgentTeams 模型通道切换与决赛演示栈复跑的无敏感信息证据。所有动作都在 10.10.10.202 Docker 内执行。

| 文件 | 内容 |
|---|---|
| `agentteams-glm-rotation.log` | luna → `glm-5.3-flash` 切换时的核验记录（当时上游 429） |
| `agentteams-glm-quota-probe.json` | 额度恢复后的鉴权、模型列表、最小生成与真实 StageTask 探针 |
| `rerun-CASE-2026-0001-*.json` | 19088 演示栈 CASE-2026-0001 复跑摘要（终态、任务账本、Trace 标记、资金操作） |
| `rerun-CASE-2026-0008-*.json` | 19088 演示栈 CASE-2026-0008 复跑摘要（偏差 1.00 → 反向冲销 → 净额归零） |
| `rehearsal-smoke-19088.log` | `scripts/rehearsal_smoke.sh` 在 202 本机的 7/7 PASS 输出 |
| `agentteams-glm-parameter-probe.txt` | 上游 `/v1/chat/completions` 参数矩阵：基线 / `reasoning_effort` / `thinking.type`（只打印状态与用量） |
| `agentteams-glm-reasoning-low-probe.txt` | `reasoning_effort=low` 在 512 预算下的重复探针（说明 low 不能替代充足预算） |
| `worker-model-probe-{intake,policy}.json` | Worker 内部经 Higress 网关的真实调用：`max_tokens=2048`、`reasoning_effort=low`、流式工具调用与结果续接 |
| `worker-adapter-command-probe.txt` | 内联 `adapter_command` 压力探针：low + 2048 下 4/4 完整未截断 JSON |
| `image-scan-0.6.0-rc2.json` | `revguard-api:0.6.0-rc2` 的 Trivy 扫描（HIGH/CRITICAL、忽略未修复项）：0 项 |
| `revguard-0.6.0-rc2.cdx.json` | 同一镜像的 CycloneDX SBOM（vuln + secret 扫描器） |
| `revguard-0.6.0-rc2.image-id.txt` | 发布镜像 ID 基线 |
| `verification/verify-gate-20260917.log` | `scripts/verify_docker.sh` 完整发布门禁（330 后端测试 + 82 PostgreSQL 集成测试 + `pip_audit` 0 漏洞 + `bandit` 0 问题 + npm audit 0 漏洞） |
| `verification/frontend-gate-20260917.log` | 前端门禁：UI 站点测试与 npm audit 输出 |
| `verification/verify-gate-20260918.log` | 收尾整改后的完整门禁复跑：331 后端测试 + 82 PostgreSQL 集成 + `pip_audit` 0 漏洞 + `bandit` 0 问题 + 前端 npm audit 0 漏洞 |
| `verification/frontend-gate-20260918.log` | 上述复跑的对应前端门禁日志 |

要点：

- 凭据只以 sha256 前 12 位指纹和长度出现，仓库、日志与证据包均不含明文 Key、Token 或 Cookie。
- glm-5.3-flash 的调用失败根因是思考模型把 `max_tokens` 全部用于 `reasoning_content`；已统一改为 `reasoning_effort: low` + `max_tokens: 2048`（详见 `docs/agentteams-glm-migration-20260917.md`）。
- 发布镜像 `revguard-api:0.6.0-rc2` 增加 Debian 基础镜像安全更新步骤，Trivy 可修复 HIGH/CRITICAL 由 13 项降为 0 项。
- 真实 Matrix 协作整改（`revguard_call.py --from-task` + `GET /api/v1/agent-tasks/{task_id}` 服务端绑定取回输入）后，触发器不再内联 JSON；门禁与聚焦测试（47 项）均在 202 容器内通过。
- 本轮同时修复了两处门禁缺陷：`tests/test_matrix_runtime_config.py` 为容器环境检查补齐 stub（verify 容器内无 docker CLI），`scripts/import_olist_erpnext.py` 改用 `tempfile.gettempdir()` 消除 bandit B108（`/tmp` 硬编码）中等风险告警；`docs/openapi.json` 已按 19088 容器导出的 rc2 快照更新。
- 19088 演示栈的两条记录由 `REVGUARD_TEAM_TRANSPORT=mcp` 参考执行器驱动：同一套 StageTask 与 Skill 契约、真实 ERPNext 读取、真实 Matrix 真人身份验证与审批、真实 PostgreSQL 资金写入与冲销。
- 真实 AgentTeams Worker 的 Matrix 协作记录见 19000 常驻栈与决赛视频；本轮 dev 栈 Matrix 路径的失败原因与整改建议见 `docs/agentteams-glm-migration-20260917.md`。
