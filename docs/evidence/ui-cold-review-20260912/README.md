# 0.5.8 页面状态、最小部署与材料核对

所有构建、测试、服务和浏览器都在 10.10.10.202 Docker 内运行。本批次修改前端状态表达、案件切换请求保护及发布版本号；资金业务实现沿用 0.5.7。没有重新执行生产资金流程，没有重置原案件。

## 已修正

- CLOSED 不再一律显示红色：有执行记录且独立验证通过才显示绿色；驳回/无写入关闭保持中性。ROLLED_BACK 缺少独立复核证据时提示待核实。
- 合成数据声明始终保留在固定顶部，覆盖 1600、1180、760、390 像素宽度及页面滚动。
- 执行模式显示为 MCP 参考链路或 AgentTeams · Matrix，不再仅凭配置宣称已连接；策略说明也不充当在线状态。
- 案件切换和重复加载使用请求代次，迟到的旧案件结果不能覆盖当前案件；权限栏将 15 分钟注明为单次签发时限。
- 根仓库提交说明修复不存在的文件路径、旧测试计数与“尚未完成整改”的过时表述。初赛 26 页、复赛 30 页 PPT 均已有团队介绍和行业迁移，逐页差异记录于根仓库 `submission/决赛材料核对与演示口径.md`。历史 PPT 和第一题原回答未改动。

## 验证

`frontend-tests.log`：9 项 UI 状态合同、4 项 Sites 测试通过；构建与 npm audit 通过，漏洞为 0。`contracts.log`：Skill 文档、OpenAPI 与调用示例检查通过。后端业务未改，本批次不冒称重新执行 291 项后端测试；对应证据保留在 [0.5.7](../recovery-consistency-20260912/README.md)。

`browser/`：真实 RevGuard API/MCP 参考流程，使用明确标识的合成可信审批夹具。覆盖 CREATED、WAITING_FOR_APPROVAL、写入后 CLOSED、驳回后 CLOSED、ROLLED_BACK；通过 CSS 状态、可见声明和四档宽度检查。浏览器人为延迟上一案件的真实响应，验证切换后仍展示新案；模拟浏览器传输失败，确认错误提示可见且未宣称已连接。此夹具不代表重新验证 Matrix 密码登录，人工登录与恢复故障已在 0.5.7 验收。

`cold-deploy.log`、`cold-upgrade.log`：在专属 Docker-in-Docker 容器、独立 daemon/卷内，实际执行 `bash scripts/deploy_demo.sh --local --observability`，先从空数据库部署，再重复升级。首次下载 Docker Hub 镜像受连接重置影响，通过镜像源拉取相同官方镜像摘要后继续。两次脚本均退出 0，8 个初始案件、执行、账务和审计快照一致；`cold/production-health.json` 验证 SQLite API 就绪、Grafana 共享看板可用、4 个 Prometheus 目标 up、受限 Grafana 路由拒绝访问。

此冷启动验收仅覆盖 SQLite/MCP 与观测拓扑，不包含外部 AgentTeams 全新安装，也未在该无身份源环境完成 L2 人工审批。完整 AgentTeams 冷启动和升级互斥仍在整体复核清单内。

## 生产发布

生产 API 更新为 0.5.8。发布前备份源代码与私密配置，取得既有运行屏障的独占租约并检查案件、活动任务、待对账资金与冻结通道，再替换 API。此发布租约是本次操作保护，尚未集成进通用部署脚本。

`deployment-before.json` 与 `deployment-after.json` 完全一致：原 8 个案件、各案 execution、9 条 ledger 和 261 行有效审计链均保留。`production/` 验证 12 个 Grafana 面板查询、全屏、手机宽度、4 个采集目标，以及实际 Case8 的状态与固定数据声明。生产没有注入故障。

`image-scan.json` 的 ImageID 与 `production-image-id.txt` 一致。Trivy 使用 `--ignore-unfixed` 扫描，当前发布镜像可修复 HIGH/CRITICAL 为 0。
