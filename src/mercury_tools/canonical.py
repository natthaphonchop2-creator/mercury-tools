"""Neutral canonical JSON primitives shared by local and hosted code."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_payload_json(payload: Any) -> str:
    """Return deterministic JSON for a JSON-safe value."""

    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload_not_canonicalizable") from exc


def canonical_payload_bytes(payload: Any) -> bytes:
    """Return canonical UTF-8 bytes for hashing or authenticated encryption."""

    return canonical_payload_json(payload).encode("utf-8")


def canonical_payload_hash(payload: Any) -> str:
    """Return the SHA-256 hash of canonical JSON."""

    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()
