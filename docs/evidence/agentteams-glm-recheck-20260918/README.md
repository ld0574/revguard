# glm-5.3-flash 参数口径复验（2026-09-18 02:46，202）

## 为什么复验

决赛口径要求 Worker 模型参数固定为 **`reasoning_effort: low` + `max_tokens: 2048`**。
2026-09-18 凌晨 10 个 Worker 容器重建过一次，需要确认 Controller 注册表与官方模型
配置 API 没有被写回旧值，并确认模型链路仍然真实可用。

## 结论

| 项目 | 结果 |
|---|---|
| 10/10 Worker 生效参数 | `{"max_tokens": 2048, "reasoning_effort": "low"}` |
| 真实流式工具调用 + 工具结果续接 | 4/4 Worker 通过，全部返回 `MODEL_READY` |
| 调用耗时 | 8.28–8.91 秒 |

生效值直接读取 CoPaw `ProviderManager.get_effective_generate_kwargs()`，
即 Worker 真正会发给上游的 kwargs，不是配置文件里的声明值。

## 复现命令（在 202 上执行）

```bash
# 逐个回读生效参数（10 个 Worker）
docker exec -i -e HOME=/root/.copaw-worker/revguard-intake \
  agentteams-worker-revguard-intake /opt/venv/standard/bin/python - < probe.py

# 真实流式工具调用 + 工具结果续接（合成只读工具，无业务副作用）
docker exec -i -e HOME=/root/.copaw-worker/revguard-intake \
  -e COPAW_PROBE_PORT=8088 -e REVGUARD_EXPECTED_MODEL=glm-5.3-flash \
  -e REVGUARD_EXPECTED_MAX_TOKENS=2048 \
  agentteams-worker-revguard-intake /opt/venv/standard/bin/python - \
  < scripts/verify_agentteams_model_runtime.py
```

## 探针踩坑（重要）

直接 `docker exec` 进 Worker 时，容器镜像环境变量把 `HOME` 指到
`/root/agentteams-fs/agents/<worker>`，该路径下的 `.copaw.secret/providers/custom`
是**空的**；Worker 真实使用的凭据目录是 `/root/.copaw-worker/<worker>/.copaw.secret`。
因此不带 `-e HOME=/root/.copaw-worker/<worker>` 的探针会以
`ProviderManager` 找不到 provider 收场（输出
`{"error_type": "RuntimeError", "passed": false}`），**这是探针环境问题，不是模型
调用失败**。跑任何 Worker 内部模型探针都必须显式覆盖 `HOME`。

## 文件

| 文件 | 说明 |
|---|---|
| `effective-*.json` | 10 个 Worker 的生效 `generate_kwargs` 回读 |
| `runtime-probe-{orchestrator,evidence,executor,verifier}.json` | 真实流式工具调用 + 工具结果续接结果 |
| `runtime-probe-*.err` | 探针标准错误（仅日志行，无凭据） |

## 边界

`reasoning_effort=low` 只降低思考量，不关闭思考；金额、政策、权限、状态迁移与资金
操作仍由确定性代码决定。上游思考长度会波动，长内联 `adapter_command` 仍有偶发自然
语言应答风险，因此业务触发器继续使用短 `--task-id` 命令。
