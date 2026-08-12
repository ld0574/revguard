"""导出 FastAPI OpenAPI 3.1，并嵌入 RevGuard Skill Registry 扩展。"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "docs" / "openapi.json"
sys.path.insert(0, str(ROOT))


def _schema_example(schema: dict, field_name: str = "value"):
    if "examples" in schema and schema["examples"]:
        return schema["examples"][0]
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        schema_type = next((item for item in schema_type if item != "null"), "null")
    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", properties.keys())
        return {
            name: _schema_example(properties[name], name)
            for name in required
            if name in properties
        }
    if schema_type == "array":
        return [_schema_example(schema.get("items", {}), field_name)]
    if schema_type in {"number", "integer"}:
        return schema.get("minimum", 1)
    if schema_type == "boolean":
        return False
    if schema_type == "null":
        return None
    return f"example-{field_name.replace('_', '-')}"


def build_document() -> dict:
    from revguard.api import app
    from revguard.skill_runtime import SKILL_ACTORS
    from revguard.skills import list_skills

    document = app.openapi()
    document["x-revguard-skill-registry"] = [
        {
            **skill,
            "invoke_endpoint": f"/api/v1/skills/{skill['name']}/invoke",
            "allowed_actors": sorted(SKILL_ACTORS[skill["name"]]),
            "example": {
                "case_id": "CASE-EXAMPLE-001",
                "input": _schema_example(skill["input_schema"]),
            },
        }
        for skill in list_skills()
    ]
    return document


def _configure_isolated_runtime(temp_dir: Path) -> None:
    os.environ.setdefault("REVGUARD_ALLOW_INSECURE_DEMO_KEYS", "true")
    os.environ.setdefault(
        "REVGUARD_APPROVAL_SIGNING_KEY",
        "revguard-openapi-export-signing-key-only-for-local-generation",
    )
    os.environ["REVGUARD_ENABLE_LEGACY_TOOL_API"] = "false"
    os.environ["REVGUARD_DB_PATH"] = str(temp_dir / "openapi.db")
    os.environ["REVGUARD_GATEWAY_STATE_PATH"] = str(temp_dir / "gateway.json")
    os.environ["REVGUARD_OUTPUT_DIR"] = str(temp_dir / "outputs")
    os.environ["REVGUARD_REPORT_DIR"] = str(temp_dir / "reports")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出或校验 RevGuard OpenAPI")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="只校验，不改写文件")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="revguard-openapi-") as temp:
        _configure_isolated_runtime(Path(temp))
        rendered = json.dumps(build_document(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            raise SystemExit(
                "OpenAPI 已漂移，请运行: python scripts/export_openapi.py"
            )
        print(f"verified {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(f"generated {args.output}")


if __name__ == "__main__":
    main()
