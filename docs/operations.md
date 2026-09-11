# RevGuard 发布、告警、容量与持续运维

## 可观测合同

- Trace：`GET /api/v1/cases/{case_id}/trace`，保留 Agent / Skill / Tool / Approval / Execution / Verification 的 sequence、父子关系、耗时、输入输出与错误。
- Log：API 每次请求输出一行 JSON，仅含 UTC、level、request ID、method、path、status 和 duration；不记录 body、Bearer key 或能力令牌。
- Metrics：`GET /api/v1/ops/metrics` 输出 JSON，`GET /api/v1/ops/metrics/prometheus` 输出 Prometheus text，都需 viewer 身份。指标包含案件/任务状态、Task attempt、Trace 错误、审计数量和 PolarDB 哈希链状态。
- 录制证据：`GET /api/v1/ops/evidence` 聚合运行指标、确定性评测、价值数据分类和外部验收状态，WebUI “工程证据”页签直接读取。
- Probe：`/api/v1/health/live` 仅证明进程存活；`/api/v1/health/ready` 会读写 Store，配置只读端点时也会检查 read pool。

运行中的 Prometheus 规则为 `config/observability/alerts.yaml`；`config/alerts.yaml` 仅为早期项目说明。Grafana、日志与 Trace 的访问及验收见 [可观测组件](observability.md)。审计破链和资金恢复积压不能靠重试消除告警。

## 版本与灰度

镜像与 `/api/v1/health` 同时暴露 `REVGUARD_RELEASE_VERSION`；以该端点和当前部署验收记录核对实际版本。发布策略以 `config/release-policy.yaml` 为准，以下灰度比例是面向未来真实流量的方案，不代表演示环境已经接入企业生产流量：

1. 0% 真实流量：在 202 执行 `bash scripts/verify_docker.sh`，完成主套件、临时 PostgreSQL 的迁移/资金恢复测试、Golden 回放、前端及依赖安全检查；
2. 5% canary，最少 30 分钟；
3. 25% limited，最少 120 分钟；
4. 100% general。

任一审计破链、错写/重复写、`ROLLBACK_REQUIRED` 超 5 分钟或 Trace error rate 超 1% 时立即停止扩容并回滚到上一个不可变镜像。Schema 使用 expand/migrate/contract：在所有旧版实例退场前不执行破坏性 DDL。

## 容量与 SLO

`make capacity` 在干净临时 SQLite 上以 200 条合成案件、20 并发执行写入和 keyset 查询，结果保存在 `docs/capacity-baseline-local.json`。该文件强制 `production_slo_claim_allowed=false`，只做回归基线。

PolarDB 上线前必须在目标规格上重测：单案闭环 P95、列表/Trace P95、并发写 TPS、连接池等待、read replica 延迟、审批等待和回滚耗时。在有真实流量前不会伪造生产阈值；初始容量从 `DB_POOL_MAX=10` 和 5% 灰度起步，再根据测量扩容。

## 值班周期

- 每日：查看 critical/high 告警、审计链、`FAILED_FINAL`、`ROLLBACK_REQUIRED` 和审批积压。
- 每周：回放 Golden Case，复核错误类型、人工重派与幂等冲突，核对备份任务状态。
- 每月：更新风险矩阵与 Skill 版本，核对依赖漏洞，用不可变镜像做回滚演练。
- 每季度：执行 PolarDB PITR 恢复演练，签署 `docs/recovery-drills/` 记录，重测容量与 SLO。
