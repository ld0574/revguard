# RevGuard 部署与 AgentTeams 联调手册

## 1. 推荐拓扑

```text
AgentTeams Orchestrator / Workers
        │  HTTPS + Bearer Principal（由 Secret/Adapter 注入）
        ▼
RevGuard API :9000
        ├── Skill Runtime / ToolGateway
        ├── PolarDB primary：Case / Task / StageResult / Audit 写入
        ├── PolarDB read endpoint：列表 / Trace / Metrics / 评测读
        ├── persistent gateway state
        └── reports / case memory
```

与 AgentTeams 同一 Docker 网络时使用 `http://revguard-api:9000`；跨主机部署必须通过
TLS Gateway 暴露，并配置限流、访问日志与网络白名单。SOUL 使用
`{{REVGUARD_API_BASE_URL}}`，`agentteams_setup.sh` 在部署时渲染，不再硬编码 IP。

## 2. 本地 Docker（SQLite Demo）

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

默认保留 volume 中的案件、Mock 台账、审批、幂等、回执、报告与 Trace。这一模式专为录屏和评委本地复现，不当作正式审计库。需要评委从
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

## 3. PolarDB 正式存储

先以独立 migration principal 运行 `scripts/migrate_polardb.py`，再为应用配置
`REVGUARD_DATABASE_URL` 和可选的 `REVGUARD_READ_DATABASE_URL`。生产必须保持
`REVGUARD_AUTO_MIGRATE=false`，不得向应用账号授予 DDL 或禁用审计触发器的权限。

PolarDB 模式下 `REVGUARD_RESET_ON_START=true` 会直接拒绝启动，以防演示重置语义进入正式审计库。金额的 Decimal → `NUMERIC(18,2)` 语义保持、哈希链、读写路由和 PITR 验收见 [`polardb-production.md`](polardb-production.md)。

## 4. 生产安全配置

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

## 5. AgentTeams Worker 与 Team

```bash
REVGUARD_HOME=/absolute/path/to/revguard \
REVGUARD_API_BASE_URL=http://revguard-api:9000 \
bash scripts/agentteams_setup.sh
```

脚本会：

1. 在临时目录渲染 SOUL 中的 API Base URL；
2. `agt apply worker` 创建或更新 1 个 Orchestrator 与 9 个职能 Worker；
3. 以 `revguard-orchestrator` 为 leader 组建 Team；
4. 等待 Worker Ready，并向各 Worker 安装 skills-only `revguard-api` Adapter；
5. 同步 CoPaw 激活模型并做 AI Gateway 请求预检；
6. 把 9 个 Worker 的独立 Matrix room 映射写入权限为 0600 的 `.env`，再输出 Worker 和
   Team 状态。设置 `INSTALL_WORKER_SKILLS=false` 可只更新 Worker/Team，设置
   `CONFIGURE_MATRIX_WORKER_ROOMS=false` 可跳过房间映射。

当前 CoPaw 运行时会在约 30 分钟空闲后把 Worker 自动置为 `Sleeping`，这是资源回收而非
故障。现场演示前可显式预热：

```bash
for worker in revguard-orchestrator revguard-intake revguard-evidence \
  revguard-policy revguard-calculation revguard-rootcause revguard-risk \
  revguard-executor revguard-verifier revguard-knowledge; do
  agt worker ensure-ready --name "$worker"
done
agt get teams   # 预期 revguard-team Active / 1 Orchestrator + 9 Worker Ready
```

API key 不写入 SOUL。所有 Worker 使用 `agentteams/skills/revguard-api/` 中的 skills-only
Adapter，并从各自 `.copaw.secret/revguard_api_key` 注入专属 Principal。调用必须携带
`X-AgentTeams-Message-ID` 与 `X-Request-ID`；聊天、日志和 Trace 均不得回显 Bearer 值。
其他 Worker 也应按相同模式各自注入：

```http
Authorization: Bearer <worker-specific-key>
```

至少配置 Orchestrator dispatcher 与 Intake、Evidence、Policy、Calculation、RootCause、Risk、
Executor、Verifier、Knowledge 九个 Worker 的独立 Principal；Executor 仅有
`commission:draft/write/reverse`，Verifier 仅有 `ledger:read`，Approver 使用独立的人类
Principal。

新版部署保持 `REVGUARD_ENABLE_LEGACY_TOOL_API=false`。只有复放 2026-08-10 的历史
`Matrix → /tools/call` 证据时才临时设为 `true`；复放结束应恢复关闭。

## 6. 验收命令

```bash
make verify-ci
make demo

curl http://127.0.0.1:19000/api/v1/health
curl -H 'Authorization: Bearer rg-demo-viewer-key-1' \
  http://127.0.0.1:19000/api/v1/skills
```

需要逐项核验：

- [ ] `make verify-ci`：固定 Ruff、自动测试、覆盖率 ≥90%、105/105 场景、9/9 安全探针、生成物无漂移；
- [ ] `make security`：锁定依赖无已知漏洞，Bandit 无未解释问题；CI 另跑 Trivy 文件系统与镜像扫描；
- [ ] 容器状态 healthy，重启后案件与幂等状态一致；
- [ ] 无认证为 401，错误角色为 403，自报 actor/scope 为 422；
- [ ] L2 在 `WAITING_FOR_APPROVAL` 挂起；
- [ ] 可信 Approver 批准后写入并验证；
- [ ] CASE-0008 首次验证失败后真实冲销，最终 `ROLLED_BACK`；
- [ ] AgentTeams Team Active，1 Orchestrator + 9 Worker Ready；
- [ ] Team room 完成 Orchestrator 握手；20 个 Worker StageTask 进入各自独立 room，避免共享上下文污染；
- [ ] Matrix 任务事件、StageTask、Worker 回执、SKILL span 与审计事件的 task ID / request ID / receipt 一致；
- [ ] Trace、报告、evaluation summary 和视频中的 case_id 一致。
- [ ] PolarDB 模式的 `/health/ready` 返回 primary/read endpoint 就绪，Metrics 的 audit chain valid=1；
- [ ] 目标集群完成过一次有任务 ID、expected/actual 指纹和签署的 PITR 演练。

## 7. 已知资源风险

一次性启动全部 Worker 可能造成演示主机资源尖峰。现场建议预热 Team，或分批 apply；
比赛只要求至少 3 个不同职能 Agent，不应为了数量牺牲 5–8 分钟内的稳定闭环。

2026-08-10 的正式链路证据位于 `submission/evidence/formal-20260810/`，只作为历史
Matrix → Evidence → legacy Tool 调用事实。2026-08-12 的新版证据位于
`submission/evidence/formal-20260812-stagetask/`，用于验证 Matrix → Orchestrator 派发 →
Intake StageTask → Skill receipt → Trace/Audit → Matrix response；它不被扩大表述为外部十阶段推进。

历史 Worker 的
MinIO Matrix password 对象仍缺失，但已持久化 access token 在关闭 E2EE 的部署中完成了
真实消息收发；token 失效前应补齐 password Secret，确保自动重新登录。旧版 2026-08-08
内网实录仅作为历史证据。
