"""Redaction for MCP and RAG outputs."""

from __future__ import annotations

import json
import re
from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any
from urllib.parse import quote, quote_plus, unquote, unquote_plus, urlsplit

TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk-[a-z0-9_-]{12,}|gho_[a-z0-9_]{12,}|"
    r"eyj[a-z0-9_-]{20,}|mc_[a-z0-9_-]{20,}\.[a-z0-9_-]{20,}|"
    r"sb_secret_[a-z0-9_-]{4,})\b"
)
KEY_VALUE_RE = re.compile(
    r"(?i)\b((?:[a-z0-9]+[_-])*(?:api[_-]?key|client[_-]?secret|"
    r"service[_-]?role[_-]?key|private[_-]?key|secret[_-]?key|"
    r"access[_-]?token|refresh[_-]?token|password|credential|secret|token))"
    r"\s*[:=]\s*([^\s,;]+)"
)
AUTH_HEADER_RE = re.compile(
    r"(?i)\b(authorization|proxy[-_]?authorization)\s*[:=]\s*"
    r"(?:(bearer|basic)\s+)?([^\s,;]+)"
)
COOKIE_HEADER_RE = re.compile(
    r"(?im)\b(cookie|set[-_]?cookie)\s*[:=]\s*([^\r\n]*)"
)
GENERIC_BEARER_RE = re.compile(r"(?i)\bbearer\s+(?!tokens?\b)([^\s,;]+)")
SENSITIVE_KEY_RE = re.compile(
    r"(?i)(?:secret|token|api[_-]?key|password|"
    r"service[_-]?role[_-]?key|private[_-]?key)"
)
SENSITIVE_REPRESENTATION_KEY_RE = re.compile(
    r"(?i)(?:credential|secret|token|api[_-]?key|password|"
    r"service[_-]?role[_-]?key|private[_-]?key)"
)
SENSITIVE_HEADER_KEY_RE = re.compile(
    r"(?i)^(?:authorization|proxy[-_]?authorization|cookie|set[-_]?cookie)$"
)
POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9:/])/(?:Users|Volumes|app|data|etc|home|mnt|opt|private|"
    r"root|run|srv|tmp|usr|var|workspace)(?:/[^\s,;\"'<>]+)+"
)
WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Z]:[\\/][^\s,;\"'<>]+"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
THAI_TAX_ID_RE = re.compile(r"\b\d{13}\b")
SAFE_SECRET_SCHEMA_KEYS = {"required_secret_fields", "token_url"}
_REDACTED = "[REDACTED]"
_MAX_REVERSIBLE_TRANSFORM_DEPTH = 8
_MAX_REPRESENTATIONS = 512
_MAX_REPRESENTATION_BYTES = 4096
_MAX_CREDENTIAL_INPUTS = 16
_REDACTED_PATH = "[REDACTED_PATH]"
_MAX_PATH_DECODE_DEPTH = 3
_MAX_ENCODED_PATH_TOKEN_BYTES = 4096
_PATH_TOKEN_RE = re.compile(r"\S+")
_PERCENT_ESCAPE_RE = re.compile(r"(?i)%[0-9a-f]{2}")
_LOCAL_PATH_ROOT_RE = re.compile(
    r"(?i)^/(?:Users|Volumes|app|data|etc|home|mnt|opt|private|root|run|srv|"
    r"tmp|usr|var|workspace)(?:/|$)"
)
_WINDOWS_DRIVE_RE = re.compile(r"(?i)^[A-Z]:[\\/]")
_AMBIGUOUS_ENCODED_LOCAL_PREFIX_RE = re.compile(
    r"(?i)^(?:(?:%(?:25){0,8}2f){1,2}|(?:%(?:25){0,8}5c){2}|"
    r"file%(?:25){0,8}3a|[A-Z]%(?:25){0,8}3a)"
)
_TOKEN_WRAPPERS = "\"'()[]{}<>,.;"
_HEADER_DESCRIPTOR_FIELDS = {
    "current",
    "currentvalue",
    "default",
    "example",
    "examples",
    "secret",
    "value",
    "values",
}


def redact_credential_text(value: str, credentials: Sequence[str]) -> str:
    """Fail closed when text contains a reversible representation of a credential."""

    text = str(value)
    if not _within_representation_limit(text):
        return _REDACTED
    representations = _credential_representations(text, credentials)
    candidate_values = _reversibly_decoded_values((text,))
    if (
        representations is None
        or candidate_values is None
        or any(
            representation in candidate
            for candidate in candidate_values
            for representation in representations
        )
    ):
        return _REDACTED
    return redact_text(text)


def redact_text(value: str) -> str:
    text = _PATH_TOKEN_RE.sub(_redact_sensitive_representation, str(value))
    return _redact_plain_text(text)


def _redact_plain_text(value: str) -> str:
    text = value
    text = AUTH_HEADER_RE.sub(_redact_auth_header, text)
    text = COOKIE_HEADER_RE.sub(_redact_cookie_header, text)
    text = GENERIC_BEARER_RE.sub(_redact_generic_bearer, text)
    text = KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = THAI_TAX_ID_RE.sub("[REDACTED_TAX_ID]", text)
    return text


def redact_absolute_paths(value: str) -> str:
    return _PATH_TOKEN_RE.sub(_redact_path_token, str(value))


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, Mapping):
        sensitive_descriptor = _sensitive_header_descriptor(value)
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized_key = _normalized_key(key_text)
            if sensitive_descriptor and normalized_key in _HEADER_DESCRIPTOR_FIELDS:
                redacted[key] = (
                    redact_text(item)
                    if isinstance(item, str) and _is_documented_placeholder_value(item)
                    else _REDACTED
                )
            elif (
                key_text not in SAFE_SECRET_SCHEMA_KEYS
                and (
                    SENSITIVE_KEY_RE.search(key_text)
                    or SENSITIVE_HEADER_KEY_RE.fullmatch(key_text)
                )
            ):
                redacted[key] = (
                    redact_text(item)
                    if isinstance(item, str) and _is_documented_placeholder_value(item)
                    else "[REDACTED]"
                )
            else:
                redacted[key] = redact_json(item)
        return redacted
    return value


def _redact_path_token(match: re.Match[str]) -> str:
    token = match.group(0)
    if _is_safe_public_uri(token):
        return token
    if _is_local_path_representation(token):
        return _REDACTED_PATH
    return token


def _is_local_path_representation(value: str) -> bool:
    candidate = value.strip(_TOKEN_WRAPPERS)
    if _is_safe_public_uri(candidate):
        return False
    if not _within_path_token_limit(value):
        return bool(_PERCENT_ESCAPE_RE.search(candidate) or _is_local_path(candidate))
    decoded = candidate
    for _ in range(_MAX_PATH_DECODE_DEPTH):
        if _is_local_path(decoded):
            return True
        if not _PERCENT_ESCAPE_RE.search(decoded):
            return False
        next_value = unquote(decoded)
        if not _within_path_token_limit(next_value):
            return True
        if next_value == decoded:
            return False
        decoded = next_value
    return bool(
        _is_local_path(decoded)
        or _AMBIGUOUS_ENCODED_LOCAL_PREFIX_RE.search(decoded)
    )


def _is_local_path(value: str) -> bool:
    candidate = value.strip(_TOKEN_WRAPPERS)
    folded = candidate.casefold()
    return bool(
        folded.startswith("file:/")
        or candidate.startswith(("//", "\\\\"))
        or _WINDOWS_DRIVE_RE.match(candidate)
        or _LOCAL_PATH_ROOT_RE.match(candidate)
    )


def _within_path_token_limit(value: str) -> bool:
    try:
        return len(value.encode("utf-8")) <= _MAX_ENCODED_PATH_TOKEN_BYTES
    except UnicodeError:
        return False


def _is_safe_public_uri(value: str) -> bool:
    candidate = value.strip("\"'()[]{}<>,.;")
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return False
    scheme = parsed.scheme.casefold()
    if scheme in {"http", "https"}:
        return bool(
            parsed.netloc
            and parsed.username is None
            and parsed.password is None
        )
    return scheme == "mercury" and bool(parsed.netloc) and "\\" not in candidate


def _redact_sensitive_representation(match: re.Match[str]) -> str:
    token = match.group(0)
    if not _PERCENT_ESCAPE_RE.search(token):
        return token
    if not _within_representation_limit(token):
        return _REDACTED
    decoded = token
    for _ in range(2):
        candidate = unquote(decoded)
        if not _within_representation_limit(candidate):
            return _REDACTED
        if candidate == decoded:
            break
        if _is_local_path_representation(candidate):
            return _REDACTED_PATH
        if _plain_text_is_sensitive(candidate):
            return _REDACTED
        decoded = candidate
    return token


def _plain_text_is_sensitive(value: str) -> bool:
    if _redact_plain_text(value) != value:
        return True
    with suppress(json.JSONDecodeError, RecursionError):
        return _structured_value_is_sensitive(json.loads(value))
    return False


def _structured_value_is_sensitive(value: Any) -> bool:
    if isinstance(value, Mapping):
        if _sensitive_header_descriptor(value):
            return True
        return any(
            (
                str(key) not in SAFE_SECRET_SCHEMA_KEYS
                and (
                    SENSITIVE_REPRESENTATION_KEY_RE.search(str(key))
                    or SENSITIVE_HEADER_KEY_RE.fullmatch(str(key))
                )
            )
            or _structured_value_is_sensitive(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_structured_value_is_sensitive(item) for item in value)
    return isinstance(value, str) and _redact_plain_text(value) != value


def _sensitive_header_descriptor(value: Mapping[Any, Any]) -> bool:
    for key, item in value.items():
        if _normalized_key(str(key)) not in {"header", "headername", "key", "name"}:
            continue
        if not isinstance(item, str):
            continue
        decoded = item
        for _ in range(2):
            decoded = unquote(decoded)
        if SENSITIVE_HEADER_KEY_RE.fullmatch(decoded.strip()):
            return True
    return False


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _redact_auth_header(match: re.Match[str]) -> str:
    secret = match.group(3)
    if _is_documented_placeholder(secret):
        return match.group(0)
    return f"{match.group(1)}=[REDACTED]"


def _redact_cookie_header(match: re.Match[str]) -> str:
    value = match.group(2)
    if _is_documented_placeholder_value(value):
        return match.group(0)
    return f"{match.group(1)}=[REDACTED]"


def _redact_generic_bearer(match: re.Match[str]) -> str:
    if _is_documented_placeholder(match.group(1)):
        return match.group(0)
    return "[REDACTED_TOKEN]"


def _is_documented_placeholder_value(value: str) -> bool:
    candidate = value.strip()
    if _is_documented_placeholder(candidate):
        return True
    scheme, separator, credential = candidate.partition(" ")
    if (
        separator
        and scheme.casefold() in {"bearer", "basic"}
        and _is_documented_placeholder(credential)
    ):
        return True
    _, separator, assigned = candidate.partition("=")
    return bool(separator and _is_documented_placeholder(assigned))


def _is_documented_placeholder(value: str) -> bool:
    candidate = value.strip("\"'")
    return bool(
        re.fullmatch(
            r"(?:<[^<>\s]+>|\{[^{}\s]+\}|\$\{?[A-Z][A-Z0-9_]*\}?|"
            r"\[REDACTED(?:_TOKEN)?\])",
            candidate,
        )
    )


def _credential_representations(
    text: str,
    credentials: Sequence[str],
) -> tuple[str, ...] | None:
    values = _credential_values(credentials)
    if values is None:
        return None
    decoded_values = _reversibly_decoded_values(values)
    if decoded_values is None:
        return None
    return _reversibly_encoded_values(decoded_values, text_length=len(text))


def _credential_values(credentials: Sequence[str]) -> tuple[str, ...] | None:
    values: list[str] = []
    for index, value in enumerate(credentials):
        if index >= _MAX_CREDENTIAL_INPUTS:
            return None
        if not isinstance(value, str) or not value:
            continue
        if not _within_representation_limit(value):
            return None
        if value not in values:
            values.append(value)

    sensitive_values = list(values)
    for first in values:
        for second in values:
            pair = f"{first}:{second}"
            if not _within_representation_limit(pair):
                return None
            if pair not in sensitive_values:
                if len(sensitive_values) >= _MAX_REPRESENTATIONS:
                    return None
                sensitive_values.append(pair)
    return tuple(sensitive_values)


def _reversibly_decoded_values(values: Sequence[str]) -> tuple[str, ...] | None:
    known = set(values)
    frontier = set(values)
    for _ in range(_MAX_REVERSIBLE_TRANSFORM_DEPTH):
        next_frontier = _add_representations(
            known,
            frontier,
            _reversible_decodings,
            text_length=None,
        )
        if next_frontier is None:
            return None
        if not next_frontier:
            return tuple(known)
        frontier = next_frontier
    if _has_new_representations(known, frontier, _reversible_decodings, None):
        return None
    return tuple(known)


def _reversibly_encoded_values(
    values: Sequence[str],
    *,
    text_length: int,
) -> tuple[str, ...] | None:
    known = {value for value in values if len(value) <= text_length}
    frontier = set(known)
    for _ in range(_MAX_REVERSIBLE_TRANSFORM_DEPTH):
        next_frontier = _add_representations(
            known,
            frontier,
            _reversible_encodings,
            text_length=text_length,
        )
        if next_frontier is None:
            return None
        if not next_frontier:
            return tuple(known)
        frontier = next_frontier
    return (
        None
        if _has_new_representations(known, frontier, _reversible_encodings, text_length)
        else tuple(known)
    )


def _add_representations(
    known: set[str],
    frontier: set[str],
    transform: Any,
    *,
    text_length: int | None,
) -> set[str] | None:
    next_frontier: set[str] = set()
    for value in frontier:
        representations = transform(value)
        if representations is None:
            return None
        for representation in representations:
            if representation in known:
                continue
            if not _within_representation_limit(representation):
                return None
            if text_length is not None and len(representation) > text_length:
                continue
            if len(known) >= _MAX_REPRESENTATIONS:
                return None
            known.add(representation)
            next_frontier.add(representation)
    return next_frontier


def _has_new_representations(
    known: set[str],
    frontier: set[str],
    transform: Any,
    text_length: int | None,
) -> bool:
    for value in frontier:
        representations = transform(value)
        if representations is None:
            return True
        for representation in representations:
            if not _within_representation_limit(representation):
                return True
            if (text_length is None or len(representation) <= text_length) and (
                representation not in known
            ):
                return True
    return False


def _reversible_decodings(value: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (unquote(value), unquote_plus(value), *_base64_decodings(value))
        )
    )


def _reversible_encodings(value: str) -> tuple[str, ...] | None:
    try:
        representations = (
            quote(value, safe=""),
            quote_plus(value, safe=""),
            *_base64_encodings(value),
        )
    except UnicodeError:
        return None
    return tuple(dict.fromkeys(representations))


def _within_representation_limit(value: str) -> bool:
    try:
        return len(value.encode("utf-8")) <= _MAX_REPRESENTATION_BYTES
    except UnicodeError:
        return False


def _base64_encodings(value: str) -> tuple[str, ...]:
    encoded = value.encode("utf-8")
    standard = b64encode(encoded).decode("ascii")
    urlsafe = urlsafe_b64encode(encoded).decode("ascii")
    return tuple(dict.fromkeys((standard, standard.rstrip("="), urlsafe, urlsafe.rstrip("="))))


def _base64_decodings(value: str) -> tuple[str, ...]:
    if not value or len(value) % 4 == 1:
        return ()
    padded = value + "=" * (-len(value) % 4)
    decoded: list[str] = []
    with suppress(UnicodeDecodeError, ValueError):
        decoded.append(b64decode(padded, validate=True).decode("utf-8"))
    with suppress(UnicodeDecodeError, ValueError):
        decoded.append(urlsafe_b64decode(padded).decode("utf-8"))
    return tuple(dict.fromkeys(decoded))
