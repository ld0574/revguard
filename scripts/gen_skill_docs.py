#!/usr/bin/env python3
"""从 Skill 注册中心自动生成 Skill 清单文档（docs/skills.md）。

保证文档与代码永远一致——Skill 增删改后重新运行本脚本即可：
    python3 scripts/gen_skill_docs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from revguard.skills import list_skills  # noqa: E402
from revguard.skill_runtime import SKILL_ACTORS  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "docs" / "skills.md"


def main() -> None:
    lines = [
        "# RevGuard Skill 清单",
        "",
        "> 本文件由 `scripts/gen_skill_docs.py` 从 `revguard/skills.py` 的 SKILL_REGISTRY",
        "> 自动生成，请勿手工编辑。字段对齐参赛手册附录 B。",
        "",
        f"共 **{len(list_skills())}** 个 Skill。设计原则：输入输出结构化、单一稳定能力、",
        "LLM 理解与确定性计算分离、失败返回明确错误类型、高风险 Skill 强制审批凭证。",
        "",
        "| Skill | 类型 | 用途 | 依赖工具 | 失败处理 | 安全边界 | 复用场景 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in list_skills():
        sec = s["security"]
        sec_text = ", ".join(f"{k}={v}" for k, v in sec.items())
        lines.append(
            f"| `{s['name']}` v{s['version']} | {s['type']} | {s['description']} "
            f"| {', '.join(s['dependencies']) or '-'} "
            f"| {', '.join(s['failure_handling'])} | {sec_text} "
            f"| {', '.join(s['reusability'])} |")
    lines += [
        "",
        "## 输入 / 输出契约",
        "",
    ]
    for s in list_skills():
        lines.append(f"### {s['name']}")
        lines.append("")
        required = set(s["input_schema"].get("required", []))
        optional = [name for name in s["inputs"] if name not in required]
        lines.append(f"- 必填输入：{', '.join(f'`{i}`' for i in s['inputs'] if i in required) or '-'}")
        lines.append(f"- 可选输入：{', '.join(f'`{i}`' for i in optional) or '-'}")
        lines.append(f"- 输出：{', '.join(f'`{o}`' for o in s['outputs'])}")
        lines.append(f"- 调用：`POST /api/v1/skills/{s['name']}/invoke`")
        lines.append(f"- 允许身份：{', '.join(f'`{a}`' for a in sorted(SKILL_ACTORS[s['name']]))}")
        lines.append(f"- 说明：{s['description']}")
        lines.append("")
        lines.append("<details><summary>Input / Output JSON Schema</summary>")
        lines.append("")
        lines.append("```json")
        lines.append(json.dumps({"input": s["input_schema"], "output": s["output_schema"]},
                                ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"generated {OUT} ({len(list_skills())} skills)")


if __name__ == "__main__":
    main()
