# 发布门禁复跑（2026-09-18，master 树）

**目的**：9/18 改了 `docker-compose.finals.yml`（演示栈模型用量记账）、`docker-compose.observability.yml`
（Prometheus 配置开关）、新增 `config/observability/prometheus.rehearsal.yaml` 与 1 项回归用例后，
在当前 master 树上把完整发布门禁再跑一遍，作为封版前的"当前树可发布"证据。

**执行**（全部在 10.10.10.202 Docker 内，独立 compose 项目，跑完自动清理）：

```bash
cd /root/rgops/gate-master/revguard          # master 树副本（不含 .runtime）
bash scripts/verify_docker.sh
```

- 隔离项目：`revguard-verify-1789691278-3093769`
- 证据目录：`/root/rgops/gate-master/revguard/.runtime/verification/revguard-verify-1789691278-3093769/`
  （`build.log` / `backend.log` / `frontend.log` / `cleanup.log`）
- 结束行：`Docker 发布验证全部通过：…`

## 结果（逐项取自 `backend.log` / `frontend.log`）

| 项 | 结果 |
|---|---|
| 后端单测 | `Ran 371 tests in 46.407s` → `OK (skipped=83)`（83 项为需要 PostgreSQL/Matrix DSN 的集成用例） |
| PostgreSQL 集成套件（带 DSN） | `Ran 83 tests in 24.713s` → `OK` |
| 覆盖率门禁 | `TOTAL 5554 567 90%`（`--fail-under=90`） |
| 确定性评测 | `verified docs/evaluation-summary.json: 105/105`（median benchmark） |
| 依赖漏洞 | `pip-audit` → `No known vulnerabilities found` |
| 静态安全 | `bandit -q -r revguard scripts` 通过（无输出即无发现） |
| Lint / 生成物一致 | `ruff`、Skill 三级摘要、Skill 清单、OpenAPI、官网回放索引、CHANGELOG 全部通过 |
| 前端 | UI 镜像内 4 项子测试 `# pass 4 / # fail 0`，含"Sites 打包所需文件"检查 |

## 与封版的关系

- 本文件记录的是 **9/18 master 树**的结果；9/20 正式封版时，`v0.6.0` 必须在**最终提交**上重跑
  `bash scripts/verify_docker.sh`，并用那次输出刷新官网 / 讲稿 / PPT 里的测试与覆盖率数字（见 runbook §6.6）。
- 本轮改动只涉及 compose 挂载开关、观测配置与文档，未触碰金额内核、审批、执行与恢复路径；
  演示栈与常驻栈的运行态验证见 runbook §9.3 / §9.4 与
  `docs/evidence/agentteams-glm-recheck-20260918/`、`docs/evidence/agentteams-handoff-20260918/`。
