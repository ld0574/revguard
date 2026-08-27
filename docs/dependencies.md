# 第三方依赖、许可证与替代边界

## 直接依赖

| 依赖 | 固定版本 | 用途 | 许可证 | 可替代性 |
|---|---:|---|---|---|
| FastAPI | 0.141.1 | HTTP API 与 OpenAPI | MIT | 可替换为任意 ASGI/HTTP 层，核心闭环不依赖 |
| Uvicorn | 0.52.1 | ASGI Server | BSD-3-Clause | 可替换为 Hypercorn/Gunicorn Worker |
| psycopg / psycopg-binary | 3.3.4 | PostgreSQL/PolarDB 协议、JSONB/NUMERIC 类型适配 | LGPL-3.0 | 仅正式 DB 路径；本地 SQLite Demo 不调用 |
| psycopg-pool | 3.3.1 | primary/read endpoint 连接池 | LGPL-3.0 | 可替换为部署层连接池，但需保留事务语义 |
| httpx | 0.28.1 | 仅开发期 ASGI API 测试 | BSD-3-Clause | 不进入核心运行路径 |
| Coverage.py | 7.15.2 | 90% 行覆盖率门禁 | Apache-2.0 | 仅开发/CI |
| Ruff | 0.15.22 | 固定规则静态检查 | MIT | 仅开发/CI |
| pip-audit | 2.10.1 | 锁定依赖漏洞审计 | Apache-2.0 | 仅开发/CI |
| Bandit | 1.9.4 | Python 安全静态扫描 | Apache-2.0 | 仅开发/CI |

核心规则、政策匹配、风险、权限、编排、评测、SQLite 和 Mock Adapter 均使用 Python 标准库；只有 PostgreSQL/PolarDB 路径需要 psycopg。

## 传递依赖

完整解析快照在 `requirements.lock`，包括 Starlette、Pydantic、AnyIO、Click、h11、
httptools、uvloop、watchfiles、websockets、PyYAML 等。构建镜像从 lock 文件安装，
避免 `>=` 在评审时解析到未经验证的新版本。

## 外部系统与商业服务

- AgentTeams 是多 Agent 协同设计基点；仓库不包含其源码或镜像。
- Demo 默认使用本地 Fixture 与 ToolGateway，不调用商业 API，不产生模型费用。
- AgentTeams 现场路径所用 LLM/网关由部署方选择，必须另行披露模型、版本、费用与数据边界。
- 当前无 MCP Server、无 RAG；PolarDB 为正式存储适配但仍保留 SQLite 本地复现。pgvector 为独立可选迁移，未达规模门槛前不引入运行时依赖。

## 数据与授权

`data/fixtures` 和 `data/golden_cases` 均为合成演示数据，不包含真实客户、员工或交易记录。
开源发布前应对仓库执行 secret/PII 扫描，并只发布 `revguard/` 目录。CI 使用 Aqua
安全公告明确列出的不可变安全提交
`57a97c7e7821a5776cebc9bb87c984fa69cba8f1`（Trivy 0.69.3）扫描文件系统与构建镜像；
禁止使用可变 `latest`，Trivy 不进入 Python 运行时依赖。

容器构建完成后会移除仅用于构建、运行时不需要的 `setuptools`，避免把其 vendored
工具链及相关攻击面带入最终镜像；该约束由镜像 Trivy HIGH/CRITICAL 门禁验证。
