#!/usr/bin/env python3
"""离线校验审计哈希链导出（revguard.audit-chain/v1），失败返回非零退出码。

只读取导出文件本身，不连接数据库、不需要凭据，因此第三方可以脱离 RevGuard 运行栈
复算：

1. ``seq`` 在同一案件内严格递增；
2. 每行 ``previous_hash`` 等于上一行 ``row_hash``，首行为 ``GENESIS``；
3. ``row_hash == sha256(previous_hash || ':' || row_digest)``；
4. 文件内 ``summary`` 与 ``generation_window.summary`` 与复算结果一致；
5. 导出时记录的库级校验 ``database_check`` 为有效且无断链（若文件包含该项）。

用法::

  python scripts/check_audit_chain_export.py docs/evidence/audit-chain-20260918/*.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

try:  # Python 3.11+ 直接用 UTC；更老解释器回退，保证随包脚本在评委机器上能直接运行
    from datetime import UTC
except ImportError:  # pragma: no cover - 兼容 3.10 及更早解释器
    from datetime import timezone

    UTC = timezone.utc  # noqa: UP017 - 旧解释器没有 datetime.UTC

SCHEMA = "revguard.audit-chain/v1"
REQUIRED_ROW_FIELDS = ("seq", "actor", "event", "created_at", "previous_hash", "row_digest", "row_hash")


class AuditChainError(Exception):
    """导出文件与哈希链复算不一致。"""


def parse_time(value: Any) -> datetime | None:
    """解析审计行 ``created_at``；无法解析返回 None（导出侧按窗口内处理）。

    兼容 ``+00`` 缩写、空格分隔与任意位数微秒：Python 3.9 的 ``fromisoformat``
    只接受 3 或 6 位微秒，而库内文本可能出现 5 位。
    """
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if text.endswith("+00"):
        text += ":00"
    elif text.endswith("-00"):
        text += ":00"
    match = re.match(r"^(.*?)\.(\d+)(.*)$", text)
    if match:
        text = f"{match.group(1)}.{match.group(2).ljust(6, '0')[:6]}{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def recompute_row_hash(previous_hash: str, row_digest: str) -> str:
    return hashlib.sha256(f"{previous_hash}:{row_digest}".encode()).hexdigest()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "rows": len(rows),
        "first_seq": rows[0]["seq"] if rows else None,
        "last_seq": rows[-1]["seq"] if rows else None,
        "head_hash": rows[-1]["row_hash"] if rows else None,
    }


def check_rows(rows: list[dict[str, Any]], *, require_genesis: bool = True) -> None:
    if not rows:
        raise AuditChainError("审计行为空")
    previous_seq: int | None = None
    for index, row in enumerate(rows):
        missing = [field for field in REQUIRED_ROW_FIELDS if field not in row]
        if missing:
            raise AuditChainError(f"第 {index} 行缺少字段：{', '.join(missing)}")
        seq = row["seq"]
        if not isinstance(seq, int):
            raise AuditChainError(f"第 {index} 行 seq 不是整数：{seq!r}")
        if previous_seq is not None and seq <= previous_seq:
            raise AuditChainError(f"第 {index} 行 seq 未严格递增：{previous_seq} -> {seq}")
        previous_seq = seq
        if index == 0:
            if require_genesis and row["previous_hash"] != "GENESIS":
                raise AuditChainError(f"首行 previous_hash 不是 GENESIS：{row['previous_hash']}")
        elif row["previous_hash"] != rows[index - 1]["row_hash"]:
            raise AuditChainError(f"第 {index} 行 previous_hash 断链：{row['previous_hash']}")
        expected_hash = recompute_row_hash(str(row["previous_hash"]), str(row["row_digest"]))
        if row["row_hash"] != expected_hash:
            raise AuditChainError(f"第 {index} 行 row_hash 复算不一致")


def check_document(document: dict[str, Any], label: str) -> str:
    if document.get("schema") != SCHEMA:
        raise AuditChainError(f"{label}: schema 不是 {SCHEMA}")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise AuditChainError(f"{label}: rows 缺失或为空")
    check_rows(rows)
    recomputed = summarize(rows)
    summary = document.get("summary") or {}
    for key, value in recomputed.items():
        if summary.get(key) != value:
            raise AuditChainError(f"{label}: summary.{key} 与复算不一致")
    if summary.get("chain_ok") is not True:
        raise AuditChainError(f"{label}: summary.chain_ok 不为 true")
    window = document.get("generation_window") or {}
    start_seq = window.get("start_seq")
    sliced = [row for row in rows if start_seq is None or row["seq"] > start_seq]
    window_summary = window.get("summary") or {}
    check_rows(sliced, require_genesis=False)
    for key, value in summarize(sliced).items():
        if window_summary.get(key) != value:
            raise AuditChainError(f"{label}: generation_window.summary.{key} 与复算不一致")
    if window_summary.get("chain_ok") is not True:
        raise AuditChainError(f"{label}: generation_window.summary.chain_ok 不为 true")
    run = document.get("run_window") or {}
    started_at = run.get("started_at")
    if started_at:
        boundary = parse_time(started_at)
        run_slice = [
            row for row in rows
            if (parsed := parse_time(row.get("created_at"))) is None or parsed >= boundary
        ]
        run_summary = run.get("summary") or {}
        for key, value in summarize(run_slice).items():
            if run_summary.get(key) != value:
                raise AuditChainError(f"{label}: run_window.summary.{key} 与复算不一致")
        if run_summary.get("chain_ok") is not True:
            raise AuditChainError(f"{label}: run_window.summary.chain_ok 不为 true")
    database = document.get("database_check") or {}
    if database:
        if database.get("valid") is not True or int(database.get("broken_links") or 0) != 0:
            raise AuditChainError(f"{label}: 库级链校验未通过 {database}")
    return (
        f"{label}: {recomputed['rows']} 行 link+hash 复算通过"
        f"（seq {recomputed['first_seq']}–{recomputed['last_seq']}，head {str(recomputed['head_hash'])[:16]}…，"
        f"本代次 {len(sliced)} 行）"
    )


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("audit-chain-*.json")))
        else:
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验审计哈希链导出（离线复算）")
    parser.add_argument("paths", nargs="+", type=Path, help="导出 JSON 或包含导出的目录")
    args = parser.parse_args(argv)
    files = collect(args.paths)
    if not files:
        print("没有找到审计链导出文件", file=sys.stderr)
        return 1
    try:
        for path in files:
            document = json.loads(path.read_text(encoding="utf-8"))
            print("审计链复算通过 " + check_document(document, path.name))
    except (AuditChainError, json.JSONDecodeError, OSError) as exc:
        print(f"审计链复算失败: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
