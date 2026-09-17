# NOTICE — RevGuard 归属、第三方组件与许可

本文件回答一个问题：**RevGuard 仓库里哪些是我们写的，哪些是别人的，各自许可状态如何。**
本仓库以 Apache-2.0 发布（见 `LICENSE`），但 Apache-2.0 只覆盖本团队原创代码，
不覆盖下列第三方系统、镜像与数据。

适用版本：`v0.6.0-rc3`。最后更新：2026-09-18。

---

## 1. 归属总览

| 类别 | 归属 | 许可 | 是否随本仓库分发 |
|---|---|---|---|
| `revguard/` 控制面与资金内核 | 本团队原创 | Apache-2.0 | 是 |
| `demo-ui/` 演示前端 | 本团队原创 | Apache-2.0 | 是 |
| `agentteams/workers/*.md`（10 个 Agent SOUL 定义） | 本团队原创 | Apache-2.0 | 是 |
| `agentteams/skills/`、`agentteams/mcp/`（Skill 与 MCP Host 适配） | 本团队原创 | Apache-2.0 | 是 |
| `agentteams/copaw-runtime/`（CoPaw 运行时补丁） | 本团队原创补丁，作用于外部运行时 | Apache-2.0 | 是（补丁脚本） |
| `website/` 项目官网 | 本团队原创 | Apache-2.0 | 是 |
| AgentTeams 平台与镜像 | 阿里云 AgentTeams | 见 §3 | **否** |
| Frappe / ERPNext | Frappe Technologies | GPL-3.0 | **否**（独立容器） |
| Olist 公开数据集 | Olist / Kaggle | CC BY-NC-SA 4.0 | **否**（不随仓库分发） |

**没有上游代码被复制进本仓库。** `revguard/`、`demo-ui/`、`agentteams/` 与 `website/`
均为本团队编写；外部系统一律以独立容器或网络服务形式接入。

---

## 2. 数据与内容许可

| 数据 | 来源 | 许可 | 处理方式 |
|---|---|---|---|
| Olist 巴西电商公开交易数据 | `https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce` | **CC BY-NC-SA 4.0** | 只在本机转换与导入 ERPNext，**不分发原始文件或逐行转换结果**；仓库只保存来源 URL、许可、源 SHA-256、确定性抽样种子与聚合摘要 |
| Etsy / eBay 公开费率页 | 官方费率页 | 页面公开展示 | 只保存来源 URL 与规则快照哈希，见 `docs/data-provenance.md` |
| 主 Demo 业务数据 | 本团队合成 | Apache-2.0 | 渠道、合同、政策、佣金争议均为合成数据 |
| 决赛运行证据 | 本团队系统产生 | Apache-2.0 | 已按脱敏规则去除密钥、Token、Cookie 与内部地址 |

Olist 的 **NC（非商业）** 条款意味着：本项目不会用它做商业再分发；使用该数据集做
验证的第三方需自行遵守 Kaggle 上的原始条款。仓库内不存在该数据集的原始行数据。

---

## 3. 运行期外部系统（独立进程，非链接代码）

`docker-compose*.yml` 引用的镜像与许可：

| 系统 | 镜像 | 许可 | 在 RevGuard 中的角色 |
|---|---|---|---|
| ERPNext | `frappe/erpnext:v16.34.2` | GPL-3.0 | 真实业务事实来源（只读 REST 集成） |
| MariaDB | `mariadb:11.8` | GPL-2.0 | ERPNext 数据库 |
| Redis | `redis:6.2-alpine` | BSD-3-Clause（6.x 分支） | ERPNext 缓存/队列 |
| PostgreSQL | `postgres:16-alpine` / `postgres:15-alpine` | PostgreSQL License | RevGuard 案件库、资金台账、主从复制 |
| PolarDB for PostgreSQL | `polardb/polardb_pg_local_instance:15` | Apache-2.0 | 本地 PolarDB 兼容性验证 |
| Grafana | `grafana/grafana:12.1.1` | AGPL-3.0 | 只读可观测大屏（iframe 同源代理） |
| Loki / Tempo | `grafana/loki:3.5.4` / `grafana/tempo:2.8.2` | AGPL-3.0 | 日志与 Trace 存储 |
| Alloy | `grafana/alloy:v1.10.2` | Apache-2.0 | 日志采集 |
| Prometheus / Alertmanager / Blackbox Exporter | `prom/prometheus:v3.5.0` 等 | Apache-2.0 | 指标、告警、就绪探测 |
| OpenTelemetry Collector | `otel/opentelemetry-collector-contrib:0.132.0` | Apache-2.0 | Trace 采集与导出 |
| AgentTeams 平台 | `agentteams-*` 镜像 | 由 AgentTeams 提供方定义 | 多 Agent 协作运行时（Matrix 房间、Worker、Controller） |
| CoPaw Worker 运行时 | AgentTeams Worker 镜像内置 | 由 AgentTeams 提供方定义 | Worker 进程宿主，本仓库只提供补丁脚本 |

**AGPL-3.0 组件（Grafana / Loki / Tempo）以独立容器运行，通过网络访问，不与
本仓库代码链接。** 若第三方把 Grafana 作为对外服务提供给用户，需要自行遵守
AGPL 的网络服务条款。

Higress 承担 MCP 鉴权与路由，其自身许可由其发行版决定，本仓库只包含配置脚本
（`scripts/setup_higress_mcp_gateway.sh`、`scripts/verify_higress_isolation.py`）。

---

## 4. 语言与库依赖

Python 直接依赖、传递依赖、许可证与可替代性见
[`docs/dependencies.md`](docs/dependencies.md)；完整版本快照见 `requirements.lock`。

前端依赖由 `demo-ui/package-lock.json` 锁定，构建使用 `npm ci`，发布门禁执行
`npm audit`。

发布制品包含：

- `revguard-0.6.0-rc3.cdx.json` —— CycloneDX SBOM（含漏洞与密钥扫描器结果）
- `image-scan-0.6.0-rc3.json` —— Trivy 镜像扫描（可修复 HIGH/CRITICAL 为 0）
- `SHA256SUMS.txt` —— 发行资产校验和

---

## 5. 上游与设计参考的披露

- **AgentTeams** 是多 Agent 协同的设计基点，本仓库**不包含**其源码与镜像；接入方式见
  `docs/agentteams-matrix-acceptance-2026-08-29.md` 与 `agentteams/README.md`。
- **Frappe REST API 与 ERPNext DocType** 作为外部系统按官方文档使用，不做二次封装发行。
- **CoPaw** 作为 AgentTeams Worker 的运行时宿主，本仓库只在
  `agentteams/copaw-runtime/` 提供启动期补丁，并附 `verify_bridge.py` 在补丁不适用时
  失败关闭（fail-closed）。
- 本仓库**没有**基于其它参赛队伍仓库的派生代码。决赛期间的竞品信息仅用于定位与
  自查，不进入本仓库的代码与资产。

---

## 6. 安全与响应

漏洞上报与披露流程见 [`SECURITY.md`](SECURITY.md)。发布门禁
（`scripts/verify_docker.sh`）在 202 Docker 内执行依赖审计、静态扫描、单元与集成测试
以及前端审计。
