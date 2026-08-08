"""确定性佣金规则引擎（ADR-001：LLM 不直接计算金额）。

职责：
1. 安全求值规则 DSL 中的公式（仅允许四则运算与括号，禁止任意代码执行）；
2. 按 eligibility / when 条件筛选规则组件；
3. 使用 Decimal 计算金额，按政策配置舍入；
4. 输出可复现的 calculation_hash（相同输入 + 相同规则版本 => 相同结果）。

设计要点：
- 公式求值基于 ast 白名单，不使用 eval()；
- 金额全程 Decimal，序列化时才转字符串；
- 每个组件单独舍入再求和，保证与台账逐笔对账一致。
"""
from __future__ import annotations

import ast
import hashlib
import json
from decimal import Decimal, ROUND_HALF_UP, ROUND_DOWN, ROUND_UP


class RuleEngineError(Exception):
    """规则引擎错误的基类，所有失败都必须返回明确错误类型（Skill 设计原则 #5）。"""


class FormulaError(RuleEngineError):
    """公式非法或引用了未提供的变量。"""


class EligibilityError(RuleEngineError):
    """业务数据不满足政策适用条件。"""


_ROUNDING_MODES = {
    "HALF_UP": ROUND_HALF_UP,
    "DOWN": ROUND_DOWN,
    "UP": ROUND_UP,
}

# 公式中允许的二元运算符白名单
_BIN_OPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
}


def to_decimal(value) -> Decimal:
    """把 int/float/str/Decimal 统一转为 Decimal（经由 str，避免浮点噪声）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise FormulaError("布尔值不能作为金额参与计算")
    if isinstance(value, (int, float, str)):
        return Decimal(str(value))
    raise FormulaError(f"不支持的数值类型: {type(value).__name__}")


def evaluate_formula(formula: str, variables: dict) -> Decimal:
    """安全求值如 ``order_amount * 0.10`` 的公式。

    :param formula: 仅含变量名、数字、+-*/% 与括号的表达式
    :param variables: 变量名 -> 数值 的映射
    :raises FormulaError: 公式含非法节点或变量缺失
    """
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"公式语法错误: {formula!r} ({exc})") from exc
    return _eval_node(tree.body, variables)


def _eval_node(node: ast.AST, variables: dict) -> Decimal:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return Decimal(str(node.value))
        raise FormulaError(f"公式中只允许数字字面量，得到: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise FormulaError(f"公式引用了未提供的变量: {node.id}")
        return to_decimal(variables[node.id])
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise FormulaError(f"不允许的运算符: {op_type.__name__}")
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        if isinstance(node.op, (ast.Div, ast.Mod)) and right == 0:
            raise FormulaError("公式出现除零")
        return _BIN_OPS[op_type](left, right)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _eval_node(node.operand, variables)
        return -value if isinstance(node.op, ast.USub) else value
    raise FormulaError(f"公式包含不允许的语法: {type(node).__name__}")


def substitute_formula(formula: str, variables: dict) -> str:
    """生成人类可读的代入式，如 ``180000.00 * 0.15``，用于审计报告展示。"""
    result = formula
    # 按变量名长度降序替换，避免 order_amount 被 order 部分替换
    for name in sorted(variables, key=len, reverse=True):
        if name in formula:
            result = result.replace(name, str(to_decimal(variables[name])))
    return result


# ---------------------------------------------------------------------------
# 条件匹配：when 子句支持精确匹配与 _lte/_lt/_gte/_gt/_ne/_in 后缀操作符
# ---------------------------------------------------------------------------

_SUFFIX_OPS = {
    "_lte": lambda a, b: to_decimal(a) <= to_decimal(b),
    "_lt": lambda a, b: to_decimal(a) < to_decimal(b),
    "_gte": lambda a, b: to_decimal(a) >= to_decimal(b),
    "_gt": lambda a, b: to_decimal(a) > to_decimal(b),
    "_ne": lambda a, b: a != b,
    "_in": lambda a, b: a in b,
}


class _Missing:
    """哨兵：区分「字段不存在」与「字段为 None/0」。"""

    def __repr__(self):  # pragma: no cover - 仅用于日志展示
        return "<MISSING>"


MISSING = _Missing()


def match_condition(when: dict, facts: dict) -> tuple[bool, list[str]]:
    """判断 facts 是否满足 when 条件。

    :returns: (是否全部满足, 不满足的原因列表)
    """
    failures: list[str] = []
    for key, expected in (when or {}).items():
        field_name, predicate = key, None
        for suffix, op in _SUFFIX_OPS.items():
            if key.endswith(suffix):
                field_name, predicate = key[: -len(suffix)], op
                break
        actual = facts.get(field_name, MISSING)
        if actual is MISSING:
            failures.append(f"缺少判断字段: {field_name}")
            continue
        if predicate is None:
            if actual != expected:
                failures.append(f"{field_name} 期望 {expected!r}，实际 {actual!r}")
        else:
            try:
                if not predicate(actual, expected):
                    failures.append(f"{field_name}={actual!r} 不满足 {key} {expected!r}")
            except Exception as exc:  # 类型不可比较等情况按不满足处理并说明
                failures.append(f"{field_name} 比较失败: {exc}")
    return (not failures), failures


def check_eligibility(eligibility: dict, facts: dict) -> tuple[bool, list[str]]:
    """检查政策适用条件（order_status / payment_status / products 等）。"""
    failures: list[str] = []
    for key, allowed in (eligibility or {}).items():
        actual = facts.get(key, MISSING)
        if actual is MISSING:
            failures.append(f"缺少适用性字段: {key}")
        elif isinstance(allowed, list):
            if actual not in allowed:
                failures.append(f"{key}={actual!r} 不在适用范围 {allowed!r}")
        elif actual != allowed:
            failures.append(f"{key}={actual!r} 不满足 {allowed!r}")
    return (not failures), failures


def run_policy(policy_version: dict, facts: dict, currency: str) -> dict:
    """执行一个政策版本，输出确定性计算结果。

    :param policy_version: 政策版本 DSL（含 eligibility/rules/rounding）
    :param facts: 业务事实（订单金额、回款金额、天数、等级、退款等）
    :param currency: 币种
    :returns: 与 models.CalculationResult 对齐的 dict
    """
    eligible, eligibility_failures = check_eligibility(policy_version.get("eligibility"), facts)
    rounding = policy_version.get("rounding", {"scale": 2, "mode": "HALF_UP"})
    quant = Decimal("1").scaleb(-int(rounding.get("scale", 2)))
    round_mode = _ROUNDING_MODES.get(rounding.get("mode", "HALF_UP"), ROUND_HALF_UP)
    rounding_rule = f"scale={rounding.get('scale', 2)},mode={rounding.get('mode', 'HALF_UP')}"

    components: list[dict] = []
    if eligible:
        for rule in policy_version.get("rules", []):
            matched, reasons = match_condition(rule.get("when"), facts)
            if not matched:
                # 条件不满足的规则跳过并留痕，便于审计解释"为什么没有这笔"
                components.append({
                    "type": rule.get("component", "UNKNOWN"),
                    "amount": "0",
                    "formula": rule.get("formula", ""),
                    "substituted": "",
                    "applied": False,
                    "skip_reasons": reasons,
                })
                continue
            amount = evaluate_formula(rule["formula"], facts)
            amount = amount.quantize(quant, rounding=round_mode)
            components.append({
                "type": rule.get("component", "UNKNOWN"),
                "amount": str(amount),
                "formula": rule.get("formula", ""),
                "substituted": substitute_formula(rule["formula"], facts),
                "applied": True,
                "skip_reasons": [],
            })

    total = sum((to_decimal(c["amount"]) for c in components), Decimal("0"))
    total = total.quantize(quant, rounding=round_mode)

    # 可复现哈希：相同政策版本 + 相同输入 => 相同结果（设计文档 12.1）
    hash_payload = {
        "policy_id": policy_version.get("policy_id"),
        "version": policy_version.get("version"),
        "facts": {k: str(v) for k, v in sorted(facts.items())},
        "components": [{"type": c["type"], "amount": c["amount"], "applied": c["applied"]} for c in components],
    }
    calc_hash = "sha256:" + hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()

    return {
        "eligible": eligible,
        "total_commission": str(total),
        "currency": currency,
        "components": components,
        "rounding_rule": rounding_rule,
        "calculation_hash": calc_hash,
        "policy_version": policy_version.get("version"),
        "eligibility_failures": eligibility_failures,
        "facts_snapshot": {k: (str(v) if isinstance(v, (int, float, Decimal)) else v) for k, v in facts.items()},
    }
