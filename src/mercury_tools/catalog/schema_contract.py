"""Shared canonical names for executable catalog schemas."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mercury_tools.catalog.identity import sanitize_document
from mercury_tools.safety.redaction import redact_text

_MAX_SCHEMA_NAME_BYTES = 512


def is_canonical_schema_name(value: Any) -> bool:
    try:
        within_limit = (
            isinstance(value, str)
            and len(value.encode("utf-8")) <= _MAX_SCHEMA_NAME_BYTES
        )
    except UnicodeError:
        return False
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not within_limit
        or value in {".", ".."}
        or any(character in value for character in ("/", "\\", "=", "%"))
        or "://" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    sanitized = str(sanitize_document(value))
    return sanitized == value and redact_text(sanitized) == value


def validate_required_schema_contract(
    schema: Any,
    *,
    allow_frozen_required: bool = True,
) -> None:
    """Validate canonical object-property names and JSON Schema required semantics."""

    _validate_required_schema_node(
        schema,
        top_level=True,
        required_types=(list, tuple) if allow_frozen_required else (list,),
    )


def _validate_required_schema_node(
    schema: Any,
    *,
    top_level: bool,
    required_types: tuple[type, ...],
) -> None:
    if not isinstance(schema, Mapping):
        raise ValueError("schema_required_contract_invalid")
    if "x-mercury-required" in schema and (
        not top_level or not isinstance(schema["x-mercury-required"], bool)
    ):
        raise ValueError("schema_required_contract_invalid")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping) or any(
        not is_canonical_schema_name(name) for name in properties
    ):
        raise ValueError("schema_required_contract_invalid")
    if any(not isinstance(declaration, Mapping) for declaration in properties.values()):
        raise ValueError("schema_required_contract_invalid")
    if "required" in schema:
        required = schema["required"]
        if (
            schema.get("type") != "object"
            or not isinstance(required, required_types)
            or any(not is_canonical_schema_name(name) for name in required)
            or len(required) != len(set(required))
            or any(name not in properties for name in required)
        ):
            raise ValueError("schema_required_contract_invalid")

    for declaration in properties.values():
        _validate_required_schema_node(
            declaration,
            top_level=False,
            required_types=required_types,
        )
    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, Mapping):
            raise ValueError("schema_required_contract_invalid")
        _validate_required_schema_node(
            items,
            top_level=False,
            required_types=required_types,
        )


__all__ = ["is_canonical_schema_name", "validate_required_schema_contract"]
