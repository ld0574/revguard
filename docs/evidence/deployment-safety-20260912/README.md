# 0.5.9 部署互斥与跨重启维护保护

构建、测试、浏览器和故障均在 10.10.10.202 Docker 中执行。冷部署使用独立 Docker daemon、数据库、网络和卷；生产 8 个案件与原始审计不参与故障或重置测试。

## 本批次修复

- 部署脚本使用宿主机文件锁串行执行，修改配置前核对 Compose 项目和数据库拓扑；Docker inspect 失败时不能把仍存在的 API 当作空环境。
- 构建后在旧 API 内取得独占运行租约，检查活动运行、正在执行的 StageTask、资金未决操作与冻结通道；持锁直到旧 API 停止，兼容 0.5.8 旧镜像。
- 持久卷中的维护标记跨容器重启保留；业务 HTTP 和直接 MCP 在租约内检查标记。部署中途失败不会自动重新接受业务写入；最终验收后由部署者解除标记。
- 迁移和播种改为停机时执行。显式重置同时提交案件、网关账务基线与重置审计，避免新 API 重新导入旧 JSON。
- Docker exec 租约助手清理时按本次随机标识核对 PID，只终止自己的持锁进程。
- 真实完整冷部署暴露 PolarDB 初始化期间的过早就绪：`pg_isready` 在业务账号创建前即成功，连接随后报 role does not exist。健康检查改为核对官方入口脚本已进入常驻阶段，且业务账号能执行 SQL。

## 已完成的隔离检查

`release.log`：发布门禁通过，包括 SQLite 覆盖率检查、75 项 PostgreSQL 检查、105 个评估样例、生成契约和依赖安全扫描。新增容器读取失败保护后，`contracts-final.log` 另行通过 20 项部署/运行保护测试；`final-contracts.log` 核对最终源代码和保留的历史基线。

`guard-boundaries.json`：实际 Docker CLI 验证并发部署、错误拓扑（含 reset）、活动共享租约和无 team_run 的 RUNNING StageTask 都阻止替换，原 API 容器 ID 保持不变。单元测试中仅替换 Docker 传输的场景不冒称真实 Docker 证据。

`failure-maintenance.json`、`resumed-maintenance.json`：真实升级在 Prometheus 配置校验阶段失败，API 保持维护；重启后写入仍返回 503；恢复配置并重跑部署后才恢复就绪。

`reset-upgrade.json`：隔离已有完成案件、执行记录和旧账务 JSON，实际运行带 reset 的升级。重启后 8 案为新录制批次，旧执行移除、网关基线和逐案重置审计已提交，旧 JSON 未被重新导入。

`browser/maintenance-browser.json`：Chromium 可查看维护中的案件；点击启动调查显示维护说明，真实请求返回 503，案件仍为 CREATED。

## 验收边界

该机制协调单台 Docker 主机和合作式应用入口，不保护管理员直接改库或跨主机自动故障切换。资金结果未知时仍须遵循资金恢复门禁；不能靠部署 reset 绕过待对账状态。业务数据为合成演示数据，保留历史基线不会把本轮单机验证升级为企业收益或云 HA/PITR 证据。

## 完整冷部署与生产发布

`full-cold-initial-failed.log` 保留首次发现过早就绪的失败记录。修复健康检查后，重新清空**仅隔离项目**的数据库卷，`full-cold-deploy.log` 与 `full-repeat-upgrade.log` 均退出 0；`full-cold-result.json` 显示重复升级前后 8 个初始案件、5 条基线 ledger 和 8 行有效审计链一致。独立环境的真实 Controller/Manager 来自 202 已缓存的官方与 Sol 适配镜像；平台作为部署脚本的安装前提另行准备，不能把该结果描述为 `deploy_demo.sh` 自带完整平台安装器。10 个角色采用 Sol，Team 9/9 Ready，真实模型网关预检和 9 个 MCP 角色的访问隔离检查通过；没有执行完整 Matrix 财务业务链。

现网已发布 0.5.9。先完成数据库逻辑备份、源码/私密配置备份和旧镜像保留，再用本批次的独占租约、持久维护标记替换 API，并更新 PolarDB 健康检查。发布直接使用隔离验收和 Trivy 扫描通过的同一镜像，现网不再构建或下载依赖。现有 AgentTeams 模型及上游凭证保持不变。

`deployment-before.json` 与 `deployment-after.json` 完全一致：原 8 个案件、execution 哈希、9 条 ledger 和 261 行有效审计链保留；没有重置或重跑生产案件。`image-scan.json` 的 ImageID 与 `production-image-id.txt` 一致，可修复 HIGH/CRITICAL 为 0（使用 `--ignore-unfixed`，并非宣称不存在任何漏洞）。`production/` 保存发布后的只读大屏验收。

日志仅规范化行尾空白；`SHA256SUMS` 校验交付证据文件。
