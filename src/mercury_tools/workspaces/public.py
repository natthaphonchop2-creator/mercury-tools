from __future__ import annotations

import hashlib
import re
import secrets
import time
from typing import Any

from mercury_tools.product import ConnectRequest

PUBLIC_WORKSPACE_RE = re.compile(r"^mw_[A-Za-z0-9_-]{20,80}$")
PUBLIC_WORKSPACE_TTL_SECONDS = 60 * 60 * 24 * 365 * 10


def new_public_workspace_id() -> str:
    return "mw_" + secrets.token_urlsafe(18)


def normalize_public_workspace_id(value: str) -> str:
    normalized = value.strip()
    if not PUBLIC_WORKSPACE_RE.fullmatch(normalized):
        raise ValueError("Invalid Mercury public workspace ID.")
    return normalized


def public_workspace_token_payload(workspace_id: str) -> dict[str, Any]:
    normalized = normalize_public_workspace_id(workspace_id)
    now = int(time.time())
    subject_hash = hashlib.sha256(normalized.encode()).hexdigest()[:20]
    return {
        "sub": f"public-{subject_hash}@workspace.invalid",
        "company": normalized,
        "host_app": "generic",
        "iat": now,
        "exp": now + PUBLIC_WORKSPACE_TTL_SECONDS,
        "jti": normalized,
        "scope": ["public:contest"],
    }


def public_workspace_connect_request(
    workspace_id: str,
    company_name: str | None,
) -> ConnectRequest:
    payload = public_workspace_token_payload(workspace_id)
    company = (company_name or "").strip() or "Mercury Public Workspace"
    return ConnectRequest(
        email=payload["sub"],
        company=company,
        host_app="generic",
        invite_code="",
    )
