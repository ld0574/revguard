# RevGuard — 企业渠道佣金与结算异常多 Agent 协同平台

> GOAI「新智基座｜Agent Infra」赛道参赛作品。
> 面向企业渠道业务中的佣金、回款、结算、激励异常，构建**可执行、可验证、可审批、可回滚、可审计**的多 Agent 协同闭环。

设计文档：[`docs/企业渠道佣金与结算异常多Agent协同平台_需求与架构设计.md`](../docs/企业渠道佣金与结算异常多Agent协同平台_需求与架构设计.md)

## 核心链路

```
案件进入 → 证据收集 → 政策匹配 → 佣金复算 → 差异解释 → 风险审批 → 受控执行 → 独立验证 → 审计沉淀
```

## 设计红线（来自 ADR）

| 编号 | 决策 | 落地位置 |
|---|---|---|
| ADR-001 | LLM 不直接计算金额，佣金由确定性规则引擎复算 | `revguard/rule_engine.py` |
| ADR-002 | Executor 与 Verifier 分离，验证器必须独立重新查询 | `revguard/orchestrator.py` |
| ADR-003 | 首期全部使用 Mock 系统，与真实接口共用同一 Schema | `revguard/mocks.py` |
| ADR-004 | 不为堆叠而堆叠组件，仅保留能证明必要性的依赖 | 全仓库 |

## 快速开始

### 本地最小闭环（零外部依赖，仅需 Python 3.11+）

```bash
cd revguard
python3 scripts/run_demo.py            # 跑通 7 个 Golden Case，产出审计报告与 Trace
python3 -m unittest discover -s tests  # 运行全部单元 / 集成测试（42 个，API 测试自动跳过）
```

完整测试（含 8 个 API 冒烟测试，需 fastapi + httpx）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -s tests   # 50/50 通过
```

运行产物（自动沉淀）：

- `docs/reports/CASE-*.md` — 每个案件的审计报告（含计算明细、证据链、审批与执行快照）
- `data/outputs/traces/CASE-*.json` — 全链路 Trace（Agent / Skill / Tool / Rule / Approval / Execution span）
- `data/outputs/case_memory/*.json` — 沉淀的案例记忆（Golden / Bad Case 评测数据）

### API 服务（可选，用于前端 / AgentTeams Worker 调用）

```bash
pip install -r requirements.txt
uvicorn revguard.api:app --host 0.0.0.0 --port 9000
# 打开 http://localhost:9000/docs 查看交互式 API 文档
```

### Docker 部署（用于和 AgentTeams 同机部署）

```bash
docker compose up -d --build
```

## 目录结构

```
revguard/
├── revguard/            # 核心 Python 包
│   ├── models.py        #   领域模型与状态机定义
│   ├── rule_engine.py   #   确定性规则引擎（ADR-001）
│   ├── policy_matcher.py#   政策版本 Time Travel
│   ├── risk.py          #   L0-L3 风险分级
│   ├── skills.py        #   Skill 注册中心（能力抽象层）
│   ├── mocks.py         #   Mock CRM/财务/合同/佣金/审批 + 工具契约（ADR-003）
│   ├── orchestrator.py  #   Case 状态机编排 + 全链路 Trace
│   ├── store.py         #   SQLite 持久化（案件/证据/审批/执行/审计）
│   ├── trace.py         #   Trace 记录器
│   └── api.py           #   FastAPI 服务（可选）
├── agentteams/          # AgentTeams Worker 定义（SOUL.md）与部署说明
├── data/
│   ├── fixtures/        #   Mock 系统业务数据（10 代理商/订单/回款/政策版本/台账）
│   ├── golden_cases/    #   Golden / Bad Case 定义
│   └── outputs/         #   运行沉淀（Trace、案例记忆）
├── docs/                # 工程文档
│   ├── agents.md        #   Agent Identity 登记总表（10 个 Agent × 8 字段）
│   ├── skills.md        #   Skill 清单（14 个，由 gen_skill_docs.py 生成）
│   ├── api.md           #   API 参考
│   ├── demo-script.md   #   Demo 剧本（对照设计文档 §20.3 必演清单逐项映射）
│   └── reports/         #   自动生成的案件审计报告
├── scripts/
│   ├── seed_demo.py     #   初始化演示数据库
│   └── run_demo.py      #   端到端 Demo 入口
└── tests/               # 单元 / 集成 / 端到端测试
```

## AgentTeams 集成

多 Agent 编排以 [AgentTeams](https://hiclaw.io)（原 Hiclaw）为协同基点：

- Worker 定义见 `agentteams/workers/*.md`（SOUL.md 身份定义）；
- 部署与创建方式见 `agentteams/README.md`；
- Worker 通过统一工具契约（`POST /api/v1/tools/call`）调用 Skill 层，与 Mock/真实系统解耦。

## 开源协议

Apache-2.0（见 `LICENSE`）。
