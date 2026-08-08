# AgentTeams 集成说明

RevGuard 以 [AgentTeams](https://hiclaw.io)（原 Hiclaw）为多 Agent 协同设计基点。
本目录存放各 Worker 的 SOUL.md 身份定义，部署到 AgentTeams 后即可通过
Element Web 聊天室驱动多 Agent 协同闭环。

## 两种运行路径

| 路径 | 用途 | 入口 |
|---|---|---|
| **确定性闭环**（本仓库 `revguard/`） | 可复现评测、Golden Case 回放、单元/集成测试 | `python3 scripts/run_demo.py` |
| **AgentTeams 协同**（本平台部署） | 现场 Demo、人机对话驱动、Worker 编排展示 | Element Web 聊天室 |

两条路径共享同一套 **Skill 层与工具契约**：Worker 通过
`POST {REVGUARD_API}/api/v1/tools/call` 调用工具、通过
`POST /api/v1/cases/{id}/run` 触发闭环，保证"聊天演示"与"可复现评测"结果一致。

## 部署步骤

### 1. 部署 RevGuard API（与 AgentTeams 同机）

```bash
cd revguard
docker compose up -d --build
# API 位于 http://<host>:19000（宿主端口 19000 → 容器 9000），文档 http://<host>:19000/docs
```

如需被 AgentTeams Worker 容器访问，将 `revguard-api` 接入 AgentTeams 的
docker 网络（或直接用宿主机 IP:19000）。

### 2. 创建 Worker（在 agentteams-controller 容器内执行）

```bash
docker exec -it agentteams-controller bash

agt apply worker --name revguard-intake \
  --soul-file /path/to/revguard-intake.md --model moonshotai/kimi-k3

# 依次创建其余 Worker：revguard-evidence / revguard-policy /
# revguard-calculation / revguard-rootcause / revguard-risk /
# revguard-executor / revguard-verifier / revguard-knowledge
```

> 也可以在 Element Web 里直接告诉 Manager：
> "创建一个名为 revguard-intake 的 Worker，SOUL 如下……"，Manager 会自动处理。

### 3. 组成 Team 并演示

```bash
agt create team --name revguard-team \
  --workers revguard-intake,revguard-evidence,revguard-policy,revguard-calculation,revguard-rootcause,revguard-risk,revguard-executor,revguard-verifier,revguard-knowledge
```

在 Element Web 中向 Team 发送 Golden Case 申诉文本（见 `data/golden_cases/`），
Worker 按 SOUL 边界协作，高风险节点回到人工审批。

## 协同映射（赛道要求 §8.1/6.3）

| 要求 | RevGuard 落地 |
|---|---|
| ≥3 个不同职能 Agent | 9 个职能 Worker + Orchestrator，职责与写权限严格分离 |
| 任务拆解 | Orchestrator 按状态机拆解为 10 个阶段任务 |
| 上下文传递 | Shared Case State（结构化 Artifact，非长文本） |
| 协同执行 | 证据采集并行批次、审批人工等待节点、失败重试 |
| 状态追踪 | Case 状态机 + Task 状态 + 全链路 Trace |
| 高风险审批/回滚 | L0-L3 分级、审批凭证、幂等键、快照、rollback_token |

## Worker 清单与 SOUL 文件

| Worker | SOUL | 写权限 |
|---|---|---|
| revguard-orchestrator | workers/revguard-orchestrator.md | 否 |
| revguard-intake | workers/revguard-intake.md | 否 |
| revguard-evidence | workers/revguard-evidence.md | 否 |
| revguard-policy | workers/revguard-policy.md | 否 |
| revguard-calculation | workers/revguard-calculation.md | 否 |
| revguard-rootcause | workers/revguard-rootcause.md | 否 |
| revguard-risk | workers/revguard-risk.md | 仅审批单 |
| revguard-executor | workers/revguard-executor.md | 是（唯一） |
| revguard-verifier | workers/revguard-verifier.md | 否 |
| revguard-knowledge | workers/revguard-knowledge.md | 仅知识库/草稿 |
