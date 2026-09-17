"""Skill 三级摘要：manifest / instruction / callable（加载期校验，不匹配即 fail-closed）。

三级摘要回答三个不同的问题，缺一不可：

| 级别 | 摘要对象 | 回答的问题 |
|---|---|---|
| `manifest` | Skill 声明的元数据（版本、类型、依赖、失败处理、安全边界、复用场景） | 这个 Skill **声称自己是什么** |
| `instruction` | 公开输入/输出 JSON Schema | 它**承诺的输入输出语义是什么** |
| `callable` | 实现函数的规范化源码 | 它**实际执行的是哪段代码** |

只有三者同时被固定，第三方才能确认"文档里写的 Skill"与"真正跑起来的函数"
是同一件东西；否则一份 Skill 清单可以在不改版本号的情况下悄悄换掉实现。

基线文件默认是仓库内的 `config/skill-integrity.json`（可用
`REVGUARD_SKILL_INTEGRITY_PATH` 覆盖）。加载期校验失败即抛
`SkillIntegrityError`，进程拒绝启动——这是 fail-closed，不是告警。
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import textwrap
from datetime import UTC, datetime
from pathlib import Path

LEVELS: tuple[str, ...] = ("manifest", "instruction", "callable")
SCHEMA = "revguard.skill-integrity/1"
MANIFEST_FIELDS: tuple[str, ...] = (
    "name", "version", "type", "description", "dependencies",
    "failure_handling", "security", "annotations", "reusability",
)
INSTRUCTION_FIELDS: tuple[str, ...] = ("input_schema", "output_schema")


class SkillIntegrityError(RuntimeError):
    """Skill 摘要与基线不一致（或基线缺失）；调用方必须拒绝启动。"""


def canonical_json(value) -> str:
    """稳定序列化：键排序、无多余空白、Non-ASCII 不转义。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_of(value) -> str:
    payload = value if isinstance(value, str) else canonical_json(value)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_source(source: str) -> str:
    """源码规范化：去掉包裹缩进与行尾空白，统一尾换行。

    只消除"重新缩进一次"这类无意义差异，任何语义改动都会改变摘要。
    """
    dedented = textwrap.dedent(source).rstrip()
    lines = [line.rstrip() for line in dedented.splitlines()]
    return "\n".join(lines) + "\n"


def manifest_of(meta: dict) -> dict:
    return {field: meta.get(field) for field in MANIFEST_FIELDS}


def instruction_of(meta: dict) -> dict:
    return {field: meta.get(field) for field in INSTRUCTION_FIELDS}


def callable_source(meta: dict) -> str:
    func = meta.get("func")
    if func is None:
        raise SkillIntegrityError(f"Skill {meta.get('name')} 缺少实现函数，无法计算 callable 摘要")
    try:
        source = inspect.getsource(func)
    except (OSError, TypeError) as exc:  # pragma: no cover - 需要缺源码的环境
        raise SkillIntegrityError(
            f"Skill {meta.get('name')} 的实现源码不可读，拒绝在无法校验的状态下运行"
        ) from exc
    return normalize_source(source)


def skill_digests(name: str, meta: dict) -> dict[str, str]:
    return {
        "version": meta.get("version"),
        "manifest": digest_of(manifest_of(meta)),
        "instruction": digest_of(instruction_of(meta)),
        "callable": digest_of(callable_source(meta)),
    }


def catalog(registry: dict) -> dict[str, dict]:
    """把注册表折算成"每 Skill 三级摘要"的可发布清单。"""
    return {name: skill_digests(name, meta) for name, meta in registry.items()}


def default_pinned_path() -> Path:
    override = os.getenv("REVGUARD_SKILL_INTEGRITY_PATH")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "config" / "skill-integrity.json"


def baseline(catalog_map: dict[str, dict], *, generated_at: str | None = None) -> dict:
    return {
        "schema": SCHEMA,
        "generated_at": generated_at or datetime.now(UTC).isoformat(),
        "generator": "scripts/gen_skill_integrity.py",
        "skill_count": len(catalog_map),
        "levels": list(LEVELS),
        "skills": {name: dict(entry) for name, entry in sorted(catalog_map.items())},
    }


def diff_against_baseline(catalog_map: dict[str, dict], pinned: dict) -> list[dict]:
    """返回全部差异；空列表表示三级摘要与基线完全一致。"""
    if pinned.get("schema") != SCHEMA:
        raise SkillIntegrityError(
            f"Skill 摘要基线 schema 不受支持: {pinned.get('schema')!r}"
        )
    expected = pinned.get("skills")
    if not isinstance(expected, dict):
        raise SkillIntegrityError("Skill 摘要基线缺少 skills 映射")
    problems: list[dict] = []
    for name in sorted(set(catalog_map) | set(expected)):
        live = catalog_map.get(name)
        pinned_entry = expected.get(name) or {}
        if live is None:
            problems.append({"skill": name, "level": "manifest", "reason": "baseline-only"})
            continue
        if not pinned_entry:
            problems.append({"skill": name, "level": "manifest", "reason": "unregistered"})
            continue
        for level in LEVELS:
            if live.get(level) != pinned_entry.get(level):
                problems.append({
                    "skill": name, "level": level, "reason": "drift",
                    "expected": pinned_entry.get(level), "actual": live.get(level),
                })
        if live.get("version") != pinned_entry.get("version"):
            problems.append({
                "skill": name, "level": "manifest", "reason": "version-drift",
                "expected": pinned_entry.get("version"), "actual": live.get("version"),
            })
    return problems


def load_baseline(path: Path | None = None) -> dict:
    target = path or default_pinned_path()
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SkillIntegrityError(
            f"Skill 摘要基线缺失: {target}；先运行 scripts/gen_skill_integrity.py --write"
        ) from exc
    except json.JSONDecodeError as exc:
        raise SkillIntegrityError(f"Skill 摘要基线不是合法 JSON: {target}") from exc


def assert_registry_integrity(registry: dict, path: Path | None = None) -> dict:
    """加载期门禁：三级摘要必须与基线一致，否则抛错并阻止启动。"""
    live = catalog(registry)
    pinned = load_baseline(path)
    problems = diff_against_baseline(live, pinned)
    if problems:
        summary = "; ".join(
            f"{item['skill']}.{item['level']}({item['reason']})" for item in problems[:6]
        )
        more = "" if len(problems) <= 6 else f" 等 {len(problems)} 处"
        raise SkillIntegrityError(
            f"Skill 三级摘要与基线不一致：{summary}{more}；"
            "确认改动后用 scripts/gen_skill_integrity.py --write 重新生成基线"
        )
    return live
