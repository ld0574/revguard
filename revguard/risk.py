"""风险分级与审批路由（设计文档第 14 章）。

纯确定性规则：任何资金相关动作的分级都必须可解释、可测试、可审计。
L0 只读 / L1 低风险草稿 / L2 审批后执行 / L3 强制人工。
"""
from __future__ import annotations

from decimal import Decimal

from .models import RiskDecision, RiskLevel

# 分级阈值（单位：案件币种最小单位前的标准金额，可由政策配置覆盖）
L1_AUTO_DRAFT_MAX = Decimal("5000")      # ≤5000 且证据充分 => 可自动建草稿
L2_APPROVAL_MAX = Decimal("50000")       # ≤50000 => L2 审批后执行
EVIDENCE_SCORE_AUTO = 0.9                # 自动处理所需最低证据分
EVIDENCE_SCORE_MIN = 0.6                 # 低于此分数不允许任何写操作


def classify_risk(
    *,
    action_type: str,
    adjustment_amount: Decimal,
    currency: str,
    evidence_score: float,
    case_type: str,
    policy_conflict: bool = False,
    order_count: int = 1,
) -> RiskDecision:
    """按设计文档 14.2 的判断因子输出风险等级。

    :param action_type: READONLY / DRAFT / LEDGER_ADJUST / LEDGER_REVERSE / BATCH
    :param adjustment_amount: 调整金额绝对值
    :param evidence_score: 证据完整度 0~1
    :param policy_conflict: 是否存在未消解的政策/数据冲突
    :param order_count: 涉及订单数（>1 视为批量）
    """
    reasons: list[str] = []
    amount = abs(Decimal(str(adjustment_amount)))

    if action_type == "READONLY" or amount == 0:
        return RiskDecision(
            risk_level=RiskLevel.L0.value,
            approval_required=False,
            approver_role=None,
            execution_constraints={"write": False},
            rollback_plan_required=False,
            reason_codes=["READONLY_OR_ZERO_AMOUNT"],
        )

    # ---- 硬性升级到 L3 的条件：任何一条命中即强制人工 ----
    hard_l3 = []
    if policy_conflict:
        hard_l3.append("POLICY_CONFLICT")
    if evidence_score < EVIDENCE_SCORE_MIN:
        hard_l3.append(f"EVIDENCE_SCORE_{evidence_score:.2f}_BELOW_{EVIDENCE_SCORE_MIN}")
    if amount > L2_APPROVAL_MAX:
        hard_l3.append(f"AMOUNT_EXCEEDS_{L2_APPROVAL_MAX}")
    if order_count > 1 or action_type == "BATCH":
        hard_l3.append("BATCH_OPERATION")

    if hard_l3:
        return RiskDecision(
            risk_level=RiskLevel.L3.value,
            approval_required=True,
            approver_role="FINANCE_HEAD+OPS_HEAD",
            execution_constraints={
                "write": False,
                "auto_execute": False,
                "note": "仅生成处理方案，强制人工线下处理",
            },
            rollback_plan_required=True,
            reason_codes=hard_l3,
        )

    # ---- 扣回/冲销（负向调整）一律不低于 L2：多退少补，扣回必须人工审批 ----
    negative = Decimal(str(adjustment_amount)) < 0
    if negative:
        reasons.append("NEGATIVE_ADJUSTMENT_REQUIRES_APPROVAL")

    # ---- L1：小额且证据充分 => 允许自动创建不生效草稿（仅限补发方向） ----
    if not negative and amount <= L1_AUTO_DRAFT_MAX and evidence_score >= EVIDENCE_SCORE_AUTO:
        reasons.append(f"AMOUNT_WITHIN_{L1_AUTO_DRAFT_MAX}")
        reasons.append(f"EVIDENCE_SCORE_{evidence_score:.2f}_OK")
        return RiskDecision(
            risk_level=RiskLevel.L1.value,
            approval_required=False,
            approver_role=None,
            execution_constraints={"write": "draft_only", "max_amount": str(L1_AUTO_DRAFT_MAX)},
            rollback_plan_required=True,
            reason_codes=reasons,
        )

    # ---- 其余 => L2：人工审批后执行 ----
    reasons.append("REQUIRES_HUMAN_APPROVAL")
    if evidence_score < EVIDENCE_SCORE_AUTO:
        reasons.append(f"EVIDENCE_SCORE_{evidence_score:.2f}_BELOW_AUTO_{EVIDENCE_SCORE_AUTO}")
    return RiskDecision(
        risk_level=RiskLevel.L2.value,
        approval_required=True,
        approver_role="FINANCE_LEAD",
        execution_constraints={"write": True, "requires_approval_token": True, "max_amount": str(L2_APPROVAL_MAX)},
        rollback_plan_required=True,
        reason_codes=reasons,
    )
