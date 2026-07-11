from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from mercury_tools.catalog.identity import sanitize_document
from mercury_tools.safety.redaction import redact_text

_REDACTED = "[REDACTED]"
_VALUE_KEYS = {"current", "currentvalue", "default", "example", "examples", "initial"}
_CONTEXT_VALUE_KEYS = _VALUE_KEYS | {"value", "values"}
_VALUE_CONTAINERS = {
    "cookie",
    "cookies",
    "header",
    "headers",
    "variable",
    "variables",
}
_DESCRIPTION_KEYS = {"description", "summary", "title"}
_SENSITIVE_PROPERTY = re.compile(
    r"(?:authorization|credential|secret|token|password|api[_-]?key)",
    re.IGNORECASE,
)


class SanitizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    redacted_values: int
    safe: bool


def sanitize_spec(value: Any) -> tuple[Any, SanitizationReport]:
    """Sanitize one parsed specification and count changed scalar values."""
    specialized = _sanitize_spec_fields(value)
    credential_safe = _relocate_sensitive_property_descriptions(specialized)
    sanitized = sanitize_document(credential_safe)
    redacted_values = _count_changed_scalars(value, specialized)
    redacted_values += _count_changed_scalars(credential_safe, sanitized)
    return sanitized, SanitizationReport(redacted_values=redacted_values, safe=True)


def _sanitize_spec_fields(value: Any, *, parent_key: str = "") -> Any:
    if isinstance(value, Mapping):
        context = _normalized(parent_key) in _VALUE_CONTAINERS or _value_record_context(value)
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            normalized_key = _normalized(key)
            if normalized_key in _VALUE_KEYS or (
                context and normalized_key in _CONTEXT_VALUE_KEYS
            ):
                sanitized[key] = _redact_payload(item)
            else:
                sanitized[key] = _sanitize_spec_fields(item, parent_key=key)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_spec_fields(item, parent_key=parent_key) for item in value]
    if isinstance(value, str):
        return _sanitize_string(value)
    return value


def _redact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            if _normalized(key) in _DESCRIPTION_KEYS:
                redacted[key] = _sanitize_spec_fields(item, parent_key=key)
            else:
                redacted[key] = _redact_payload(item)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_payload(item) for item in value]
    if value is None or value == "" or value == _REDACTED:
        return value
    return _REDACTED


def _sanitize_string(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith(("{", "[")):
        try:
            decoded = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            pass
        else:
            sanitized = _sanitize_spec_fields(decoded)
            sanitized = sanitize_document(sanitized)
            return json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return redact_text(value)


def _value_record_context(value: Mapping[Any, Any]) -> bool:
    location = value.get("in")
    if isinstance(location, str) and location.casefold() in {"header", "cookie"}:
        return True
    identifiers = (value.get("key"), value.get("name"))
    return any(
        isinstance(item, str)
        and _normalized(item)
        in {
            "authorization",
            "cookie",
            "proxyauthorization",
            "setcookie",
            "xapikey",
            "xaccesstoken",
            "xauthtoken",
        }
        for item in identifiers
    )


def _relocate_sensitive_property_descriptions(value: Any) -> Any:
    if isinstance(value, Mapping):
        relocated = {
            key: _relocate_sensitive_property_descriptions(item) for key, item in value.items()
        }
        properties = relocated.get("properties")
        if isinstance(properties, dict):
            descriptions: list[dict[str, str]] = []
            for name, schema in properties.items():
                if not isinstance(name, str) or not _SENSITIVE_PROPERTY.search(name):
                    continue
                if isinstance(schema, dict) and isinstance(schema.get("description"), str):
                    descriptions.append(
                        {"name": name, "description": schema.pop("description")}
                    )
            if descriptions:
                existing = relocated.get("x-mercury-property-descriptions")
                if isinstance(existing, list):
                    descriptions.extend(existing)
                relocated["x-mercury-property-descriptions"] = descriptions
        return relocated
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_relocate_sensitive_property_descriptions(item) for item in value]
    return value


def _count_changed_scalars(before: Any, after: Any) -> int:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        return sum(
            _count_changed_scalars(item, after.get(key)) for key, item in before.items()
        )
    if (
        isinstance(before, Sequence)
        and not isinstance(before, (str, bytes, bytearray))
        and isinstance(after, Sequence)
        and not isinstance(after, (str, bytes, bytearray))
    ):
        return sum(
            _count_changed_scalars(left, right)
            for left, right in zip(before, after, strict=False)
        )
    return int(before != after)


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
