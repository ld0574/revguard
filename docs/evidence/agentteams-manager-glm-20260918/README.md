# Manager glm-5.3-flash 生成预算修复（2026-09-18）

## 结论

`agentteams-manager` 的 `glm-5.3-flash` 生成预算已固定为 **`max_tokens=2048` + `reasoning_effort=low`**，
并在镜像重建、容器重启和一个 MinIO 同步周期后保持生效。真实流式工具调用探针返回 `MODEL_READY`
（[manager-runtime-probe.json](manager-runtime-probe.json)）。

## 现象与根因

- 上游 key 切换到 `tokens1688` 的 `glm-5.3-flash` 后，Manager 侧表现为“没有调用成功”。HTTP 实际是 200，
  但 `content` 为空、预算被写进 `reasoning_content`。
- `glm-5.3-flash` 恒为思考模型：不传 `reasoning_effort`、只给 512 预算时，思考会吃光全部预算，最终
  `content` 为空。上游 `thinking.type` 仅支持 `enabled`，不能用来关闭思考；选择是 `reasoning_effort=low`
  同时给足 `max_tokens`。
- 202 上 Manager 镜像 `revguard-agentteams-manager:glm-20260917` 的 `copaw_worker/bridge.py` 在重新投影
  provider 时把 `glm-5.3-flash` 写成 `{"max_tokens": 512}`
  （[bridge-before.txt](bridge-before.txt)），而 Manager 启动与周期同步会触发重新投影，把通过运行态 API
  写入的 low 覆盖回 512。观测时间线：19:44:46 经 CoPaw API 写入 `low + 2048` 并回读成功；19:45:07
  provider 文件被重新投影回 512。

## 修复

1. 用仓库补丁 [`agentteams/copaw-runtime/patch_bridge.py`](../../../agentteams/copaw-runtime/patch_bridge.py)
   重建 Manager 镜像，bridge 对 `glm-5.3-flash` 输出
   `{"max_tokens": 2048, "reasoning_effort": "low"}`（[bridge-after.txt](bridge-after.txt)）；镜像构建时
   `verify_bridge.py` 的 worker/manager 双 profile 自检通过。
2. [`scripts/recreate_agentteams_manager.py`](../../../scripts/recreate_agentteams_manager.py) `--execute`
   从 `docker inspect` 原样重建容器（网络、挂载、已发布控制台端口、restart 策略、日志选项、entrypoint
   与环境变量不变），旧容器重命名为 `agentteams-manager-prev-*` 供回滚。
3. [`scripts/apply_agentteams_model_budget.py`](../../../scripts/apply_agentteams_model_budget.py) 新增
   Manager 目标（端口 18799）：`--include-manager` / `--manager-only`，经 CoPaw 官方模型 API 应用并回读校验。
   `scripts/agentteams_setup.sh` 的 glm 分支已默认带上 `--include-manager`。

## 验证

| 项目 | 结果 | 证据 |
|---|---|---|
| Manager 真实流式工具调用 + 工具结果续接 | `MODEL_READY`，11.61s，两次真实 `POST /v1/chat/completions` HTTP 200 | [manager-runtime-probe.json](manager-runtime-probe.json) |
| 三层生效一致 | `.copaw/providers.json`、`.copaw.secret/providers/custom/agentteams-gateway.json`、运行中 API 均为 `low + 2048` | [manager-effective-policy.txt](manager-effective-policy.txt) |
| 容器重启 | `docker restart agentteams-manager` 后回读仍为 `low + 2048` | 同上 |
| 跨同步周期 | 跨过一个 60s MinIO 同步周期后无回退 | 同上 |
| 预算脚本 | `--workers intake policy --include-manager` 三个目标全部 `verified` | 运行输出 |

镜像 ID 记录在 [images.txt](images.txt)。全部操作在 `10.10.10.202` Docker 内完成。

## 残余风险

- 正在运行的 10 个 Worker 容器仍使用 `revguard-agentteams-worker:glm-20260917`（bridge 同样硬编码 512），
  但运行态由 `agentteams_setup.sh` 在 Team Ready 后调用 `apply_agentteams_model_budget.py` 纠正；
  2026-09-18 复验 10/10 生效 `low + 2048`。Worker 下一次重建会使用带补丁的 `glm-20260918` 镜像。
- 本次修复只涉及 AgentTeams 运行时与部署脚本，不改变金额、政策、权限或资金执行逻辑。

## 边界

未打印或提交任何上游 key、内部网关凭据或 Matrix token；上游 Base URL 与 key 只保留在服务器权限受限的
安装环境文件中。
