"""Small dependency-free JSON Schema validator for RevGuard Skill contracts.

The published contracts are regular JSON Schema objects.  The runtime only needs a
deliberately small, auditable subset: type, required, properties,
additionalProperties, items, enum, minimum/maximum and minLength.
"""
from __future__ import annotations

from typing import Any


class SchemaValidationError(ValueError):
    """Raised when a Skill input or output violates its published contract."""


_TYPE_NAMES = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _matches_type(value: Any, expected: str) -> bool:
    if expected not in _TYPE_NAMES:
        raise SchemaValidationError(f"不支持的 Schema type: {expected}")
    if expected in {"number", "integer"} and isinstance(value, bool):
        return False
    return isinstance(value, _TYPE_NAMES[expected])


def validate_json(value: Any, schema: dict, *, path: str = "$") -> None:
    """Validate ``value`` against the supported JSON Schema subset."""
    expected = schema.get("type")
    if expected is not None:
        expected_types = [expected] if isinstance(expected, str) else list(expected)
        if not any(_matches_type(value, item) for item in expected_types):
            actual = type(value).__name__
            raise SchemaValidationError(
                f"{path} 类型错误：期望 {'/'.join(expected_types)}，实际 {actual}"
            )

    if "enum" in schema and value not in schema["enum"]:
        raise SchemaValidationError(f"{path} 必须是 {schema['enum']} 之一")

    if isinstance(value, str):
        if len(value) < int(schema.get("minLength", 0)):
            raise SchemaValidationError(f"{path} 长度不足")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaValidationError(f"{path} 不能小于 {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaValidationError(f"{path} 不能大于 {schema['maximum']}")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                raise SchemaValidationError(f"{path} 缺少必填字段: {name}")
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value) - set(properties))
            if extras:
                raise SchemaValidationError(f"{path} 包含未声明字段: {extras}")
        for name, item in value.items():
            item_schema = properties.get(name)
            if item_schema is not None:
                validate_json(item, item_schema, path=f"{path}.{name}")

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            validate_json(item, schema["items"], path=f"{path}[{index}]")
