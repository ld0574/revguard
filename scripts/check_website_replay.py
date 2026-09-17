#!/usr/bin/env python3
"""校验官网回放页与数据包一致（CI 门禁）。

回放页的案件清单由 ``website/data/index.json`` 驱动，页面本身不写死案件编号。
这个门禁保证“页面上能点到的、索引里声明的、数据包里实际存在的”三者一致，
避免出现界面显示某案件、数据包却缺失或步数不符的情况。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCHEMA = "revguard.replay/v1"
REQUIRED_FIELDS = (
    "case_id", "file", "status", "title", "summary", "tone", "steps", "spans", "audit_events",
)
LINK_RE = re.compile(r"replay\.html\?case=([A-Za-z0-9_.-]+)")


def fail(message: str) -> None:
    print(f"官网回放索引校验失败: {message}", file=sys.stderr)
    raise SystemExit(1)


def check(root: Path) -> list[str]:
    website = root / "website"
    data_dir = website / "data"
    index_path = data_dir / "index.json"
    if not index_path.exists():
        fail(f"缺少 {index_path.relative_to(root)}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("schema") != SCHEMA:
        fail(f"index.json 的 schema 不是 {SCHEMA}")
    cases = index.get("cases") or []
    if not cases:
        fail("index.json 没有声明任何案件")

    case_ids: list[str] = []
    for entry in cases:
        case_id = str(entry.get("case_id") or "<未知案件>")
        missing = [field for field in REQUIRED_FIELDS if entry.get(field) in (None, "")]
        if missing:
            fail(f"{case_id} 缺少字段 {missing}")
        if case_id in case_ids:
            fail(f"{case_id} 在索引中重复出现")
        bundle_path = data_dir / str(entry["file"])
        if not bundle_path.exists():
            fail(f"{case_id} 的数据包不存在: {bundle_path.relative_to(root)}")
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        if bundle.get("schema") != SCHEMA:
            fail(f"{entry['file']} 的 schema 不是 {SCHEMA}")
        case = bundle.get("case") or {}
        if case.get("case_id") != entry["case_id"]:
            fail(f"{entry['file']} 的 case_id 与索引不一致")
        if case.get("status") != entry["status"]:
            fail(f"{entry['file']} 的 status 与索引不一致")
        if len(bundle.get("steps") or []) != entry["steps"]:
            fail(f"{entry['file']} 的步骤数与索引不一致")
        if len((bundle.get("trace") or {}).get("spans") or []) != entry["spans"]:
            fail(f"{entry['file']} 的跨度数与索引不一致")
        audit = bundle.get("audit") or {}
        if audit.get("count") != entry["audit_events"]:
            fail(f"{entry['file']} 的审计事件数与索引不一致")
        if audit.get("chain_ok") is not True:
            fail(f"{case_id} 的审计哈希链校验未通过")
        case_ids.append(case_id)

    home = (website / "index.html").read_text(encoding="utf-8")
    for linked in LINK_RE.findall(home):
        if linked not in case_ids:
            fail(f"index.html 链接的案件 {linked} 不在回放索引中")

    replay_html = (website / "replay.html").read_text(encoding="utf-8")
    if "data-case=" in replay_html:
        fail("replay.html 仍写死案件标签；案件清单必须由 index.json 驱动")

    return case_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验官网回放页与数据包一致")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    case_ids = check(args.root)
    print(f"官网回放索引校验通过: {len(case_ids)} 个案件 {case_ids}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
