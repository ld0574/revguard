# RevGuard 部署与 AgentTeams 联调手册

## 0. 推荐的一键入口

伙伴复现不要分别手工执行本文后续命令，优先从仓库根目录运行：

```bash
# 最小可复现闭环，不要求已安装 AgentTeams
bash scripts/deploy_demo.sh --local --reset

# 与复赛录制机一致；要求宿主机已有 AgentTeams v1.2.0
bash scripts/deploy_demo.sh --full --reset --model MiniMax-M3
```

`--full` 会依次完成私密后端 Principal、PolarDB 启动与 Schema、RevGuard API、AgentTeams 角色和 Team、
skills-only Adapter 的 MinIO 持久化、9 个独立 Higress MCP Server 与精确 consumer 授权、Matrix 登录与独立房间自动发现、8 个 Golden Case
播种以及最终健康验收。重复运行默认保留案件；只有显式传入 `--reset` 才重置合成库。
脚本不会打印 Matrix 或数据库凭证，生成的 `.env` 权限为 `0600`。

新版 L2 审批必须通过白名单 Matrix 账号验证。`--local` 不包含身份源，未配置 Matrix 时
会停在人审，不能使用旧静态 approver key。完整录制方式与账号配置见
[`hitl-mcp-recording.md`](hitl-mcp-recording.md)。

为避免容器重建丢失进程内的后台协程，未传 `--reset` 时如果检测到
`QUEUED / STARTING / RUNNING` 案件，脚本会拒绝重建 API。意外重启后，WebUI
在运行 10 分钟无更新时明确标记“执行已中断”，并由审批人点击“继续执行”。
续跑仅重新签发未消耗组件的 15 分钟能力令牌，已提交写入由持久化幂等键抑制。

## 1. 推荐拓扑

```text
AgentTeams Matrix → 职能 Workers
        │ MCP tools/call + consumer token
        ▼
Higress：9 个 actor-scoped MCP Server（后端凭证托管）
        │ REST-to-MCP → HTTP + 后端 Principal
        ▼
RevGuard API :9000
        ├── Skill Runtime / ToolGateway
        ├── PolarDB primary：Case / Task / StageResult / Audit 写入
        ├── PolarDB read endpoint：列表 / Trace / Metrics / 评测读
        ├── persistent gateway state
        └── reports / case memory
```

Higress 与 RevGuard 同一 Docker 网络时使用 `http://revguard-api.internal:9000`；跨主机部署必须通过
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

录制中如果人工驳回了某个 Golden Case，不需要重置整套案件库。WebUI 的“重新准备当前案件”调用
`POST /api/v1/cases/{case_id}/reprepare`，只清理该案件的证据、任务、Trace、审批与模拟写入，保留原审批审计链，
再从对应 Golden Case 恢复为 `CREATED`；该端点仅在 `REVGUARD_ENABLE_RECORDING_UI=true` 且由 operator 调用时开放。

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

### 3.1 复赛服务器的开源 PolarDB-PG local_instance

录制环境使用 PolarDB 官方 `polardb/polardb_pg_local_instance:15`，只在宿主机
`127.0.0.1:15432-15434` 暴露三个本地节点端口，应用通过 Compose 内网访问 primary。
它用于证明 PostgreSQL 兼容 Store、JSONB、`NUMERIC(18,2)`、事务性 StageResult 和
DB trigger 审计链，不等同于云上共享存储、高可用、备份或 PITR 验收。

```bash
openssl rand -hex 24  # 把结果安全写入服务器 .env 的 REVGUARD_POLARDB_PASSWORD
docker compose \
  -f docker-compose.yml \
  -f docker-compose.agentteams.yml \
  -f docker-compose.polardb.yml \
  up -d polardb-pg

# 创建 revguard 数据库后，以独立 migration principal 执行 001_core.sql，最后再启动 API。
docker compose \
  -f docker-compose.yml \
  -f docker-compose.agentteams.yml \
  -f docker-compose.polardb.yml \
  up -d --build revguard-api
```

录制服务器是独立合成库，可显式设置 `REVGUARD_ALLOW_DATABASE_RESET=true`，使“重新准备”
在同一事务中重建 public schema 并重新播种 8 个 Golden Case。该开关默认关闭；生产应用
principal 不得拥有 schema owner 权限。

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
6. 从 AgentTeams 容器自动发现 Matrix 登录、Orchestrator 控制房间和 9 个 Worker 独立
   room，写入权限为 0600 的 `.env`，再输出 Worker 和 Team 状态。设置
   `INSTALL_WORKER_SKILLS=false` 可只更新 Worker/Team，设置
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

API key 不写入 SOUL。职能 Worker 使用 skills-only Adapter 经 mcporter 调用自己的
Higress MCP Server，只有 consumer token；后端 key 只进入 Higress，旧 Worker key 文件
会移除。完整部署把公开示例的 Worker/dispatcher key 升级为随机私密值，保存在宿主机
0600 `.env`；重复部署保留已有私密值。公开 viewer/operator key 仅为内网演示页面保留，
不得把该演示入口直接暴露公网。Orchestrator 只保留任务派发用的专用凭证。
调用携带 `X-AgentTeams-Message-ID`、`X-Request-ID` 与 Task ID；不得回显任何 Bearer 值。

至少配置 Orchestrator dispatcher 与 Intake、Evidence、Policy、Calculation、RootCause、Risk、
Executor、Verifier、Knowledge 九个 Worker 的独立 Principal；Executor 仅有
`commission:draft/write/reverse`，Verifier 仅有 `ledger:read`，Approver 使用独立的人类
身份的短时动作证明，不再使用静态 approver Principal。

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
