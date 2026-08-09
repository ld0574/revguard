"""RevGuard 领域模型与状态机定义。

只存放结构化定义，不放业务逻辑：
- Case / Task 状态机（对应设计文档第 11 章）
- 风险等级 L0-L3（对应设计文档第 14 章）
- 各阶段 Artifact 的数据类定义
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> str:
    """返回 ISO-8601 UTC 时间戳，统一全系统时间格式。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    """生成带业务前缀的短 ID，便于日志与审计阅读。"""
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"


class CaseStatus(str, enum.Enum):
    """Case 状态机（设计文档 11.1）。每个迁移都会被审计记录。"""
    CREATED = "CREATED"
    NORMALIZING = "NORMALIZING"
    EVIDENCE_COLLECTING = "EVIDENCE_COLLECTING"
    WAITING_FOR_EVIDENCE = "WAITING_FOR_EVIDENCE"
    POLICY_MATCHING = "POLICY_MATCHING"
    CALCULATING = "CALCULATING"
    ROOT_CAUSE_ANALYZING = "ROOT_CAUSE_ANALYZING"
    RISK_REVIEW = "RISK_REVIEW"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    READY_TO_EXECUTE = "READY_TO_EXECUTE"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ROLLBACK_REQUIRED = "ROLLBACK_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"
    REJECTED = "REJECTED"
    KNOWLEDGE_ARCHIVED = "KNOWLEDGE_ARCHIVED"
    CLOSED = "CLOSED"
    FAILED = "FAILED"  # 系统级失败（工具连续失败等），不产出虚假结论


class TaskStatus(str, enum.Enum):
    """Task 状态（设计文档 11.2）。"""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_TOOL = "WAITING_TOOL"
    WAITING_HUMAN = "WAITING_HUMAN"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


class RiskLevel(str, enum.Enum):
    """风险等级（设计文档 14.1）。"""
    L0 = "L0"  # 只读诊断，自动执行
    L1 = "L1"  # 低风险，仅自动创建不生效草稿
    L2 = "L2"  # 有限写操作，人工审批后执行
    L3 = "L3"  # 高风险，只生成方案，强制人工处理


@dataclass
class Case:
    """佣金/结算异常案件（设计文档 10.2）。"""
    case_id: str
    case_type: str            # COMMISSION_UNDERPAYMENT / COLLECTION_MISSING / POLICY_MISCONFIG ...
    source: str               # EMAIL / TICKET / MANUAL / API
    status: str = CaseStatus.CREATED.value
    priority: str = "P1"
    partner_id: Optional[str] = None
    partner_name: Optional[str] = None
    order_id: Optional[str] = None
    contract_id: Optional[str] = None
    description: str = ""
    claim: dict = field(default_factory=dict)   # {actual_amount, expected_amount, currency}
    entities: dict = field(default_factory=dict)
    evidence_score: float = 0.0
    risk_level: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Evidence:
    """证据条目（设计文档 10.3）。每项证据必须可溯源。"""
    evidence_id: str
    case_id: str
    type: str                 # ORDER / PAYMENT_RECORD / CONTRACT / POLICY_VERSIONS / COMMISSION_LEDGER / INVOICE ...
    source_system: str        # CRM_MOCK / FINANCE_MOCK / CONTRACT_MOCK / COMMISSION_MOCK
    source_ref: str           # 源系统主键，如 PAY-98765
    collected_by: str         # 采集者（Agent / Skill 名）
    payload: dict
    strength: str = "STRONG"  # STRONG / MEDIUM / WEAK（设计文档 16.3）
    tool_receipt: Optional[str] = None
    collected_at: str = field(default_factory=utc_now)
    content_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PolicyDecision:
    """政策匹配结论。必须说明选中、排除与冲突。"""
    policy_id: str
    policy_version: str
    time_basis: str                 # 用哪个时间字段做判断（order_date / payment_date ...）
    decision_date: str
    effective_rule_set: dict
    cited_clauses: list = field(default_factory=list)
    excluded_versions: list = field(default_factory=list)  # [{version, reason}]
    unresolved_conflicts: list = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class CalculationResult:
    """规则引擎复算结果。金额一律 Decimal 序列化为字符串，避免浮点误差。"""
    eligible: bool
    total_commission: str
    currency: str
    components: list              # [{type, amount, formula, substituted}]
    rounding_rule: str
    calculation_hash: str
    policy_version: str
    eligibility_failures: list = field(default_factory=list)
    facts_snapshot: dict = field(default_factory=dict)


@dataclass
class RiskDecision:
    """风险分级结论（设计文档 14.2 判断因子）。"""
    risk_level: str
    approval_required: bool
    approver_role: Optional[str]
    execution_constraints: dict
    rollback_plan_required: bool
    reason_codes: list


@dataclass
class ExecutionResult:
    """受控执行结果（设计文档 7.6 前置条件全部满足后才会产生）。"""
    action_id: str
    action_type: str              # LEDGER_ADJUST / LEDGER_REVERSE
    status: str                   # DRAFT_CREATED / SUBMITTED / REVERSED / FAILED
    amount: str
    currency: str
    idempotency_key: str
    before_snapshot: dict
    after_snapshot: dict
    tool_receipts: list
    rollback_token: Optional[str] = None


@dataclass
class VerificationResult:
    """独立验证结果（ADR-002：不复用 Executor 返回值作为唯一证据）。"""
    verification_status: str      # PASSED / FAILED
    expected_amount: str
    actual_amount: str
    variance: str
    evidence_refs: list
    rollback_required: bool
    checked_at: str = field(default_factory=utc_now)
