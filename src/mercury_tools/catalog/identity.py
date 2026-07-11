import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "accesstoken",
    "apikey",
    "apisecret",
    "authorization",
    "authtoken",
    "clientsecret",
    "credential",
    "credentials",
    "credentialvalue",
    "idtoken",
    "password",
    "passwd",
    "pwd",
    "refreshtoken",
    "secret",
    "secretvalue",
    "token",
}
_SENSITIVE_KEY_SUFFIXES = (
    "apikeys",
    "apikey",
    "authorization",
    "credentials",
    "credential",
    "passwords",
    "password",
    "secrets",
    "secret",
    "tokens",
    "token",
)
_METADATA_KEY_SUFFIXES = (
    "format",
    "granttype",
    "name",
    "names",
    "prefix",
    "scheme",
    "scope",
    "scopes",
    "type",
    "uri",
    "uris",
    "url",
    "urls",
)
_SENSITIVE_HEADER_NAMES = {
    "apikey",
    "authorization",
    "clientsecret",
    "xapikey",
    "xclientsecret",
}
_HEADER_IDENTIFIER_FIELDS = {"header", "headername", "key", "name"}
_HEADER_VALUE_FIELDS = {
    "current",
    "currentvalue",
    "default",
    "example",
    "examples",
    "secret",
    "value",
    "values",
}
_SENSITIVE_VALUE_PREFIXES = (
    "aiza",
    "akia",
    "basic ",
    "bearer ",
    "digest ",
    "gho_",
    "ghp_",
    "github_pat_",
    "pk_live_",
    "rk_live_",
    "sk-",
    "sk_",
    "sq0atp-",
    "xoxb-",
    "xoxp-",
    "ya29.",
)


class FrozenDict(dict[str, Any]):
    """A JSON-serializable mapping that rejects mutation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if getattr(self, "_initialized", False):
            raise TypeError("immutable_mapping")
        dict.__init__(self, *args, **kwargs)
        object.__setattr__(self, "_initialized", True)

    @staticmethod
    def _immutable(*_args: Any, **_kwargs: Any) -> None:
        raise TypeError("immutable_mapping")

    __setattr__ = _immutable
    __delattr__ = _immutable
    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable


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
    sanitized, _ = _credential_transform(value)
    return _canonical_value(sanitized)


def validate_credential_safe(value: Any) -> None:
    """Reject credential-bearing content without exposing the rejected value."""
    _, changed = _credential_transform(value)
    if changed:
        raise ValueError("catalog_credentials_unsafe")


def deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return FrozenDict({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, list):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    return value


def build_source_id(connector_id: str, source_uri: str, source_hash: str) -> str:
    identity = canonical_json([connector_id.casefold(), source_uri, source_hash])
    return "src_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def build_action_id(action: Any) -> str:
    identity = canonical_json(
        [
            action.connector_id.casefold(),
            _enum_value(action.method),
            action.path_template,
            action.operation_id,
            action.variant_id,
        ]
    )
    return "act_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def build_version_id(action: Any) -> str:
    data = action.model_dump(mode="json", exclude={"version_id"})
    return "av_" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


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
    if isinstance(value, (set, frozenset)):
        canonical_items = [_canonical_value(item) for item in value]
        return sorted(canonical_items, key=canonical_json)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError("unsupported_canonical_value")


def _credential_transform(value: Any, *, force_redaction: bool = False) -> tuple[Any, bool]:
    if force_redaction and not _is_collection(value):
        if value is None or value == "" or value == _REDACTED:
            return value, False
        return _REDACTED, True
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        changed = False
        sensitive_header = _mapping_identifies_sensitive_header(value)
        schema_context = force_redaction and _looks_like_schema(value)
        sensitive_context = force_redaction and not schema_context
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            normalized_key = _normalized_name(key)
            redact_item = (
                _is_sensitive_key(normalized_key)
                or (sensitive_header and normalized_key in _HEADER_VALUE_FIELDS)
                or (
                    sensitive_context
                    and not normalized_key.endswith(_METADATA_KEY_SUFFIXES)
                )
                or (schema_context and normalized_key in _HEADER_VALUE_FIELDS)
            )
            sanitized_item, item_changed = _credential_transform(
                item,
                force_redaction=redact_item,
            )
            sanitized[key] = sanitized_item
            changed = changed or item_changed
        return sanitized, changed
    if isinstance(value, (set, frozenset)):
        transformed = [
            _credential_transform(item, force_redaction=force_redaction) for item in value
        ]
        return {item for item, _ in transformed}, any(changed for _, changed in transformed)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        transformed = [
            _credential_transform(item, force_redaction=force_redaction) for item in value
        ]
        return [item for item, _ in transformed], any(changed for _, changed in transformed)
    if isinstance(value, str):
        return _sanitize_string(value)
    return value, False


def _sanitize_string(value: str) -> tuple[str, bool]:
    if value == _REDACTED:
        return value, False
    if value.lstrip().casefold().startswith(_SENSITIVE_VALUE_PREFIXES):
        return _REDACTED, True
    if "://" not in value:
        return value, False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value, False

    changed = False
    netloc = parsed.netloc
    if "@" in netloc:
        userinfo, host = netloc.rsplit("@", 1)
        if userinfo != _REDACTED:
            netloc = f"{_REDACTED}@{host}"
            changed = True

    query_items: list[tuple[str, str]] = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        if _is_sensitive_key(_normalized_name(key)) and item != _REDACTED:
            query_items.append((key, _REDACTED))
            changed = True
        else:
            query_items.append((key, item))
    query = urlencode(query_items, doseq=True)
    if not changed:
        return value, False
    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    return sanitized, True


def _mapping_identifies_sensitive_header(value: Mapping[Any, Any]) -> bool:
    for key, item in value.items():
        if (
            isinstance(key, str)
            and _normalized_name(key) in _HEADER_IDENTIFIER_FIELDS
            and isinstance(item, str)
            and _normalized_name(item) in _SENSITIVE_HEADER_NAMES
        ):
            return True
    return False


def _normalized_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _is_sensitive_key(normalized_key: str) -> bool:
    if normalized_key in _SENSITIVE_KEYS:
        return True
    if normalized_key.endswith(_METADATA_KEY_SUFFIXES):
        return False
    return normalized_key.endswith(_SENSITIVE_KEY_SUFFIXES)


def _is_collection(value: Any) -> bool:
    return isinstance(value, Mapping) or (
        isinstance(value, (Sequence, set, frozenset))
        and not isinstance(value, (str, bytes, bytearray))
    )


def _looks_like_schema(value: Mapping[Any, Any]) -> bool:
    keys = {
        _normalized_name(key)
        for key in value
        if isinstance(key, str)
    }
    if keys.intersection({"properties", "ref", "required", "schema"}):
        return True
    return "type" in keys and bool(
        keys.intersection(
            {
                "bearerformat",
                "description",
                "enum",
                "flows",
                "format",
                "in",
                "items",
                "name",
                "scheme",
            }
        )
    )


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
