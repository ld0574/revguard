# 整项目复核阶段验收：0.5.2

本轮修复了资金 Schema 启动检查、部署遗漏迁移/观测配置、PG 发布门禁遗漏、前端依赖漏洞及当前文档失真。全部构建、测试、浏览器和扫描在 10.10.10.202 Docker 执行；这是整项目审查中的一个完成批次，剩余检查见 [审查清单](../../final-project-review-20260912.md)。

| 项目 | 实际结果 | 证据 |
| --- | --- | --- |
| 缺少资金表的旧库 | 修改前错误地允许初始化；修改后在应用启动阶段拒绝 | migration-before.log、migration-after.log |
| 正式 Docker 发布门禁 | 主套件发现 188 项，其中 166 通过、22 项 PG 条件测试单独执行并通过；覆盖率显示 90%，105/105 确定性评测及生成物校验通过 | release-gate.log |
| 部署保护回归 | 另有 2 项通过：活动案件不能被 reset 绕过，状态查询失败不能当作空闲；部署配置保持原样 | deployment-guards.log |
| 前端 | Vite 6.4.3 与传递依赖修复后，构建、8 项 UI 测试、4 项 Sites 测试通过，npm 审计为 0 | release-gate.log、npm-remediation.log |
| Python | 固定依赖 pip-audit 与 Bandit 通过 | release-gate.log |
| 发布镜像 | Trivy 0.69.3 使用当次下载的漏洞库，Debian 13.6 与 Python 包无可修复 HIGH/CRITICAL 项 | trivy-image.json、trivy.log |
| 正式页面 | 12 个不同面板查询全部 200，无浏览器错误；1600×1050 全屏及 390×844 外层宽度检查通过 | production/browser-result.json、production/*.png |
| 正式服务 | API 0.5.2、PolarDB ready、Grafana 正常、Prometheus 4 目标 up；管理员/任意数据源查询仍拒绝 | production-health.json |
| 数据保留 | 原 8 案件、执行记录、9 条 ledger、261 行审计链前后相同 | deployment-before.json、deployment-after.json、deployment.log |

原始门禁确实以非零结果拒绝了有 5 项前端漏洞的版本，见 `gate-before.log`。最终的 166 项非 PG、22 项 PG 和 2 项部署保护共覆盖 190 个不同测试；不能把 PG 条件跳过当作通过。保护脚本的新增用例独立执行，未伪装成最后一次主套件里已经发现它们。

Trivy 扫描范围是发布镜像的系统/Python 漏洞，设置 `--severity HIGH,CRITICAL --ignore-unfixed`；不等同于所有严重级别、未知漏洞或宿主机审计。正式镜像 ID 与扫描元数据一致。前端构建工具不进入最终 Python 镜像，其漏洞由 npm 单独审计。

`preview/` 为隔离 API 页面验证，`production/` 为实际 19000 入口。看板查询真实 Prometheus，业务案件仍是合成数据；空闲时业务 P95 图可能没有新样本，不编造延迟数字。没有重跑生产资金案件，没有重新配置生产 Worker，也没有执行全量 AgentTeams 冷安装；全量部署脚本的完整冷启动仍在审查清单内。

`source-sha256.json` 固定本批相关源文件，`production-image.json` 记录正式镜像，`SHA256SUMS` 覆盖本目录。凭证、私有备份及部署 `.env` 均不纳入证据包。
