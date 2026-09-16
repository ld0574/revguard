# ADR-0004：状态机白名单

- 状态：已采纳
- 日期：2026-08-12

## 背景

案件状态由多个 Agent、审批回调和异常分支共同驱动。任意字段改写会产生跳过审批、跳过验证或无法解释的终态。

## 决策

19 个业务状态只允许经 `transition_case` 迁移；普通迁移采用显式白名单，共 24 条。除 `ROLLED_BACK`、`CLOSED`、`FAILED` 外，进行中状态可统一失败到 `FAILED`。每次迁移先校验，再写 Audit 与持久化时间。

驳回不是短路响应：完整路径为 `WAITING_FOR_APPROVAL → REJECTED → KNOWLEDGE_ARCHIVED → CLOSED`，并生成 Shared State、Trace、case memory 与报告。

## 后果与验证

- 非法跳转立即失败，不产生伪成功状态。
- 状态、Audit 与产物可按 case ID 对账。
- 新增状态或路径必须同步白名单和状态机测试。
