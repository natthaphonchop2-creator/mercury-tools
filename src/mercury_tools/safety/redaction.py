"""Redaction for MCP and RAG outputs."""

from __future__ import annotations

import re
from base64 import b64decode, b64encode, urlsafe_b64decode, urlsafe_b64encode
from collections.abc import Sequence
from contextlib import suppress
from typing import Any
from urllib.parse import unquote, unquote_plus

TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk-[a-z0-9_-]{12,}|gho_[a-z0-9_]{12,}|eyj[a-z0-9_-]{20,}|mc_[a-z0-9_-]{20,}\.[a-z0-9_-]{20,})\b"
)
KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*([^\s,;]+)"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
THAI_TAX_ID_RE = re.compile(r"\b\d{13}\b")
SAFE_SECRET_SCHEMA_KEYS = {"required_secret_fields", "token_url"}
_MAX_REVERSIBLE_DECODING_DEPTH = 8


def redact_credential_text(value: str, credentials: Sequence[str]) -> str:
    """Fail closed when text contains a reversible representation of a credential."""

    text = str(value)
    sensitive_values = _credential_values(credentials)
    if _contains_reversible_credential(text, sensitive_values):
        return "[REDACTED]"
    return redact_text(text)


def redact_text(value: str) -> str:
    text = str(value)
    text = KEY_VALUE_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text)
    text = TOKEN_RE.sub("[REDACTED_TOKEN]", text)
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = THAI_TAX_ID_RE.sub("[REDACTED_TAX_ID]", text)
    return text


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if (
                key_text not in SAFE_SECRET_SCHEMA_KEYS
                and re.search(r"(?i)(secret|token|api[_-]?key|password)", key_text)
            ):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_json(item)
        return redacted
    return value


def _credential_values(credentials: Sequence[str]) -> tuple[str, ...]:
    values = tuple(
        dict.fromkeys(
            value for value in credentials if isinstance(value, str) and value
        )
    )
    pairs = tuple(
        f"{first}:{second}"
        for first in values
        for second in values
    )
    return tuple(dict.fromkeys((*values, *pairs)))


def _contains_reversible_credential(text: str, credentials: Sequence[str]) -> bool:
    if not credentials:
        return False

    sensitive_values = set(credentials)
    for credential in tuple(sensitive_values):
        sensitive_values.update(_recursive_url_decodings(credential))
        sensitive_values.update(_recursive_base64_decodings(credential))

    needles = set(sensitive_values)
    for credential in sensitive_values:
        needles.update(_base64_encodings(credential))

    for candidate in _recursive_url_decodings(text):
        if any(needle in candidate for needle in needles):
            return True
    return False


def _recursive_url_decodings(value: str) -> tuple[str, ...]:
    decoded = {value}
    frontier = {value}
    for _ in range(_MAX_REVERSIBLE_DECODING_DEPTH):
        next_frontier = {
            result
            for item in frontier
            for result in (unquote(item), unquote_plus(item))
            if result not in decoded
        }
        if not next_frontier:
            break
        decoded.update(next_frontier)
        frontier = next_frontier
    return tuple(decoded)


def _recursive_base64_decodings(value: str) -> tuple[str, ...]:
    decoded: set[str] = set()
    frontier = {value}
    for _ in range(_MAX_REVERSIBLE_DECODING_DEPTH):
        next_frontier = {
            result
            for item in frontier
            for result in _base64_decodings(item)
            if result not in decoded
        }
        if not next_frontier:
            break
        decoded.update(next_frontier)
        frontier = next_frontier
    return tuple(decoded)


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
