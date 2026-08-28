# RevGuard 可观测与 OpenTelemetry 语义映射

RevGuard 以每案 JSON Trace + Store Audit + Markdown 报告提供可回放证据。本地 Demo 使用 SQLite，正式 PostgreSQL/PolarDB 由数据库触发器强制 append-only 哈希链。Trace 导出在保留内部字段的同时增加一层向后兼容的 OpenTelemetry GenAI 属性投影。

## 映射

| 内部 span kind | `gen_ai.operation.name` | 其它标准属性 | 保留的项目属性 |
|---|---|---|---|
| `AGENT` | `invoke_agent` | `gen_ai.agent.name`、`gen_ai.workflow.name=revguard` | `revguard.case.id`、`revguard.span.kind` |
| `SKILL` | `execute_tool` | `gen_ai.agent.name`、`gen_ai.tool.name`、`gen_ai.tool.type=function` | 同上 |
| `TOOL` / `REMOTE_TOOL` | `execute_tool` | `gen_ai.agent.name`、`gen_ai.tool.name`、`gen_ai.tool.type=function` | 同上 |
| 其它内部 kind | 不强行映射 | 有 actor 时只映射 `gen_ai.agent.name` | 同上 |

顶层 `semantic_conventions` 明确记录参考版本、官方链接和 `development` 状态。项目不会为了字段齐全而虚构模型名、供应商、Token 数量或推理参数。

## 关联与安全

- `trace_id` 等于 case ID；span 有稳定 sequence、父子关系、UTC 起止时间、状态和错误。
- AgentTeams/MCP 调用把 transport、Matrix message ID、request ID、task ID、skill/tool receipt 写入 Trace 与 Audit，可跨证据包对账。
- Bearer key 与 `RGC1` 能力令牌在任何嵌套字符串中都会被替换为不可授权指纹。
- ERROR span 不被降级成 Evidence Gap；只有工具级 `ToolError` 才能转换为显式缺口。

## 边界与路线

当前已增加 JSON 结构化访问日志、`/api/v1/ops/metrics` JSON 端点、
`/api/v1/ops/metrics/prometheus` Prometheus text 端点、liveness/readiness 探针和
`config/alerts.yaml` 告警规则。日志不记录请求体或凭证。

这仍不表示生产观测后端、OTLP exporter、采样策略或跨进程传播已经验收。后续接入 OTLP 时，以本映射为 Adapter 输入，并新增 exporter 背压、采样与租户隔离设计。运维约定见 [`operations.md`](operations.md)。

录制排练的脱敏任务、Audit、Trace、报告与 SHA-256 清单位于
[`evidence/demo-rehearsal/manifest.json`](evidence/demo-rehearsal/manifest.json)。其中自动化批准
明确标记为 `simulated_human=true`；正式人审证据必须来自 WebUI 实际点击录屏。

参考：[OpenTelemetry GenAI attributes registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)（该语义约定仍标记为 Development）。
