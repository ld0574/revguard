# v0.6.0 最终发布门禁

执行时间：2026-09-18 03:05–03:08 UTC。执行主机：`10.10.10.202`。工作区：
`/root/rgops/gate-v060/revguard`。所有命令在远程 Docker 容器内执行，测试数据库使用独立
`revguard-verify-shorttask_default` 网络中的一次性 PostgreSQL 容器。

## 后端 `verify-release`

镜像：`revguard-verify-1789691278-3093769-checks:latest`；容器：
`revguard-final-release-gate`；退出码：`0`。

| 检查 | 结果 |
|---|---|
| 默认测试 | `Ran 378 tests`，`OK (skipped=83)` |
| PostgreSQL 集成 | `Ran 83 tests`，`OK`（同一套件的 DSN 集成项） |
| 覆盖率 | `TOTAL 5554 567 90%`，门禁 `--fail-under=90` |
| 确定性评测 | `105/105`，UTC，median benchmark |
| 依赖漏洞 | `pip-audit: No known vulnerabilities found` |
| Lint / 生成物 / 版本 | Ruff、Skill 三级摘要、OpenAPI、CHANGELOG、官网回放索引均通过 |
| 静态安全 | Bandit 无失败项；仅保留既有 `nosec` 提示 |

正式镜像由同一份 `v0.6.0` 源码在 202 Docker 构建，镜像摘要为
`sha256:ae303afa7ba66be32a11538dd8ed3074caea2769c228c847b2d880854e2b6c1b`。
Trivy 0.69.3 的完整扫描随 Release 附件提供：`CRITICAL=0`、`HIGH=44`、`MEDIUM=52`、
`LOW=58`、`UNKNOWN=3`；这 44 个 HIGH 在当前 Debian 13.7 数据库中都没有 `FixedVersion`，
因此没有把“扫描完成”包装成“镜像无漏洞”。Python 依赖的可修复项由 `pip-audit` 单独门禁，结果为无已知漏洞。

## 官网与前端

- `revguard-final-replay-gate`：回放索引校验 + 11 项回放/可观测回归测试，退出码 `0`。
- `revguard-final-ui-gate`：9 项 UI 状态测试、4 项 Sites 测试、Vite production build，退出码 `0`。
- 回放索引发布版本为 `0.6.0`；`capture.kind=CAPTURED_FROM_RUNTIME`，
  `capture.source_release=0.6.0-rc3`。两个正式包回放文件都带同一 provenance，页面会明确显示
  “静态回放不调用模型或后端”。

这份证据只证明最终发布树的可重复门禁，不把候选运行记录改写成一次不存在的正式运行；真实 ERPNext、
AgentTeams/Matrix、真人审批、PostgreSQL 写入/恢复和观测证据仍以 `docs/evidence/` 下对应的运行记录为准。

## 2026-09-18 正式包复验

同一冻结 `v0.6.0` 工作区 `/root/rgops/gate-v060/revguard` 在 2026-09-18 15:07–15:10 UTC
重新执行 `bash scripts/verify_docker.sh`，结果仍为：默认测试 `Ran 378 tests`、
`OK (skipped=83)`；PostgreSQL/Matrix 集成 `Ran 83 tests`、`OK`；覆盖率
`TOTAL 5554 567 90%`；确定性评测 `105/105`；`pip-audit` 无已知漏洞；Bandit 无失败项；
前端 9 项 UI 测试、4 项 Sites 测试与 production build 通过。证据目录：
`/root/rgops/gate-v060/revguard/.runtime/verification/revguard-verify-1789744050-1315947/`。
