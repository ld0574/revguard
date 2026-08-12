# RevGuard 可观测与 OpenTelemetry 语义映射

RevGuard 当前以每案 JSON Trace + SQLite Audit + Markdown 报告提供可回放证据。它不是 OTLP exporter；为降低后续接入成本，Trace 导出在保留内部字段的同时增加一层向后兼容的 OpenTelemetry GenAI 属性投影。

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
- AgentTeams 调用把 Matrix message ID、request ID、task ID、skill/tool receipt 写入 Trace 与 Audit，可跨证据包对账。
- Bearer key 与 `RGC1` 能力令牌在任何嵌套字符串中都会被替换为不可授权指纹。
- ERROR span 不被降级成 Evidence Gap；只有工具级 `ToolError` 才能转换为显式缺口。

## 边界与路线

当前实现证明“语义可映射、证据可回放”，不证明生产观测后端、采样策略或跨进程传播已经完成。后续接入 OTLP 时，以本映射为 Adapter 输入，并新增 exporter 背压、采样与租户隔离设计。

参考：[OpenTelemetry GenAI attributes registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)（该语义约定仍标记为 Development）。
