"""Bounded, value-free response shape extraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_DEPTH = 6
MAX_FIELDS = 128

_TYPE_DESCRIPTORS = frozenset(
    {"boolean", "integer", "null", "number", "string", "truncated", "unknown"}
)
_PUBLIC_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "apisecret",
        "authorization",
        "authtoken",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "payload",
        "raw",
        "rawpayload",
        "rawresponse",
        "refreshtoken",
        "request",
        "requestbody",
        "response",
        "responsebody",
        "secret",
        "token",
    }
)


def extract_response_shape(value: Any, *, depth: int = 0) -> dict[str, Any] | str:
    if depth >= MAX_DEPTH:
        return "truncated"
    if isinstance(value, Mapping):
        keys = sorted(value, key=str)[:MAX_FIELDS]
        return {
            str(key): extract_response_shape(value[key], depth=depth + 1)
            for key in keys
        }
    if isinstance(value, list):
        return {
            "type": "array",
            "items": extract_response_shape(value[0], depth=depth + 1) if value else "unknown",
        }
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return "string"


def _validate_approved_public_response_shape(value: Any) -> None:
    if not _is_approved_public_response_shape(value):
        raise ValueError("approved_public_response_shape_unsafe")


def _is_approved_public_response_shape(value: Any) -> bool:
    if isinstance(value, str):
        return value in _TYPE_DESCRIPTORS
    if not isinstance(value, Mapping):
        return False

    if set(value) == {"type", "items"}:
        return value["type"] == "array" and _is_approved_public_response_shape(value["items"])

    return all(
        _is_allowed_public_field_name(field_name)
        and _is_approved_public_response_shape(field_shape)
        for field_name, field_shape in value.items()
    )


def _is_allowed_public_field_name(field_name: Any) -> bool:
    if not isinstance(field_name, str) or not field_name:
        return False

    normalized = "".join(character for character in field_name.lower() if character.isalnum())
    return (
        bool(normalized)
        and normalized not in _PUBLIC_FORBIDDEN_FIELD_NAMES
        and not normalized.startswith(("local", "raw", "source"))
        and not normalized.endswith(("directory", "filepath", "filename", "path"))
    )
