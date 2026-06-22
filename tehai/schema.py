"""A tiny, dependency-free JSON-Schema (draft-07 subset) validator.

Just enough to actually enforce our own schemas at runtime/test time without
pulling in `jsonschema`. Supports: type (incl. unions), required, properties,
additionalProperties:false, enum, minimum, maximum, minItems, minLength, items.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).parent / "schemas"

_JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "null": type(None),
}


def _type_ok(value: Any, t: str) -> bool:
    if t == "integer":
        # bool is a subclass of int in Python; exclude it.
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    py = _JSON_TYPES.get(t)
    return isinstance(value, py) if py else True


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []

    # type
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_type_ok(instance, tt) for tt in types):
            errors.append(f"{path}: expected type {t}, got {type(instance).__name__}")
            return errors  # further checks are meaningless on a type mismatch

    # enum
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    # numbers
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    # strings
    if isinstance(instance, str) and "minLength" in schema:
        if len(instance) < schema["minLength"]:
            errors.append(f"{path}: string shorter than minLength {schema['minLength']}")

    # arrays
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: array shorter than minItems {schema['minItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    # objects
    if isinstance(instance, dict):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property '{req}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: additional property '{key}' not allowed")
        for key, sub in props.items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))

    return errors


def load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_task_contract(d: dict) -> list[str]:
    return validate(d, load_schema("task_contract.schema.json"))


def validate_agent_template(d: dict) -> list[str]:
    return validate(d, load_schema("agent_template.schema.json"))


def validate_log_record(d: dict) -> list[str]:
    return validate(d, load_schema("log_record.schema.json"))
