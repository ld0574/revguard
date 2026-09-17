#!/usr/bin/env python3
"""生成/校验 Skill 三级摘要基线（config/skill-integrity.json）。

    python3 scripts/gen_skill_integrity.py --write   # 确认改动后重写基线
    python3 scripts/gen_skill_integrity.py --check   # CI/部署门禁：不一致即非零退出

`--check` 与运行期 `assert_registry_integrity()` 使用同一套比较逻辑，
因此它可以直接当作"Skill 实现是否被悄悄换过"的独立验证入口。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.skill_integrity import (
    SCHEMA,
    assert_registry_integrity,
    baseline,
    catalog,
    default_pinned_path,
)
from revguard.skills import SKILL_REGISTRY


def write(path: Path) -> int:
    payload = baseline(catalog(SKILL_REGISTRY))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入 {len(payload['skills'])} 个 Skill 的三级摘要基线: {path}")
    return 0


def check(path: Path) -> int:
    try:
        assert_registry_integrity(SKILL_REGISTRY, path)
    except Exception as exc:  # noqa: BLE001 - CLI 边界，错误类型已足够
        print(f"Skill 三级摘要校验失败: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Skill 三级摘要与基线一致（{len(SKILL_REGISTRY)} 个 Skill，schema={SCHEMA}）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="重写基线文件")
    parser.add_argument("--check", action="store_true", help="只校验，不写文件")
    parser.add_argument("--path", type=Path, default=default_pinned_path())
    args = parser.parse_args()
    if args.write == args.check:
        parser.error("必须且只能指定 --write 或 --check")
    if args.write:
        return write(args.path)
    target = args.path
    if not target.exists():
        print(f"Skill 摘要基线缺失: {target}", file=sys.stderr)
        return 1
    return check(target)


if __name__ == "__main__":
    raise SystemExit(main())
