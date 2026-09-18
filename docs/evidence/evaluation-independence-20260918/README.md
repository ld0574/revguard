# 评测判据独立性与证据不可自报（2026-09-18）

对应 `docs/决赛/20260916-ClaudeCode决赛竞品深度分析.md` §5.6 的两条"穿透式追问"：

- **追问 5**：「105/105 是谁定的标准？判据从哪来？」竞品里出现过演示脚本写死的动作与
  `ground-truth` 逐字相同、答案同时充当输入与验收标准的翻车形态。
- **追问 7**：「怎么证明这些 Agent 活动不是编出来的？」竞品里出现过
  `matrix_event_id` / `room_id` / `worker_name` / `tool_receipt` 全部由请求体自报、
  后端只做格式检查，`curl` 即可编造完整证据链的形态。

本目录用**可复跑探针**给出这两条的自证材料，不依赖口头声明。

## 1. 判据从哪来：105 个场景的构成与存放位置

| 类别 | 数量 | 期望值存放位置 | 判据来源 |
|---|---|---|---|
| 端到端 Golden Case | 8 | `data/golden_cases/GOLDEN-00{1..8}.json` 的 `expected` 块 | 每案按业务设定（政策版本、等级时点、差异类型）人工冻结的期望状态、金额与根因 |
| 风险边界组合 | 80 | `data/expected/risk_matrix.csv`（80 行，列为 `amount,evidence_score,policy_conflict,expected_risk`） | 按风险分级设计中的阈值与证据分档枚举出的边界样本及其期望等级 |
| 政策生效日样本 | 8 | `scripts/run_evaluation.py::evaluate_policy_dates` 内联常量 | 政策生效窗口两侧的边界日期（`2026-06-30`→`2026-Q2`、`2026-07-01`→`2026-Q3`） |
| 安全攻击探针 | 9 | `scripts/run_evaluation.py::evaluate_security_probes` 内联常量 | 伪造签名、越权 scope、跨案令牌、并发双提交、组件额度滥用、回滚令牌重放 |

评测运行器自称 `method = external_expected_matrix_and_golden_holdout`，并且代码注释明确
"从独立静态期望集读取边界，**不在评测代码中复刻被测规则**"。

**关键性质：期望值只被读入比较，不被注入被测系统。** 每个 Golden Case 的 `input` 与
`expected` 是两个并列字段；运行器只用 `spec["input"]` 构造案件，`expected` 仅用于比对
（`scripts/run_evaluation.py::evaluate_golden_cases`）。探针的第 1 节把两者的键名逐案打印出来，
可以直接看到 `input` 里没有任何期望字段。

## 2. 自证材料：把期望值改错，门禁立刻变红

只声明"判据是独立的"没有说服力，所以探针做了两组**篡改实验**：

| 实验 | 操作 | 结果 |
|---|---|---|
| 基线 | 原样运行 `scripts/run_evaluation.py` | `exit=0`，`total_scenarios=105 passed=105`（8/80/8/9 全通过） |
| 篡改 1 | 把 `GOLDEN-001` 的期望佣金从 `32400.00` 改成 `99999.00` | `exit=1`，`failure: CASE-2026-0001 total_commission: 32400.00 != 99999.00`，其余类别 0 失败 |
| 篡改 2 | 把风险矩阵第 1 行期望 `L0` 改成 `L3` | `exit=1`，`failure: risk amount=0 score=1.0 conflict=False: L0!=L3` |

如果期望值是被测系统自己产出的（或与输入同源），篡改期望不会让结果变红——这正是本实验要排除的形态。

第 5 节另外确认发布快照 `docs/evaluation-summary.json` 会被
`scripts/validate_evaluation_snapshot.py` 复核（`verified ...: 105/105, UTC, median benchmark`），
材料里引用的 105/105 与仓库快照、门禁日志是同一条链路。

## 3. 证据不可自报：审批与审计主体都来自服务端

| 检查 | 方法 | 结果 |
|---|---|---|
| 审批端点不接受客户端自报证据字段 | `ApprovalDecision.model_fields` 只允许 `comment` / `decision`（`extra="forbid"`）；构造 `ApprovalDecision(decision="APPROVED", matrix_event_id="forged")` | 允许字段 = `['comment', 'decision']`；多余字段 → `ValidationError` |
| 审计主体不可自报 | `python3 -m unittest tests.test_api -v` 的 `test_10b_skill_identity_cannot_be_self_reported` | 请求体自报 `actor`/`scope` → `422`，且不产生审计；29 项 API 测试 `OK` |
| Matrix 事件 ID 来自服务端响应 | `revguard/matrix_team.py` 静态检查 | `response.get("event_id")` 存在；未返回 `event_id` 抛 `MatrixTransportError`；交接事件 ID 取自 `send_text` 返回值 |
| 人类身份来自服务端签发的短时证明 | `revguard/api.py::require_human_action` + `_publish_human_decision_intent` | 审批前需具名人类的短时动作断言；审批意图由服务端**先**发布到权威房间，本地审批事务再引用该事件 ID |

也就是说：真人审批的 `matrix_event_id` 不是请求体里的字符串，而是服务端向 Matrix 发送消息后
由 Matrix 返回的事件 ID；客户端既不能在审批请求里塞身份，也不能指定事件 ID。

## 4. 复现方式（202 容器内，只读）

```bash
docker run --rm -v /root/rgops/gate-head/revguard:/src:ro \
  -v <本目录>/probe.py:/tmp/probe.py:ro \
  -v <本目录>/approval-schema-probe.py:/tmp/approval_schema_probe.py:ro \
  -w /tmp revguard-verify-<时间戳>-checks:latest \
  sh -c "cp -a /src /tmp/rg && python3 /tmp/probe.py"
```

本次执行的镜像：`revguard-verify-1789691278-3093769-checks:latest`
（image id `sha256:bcb09108930040a1ef5b29b3ee98500d6f0bf2e3e6b76a2231a07afccae80c41`）；
探针 `probe.py` sha256 `183d31b3a6d9e953c2bbdac8eddd4f97a878e6bdae48c8529614f0e12bd5b4f0`、
`approval-schema-probe.py` sha256 `05d037c46568a1a80668f9f46b886a400a385791b83cfbefcbb474daa9b0b992`；
原始输出见同目录 [`probe-output.txt`](probe-output.txt)。探针只读仓库副本、不连数据库、不联网、不含凭据。

## 5. 诚实边界（不要越过）

- 105 个场景的期望值是**本团队按业务与政策设计冻结**的，不是外部机构或客户签发的真值；
  它能证明"实现与冻结口径一致"，不能证明"这套阈值就是贵公司的正确口径"。
- 风险分级阈值（L1 ≤ 5,000 / L2 ≤ 50,000 / 证据分 ≥ 0.9 等）是**业务参数**：接真实客户时
  必须由业务方确认或改写，并重跑同一套边界样本。若规则表本身写错，测试会"稳定地通过错误口径"。
- 因此对外表述固定为：**判据来自仓库内冻结的静态期望集，与输入分离、可被篡改实验证伪；
  真值本身由我们按业务设计定义，企业口径验收仍待真实业务方确认。**
- 金蝶 / 用友 / SAP 的 Adapter 契约测试、外部 ERP 正式会计写入、云 PolarDB 高可用不在本目录证明范围内。
