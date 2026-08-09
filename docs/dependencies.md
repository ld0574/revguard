# 第三方依赖、许可证与替代边界

## 直接依赖

| 依赖 | 固定版本 | 用途 | 许可证 | 可替代性 |
|---|---:|---|---|---|
| FastAPI | 0.141.1 | HTTP API 与 OpenAPI | MIT | 可替换为任意 ASGI/HTTP 层，核心闭环不依赖 |
| Uvicorn | 0.52.1 | ASGI Server | BSD-3-Clause | 可替换为 Hypercorn/Gunicorn Worker |
| httpx | 0.28.1 | 仅开发期 ASGI API 测试 | BSD-3-Clause | 不进入核心运行路径 |

核心规则、政策匹配、风险、权限、编排、评测、SQLite 和 Mock Adapter 均使用 Python 标准库。

## 传递依赖

完整解析快照在 `requirements.lock`，包括 Starlette、Pydantic、AnyIO、Click、h11、
httptools、uvloop、watchfiles、websockets、PyYAML 等。构建镜像从 lock 文件安装，
避免 `>=` 在评审时解析到未经验证的新版本。

## 外部系统与商业服务

- AgentTeams 是多 Agent 协同设计基点；仓库不包含其源码或镜像。
- Demo 默认使用本地 Fixture 与 ToolGateway，不调用商业 API，不产生模型费用。
- AgentTeams 现场路径所用 LLM/网关由部署方选择，必须另行披露模型、版本、费用与数据边界。
- 当前无 MCP Server、无向量数据库、无 RAG、无云数据库强依赖；迁移路径见 `README.md`。

## 数据与授权

`data/fixtures` 和 `data/golden_cases` 均为合成演示数据，不包含真实客户、员工或交易记录。
开源发布前应对仓库执行 secret/PII 扫描，并只发布 `revguard/` 目录。
