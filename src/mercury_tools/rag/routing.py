"""Deterministic ERP routing for Mercury knowledge retrieval."""

from __future__ import annotations

import re
from typing import Any

CONNECTOR_PATTERNS: dict[str, tuple[str, ...]] = {
    "flowaccount": (r"\bflow\s*account\b",),
    "peak": (r"\bpeak\s*accounting\b", r"\bpeak\b"),
    "express": (r"\bexpress\s*account\b",),
}


def infer_connector_id(query: str) -> str | None:
    """Infer one connector only when an explicit, unambiguous alias is present."""
    text = str(query or "").casefold()
    matches = {
        connector_id
        for connector_id, patterns in CONNECTOR_PATTERNS.items()
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def apply_connector_routing(
    query: str,
    filters: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Apply explicit filters first, then deterministic connector inference."""
    applied = dict(filters or {})
    if applied.get("connector"):
        return applied, None
    inferred = infer_connector_id(query)
    if inferred:
        applied["connector"] = inferred
    return applied, inferred
