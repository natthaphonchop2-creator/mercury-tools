"""Mercury Connect product-layer helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from mercury_tools.config import Settings

DEFAULT_TOKEN_TTL_SECONDS = 60 * 60 * 24 * 30
TOKEN_PREFIX = "mc_"


@dataclass(frozen=True)
class ConnectRequest:
    email: str
    company: str
    host_app: str
    invite_code: str


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(message: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return _b64url_encode(digest)


def normalize_host_app(value: str) -> str:
    host = value.strip().lower()
    aliases = {
        "claude desktop": "claude",
        "claude-desktop": "claude",
        "vscode": "vs-code",
        "visual studio code": "vs-code",
    }
    host = aliases.get(host, host)
    if host not in {"codex", "cursor", "claude", "vs-code", "generic"}:
        return "generic"
    return host


def create_client_token(
    settings: Settings,
    request: ConnectRequest,
    *,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
) -> str:
    if not settings.connect_signing_secret:
        raise RuntimeError("MERCURY_CONNECT_SIGNING_SECRET is required to issue client tokens.")

    now = int(time.time()) if now is None else now
    payload = {
        "iss": "mercury-tools",
        "sub": request.email.strip().lower(),
        "company": request.company.strip(),
        "host_app": normalize_host_app(request.host_app),
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": secrets.token_urlsafe(12),
        "scope": ["mcp:read", "rag:read", "skills:read", "flows:read", "flows:run"],
    }
    body = _b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _sign(body, settings.connect_signing_secret)
    return f"{TOKEN_PREFIX}{body}.{signature}"


def verify_client_token(
    settings: Settings,
    token: str,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    if not settings.connect_signing_secret:
        raise ValueError("client token signing is not configured")
    if not token.startswith(TOKEN_PREFIX):
        raise ValueError("not a Mercury client token")
    try:
        body, signature = token[len(TOKEN_PREFIX) :].split(".", 1)
    except ValueError as exc:
        raise ValueError("invalid Mercury client token") from exc

    expected = _sign(body, settings.connect_signing_secret)
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid Mercury client token signature")

    payload = json.loads(_b64url_decode(body))
    now = int(time.time()) if now is None else now
    if int(payload.get("exp") or 0) < now:
        raise ValueError("Mercury client token has expired")
    return payload


def is_authorized_bearer(settings: Settings, authorization_header: str | None) -> bool:
    if not authorization_header or not authorization_header.startswith("Bearer "):
        return False
    token = authorization_header.removeprefix("Bearer ").strip()
    if settings.http_bearer_token and hmac.compare_digest(token, settings.http_bearer_token):
        return True
    try:
        verify_client_token(settings, token)
    except ValueError:
        return False
    return True


def build_connection_payload(
    *,
    public_base_url: str,
    mcp_path: str,
    token: str,
    email: str,
    company: str,
    host_app: str,
) -> dict[str, Any]:
    endpoint = f"{public_base_url.rstrip('/')}{mcp_path}"
    server_name = "mercury-tools"
    env_name = "MERCURY_TOOLS_MCP_TOKEN"
    codex_command = (
        f"export {env_name}='{token}'\n"
        f"codex mcp add {server_name} --url {endpoint} --bearer-token-env-var {env_name}"
    )
    remote_config = {
        "mcpServers": {
            server_name: {
                "url": endpoint,
                "headers": {"Authorization": f"Bearer ${{{env_name}}}"},
            }
        }
    }
    return {
        "status": "ok",
        "endpoint": endpoint,
        "token": token,
        "expires_in_days": DEFAULT_TOKEN_TTL_SECONDS // 86400,
        "workspace": {
            "email": email,
            "company": company,
            "host_app": normalize_host_app(host_app),
        },
        "codex": {
            "env_var": env_name,
            "command": codex_command,
        },
        "cursor": {
            "config": remote_config,
            "note": f"Add this MCP server in Cursor settings and set {env_name} locally.",
        },
        "claude": {
            "config": remote_config,
            "note": (
                "Use an MCP client that supports remote Streamable HTTP servers "
                "and auth headers."
            ),
        },
    }


def validate_connect_request(settings: Settings, data: dict[str, Any]) -> ConnectRequest:
    request = ConnectRequest(
        email=str(data.get("email") or "").strip(),
        company=str(data.get("company") or "").strip(),
        host_app=str(data.get("host_app") or "codex").strip(),
        invite_code=str(data.get("invite_code") or "").strip(),
    )
    if not request.email or "@" not in request.email:
        raise ValueError("Valid email is required.")
    if not request.company:
        raise ValueError("Company is required.")
    if not settings.connect_invite_code:
        raise ValueError("Mercury Connect invite code is not configured.")
    if not hmac.compare_digest(request.invite_code, settings.connect_invite_code):
        raise PermissionError("Invalid invite code.")
    return request
