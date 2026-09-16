# 第三方依赖、许可证与替代边界

## 直接依赖

| 依赖 | 固定版本 | 用途 | 许可证 | 可替代性 |
|---|---:|---|---|---|
| FastAPI | 0.141.1 | HTTP API 与 OpenAPI | MIT | 可替换为任意 ASGI/HTTP 层，核心闭环不依赖 |
| Uvicorn | 0.52.1 | ASGI Server | BSD-3-Clause | 可替换为 Hypercorn/Gunicorn Worker |
| psycopg / psycopg-binary | 3.3.4 | PostgreSQL/PolarDB 协议、JSONB/NUMERIC 类型适配 | LGPL-3.0 | 仅正式 DB 路径；本地 SQLite Demo 不调用 |
| psycopg-pool | 3.3.1 | primary/read endpoint 连接池 | LGPL-3.0 | 可替换为部署层连接池，但需保留事务语义 |
| httpx | 0.28.1 | 仅开发期 ASGI API 测试 | BSD-3-Clause | 不进入核心运行路径 |
| httpx2 | 2.12.0 | Grafana 同源只读代理及 MCP 运行时 HTTP 客户端 | BSD-3-Clause | 替换须保留认证隔离、超时和响应上限 |
| MCP Python SDK | 2.1.1 | 按 Worker 隔离的 MCP Skill 服务与参考客户端 | MIT | 保留 Schema、身份、StageTask 与幂等合同 |
| OpenTelemetry SDK / OTLP HTTP exporter | 1.44.0 | 标准 Trace 与 Collector 导出 | Apache-2.0 | 不得以可采样 Trace 替代资金审计 |
| Coverage.py | 7.15.2 | 90% 行覆盖率门禁 | Apache-2.0 | 仅开发/CI |
| Ruff | 0.15.22 | 固定规则静态检查 | MIT | 仅开发/CI |
| pip-audit | 2.10.1 | 锁定依赖漏洞审计 | Apache-2.0 | 仅开发/CI |
| Bandit | 1.9.4 | Python 安全静态扫描 | Apache-2.0 | 仅开发/CI |

确定性规则与金额使用标准库 Decimal；编排、MCP、API、数据库及遥测另有上述依赖。许可证按 202 已安装的固定版本包元数据核对。前端依赖由 `demo-ui/package-lock.json` 锁定，Docker 构建使用 `npm ci`，发布门禁同时执行 `npm audit`。

## 传递依赖

完整解析快照在 `requirements.lock`，包括 Starlette、Pydantic、AnyIO、Click、h11、
httptools、uvloop、watchfiles、websockets、PyYAML 等。构建镜像从 lock 文件安装，
避免 `>=` 在评审时解析到未经验证的新版本。

## 外部系统与商业服务

- AgentTeams 是多 Agent 协同设计基点；仓库不包含其源码或镜像。
- Demo 默认使用本地 Fixture 与 ToolGateway，不调用商业 API，不产生模型费用。
- AgentTeams 现场路径所用 LLM/网关由部署方选择，必须另行披露模型、版本、费用与数据边界。
- 已实现官方 SDK 参考 MCP Server 和真实 Higress 的 9 个独立角色入口；当前没有 RAG。PolarDB 为正式存储适配，SQLite 保留作 202 Docker 中的参考验证。pgvector 为独立可选迁移，未达规模门槛前不引入运行时依赖。

## 数据与授权

`data/fixtures` 和 `data/golden_cases` 均为合成演示数据，不包含真实客户、员工或交易记录。
开源发布前对变更文件执行凭证扫描，并只向 GitHub 发布 `revguard/` 子树。可复现发布入口为 `scripts/verify_docker.sh`，全部验证在 202 容器中完成。工作区根目录的历史 GitHub Actions 文件不属于公开子树，不能把它们当作当前自动 CI 已执行的证据。Trivy 历史结果见相应日期记录，不代表当前镜像已扫描；Trivy 不进入 Python 运行时依赖。

容器构建完成后会移除仅用于构建、运行时不需要的 `setuptools`，避免把其 vendored
工具链及相关攻击面带入最终镜像。依赖审计与基础镜像扫描是不同范围，验收记录须分别说明。
