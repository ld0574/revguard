# RevGuard 部署与 AgentTeams 联调手册

> 沉淀日期：2026-08-08。记录 VM（10.10.10.202）上的实际部署状态、已踩过的坑与恢复步骤。

## 1. 部署拓扑

```
┌─────────────────────────── VM 10.10.10.202 ───────────────────────────┐
│                                                                       │
│  agentteams-controller (agt CLI)      revguard-api (Docker Compose)   │
│  ├─ agentteams-manager                ├─ FastAPI :9000 (容器)          │
│  ├─ agentteams-dashboard :13000       └─ 宿主端口 19000 → 9000         │
│  ├─ Higress :8086/:8087/:8088                                        │
│  │   └─ Element Web http://10.10.10.202:8088                          │
│  └─ revguard-worker × 10 (copaw) ──HTTP──► 10.10.10.202:19000         │
│                                            /api/v1/tools/call         │
└───────────────────────────────────────────────────────────────────────┘
```

- RevGuard API：`http://10.10.10.202:19000`（文档 `/docs`，健康检查 `/api/v1/health`）
- LLM 网关：`moonshotai/kimi-k3` 经 Higress ai-proxy（openai-compat.static:34350，前期已修复 503）

## 2. 部署命令（VM 上）

```bash
cd /root/revguard
docker compose up -d --build        # 构建并启动（启动时自动 seed 3 个 Golden Case）
curl http://localhost:19000/api/v1/health   # {"status":"ok","cases":3}
```

AgentTeams 联调（Worker + Team 一键创建，幂等）：

```bash
bash /root/revguard/scripts/agentteams_setup.sh
```

## 3. 端到端验证记录（2026-08-08 实测通过）

| 步骤 | 命令 | 结果 |
|---|---|---|
| 健康检查 | `GET /api/v1/health` | `{"status":"ok","cases":3}` |
| 运行案件 | `POST /api/v1/cases/CASE-2026-0001/run` | `WAITING_FOR_APPROVAL`（L2 挂起） |
| 人工审批续跑 | `POST /api/v1/cases/CASE-2026-0001/approval` | `CLOSED` + 独立验证 `PASSED` |
| Worker 创建 | `agt apply worker × 10` | 全部 created（copaw / kimi-k3） |
| Team 组建 | `agt create team revguard-team` | **Active，READY 9/9**（首次 Failed 为启动竞态，恢复后重建通过） |
| Worker→API 连通 | Worker 容器内 curl `/api/v1/health` 与 `/api/v1/tools/call` | 成功，返回 `tool_receipt` |

## 4. 踩坑与排查手册

### 4.1 宿主端口冲突

VM 上 9000 已被其他容器占用（docker-proxy）→ compose 使用 **19000:9000**。
改端口需同步三处：`docker-compose.yml`、`agentteams/README.md`、Worker SOUL 中的工具契约地址。

### 4.2 SOUL 中的 API 地址占位符

初版 SOUL 写 `{REVGUARD_API}` 占位符，Worker 无法解析。
现 8 个 SOUL 统一为 `http://10.10.10.202:19000/api/v1/tools/call`（迁移环境时全局替换即可）。

### 4.3 10 个 Worker 并发拉起导致主机过载

- 现象：sshd TCP 可连但会话挂起、RevGuard API 超时、`agt` 查询无响应。
- 原因：10 个 copaw 运行时同时启动，CPU/内存瞬时打满（load 峰值 >390）；磁盘已 89%。
- 结局：约 1.5 小时后负载自行回落，10 个 Worker 全部 Running，Team 自愈为 Active 9/9。
- 应对：
  1. 等待 Worker 全部进入 Running 后再操作（避免启动高峰期查询 controller）；
  2. 如持续过载，分批启动或缩减演示 Worker 数（≥3 个即满足赛道要求）；
  3. SSH 使用 ControlMaster 复用连接，避免 MaxStartups 限流（见 §4.4）。
- 另注意：`agt delete team` 为异步删除，立即重建会 409；`agentteams_setup.sh` 已处理
  （Active 跳过 / 非 Active 时先删并轮询等待消失再建）。

### 4.4 本机访问 VM 的 SSH 工具链

本机无 sshpass，使用 expect 脚本（密码在脚本中，仅限内网演示环境）：

- `/tmp/at_ssh.exp "cmd"` — 普通命令（20s 空闲超时）
- `/tmp/at_ssh_long.exp "cmd"` — 长任务（280s 空闲超时，用于镜像构建/批量 agt）
- `/tmp/at_scp.exp local remote` — 文件上传
- `/tmp/at_mux.exp "cmd"` — ControlMaster 连接复用（防 sshd 限流）

注意：expect 空闲超时会在远程长时间无输出时掐断会话（曾因镜像拉取无输出被掐断），
长任务务必用 long 版本并让命令持续输出（如 `| tail -f` 或分段执行）。

### 4.5 Element Web 登录页显示 127.0.0.1（2026-08-08 已修复）

- 现象：浏览器打开 `http://10.10.10.202:8088` 登录时 homeserver 指向 127.0.0.1，必然失败。
- 根因：`agentteams-controller` 容器内 `/opt/element-web/config.json` 的
  `default_server_config.m.homeserver.base_url` 出厂值为 `http://127.0.0.1:8086`，
  该地址只在 VM 本机有意义。
- 修复（已执行）：`sed -i 's|http://127.0.0.1:8086|http://10.10.10.202:8086|g' /opt/element-web/config.json`
  （原文件备份为 config.json.bak）。验证：`/_matrix/client/versions` 与
  `m.login.password`（admin）均通过 8086 正常返回。
- 注意：该修改在容器文件系统内，**容器重建后需重做**；持久化做法是把修正后的
  config.json 挂卷覆盖 `/opt/element-web/config.json`。

## 5. 复赛演示检查清单

- [x] `docker compose ps`：revguard-api Up（8.8 验证）
- [x] `agt get workers`：10 个 Running（8.8 验证，copaw / kimi-k3）
- [x] `agt get teams`：revguard-team **Active，READY 9/9**（8.8 验证）
- [x] Worker→API 连通：Worker 容器内 `curl http://10.10.10.202:19000/api/v1/health` 与
      `POST /api/v1/tools/call`（crm.get_partner）均成功，返回 tool_receipt（8.8 验证）
- [x] `agentteams_setup.sh` 幂等验证：Active 时跳过重建；异步删除竞态已修复（8.8）
- [ ] Element Web 进入 revguard-team 聊天室（现场演示步骤）
- [ ] 发送 Golden Case 申诉文本 → 观察 Worker 协同与审批节点
- [ ] 展示 `GET /api/v1/cases/{id}/trace` 与 `docs/reports/` 审计报告
