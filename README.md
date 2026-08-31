# RevGuard — 面向企业渠道佣金结算异常的多智能体治理平台

## 场景背景

企业通常通过代理商、经销商和服务商销售产品，再按照订单、回款、合作等级和阶段政策，
向渠道伙伴支付佣金（销售提成）。一笔结算可能同时受政策版本、订单时间、回款状态、
退款和激励条款影响；相关数据又分散在 CRM、合同、财务、佣金和工单系统中。
因此，少算、多算、漏算或错用规则不仅难以及时发现，还会直接形成资金损失和对账争议。

RevGuard 将这类异常处理做成一条可复核的协作流程：从受理问题开始，收集相关证据，
找出正确规则，重新计算金额，说明差异原因，判断风险，经过审批后执行，并在执行后独立验证。
它不是只给出一个答案，而是把“为什么这样算、谁可以批准、实际改了什么、结果是否恢复”
都留下可追溯记录。

## 先看懂几个词

| 术语 | 本项目中的含义 |
|---|---|
| 渠道伙伴 | 代理商、经销商、门店或服务商等企业外部销售合作方。 |
| 佣金 | 企业根据销售、回款或激励规则支付给渠道伙伴的提成。 |
| 政策 / 政策版本 | 规定佣金比例、适用条件和生效时间的规则；同一政策可能随季度或日期变化。 |
| 等级 | 渠道伙伴的合作级别，例如 GOLD、SILVER；等级会影响佣金比例，但必须按订单发生时点判断。 |
| 证据 | 支撑“这笔佣金是否正确、应该是多少”的订单、合同、回款、退款和审批记录。 |
| 案件 | 一次需要调查、判断、处理和验证的佣金或结算异常。 |
| Skill | 一个有固定输入、输出和权限边界、可由多个 Agent 复用的任务能力。 |
| MCP | Agent 与 Skill 之间的标准协议层；本项目按 Worker 隔离可见 Skill，并要求绑定 StageTask。 |
| Adapter | 连接 Agent 与 CRM、财务、工单等外部系统的适配层；它负责传递请求，不负责替代业务规则。 |

## 系统边界

当前 Demo 把“理解问题”和“动用资金”明确分开：案件解析、政策匹配、金额计算、差异解释、
风险分级、权限和状态流转均由可重复的确定性代码完成。AgentTeams Worker 使用语言模型
理解并执行被绑定的 StageTask，但模型不能自行推进案件状态、计算金额或直接写资金台账；
实际业务结果仍由服务端确定性 Skill 与权限边界裁决。

## 四重约束：Agent 不能绕过的四道边界

这四重约束是项目自身的安全设计归纳，每一重都对应服务端机制和可复核证据：

| 约束 | 代码机制 | 可复核证据 |
|---|---|---|
| **任务不漂移** | 状态迁移白名单、Skill/状态绑定、case version 快照和 StageTask 约束任务范围。 | 非法迁移测试、旧任务失效测试、`STATE_TRANSITION` 审计事件。 |
| **审批不自签** | 带外 Matrix 账号验证、绑定案件/审批单/决定的 120 秒身份证明；拒绝静态审批 key；L2 短时执行能力、L3 禁止自动执行。 | 人类 subject/认证时间审计、跨案件/动作拒绝测试及回滚案例。 |
| **额度不外溢** | 案件、币种、总额和逐组件额度绑定；幂等键防重复写入；冲销令牌一次性使用。 | 跨组件额度、并发双写和令牌重放安全探针。 |
| **权限不升级** | Bearer 请求映射为服务端 Principal；每个 Skill 有 actor 白名单和最小 scope；响应与 Trace 脱敏。 | 401/403/422 边界测试和嵌套字符串脱敏测试。 |

风险分级回答“这笔异常最多允许处理到哪一步”，四重约束回答“Agent 为什么不能绕过这一步”。

## 已验证能力

- 1 个 Orchestrator 加 9 个职能 Worker，共 10 个 Agent；Executor 负责受控写入，Verifier 独立复核。
- 16 个版本化 Skill，每个 Skill 都有统一调用入口、允许身份、输入/输出格式、失败处理和复用说明。
- 录制环境由真实 AgentTeams/Matrix 驱动状态型 Team 流程：Team room 做 Orchestrator
  协同任务编排，9 个 Worker 独立 room 经 skills-only Adapter 调用各自的 Higress MCP Server；本地官方 MCP Client/Server
  保留为可复现 reference harness。底层 Tool 不暴露给模型，错 Worker、错 Skill、篡改输入、
  过期 task 和重放都会拒绝。
- CASE-0008 实测 20 个成功 StageTask、9 个 Worker、16 种 Skill；L2 在 WebUI 真实暂停，
  人工批准后从持久化状态续跑并完成 `FAILED → ROLLED_BACK → rollback PASSED`。
- 7 路独立 I/O 真实并行取证；政策查询会在合同证据返回后按依赖继续执行。
- L0 只读，L1 只创建不生效的草稿，L2 经过人工审批后写入，L3 只给出方案、禁止自动执行。
- HMAC-SHA256 能力令牌把案件、币种、总额、逐组件额度、用途、有效期和唯一编号绑定在一起。
- 执行后验证失败时，系统会创建反向台账，再由 Verifier 独立确认金额恢复到执行前状态。
- 8 个端到端 Golden Case；105 个确定性场景（8 Golden + 80 风险 + 8 政策 + 9 安全）；
  自动化测试覆盖内核、MCP、API、状态桥接、安全和持久层，核心路径行覆盖门禁为 90%。
- 19 状态、24 条普通迁移的显式白名单；SQLite WAL + keyset 分页；支持干净重置、
  重复 seed 与容器重启。
- 真实 Matrix → Orchestrator handshake → Worker StageTask → Skill 已用
  room/message/request/task/receipt/trace ID 与 Audit 对账；旧 Matrix → Evidence → Tool 链保留为历史证据。
- 持久化 StageTask/StageResult 桥接绑定 case version、Skill、Worker actor 和输入快照；
  Task 终态与每次 StageResult 同事务落库，支持失败重试、显式重派和 lineage。
- 正式持久化可切换到 PostgreSQL/PolarDB：金额使用 `NUMERIC(18,2)`，审计事件由
  DB trigger 强制 append-only 哈希链，列表/Trace/Metrics 可分流到只读端点。
- 可查询 JSON/Prometheus Metrics、JSON 访问日志、liveness/readiness、灰度/回滚策略、
  告警规则、容量探针和 PolarDB PITR 证据捕获脚本。
- 10 个合成伙伴、11 笔订单和 8 个案件带来源边界、关联/时序/币种检查与源文件哈希；
  录制服务器已运行官方开源 PolarDB-PG 15 local_instance，所有材料明确区分“合成业务
  数据”“真实执行链路”“开源单机 PolarDB-PG 已验收”“云 PolarDB 高可用/PITR 待验收”。

最新可复现指标见 [`docs/evaluation-summary.json`](docs/evaluation-summary.json)。
录制服务器的 20/20 AgentTeams/Matrix 脱敏验收结果见
[`docs/agentteams-matrix-acceptance-2026-08-29.md`](docs/agentteams-matrix-acceptance-2026-08-29.md)。
评委意见的逐条实施状态见 [`docs/reviewer-remediation.md`](docs/reviewer-remediation.md)；
合成价值数据只验证指标口径，文件内强制标记“不得作为企业真实收益”。驾驶舱新增
可交互的“价值模拟”页签，可按月案件量和综合人工成本试算释放工时与人工经费空间；
公式、默认假设和生产使用边界见 [`docs/value-simulation.md`](docs/value-simulation.md)。
可直接审阅正常闭环报告 [`CASE-2026-0001.md`](docs/reports/CASE-2026-0001.md)
与故障回滚报告 [`CASE-2026-0008.md`](docs/reports/CASE-2026-0008.md)；完整 Trace 可由
`make demo-reset` 同步重建。

## 核心链路

```text
案件受理 → 实体解析 → 并行取证 → 按业务时点匹配政策 → 精确金额复算
       → 解释差异原因 → 风险分级 → 人工审批 → 受控写入
       → 独立验证 ─失败→ 反向冲销 → 再次验证
       → Trace / 报告 / Dataset 沉淀
```

其中，Trace 是每一步处理的时间线记录，Audit 是面向审计的关键操作记录，Dataset 是可用于
复盘和评测的案例数据；三者共同回答“系统做了什么、依据是什么、结果能否重放”。

## 一键复现

需要 Python 3.11+。

如果目标是直接打开可录制 WebUI，而不是搭建开发环境，使用部署总入口：

```bash
# 任意安装 Docker Compose v2 的机器：SQLite + 本地 MCP Team
bash scripts/deploy_demo.sh --local --reset

# 已安装 AgentTeams v1.2.0 的宿主机：PolarDB + Matrix + 10 个 Agent 角色
bash scripts/deploy_demo.sh --full --reset --model MiniMax-M3
```

脚本会生成权限为 `0600` 的本地 `.env`，完成镜像构建、Schema、Golden Case、
Worker Adapter、9 个精确授权的 Higress MCP Server、Matrix 房间和健康验收；不会输出或提交凭证。`--full` 建议为 Docker
分配至少 6 GiB 内存。部署完成后访问 `http://<宿主机>:19000/demo/`。

L2 审批现在要求登录白名单中的 AgentTeams Matrix 账号，不能使用旧 approver key。
`--local` 不自带身份提供方，若未配置 Matrix，只能运行到人审暂停；完整自动化内核验证
使用 `make verify-ci`。账号配置、录制边界见 [`docs/hitl-mcp-recording.md`](docs/hitl-mcp-recording.md)。

开发与完整门禁使用以下命令：

```bash
cd revguard
make setup
make verify-ci    # Ruff + 自动化测试（PG 条件项除外）+ 90% 覆盖率门禁 + 105 场景评测 + 生成物校验
make competition-verify # 在 verify-ci 上增加依赖安全审计、WebUI 构建与脱敏证据包重放
make value-evaluate # 运行五类业务价值指标口径（当前为明确标注的合成数据）
make synthetic-validate # 校验合成数据血缘、引用、时序、币种和源文件哈希
make evidence-bundle # 重放 MCP Team 并生成脱敏可审计证据包
make capacity     # 本地合成容量回归，不冒充 PolarDB 生产 SLO
make security     # pip-audit + Bandit；CI 另执行 Trivy 文件系统与镜像扫描
make demo         # 干净重置并运行 8 个 Golden Case
```

运行产物：

- `docs/reports/CASE-*.md`：证据、政策、公式、审批、执行、回滚与审计报告；
- `data/outputs/traces/CASE-*.json`：Agent / Skill / Tool / Approval / Execution Trace；
- `data/outputs/case_memory/*.json`：Golden、Bad 与 Safe-Rollback 评测样本；
- `data/outputs/evaluation_summary.json`：运行时评测产物（忽略目录）；
- `docs/evaluation-summary.json`：含 UTC、环境、重复次数、中位数与样本的发布快照。
- `docs/evidence/demo-rehearsal/`：MCP Task、人审暂停、Audit、Trace、报告与证据哈希清单。
- `docs/demo-script.md` / `docs/recording-shot-list.md`：复赛旁白、镜头和组员分工。
- `docs/runtime-acceptance-2026-08-31.md`：最新运行验收、CASE-0002 恢复结果与正式录制前提。

核心编排与评测只使用 Python 标准库；FastAPI/Uvicorn 仅用于 API 层。
`requirements.lock` 固定完整运行时依赖，`requirements-dev.txt` 增加 API 测试依赖。
90% 行覆盖率门禁覆盖默认可复现的内核与 SQLite/API 路径；
`postgres_store.py` 需真实 PostgreSQL 事务/触发器，不纳入无 DB 的行覆盖率分母，改由
`make postgres-integration REVGUARD_TEST_POSTGRES_DSN='...'` 在一次性数据库上单独门禁。

## API 与身份边界

安全默认值是 fail-closed：API 启动必须提供签名密钥和 Principal 配置。
本地演示可显式启用 `config/demo_principals.json` 中的公开 Demo principals：

```bash
make run
```

生产环境复制 `.env.example`，配置：

- `REVGUARD_APPROVAL_SIGNING_KEY`：至少 32 字节；
- `REVGUARD_API_KEYS_JSON`：API key 到可信 actor、roles、scopes 的服务端映射；
- `REVGUARD_GATEWAY_STATE_PATH`：Mock 台账、审批、幂等和回执的持久化文件。

请求使用 `Authorization: Bearer <api-key>`。请求体不能自报 `actor` 或 `scope`。

```bash
curl -H 'Authorization: Bearer rg-demo-viewer-key-1' \
  http://127.0.0.1:9000/api/v1/skills
```

主要接口：

- `POST /api/v1/cases/{id}/run`：运行确定性回放闭环；
- `POST /api/v1/cases/{id}/team/run`：通过 scoped MCP 运行多 Worker 状态流，L2 停在人审；
- `POST /api/v1/cases/{id}/team/resume`：审批人为超时的 Matrix 执行重新授权，按持久化幂等键续跑；
- `POST /api/v1/cases/{id}/approval`：可信 Approver 决策并自动续跑；
- `POST /api/v1/cases/{id}/evidence/resume`：补证后重新进入状态机；
- `POST /api/v1/cases/{id}/agent-tasks`：派发状态绑定的 Agent StageTask；
- `POST /api/v1/skills/{skill}/invoke`：版本化 Skill 调用入口；
- `POST /api/v1/tools/call`：默认关闭的历史兼容入口；启用后也只允许 Evidence 身份调用只读工具；
- `GET /api/v1/cases?limit=50&cursor=...`：稳定 keyset 分页；
- `GET /api/v1/cases/{id}/trace`：Trace 回放；
- `GET /api/v1/cases/{id}/report`：审计报告。
- `GET /api/v1/agent-tasks/{task_id}/results`：查询每次 StageResult；
- `POST /api/v1/agent-tasks/{task_id}/reassign`：受权调度员重派失败任务；
- `GET /api/v1/ops/metrics[ /prometheus]`：可查询运营与审计链指标。
- `GET /api/v1/ops/evidence`：录制 WebUI 使用的工程门禁、价值口径与外部验收状态。

完整示例见 [`docs/api.md`](docs/api.md)。
正式 PolarDB 迁移、主/只读路由、pgvector 决策门槛和 PITR 验收见
[`docs/polardb-production.md`](docs/polardb-production.md)；发布与运维见
[`docs/operations.md`](docs/operations.md)。

安全门禁与 2026-08-12 实扫处置记录见
[`docs/security-scan-2026-08-12.md`](docs/security-scan-2026-08-12.md)。
机器契约见 [`docs/openapi.json`](docs/openapi.json)，其 OpenAPI 3.1
`x-revguard-skill-registry` 与 16 个 Skill 注册表同源并受漂移校验。

## Docker

```bash
docker compose up -d --build
curl http://127.0.0.1:19000/api/v1/health
```

与 AgentTeams 同机时使用 `docker-compose.agentteams.yml` 把 API 接入 Worker 网络，详见
[`docs/deployment.md`](docs/deployment.md)。

基础 Compose 默认使用 SQLite Demo Store；叠加 `docker-compose.polardb.yml` 后运行官方
PolarDB-PG local_instance，并通过 `REVGUARD_DATABASE_URL` 切换为 PolarDB Store。容器使用
非 root 用户、只读根文件系统、无 Linux capabilities、资源限制和健康检查。
设置 `REVGUARD_RESET_ON_START=true` 可在评审前原子清空全部 Demo 状态再 seed；默认保留状态。

## 目录结构

```text
revguard/
├── revguard/
│   ├── security.py       # RBAC、API Principal、签名能力令牌
│   ├── skill_runtime.py  # 16 个 Skill 的版本化运行时
│   ├── skills.py         # Skill 实现与注册中心
│   ├── skill_schemas.py  # 16 个 Skill 的 JSON Schema 单一事实源
│   ├── agent_bridge.py   # StageTask/StageResult 与 case version 绑定
│   ├── mcp_server.py     # 每 Worker 隔离、任务绑定的 MCP Skill Server
│   ├── mcp_team.py       # 状态驱动 MCP Team 可执行参考编排
│   ├── state_machine.py  # 状态迁移白名单与终态不变量
│   ├── orchestrator.py   # 阶段编排、审批、执行、验证与回滚
│   ├── rule_engine.py    # Decimal 确定性规则引擎
│   ├── policy_matcher.py # 严格日期解析与政策 Time Travel
│   ├── mocks.py          # 最小权限 ToolGateway 与持久化 Mock
  │   ├── store.py          # 本地 SQLite Store + 存储工厂
  │   ├── postgres_store.py # PostgreSQL/PolarDB 主/只读连接池适配
│   ├── trace.py          # 可回放 Trace
│   └── api.py            # FastAPI 服务
├── agentteams/           # Worker SOUL、MCP Host 示例与 REST 兼容 Adapter
├── data/golden_cases/    # 8 个端到端场景
├── migrations/polardb/  # 核心 Schema 与可选 pgvector 迁移
├── docs/                 # API、Agent、PolarDB、运维、评测与报告
├── scripts/              # seed、demo、evaluation、AgentTeams setup
└── tests/                # 自动化测试（含需一次性 PostgreSQL 的条件测试）
```

## MCP 与 RAG 边界

完整部署使用 Higress REST-to-MCP：9 个独立 Server 分别授权给对应 Worker consumer，
后端 key 不下发给职能 Worker。每次调用必须绑定不可漂移的 StageTask；本地 stdio
Server 仅作为参考测试。配置与验证见 [`agentteams/mcp/README.md`](agentteams/mcp/README.md)。
MCP 与 REST 共用同一套 Schema、执行器、状态机、权限、StageResult 事务与 Audit。详见
[`docs/adr/0007-scoped-mcp-skill-transport.md`](docs/adr/0007-scoped-mcp-skill-transport.md)。

金额与政策判断依赖精确业务事实，当前不采用语义检索；上下文由 Shared Case State、
Case Memory 与 Trace 三层承载。只在自然语言政策规模和离线 recall@k 证明收益后考虑 RAG。

## 开源状态

本项目已作为公开仓库发布，采用 Apache-2.0 LICENSE。依赖/许可证边界、OpenAPI、6 条 ADR、
安全工作流和发布材料均已纳入仓库。见 [`LICENSE`](LICENSE)、
[`docs/dependencies.md`](docs/dependencies.md) 与 [`docs/adr/`](docs/adr/README.md)。

公开地址：<https://github.com/ld0574/revguard>。
