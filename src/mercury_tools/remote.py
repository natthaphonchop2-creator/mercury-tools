"""Remote deployment verification helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DEFAULT_RENDER_URL = "https://mercury-tools-mcp.onrender.com"
DEFAULT_TOKEN_FILE = Path.home() / ".mercury-tools" / "render-mcp-token.txt"
MCP_ACCEPT_HEADER = "application/json, text/event-stream"


@dataclass(frozen=True)
class RemoteVerification:
    base_url: str
    health_url: str
    mcp_url: str
    health_status_code: int | None
    health: dict[str, Any] = field(default_factory=dict)
    unauthenticated_mcp_status_code: int | None = None
    authenticated_mcp_status_code: int | None = None
    authenticated_mcp_reachable: bool = False
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.missing and not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "base_url": self.base_url,
            "health_url": self.health_url,
            "mcp_url": self.mcp_url,
            "health_status_code": self.health_status_code,
            "health": self.health,
            "unauthenticated_mcp_status_code": self.unauthenticated_mcp_status_code,
            "authenticated_mcp_status_code": self.authenticated_mcp_status_code,
            "authenticated_mcp_reachable": self.authenticated_mcp_reachable,
            "missing": self.missing,
            "errors": self.errors,
        }


def _join_url(base_url: str, path: str) -> str:
    normalized_path = path if path.startswith("/") else f"/{path}"
    return f"{base_url.rstrip('/')}{normalized_path}"


def read_token(token: str | None = None, token_file: str | Path | None = None) -> str:
    if token:
        return token.strip()
    if token_file is None:
        token_file = DEFAULT_TOKEN_FILE
    path = Path(token_file).expanduser()
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def verify_remote(
    *,
    base_url: str = DEFAULT_RENDER_URL,
    mcp_path: str = "/mcp",
    token: str = "",
    timeout: float = 20,
    client: httpx.Client | None = None,
) -> RemoteVerification:
    base_url = base_url.rstrip("/")
    health_url = _join_url(base_url, "/healthz")
    mcp_url = _join_url(base_url, mcp_path)
    errors: list[str] = []
    missing: list[str] = []
    health: dict[str, Any] = {}
    health_status_code: int | None = None
    unauthenticated_status: int | None = None
    authenticated_status: int | None = None
    authenticated_reachable = False

    owns_client = client is None
    if client is None:
        client = httpx.Client(timeout=timeout)

    try:
        health_response = client.get(health_url)
        health_status_code = health_response.status_code
        if health_response.status_code == 200:
            try:
                parsed = health_response.json()
                if isinstance(parsed, dict):
                    health = parsed
            except ValueError:
                errors.append("healthz returned non-JSON response")
        else:
            errors.append(f"healthz returned HTTP {health_response.status_code}")

        if isinstance(health.get("mcp_path"), str):
            mcp_url = _join_url(base_url, str(health["mcp_path"]))

        if health.get("status") != "ok":
            missing.append("healthy Mercury Tools service")
        if health.get("supabase") is not True:
            missing.append("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on remote service")
        if health.get("embedding_configured") is not True:
            provider = str(health.get("embedding_provider") or "unknown")
            if provider == "openai":
                missing.append("OPENAI_API_KEY on remote service")
            else:
                missing.append(f"configured embedding provider on remote service ({provider})")
        if (
            health.get("http_auth_required") is True
            and health.get("http_auth_configured") is not True
        ):
            missing.append("MERCURY_TOOLS_HTTP_BEARER_TOKEN on remote service")
        if (
            health.get("http_auth_required") is not True
            and health.get("legacy_http_api") != "disabled"
        ):
            missing.append("disabled legacy HTTP API on public remote service")

        if health.get("http_auth_required") is True:
            unauthenticated_response = client.get(
                mcp_url,
                headers={"Accept": MCP_ACCEPT_HEADER},
            )
            unauthenticated_status = unauthenticated_response.status_code
            if unauthenticated_status != 401:
                errors.append(
                    "MCP endpoint did not reject unauthenticated requests with HTTP 401"
                )

        if token:
            authenticated_response = client.get(
                mcp_url,
                headers={
                    "Accept": MCP_ACCEPT_HEADER,
                    "Authorization": f"Bearer {token}",
                },
            )
            authenticated_status = authenticated_response.status_code
            authenticated_reachable = authenticated_status not in {401, 403, 421} and (
                authenticated_status < 500
            )
            if not authenticated_reachable:
                errors.append(
                    f"MCP endpoint rejected bearer token with HTTP {authenticated_status}"
                )
        elif health.get("http_auth_required") is True:
            missing.append("local bearer token for MCP verification")
    except httpx.HTTPError as exc:
        errors.append(str(exc))
    finally:
        if owns_client:
            client.close()

    return RemoteVerification(
        base_url=base_url,
        health_url=health_url,
        mcp_url=mcp_url,
        health_status_code=health_status_code,
        health=health,
        unauthenticated_mcp_status_code=unauthenticated_status,
        authenticated_mcp_status_code=authenticated_status,
        authenticated_mcp_reachable=authenticated_reachable,
        errors=errors,
        missing=missing,
    )
