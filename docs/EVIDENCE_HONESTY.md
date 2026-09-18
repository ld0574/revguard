# RevGuard 证据诚实性与对外表述边界

本文档把散落在各验收报告里的边界声明收敛成**一张可对照的口径表**，用于决赛材料、
答辩、官网与第三方复现。任何对外表述与本文冲突时，以本文为准。

适用版本：`v0.6.0`（决赛封版口径）。最后更新：2026-09-18。

---

## 1. 四层数据来源标签

每条数据、证据和指标必须能归入且只能归入以下一层。仓库内出现这些标签的地方，
含义以本表为准。

| 标签 | 含义 | 在 RevGuard 中的实际例子 |
|---|---|---|
| `PUBLIC_REAL` | 公开真实交易数据或真实佣金规则 | Olist 巴西电商公开交易数据（CC BY-NC-SA 4.0）；Etsy / eBay 公开费率与佣金规则快照 |
| `SYNTHETIC_DOMAIN` | 合成佣金规则与业务设定 | 渠道等级、合同政策、合成佣金规则、案件争议原因、平台归属映射、结算与异常场景 |
| `LIVE_SYSTEM` | 当前运行系统真实产生的对象与回执 | ERPNext 单据与鉴权 REST 回执；PolarDB 资金台账行；Matrix 事件与审批记录 |
| `SYSTEM_GENERATED` | RevGuard 自身生成的治理记录 | StageTask、审批断言、执行与验证记录、Trace、审计哈希链、恢复记录 |

**标准表述**：

> 公开真实交易数据 + 真实佣金规则 + 合成佣金规则。

**主 Demo（CASE-2026-0001 / CASE-2026-0008）**：企业、合同、政策与佣金争议为
`SYNTHETIC_DOMAIN`，运行链路（ERPNext 读取、真人审批、PolarDB 写入与冲销、
审计与 Trace）为 `LIVE_SYSTEM` / `SYSTEM_GENERATED`。

---

## 2. 执行证据等级

“代码里有这个机制”和“这个机制真的跑过”是两件不同的事。RevGuard 的证据分三级，
材料引用时必须标注级别。

| 级别 | 判据 | 例子 |
|---|---|---|
| **A 实测执行** | 有 202 Docker 内的执行日志、退出码、时间戳与产物哈希 | 378 项后端测试（295 项默认测试 + 83 项 PostgreSQL/Matrix 集成测试）；`verify_docker.sh` 发布门禁；Trivy 0 项可修复高危；两条案件运行记录与回放导出 |
| **B 机制存在、本轮未执行** | 代码与测试存在，但没有本轮真实系统执行记录 | 金蝶 / 用友 / SAP Adapter（待验证）；外部 ERP 正式会计写入与跨系统补偿；PolarDB 双容器同步复制、自动故障切换与 PITR 演练 |
| **C 规划** | 只有设计或路线，没有实现 | 多租户与水平扩展 |

**只有 A 级证据可以写成“已验证”。** B 级写成“扩展点已提供、状态为待验证”，
C 级写成“规划”。

覆盖率口径：`verify_docker.sh` 对 `revguard/` 包（排除 `postgres_store.py` 单列）
给出 **≥90%** 行覆盖率，门禁阈值为 90%。不要引用未在同一日志中出现的更高数字。

### 2.1 评测判据的来源与独立边界

`105/105` 只说明"实现与仓库内冻结的期望集一致"，不等于"这套业务口径已被客户确认"。
引用该数字时必须能说出判据来自哪里：

- 构成：8 个端到端 Golden Case（`data/golden_cases/*.json` 的 `expected` 块）+
  80 个风险边界组合（`data/expected/risk_matrix.csv`）+ 8 个政策生效日边界 + 9 个安全攻击探针；
- 期望值只被读入比较，**不作为输入注入被测系统**（`input` / `expected` 是两个并列字段）；
- 可被证伪：篡改期望值后评测立即失败（`GOLDEN-001` 期望佣金改 `99999.00`、风险矩阵一行期望改 `L3`
  两组实验均为 `exit=1`）；
- **边界**：真值由本团队按业务与政策设计冻结；阈值口径仍需真实业务方确认。
  若规则表本身写错，测试会稳定地通过错误口径。

探针与原始输出：`docs/evidence/evaluation-independence-20260918/`。

---

## 3. 禁止表述 / 正确表述对照表

| ❌ 禁止表述 | ✅ 正确表述 | 原因 |
|---|---|---|
| “所有业务数据和佣金规则均为真实生产数据” | “公开真实交易数据 + 真实佣金规则 + 合成佣金数据” | 用来源标签明确区分公开真实交易、真实佣金规则与合成佣金数据 |
| “已接入金蝶 / 用友 / SAP” | “提供 Adapter 配置 Schema 与映射清单，状态为待验证” | 只有扩展点，没有租户与联调授权 |
| “已完成外部 ERP 会计写入闭环” | “ERPNext 读取集成已验证；外部 ERP 正式会计写入与分布式补偿待验证” | 写入只落在 RevGuard 受控台账 |
| “已完成 PolarDB 高可用与 PITR” | “官方开源 PolarDB-PG 单实例已验收；双容器 HA/PITR 仅在 `docs/evidence/polardb-ha-pitr-*/manifest.json` 同时为 PASSED 后才可写为已验证” | 机制、脚本和实测证据必须分开表述 |
| “客户处理时长下降 84.71%” | “8 个合成 Golden Case 的情景测算，指数口径” | 没有授权基线 |
| “AI 自动审批资金” / “智能体自主决定金额” | “真人 Matrix 身份审批 + 确定性金额内核” | 金额、政策、权限、状态迁移由确定性代码决定 |
| “1 KES 偏差事故” | “受控注入的偏差场景（`posting_tamper_amount=1`）” | 故障是刻意注入的演示条件 |
| “两条案件记录由真实 AgentTeams Worker 跑出” | 见第 4 节的运行通道说明 | 取决于该条记录当时的 `transport` |
| “外部仓库测试规模对比说明我们更强” | “我们提供可复现的执行证据” | 第三方测试数字是静态计数，未执行 |
| “Higress 提供可观测性” | “Higress 负责网关鉴权、路由与调用层指标；Trace/Audit 负责案件级业务证据；Prometheus/Grafana 负责运行监控与展示” | 三者分工不同 |

---

## 4. 运行通道：界面显示的通道就是该条记录的真实通道

RevGuard 的运行通道有两种，界面与材料必须与该条记录一致：

| 通道 | `execution_mode` | 含义 |
|---|---|---|
| **AgentTeams · Matrix** | `AGENTTEAMS_MATRIX` | 每个 StageTask 由真实 AgentTeams Worker 在 Matrix 房间中执行，模型调用真实发生，房间消息与事件 ID 可核验 |
| **MCP 参考链路** | `MCP_TEAM` | 由 RevGuard 自带参考执行器按同一 StageTask / Skill 契约推进，用于无模型依赖的回归与录制 |

- Demo UI 的案件标题栏与任务账本**显式标注**当前通道（`AgentTeams · Matrix` /
  `MCP 参考链路`），逐任务也标注 `Matrix` / `MCP 参考链路` / `MCP 网关`。
- 两种通道共享同一套 StageTask、Skill 契约、真实 ERPNext 读取、真实 Matrix 真人
  身份审批、真实 PolarDB 资金写入与冲销、审计与 Trace。
- **差异只在“推理步由谁执行”**：真实 Worker 会引入模型耗时与不确定性；参考链路
  是确定性的快速回放。
- 引用任何案件记录时，必须连同该记录的通道一起引用；不得把参考链路记录说成
  真实 Worker 执行。

---

## 5. “界面显示 X，后端是否真有 X”自查

按竞品分析中“RepoMesh 的 `· IMMUTABLE`、OpsKeeper 的 0.7ms”一类翻车点做的逐项自查。

| 界面元素 | 后端事实 | 结论 |
|---|---|---|
| 案件状态（CREATED / EVIDENCE_COLLECTING / CLOSED / ROLLED_BACK） | 由 PolarDB 案件表权威读取，写后验证走主库 | ✅ 一致 |
| 证据“来源链”与 `STRONG / WEAK` 强度 | 每条证据带 `provider`、外部单据号、`payload_hash`、`http_status`、`latency_ms` 与工具回执 | ✅ 一致 |
| 独立验证差异 `0.00` | Verifier 独立查询账务结果后计算 | ✅ 一致 |
| Token 计数 | 采集不到时显示“未采集”，不做估算 | ✅ 诚实标注 |
| 运行通道徽标 | 由 `case.execution_mode` 驱动 | ✅ 一致 |
| Grafana 只读大屏（iframe） | 同源 iframe，`kiosk` 只读，面板数据来自 Prometheus | ✅ 一致 |
| 数据库故障演示 | 使用**已存档的隔离实验**结果与告警证据，不在演示库执行 reset 或停库 | ✅ 已声明 |
| 官网运行回放页 | 由真实运行导出，页面标注 `provenance.snapshot_sha256` 与 `audit.chain_ok` | ✅ 一致 |
| 金额分摊、政策版本选择 | 确定性代码，不经过模型 | ✅ 一致 |
| 评测数字 `105/105` | 与仓库内冻结期望集逐项比对，期望值不注入被测系统；篡改期望即失败 | ✅ 一致（业务口径验收仍待真实业务方） |
| L3 高风险动作 | 禁止自动执行，必须真人审批 | ✅ 一致 |

**没有发现“界面显示但后端不存在”的项。** 若后续新增界面元素，必须在同一 PR 中
补充本表的一行，否则视为未完成。

---

## 6. 复现入口

| 目标 | 命令 / 位置 |
|---|---|
| 发布门禁（含覆盖率、bandit、pip-audit、npm audit） | `scripts/verify_docker.sh` |
| 彩排栈冒烟 | `bash scripts/rehearsal_smoke.sh http://10.10.10.202:19088` |
| 隔离 PolarDB HA/PITR 演练 | `bash scripts/run_polardb_ha_pitr_drill.sh`，仅 `ha-result.json` 与 `pitr-result.json` 均为 `PASSED` 才更新对外口径 |
| 隔离 A/B/C 消融实验 | `bash scripts/run_ablation_experiment.sh`，Direct/MCP 每案 5 次，Matrix 每案 1 次 |
| 案件回放导出 | `scripts/export_case_replay.py` → `website/data/case-*.json` |
| 证据目录 | `docs/evidence/`（每个目录含 README 与原始日志） |
| 数据来源与许可 | `docs/data-provenance.md` |
| 第三方与上游归属 | `NOTICE.md` |

全部构建、服务与故障演练均在 `10.10.10.202` 的 企业内网环境 执行。
