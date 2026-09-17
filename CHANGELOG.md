# Changelog

## Unreleased — 2026-09-18

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

## 0.5.10 — 2026-09-12

- Made coordinator projection writes atomic with the case-version transition.
- Preserved old-worker isolation during recovery and production deployment.

Earlier detailed release evidence is retained under `docs/evidence/`.
