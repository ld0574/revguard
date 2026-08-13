# RevGuard — 企业渠道佣金与结算异常多 Agent 协同平台

RevGuard 面向佣金少算、多算、政策版本错配、等级时点冲突与证据不足等企业渠道场景，
在 AgentTeams 上构建可审批、可验证、可回滚、可审计的多 Agent 闭环。

核心原则：当前 Demo 的 Normalize、Explain、金额、政策匹配、风险分级、权限和状态流转
都由确定性代码完成；语言模型是边界明确的后续 Adapter，不把路线图包装成现状。

## 已验证能力

- 1 Manager + 9 个职能 Worker，共 10 Agent；Executor 与 Verifier 强制分离。
- 16 个版本化 Skill，均有统一调用入口、允许身份、运行时 JSON Schema 输入/输出校验、
  失败处理与复用说明。
- 7 路独立 I/O 真实并行采集，政策查询在合同返回后按依赖继续执行。
- L0 只读、L1 仅建不生效草稿、L2 人工审批后写入、L3 禁止自动执行。
- HMAC-SHA256 能力令牌绑定案件、币种、总额、逐组件额度、用途、过期时间与唯一 JTI。
- 验证失败时真实创建反向台账，再由 Verifier 独立确认恢复执行前净额。
- 8 个端到端 Golden Case；105 个确定性场景（8 Golden + 80 风险 + 8 政策 + 9 安全）；
  83 项单元、集成与 API 测试，实测 91% 行覆盖率，门禁为 90%。
- 19 状态、24 条普通迁移的显式白名单；SQLite WAL + keyset 分页；支持干净重置、
  重复 seed 与容器重启。
- 真实 Matrix → Orchestrator StageTask → Intake Skill 已用 message/request/task/receipt ID、
  Trace 与 Audit 对账；旧 Matrix → Evidence → Tool 链保留为历史证据。
- 持久化 StageTask/StageResult 桥接绑定 case version、Skill、Worker actor 和输入快照；
  支持补证后从 `WAITING_FOR_EVIDENCE` 恢复。

最新可复现指标见 [`docs/evaluation-summary.json`](docs/evaluation-summary.json)。
可直接审阅正常闭环报告 [`CASE-2026-0001.md`](docs/reports/CASE-2026-0001.md)
与故障回滚报告 [`CASE-2026-0008.md`](docs/reports/CASE-2026-0008.md)；完整 Trace 可由
`make demo-reset` 同步重建。

## 核心链路

```text
案件受理 → 实体解析 → 并行取证 → 政策 Time Travel → Decimal 复算
       → 根因解释 → L0-L3 风险 → 人工审批 → 受控写入
       → 独立验证 ─失败→ 反向冲销 → 回滚验证
       → Trace / 报告 / Dataset 沉淀
```

## 一键复现

需要 Python 3.11+。

```bash
cd revguard
make setup
make verify-ci    # 固定 Ruff + 83 项测试 + 90% 覆盖率门禁 + 105 场景评测 + 生成物校验
make security     # pip-audit + Bandit；CI 另执行 Trivy 文件系统与镜像扫描
make demo         # 干净重置并运行 8 个 Golden Case
```

运行产物：

- `docs/reports/CASE-*.md`：证据、政策、公式、审批、执行、回滚与审计报告；
- `data/outputs/traces/CASE-*.json`：Agent / Skill / Tool / Approval / Execution Trace；
- `data/outputs/case_memory/*.json`：Golden、Bad 与 Safe-Rollback 评测样本；
- `data/outputs/evaluation_summary.json`：运行时评测产物（忽略目录）；
- `docs/evaluation-summary.json`：含 UTC、环境、重复次数、中位数与样本的发布快照。

核心编排与评测只使用 Python 标准库；FastAPI/Uvicorn 仅用于 API 层。
`requirements.lock` 固定完整运行时依赖，`requirements-dev.txt` 增加 API 测试依赖。

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

- `POST /api/v1/cases/{id}/run`：运行确定性闭环；
- `POST /api/v1/cases/{id}/approval`：可信 Approver 决策并自动续跑；
- `POST /api/v1/cases/{id}/evidence/resume`：补证后重新进入状态机；
- `POST /api/v1/cases/{id}/agent-tasks`：派发状态绑定的 Agent StageTask；
- `POST /api/v1/skills/{skill}/invoke`：版本化 Skill 调用入口；
- `POST /api/v1/tools/call`：默认关闭的历史兼容入口；启用后也只允许 Evidence 身份调用只读工具；
- `GET /api/v1/cases?limit=50&cursor=...`：稳定 keyset 分页；
- `GET /api/v1/cases/{id}/trace`：Trace 回放；
- `GET /api/v1/cases/{id}/report`：审计报告。

完整示例见 [`docs/api.md`](docs/api.md)。

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

容器使用非 root 用户、只读根文件系统、无 Linux capabilities、资源限制和健康检查。
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
│   ├── state_machine.py  # 状态迁移白名单与终态不变量
│   ├── orchestrator.py   # 阶段编排、审批、执行、验证与回滚
│   ├── rule_engine.py    # Decimal 确定性规则引擎
│   ├── policy_matcher.py # 严格日期解析与政策 Time Travel
│   ├── mocks.py          # 最小权限 ToolGateway 与持久化 Mock
│   ├── store.py          # 线程安全 SQLite Store
│   ├── trace.py          # 可回放 Trace
│   └── api.py            # FastAPI 服务
├── agentteams/           # Worker SOUL、few-shot Playbook 与 skills-only API Adapter
├── data/golden_cases/    # 8 个端到端场景
├── docs/                 # API、Agent、Skill、部署、评测与报告
├── scripts/              # seed、demo、evaluation、AgentTeams setup
└── tests/                # 83 项自动测试
```

## MCP、RAG 与替代机制

当前版本没有为了数量而引入 MCP 或 RAG：

- ToolGateway 已覆盖鉴权、Schema、结构化错误、重试、幂等、审计与降级；迁移 MCP
  时只替换协议 Adapter，不重写 Skill 或编排器。
- 金额与政策判断依赖精确业务事实，不采用语义检索；上下文由 Shared Case State、
  Case Memory 与 Trace 三层承载。后续仅在政策条款自然语言检索等适合场景引入 RAG。

## 开源状态

本项目已作为公开仓库发布，采用 Apache-2.0 LICENSE。依赖/许可证边界、OpenAPI、6 条 ADR、
安全工作流和发布材料均已纳入仓库。见 [`LICENSE`](LICENSE)、
[`docs/dependencies.md`](docs/dependencies.md) 与 [`docs/adr/`](docs/adr/README.md)。

公开地址：<https://github.com/ld0574/revguard>。
