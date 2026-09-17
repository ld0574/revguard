#!/usr/bin/env python3
"""CHANGELOG 与版本号发布门禁（CI 门禁）。

对应竞品分析里的 `changelog-check`：避免“包版本、CHANGELOG 最新发布条目、导出的
OpenAPI 文档”三处版本号各说各话。检查内容：

1. CHANGELOG 首节必须是 ``Unreleased``，其余每节必须是 ``## <版本> — YYYY-MM-DD``；
2. 版本号不重复，且按语义化版本严格降序（含 rc / beta / alpha 预发布次序）；
3. 最新发布条目归一化后等于 ``revguard.__version__``；
4. ``docs/openapi.json`` 的 ``info.version`` 与包版本一致；
5. 传 ``--tag`` 时（CI 打 tag 发布），tag 归一化后也必须等于包版本。

任一不成立即以非零退出，供 GitHub Actions 与 ``make generated-check`` 使用。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^##\s+(?P<name>.+?)\s*$")
VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
                        r"(?:-(?P<stage>rc|beta|alpha)\.?(?P<number>\d+))?$")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
STAGE_RANK = {"alpha": 0, "beta": 1, "rc": 2}
PINNED_VERSION_RE = re.compile(r'__version__\s*=\s*"([^"]+)"')


def fail(message: str) -> None:
    print(f"CHANGELOG 校验失败: {message}", file=sys.stderr)
    raise SystemExit(1)


def normalize(text: str) -> str:
    """把 0.6.0rc3 / v0.6.0-rc3 / 0.6.0-rc3 归一到同一形状。"""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def version_key(version: str) -> tuple[int, int, int, int, int]:
    match = VERSION_RE.match(version)
    if not match:
        fail(f"版本号格式不支持: {version!r}（期望 X.Y.Z 或 X.Y.Z-rcN）")
    return (
        int(match["major"]),
        int(match["minor"]),
        int(match["patch"]),
        STAGE_RANK.get(match["stage"] or "", 3),
        int(match["number"] or 0),
    )


def parse_headings(changelog: Path) -> tuple[str | None, list[tuple[str, str]]]:
    """返回 (Unreleased 标题, [(版本, 日期)…])，顺序与文件一致。"""
    if not changelog.exists():
        fail(f"缺少 {changelog.name}")
    text = changelog.read_text(encoding="utf-8")
    if not text.lstrip().startswith("# Changelog"):
        fail("CHANGELOG 首行必须是 `# Changelog`")
    headings: list[str] = []
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            headings.append(match["name"].strip())
    if not headings:
        fail("CHANGELOG 没有任何二级标题")
    if not headings[0].lower().startswith("unreleased"):
        fail(f"首个二级标题必须是 Unreleased，实际是 {headings[0]!r}")

    releases: list[tuple[str, str]] = []
    for heading in headings[1:]:
        version, separator, date_part = heading.partition("—")
        if not separator:
            fail(f"发布条目标题缺少「— 日期」: {heading!r}")
        version = version.strip()
        version_key(version)  # 格式不合法会在这里失败
        date_match = DATE_RE.search(date_part)
        if not date_match:
            fail(f"发布条目缺少 YYYY-MM-DD 日期: {heading!r}")
        releases.append((version, date_match.group(1)))
    if not releases:
        fail("CHANGELOG 没有任何已发布版本条目")
    return headings[0], releases


def check(root: Path, tag: str | None = None) -> tuple[str, str]:
    unreleased, releases = parse_headings(root / "CHANGELOG.md")

    seen: dict[str, str] = {}
    for version, _date in releases:
        key = normalize(version)
        if key in seen:
            fail(f"版本 {version} 在 CHANGELOG 中重复出现（已见 {seen[key]}）")
        seen[key] = version

    keys = [version_key(version) for version, _date in releases]
    for index in range(1, len(keys)):
        if keys[index] >= keys[index - 1]:
            fail(f"版本必须严格降序：{releases[index][0]} 排在了 {releases[index - 1][0]} 之后")

    package_version = package_version_from(root)
    latest_version, latest_date = releases[0]
    if normalize(latest_version) != normalize(package_version):
        fail(f"包版本 {package_version!r} 与 CHANGELOG 最新发布条目 {latest_version!r} 不一致")

    openapi_version = openapi_version_from(root)
    if normalize(openapi_version) != normalize(package_version):
        fail(f"docs/openapi.json 的 info.version {openapi_version!r} 与包版本 {package_version!r} 不一致")

    if tag:
        normalized_tag = normalize(tag)
        if not normalized_tag.startswith("v"):
            fail(f"--tag 参数应为发布 tag（如 v0.6.0-rc3），实际是 {tag!r}")
        if normalized_tag[1:] != normalize(package_version):
            fail(f"发布 tag {tag!r} 与包版本 {package_version!r} 不一致")

    return unreleased, latest_version


def package_version_from(root: Path) -> str:
    init_path = root / "revguard" / "__init__.py"
    if not init_path.exists():
        fail(f"缺少 {init_path.relative_to(root)}")
    match = PINNED_VERSION_RE.search(init_path.read_text(encoding="utf-8"))
    if not match:
        fail(f"{init_path.relative_to(root)} 里没有 __version__")
    return match.group(1)


def openapi_version_from(root: Path) -> str:
    openapi_path = root / "docs" / "openapi.json"
    if not openapi_path.exists():
        fail(f"缺少 {openapi_path.relative_to(root)}（先跑 scripts/export_openapi.py）")
    document = json.loads(openapi_path.read_text(encoding="utf-8"))
    version = (document.get("info") or {}).get("version")
    if not version:
        fail("docs/openapi.json 缺少 info.version")
    return str(version)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验 CHANGELOG、包版本与 OpenAPI 版本一致")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag", default=None, help="发布 tag（如 v0.6.0-rc3），仅在打 tag 时传")
    args = parser.parse_args(argv)
    unreleased, version = check(args.root, args.tag)
    print(f"CHANGELOG 校验通过: 最新发布 {version}，Unreleased 段落：{unreleased}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
