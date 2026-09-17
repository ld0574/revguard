# RevGuard 可观测组件与验收

Higress承担MCP发现、鉴权和路由。观测后端使用OpenTelemetry SDK → Collector → Tempo、Prometheus → Alertmanager、Alloy → Loki，并由Grafana统一查询。财务审计单独随账务事务提交，不以可采样的Trace替代。

## 关联和指标

- 标准OTel Trace使用W3C trace ID。case_id、run_id、task_id及资金operation_id作为关联属性；HTTP、MCP与Worker适配器传递traceparent，审批暂停后保留案件关联上下文。
- 案件JSON回放中的兼容字段trace_id=case_id仍保留，它不是标准OTel trace ID。内部Trace映射保守的GenAI语义属性，不虚构模型、Token数量或推理参数。
- HTTP请求时延使用路由模板、方法和状态类别作标签，案件编号不会进入Prometheus标签。另有未确认资金操作数、冻结通道数和审计链状态。
- Blackbox独立探测readiness。业务库故障时，应用指标可能抓取失败，此时通过up、probe_success和缺失数据规则告警。readiness目标的up=1只代表探针采集器在线，须同时检查probe_success。
- Collector启用容量有界的磁盘队列；应用使用有界异步导出。导出失败不阻断资金线程，业务库Trace保存失败不覆盖原始异常。必要资金审计失败仍必须回滚资金事务。
- 只采集关联标识和安全属性，不向OTLP发送原始金额载荷、密码或能力令牌。Alloy只采集同一Compose项目的RevGuard API日志。

## 在202部署

所有构建、测试和服务运行均在10.10.10.202的Docker中执行；本地只编辑、同步文件。Compose配置为docker-compose.observability.yml，Prometheus原生规则位于config/observability/alerts.yaml；旧config/alerts.yaml是项目说明格式，不作为Prometheus加载文件。

运行scripts/prepare_observability.py会保留现有身份，新增只读metrics凭证，并把凭证及Grafana密码保存到忽略版本控制的.runtime/observability/。Grafana默认只监听202的127.0.0.1:13001。不要在仓库、日志或答辩截图中展示密码。

部署入口scripts/deploy_observability.sh --full组合PolarDB、AgentTeams和观测配置；升级资金Schema前先备份并运行独立迁移。**不要带reset参数，也不要在原演示环境注入故障。** 告警当前只保留在本地Alertmanager，未配置邮件或即时消息通知。

## demo-ui 内嵌 Grafana

RevGuard 0.6.0-rc3 的「可观测大屏」入口为 `http://10.10.10.202:19088/demo/?view=observability`（彩排栈）与 `http://10.10.10.202:19000/demo/?view=observability`（常驻栈），两栈复用同一块只读共享看板。这是全部案件的只读视图，支持手动刷新、全屏和独立打开。23 个面板展示 API 采集、数据库就绪、审计链、待对账操作、冻结通道、告警、请求速率、P95 延迟、资金结果恢复、服务可用性、案件状态、Agent 任务状态、ERPNext 调用与延迟、Agent 模型调用与 Token、模型超时、PostgreSQL 副本健康与复制延迟、数据库连接与锁等待、审计事件增长与 Evidence Gap、资金操作状态、冲销与恢复。指标来自实际 Prometheus 采集，业务案件仍为合成演示数据；未采集时显示缺失状态。

页面通过同源 `/grafana/` iframe 访问唯一的 externally shared dashboard（UID `revguard-operations`）。Grafana 匿名 Viewer 登录保持关闭，管理员密码只供短时配置容器使用，不进入 API 服务或浏览器。代理仅允许该看板的公开 HTML、保存面板查询和静态资源；管理员、登录、通用数据源查询及写入接口均拒绝，浏览器的 Authorization/Cookie 不转发，Grafana Set-Cookie 不回传。共享链接本身可被持有者读取，因此该看板仅放置允许展示的演示运行指标。

`docker-compose.observability.yml` 已配置子路径、允许同源嵌入和 token 文件挂载。`scripts/deploy_observability.sh --full` 会在 Docker 中准备凭证、部署组件，再运行 `scripts/prepare_grafana_embed.py` 为选定看板开启共享并保存标识到 `.runtime/observability/grafana-public-token`。该文件不是管理员密钥，但不纳入 Git。更换访问地址时设置 `REVGUARD_GRAFANA_ROOT_URL`，保留结尾 `/grafana/`；202 默认 Grafana 管理端口仍只绑定回环地址。

看板每 15 秒刷新；页面每 30 秒检查连接，服务不可用时显示重连提示。Grafana 仅负责可视化，Higress 继续负责网关鉴权与路由。日志和 Trace 后端已接入，当前嵌入页集中展示运行指标与资金恢复状态，没有把日志检索或 Trace 浏览器开放到公共 iframe。

本次浏览器、只读边界、隔离停机提示、部署前后数据一致性记录见 [Grafana 嵌入验收](evidence/grafana-embed-20260912/README.md)；23 面板版本在彩排栈与常驻栈的双栈验收见 [Grafana 内嵌大屏验收（2026-09-18）](evidence/grafana-embed-20260918/README.md)。

## 已验证与限制

202隔离Docker验收已查到同一Case8的51个Agent/Skill/Tool span、Loki日志、Grafana健康状态及四个Prometheus采集目标。停止隔离数据库后RevGuardUnavailable触发，并到达Alertmanager；恢复后该告警解除。原始验收记录保存在docs/evidence/finals-acceptance-20260912/。

新Case8的51-span证据使用隔离MCP参考执行链。生产Worker适配器还通过真实Higress调用了不存在任务，收到预期拒绝，并在生产Tempo查到相同W3C trace ID，详见deployment-observability.json；这证明跨进程载体传递，不代表重新执行了Matrix完整业务链。原Matrix历史演示保留。当前数据库监控采用readiness和应用指标，尚未接入数据库专用exporter、复制延迟指标；没有进行真实主备切换或PITR。组件部署在同一台202服务器，数据库容器故障演练不代表整机故障时观测后端仍可用。

资金恢复门禁与备份恢复要求见[ADR-0004](adr/0004-money-outcome-recovery.md)。
