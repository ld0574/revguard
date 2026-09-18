# P0 HA/PITR 与消融实验执行指南

状态：`PARTIALLY_EXECUTED_2026-09-18`。本文件描述隔离流程；本轮的实际结果和未通过项记录在 `p0-execution-status-2026-09-18.md`，不能用机制或脚本替代运行证据。

所有命令只在 `10.10.10.202` 上执行，构建、测试、服务和故障注入均在 Docker 中完成。本地工作区只用于编辑和同步。

## 1. PolarDB HA/PITR

```bash
cd /root/<revguard-worktree>
bash scripts/run_polardb_ha_pitr_drill.sh
```

脚本以 `revguard-polardb-ha` Compose 项目创建两个官方 PolarDB-PG 容器、独立私有目录、主共享存储、备库恢复存储、WAL 归档和 PITR 恢复卷。稳定端点仅位于隔离网络中。它会：

1. 检查 `pg_stat_replication.sync_state=sync`；
2. 在检查点后把官方镜像 localfs 共享存储快照复制到独立恢复卷，并以 `polar-initdb.sh replica` 创建其私有状态；
3. 写入 A、记录 UTC 恢复目标、写入 B，再在新容器恢复；
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
