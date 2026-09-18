# HA/PITR 与消融实验执行指南

状态：已执行。

## 1. PolarDB HA/PITR

```bash
cd /root/<revguard-worktree>
bash scripts/run_polardb_ha_pitr_drill.sh
```

脚本以 `revguard-polardb-ha` Compose 项目创建两个官方 PolarDB-PG 容器、独立私有目录、主共享存储、备库恢复存储、WAL 归档和 PITR 恢复卷。稳定端点仅位于隔离网络中。它会：

1. 检查 `pg_stat_replication.sync_state=sync`；
2. 先写入目标点前标记 A，再使用官方 `pg_basebackup -D PRIVATE --polardata=SHARED -X stream` 生成私有/共享协同基备；
3. 备份完成后记录 UTC 恢复目标，写入目标点后标记 B，再在新容器恢复；
4. 验证 A 存在、B 不存在、金额指纹和审计链；
5. 停止主节点容器，等待控制器调用 `pg_promote()`，验证切换前已提交标记与稳定端点写入；
6. 输出 `manifest.json`、`ha-result.json`、`pitr-result.json`、脱敏日志和 `SHA256SUMS.txt`。

验收条件：`ha-result.json.verdict=PASSED`、`pitr-result.json.verdict=PASSED`、RTO 不超过 60 秒、RPO 为 0，且 `SHA256SUMS.txt` 全量校验通过。

演练结束时默认仅清理它创建的容器、网络和命名卷，不使用全局 prune。运行中断时也会保存本项目日志。此结论仅覆盖单宿主机容器、卷和网络故障，不代表托管控制面、跨宿主机或跨可用区 SLA。

## 2. Direct / MCP Team / AgentTeams Matrix 消融

先在 202 私有 `.env` 设定一次性数据库口令 `REVGUARD_ABLATION_DB_PASSWORD`，不要写进仓库。其余 Matrix 配置复用现有私有配置。

```bash
cd /root/<revguard-worktree>
bash scripts/run_ablation_experiment.sh
```

脚本使用 `revguard-ablation` 项目和独立 PostgreSQL 卷。A、B 分别在每个 Golden Case 上执行 5 次；C 在每例执行一次真实 Matrix/AgentTeams 调用。`CASE-2026-0001` 统一停在人审门，绝不批准资金动作。该脚本不重置或重跑 19000/19088 中的 Case1 / Case8。

真实 Matrix 阶段短时把 `revguard-api.internal` 指到无宿主端口的 `revguard-api-experiment`。`trap` 会在成功、失败和中断路径把别名恢复到 `revguard-api-dev`，并核对 19088 健康端点。最终 `manifest.json` 只在所有样本的业务终态与资金约束都匹配时写 `all_passed=true`。

每个 JSON 样本包含：`mode`、`case_spec`、`outcome`、`governance_metrics`、`runtime_metrics`、`evidence_refs` 和 `limitations`。C 的一个样本只能报告观测值，不得被表述为统计显著性。

## 3. 对外材料更新门槛

只有上述两个 manifest 和最终发布门禁在同一版本树中通过，才更新 `EVIDENCE_HONESTY.md`、PPT、讲稿、官网、提交说明和包内证据的 HA/PITR/消融结论。历史证据保留原始正文，并在元数据中标注验证日期、范围与新证据包引用；历史结论不得倒灌为新演练事实。

# 执行状态（2026-09-18）

## 结论

本轮 PolarDB HA/PITR 与 Direct / MCP Team / AgentTeams Matrix 三组消融均已在独立容器、网络、数据库和卷中通过。该结论仍按同一正式 `v0.6.0` 决赛包发布，不触发 `v0.6.1`。

| 项目 | 本轮结果 | 机器证据 |
| --- | --- | --- |
| PolarDB 自动故障切换 | 通过 | 故障前 `sync_state=sync`；控制器自动提升；RTO `27.09s`；RPO `0`；稳定端点故障后写入成功 |
| PolarDB PITR | 通过 | 官方 `pg_basebackup` 私有/共享协同基备；目标时间 `2026-09-18T13:23:32.573833Z`；恢复 `46.24s`；A 存在、B 不存在；金额指纹与审计链通过 |
| Direct Orchestrator | 通过 | 4 个 Golden Case × 5 次，共 20/20 通过；中位延迟 `212.5ms` |
| MCP Team | 通过 | 4 个 Golden Case × 5 次，共 20/20 通过；中位延迟 `375.5ms` |
| AgentTeams / Matrix | 通过 | 4 个 Golden Case 各 1 次真实运行，共 4/4 通过；观测值 `750883ms / 208423ms / 939436ms / 708912ms`，中位数 `729897.5ms` |

Matrix 真实观测的业务终态：001 停在 `WAITING_FOR_APPROVAL`（L2）、003 停在 `WAITING_FOR_EVIDENCE`、004 为 `CLOSED`（L0）、007 为 `CLOSED`（L3）。四个样本资金动作数与非法资金动作数均为 0；StageTask 持久化、actor–Skill 服务端绑定、输入/输出/交接哈希、Matrix 派发与响应事件覆盖率均为 1.0。C 组仍是单观测值，不能被表述为统计显著性。

## 可复核证据

- HA/PITR：`docs/evidence/polardb-ha-pitr-20260918T132221Z/`
  - `ha-result.json`：`verdict=PASSED`、`sync_state_before_fault=sync`、`rto_seconds=27.09`、`rpo=0`、`automatic_promotion_log_verified=true`
  - `pitr-result.json`：`verdict=PASSED`、`duration_seconds=46.24`、`basebackup_method=official_pg_basebackup_private_plus_polardata_shared`
  - `SHA256SUMS.txt`：全部成员复验通过
- 消融：`docs/evidence/ablation-20260918T140320Z/`
  - `manifest.json`：`sample_counts={DIRECT_ORCHESTRATOR:20, MCP_TEAM:20, AGENTTEAMS_MATRIX:4}`、`all_passed=true`
  - 本地 A/B 样本来源：`docs/evidence/ablation-20260918T084653Z/`；本次 `--matrix-only` 运行使用全新隔离数据库完成 4 个真实 Matrix 观测，并复用这 40 个未改动样本
  - `SHA256SUMS.txt`：59 个成员复验通过（首次清单曾把仍在写入的 `checksum.log` 计入哈希，已修正脚本顺序并重签；业务样本与 `manifest.json` 未改动）

## 运行后环境核对

演练结束后，`revguard-api.internal` 已恢复到 `revguard-api-dev`（`0.6.0-rc3`），`http://127.0.0.1:19088/api/v1/health` 返回 `ready=true`。`revguard-ablation` 与 `revguard-polardb-ha` 的容器和命名卷均已清理；`revguard-api`（19000）与 `revguard-api-dev`（19088）保持运行且未被重置。

## 边界

- HA/PITR 是两个官方 PolarDB-PG 容器在宿主机上的隔离演练，覆盖容器级故障、独立卷恢复与单网络稳定端点；不代表阿里云托管控制面、跨宿主机或跨可用区 SLA。
- 本轮证据使用合成 RevGuard 数据集和公开真实交易叠加合成佣金规则；不等同企业生产租户授权数据。
- 三种模式的确定性业务结论一致；真实 AgentTeams / Matrix 显著增加延迟与协作成本，其价值是职责隔离、最小权限、独立复核、故障约束和可归责证据，而不是“把钱算得更准”。
