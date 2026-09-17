# Changelog

## Unreleased — 2026-09-18

- 修复录制代次缺陷：注入的写后偏差（`REVGUARD_POSTING_TAMPER_*`）从全局一次性开关改为按案件记录消费状态，单案重新准备（`POST /api/v1/cases/{id}/reprepare`）会为新代次重新武装该案，不再出现第二次录制静默退化为正常结案、冲销与恢复演示消失；回归用例 `tests/test_api.py::test_15z_*`（`revguard/mocks.py`）。
- 评测快照在 10.10.10.202 Docker（Linux / Python 3.11）重跑：105/105 确定性场景与 7 路并行基准数字刷新，`scripts/validate_evaluation_snapshot.py` 校验通过（`docs/evaluation-summary.json`）。
- 发布门禁扩展：官网回放索引与数据包一致（`scripts/check_website_replay.py`，接入 `checks.yml`）、候选版本号必须在 CHANGELOG 与导出 OpenAPI 中同时出现。
- 发布门禁再扩展：新增 `scripts/check_changelog.py`，机读 CHANGELOG 结构（Unreleased 首节、`## <版本> — YYYY-MM-DD`、版本唯一且严格降序），并要求包版本 = CHANGELOG 最新发布条目 = 导出 OpenAPI 版本，打 tag 时再校验 tag 与包版本一致；负例用例见 `tests/test_changelog.py`。
- AgentTeams Manager glm-5.3-flash 生成预算修复：重建 Manager 镜像，使 bridge 重新投影 provider 时保持 `max_tokens=2048` + `reasoning_effort=low`；预算脚本新增 Manager 目标（端口 18799），新增从 `docker inspect` 原样重建 Manager 容器的脚本（证据 `docs/evidence/agentteams-manager-glm-20260918/`）。

## 0.6.0-rc3 — 2026-09-18

- 审批 = 参数承诺：规范化参数摘要同时落审批单与执行能力令牌，执行前三方比对，参数漂移即拒绝；重新授权走同一校验（`revguard/commitment.py`）。
- 执行引用监视器：未实际读取订单/合同/佣金台账即 `EVIDENCE_GAP`，资金分录携带事实引用与折叠锚点；开关 `REVGUARD_REQUIRE_EXECUTION_REFERENCES` 默认关闭（`revguard/execution_reference.py`）。
- Skill 三级摘要（manifest / instruction / callable）与加载期 fail-closed、运行期 `SKILL_INVOKED` 审计。
- 身份不可自报：请求体 `actor`/`scope` 一律 422；审计主体来自服务端 Bearer Principal。
- 真实 AgentTeams Matrix 链路：StageTask 绑定的 Worker 只读回执，真实案件 `CASE-82822305` 8/8 任务。
- GitHub Actions 校验工作流、官网运行回放页与六项核心创新、招标差距闭合清单。

## 0.6.0-rc2 — 2026-09-17

- Added a fail-closed ERPNext REST provider with authenticated receipts,
  pagination, provenance and schema/error handling.
- Split CASE-0001 and CASE-0008 onto independent orders and recording chains;
  added verifiable Element handoffs and Matrix-bound human approval evidence.
- Added the reproducible 10,000-row Olist experiment, official public fee-rule
  snapshots, controlled anomaly injection and ERPNext draft import.
- Added PostgreSQL physical read-replica deployment, lag-aware routing and
  primary fallback for stale or unavailable replicas.
- Expanded Prometheus/Grafana coverage for providers, model usage, replication,
  database waits, evidence gaps and money recovery.
- Added the static GitHub Pages site, adapter documentation, contribution and
  security policies, release evidence and finals presentation assets.

## 0.6.0-rc1 — 2026-09-17

- 决赛候选版首发：ERPNext REST 只读取证接入、Adapter Provider Registry 与
  证据来源标签（`PUBLIC_REAL` / `SYNTHETIC_DOMAIN` / `LIVE_SYSTEM` / `SYSTEM_GENERATED`）。
- CASE-2026-0001 正常闭环与 CASE-2026-0008 冲销恢复拆成两条独立运行记录。
- 10,000 行 Olist 公开真实交易实验、公开费用规则快照与受控异常注入。
- 口径统一（真实系统 / 真实调用 / 无企业生产数据）、官网首版与验收工具。

## 0.5.10 — 2026-09-12

- Made coordinator projection writes atomic with the case-version transition.
- Preserved old-worker isolation during recovery and production deployment.

Earlier detailed release evidence is retained under `docs/evidence/`.
