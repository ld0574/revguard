"""政策版本匹配（Policy Time Travel，设计文档 12.3）。

核心原则：不能直接使用「当前生效」的政策或「当前」代理商等级，
必须按规则指定的业务时点（time_basis，如订单创建时间）选择当时有效的版本，
并显式输出：选中了谁、排除了谁、是否存在冲突、依据来自哪里。
"""
from __future__ import annotations

from .models import PolicyDecision


class PolicyMatchError(Exception):
    """政策匹配失败（无可用版本 / 冲突无法消解）。"""


def _date_of(facts: dict, field_name: str) -> str:
    """从业务事实中取日期字段（ISO 日期字符串，按字典序即可比较）。"""
    value = facts.get(field_name)
    if not value:
        raise PolicyMatchError(f"政策匹配缺少时间字段: {field_name}")
    return str(value)[:10]


def select_policy_version(versions: list[dict], facts: dict, time_basis: str = "order_date") -> PolicyDecision:
    """在多个政策版本中按业务时点选择有效版本。

    :param versions: 政策版本列表，每个含 version/effective_from/effective_to/rule_dsl/clauses
    :param facts: 业务事实（至少包含 time_basis 指定的时间字段）
    :param time_basis: 使用哪个时间字段判断（默认订单创建时间）
    """
    if not versions:
        raise PolicyMatchError("政策库中没有任何版本")

    decision_date = _date_of(facts, time_basis)
    selected: list[dict] = []
    excluded: list[dict] = []

    for v in versions:
        eff_from = str(v.get("effective_from", "0000-00-00"))[:10]
        eff_to = v.get("effective_to")
        eff_to = str(eff_to)[:10] if eff_to else "9999-12-31"
        if eff_from <= decision_date <= eff_to:
            selected.append(v)
        else:
            excluded.append({
                "version": v.get("version"),
                "reason": f"有效期 {eff_from}~{eff_to} 不覆盖业务时点 {decision_date}",
            })

    conflicts: list[str] = []
    if not selected:
        raise PolicyMatchError(
            f"业务时点 {decision_date}（{time_basis}）没有任何生效政策版本；"
            f"已排除: {[e['version'] for e in excluded]}"
        )
    if len(selected) > 1:
        # 多个版本同时生效属于配置冲突：取最新生效者，但必须显式留痕（不静默）
        selected.sort(key=lambda v: str(v.get("effective_from", "")), reverse=True)
        conflicts.append(
            f"业务时点 {decision_date} 存在 {len(selected)} 个重叠生效版本: "
            f"{[v.get('version') for v in selected]}，已按最新生效版本裁决，需政策管理员复核"
        )
        for loser in selected[1:]:
            excluded.append({"version": loser.get("version"), "reason": "与更新版本重叠生效，被裁决排除"})
    chosen = selected[0]

    return PolicyDecision(
        policy_id=chosen.get("policy_id", ""),
        policy_version=chosen.get("version", ""),
        time_basis=time_basis,
        decision_date=decision_date,
        effective_rule_set=chosen.get("rule_dsl", {}),
        cited_clauses=chosen.get("clauses", []),
        excluded_versions=excluded,
        unresolved_conflicts=conflicts,
        confidence=0.9 if conflicts else 1.0,
    )


def resolve_tier_at_date(tier_history: list[dict], at_date: str) -> dict:
    """按等级生效时间回溯业务时点的代理商等级（设计文档 3.2 冲突场景的确定性解法）。

    :param tier_history: [{tier, effective_from}] 按生效时间排列
    :param at_date: 业务时点（YYYY-MM-DD）
    :returns: {tier, effective_from, conflict}
    """
    if not tier_history:
        raise PolicyMatchError("代理商等级历史为空，无法确定业务时点等级")
    ordered = sorted(tier_history, key=lambda t: str(t.get("effective_from", "")))
    effective = ordered[0]
    for item in ordered:
        if str(item.get("effective_from", ""))[:10] <= at_date:
            effective = item
        else:
            break
    # 若最新等级已生效但业务时点在其生效日之前，标记冲突供审计解释
    latest = ordered[-1]
    conflict = None
    if latest is not effective:
        conflict = (
            f"当前等级 {latest.get('tier')}（{latest.get('effective_from')} 生效），"
            f"但业务时点 {at_date} 应适用 {effective.get('tier')}"
        )
    return {
        "tier": effective.get("tier"),
        "effective_from": effective.get("effective_from"),
        "conflict": conflict,
    }
