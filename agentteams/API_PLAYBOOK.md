# RevGuard AgentTeams API Few-shot Playbook

本手册是所有 Worker SOUL 的强制执行契约。Bearer API key 只允许由
AgentTeams Secret/Adapter 在传输层注入；模型输入、Matrix 消息、日志、Trace
和回复中都不得出现 key、`RGC1.` 能力令牌原文或其它凭证。

## 每次请求必须携带的关联信息

- `X-AgentTeams-Message-ID`：触发任务的 Matrix event/message ID；
- `X-Request-ID`：本次调用唯一 ID，例如 `REQ-MATRIX-20260810-001`；
- `X-RevGuard-Task-ID`：Orchestrator 已派发且绑定当前 Worker/Skill 的 Task ID；
- `case_id`：已有案件必须填写；
- `Authorization`：只由 Adapter 注入，Worker 不生成、不猜测、不回显。

API 返回 `X-Request-ID`、`X-Skill-Receipt`；Worker 在聊天中只报告这两个不可授权
的引用和结构化业务结论。

## Few-shot 1：正确的只读 Skill 调用

用户消息：`请核对 CASE-2026-0008 的订单 EZ202608001。`

Worker 调用（省略由 Adapter 注入的 Authorization）：

```http
POST /api/v1/skills/EvidenceCollectSkill/invoke
X-AgentTeams-Message-ID: $matrix-event-0008
X-Request-ID: REQ-EVIDENCE-0008-01
X-RevGuard-Task-ID: TASK-EVIDENCE-0008-01
Content-Type: application/json

{
  "case_id": "CASE-2026-0008",
  "input": {
    "partner": {"partner_id": "AGT-10001"},
    "order_id": "EZ202608001"
  }
}
```

成功响应后只报告：`已取得证据包，skill_receipt=SKR-...，request_id=REQ-EVIDENCE-0008-01。`
不要把 HTTP Header、凭证或整份含敏感字段的响应贴回 Matrix。

## Few-shot 2：可重试工具错误

Skill 内部 Tool 不可用时的响应：

```json
{"success": false, "error": {"type": "TOOL_UNAVAILABLE", "retryable": true}, "skill": "EvidenceCollectSkill"}
```

正确行为：保留同一 `X-AgentTeams-Message-ID`，生成新的 `X-Request-ID`，按退避策略
最多重试 3 次；每次记录 receipt。超过上限后返回 `WAITING_FOR_EVIDENCE`/人工升级，
不得补造结果。

## Few-shot 3：401 身份配置故障

响应：`401 API key 无效`。

正确行为：立即停止；只报告“Adapter 身份注入失败 + request_id”，通知 Manager 检查
Secret 映射。禁止在聊天中索要、猜测或测试其它 key，禁止重复盲重试。

## Few-shot 4：403 越权拒绝

Evidence Worker 尝试 `LedgerAdjustSkill`，响应 403。

正确行为：承认该动作不属于 Evidence 权限，把任务退回 Orchestrator，由 Executor 在
审批完成后执行。禁止修改 actor、scope 或请求体来绕过授权。

## Few-shot 5：业务安全错误

响应：

```json
{"success": false, "error": {"type": "AUTH_FAILED", "message": "提交金额超过该组件审批额度"}}
```

正确行为：停止写入，不拆单、不换组件、不复用其它案件 token；把 action_id、
request_id、tool_receipt 和错误类型交给 Risk/Approver 重新审批。不得回显能力令牌。

## Worker 调用边界速查

| Worker | 典型允许调用 | 必须拒绝 |
|---|---|---|
| intake | `CaseNormalizeSkill`、`EntityResolveSkill` | 台账写入、审批 |
| evidence | `EvidenceCollectSkill` | 所有写 Skill |
| policy | `PolicyVersionMatchSkill` | 金额计算、审批、写入 |
| calculation | `CommissionCalculateSkill`、订单只读 | 自行更改政策、台账写入 |
| rootcause | `DifferenceExplainSkill` | 修改证据或金额 |
| risk | `RiskClassifySkill`、`ApprovalRouteSkill` | 代替人工批准、台账写入 |
| executor | `PermissionCheckSkill`、`AdjustmentDraftSkill`、`LedgerAdjustSkill`、`LedgerReverseSkill` | 越组件/越额度/无审批写入 |
| verifier | `PostActionVerifySkill`、`PostRollbackVerifySkill` | 信任 Executor 回执代替查询 |
| knowledge | `CaseToDatasetSkill` | 直接发送邮件、修改资金数据 |

Agent 可见清单只包含 `Skill`；底层 Tool 由 Skill/状态机在服务端调用。Orchestrator
只拆解、路由和推进合法状态；它不得借用其它 Worker 的 Principal。
