# RevGuard 架构决策记录

ADR 只记录已经进入代码、测试和可运行契约的决策；路线图不伪装成现状。

| ADR | 决策 | 状态 |
|---|---|---|
| [0001](0001-deterministic-money-kernel.md) | 确定性金额内核 | 已采纳 |
| [0002](0002-executor-verifier-separation.md) | Executor / Verifier 分离 | 已采纳 |
| [0003](0003-tool-gateway-contract.md) | ToolGateway 契约 | 已采纳 |
| [0004](0004-state-machine-allowlist.md) | 状态机白名单 | 已采纳 |
| [0005](0005-capability-tokens.md) | 能力令牌 | 已采纳 |
| [0006](0006-skill-registry-source-of-truth.md) | Skill Registry 单一事实源 | 已采纳 |
| [0007](0007-scoped-mcp-skill-transport.md) | Worker-scoped MCP Skill transport | 已采纳 |
| [资金结果恢复](0004-money-outcome-recovery.md) | 同库资金事务、结果未知冻结、原操作对账与 fencing | 已采纳（自管模拟账务）；外部 ERP 协议与 HA/PITR 保持验收边界 |
