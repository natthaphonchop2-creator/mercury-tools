import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

_REDACTED = "[REDACTED]"
_CREDENTIAL_PATH_UNSAFE = "catalog_credential_path_unsafe"
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
    "cookie",
    "clientsecret",
    "proxyauthorization",
    "setcookie",
    "xaccesstoken",
    "xapikey",
    "xauthtoken",
    "xclientsecret",
    "xamzsecuritytoken",
}
_SAFE_PARAMETER_METADATA_KEYS = {
    "keyname",
    "headername",
    "parametername",
    "clientidname",
    "clientsecretname",
}
_CREDENTIAL_CONTAINER_NAMES = {
    "auth",
    "authentication",
    "credential",
    "credentials",
    "oauth",
    "oauth2",
    "secret",
    "secrets",
    "security",
    "securityscheme",
    "securityschemes",
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
_PATH_ASSIGNMENT = re.compile(
    r"^(?P<key>[A-Za-z0-9_-]+)[=:](?P<value>.+)$"
)
_SENSITIVE_PATH_PREFIX_VALUE = re.compile(
    r"(?i)^(?:access[_-]?token|api[_-]?(?:key|secret)|auth[_-]?token|"
    r"client[_-]?secret|consumer[_-]?secret|id[_-]?token|oauth[_-]?token|"
    r"refresh[_-]?token|signed[_-]?token)-(?=.+)"
)
_JWT_PATH_SEGMENT = re.compile(
    r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{4,}$"
)
_PATH_VALUE_FIELDS = {"endpoint", "path", "pathtemplate", "route", "sourceuri", "url"}


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


def validate_credential_safe_paths(value: Any) -> None:
    """Reject credential material in endpoint paths without retaining the value."""
    _inspect_credential_paths(value)


def validate_credential_safe_path(value: str) -> None:
    """Reject one credential-bearing URI or endpoint path with a constant error."""
    if not isinstance(value, str):
        return
    path = _decode_path_value(_path_component(value))
    for segment in path.split("/"):
        if _path_segment_has_credential(segment):
            raise ValueError(_CREDENTIAL_PATH_UNSAFE)


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


def _inspect_credential_paths(
    value: Any,
    *,
    parent_key: str = "",
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                continue
            decoded_field_key = _decode_field_name(key)
            normalized_key = _normalized_name(decoded_field_key)
            decoded_key = _structural_path_key(key)
            if decoded_key is not None:
                validate_credential_safe_path(decoded_key)
            if normalized_key in _PATH_VALUE_FIELDS or (
                normalized_key == "raw" and _normalized_name(parent_key) == "url"
            ):
                _inspect_path_value(item)
            _inspect_credential_paths(
                item,
                parent_key=decoded_field_key,
            )
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _inspect_credential_paths(item, parent_key=parent_key)


def _inspect_path_value(value: Any) -> None:
    if isinstance(value, str):
        validate_credential_safe_path(value)
    elif (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and all(isinstance(item, str) for item in value)
    ):
        validate_credential_safe_path("/" + "/".join(value))


def _path_component(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value.split("?", 1)[0].split("#", 1)[0]
    return parsed.path


def _decode_path_value(value: str) -> str:
    """Decode percent-encoded path material until it is stable within a finite bound."""
    decoded = value
    for _ in range(len(value) + 1):
        if not _has_valid_percent_encoding(decoded):
            return decoded
        next_decoded = _decode_percent_encoded_utf8(decoded)
        if next_decoded is None:
            raise ValueError(_CREDENTIAL_PATH_UNSAFE)
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    raise ValueError(_CREDENTIAL_PATH_UNSAFE)


def _decode_field_name(value: str) -> str:
    """Decode field names for classification without rejecting unrelated metadata."""
    decoded = value
    for _ in range(len(value) + 1):
        if "%" not in decoded:
            return decoded
        if not _has_only_valid_percent_encoding(decoded):
            return value
        next_decoded = _decode_percent_encoded_utf8(decoded)
        if next_decoded is None:
            return value
        if next_decoded == decoded:
            return decoded
        decoded = next_decoded
    return value


def _structural_path_key(key: str) -> str | None:
    decoded = key
    for _ in range(len(key) + 1):
        if decoded.lstrip().startswith("/"):
            return decoded
        if not _has_valid_percent_encoding(decoded):
            return None
        next_decoded = _decode_percent_encoded_utf8(decoded)
        if next_decoded is None or next_decoded == decoded:
            return None
        decoded = next_decoded
    return None


def _decode_percent_encoded_utf8(value: str) -> str | None:
    try:
        return unquote(value, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _has_valid_percent_encoding(value: str) -> bool:
    return any(
        character == "%"
        and index + 2 < len(value)
        and all(digit in "0123456789abcdefABCDEF" for digit in value[index + 1 : index + 3])
        for index, character in enumerate(value)
    )


def _has_only_valid_percent_encoding(value: str) -> bool:
    return all(
        character != "%"
        or (
            index + 2 < len(value)
            and all(digit in "0123456789abcdefABCDEF" for digit in value[index + 1 : index + 3])
        )
        for index, character in enumerate(value)
    )


def _path_segment_has_credential(segment: str) -> bool:
    candidate = segment.strip()
    if not candidate or (candidate.startswith("{") and candidate.endswith("}")):
        return False
    folded = candidate.casefold()
    if folded.startswith(_SENSITIVE_VALUE_PREFIXES):
        return True
    assignment = _PATH_ASSIGNMENT.fullmatch(candidate)
    sensitive_assignment = bool(
        assignment and _is_sensitive_key(_normalized_name(assignment.group("key")))
    )
    return bool(
        sensitive_assignment
        or _SENSITIVE_PATH_PREFIX_VALUE.match(candidate)
        or _JWT_PATH_SEGMENT.fullmatch(candidate)
    )


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
        sensitive_context = force_redaction
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("unsupported_canonical_key")
            normalized_key = _normalized_name(key)
            redact_item = (
                _is_sensitive_key(normalized_key)
                or _is_credential_container_key(normalized_key)
                or _is_sensitive_header_name(normalized_key)
                or (sensitive_header and normalized_key in _HEADER_VALUE_FIELDS)
                or (
                    sensitive_context
                    and not _is_safe_metadata_key(normalized_key, item)
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
    if "?" not in value and "://" not in value and not value.startswith("//"):
        return value, False

    try:
        parsed = urlsplit(value)
    except ValueError:
        return _sanitize_malformed_uri(value)

    netloc = parsed.netloc
    changed = False
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
        sanitized = _sanitize_userinfo(value)
        return sanitized, sanitized != value
    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, query, parsed.fragment))
    sanitized = _sanitize_userinfo(sanitized)
    return sanitized, True


def _sanitize_malformed_uri(value: str) -> tuple[str, bool]:
    before_fragment, fragment_separator, fragment = value.partition("#")
    prefix, query_separator, query = before_fragment.partition("?")
    if not query_separator:
        sanitized = _sanitize_userinfo(value)
        return sanitized, sanitized != value

    sanitized_query_parts: list[str] = []
    changed = False
    for part in re.split(r"([&;])", query):
        if part in {"&", ";"}:
            sanitized_query_parts.append(part)
            continue
        key, value_separator, item = part.partition("=")
        if (
            value_separator
            and _is_sensitive_key(_normalized_name(unquote(key)))
            and unquote(item) != _REDACTED
        ):
            sanitized_query_parts.append(f"{key}{value_separator}{_REDACTED}")
            changed = True
        else:
            sanitized_query_parts.append(part)

    sanitized = (
        f"{prefix}{query_separator}{''.join(sanitized_query_parts)}"
        f"{fragment_separator}{fragment}"
    )
    sanitized = _sanitize_userinfo(sanitized)
    return sanitized, changed or sanitized != value


def _sanitize_userinfo(value: str) -> str:
    authority_pattern = re.compile(
        r"(?P<prefix>(?:[a-z][a-z0-9+.-]*:)?//|://)"
        r"(?P<userinfo>[^/?#\s]*@)(?P<host>[^/?#\s]*)",
        re.IGNORECASE,
    )

    def replace_authority(match: re.Match[str]) -> str:
        if match.group("userinfo") == f"{_REDACTED}@":
            return match.group(0)
        return f"{match.group('prefix')}{_REDACTED}@{match.group('host')}"

    sanitized = authority_pattern.sub(replace_authority, value, count=1)
    if sanitized != value:
        return sanitized

    relative_pattern = re.compile(
        r"^(?P<userinfo>[^/?#\s@]+@)(?P<host>[^/?#\s/]+)(?P<path>/[^?#\s]*)?"
        r"(?P<suffix>[?#].*)?$"
    )
    return relative_pattern.sub(
        lambda match: (
            f"{_REDACTED}@{match.group('host')}{match.group('path') or ''}"
            f"{match.group('suffix') or ''}"
        ),
        value,
        count=1,
    )


def _mapping_identifies_sensitive_header(value: Mapping[Any, Any]) -> bool:
    for key, item in value.items():
        if (
            isinstance(key, str)
            and _normalized_name(key) in _HEADER_IDENTIFIER_FIELDS
            and isinstance(item, str)
            and _is_sensitive_header_name(_normalized_name(item))
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


def _is_safe_metadata_key(normalized_key: str, value: Any) -> bool:
    if normalized_key in _SAFE_PARAMETER_METADATA_KEYS:
        return isinstance(value, str)
    return normalized_key.endswith(_METADATA_KEY_SUFFIXES)


def _is_credential_container_key(normalized_key: str) -> bool:
    return normalized_key in _CREDENTIAL_CONTAINER_NAMES or normalized_key.endswith(
        ("authentication", "credentials", "secrets")
    )


def _is_sensitive_header_name(normalized_name: str) -> bool:
    if normalized_name in _SENSITIVE_HEADER_NAMES:
        return True
    return (
        normalized_name.startswith("x")
        and (
            normalized_name.endswith("apikey")
            or normalized_name.endswith("authtoken")
            or normalized_name.endswith("accesstoken")
            or normalized_name.endswith("clientsecret")
            or normalized_name.endswith("securitytoken")
            or normalized_name.endswith("token")
        )
    )


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
