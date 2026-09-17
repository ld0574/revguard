# 审批 = 参数承诺：实现与对抗性验证（2026-09-18）

## 结论

人工批准不再只是"发一张令牌"，而是**对一组规范化参数作出承诺**。批准与执行之间
出现任何参数漂移，执行入口都会在写台账之前拒绝，并且拒绝原因可被第三方复算。

- 承诺摘要：`sha256:<hex>`，输入是 `canonical_json({action_summary, amount, approval_id, case_id, component_quota, currency, release_version, risk_level})`；
- 金额与组件额度先做两位小数定点化，`100` / `100.0` / `100.00` 得到同一摘要；
- 摘要同时落在**审批单**、**执行能力令牌**两处；
- 执行时**重新计算**审批单当前参数的摘要，与审批单摘要、令牌摘要三方比对，任一不一致即 `AUTH_FAILED`；
- 审批时的 `release_version` 与执行时运行版本不一致同样拒绝；
- 重新授权（`workflow.renew_approval_capability`）走同一套承诺校验，漂移后不签发新令牌。

实现：`revguard/commitment.py`、`revguard/mocks.py`（`_tool_workflow_decide_approval`、
`_tool_workflow_renew_approval_capability`、`_submit_single`）。

## 对抗性探针

`scripts/approval_commitment_probe.py` 复现 5 个场景，本轮 2026-09-18 在 202 Docker
验收镜像内 **5/5 通过**：

| 场景 | 篡改方式 | 观测结果 |
|---|---|---|
| `unsealed_drift` | 只改审批单金额，摘要不动 | `AUTH_FAILED` 审批参数已被改动（参数漂移），台账无写入 |
| `resealed_drift` | 改金额并重算审批单摘要 | `AUTH_FAILED` 审批凭证与审批单参数承诺不一致（参数漂移） |
| `renewal_drift` | 参数漂移后重新申请能力令牌 | `AUTH_FAILED` 审批参数已被改动（参数漂移），拒绝重新授权 |
| `canonical_amount` | 两案分别写 `100` / `100.00` | 承诺金额同为 `100.00`，摘要因审批单/案件不同而不同 |
| `happy_path` | 不篡改（对照组） | 正常写入台账，台账金额 `100` 与承诺一致 |

对照组说明这不是"一律拒绝"：只有承诺被破坏时才拒绝。

## 复跑方式

```bash
# 在 202 Docker 内，工作目录为 revguard 项目根
python scripts/approval_commitment_probe.py \
  --output docs/evidence/approval-commitment-20260918/probe-output.json
python -m unittest tests.test_risk_and_mocks -v   # 4 项承诺相关单元测试
```

`probe-output.json` 是机器可读结果（含 `passed` 字段，失败返回非零退出码），
`probe-console.txt` 是同一次运行的完整控制台输出。

## 制品哈希（SHA-256）

| 文件 | SHA-256 |
|---|---|
| `probe-console.txt` | `f599b2f3f604023dd8041737c76f566ab283cfd564132439e4940c9160c4f208` |
| `probe-output.json` | `9845e63df7152a264c90ee3abfd1a544a1f7aec99f0c71c3c0386df5335975eb` |

## 边界

- 探针运行在验收容器内的 Mock 工具网关（与演示栈同一份 `revguard` 代码），
  不使用生产数据，不写任何真实账务系统；
- 该机制覆盖"审批参数被事后改写"的漂移；审批**人**身份绑定由真人审批链路
  `docs/evidence/agentteams-matrix-binding-20260918/` 单独验证，两者互补；
- 外部 ERP 正式会计写入 Saga 仍未验收，本证据只覆盖 RevGuard 受控台账。
