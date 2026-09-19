# Element 房间审批桥接

当 `REVGUARD_TEAM_TRANSPORT=matrix` 且启用
`REVGUARD_MATRIX_APPROVAL_BRIDGE_ENABLED=true` 时，AgentTeams 主房间会在案件
进入 `WAITING_FOR_APPROVAL` 后，由 `revguard-orchestrator` 发布一条
“【待审批】佣金差额调整”。正文只呈现审批人需要理解的业务摘要、金额核对、
差异原因、执行边界和角色分工；机器使用的结构化上下文保留在后端审计记录中，
不再把 JSON 直接展示给审批人。

审批人可以在 Element 中直接回复该消息：

```text
批准
```

或者：

```text
驳回：原因
```

如果房间里只有一个未过期的待审批单，也可以直接发送 `批准`；驳回仍必须带原因。服务端会把这类消息标记为 `single_pending_direct`，绑定到唯一待审批单，不会在多个待审批单之间猜测。

审批请求和结果使用已加入 Team 房间的 AgentTeams 服务身份发送，Element 中的 `admin` 只作为人工审批人，不再出现 `admin` 给 `admin` 的视觉混淆。服务端接受同时满足以下条件的决定：

- 回复目标是当前审批请求事件，或房间内只有一个有效待审批单且启用了单待审批直接发送兼容；
- 房间是 `REVGUARD_MATRIX_ROOM_ID` 配置的权威 AgentTeams 房间；
- 发送者在 `REVGUARD_HITL_MATRIX_USERS_JSON` 白名单，并拥有 `approval:decide`；
- 案件仍为 `WAITING_FOR_APPROVAL`，审批单仍为 `PENDING`；
- 回复在请求有效期内，且通过案件、审批单和动作绑定校验。

有效回复会复用 WebUI 的审批事务。`admin` 只提供人工授权，不负责调度 Worker；
批准后仍由 `revguard-orchestrator` 继续调度 `revguard-executor`，随后交给
`revguard-verifier` 独立复核。驳回时由编排智能体停止后续调度。房间会收到中文
“【审批结果】”摘要，WebUI 在待审批状态下会自动刷新。

录制环境建议使用独立案件和独立数据库；不要重置已经完成的正式 Case1/Case8。
