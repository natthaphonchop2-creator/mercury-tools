"""Starlette middleware for Mercury V1 OAuth identities."""

from __future__ import annotations

from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal, PrincipalResolver

PUBLIC_PATHS = frozenset(
    {
        "/",
        "/healthz",
        "/readyz",
        "/privacy",
        "/terms",
        "/support",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
        "/auth/providers/flowaccount/callback",
    }
)
REQUIRED_IDENTITY_SCOPES = frozenset({"openid", "email", "profile"})
_REQUEST_BEARER: ContextVar[tuple[UUID, str] | None] = ContextVar(
    "mercury_request_bearer",
    default=None,
)


class MercuryOAuthMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app,
        *,
        principal_resolver: PrincipalResolver,
        canonical_resource: str,
        mcp_path: str = "/mcp",
    ) -> None:
        super().__init__(app)
        self.principal_resolver = principal_resolver
        self.mcp_path = mcp_path.rstrip("/") or "/"
        resource = urlsplit(canonical_resource)
        self.resource_metadata_url = urlunsplit(
            (
                resource.scheme,
                resource.netloc,
                f"/.well-known/oauth-protected-resource{resource.path}",
                "",
                "",
            )
        )

    async def dispatch(self, request: Request, call_next):
        if request.method == "OPTIONS" or not self._is_protected(request.url.path):
            return await call_next(request)

        bearer_token = _bearer_token(request.headers.get("authorization"))
        if bearer_token is None:
            return self._auth_required()

        try:
            principal = await self.principal_resolver.resolve(bearer_token)
        except MercuryAuthError as exc:
            if exc.code == "mercury_scope_insufficient":
                return JSONResponse({"error": exc.code}, status_code=403)
            return self._invalid_token()
        except Exception:
            return self._invalid_token()

        if not REQUIRED_IDENTITY_SCOPES.issubset(principal.scopes):
            return JSONResponse(
                {"error": "mercury_scope_insufficient"},
                status_code=403,
            )
        request.state.mercury_principal = principal
        token = _REQUEST_BEARER.set((principal.subject, bearer_token))
        try:
            return await call_next(request)
        finally:
            _REQUEST_BEARER.reset(token)

    def _is_protected(self, path: str) -> bool:
        if path in PUBLIC_PATHS:
            return False
        return any(
            _path_matches(path, prefix)
            for prefix in (
                self.mcp_path,
                "/api/cloud/v1",
                "/api/v1",
                "/auth/providers",
            )
        )

    def _auth_required(self) -> JSONResponse:
        return JSONResponse(
            {"error": "mercury_auth_required"},
            status_code=401,
            headers={
                "WWW-Authenticate": (f'Bearer resource_metadata="{self.resource_metadata_url}"')
            },
        )

    def _invalid_token(self) -> JSONResponse:
        return JSONResponse(
            {"error": "mercury_token_invalid"},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer error="invalid_token", '
                    f'resource_metadata="{self.resource_metadata_url}"'
                )
            },
        )


def _path_matches(path: str, prefix: str) -> bool:
    normalized = prefix.rstrip("/") or "/"
    return path == normalized or path.startswith(f"{normalized}/")


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        return None
    return token


def current_mercury_access_token(principal: MercuryPrincipal) -> str:
    checked = MercuryPrincipal.model_validate(principal)
    request_bearer = _REQUEST_BEARER.get()
    if request_bearer is None or request_bearer[0] != checked.subject:
        raise RuntimeError("mercury_request_bearer_unavailable")
    return request_bearer[1]


__all__ = [
    "MercuryOAuthMiddleware",
    "PUBLIC_PATHS",
    "current_mercury_access_token",
]
