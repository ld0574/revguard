# RevGuard 复赛视频脚本（6 分钟主版）

> 核心叙事不是“又一个 Agent 平台”，而是：**让多个 Agent 在资金业务中不能越权、不能自签、
> 不能用聊天结论冒充执行结果，而且失败后能自动恢复并留下证据。**

## 录制前准备

```bash
make demo-ui
# 浏览器打开 http://127.0.0.1:9000
```

页面固定演示 `CASE-2026-0008`。它使用合成业务数据，但 MCP 调用、StageTask、状态迁移、
人工暂停、受控写入、独立验证、反向冲销和证据落盘都是真实可执行链路。正式录屏必须由
组员亲自点击审批按钮；`docs/evidence/demo-rehearsal` 中的自动批准只用于可复现排练，已明确
标为 `simulated_human=true`。

## 分镜与旁白

| 时间 | 画面与操作 | 旁白（可直接念） | 必须看清的证据 |
|---|---|---|---|
| 00:00–00:25 | 驾驶舱初始全景，镜头停在案件摘要与 8 段流水线 | “企业佣金异常不是问答题。它牵涉跨系统证据、历史政策、资金审批和执行后验证。RevGuard 解决的是：多个 Agent 如何在可控边界内真正把案件办完。” | 顶部 `合成业务数据 · 真实运行链路`；CASE-0008；180,000 / 18,000 / 32,400 KES |
| 00:25–00:55 | 缓慢扫过流水线和 Agent 权限矩阵 | “我们把职责拆成 Intake、Evidence、Policy、Calculation、RootCause、Risk、Executor、Verifier 和 Knowledge。Executor 能受控写入，但 Verifier 只有独立读取权，任何一个 Agent 都不能既执行又自证。” | Worker 名称、权限差异、Executor/Verifier 分离 |
| 00:55–01:15 | 点击“启动多 Agent 调查” | “现在启动的不是预置动画。Orchestrator 根据案件当前状态创建版本绑定的 StageTask；每个 Worker 通过自己的 scoped MCP Server 只看到被允许的 Skill。” | 按钮 loading；状态从 CREATED 开始推进 |
| 01:15–02:00 | 运行结束后查看 MCP Agent 任务表和证据表 | “8 个任务先后完成：标准化、实体解析、七路证据收集、政策匹配、确定性金额复算、根因解释、风险判断和审批路由。聊天文本不能推进状态，只有服务端落库的 SUCCEEDED StageResult 才能继续。” | `MCP`、真实 task ID、Skill、assigned actor、SUCCEEDED；8/8 证据与 hash/receipt |
| 02:00–02:35 | 镜头聚焦政策和金额详情 | “系统按订单时点回溯到 2026-Q3，而不是误用旧批处理版本；销售佣金 27,000，加回款佣金 5,400，应付 32,400。现有台账只有 18,000，差额 14,400。金额由 Decimal 规则内核计算，不交给大模型猜。” | policy version；组件公式；calculation hash；根因 `WRONG_POLICY_VERSION`、`MISSING_COMPONENT` |
| 02:35–03:10 | 停在橙色人工审批边界，暂时不点 | “风险为 L2，流程已经真正暂停。系统没有模拟审批，也没有后台偷偷继续。审批人、币种、总额度和逐组件额度会被绑定进短时能力凭证。” | `WAITING_FOR_APPROVAL`；PENDING approval；暂停至少 3 秒 |
| 03:10–03:25 | 组员亲自点击“批准并执行 14,400 KES” | “现在由独立 Approver 做出明确决定。这个动作会写入 `simulated_human=false` 的审计事件，然后 MCP Team 从持久化状态继续，而不是重新跑一条脚本。” | 人手点击录入；按钮 loading；状态进入 READY/EXECUTING |
| 03:25–04:05 | 查看执行、验证与回滚阶段逐步完成 | “Executor 按组件和额度写入，Verifier 随后重新查询台账，不使用 Executor 的回执自证。我们故意让首次读取出现 1 KES 偏差，验证立即失败，案件转入 ROLLBACK_REQUIRED。” | 两个 adjustment；`verification=FAILED`；variance=1 KES |
| 04:05–04:35 | 聚焦自动回滚与最终状态 | “系统使用一次性回滚能力做反向台账，再由 Verifier 第二次独立查询。只有恢复到执行前净额，案件才以 ROLLED_BACK 终止。失败没有被改写成成功，也没有静默吞掉。” | reversal entries；`ROLLBACK_VERIFIED=PASSED`；最终 `ROLLED_BACK` |
| 04:35–05:10 | 打开完整审计/工程证据页签 | “同一个 case ID 下保留 20 个成功 StageTask、9 个 Worker、16 种 Skill，以及 task、request、receipt、Trace 和审计事件。凭证会被替换为不可授权指纹，金额、审批、执行与回滚可逐项对账。” | 20/20；9 Workers；MCP；Audit/Trace；无原始 token |
| 05:10–05:35 | 打开“价值模拟”，保持 500 案/月、100 元/小时默认情景 | “技术闭环最终要回答企业价值。8 个合成样本中，单案处理中位时长从 127.5 分钟降到 19.5 分钟，时长下降 84.7%，同等工时理论吞吐是 6.54 倍。若企业每月有 500 个异常案件、综合人工成本每小时 100 元，模拟可释放 900 小时，对应 9 万元每月的人工产能空间。” | 输入假设；84.7%；6.54×；900 h；¥90,000/月 |
| 05:35–05:50 | 下移到公式与边界，再切工程证据 | “这不是现金节省承诺。页面同时展示公式、8 个合成样本和 NOT ALLOWED 生产声明；接入企业实测基线后才能对外主张收益。当前持久层已通过本地 PostgreSQL 验证，云 PolarDB 仍明确标记为待部署。” | 公式；SYNTHETIC；NOT ALLOWED；Local PostgreSQL PASSED；Cloud PolarDB PENDING |
| 05:50–06:00 | 回到全景，停在闭环流水线 | “RevGuard 的差异化不是 Agent 数量，而是给 Agent 协作加上可执行的治理底座：MCP 最小权限、真实状态流、人审不可自签、执行与验证分离、失败可回滚、价值可测算。” | 完整 8 段流水线与 ROLLED_BACK/PASSED |

## 可裁剪的 3 分钟版

保留 00:00–00:25、00:55–01:15、01:15–02:00、02:35–03:25、03:25–04:35、
05:10–06:00 六段；其余用 2–3 秒特写加字幕。不得删除“合成数据”声明、真实人工点击、
验证失败和回滚后复核四个关键镜头。

## 旁白禁区

- 不说“接入了公司真实数据”，应说“采用高保真合成业务数据，真实执行工程链路”。
- 不说“已部署 PolarDB”，应说“完成 PostgreSQL 兼容验证，PolarDB 接入配置已就绪”。
- 不把本地 MCP harness 说成完整 Matrix 房间；Matrix 证据没有补齐前明确标为待采集。
- 不说“AI 自动审批”；L2 必须由独立审批人点击，L3 永远不自动执行。
- 不说“已为企业节省 84.7%/9 万元”；应说“基于 8 个合成样本和输入假设的模拟测算，待企业真实基线验证”。
- 不展示 API key、签名密钥、approval token 或 rollback token。
