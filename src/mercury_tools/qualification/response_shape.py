"""Bounded, value-free response shape extraction."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_DEPTH = 6
MAX_FIELDS = 128


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
