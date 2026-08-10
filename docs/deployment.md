# RevGuard 部署与 AgentTeams 联调手册

## 1. 推荐拓扑

```text
AgentTeams Manager / Workers
        │  HTTPS + Bearer Principal（由 Secret/Adapter 注入）
        ▼
RevGuard API :9000
        ├── Skill Runtime / ToolGateway
        ├── SQLite Case / Trace / Audit
        ├── persistent gateway state
        └── reports / case memory
```

与 AgentTeams 同一 Docker 网络时使用 `http://revguard-api:9000`；跨主机部署必须通过
TLS Gateway 暴露，并配置限流、访问日志与网络白名单。SOUL 使用
`{{REVGUARD_API_BASE_URL}}`，`agentteams_setup.sh` 在部署时渲染，不再硬编码 IP。

## 2. 本地 Docker

```bash
cd revguard
docker compose up -d --build
curl http://127.0.0.1:19000/api/v1/health
docker compose ps
```

与 AgentTeams 同机部署时，用覆盖文件把 API 加入 `agentteams-net`：

```bash
docker compose -f docker-compose.yml -f docker-compose.agentteams.yml up -d --build
docker exec agentteams-worker-revguard-evidence \
  python -c 'import urllib.request; print(urllib.request.urlopen("http://revguard-api:9000/api/v1/health").status)'
```

默认保留 volume 中的案件、Mock 台账、审批、幂等、回执、报告与 Trace。需要评委从
完全相同的干净状态复现时：

```bash
REVGUARD_RESET_ON_START=true docker compose up -d --build
```

`seed_demo.py` 的语义：

- 默认模式：已有案件保持原状态，只补充不存在的 Golden Case；
- `--reset`：原子清空案件、证据、审批、执行、验证、审计和 Trace，再 seed；
- `--gateway-state`：reset 时同步删除指定的 ToolGateway 状态文件。

正式故障演练可在干净状态下让 Verifier 的首次读取产生可控偏差：

```bash
REVGUARD_RESET_ON_START=true \
REVGUARD_VERIFICATION_TAMPER_AMOUNT=1 \
docker compose up -d --build
```

该偏差只作用于 Verifier 的一次查询结果，不会修改真实台账；预期链路为“审批后写入 →
独立验证失败 → 自动反向冲销 → 回滚后验证通过”。取证完成后把两个变量恢复为 `false`
和 `0`，已有 Trace 与报告仍保留。

## 3. 生产安全配置

复制 `.env.example` 并生成真实值：

```bash
cp .env.example .env
```

必须满足：

1. `REVGUARD_APPROVAL_SIGNING_KEY` 至少 32 字节并由 Secret Manager 管理；
2. `REVGUARD_API_KEYS_JSON` 为每个 Worker 配置独立 actor、roles 和最小 scopes；
3. `REVGUARD_ALLOW_INSECURE_DEMO_KEYS=false`；
4. API 只经 TLS Gateway 暴露，SQLite/状态 volume 不对 Worker 直接开放；
5. 定期轮换 API key 和签名密钥；轮换签名密钥会使旧能力令牌立即失效。

Compose 已配置非 root、只读根文件系统、`no-new-privileges`、drop all capabilities、
CPU/内存限制与健康检查。三个命名 volume 保持可写。

从旧版 root 容器升级到非 root 镜像时，需要在宿主机上一次性修正旧卷属主，
否则 SQLite 迁移会报 `attempt to write a readonly database`：

```bash
UID_GID=$(docker run --rm --entrypoint id revguard-revguard-api revguard \
  | sed -n 's/uid=\([0-9]*\).*gid=\([0-9]*\).*/\1:\2/p')
for VOLUME in revguard_revguard-db revguard_revguard-outputs revguard_revguard-reports; do
  docker run --rm --user 0 -v "$VOLUME:/mnt" --entrypoint sh revguard-revguard-api \
    -c "chown -R $UID_GID /mnt && chmod -R u+rwX /mnt"
done
```

## 4. AgentTeams Worker 与 Team

```bash
REVGUARD_HOME=/absolute/path/to/revguard \
REVGUARD_API_BASE_URL=http://revguard-api:9000 \
bash scripts/agentteams_setup.sh
```

脚本会：

1. 在临时目录渲染 SOUL 中的 API Base URL；
2. `agt apply worker` 创建或更新 10 个 Agent；
3. 以 `revguard-orchestrator` 为 leader 组建 Team；
4. 等待异步删除完成，避免重复部署 409；
5. 输出 Worker 和 Team 状态。

当前 CoPaw 运行时会在约 30 分钟空闲后把 Worker 自动置为 `Sleeping`，这是资源回收而非
故障。现场演示前可显式预热：

```bash
for worker in revguard-orchestrator revguard-intake revguard-evidence \
  revguard-policy revguard-calculation revguard-rootcause revguard-risk \
  revguard-executor revguard-verifier revguard-knowledge; do
  agt worker ensure-ready --name "$worker"
done
agt get teams   # 预期 revguard-team Active / 9/9
```

API key 不写入 SOUL。Evidence Worker 使用 `agentteams/skills/revguard-api/` 中的只读
Adapter，并从 Worker 的 `.copaw.secret/revguard_api_key` 注入专属 Principal。调用必须携带
`X-AgentTeams-Message-ID` 与 `X-Request-ID`；聊天、日志和 Trace 均不得回显 Bearer 值。
其他 Worker 也应按相同模式各自注入：

```http
Authorization: Bearer <worker-specific-key>
```

建议至少配置 Evidence、Intake、Policy、Calculation、Risk、Executor、Verifier、Knowledge
八个独立 Principal；Executor 仅有 `commission:draft/write/reverse`，Verifier 仅有
`ledger:read`，Approver 使用独立的人类 Principal。

## 5. 验收命令

```bash
make verify-ci
make demo

curl http://127.0.0.1:19000/api/v1/health
curl -H 'Authorization: Bearer rg-demo-viewer-key-1' \
  http://127.0.0.1:19000/api/v1/skills
```

需要逐项核验：

- [ ] `make verify-ci`：70 项测试、覆盖率 ≥75%、105/105 场景、9/9 安全探针；
- [ ] 容器状态 healthy，重启后案件与幂等状态一致；
- [ ] 无认证为 401，错误角色为 403，自报 actor/scope 为 422；
- [ ] L2 在 `WAITING_FOR_APPROVAL` 挂起；
- [ ] 可信 Approver 批准后写入并验证；
- [ ] CASE-0008 首次验证失败后真实冲销，最终 `ROLLED_BACK`；
- [ ] AgentTeams Team Active，9 个 Worker Ready；
- [ ] Matrix 任务事件、Worker 回执、REMOTE_TOOL span 与审计事件的 request ID / receipt 一致；
- [ ] Trace、报告、evaluation summary 和视频中的 case_id 一致。

## 6. 已知资源风险

一次性启动全部 Worker 可能造成演示主机资源尖峰。现场建议预热 Team，或分批 apply；
比赛只要求至少 3 个不同职能 Agent，不应为了数量牺牲 5–8 分钟内的稳定闭环。

2026-08-10 的正式链路证据位于 `submission/evidence/formal-20260810/`。当前 Worker 的
MinIO Matrix password 对象仍缺失，但已持久化 access token 在关闭 E2EE 的部署中完成了
真实消息收发；token 失效前应补齐 password Secret，确保自动重新登录。旧版 2026-08-08
内网实录仅作为历史证据。
