import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_SENSITIVE_VALUE_PREFIXES = (
    "basic ",
    "bearer ",
    "digest ",
    "sk-",
    "sk_",
    "xoxb-",
    "xoxp-",
    "ya29.",
)


def canonical_json(value: Any) -> str:
    """Serialize JSON-safe data deterministically without lossy coercion."""
    return json.dumps(
        _canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sanitize_document(value: Any) -> Any:
    """Remove secret-bearing values while retaining deterministic source shape."""
    return _sanitize_value(value)


def build_source_id(connector_id: str, source_uri: str, source_hash: str) -> str:
    identity = f"{connector_id.casefold()}|{source_uri}|{source_hash}"
    return "src_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def build_action_id(action: Any) -> str:
    identity = "|".join(
        (
            action.connector_id.lower(),
            _enum_value(action.method),
            action.path_template,
            action.operation_id,
            action.variant_id,
        )
    )
    return "act_" + hashlib.sha256(identity.encode()).hexdigest()[:24]


def build_version_id(action: Any) -> str:
    data = action.model_dump(mode="json", exclude={"version_id"})
    return "av_" + hashlib.sha256(canonical_json(data).encode()).hexdigest()


def validate_action_identity(action: Any) -> None:
    if action.action_id != build_action_id(action):
        raise ValueError("catalog_action_id_invalid")
    if action.version_id != build_version_id(action):
        raise ValueError("catalog_action_version_invalid")


def validate_source_identity(source: Any) -> None:
    expected = build_source_id(source.connector_id, source.source_uri, source.source_hash)
    if source.source_id != expected:
        raise ValueError("catalog_source_id_invalid")


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_canonical_number")
        return value
    if isinstance(value, Mapping):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            canonical[key] = _canonical_value(item)
        return canonical
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError("unsupported_canonical_value")


def _sanitize_value(value: Any, *, secret_key: bool = False) -> Any:
    if secret_key:
        _canonical_value(value)
        return "[REDACTED]"
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            sanitized[key] = _sanitize_value(
                item,
                secret_key=any(marker in key.casefold() for marker in _SENSITIVE_KEY_MARKERS),
            )
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str) and value.casefold().startswith(_SENSITIVE_VALUE_PREFIXES):
        return "[REDACTED]"
    return _canonical_value(value)


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
