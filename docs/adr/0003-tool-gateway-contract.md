# ADR-0003：ToolGateway 契约

- 状态：已采纳
- 日期：2026-08-12

## 背景

直接散落调用 CRM、合同、政策和财务系统会导致身份、自报 scope、重试、脱敏和回执语义各自为政。

## 决策

所有业务工具通过 ToolGateway 统一执行：服务端 Principal 映射 actor，`TOOL_REQUIRED_SCOPES` 与 actor 固有 scope 取交集授权；调用返回结构化成功/错误、`tool_receipt` 和可审计元数据。工具级失败可转为 Evidence Gap；代码错误和返回契约错误保留 ERROR span 并向上抛出。

遗留 `/tools/call` 默认关闭；即使显式开启，也只允许 Evidence 兼容身份调用只读工具，启动时输出醒目告警。

## 后果与验证

- Worker 不能通过请求体自报 actor 或扩大 scope。
- 所有写操作仍需 Skill 安全边界、审批或能力令牌；旧 Tool API 不能绕过。
- Adapter 可替换为真实系统，但授权、回执和错误分类契约保持不变。
