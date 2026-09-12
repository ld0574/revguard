# Luna 与空闲成本治理验收

过程、原因和边界见 [迁移记录](../../agentteams-luna-migration-20260912.md)。全部执行位于 10.10.10.202 Docker。

- `build.log`：Worker lite/standard 与 Manager 真实安装 bridge 的首次/重启配置检查；无 LLM 调用。
- `checks.log`：相关脚本 Ruff、Shell 语法以及部署 Principal/Matrix 运行时配置检查。
- `runtime-probe.log`：仅两次 Manager CoPaw 流式模型请求，Luna 工具调用及结果续接通过，输出上限各 128 token。
- `manager-runtime.json`：重建后实际激活模型 Luna、参数 none、心跳关闭，以及历史累计使用量。
- `worker-runtime.json`：重建后再次唤醒 Intake，实际模型和参数正确，10 分钟模型心跳已关闭。检查只读配置，没有发出模型请求。
- `persist-workers.log`：10 个 Worker 的 MinIO 活跃模型和 Luna 参数已写入并读回验证，内部凭证不变。
- `resources.json`：最终 Controller 资源、实际镜像与休眠状态。
- `cases-before.json` / `cases-after.json`：原演示案件和账务前后哈希一致。

API 0.5.10 继续就绪；Grafana/Prometheus 保留。管理端累计调用数 188 是运行时统计，不包含两次直接模型客户端探针，也不是账单金额。

本地 20 个 codex 分支已核对全部可达于 GitHub main 后清理，没有丢弃独有提交或删除远端分支。日志规范化行尾空白；`SHA256SUMS` 校验公开证据。私密配置与工作区备份不进入 Git。
