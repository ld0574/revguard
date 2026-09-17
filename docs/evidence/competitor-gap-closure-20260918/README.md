# 竞品分析差距闭合（2026-09-18）

来源：`docs/决赛/20260916-ClaudeCode决赛竞品深度分析.md` §5.5 决赛行动清单、§5.8 开源敞口自查。
本文记录逐项状态与可核验入口；未完成项保持显式 `OPEN`，不写进"已完成"。

## P0（全部闭合）

| 项 | 状态 | 证据 |
|---|---|---|
| `EVIDENCE_HONESTY.md` 证据分层 + 禁止/正确表述对照表 | ✅ | `docs/EVIDENCE_HONESTY.md`（四层标签、A/B/C 证据分级、10 组表述对照、运行通道表、"界面显示 X 后端是否有 X" 自查表） |
| 公开仓库同步到最新 | ✅ | GitHub `ld0574/revguard` main = `dc010f1`（子树同步自 Gitee master `4634a75`） |
| 发布 Release | ✅ | `v0.6.0-rc1`、`v0.6.0-rc2`（正式 `v0.6.0` 计划 9/20 从最终封板提交发布） |
| GitHub Pages 官网 | ✅ | <https://ld0574.github.io/revguard/>（Pages workflow 每次推送自动部署，最近一次 18 秒成功） |
| GitHub Actions 真跑校验 | ✅ | `.github/workflows/checks.yml`；run [35262130888](https://github.com/ld0574/revguard/actions/runs/35262130888) 全部步骤 success：Skill 三级摘要 / Skill 清单一致 / OpenAPI 一致 / Lint / 后端单测 |
| 仓库 Topics | ✅ | `agent` `fintech` `platform` `multi-agent` `ai-agents` `human-in-the-loop` `audit-trail` `erpnext` `fastapi` `postgresql` |
| `NOTICE.md` 归属与第三方许可 | ✅ | `NOTICE.md`（原创/上游/镜像许可分离，Olist CC BY-NC-SA 4.0 不分发原始数据） |
| PPT / 答辩统一口径"确定性控制面 / LLM 语义面强制切分" | ✅ | `revguard/README.md` 四重约束前言、`website/index.html` 首屏、`submission/决赛8分钟讲稿-v0.6.0.md` 1:00–2:10 段、`submission/决赛材料核对与演示口径.md` |

## P1

| 项 | 状态 | 证据 |
|---|---|---|
| 审计主体来自服务端会话、不可从请求体自报 | ✅ | `tests/test_api.py::test_10b_skill_identity_cannot_be_self_reported`：请求体带 `actor`/`scope` → 422，且不产生任何审计；干净调用写入的审计 actor = Bearer Principal |
| Skill 三级摘要（manifest / instruction / callable） | ✅ | `revguard/skill_integrity.py`、基线 `config/skill-integrity.json`、`scripts/gen_skill_integrity.py --check`、加载期 fail-closed（API/MCP 启动）、运行期写入 `SKILL_INVOKED`；`tests/test_skill_integrity.py` 10 项；CI 步骤之一 |
| 样例数据 + 一条命令起全套 | ✅ | `bash scripts/deploy_demo.sh --local`（SQLite + 进程内团队）/ `--full`（PolarDB + Matrix + 10 Agent），见 `README.md` 一键复现 |
| 审批 = 参数承诺（`SHA-256(canonical_json(...))`，参数漂移即失效） | ✅ | `revguard/commitment.py` + `docs/evidence/approval-commitment-20260918/`：摘要同时落审批单与能力令牌，执行时三方比对（审批单 / 令牌 / 重算），金额定点化；重新授权走同一校验；对抗探针 5/5 通过（`scripts/approval_commitment_probe.py`），单测 `tests/test_risk_and_mocks.py` 4 项 |
| 执行依赖来自实际读取（金额结论绑定实际读取过的事实 slot） | ⚠️ OPEN | 取证链路已保存工具回执与证据缺口；尚未实现"未读取即不可引用"的执行引用监视器 |

## 本轮新增的运行链路证据（非竞品项）

- 真实 AgentTeams Matrix 链路修复（StageTask 绑定 + 后端别名唯一性）：
  `docs/evidence/agentteams-matrix-binding-20260918/`，真实案件 `CASE-82822305` 8/8 StageTask
  `SUCCEEDED`、`CLOSED`、逐任务带 Matrix 房间与事件 ID。
- glm-5.3-flash 参数口径复验：`docs/evidence/agentteams-glm-recheck-20260918/`
  （10/10 Worker 生效 `max_tokens=2048 + reasoning_effort=low`，4/4 真实工具调用探针 `MODEL_READY`）。
- 官网运行回放页验收：`docs/evidence/website-replay-20260918/`（CASE-0001/CASE-0008 浏览器回放、移动端、子路径资源）。
- 审批参数承诺对抗验证：`docs/evidence/approval-commitment-20260918/`（5 场景探针 + JSON 机器可读结果）。

## 复核方式

```bash
python3 scripts/gen_skill_integrity.py --check     # Skill 三级摘要
python3 -m unittest tests.test_skill_integrity -v  # 摘要与审计落点
python3 -m unittest tests.test_api -v              # 身份不可自报
python3 scripts/approval_commitment_probe.py       # 审批参数承诺（5 场景，失败非零退出）
python3 -m unittest tests.test_risk_and_mocks -v   # 承诺 / 幂等 / 故障恢复契约
```
