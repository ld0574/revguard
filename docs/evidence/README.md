# Evidence 目录总览

这里按用途整理 `revguard/docs/evidence/` 下现有的 **35 个证据包**。每个证据包保留
自己的采集日期、运行版本和原始文件；本页只做导航和口径说明，不替换包内的原始证据。
目录名是稳定入口，新增证据时请在对应分组补一行。

## 先看哪一组

| 想核对的内容 | 推荐入口 |
| --- | --- |
| 决赛两条真实 AgentTeams Matrix 运行 | [`finals-recording-20260918/`](finals-recording-20260918/README.md) |
| 审计哈希链和第三方复算 | [`audit-chain-20260918/`](audit-chain-20260918/README.md) |
| Element 中的多 Agent 任务交接 | [`agentteams-handoff-20260918/`](agentteams-handoff-20260918/README.md) |
| StageTask 绑定、Matrix 事件和后端别名 | [`agentteams-matrix-binding-20260918/`](agentteams-matrix-binding-20260918/README.md) |
| 官网回放页和公开截图 | [`website-replay-20260918/`](website-replay-20260918/README.md) |
| 决赛成片、抽帧和录制日志 | [`finals-media-20260918/`](finals-media-20260918/README.md) |
| 当前树的完整发布门禁 | [`release-gate-20260918/`](release-gate-20260918/README.md) |
| Worker / Manager 的模型预算 | [`agentteams-glm-recheck-20260918/`](agentteams-glm-recheck-20260918/README.md) |
| PolarDB HA/PITR 演练 | [`polardb-ha-pitr-20260918/`](polardb-ha-pitr-20260918/ha-result.json) |
| Direct、MCP Team、AgentTeams 的消融 | [`ablation-20260918/`](ablation-20260918/manifest.json) |
| 最早的本地 MCP 可重放闭环 | [`demo-rehearsal/`](demo-rehearsal/manifest.json) |

## 证据口径和完整性

- 业务案件、订单、金额、人员和组织均为 **`SYNTHETIC`**。真实的是工作流执行、任务状态、
  工具调用、审批门、审计链、独立验证和回滚代码路径，不是企业生产数据。
- 脚本或夹具批准会明确标为 `simulated_human=true`、`SCRIPTED` 或等价字段；它不能替代
  真人点击。真人 Matrix 审批只在明确标注了真实运行和房间事件的证据包中成立。
- `demo-rehearsal/` 是本地 MCP reference harness 的可重放包，不是 AgentTeams 房间证据。
  AgentTeams / Matrix 的运行和 Element 画面分别见本页的 AgentTeams 分组与决赛录制分组。
- `polardb-ha-pitr-20260918/` 是官方开源 PolarDB-PG 容器在**宿主机、隔离网络和独立卷**中的
  演练；它不代表阿里云托管控制面、跨宿主机、跨可用区或生产 SLA。
- 涉及构建、测试、服务、浏览器检查或故障演练的操作，均按项目约定在
  `10.10.10.202` 的 Docker 中执行；本机只用于编辑、同步和文件检查。
- `manifest.json`、`SHA256SUMS`、包内 `source-sha256.json` 或报告中列出的哈希是对应包的完整性锚点。
  修改包内文件后，应按包自己的采集脚本重新生成清单和哈希；不要手工改哈希文件来掩盖变化。
- 证据包之间不是同一运行的不同目录。不要把 0.5.x 历史验收、rc2 准备记录和 rc3 决赛运行
  混写成一条链；以包内的 `release`、`recording_id`、`run_id` 和时间戳为准。

## A. 决赛运行、公开材料和当前发布

这些包回答“当前发布版本实际跑了什么、怎样对外展示、怎样复算”的问题。

| 证据包 | 这是做什么的 | 主要入口 / 关键内容 |
| --- | --- | --- |
| [`finals-recording-20260918/`](finals-recording-20260918/README.md) | rc3 录制代次的两条完整真实 AgentTeams Matrix 运行。`CASE-2026-0001` 走真人审批后 `CLOSED`；`CASE-2026-0008` 发生 1.00 KES 偏差，独立复核后冲销并 `ROLLED_BACK`。 | `README.md`、`case-facts.json`、`replay-index.json`；含传输方式、审批、资金结果、StageTask、审计事件和运行时长。 |
| [`recording-reset-rehearsal-20260918/`](recording-reset-rehearsal-20260918/README.md) | 在 19088 合成录制库中预演决赛前重置，确认 8 个 Golden Case、两条台账基线、审批/执行清零和重置审计都回到可录制状态。 | `seed.txt`、`cases.txt`、`ledger.txt`、`counts.txt`；只作用于录制库，不是生产数据。 |
| [`audit-chain-20260918/`](audit-chain-20260918/README.md) | 把上述两条运行的数据库审计表哈希链和运行窗口导出，供不依赖 RevGuard 代码的第三方离线复算。 | `audit-chain-case-2026-0001.json`（全链 435 行）、`audit-chain-case-2026-0008.json`（全链 335 行）、`chain-verification.sql` 及两份 SQL 输出。 |
| [`agentteams-handoff-20260918/`](agentteams-handoff-20260918/README.md) | 证明 Element 编排房间里上一阶段的产物、哈希、事件和失败要求如何交给下一阶段，并能在 Worker 房间收到和回执。 | `element-orchestration-room.png`、`element-handoff-tile.png`、`element-worker-room.png`、`handoff-chain.json`、抓取脚本。 |
| [`agentteams-matrix-binding-20260918/`](agentteams-matrix-binding-20260918/README.md) | 验证无后端凭据的 Worker 仍能通过绑定的只读 MCP 工具取回自己的 StageTask，同时保证 19088 后端别名唯一；附一条 8/8 StageTask 成功的真实 Matrix 运行。 | `run-CASE-82822305.json` 和包内 README 的修复、隔离、复验表。 |
| [`website-replay-20260918/`](website-replay-20260918/README.md) | 验收 GitHub Pages 的运行回放页：索引加载、两条案件回放、播放交互、移动端、子路径资源和分享卡片。 | `browser-result.json`、`public/` 公网结果、`replay-case-*.png`、`replay-playing.png`、`replay-mobile.png`。 |
| [`finals-media-20260918/`](finals-media-20260918/README.md) | 决赛主视频和故障备用片段的录制元数据、逐帧时间、场景日志、抽帧和哈希。视频附件在 GitHub Release，仓库包保存可核验的采集记录。 | `record-log.json`、`recovery-record-log.json`、`frame-times-*.json`、`still-*.png`、标题帧和录制脚本。视频无音轨，不能当作真人现场点击的单独证明。 |
| [`cold-deploy-rc3-20260918/`](cold-deploy-rc3-20260918/README.md) | 在全新的 Docker-in-Docker 守护进程、网络、卷和空 SQLite 库中从零部署 `v0.6.0-rc3`，验证健康、观测组件和 Prometheus 目标。 | `clean_deploy_final.log`、`health.json`、`containers*.txt`、`prom-targets.json`、`grafana-health.json`。 |
| [`grafana-embed-20260918/`](grafana-embed-20260918/README.md) | 验收 rc3 演示栈和常驻栈的同源 Grafana 只读嵌入、23 个面板、全屏、390×844 移动端和失败请求边界。 | `dev/`、`prod/` 浏览器结果，`observability-endpoints.json` 及包内 README。 |
| [`release-gate-20260918/`](release-gate-20260918/README.md) | 在 9/18 的 master 树上重跑当前发布门禁，确认后端、PG 集成、覆盖率、确定性评测、依赖、静态安全、生成物和前端门禁。 | `final-v060-gate.md`；完整执行输出在说明中指向隔离门禁目录。 |
| [`competitor-gap-closure-20260918/`](competitor-gap-closure-20260918/README.md) | 竞品分析差距的逐项状态表和对应可核验入口，包含已经闭合的 P0/P1/P2 和仍为 `OPEN` 的项目。它是状态索引，不是一次运行的验收日志。 | `README.md`；重点链接到模型、Matrix、回放、审批承诺和执行引用证据。 |
| [`evaluation-independence-20260918/`](evaluation-independence-20260918/README.md) | 验证 105/105 的期望集、输入与 expected 分离，以及修改期望集会失败；同时验证审批字段和 Matrix 事件不能由请求体自报。 | `probe.py`、`approval-schema-probe.py`、`probe-output.txt`。 |

## B. AgentTeams、模型和基础设施

这些包解释真实 AgentTeams 链路、模型参数治理、三种执行模式对比和数据库故障演练。

| 证据包 | 这是做什么的 | 主要入口 / 关键内容 |
| --- | --- | --- |
| [`agentteams-glm-budget-20260918/`](agentteams-glm-budget-20260918/README.md) | 记录 `glm-5.3-flash` 从 512 预算风险修到 `reasoning_effort=low + max_tokens=2048` 的首轮 Worker 复验。 | `apply-model-budget.txt`、`controller-model-env.txt`、4 个 Worker 流式工具调用探针、`team-status.txt`。 |
| [`agentteams-glm-recheck-20260918/`](agentteams-glm-recheck-20260918/README.md) | Worker 容器重建后再次读取真正生效的 `generate_kwargs`，并复验 Manager/Worker 的流式工具调用和结果续接。封板前回读为 10 个 Worker + Manager 共 11/11。 | `effective-*.json`、`runtime-probe-*.json`、`budget-recheck-20260918-223647Z.txt`；README 还记录了探针必须显式覆盖 `HOME` 的环境边界。 |
| [`agentteams-manager-glm-20260918/`](agentteams-manager-glm-20260918/README.md) | 针对 Manager 重投影会把 2048/low 写回 512 的问题，验证 bridge 修复、容器重启、MinIO 同步周期和真实工具调用。 | `bridge-before.txt`、`bridge-after.txt`、`manager-effective-policy.txt`、`manager-runtime-probe.json`。 |
| [`agentteams-luna-20260912/`](agentteams-luna-20260912/README.md) | 记录 Luna 模型迁移和空闲成本治理：Manager/Worker 激活模型、心跳关闭、MinIO 持久化、资源状态和案件账务前后哈希。 | `runtime-probe.log`、`manager-runtime.json`、`worker-runtime.json`、`persist-workers.log`、`resources.json`、`SHA256SUMS`。 |
| [`ablation-20260918/`](ablation-20260918/manifest.json) | 比较 Direct Orchestrator、MCP Team、AgentTeams Matrix 三种模式的同一组 Golden Case。当前清单为 Direct 20 次、MCP 20 次、Matrix 4 次，`all_passed=true`。 | `manifest.json`、`direct-*.json`、`mcp-*.json`、`matrix-*.json`、`work-matrix-*/`、`SHA256SUMS.txt`。Matrix 每个案件只有一次真实观测，不能解读为统计显著性。 |
| [`polardb-ha-pitr-20260918/`](polardb-ha-pitr-20260918/ha-pitr-guide.md) | 在隔离的官方 PolarDB-PG 双容器环境中演练同步复制、主库故障自动提升和 PITR；HA 与 PITR 均为 `PASSED`，RTO 27.09 秒、PITR 恢复 46.24 秒、RPO 0。 | `ha-result.json`、`pitr-result.json`、`sync-before-fault.json`、`manifest.json`、`SHA256SUMS.txt`、`ha-pitr-guide.md`。 |
| [`demo-rehearsal/`](demo-rehearsal/manifest.json) | 最早的本地 MCP reference harness 证据包：一个合成案件、20 个 StageTask、9 个 Worker、16 个 Skill，脚本批准后故障验证并回滚。 | `00-data-provenance.json`、`01-human-gate.json`、`02-agent-tasks.json`、`03-audit-events.json`、`04-trace-spans.json`、`05-final-case.json`、`06-audit-report.md`、`manifest.json`。批准是显式脚本批准，不是真人点击。 |

## C. 业务一致性、审批、执行和部署安全

这些包以故障复现、修复后合同测试和部署前后快照为主，回答“失败时是否停住、恢复时是否会串写、
资金和审计是否原子一致”。日期较早的包仍保留，用于追溯每一轮修复，不应替代 rc3 决赛运行证据。

| 证据包 | 这是做什么的 | 主要入口 / 关键内容 |
| --- | --- | --- |
| [`approval-commitment-20260918/`](approval-commitment-20260918/README.md) | 验证审批是对规范化参数的承诺：金额、版本或其他参数漂移时，执行和重新授权都在写台账前拒绝；5 个对抗场景包含正常对照组。 | `probe-output.json`、`probe-console.txt`。 |
| [`execution-reference-20260918/`](execution-reference-20260918/README.md) | 验证资金执行必须引用同案实际读取过的 `ORDER`、`CONTRACT`、`COMMISSION_LEDGER` 三类事实；缺引用或跨订单时返回 `EVIDENCE_GAP` 且台账零写入。 | `probe-output.json`、`probe-console.txt`。 |
| [`approval-consistency-20260912/`](approval-consistency-20260912/README.md) | 0.5.5 人审、状态迁移、驳回、任务取消和审批后执行交接的一致性验收，覆盖 SQLite/PG、浏览器和生产数据保留。 | `reproduced.log`、`acceptance.json`、`browser/`、`production/`、`SHA256SUMS`。 |
| [`coordinator-consistency-20260912/`](coordinator-consistency-20260912/README.md) | 0.5.10 编排阶段记录与案件状态一致性；复现旧 Verifier 迟到结果覆盖新运行的缺口，并验证 `commit_case_stage` 的同事务提交和版本检查。 | `reproduction.log`、`coordinator-contracts.log`、`final-contracts.log`、`production/`。 |
| [`workflow-concurrency-20260912/`](workflow-concurrency-20260912/README.md) | 验证案件快照、Worker 领取/完成、迟到 Matrix 元数据、过期 attempt 和 `RESULT_UNKNOWN` 在并发下不会覆盖新结果或重复提醒。 | `reproduced.log`、`targeted.log`、`contracts.log`、`browser/`、`production/`。 |
| [`recovery-consistency-20260912/`](recovery-consistency-20260912/README.md) | 验证人工恢复与旧 Worker 隔离、资金意图与效果、能力续签、案件代次、队列和恢复审计的一次提交；覆盖恢复后真实 MCP 闭环。 | `reproduced.log`、`permission-contracts.log`、`permission-gap-before.json`、`browser/`、`production/`。 |
| [`runtime-safety-20260912/`](runtime-safety-20260912/README.md) | 验证任务 ID 强制绑定、未知结果持久化、重置互斥、清空播种原子性和恢复续签失败时的状态保护。 | `regressions-before.log`、`regressions-after.log`、`release-gate.log`、`production/`、`SHA256SUMS`。 |
| [`recording-consistency-20260912/`](recording-consistency-20260912/README.md) | 验证单案重新准备、全量重置、网关重启、报告按 recording id 隔离以及导出文件原子替换，保留修复前失败复现。 | `regressions-before.log`、`regressions-after.log`、`isolated/`、`production/`、`SHA256SUMS`。 |
| [`deployment-safety-20260912/`](deployment-safety-20260912/README.md) | 验证部署文件锁、维护租约、跨重启保护、冷部署健康边界、重置互斥和发布前后的业务数据保留。 | `guard-boundaries.json`、`failure-maintenance.json`、`full-cold-result.json`、`production/`、`SHA256SUMS`。 |
| [`project-audit-20260912/`](project-audit-20260912/README.md) | 整项目复核的一轮完成批次：资金 Schema 启动检查、迁移/观测配置、PG 发布门禁、前端漏洞修复、镜像扫描和正式页面检查。 | `acceptance.json`、`gate-before.log`、`release-gate.log`、`trivy-image.json`、`production/`。 |

## D. UI、观测和截图材料

这些包主要证明页面、同源嵌入、移动端布局和录屏画面。它们是展示层证据，不能单独替代数据库、
StageTask 或 Matrix 事件证据。

| 证据包 | 这是做什么的 | 主要入口 / 关键内容 |
| --- | --- | --- |
| [`grafana-embed-20260912/`](grafana-embed-20260912/README.md) | 0.5.1 正式页面的 Grafana iframe、12 个面板、全屏、移动端、只读边界、Grafana 暂停后的不可用提示和数据保留验收。 | `production/browser-result.json`、`production/*.png`、`browser-unavailable.json`、`grafana-unavailable.png`、`SHA256SUMS`。 |
| [`ui-cold-review-20260912/`](ui-cold-review-20260912/README.md) | 0.5.8 页面状态与冷部署复核：CLOSED/ROLLED_BACK 语义、合成数据声明、运行通道显示、案件切换防迟到响应、冷启动和生产发布。 | `browser/`、`cold/`、`frontend-tests.log`、`ui-build.log`、`production/`、`SHA256SUMS`。 |
| [`ui-recording-2026-08-31/`](ui-recording-2026-08-31/) | 2026-08-31 的静态录屏截图集，按画面展示回滚、关闭、人工审批、Trace/审计、价值、工程、权限和任务证据。它只有图片，没有机器可读运行链。 | `01-case2-rollback.png` 至 `08-task-evidence.png`。 |

## E. 历史决赛准备与反馈复现

这些包保留版本演进和复赛反馈的原始轨迹。它们对定位“当时为什么失败、后来改了什么”有用，
但不能被引用成当前 rc3 的运行结果。

| 证据包 | 这是做什么的 | 主要入口 / 边界 |
| --- | --- | --- |
| [`finals-0.6.0-rc2-20260917/`](finals-0.6.0-rc2-20260917/README.md) | rc2 决赛准备：模型通道切换、额度恢复、Worker 参数探针、两条演示栈复跑、镜像 SBOM/扫描和发布门禁。后续 rc3 运行已另行归档。 | `README.md`、`rerun-CASE-*.json`、`verification/`、`revguard-0.6.0-rc2.cdx.json`。 |
| [`finals-acceptance-20260912/`](finals-acceptance-20260912/README.md) | 0.5.0 决赛整改验收：发布、PG 恢复合同、105/105 确定性评测、前端、MCP 角色隔离、观测和数据保留。 | `acceptance.json`、`release-checks.log`、`postgres-recovery-tests.log`、`deployment-*.json`。 |
| [`finals-review-20260911/`](finals-review-20260911/README.md) | 复赛反馈中的恢复协议缺口复现，只说明当时的失败形态，不是整改后的通过证据。 | `probe_recovery_gap.py`、`recovery-probe.json`。 |

## 和其他文档的关系

- 证据分级、允许/禁止的对外表述见 [`docs/EVIDENCE_HONESTY.md`](../EVIDENCE_HONESTY.md)。
- 项目运行与材料入口见 [`revguard/README.md`](../../README.md) 和根仓库 `submission/` 目录。
- 单个证据包的复现命令、环境和限制优先以该包自己的 `README.md`、`manifest.json` 或结果 JSON 为准；
  本页的表格只负责回答“这个包是做什么的”。
