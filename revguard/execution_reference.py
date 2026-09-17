"""执行引用监视器：没有实际读取过的事实，不得成为资金执行的依据。

资金分录不能只带一个金额。它必须能回答"这个金额依据的事实是谁读的、什么时候读的、
读到的原文摘要是什么"。本模块在工具网关层面补上这条约束：

- **读取时**：每一次成功的只读调用都在回执里留下事实引用
  （`fact_digest` = 返回数据的规范化 SHA-256，`reference_keys` = 订单/伙伴/币种等绑定键）；
- **执行时**：`commission.submit_adjustment` 必须能在同一案件中找到必备事实槽位的
  成功回执（订单、合同、佣金台账），否则拒绝写入并返回 `EVIDENCE_GAP`；
- **绑定强度**：回执带绑定键且与执行依据一致记为 `FACT_BOUND`（强绑定：订单/台账按
  `order_id`，合同按从订单事实推出的 `partner_id`）；历史回执没有键位时记为
  `CASE_ONLY`（弱绑定，显式标注，不冒充强绑定）；
- **历史回执**：监视器上线前的回执没有事实摘要与绑定键，按 `CASE_ONLY` 弱绑定受理，
  摘要为 `null`，不冒充强绑定；
- **落账**：台账分录携带 `execution_references` 与 `reference_anchor`，
  第三方可用同一份回执复核该分录依据的事实没有被换掉。

监视器可通过 `REVGUARD_REQUIRE_EXECUTION_REFERENCES=true` 打开。关闭时行为与历史版本
一致（只为兼容历史回归），打开后"未读取即不可执行"是硬门禁。
"""
from __future__ import annotations

import hashlib

from .commitment import canonical_json

#: 资金执行前必须具备的事实槽位；值是可接受的读取工具名（命中任一即可）。
REQUIRED_REFERENCE_SLOTS: dict[str, tuple[str, ...]] = {
    "ORDER": ("crm.get_order",),
    "CONTRACT": ("contract.get_contract", "contract.get_effective_terms"),
    "COMMISSION_LEDGER": ("finance.get_commission_ledger",),
}

#: 允许出现在事实引用里的绑定键；只记录业务键，不记录凭据或自由文本。
REFERENCE_KEY_FIELDS: tuple[str, ...] = ("order_id", "partner_id", "currency")

#: 每个槽位用来判定"确实是同一条业务事实"的绑定键。
SLOT_BINDING_KEYS: dict[str, str] = {
    "ORDER": "order_id",
    "CONTRACT": "partner_id",
    "COMMISSION_LEDGER": "order_id",
}

BOUND = "FACT_BOUND"
CASE_ONLY = "CASE_ONLY"


def fact_digest(data) -> str:
    """返回数据的规范化摘要；同一份事实永远得到同一个摘要。"""
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def reference_keys(parameters: dict | None, data) -> dict[str, str]:
    """从参数与返回数据里提取业务绑定键。"""
    keys: dict[str, str] = {}
    for field in REFERENCE_KEY_FIELDS:
        value = None
        if isinstance(data, dict):
            value = data.get(field)
            if value is None:
                for nested in data.values():
                    if isinstance(nested, dict) and nested.get(field) is not None:
                        value = nested[field]
                        break
        if value is None and isinstance(parameters, dict):
            value = parameters.get(field)
        if value is not None:
            keys[field] = str(value)
    return keys


def attach_receipt_reference(receipt: dict, *, tool_name: str,
                             parameters: dict | None, data) -> dict:
    """把一次成功读取写成回执里的事实引用。"""
    receipt["fact_digest"] = fact_digest(data)
    receipt["reference_keys"] = reference_keys(parameters, data)
    receipt["reference_tool"] = tool_name
    return receipt


def _successful_reads(receipts, case_id: str) -> list[dict]:
    """同案成功的只读回执。

    监视器上线前写入的历史回执没有 `fact_digest` 与绑定键，仍然算"读过"，
    但只会得到 `CASE_ONLY` 弱绑定，不会冒充强绑定。
    """
    return [
        item for item in receipts
        if item.get("case_id") == case_id
        and item.get("success")
        and item.get("tool_name") not in (None, "")
    ]


def verify_execution_references(*, case_id: str, order_id: str, currency: str,
                                receipts) -> tuple[list[dict], list[str]]:
    """检查必备事实槽位是否真的被读过，并返回引用清单与问题清单。

    绑定键按槽位语义选择：订单与台账按 `order_id`，合同按 `partner_id`
    （期望值取自同案已读到的订单事实，而不是执行请求自报）。
    找不到键位的历史回执退化为 `CASE_ONLY` 弱绑定，并显式标注。
    """
    reads = _successful_reads(receipts, case_id)
    references: list[dict] = []
    problems: list[str] = []
    expected = {"order_id": str(order_id or "")}
    order_reads = [item for item in reads if item.get("tool_name") in REQUIRED_REFERENCE_SLOTS["ORDER"]]
    order_bound_reads = [
        item for item in order_reads
        if str((item.get("reference_keys") or {}).get("order_id") or "") == expected["order_id"]
    ]
    if order_bound_reads:
        expected["partner_id"] = str(
            (order_bound_reads[-1].get("reference_keys") or {}).get("partner_id") or ""
        )
    for slot, tools in REQUIRED_REFERENCE_SLOTS.items():
        binding_key = SLOT_BINDING_KEYS[slot]
        want = expected.get(binding_key, "")
        candidates = [item for item in reads if item.get("tool_name") in tools]
        if not candidates:
            problems.append(f"未读取必备事实槽位 {slot}")
            continue
        bound = [
            item for item in candidates
            if want and str((item.get("reference_keys") or {}).get(binding_key) or "") == want
        ]
        case_only = [item for item in candidates if not (item.get("reference_keys") or {}).get(binding_key)]
        chosen_bound = bound[-1] if bound else None
        # 命中绑定键的强绑定优先；没有键位的历史回执退化为案件级弱绑定。
        chosen = chosen_bound or (case_only[-1] if case_only else None)
        if chosen is None:
            problems.append(f"事实引用与执行依据不一致：{slot}")
            continue
        keys = chosen.get("reference_keys") or {}
        if keys.get("currency") and currency and keys["currency"] != currency:
            problems.append(f"事实引用币种与执行币种不一致：{slot}")
            continue
        references.append({
            "slot": slot,
            "tool_name": chosen.get("tool_name"),
            "tool_receipt": chosen.get("tool_receipt"),
            "actor": chosen.get("actor"),
            "called_at": chosen.get("called_at"),
            "binding": f"{BOUND}" if chosen is chosen_bound else CASE_ONLY,
            "binding_key": binding_key,
            "binding_value": keys.get(binding_key),
            "fact_digest": chosen.get("fact_digest"),
            "reference_keys": keys,
        })
    return references, problems


def reference_anchor(references: list[dict]) -> str:
    """把整组引用折叠成一个可写进台账的锚点摘要。"""
    payload = [
        {
            "slot": item.get("slot"),
            "tool_name": item.get("tool_name"),
            "tool_receipt": item.get("tool_receipt"),
            "binding": item.get("binding"),
            "fact_digest": item.get("fact_digest"),
        }
        for item in references
    ]
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def require_execution_references_from_env(default: bool = False) -> bool:
    import os
    raw = os.getenv("REVGUARD_REQUIRE_EXECUTION_REFERENCES")
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "required"}
