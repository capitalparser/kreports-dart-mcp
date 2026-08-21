"""Self-contained MCP JSON Schema generation for nested Pydantic models."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel


def _inline_refs(
    node: Any,
    definitions: dict[str, Any],
    stack: tuple[str, ...] = (),
) -> Any:
    """Recursively inline local ``#/$defs/...`` references.

    MCP clients are not guaranteed to preserve Pydantic's top-level ``$defs``.
    KReports therefore emits a self-contained schema while rejecting recursive
    models that cannot be safely flattened.
    """
    if isinstance(node, list):
        return [_inline_refs(item, definitions, stack) for item in node]
    if not isinstance(node, dict):
        return node

    ref = node.get("$ref")
    if isinstance(ref, str):
        prefix = "#/$defs/"
        if not ref.startswith(prefix):
            raise ValueError(f"unsupported JSON Schema reference: {ref}")
        key = ref[len(prefix):]
        if key not in definitions:
            raise ValueError(f"dangling JSON Schema reference: {ref}")
        if key in stack:
            raise ValueError(
                f"recursive JSON Schema reference is not supported: {ref}"
            )
        replacement = deepcopy(definitions[key])
        overlay = {
            name: value
            for name, value in node.items()
            if name != "$ref"
        }
        replacement.update(overlay)
        return _inline_refs(replacement, definitions, (*stack, key))

    return {
        key: _inline_refs(value, definitions, stack)
        for key, value in node.items()
    }


def _clean_legacy_shape(node: Any) -> None:
    if isinstance(node, dict):
        node.pop("title", None)
        if node.get("default") is None:
            node.pop("default", None)
        any_of = node.get("anyOf")
        if isinstance(any_of, list):
            non_null = [
                item
                for item in any_of
                if not (
                    isinstance(item, dict)
                    and item.get("type") == "null"
                )
            ]
            if len(non_null) == 1 and isinstance(non_null[0], dict):
                replacement = dict(non_null[0])
                node.pop("anyOf", None)
                node.update(replacement)
        node.pop("format", None)
        node.pop("writeOnly", None)
        for value in list(node.values()):
            _clean_legacy_shape(value)
    elif isinstance(node, list):
        for value in node:
            _clean_legacy_shape(value)


def find_json_schema_refs(node: Any) -> list[str]:
    """Return every remaining JSON Schema reference."""
    refs: list[str] = []
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            refs.append(ref)
        for value in node.values():
            refs.extend(find_json_schema_refs(value))
    elif isinstance(node, list):
        for value in node:
            refs.extend(find_json_schema_refs(value))
    return refs


def legacy_compatible_schema(
    model: type[BaseModel],
    name: str,
) -> dict[str, Any]:
    """Generate the established wire shape without dangling local references."""
    raw_schema = model.model_json_schema()
    definitions = raw_schema.pop("$defs", {})
    schema = _inline_refs(raw_schema, definitions)
    schema.pop("title", None)
    schema.pop("additionalProperties", None)
    _clean_legacy_shape(schema)

    refs = find_json_schema_refs(schema)
    if refs:
        raise ValueError(
            f"unresolved JSON Schema references: {refs[:3]}"
        )

    if name == "get_industry_audit_landscape":
        schema["oneOf"] = [
            {"required": ["company"]},
            {"required": ["induty_code"]},
        ]
    return schema
