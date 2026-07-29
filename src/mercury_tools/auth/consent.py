"""First-party Mercury sign-in and consent backed by Supabase OAuth."""

from __future__ import annotations

import base64
import binascii
import html
import os
import re
import time
from collections.abc import Mapping, Sequence
from importlib import resources
from string import Template
from typing import Protocol
from urllib.parse import parse_qsl, quote, unquote, urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

IDENTITY_SCOPES = frozenset({"openid", "email", "profile"})
OAUTH_SESSION_COOKIE = "__Secure-mercury-oauth-session"
OAUTH_SESSION_TTL_SECONDS = 600
OAUTH_SESSION_COOKIE_PATH = "/oauth"
OAUTH_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}
CONSENT_HEADERS = OAUTH_HEADERS
_AUTHORIZATION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,512}$")
_SESSION_PREFIX = b"mercury-oauth-session\x00"
_SESSION_AAD = b"mercury-oauth-session-cookie-v1"
_SESSION_NONCE_BYTES = 12
_SESSION_TIMESTAMP_BYTES = 8


class ConsentError(RuntimeError):
    """Closed consent failure without upstream details."""

    def __init__(self, code: str = "mercury_authorization_invalid") -> None:
        self.code = code
        super().__init__(code)


class ConsentAuthenticationRequired(ConsentError):
    """The browser needs a Mercury-owned Supabase sign-in session."""


class ConsentDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    authorization_id: str = Field(min_length=16, max_length=512)
    client_id: str = Field(min_length=1, max_length=512)
    client_name: str = Field(min_length=1, max_length=200)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    scopes: frozenset[str]


class AuthorizationRedirect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    redirect_url: str = Field(min_length=1, max_length=4096)


class OAuthSession(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    access_token: SecretStr
    expires_in: int = Field(gt=0, le=86_400)


AuthorizationResult = ConsentDetails | AuthorizationRedirect


class ConsentHandoff(Protocol):
    async def get_authorization_details(
        self,
        request: Request,
        authorization_id: str,
    ) -> AuthorizationResult: ...

    async def submit_decision(
        self,
        request: Request,
        authorization_id: str,
        decision: str,
    ) -> str: ...

    async def sign_in(self, email: str, password: str) -> OAuthSession: ...


class OAuthSessionCookie:
    """Authenticated encryption for the short-lived browser OAuth session."""

    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(encoded_key, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("mercury_oauth_session_key_invalid") from None
        if len(key) != 32:
            raise ValueError("mercury_oauth_session_key_invalid")
        self._cipher = AESGCM(key)

    def seal(self, access_token: SecretStr) -> str:
        sealed: str | None = None
        failed = False
        try:
            raw = access_token.get_secret_value()
            if not raw or len(raw) > 16_384:
                raise ValueError
            issued_at = int(time.time()).to_bytes(_SESSION_TIMESTAMP_BYTES, "big")
            nonce = os.urandom(_SESSION_NONCE_BYTES)
            ciphertext = self._cipher.encrypt(
                nonce,
                _SESSION_PREFIX + raw.encode("utf-8"),
                _SESSION_AAD + issued_at,
            )
            sealed = base64.urlsafe_b64encode(issued_at + nonce + ciphertext).decode("ascii")
        except Exception:
            failed = True
        if failed or sealed is None:
            if "raw" in locals():
                del raw
            del access_token
            del self
            raise ConsentError()
        return sealed

    def open(self, sealed: str) -> str:
        if not sealed or len(sealed) > 32_768:
            raise ConsentAuthenticationRequired()
        token: str | None = None
        failed = False
        try:
            payload = base64.b64decode(
                sealed.encode("ascii"),
                altchars=b"-_",
                validate=True,
            )
            minimum_length = _SESSION_TIMESTAMP_BYTES + _SESSION_NONCE_BYTES + 16
            if len(payload) < minimum_length:
                raise ValueError
            issued_at_bytes = payload[:_SESSION_TIMESTAMP_BYTES]
            issued_at = int.from_bytes(issued_at_bytes, "big")
            age = int(time.time()) - issued_at
            if age < 0 or age > OAUTH_SESSION_TTL_SECONDS:
                raise ValueError
            nonce_start = _SESSION_TIMESTAMP_BYTES
            nonce_end = nonce_start + _SESSION_NONCE_BYTES
            nonce = payload[nonce_start:nonce_end]
            plaintext = self._cipher.decrypt(
                nonce,
                payload[nonce_end:],
                _SESSION_AAD + issued_at_bytes,
            )
            if not plaintext.startswith(_SESSION_PREFIX):
                raise ValueError
            token = plaintext.removeprefix(_SESSION_PREFIX).decode("utf-8")
        except (
            binascii.Error,
            InvalidTag,
            UnicodeDecodeError,
            UnicodeEncodeError,
            ValueError,
        ):
            failed = True
        if failed or not token:
            if "plaintext" in locals():
                del plaintext
            if "payload" in locals():
                del payload
            del sealed
            del self
            raise ConsentAuthenticationRequired()
        return token


class SupabaseConsentHandoff:
    """Relay a verified user decision to the Supabase OAuth server."""

    def __init__(
        self,
        *,
        authorization_server: str,
        publishable_key: str,
        session_cookie: OAuthSessionCookie,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not publishable_key:
            raise ValueError("mercury_publishable_key_missing")
        self.authorization_server = authorization_server.rstrip("/")
        self.publishable_key = publishable_key
        self.session_cookie = session_cookie
        self._http_client = http_client

    async def sign_in(self, email: str, password: str) -> OAuthSession:
        response = await self._request(
            "POST",
            "/token?grant_type=password",
            json={"email": email, "password": password},
        )
        try:
            payload = response.json()
            access_token = payload["access_token"]
            expires_in = payload["expires_in"]
            if not isinstance(access_token, str) or not isinstance(expires_in, int):
                raise ValueError
            return OAuthSession(
                access_token=SecretStr(access_token),
                expires_in=expires_in,
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ConsentError() from None

    async def get_authorization_details(
        self,
        request: Request,
        authorization_id: str,
    ) -> AuthorizationResult:
        response = await self._authenticated_request(
            request,
            "GET",
            f"/oauth/authorizations/{quote(authorization_id, safe='')}",
        )
        try:
            payload = response.json()
            if isinstance(payload, Mapping) and set(payload) == {"redirect_url"}:
                return AuthorizationRedirect.model_validate(payload)
            client = payload["client"]
            scope = payload["scope"]
            if not isinstance(scope, str):
                raise ValueError
            return ConsentDetails(
                authorization_id=payload["authorization_id"],
                client_id=client["id"],
                client_name=client["name"],
                redirect_uri=payload["redirect_uri"],
                scopes=frozenset(scope.split()),
            )
        except (KeyError, TypeError, ValueError, ValidationError):
            raise ConsentError() from None

    async def submit_decision(
        self,
        request: Request,
        authorization_id: str,
        decision: str,
    ) -> str:
        response = await self._authenticated_request(
            request,
            "POST",
            f"/oauth/authorizations/{quote(authorization_id, safe='')}/consent",
            json={"action": decision},
        )
        try:
            payload = AuthorizationRedirect.model_validate(response.json())
        except (TypeError, ValueError, ValidationError):
            raise ConsentError() from None
        return payload.redirect_url

    async def _authenticated_request(
        self,
        request: Request,
        method: str,
        path: str,
        *,
        json: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        token = _supabase_session_token(
            request,
            session_cookie=self.session_cookie,
        )
        return await self._request(
            method,
            path,
            bearer_token=token,
            json=json,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        bearer_token: str | None = None,
        json: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json",
            "apikey": self.publishable_key,
        }
        if bearer_token is not None:
            headers["Authorization"] = f"Bearer {bearer_token}"
        try:
            if self._http_client is not None:
                response = await self._http_client.request(
                    method,
                    f"{self.authorization_server}{path}",
                    headers=headers,
                    json=json,
                )
            else:
                async with httpx.AsyncClient(
                    follow_redirects=False,
                    timeout=httpx.Timeout(5.0),
                ) as client:
                    response = await client.request(
                        method,
                        f"{self.authorization_server}{path}",
                        headers=headers,
                        json=json,
                    )
            if response.status_code == 401 and bearer_token is not None:
                raise ConsentAuthenticationRequired()
            response.raise_for_status()
            return response
        except ConsentAuthenticationRequired:
            raise
        except httpx.HTTPError:
            raise ConsentError() from None


class MercuryConsent:
    def __init__(
        self,
        *,
        handoff: ConsentHandoff,
        canonical_resource: str,
        browser_origin: str,
        session_cookie: OAuthSessionCookie,
        additional_session_cookie_paths: Sequence[str] = (),
    ) -> None:
        self.handoff = handoff
        self.canonical_resource = canonical_resource
        self.browser_origin = _origin(browser_origin)
        self.session_cookie = session_cookie
        self.session_cookie_paths = _session_cookie_paths(additional_session_cookie_paths)

    async def show(self, request: Request) -> Response:
        authorization_id = _query_authorization_id(request)
        if authorization_id is None:
            return _oauth_error()
        try:
            result = await self.handoff.get_authorization_details(
                request,
                authorization_id,
            )
            if isinstance(result, AuthorizationRedirect):
                if not _safe_final_redirect(result.redirect_url):
                    raise ConsentError()
                return RedirectResponse(
                    result.redirect_url,
                    status_code=303,
                    headers=OAUTH_HEADERS,
                )
            _validate_details(result, authorization_id=authorization_id)
            content = _render_consent(
                result,
                canonical_resource=self.canonical_resource,
            )
        except ConsentAuthenticationRequired:
            return _sign_in_redirect(
                authorization_id,
                clear_cookie=True,
                cookie_paths=self.session_cookie_paths,
            )
        except (ConsentError, OSError, ValidationError, TypeError, ValueError):
            return _oauth_error()
        return HTMLResponse(content, headers=OAUTH_HEADERS)

    async def sign_in_page(self, request: Request) -> Response:
        authorization_id = _query_authorization_id(request)
        if authorization_id is None:
            return _oauth_error()
        try:
            content = _render_sign_in(authorization_id)
        except (OSError, ValueError):
            return _oauth_error()
        return HTMLResponse(content, headers=OAUTH_HEADERS)

    async def sign_in(self, request: Request) -> Response:
        if not _same_origin(request, expected_origin=self.browser_origin):
            return _oauth_error()
        form = await _parse_sign_in_form(request)
        if form is None:
            return _oauth_error()
        authorization_id = form["authorization_id"]
        try:
            session = await self.handoff.sign_in(
                form["email"],
                form["password"],
            )
            sealed = self.session_cookie.seal(session.access_token)
            max_age = min(session.expires_in, OAUTH_SESSION_TTL_SECONDS)
            response = RedirectResponse(
                f"/oauth/consent?authorization_id={authorization_id}",
                status_code=303,
                headers=OAUTH_HEADERS,
            )
            for cookie_path in self.session_cookie_paths:
                response.set_cookie(
                    OAUTH_SESSION_COOKIE,
                    sealed,
                    max_age=max_age,
                    path=cookie_path,
                    secure=True,
                    httponly=True,
                    samesite="lax",
                )
            return response
        except (ConsentError, ValidationError, TypeError, ValueError):
            try:
                content = _render_sign_in(
                    authorization_id,
                    error=True,
                )
            except (OSError, ValueError):
                return _oauth_error()
            return HTMLResponse(
                content,
                status_code=400,
                headers=OAUTH_HEADERS,
            )

    async def decide(self, request: Request) -> Response:
        if not _same_origin(request, expected_origin=self.browser_origin):
            return _oauth_error()
        form = await _parse_decision_form(request)
        if form is None:
            return _oauth_error()
        authorization_id = form["authorization_id"]
        decision = form["decision"]
        try:
            result = await self.handoff.get_authorization_details(
                request,
                authorization_id,
            )
            if not isinstance(result, ConsentDetails):
                raise ConsentError()
            _validate_details(result, authorization_id=authorization_id)
            redirect_url = await self.handoff.submit_decision(
                request,
                authorization_id,
                decision,
            )
            if not _redirect_matches(result.redirect_uri, redirect_url):
                raise ConsentError()
        except ConsentAuthenticationRequired:
            return _sign_in_redirect(
                authorization_id,
                clear_cookie=True,
                cookie_paths=self.session_cookie_paths,
            )
        except (ConsentError, ValidationError, TypeError, ValueError):
            return _oauth_error()
        return RedirectResponse(
            redirect_url,
            status_code=303,
            headers=OAUTH_HEADERS,
        )


def _query_authorization_id(request: Request) -> str | None:
    items = list(request.query_params.multi_items())
    if len(items) != 1 or items[0][0] != "authorization_id":
        return None
    authorization_id = items[0][1]
    return authorization_id if _AUTHORIZATION_ID_RE.fullmatch(authorization_id) else None


async def _parse_sign_in_form(request: Request) -> dict[str, str] | None:
    form = await _urlencoded_form(
        request,
        expected_fields={"authorization_id", "email", "password"},
    )
    if form is None:
        return None
    if (
        not _AUTHORIZATION_ID_RE.fullmatch(form["authorization_id"])
        or not 1 <= len(form["email"]) <= 320
        or not 1 <= len(form["password"]) <= 1024
    ):
        return None
    return form


async def _parse_decision_form(request: Request) -> dict[str, str] | None:
    form = await _urlencoded_form(
        request,
        expected_fields={"authorization_id", "decision"},
    )
    if form is None:
        return None
    if not _AUTHORIZATION_ID_RE.fullmatch(form["authorization_id"]) or form["decision"] not in {
        "approve",
        "deny",
    }:
        return None
    return form


async def _urlencoded_form(
    request: Request,
    *,
    expected_fields: set[str],
) -> dict[str, str] | None:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
        "application/x-www-form-urlencoded"
    ):
        return None
    body = await request.body()
    if len(body) > 4096:
        return None
    try:
        items = parse_qsl(
            body.decode("utf-8"),
            keep_blank_values=True,
            strict_parsing=True,
        )
    except (UnicodeDecodeError, ValueError):
        return None
    if len(items) != len(expected_fields) or {key for key, _ in items} != expected_fields:
        return None
    return dict(items)


def _validate_details(details: ConsentDetails, *, authorization_id: str) -> None:
    if details.authorization_id != authorization_id:
        raise ConsentError()
    if details.scopes != IDENTITY_SCOPES:
        raise ConsentError()
    if not _safe_registered_redirect(details.redirect_uri):
        raise ConsentError()


def _safe_registered_redirect(value: str) -> bool:
    if not _safe_redirect_base(value):
        return False
    parsed = urlsplit(value)
    return bool(not parsed.query and not parsed.fragment and "?" not in value and "#" not in value)


def _safe_final_redirect(value: str) -> bool:
    if not _safe_redirect_base(value):
        return False
    return not urlsplit(value).fragment and "#" not in value


def _safe_redirect_base(value: str) -> bool:
    decoded = unquote(value)
    if any(marker in decoded for marker in ("*", "[", "]", "{", "}")):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    localhost = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    valid_scheme = parsed.scheme == "https" or (localhost and parsed.scheme == "http")
    return bool(
        valid_scheme
        and parsed.hostname
        and (port is None or port > 0)
        and not parsed.username
        and not parsed.password
    )


def _redirect_matches(registered: str, redirect_url: str) -> bool:
    if not _safe_registered_redirect(registered) or not _safe_final_redirect(redirect_url):
        return False
    expected = urlsplit(registered)
    actual = urlsplit(redirect_url)
    return bool(
        actual.scheme == expected.scheme
        and actual.netloc == expected.netloc
        and actual.path == expected.path
    )


def _render_consent(
    details: ConsentDetails,
    *,
    canonical_resource: str,
) -> str:
    template = Template(_read_template("consent.html"))
    scope_items = "\n".join(
        f"<li><code>{html.escape(scope)}</code></li>" for scope in ("openid", "email", "profile")
    )
    return template.substitute(
        client_name=html.escape(details.client_name),
        redirect_uri=html.escape(details.redirect_uri),
        resource=html.escape(canonical_resource),
        scope_items=scope_items,
        authorization_id=html.escape(details.authorization_id, quote=True),
    )


def _render_sign_in(authorization_id: str, *, error: bool = False) -> str:
    template = Template(_read_template("sign_in.html"))
    error_message = (
        '<p class="error">Sign-in failed. Check your credentials and try again.</p>'
        if error
        else ""
    )
    return template.substitute(
        authorization_id=html.escape(authorization_id, quote=True),
        error_message=error_message,
    )


def _read_template(name: str) -> str:
    return (
        resources.files("mercury_tools.auth")
        .joinpath("templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _supabase_session_token(
    request: Request,
    *,
    session_cookie: OAuthSessionCookie,
) -> str:
    state_token = getattr(request.state, "supabase_access_token", None)
    if isinstance(state_token, str) and state_token:
        return state_token
    authorization = request.headers.get("authorization", "")
    scheme, separator, token = authorization.partition(" ")
    if (
        separator
        and scheme.lower() == "bearer"
        and token
        and token == token.strip()
        and not any(character.isspace() for character in token)
    ):
        return token
    sealed = request.cookies.get(OAUTH_SESSION_COOKIE, "")
    return session_cookie.open(sealed)


def _origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("mercury_browser_origin_invalid")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def _same_origin(request: Request, *, expected_origin: str) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return False
    try:
        return _origin(origin) == expected_origin and origin.rstrip("/") == expected_origin
    except ValueError:
        return False


def _sign_in_redirect(
    authorization_id: str,
    *,
    clear_cookie: bool,
    cookie_paths: Sequence[str] = (OAUTH_SESSION_COOKIE_PATH,),
) -> RedirectResponse:
    response = RedirectResponse(
        f"/oauth/sign-in?authorization_id={authorization_id}",
        status_code=303,
        headers=OAUTH_HEADERS,
    )
    if clear_cookie:
        for cookie_path in cookie_paths:
            response.delete_cookie(
                OAUTH_SESSION_COOKIE,
                path=cookie_path,
                secure=True,
                httponly=True,
                samesite="lax",
            )
    return response


def _session_cookie_paths(additional_paths: Sequence[str]) -> tuple[str, ...]:
    paths = (OAUTH_SESSION_COOKIE_PATH, *tuple(additional_paths))
    if any(
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or path.endswith("/")
        or urlsplit(path) != urlsplit(path)._replace(query="", fragment="")
        for path in paths
    ):
        raise ValueError("mercury_oauth_session_cookie_path_invalid")
    return tuple(dict.fromkeys(paths))


def _oauth_error() -> JSONResponse:
    return JSONResponse(
        {"error": "mercury_authorization_invalid"},
        status_code=400,
        headers=OAUTH_HEADERS,
    )


__all__ = [
    "AuthorizationRedirect",
    "CONSENT_HEADERS",
    "ConsentDetails",
    "ConsentError",
    "ConsentHandoff",
    "MercuryConsent",
    "OAuthSession",
    "OAuthSessionCookie",
    "SupabaseConsentHandoff",
]
