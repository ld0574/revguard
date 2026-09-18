# P0 执行状态（2026-09-18）

执行主机：`10.10.10.202`；所有构建、服务、故障注入和样本运行均在 Docker 容器中完成。本地工作区只用于编辑与同步。

## 结论

本轮 **不得** 宣称 PolarDB HA/PITR 或完整 A/B/C 消融已验证，因而不触发 v0.6.1 发布、PPT 结论更新或对外材料改写。

| 项目 | 本轮结果 | 说明 |
| --- | --- | --- |
| 双节点同步 | 观测到 | 两个官方 PolarDB-PG 容器、独立私有目录和专用共享卷；同步探针写入。 |
| 主节点停止后的稳定端点 | 部分成功 | 故障前提交标记和故障后写入标记均存在；首次写入耗时 46.838 秒。但控制器未在预期窗口自行提升，最终提升由隔离控制器容器内的人工 `pg_promote` 触发。 |
| PITR | 未通过 | localfs 共享存储恢复卷未能恢复可读的 RevGuard 数据库；不能把恢复机制表述为已验证。 |
| Direct / MCP Team | 通过 | 40/40 个隔离样本通过既定业务终态和资金约束检查。 |
| AgentTeams / Matrix | 未完成 | 第一个真实样本超过三分钟未返回阶段响应，已停止专用运行器并恢复 dev 别名；没有 C 组 JSON。 |

## 可复核证据（远端保留）

- HA/PITR 演练目录：`/root/revguard-0.6.0-dev/docs/evidence/polardb-ha-pitr-20260918T083746Z/`
  - `sync-before-fault.json` SHA-256：`0b23756ee36f58a9fc6bbc91d47ce8e98e8c8745ab61a9eb38afcfa3a9f7bff3`
  - `ha-after-failover.json` SHA-256：`3f1191b735eb4e5b149bfd7f904bd4eec4453d7d717990a8997c5137f25cbc47`
- 消融目录：`/root/revguard-0.6.0-dev/docs/evidence/ablation-20260918T084653Z/`
  - `ab-local-manifest.json` SHA-256：`b166b3d9498301ff7696a756c964f1abd83be0575e5d653eabc8d39257302239`
  - 摘要：`direct_mcp_samples=40`、`direct_mcp_all_passed=true`、`matrix_samples=0`、`matrix_status=NOT_COMPLETED_NO_RESPONSE`。

每次失败的隔离演练也保留在同一远端 `docs/evidence/` 下，以供调试；没有重置、删除或覆盖 19000/19088 的既有演示 Case、数据库、卷或证据。

## 运行后环境核对

演练结束后，`revguard-api.internal` 已恢复到 `revguard-api-dev`，`http://127.0.0.1:19088/api/v1/health` 返回 HTTP 200，且没有遗留名为 `revguard-polardb-ha` 或 `revguard-ablation` 的容器。
