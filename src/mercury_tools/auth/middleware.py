"""Starlette middleware for Mercury V1 OAuth identities."""

from __future__ import annotations

from contextvars import ContextVar
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from mercury_tools.auth.consent import OAUTH_SESSION_COOKIE
from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal, PrincipalResolver
from mercury_tools.providers.peak_setup import (
    PEAK_SETUP_BROWSER_COOKIE,
    PEAK_SETUP_EXCHANGE_PATH,
    PEAK_SETUP_PATH,
    PeakBrowserSessionBinding,
    PeakBrowserSessionManager,
)

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
        peak_browser_session_key: str | None = None,
        peak_browser_session_clock=None,
    ) -> None:
        super().__init__(app)
        self.principal_resolver = principal_resolver
        self.mcp_path = mcp_path.rstrip("/") or "/"
        self.peak_browser_session_manager = (
            PeakBrowserSessionManager(
                encoded_key=peak_browser_session_key,
                principal_resolver=principal_resolver,
                clock=peak_browser_session_clock,
            )
            if peak_browser_session_key is not None
            else None
        )
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
        if request.method == "GET" and request.url.path == PEAK_SETUP_PATH:
            return await call_next(request)
        if request.method == "OPTIONS" or not self._is_protected(request.url.path):
            return await call_next(request)

        bearer_token = _bearer_token(request.headers.get("authorization"))
        peak_browser_request = self._accepts_peak_browser_session(request)
        principal: MercuryPrincipal | None = None
        binding: PeakBrowserSessionBinding | None = None
        error_code: str | None = None
        try:
            if peak_browser_request:
                if request.headers.getlist("authorization"):
                    return self._invalid_token(clear_peak_browser_session=True)
                sealed_session = _single_cookie(request, OAUTH_SESSION_COOKIE)
                if sealed_session is None:
                    return self._auth_required(clear_peak_browser_session=True)
                if request.url.path == PEAK_SETUP_PATH:
                    sealed_binding = _single_cookie(request, PEAK_SETUP_BROWSER_COOKIE)
                    if sealed_binding is None:
                        return self._auth_required(clear_peak_browser_session=True)
                    (
                        principal,
                        bearer_token,
                        binding,
                    ) = await self.peak_browser_session_manager.authenticate_bound(
                        sealed_session,
                        sealed_binding,
                    )
                else:
                    principal, bearer_token = await self.peak_browser_session_manager.authenticate(
                        sealed_session
                    )
            elif bearer_token is not None:
                principal = await self.principal_resolver.resolve(bearer_token)
            else:
                return self._auth_required()
        except MercuryAuthError as exc:
            error_code = exc.code
            del exc
        except Exception:
            error_code = "mercury_token_invalid"

        if error_code is not None or principal is None or bearer_token is None:
            if error_code == "mercury_scope_insufficient":
                return JSONResponse({"error": error_code}, status_code=403)
            return self._invalid_token(clear_peak_browser_session=peak_browser_request)

        if not REQUIRED_IDENTITY_SCOPES.issubset(principal.scopes):
            return JSONResponse(
                {"error": "mercury_scope_insufficient"},
                status_code=403,
            )
        request.state.mercury_principal = principal
        request.state.peak_browser_session_manager = self.peak_browser_session_manager
        if binding is not None:
            request.state.peak_browser_session_binding = binding
        token = _REQUEST_BEARER.set((principal.subject, bearer_token))
        try:
            return await call_next(request)
        finally:
            _REQUEST_BEARER.reset(token)

    def _accepts_peak_browser_session(self, request: Request) -> bool:
        return bool(
            self.peak_browser_session_manager is not None
            and request.method == "POST"
            and request.url.path in {PEAK_SETUP_EXCHANGE_PATH, PEAK_SETUP_PATH}
        )

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

    def _auth_required(
        self,
        *,
        clear_peak_browser_session: bool = False,
    ) -> JSONResponse:
        response = JSONResponse(
            {"error": "mercury_auth_required"},
            status_code=401,
            headers={
                "WWW-Authenticate": (f'Bearer resource_metadata="{self.resource_metadata_url}"')
            },
        )
        if clear_peak_browser_session:
            _clear_peak_browser_cookies(response)
        return response

    def _invalid_token(
        self,
        *,
        clear_peak_browser_session: bool = False,
    ) -> JSONResponse:
        response = JSONResponse(
            {"error": "mercury_token_invalid"},
            status_code=401,
            headers={
                "WWW-Authenticate": (
                    'Bearer error="invalid_token", '
                    f'resource_metadata="{self.resource_metadata_url}"'
                )
            },
        )
        if clear_peak_browser_session:
            _clear_peak_browser_cookies(response)
        return response


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


def _single_cookie(
    request: Request,
    name: str,
) -> str | None:
    values: list[str] = []
    for header in request.headers.getlist("cookie"):
        for item in header.split(";"):
            cookie_name, separator, value = item.strip().partition("=")
            if separator and cookie_name == name:
                values.append(value)
    if len(values) != 1 or not values[0] or any(character.isspace() for character in values[0]):
        return None
    return values[0]


def _clear_peak_browser_cookies(response: JSONResponse) -> None:
    response.delete_cookie(
        OAUTH_SESSION_COOKIE,
        path=PEAK_SETUP_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.delete_cookie(
        PEAK_SETUP_BROWSER_COOKIE,
        path=PEAK_SETUP_PATH,
        secure=True,
        httponly=True,
        samesite="strict",
    )


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
