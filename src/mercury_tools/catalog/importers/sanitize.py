from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

import mercury_tools.catalog.identity as catalog_identity
from mercury_tools.safety.redaction import redact_text

_REDACTED = "[REDACTED]"
_VALUE_KEYS = {"current", "currentvalue", "default", "example", "examples", "initial"}
_CONTEXT_VALUE_KEYS = _VALUE_KEYS | {"value", "values"}
_VALUE_CONTAINERS = {"cookie", "cookies", "setcookie"}
_DESCRIPTION_KEYS = {"description", "summary", "title"}
_IDENTIFIER_FIELDS = {"header", "headername", "key", "name", "parametername"}
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<key>[a-z0-9_-]*(?:authorization|credential|secret|token|password|"
    r"passwd|pwd|api[_-]?key)[a-z0-9_-]*)\s*[:=]\s*"
    r"(?P<value>(?!\[REDACTED\])(?:(?:basic|bearer)\s+)?[^\s,;]+)"
)
_SENSITIVE_HEADER_TEXT = re.compile(
    r"(?im)\b(?P<key>authorization|proxy-authorization|cookie|set-cookie)"
    r"\s*[:=]\s*(?P<value>(?!\[REDACTED\])[^\r\n]+)"
)
_BASIC_CREDENTIAL = re.compile(
    r"(?i)\bbasic\s+(?!\[REDACTED\])(?:[a-z0-9+/=_-]+)"
)


class SanitizationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    redacted_values: int
    safe: bool


def sanitize_spec(value: Any) -> tuple[Any, SanitizationReport]:
    """Sanitize one parsed specification and count changed scalar values."""
    specialized = _sanitize_spec_fields(value)
    credential_safe = _relocate_sensitive_property_descriptions(specialized)
    sanitized = catalog_identity.sanitize_document(credential_safe)
    redacted_values = _count_changed_scalars(value, specialized)
    redacted_values += _count_changed_scalars(credential_safe, sanitized)
    return sanitized, SanitizationReport(redacted_values=redacted_values, safe=True)


def _sanitize_spec_fields(value: Any, *, parent_key: str = "") -> Any:
    parent_is_value_container = _normalized(parent_key) in _VALUE_CONTAINERS
    if isinstance(value, Mapping):
        cookie_jar = parent_is_value_container and not _named_cookie_record(value)
        context = parent_is_value_container or _value_record_context(value)
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            normalized_key = _normalized(key)
            if cookie_jar or normalized_key in _VALUE_KEYS or (
                context and normalized_key in _CONTEXT_VALUE_KEYS
            ):
                sanitized[key] = _redact_payload(item)
            else:
                sanitized[key] = _sanitize_spec_fields(item, parent_key=key)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_spec_fields(item, parent_key=parent_key) for item in value]
    if parent_is_value_container:
        return _redact_payload(value)
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
            sanitized = catalog_identity.sanitize_document(sanitized)
            return json.dumps(sanitized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    shared = catalog_identity.sanitize_document(value)
    if not isinstance(shared, str):
        raise ValueError("unsupported_canonical_value")
    sanitized = _SENSITIVE_HEADER_TEXT.sub(
        lambda match: f"{match.group('key')}=[REDACTED]",
        shared,
    )
    sanitized = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group('key')}=[REDACTED]",
        sanitized,
    )
    sanitized = _BASIC_CREDENTIAL.sub("[REDACTED_TOKEN]", sanitized)
    return redact_text(sanitized)


def _value_record_context(value: Mapping[Any, Any]) -> bool:
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            continue
        if _normalized(key) not in _IDENTIFIER_FIELDS:
            continue
        normalized = catalog_identity._normalized_name(item)
        if catalog_identity._is_sensitive_key(
            normalized
        ) or catalog_identity._is_sensitive_header_name(normalized):
            return True
    return False


def _named_cookie_record(value: Mapping[Any, Any]) -> bool:
    normalized_keys = {
        _normalized(key) for key in value if isinstance(key, str)
    }
    return bool(normalized_keys & _IDENTIFIER_FIELDS) and bool(
        normalized_keys & _CONTEXT_VALUE_KEYS
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
                if not isinstance(name, str) or not catalog_identity._is_sensitive_key(
                    catalog_identity._normalized_name(name)
                ):
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
