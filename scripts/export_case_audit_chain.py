#!/usr/bin/env python3
"""导出案件审计哈希链的机器可读证据（revguard.audit-chain/v1）。

在 202 Docker 内对真实运行栈执行：读取 ``/api/v1/cases/{id}`` 的完整审计行、
``/api/v1/cases/{id}/dashboard`` 的代次边界与 ``/api/v1/ops/metrics`` 的库级链校验，
生成第三方可独立复算的 JSON。脚本只读，不写数据库、不打印凭据。

链算法与 ``migrations/polardb/001_core.sql`` 的 ``BEFORE INSERT`` 触发器一致::

    canonical_row = jsonb_build_object('case_id', …,'actor', …,'event', …,
                                       'detail', …,'created_at', …)::text
    row_digest    = sha256(canonical_row)
    row_hash      = sha256(previous_hash || ':' || row_digest)
    previous_hash = 同一 case_id 上一行的 row_hash；首行为 'GENESIS'

脱敏边界：审计行**原样导出**，否则第三方无法复算哈希链；导出前做 fail-closed 扫描，
命中凭据字段带值、Bearer、内部主机或内网 IPv4 就直接失败，而不是静默替换。
Matrix 房间与事件 ID 是可核验的运行事实，予以保留。

用法::

  python scripts/export_case_audit_chain.py \
    --base-url http://localhost:19088 \
    --api-key-file /run/secrets/demo-viewer-key \
    --case CASE-2026-0001 --case CASE-2026-0008 \
    --output-dir docs/evidence/audit-chain-20260918
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "revguard.audit-chain/v1"

SECRET_KEY_RE = re.compile(
    r"^(?:api[_-]?key|apikey|secret|client_secret|password|passwd|token|access_token"
    r"|refresh_token|private_key|cookie|credential|credentials)$",
    re.IGNORECASE,
)
FORBIDDEN_SUBSTRING_RE = re.compile(
    r"(?:Bearer\s|rg-demo-|-----BEGIN [A-Z ]*PRIVATE KEY-----|POSTGRES_PASSWORD)",
)
INTERNAL_HOST_RE = re.compile(
    r"\b(?:revguard|agentteams|matrix|erpnext|grafana|prometheus|tempo|loki|alloy)"
    r"[A-Za-z0-9._-]*\.internal\b",
    re.IGNORECASE,
)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

GENERATION_WINDOW_RULE = "seq > audit_generation.start_seq（该代次标记行之后的所有审计行）"
RUN_WINDOW_RULE = "created_at >= 首个 trace span 起点（与官网回放页同一口径）"


ALGORITHM = {
    "canonical_row": (
        "jsonb_build_object('case_id', case_id, 'actor', actor, 'event', event, "
        "'detail', detail, 'created_at', created_at)::text"
    ),
    "row_digest": "sha256(canonical_row)",
    "row_hash": "sha256(previous_hash || ':' || row_digest)",
    "previous_hash": "同一 case_id 上一行的 row_hash；首行为 GENESIS",
    "source": "migrations/polardb/001_core.sql :: revguard_audit_chain_before_insert",
    "offline_check": "python scripts/check_audit_chain_export.py <导出文件>",
}


def digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def row_hash(previous_hash: str, row_digest: str) -> str:
    return hashlib.sha256(f"{previous_hash}:{row_digest}".encode()).hexdigest()


def parse_time(value: Any) -> datetime | None:
    """解析库内 ``created_at`` / trace 起点（兼容 ``+00``、空格分隔与任意微秒位数）。"""
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00").replace(" ", "T", 1)
    if text.endswith("+00"):
        text += ":00"
    elif text.endswith("-00"):
        text += ":00"
    match = re.match(r"^(.*?)\.(\d+)(.*)$", text)
    if match:
        # Python 3.9 的 fromisoformat 只接受 3/6 位微秒；观察写入的 Postgres 文本最多 6 位。
        text = f"{match.group(1)}.{match.group(2).ljust(6, '0')[:6]}{match.group(3)}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def get_json(base_url: str, path: str, api_key: str) -> Any:
    """只允许对显式传入的 http/https 演示栈地址发起只读请求。"""
    url = base_url.rstrip("/") + path
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"仅支持 http/https 地址：{url}")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310 - 协议已白名单校验
        return json.loads(response.read().decode("utf-8"))


def scan_forbidden(value: Any, path: str, problems: list[str]) -> None:
    """Fail-closed 扫描：命中凭据或内网标识即中止导出。"""
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.match(str(key)) and isinstance(item, str) and item.strip():
                problems.append(f"{path}.{key} 凭据字段带值")
            scan_forbidden(item, f"{path}.{key}", problems)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            scan_forbidden(item, f"{path}[{index}]", problems)
    elif isinstance(value, str):
        if FORBIDDEN_SUBSTRING_RE.search(value):
            problems.append(f"{path} 命中禁止子串")
        if INTERNAL_HOST_RE.search(value) or IPV4_RE.search(value):
            problems.append(f"{path} 命中内部主机或内网地址")
        text = value.strip()
        if text.startswith("{"):
            try:
                scan_forbidden(json.loads(text), f"{path}(detail)", problems)
            except json.JSONDecodeError:
                pass


def build_rows(case_payload: dict[str, Any], case_id: str) -> list[dict[str, Any]]:
    rows = [
        {
            "seq": item.get("seq"),
            "actor": item.get("actor"),
            "event": item.get("event"),
            "detail": item.get("detail"),
            "created_at": item.get("created_at"),
            "previous_hash": item.get("previous_hash"),
            "row_digest": item.get("row_digest"),
            "row_hash": item.get("row_hash"),
        }
        for item in (case_payload.get("audit_events") or [])
        if item.get("case_id") in (None, case_id)
    ]
    rows.sort(key=lambda item: item.get("seq") or 0)
    return rows


def chain_status(rows: list[dict[str, Any]], *, require_genesis: bool = True) -> tuple[bool, int | None]:
    """返回 (链是否自洽, 首个断链行号)；断链判定与库内 SQL 校验同构。

    ``require_genesis`` 只对完整链启用：代次窗口从案件历史中间截取，首行的
    ``previous_hash`` 指向窗口外的上一行，因此窗口只校验「内部链接 + 行内哈希」。
    """
    for index, row in enumerate(rows):
        if index == 0:
            if require_genesis and row.get("previous_hash") != "GENESIS":
                return False, index
        elif row.get("previous_hash") != rows[index - 1].get("row_hash"):
            return False, index
        if row.get("row_hash") != row_hash(str(row.get("previous_hash")), str(row.get("row_digest"))):
            return False, index
    return True, None


def summarize(rows: list[dict[str, Any]], *, require_genesis: bool = True) -> dict[str, Any]:
    ok, broken_at = chain_status(rows, require_genesis=require_genesis)
    return {
        "rows": len(rows),
        "first_seq": rows[0].get("seq") if rows else None,
        "last_seq": rows[-1].get("seq") if rows else None,
        "head_hash": rows[-1].get("row_hash") if rows else None,
        "chain_ok": ok,
        "first_broken_index": broken_at,
    }


def build_document(
    case_id: str,
    case_payload: dict[str, Any],
    dashboard: dict[str, Any],
    health: dict[str, Any],
    ops: dict[str, Any],
) -> dict[str, Any]:
    rows = build_rows(case_payload, case_id)
    generation = dashboard.get("audit_generation") or {}
    start_seq = generation.get("start_seq")
    window = [row for row in rows if start_seq is None or (row.get("seq") or 0) > start_seq]
    span_starts = [
        parse_time(span.get("started_at"))
        for span in ((dashboard.get("trace") or {}).get("spans") or [])
    ]
    run_start = min((item for item in span_starts if item is not None), default=None)
    run_window = [
        row for row in rows
        if run_start is None
        or (parsed := parse_time(row.get("created_at"))) is None
        or parsed >= run_start
    ]
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "release": health.get("release"),
        "backend": health.get("backend"),
        "case_id": case_id,
        "case_status": case_payload.get("status"),
        "recording_id": case_payload.get("recording_id"),
        "algorithm": ALGORITHM,
        "database_check": ops.get("audit_chain") or {},
        "source": {
            "case_endpoint": f"/api/v1/cases/{case_id}",
            "dashboard_endpoint": f"/api/v1/cases/{case_id}/dashboard",
            "snapshot_sha256": digest(case_payload),
        },
        "summary": summarize(rows),
        "generation_window": {
            "rule": GENERATION_WINDOW_RULE,
            "recording_id": generation.get("recording_id"),
            "start_seq": start_seq,
            "rows": len(window),
            "history_rows": generation.get("history_event_count"),
            "summary": summarize(window, require_genesis=False),
        },
        "run_window": {
            "rule": RUN_WINDOW_RULE,
            "recording_id": generation.get("recording_id"),
            "started_at": run_start.isoformat().replace("+00:00", "Z") if run_start else None,
            "rows": len(run_window),
            "summary": summarize(run_window, require_genesis=False),
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 RevGuard 案件审计哈希链（机器可读）")
    parser.add_argument("--base-url", default="http://localhost:19088")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--case", action="append", dest="cases", default=[])
    parser.add_argument("--output-dir", type=Path, default=Path("docs/evidence/audit-chain"))
    args = parser.parse_args(argv)

    api_key = args.api_key
    if not api_key and args.api_key_file:
        api_key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not api_key:
        parser.error("必须提供 --api-key 或 --api-key-file")
    cases = args.cases or ["CASE-2026-0001", "CASE-2026-0008"]

    health = get_json(args.base_url, "/api/v1/health", api_key)
    ops = get_json(args.base_url, "/api/v1/ops/metrics", api_key)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict[str, Any]] = []
    for case_id in cases:
        case_payload = get_json(args.base_url, f"/api/v1/cases/{case_id}", api_key)
        if not case_payload.get("case_id"):
            print(f"案件 {case_id} 无数据，跳过", file=sys.stderr)
            continue
        dashboard = get_json(args.base_url, f"/api/v1/cases/{case_id}/dashboard", api_key)
        document = build_document(case_id, case_payload, dashboard, health, ops)
        problems: list[str] = []
        scan_forbidden(document, "$", problems)
        if problems:
            print(f"案件 {case_id} 审计链导出命中禁止内容，已中止：", file=sys.stderr)
            for item in problems[:5]:
                print(f"  - {item}", file=sys.stderr)
            return 1
        path = args.output_dir / f"audit-chain-{case_id.lower()}.json"
        path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary = document["summary"]
        written.append(
            {
                "case_id": case_id,
                "file": path.name,
                "rows": summary["rows"],
                "first_seq": summary["first_seq"],
                "last_seq": summary["last_seq"],
                "head_hash": summary["head_hash"],
                "chain_ok": summary["chain_ok"],
                "generation_rows": document["generation_window"]["rows"],
                "run_rows": document["run_window"]["rows"],
            }
        )
        print(json.dumps(written[-1], ensure_ascii=False))
    if not written:
        print("没有任何案件被导出", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
