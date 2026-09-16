# 复赛评委意见逐条闭环

本页是《项目优化建议》的实施索引。状态只分为“已实现并本地验证”、“已就绪，待外部环境验收”和“待企业真实数据”，不把模拟数据或本机 PostgreSQL 说成生产 PolarDB 结果。

| # | 评委建议 | 修改结果 | 验证证据 | 状态 |
|---|---|---|---|---|
| A1 | 补充人工时长、错付率、追回成本、人工升级率、审计风险 | 新增强制数据分类的基线 CSV 和评估脚本，五类指标统一计算；WebUI“价值模拟”可输入月案件量和综合人工成本，直观展示时长下降、吞吐倍数、释放工时及月/年经费空间；合成样本输出强制 `production_claim_allowed=false` | `scripts/run_value_evaluation.py`、`data/value_baseline/template.csv`、`docs/value-evaluation-synthetic.json`、`docs/value-simulation.md`、`demo-ui` | 模拟口径与展示已实现；企业基线待真实数据 |
| A2 | 说明 Agent 上下文传递 | StageTask 绑定 case ID、case status、case version 哈希、Skill、Worker actor 和输入快照；Matrix/REST 关联 room/message/request/task/receipt/trace ID | `revguard/agent_bridge.py`、`revguard/matrix_team.py`、录制环境验收摘要 | 已实现并在录制环境验证 |
| A3 | 政策/金额冲突如何裁决 | 政策重叠显式输出 `unresolved_conflicts`，风险硬升 L3；金额由 Decimal 确定性内核复算，差异无法归因时禁止自动写入 | `policy_matcher.py`、`risk.py`、风险/政策测试 | 已实现并本地验证 |
| A4 | 谁可以批准写入 | 只有独立 Approver Principal 可决策；Executor 仅在签名能力令牌通过后写，Verifier 独立读后验证 | `security.py`、`api.py`、`adr/0005` | 已实现并本地验证 |
| A5 | 证据不足、重复写入、人工驳回、重派、恢复 | 低于阈值挂起至 `WAITING_FOR_EVIDENCE`；幂等键拦截重复写；驳回进入 `REJECTED`并归档关闭；失败 Task 可通过受权接口重派并保留 lineage；验证失败自动冲销并再验证 | `state_machine.py`、`store.py`、`POST /agent-tasks/{id}/reassign`、Golden-003/008 | 已实现并本地验证 |
| A6 | 提供 AgentTeams、Skills、结算工具与测试源码 | 代码包保留 Orchestrator + 9 Workers、16 Skills、AgentTeams Adapter、scoped MCP reference Server、Tool Gateway、单测/集成/安全/评测源码与可重放命令 | `agentteams/`、`revguard/matrix_team.py`、`revguard/mcp_server.py`、`tests/` | 已实现并在录制环境验证 |
| A7 | Trace、Log、Metrics、评测数据 | Trace 可按案件查询；真实 Matrix/Worker 往返记录为秒级 `AGENT` span，进程内 Skill/Tool 低于 1ms 时显示 `<1 ms`；API 记录 JSON 结构化访问日志；新增 JSON/Prometheus 指标与聚合证据端点；WebUI 新增“工程证据”页签；105 场景与价值基线分类保留 | `trace.py`、`matrix_team.py`、`observability.py`、`/api/v1/ops/*`、`demo-ui` | 已实现并在录制环境验证 |
| A8 | 版本发布、灰度回滚、告警、容量、部署、持续运维 | 发布升至 0.4.0，新增 0/5/25/100% 灰度策略、5 类告警、合成容量探针、健康/就绪端点和运维手册 | `config/release-policy.yaml`、`config/alerts.yaml`、`docs/operations.md` | 已实现并本地验证；生产 SLO 待目标规格压测 |
| A9 | 证明多 Agent 流程真实流动而非关键词脚本 | CASE-0008 在真实 AgentTeams/Matrix 上完成 Team room 控制面握手、9 个 Worker 独立 room、20/20 StageTask 和 20/20 Matrix 回执；人审前真实暂停，批准后从 Store 续跑并安全回滚 | `revguard/matrix_team.py`、`docs/agentteams-matrix-acceptance-2026-08-29.md`、Web 驾驶舱 | 录制环境完整闭环已验证；待组员录制真人点击与 Element 画面 |
| A10 | 模拟数据要真实且来源透明 | 新增数据来源清单、场景目录、跨表引用、时序、金额、币种和源文件哈希检查；禁止声称公司生产数据 | `data/synthetic-provenance.json`、`docs/synthetic-data-validation.json` | 已实现并本地验证 |
| D1 | SQLite 迁移 PolarDB PG，金额改 `NUMERIC(18,2)` | 环境选择的 PolarDB Store 已实现；案件、执行、验证金额同步写入 6 个 `NUMERIC(18,2)` 列，应用仍使用 Decimal，定义为语义保持；录制服务器已运行官方开源 PolarDB-PG 15.19.5.0 local instance | `postgres_store.py`、`migrations/polardb/001_core.sql`、`docs/polardb-local-instance-acceptance-2026-08-29.json` | 单机开源 PolarDB 已验证；云高可用/PITR 待账号 |
| D2 | 数据库层 append-only 审计哈希链 | `BEFORE INSERT` 触发器在逐案事务锁内计算 previous hash + row digest；`UPDATE/DELETE/TRUNCATE` 触发器拒绝篡改；API metrics 可验链 | 迁移 SQL、`verify_audit_chain()`、本机 PG 篡改拦截实测 | 已实现并本地验证 |
| D3 | StageTask + StageResult 同事务 | 每次 Task attempt 的状态更新和结果插入在一个 DB transaction 中，`UNIQUE(task_id, attempt)` 防重 | `complete_agent_task()`、`tests/test_stage_task_persistence.py` | SQLite 和本机 PostgreSQL 已验证 |
| D4 | 并发写主库，对账/Trace/评测走只读，保留 keyset | 强一致读写始终走 primary pool；列表、Trace、Metrics 可走 read endpoint；案件列表仍用 `(updated_at, case_id)` keyset | `postgres_store.py`、`REVGUARD_READ_DATABASE_URL` | 代码已就绪；PolarDB 主从路由待外部环境验收 |
| D5 | 规模化后可选 pgvector Case Memory | 保留结构化检索为默认；vector 扩展、768 维列和 HNSW 建议放入独立可选迁移，核心 Schema 不强依赖 | `002_case_memory_pgvector_optional.sql` | 已就绪，达到规模门槛后才启用 |
| D6 | 自动备份/PITR 与恢复演练记录 | 新增备份策略、PITR 演练步骤、源/恢复库只读指纹对比脚本和证据模板 | `docs/polardb-production.md`、`capture_recovery_evidence.py`、`docs/recovery-drills/template.md` | 已就绪，待 PolarDB 账号/实例后执行；不宣称已完成云端 PITR |

## 当前两个外部阻塞

1. 公司未提供真实结算基线：当前只能证明指标可计算，不能声称真实节省 84.71% 或真实减少错付。
2. 未提供云 PolarDB 集群、主/只读端点和云账号：官方开源单机 PolarDB-PG、迁移和触发器已验证；云端高可用、备份策略和 PITR 演练仍必须等外部环境。
