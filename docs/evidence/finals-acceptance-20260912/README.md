# 决赛整改验收记录（2026-09-12）

RevGuard 0.5.0 已部署至 10.10.10.202 Docker，入口为 http://10.10.10.202:19000/demo/ 。构建、测试、模型调用与故障演练均在 202 容器执行；本地仅编辑与同步。

## 发布与数据保留

`acceptance.json` 记录版本、镜像、执行范围与限制。`deployment-before.json`、`deployment-after.json` 和 `deployment.log` 证明原 8 个案件、execution 及全量 ledger 的哈希前后一致，审计链有效。原演示未 reset；数据库备份及私密配置仅保存在 202 的 root 私有目录，不包含在本证据包。

## 结果

| 项目 | 结果 | 文件 |
| --- | --- | --- |
| 后端主套件 | 发现 183 项，163 通过，20 项 PostgreSQL 测试在独立容器执行 | release-checks.log |
| PostgreSQL | 20/20：18 项资金恢复合同、2 项既有集成 | postgres-recovery-tests.log |
| 覆盖率与确定性计算 | 覆盖率显示 91%；105/105 案例 | release-checks.log |
| 前端 | 12 项测试与构建通过 | frontend-tests.log、frontend-build.log |
| 代码与依赖检查 | Ruff、契约生成一致性、pip-audit、Bandit 通过 | release-checks.log |
| MCP 角色隔离 | 9 次本角色发现通过、72 次跨角色拒绝 | higress-isolation.log |
| 数据库容器故障告警 | 停隔离库后 Prometheus 与 Alertmanager 观察到告警，恢复后解除 | observation-*.json |
| 生产观测 | 4 个 Prometheus 目标正常、Tempo 与 Loki 有数据、Grafana 正常 | deployment-observability.json |

## 三种链路证据的边界

1. 新 Case8 故障证据来自隔离 PostgreSQL 与进程内 MCP 参考执行链，使用真实的合成账务错记触发补偿；Tempo 收到 51 个 Agent/Skill/Tool span，见 `trace-case8*.json`。
2. 生产 Worker 适配器经真实 Higress 对不存在的任务发送探针，收到预期拒绝，不产生资金效果；同一 W3C trace ID 出现在生产 Tempo，见 `deployment-observability.json`。这证明跨进程载体传递，不代表重新跑完 Matrix 业务流程。
3. 原 Matrix 演示案件和历史回执保留。原 Case8 的 20 项任务记录属于历史演示；本次隔离参考故障流程为 18 项，不能混用计数。

AgentTeams 的 Sol 迁移另见 `agentteams-sol.json`：实际 CoPaw 流式客户端使用合成只读工具验证工具调用和结果续接，不发 Matrix 消息、不执行资金操作。

## 限制与校验

已实现自管模拟账务的事务、幂等、批次补偿及结果对账门禁；本地 outbox 不等同于外部 ERP Saga 投递。尚未验收真实主备切换、PITR、数据库专用 exporter 或复制延迟监控。观测组件与业务同在 202，数据库容器故障演练不能代表整机故障下的高可用。

`source-sha256.json` 固定资金与观测验收时的源文件；`SHA256SUMS` 覆盖本证据目录中的文件。模型迁移的派生镜像 ID 独立记录在 `agentteams-sol.json`。
