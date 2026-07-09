"""Redaction for MCP and RAG outputs."""

from __future__ import annotations

import re
from typing import Any

TOKEN_RE = re.compile(
    r"(?i)\b(?:bearer\s+)?(?:sk-[a-z0-9_-]{12,}|gho_[a-z0-9_]{12,}|eyj[a-z0-9_-]{20,}|mc_[a-z0-9_-]{20,}\.[a-z0-9_-]{20,})\b"
)
KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|password)\s*[:=]\s*([^\s,;]+)"
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
THAI_TAX_ID_RE = re.compile(r"\b\d{13}\b")


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
            if re.search(r"(?i)(secret|token|api[_-]?key|password)", key_text):
                redacted[key] = "[REDACTED]"
            else:
                redacted[key] = redact_json(item)
        return redacted
    return value
