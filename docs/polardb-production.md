# PolarDB for PostgreSQL 生产接入与恢复手册

## 1. 实施边界

本仓库已完成 PostgreSQL/PolarDB 适配代码、核心 Schema、库层审计哈希链、主/只读路由和恢复验证脚本。录制服务器 `10.10.10.202` 已部署 PolarDB 官方开源 `polardb_pg_local_instance:15`，实测引擎为 `PostgreSQL 15.19 (PolarDB 15.19.5.0)`，并由 RevGuard 通过独立 Docker 网络访问。它能证明 PolarDB Store、金额语义、事务任务结果和审计链可以实际运行，但它是单机开源实例，不等于云 PolarDB 的高可用、只读节点、自动备份或 PITR。当前仍没有可用的云 PolarDB 集群与云账号，因此本文不记录虚构的云集群 ID、备份集 ID、RPO 或 RTO。

本机 PostgreSQL 18.6 兼容性测试仍保留：`NUMERIC(18,2)` 保留 `112.34`，审计事件链校验通过，直接 `UPDATE audit_events` 被 append-only 触发器拒绝，StageTask 与 StageResult 一次事务完成；另有一次官方 MCP `tools/call` 经 scoped Server 执行后把成功 Task/Result 写入同一 PostgreSQL。录制服务器进一步完成了官方开源 PolarDB-PG 的运行验收，详见 `docs/polardb-local-instance-acceptance-2026-08-29.json`；两者都不是云 PolarDB 验收。

## 2. 迁移与最小权限

1. 由独立 migration principal 创建 `pgcrypto`、表、索引和触发器：

   ```bash
   export REVGUARD_MIGRATION_DATABASE_URL='postgresql://...primary.../revguard?sslmode=verify-full'
   .venv/bin/python scripts/migrate_polardb.py
   unset REVGUARD_MIGRATION_DATABASE_URL
   ```

2. 应用 principal 只授予核心表 DML 和 sequence 权限，不授予 DDL、trigger disable 或 audit owner 权限。生产保持 `REVGUARD_AUTO_MIGRATE=false`。
3. 配置主库与只读端点：

   ```dotenv
   REVGUARD_DATABASE_URL=postgresql://revguard_app:***@primary:5432/revguard?sslmode=verify-full
   REVGUARD_READ_DATABASE_URL=postgresql://revguard_read:***@readonly:5432/revguard?sslmode=verify-full
   REVGUARD_DB_POOL_MIN=1
   REVGUARD_DB_POOL_MAX=10
   ```

   案件读后写、幂等查找、任务状态与所有事务都走 primary；案件列表、Trace 回放和 Metrics 可走 read endpoint。如果未配只读端点，两类查询共用 primary pool。PolarDB 集群端点也可由 PolarProxy 自动读写分离，但对账/评测资源隔离建议用独立只读端点。

## 3. 金额语义保持

- Python 内核继续使用 `Decimal`，不引入 float。
- 案件的实付/应付、执行金额、验证的应有/实有/差额同步投影为 `NUMERIC(18,2)`。
- JSONB 保留完整领域 Artifact，强类型金额列用于库内对账、索引和约束。
- 这是“语义保持的存储迁移”：迁移前后舍入与币种规则不变，只把存储层从 SQLite JSON 升级为 PostgreSQL JSONB + NUMERIC。

## 4. 审计链与原子任务

`001_core.sql` 的 `BEFORE INSERT` 触发器对 case 取事务级 advisory lock，生成 canonical row digest，再计算 `SHA256(previous_hash + ':' + row_digest)`。首条事件的 previous hash 为 `GENESIS`。第二个触发器拒绝 `UPDATE / DELETE / TRUNCATE`。运维端点会输出链校验状态，破链立即冻结写入。

`complete_agent_task()` 在同一个数据库事务内锁定 StageTask、更新终态并插入 StageResult。`UNIQUE(task_id, attempt)` 使每次尝试只能有一个结果。

## 5. pgvector 启用门槛

默认使用 case type、root cause、policy version 和时间等结构化检索。只有同时满足以下条件才执行 `002_case_memory_pgvector_optional.sql`：

- 真实 Case Memory 数量已使结构化检索 P95 超过目标；
- 已有固定 embedding 模型/版本和离线 recall@k 基线；
- 目标 PolarDB engine/revision 通过 `SHOW polardb_version` 核对扩展兼容性；
- 先导入代表性 embedding，再在灰度环境选择 HNSW/IVFFlat 和索引参数。

## 6. 自动备份与 PITR 演练

目标策略（需云账号实际配置后才可勾选）：

- 开启每日数据备份，保留 14 天；日志备份保留期不小于 14 天；
- 有合规要求时开启跨地域备份与 WORM；
- 每季度至少一次恢复到新集群，不覆盖原集群。

演练步骤：

1. 在源库使用只读账号捕获目标恢复点的计数、金额指纹和审计链头：

   ```bash
   REVGUARD_RECOVERY_DATABASE_URL='postgresql://...source-readonly...' \
     .venv/bin/python scripts/capture_recovery_evidence.py \
     --output docs/recovery-drills/<drill-id>-expected.json
   ```

2. 记录 `captured_at`，在 PolarDB 控制台选择该时间点恢复到新集群。PolarDB 官方说明会以全备份加重做日志恢复，且恢复集群不携带源集群参数设置，因此参数也要单独核对。
3. 对恢复集群运行对比：

   ```bash
   REVGUARD_RECOVERY_DATABASE_URL='postgresql://...restored-readonly...' \
     .venv/bin/python scripts/capture_recovery_evidence.py \
     --expected docs/recovery-drills/<drill-id>-expected.json \
     --output docs/recovery-drills/<drill-id>-actual.json
   ```

4. 只有 `verdict=PASSED`、`audit_chain.valid=true`、RPO/RTO 在目标内，并且应用用只读凭据能回放 Trace 时，才签署演练记录。模板见 `docs/recovery-drills/template.md`。

## 7. 官方能力依据

- [PolarDB for PostgreSQL 读写分离](https://help.aliyun.com/en/polardb/polardb-for-postgresql/read-or-write-splitting-5)
- [PolarDB for PostgreSQL 一致性级别](https://help.aliyun.com/en/polardb/polardb-for-postgresql/consistency-levels)
- [PolarDB PGVector](https://help.aliyun.com/en/polardb/polardb-for-postgresql/pgvector)
- [PolarDB 备份策略](https://help.aliyun.com/en/polardb/polardb-for-postgresql/configure-a-backup-policy)
- [PolarDB 按时间点恢复](https://help.aliyun.com/en/polardb/polardb-for-postgresql/method-1-for-full-restoration-point-in-time-restoration)
