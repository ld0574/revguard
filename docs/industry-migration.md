# RevGuard 六行业迁移矩阵

标识只表示迁移方式，不表示“零成本支持”：**直接复用**为领域无关治理能力，**替换 Adapter**为外部系统连接，**重写领域规则**为业务口径与阈值。

| 行业 / 场景 | 直接复用 | 替换 Adapter | 重写领域规则 |
|---|---|---|---|
| 渠道佣金争议 | StageTask、审批、能力令牌、幂等、Trace/Audit、执行/验证分离 | CRM、合同、政策、财务接口 | 佣金组件、政策时点、L0-L3 阈值 |
| 保险理赔复核 | 同上，另复用证据缺口与人工升级 | 保单、理赔、医院/查勘、支付接口 | 责任范围、免赔额、赔付公式、反欺诈阈值 |
| 供应商对账 | 同上，另复用版本匹配与差异解释 | 采购、收货、发票、付款接口 | 三单匹配、税额/账期、容差规则 |
| 电商售后退款 | 同上，另复用一次性写能力与回滚验证 | 订单、物流、支付、库存接口 | 退款资格、折损、运费和时效规则 |
| SaaS 计费申诉 | 同上，另复用确定性金额内核 | 订阅、用量、定价、账单接口 | 计量窗口、阶梯价格、Credit 规则 |
| 员工费用报销 | 同上，另复用 Principal/RBAC 与审批链 | HR、差旅、发票、支付接口 | 费用标准、城市等级、票据与税务规则 |

## 16 Skill 的迁移分层

- **直接复用**：`PermissionCheckSkill`、`IdempotencyGuardSkill`、`PostActionVerifySkill`、`PostRollbackVerifySkill` 及 StageTask/Trace/Audit 契约；输入字段需要做领域命名映射。
- **主要替换 Adapter**：`EntityResolveSkill`、`EvidenceCollectSkill`、`ApprovalRouteSkill`、`AdjustmentDraftSkill`、`LedgerAdjustSkill`、`LedgerReverseSkill`。
- **必须重写领域规则**：`PolicyVersionMatchSkill`、`CommissionCalculateSkill`、`DifferenceExplainSkill`、`RiskClassifySkill`；`CaseNormalizeSkill` 和 `CaseToDatasetSkill` 需要重配 Schema/标签。

因此 RevGuard 的可迁移资产是“受控协同与可验证写入骨架”，而不是把佣金规则原样套到六个行业。
