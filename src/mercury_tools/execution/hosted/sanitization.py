"""Server-owned text projections for hosted preview surfaces."""

from __future__ import annotations

import re
import unicodedata

from mercury_tools.safety.redaction import redact_text

_THAI_TAX_IDENTIFIER = re.compile(r"(?<!\d)(?:\d[- ]?){13}(?!\d)")
_PHONE_IDENTIFIER = re.compile(
    r"(?<!\d)(?:\+?66[- ]?(?:[689]\d|[2-7])[- ]?\d{3}[- ]?\d{4}"
    r"|0(?:[689]\d|[2-7])[- ]?\d{3}[- ]?\d{4})(?!\d)"
)
_REDACTION_MARKER = re.compile(r"\[REDACTED(?:_[A-Z]+)?\]")


def sanitize_public_text(value: str, *, code: str) -> str:
    """Project a typed display string without personal or credential-like values."""

    if (
        not isinstance(value, str)
        or not value
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise ValueError(code)
    projected = redact_text(value)
    projected = _THAI_TAX_IDENTIFIER.sub("[REDACTED_TAX_ID]", projected)
    return _PHONE_IDENTIFIER.sub("[REDACTED_PHONE]", projected)


def require_safe_public_identifier(value: str, *, code: str) -> str:
    """Reject caller identifiers that would need a public redaction."""

    projected = sanitize_public_text(value, code=code)
    if projected != value or _REDACTION_MARKER.search(projected) is not None:
        raise ValueError(code)
    return value
