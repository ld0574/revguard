# RevGuard Skill 清单

> 本文件由 `scripts/gen_skill_docs.py` 从 `revguard/skills.py` 的 SKILL_REGISTRY
> 自动生成，请勿手工编辑。字段对齐参赛手册附录 B。

共 **14** 个 Skill。设计原则：输入输出结构化、单一稳定能力、
LLM 理解与确定性计算分离、失败返回明确错误类型、高风险 Skill 强制审批凭证。

| Skill | 类型 | 用途 | 依赖工具 | 失败处理 | 安全边界 | 复用场景 |
|---|---|---|---|---|---|---|
| `CaseNormalizeSkill` v1.0.0 | deterministic | 申诉/工单解析为标准化案件实体 | - | missing_entity | read_only=True, pii=True | commission_dispute, ticket_intake |
| `EntityResolveSkill` v1.0.0 | tool | 解析代理商为唯一系统实体 | crm.get_partner | not_found, ambiguous | read_only=True, pii=True | commission_dispute, partner_lookup |
| `EvidenceCollectSkill` v1.0.0 | tool | 跨系统证据采集与完整度评分 | crm.*, contract.*, policy.*, finance.* | tool_unavailable_retry, evidence_gap | read_only=True, pii=False | commission_dispute, batch_reconciliation, audit |
| `PolicyVersionMatchSkill` v1.0.0 | deterministic | 按业务时点匹配政策版本 | - | no_effective_version, version_conflict | read_only=True, pii=False | commission_dispute, policy_simulation |
| `CommissionCalculateSkill` v1.0.0 | deterministic | 规则引擎确定性佣金复算 | rule_engine | invalid_schema, missing_rule, conflicting_rule | read_only=True, pii=False | commission_dispute, policy_simulation, batch_reconciliation |
| `DifferenceExplainSkill` v1.0.0 | deterministic | 差异解释与根因判定 | - | evidence_conflict | read_only=True, pii=False | commission_dispute, audit |
| `RiskClassifySkill` v1.0.0 | policy | L0-L3 风险分级与审批路由判定 | - | unknown_policy, missing_threshold | write_permission=False | commission_dispute, batch_reconciliation, any_write_action |
| `ApprovalRouteSkill` v1.0.0 | tool | 创建审批单并路由审批角色 | workflow.create_approval | workflow_unavailable | write_permission=approval | any_approval_needed_case |
| `PermissionCheckSkill` v1.0.0 | policy | 执行前权限与审批凭证校验 | - | auth_failed, missing_token | write_permission=False | any_write_action |
| `IdempotencyGuardSkill` v1.0.0 | policy | 幂等键冲突检查 | store | idempotency_conflict | write_permission=False | any_write_action |
| `AdjustmentDraftSkill` v1.0.0 | tool | 创建不生效的佣金调整草稿 | commission.create_adjustment_draft | tool_unavailable | write_permission=commission_draft | commission_dispute |
| `LedgerAdjustSkill` v1.0.0 | tool | 提交调整写入台账（审批凭证+幂等） | commission.submit_adjustment | auth_failed, idempotency_conflict | write_permission=commission_post | commission_dispute |
| `PostActionVerifySkill` v1.0.0 | tool | 独立查询验证执行结果 | finance.get_commission_ledger | tool_unavailable | read_only=True | any_executed_case |
| `CaseToDatasetSkill` v1.0.0 | deterministic | 案件轨迹沉淀为评测样本 | - | incomplete_trace | read_only=False | evaluation, knowledge_base |

## 输入 / 输出契约

### CaseNormalizeSkill

- 输入：`raw_case`
- 输出：`entities`, `missing_fields`, `claim`
- 说明：申诉/工单解析为标准化案件实体

### EntityResolveSkill

- 输入：`entities`
- 输出：`partner`, `resolved_by`
- 说明：解析代理商为唯一系统实体

### EvidenceCollectSkill

- 输入：`partner`, `order_id`
- 输出：`evidence`, `evidence_gaps`, `evidence_score`
- 说明：跨系统证据采集与完整度评分

### PolicyVersionMatchSkill

- 输入：`versions`, `facts`, `time_basis`
- 输出：`policy_id`, `policy_version`, `excluded_versions`, `conflicts`
- 说明：按业务时点匹配政策版本

### CommissionCalculateSkill

- 输入：`rule_dsl`, `facts`, `currency`
- 输出：`total_commission`, `components`, `calculation_hash`
- 说明：规则引擎确定性佣金复算

### DifferenceExplainSkill

- 输入：`calculation`, `ledger_entries`, `matched_policy_version`
- 输出：`diffs`, `total_delta`, `root_causes`
- 说明：差异解释与根因判定

### RiskClassifySkill

- 输入：`adjustment_amount`, `evidence_score`, `action_type`
- 输出：`risk_level`, `approval_required`, `approver_role`, `reason_codes`
- 说明：L0-L3 风险分级与审批路由判定

### ApprovalRouteSkill

- 输入：`risk`, `amount`, `action_summary`
- 输出：`approval`
- 说明：创建审批单并路由审批角色

### PermissionCheckSkill

- 输入：`actor`, `risk`, `approval`
- 输出：`pass_or_raise`
- 说明：执行前权限与审批凭证校验

### IdempotencyGuardSkill

- 输入：`idempotency_key`
- 输出：`existing_or_none`
- 说明：幂等键冲突检查

### AdjustmentDraftSkill

- 输入：`order_id`, `component`, `delta`
- 输出：`draft`
- 说明：创建不生效的佣金调整草稿

### LedgerAdjustSkill

- 输入：`action_id`, `approval_token`, `idempotency_key`
- 输出：`execution`, `snapshots`, `rollback_token`
- 说明：提交调整写入台账（审批凭证+幂等）

### PostActionVerifySkill

- 输入：`order_id`, `expected_components`
- 输出：`verification`
- 说明：独立查询验证执行结果

### CaseToDatasetSkill

- 输入：`case`, `shared_state`, `verification`
- 输出：`dataset_record`
- 说明：案件轨迹沉淀为评测样本
