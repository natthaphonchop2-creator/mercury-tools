from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from starlette.requests import Request
from starlette.testclient import TestClient

from mercury_tools.auth import consent as consent_module
from mercury_tools.auth.consent import (
    AuthorizationRedirect,
    ConsentAuthenticationRequired,
    ConsentDetails,
    ConsentError,
    OAuthSession,
    OAuthSessionCookie,
)
from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.mcp.server import create_test_http_app
from mercury_tools.v1.constants import CANONICAL_MCP_RESOURCE

AUTHORIZATION_SERVER = "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1"
AUTHORIZATION_ID = "auth_txn_0123456789abcdef"
MERCURY_ORIGIN = "https://mercury-tools-mcp.onrender.com"
PUBLISHABLE_KEY = "sb_publishable_test"
FORM_HEADERS = {"Origin": MERCURY_ORIGIN}


class UnusedProviderOAuthService:
    async def complete_callback(self, _callback: object) -> None:
        raise AssertionError("consent tests must not invoke provider OAuth")

    async def disconnect(self, *_args: object) -> None:
        raise AssertionError("consent tests must not disconnect provider OAuth")


class UnusedPeakSetupService:
    async def start(self, *_args: object) -> None:
        raise AssertionError("consent tests must not start PEAK setup")

    async def exchange(self, *_args: object) -> None:
        raise AssertionError("consent tests must not exchange PEAK setup")

    async def complete(self, *_args: object) -> None:
        raise AssertionError("consent tests must not complete PEAK setup")

    async def disconnect(self, *_args: object) -> None:
        raise AssertionError("consent tests must not disconnect PEAK")


def _create_http_app(**kwargs: object):
    return create_test_http_app(
        provider_oauth_service=UnusedProviderOAuthService(),
        peak_setup_service=UnusedPeakSetupService(),
        **kwargs,
    )


class StubConsentHandoff:
    def __init__(self, details: ConsentDetails) -> None:
        self.details = details
        self.decisions: list[tuple[str, str]] = []
        self.sign_ins: list[tuple[str, str]] = []

    async def get_authorization_details(
        self,
        request: Request,
        authorization_id: str,
    ) -> ConsentDetails | AuthorizationRedirect:
        return self.details

    async def submit_decision(
        self,
        request: Request,
        authorization_id: str,
        decision: str,
    ) -> str:
        self.decisions.append((authorization_id, decision))
        return f"{self.details.redirect_uri}?code=opaque-code&state=opaque-state"

    async def sign_in(self, email: str, password: str) -> OAuthSession:
        self.sign_ins.append((email, password))
        return OAuthSession(access_token="session-access-token", expires_in=600)


def test_oauth_session_cookie_authenticates_its_issued_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cookie = OAuthSessionCookie(base64.b64encode(b"k" * 32).decode("ascii"))
    monkeypatch.setattr(consent_module.time, "time", lambda: 1_000)
    sealed = cookie.seal(OAuthSession(access_token="browser-bearer", expires_in=600).access_token)
    payload = bytearray(base64.urlsafe_b64decode(sealed))
    payload[:8] = (10_000).to_bytes(8, "big")
    tampered = base64.urlsafe_b64encode(payload).decode("ascii")
    monkeypatch.setattr(consent_module.time, "time", lambda: 10_000)

    with pytest.raises(ConsentAuthenticationRequired):
        cookie.open(tampered)


@pytest.fixture(autouse=True)
def _v1_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from mercury_tools.mcp import server
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    original_enabled = server._PROCESS_V1_ENABLED
    original_tools = dict(server.mcp._tool_manager._tools)
    values = {
        "MERCURY_V1_ENABLED": "true",
        "MERCURY_CANONICAL_MCP_RESOURCE": CANONICAL_MCP_RESOURCE,
        "SUPABASE_URL": "https://vbnlkqvauqwnjbxngkas.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-test",
        "SUPABASE_AUTH_ISSUER": AUTHORIZATION_SERVER,
        "SUPABASE_PUBLISHABLE_KEY": PUBLISHABLE_KEY,
        "SUPABASE_JWKS_URL": f"{AUTHORIZATION_SERVER}/.well-known/jwks.json",
        "SUPABASE_JWT_AUDIENCE": CANONICAL_MCP_RESOURCE,
        "MERCURY_VAULT_ACTIVE_KEY": base64.b64encode(b"a" * 32).decode("ascii"),
        "MERCURY_VAULT_ACTIVE_KEY_VERSION": "v1",
        "FLOWACCOUNT_MCP_SANDBOX_URL": "https://flowaccount-sandbox.example/mcp",
        "FLOWACCOUNT_MCP_PRODUCTION_URL": "https://flowaccount.example/mcp",
        "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity-sandbox.flowaccount.example"
        ),
        "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity.flowaccount.example"
        ),
        "PEAK_MCP_UAT_URL": "https://peak-uat.example/mcp",
        "PEAK_MCP_PRODUCTION_URL": "https://peak.example/mcp",
        "MERCURY_PROVIDER_CALLBACK_BASE_URL": "https://mercury-tools-mcp.onrender.com",
        "MERCURY_TOOLS_HTTP_REQUIRE_AUTH": "false",
        "MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with server._PROCESS_V1_CONFIGURATION_LOCK:
        configure_v1_tools(server.mcp, enabled=True)
        server._PROCESS_V1_ENABLED = True
    try:
        yield
    finally:
        with server._PROCESS_V1_CONFIGURATION_LOCK:
            server.mcp._tool_manager._tools.clear()
            server.mcp._tool_manager._tools.update(original_tools)
            server._PROCESS_V1_ENABLED = original_enabled


def _details(**overrides: object) -> ConsentDetails:
    values: dict[str, object] = {
        "authorization_id": AUTHORIZATION_ID,
        "client_id": "client-1",
        "client_name": "Trusted Desktop Host",
        "redirect_uri": "https://client.example/oauth/callback",
        "scopes": frozenset({"openid", "email", "profile"}),
    }
    values.update(overrides)
    return ConsentDetails(**values)


def _client(handoff: StubConsentHandoff) -> TestClient:
    return TestClient(
        _create_http_app(consent_handoff=handoff),
        base_url=MERCURY_ORIGIN,
        raise_server_exceptions=False,
    )


def test_consent_displays_verified_client_scopes_resource_and_workspace_access() -> None:
    response = _client(StubConsentHandoff(_details())).get(
        f"/oauth/consent?authorization_id={AUTHORIZATION_ID}"
    )

    assert response.status_code == 200
    assert "Trusted Desktop Host" in response.text
    assert "openid" in response.text
    assert "email" in response.text
    assert "profile" in response.text
    assert CANONICAL_MCP_RESOURCE in response.text
    assert "Mercury workspace" in response.text
    assert 'name="authorization_id"' in response.text
    assert 'name="decision"' in response.text
    assert 'name="client_id"' not in response.text
    assert 'name="redirect_uri"' not in response.text
    assert 'name="scope"' not in response.text
    assert "<script" not in response.text
    assert "analytics" not in response.text.lower()
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'none'; style-src 'self' 'unsafe-inline'; "
        "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )


@pytest.mark.parametrize(
    "redirect_uri",
    (
        "https://*.example.com/oauth/callback",
        "http://client.example/oauth/callback",
        "https://client.example/oauth/*",
        "https://user:secret@client.example/oauth/callback",
    ),
)
def test_consent_rejects_wildcard_or_unsafe_hosted_redirect_uri(
    redirect_uri: str,
) -> None:
    response = _client(StubConsentHandoff(_details(redirect_uri=redirect_uri))).get(
        f"/oauth/consent?authorization_id={AUTHORIZATION_ID}"
    )

    assert response.status_code == 400
    assert response.json() == {"error": "mercury_authorization_invalid"}
    assert redirect_uri not in response.text


def test_consent_rejects_mismatched_oauth_transaction() -> None:
    handoff = StubConsentHandoff(
        _details().model_copy(update={"authorization_id": "auth_txn_ffffffffffffffff"})
    )

    response = _client(handoff).get(f"/oauth/consent?authorization_id={AUTHORIZATION_ID}")

    assert response.status_code == 400
    assert response.json() == {"error": "mercury_authorization_invalid"}
    assert handoff.decisions == []


def test_consent_posts_only_opaque_transaction_and_user_choice() -> None:
    handoff = StubConsentHandoff(_details())
    response = _client(handoff).post(
        "/oauth/consent",
        data={"authorization_id": AUTHORIZATION_ID, "decision": "approve"},
        headers=FORM_HEADERS,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        "https://client.example/oauth/callback?code=opaque-code&state=opaque-state"
    )
    assert handoff.decisions == [(AUTHORIZATION_ID, "approve")]
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_consent_rejects_extra_form_fields_and_unverified_redirect_result() -> None:
    handoff = StubConsentHandoff(_details())
    client = _client(handoff)

    extra = client.post(
        "/oauth/consent",
        data={
            "authorization_id": AUTHORIZATION_ID,
            "decision": "approve",
            "client_secret": "must-not-be-accepted",
        },
        headers=FORM_HEADERS,
    )
    assert extra.status_code == 400
    assert "must-not-be-accepted" not in extra.text
    assert handoff.decisions == []

    class BadRedirectHandoff(StubConsentHandoff):
        async def submit_decision(
            self,
            request: Request,
            authorization_id: str,
            decision: str,
        ) -> str:
            return "https://attacker.example/callback?code=stolen"

    bad_redirect = _client(BadRedirectHandoff(_details())).post(
        "/oauth/consent",
        data={"authorization_id": AUTHORIZATION_ID, "decision": "approve"},
        headers=FORM_HEADERS,
        follow_redirects=False,
    )
    assert bad_redirect.status_code == 400
    assert "attacker.example" not in bad_redirect.text


def test_consent_rejects_missing_or_malformed_authorization_id() -> None:
    handoff = StubConsentHandoff(_details())
    client = _client(handoff)

    assert client.get("/oauth/consent").status_code == 400
    assert client.get("/oauth/consent?authorization_id=../secret").status_code == 400
    assert handoff.decisions == []


def test_consent_type_does_not_expand_mercury_principal_contract() -> None:
    assert set(MercuryPrincipal.model_fields) == {
        "subject",
        "client_id",
        "scopes",
        "token_id",
    }


def test_consent_fails_closed_when_canonical_template_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        consent_module,
        "_read_template",
        lambda _name: (_ for _ in ()).throw(FileNotFoundError()),
        raising=False,
    )

    response = _client(StubConsentHandoff(_details())).get(
        f"/oauth/consent?authorization_id={AUTHORIZATION_ID}"
    )

    assert response.status_code == 400
    assert response.json() == {"error": "mercury_authorization_invalid"}


@pytest.mark.parametrize(
    "redirect_url",
    (
        "https://client.example/oauth/callback?code=auto&state=opaque",
        "http://127.0.0.1:4567/callback?code=auto&state=opaque",
    ),
)
def test_auto_approved_authorization_redirects_without_second_consent_page(
    redirect_url: str,
) -> None:
    class AutoApprovedHandoff(StubConsentHandoff):
        async def get_authorization_details(
            self,
            request: Request,
            authorization_id: str,
        ) -> ConsentDetails | AuthorizationRedirect:
            return AuthorizationRedirect(redirect_url=redirect_url)

    response = _client(AutoApprovedHandoff(_details())).get(
        f"/oauth/consent?authorization_id={AUTHORIZATION_ID}",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == redirect_url
    assert "Trusted Desktop Host" not in response.text


@pytest.mark.parametrize(
    "redirect_url",
    (
        "http://client.example/oauth/callback?code=auto",
        "https://*.example.com/oauth/callback?code=auto",
        "https://user:secret@client.example/oauth/callback?code=auto",
    ),
)
def test_auto_approved_authorization_rejects_unsafe_redirect(
    redirect_url: str,
) -> None:
    class UnsafeAutoApprovedHandoff(StubConsentHandoff):
        async def get_authorization_details(
            self,
            request: Request,
            authorization_id: str,
        ) -> ConsentDetails | AuthorizationRedirect:
            return AuthorizationRedirect(redirect_url=redirect_url)

    response = _client(UnsafeAutoApprovedHandoff(_details())).get(
        f"/oauth/consent?authorization_id={AUTHORIZATION_ID}",
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert redirect_url not in response.text


@pytest.mark.parametrize("path", ("/oauth/consent", "/oauth/sign-in"))
@pytest.mark.parametrize("origin", (None, "https://attacker.example"))
def test_state_changing_browser_forms_require_exact_mercury_origin(
    path: str,
    origin: str | None,
) -> None:
    handoff = StubConsentHandoff(_details())
    client = _client(handoff)
    data = (
        {"authorization_id": AUTHORIZATION_ID, "decision": "approve"}
        if path.endswith("consent")
        else {
            "authorization_id": AUTHORIZATION_ID,
            "email": "owner@example.com",
            "password": "not-a-real-password",
        }
    )
    headers = {"Origin": origin} if origin is not None else {}

    response = client.post(path, data=data, headers=headers, follow_redirects=False)

    assert response.status_code == 400
    assert handoff.decisions == []
    assert handoff.sign_ins == []


def test_consumed_authorization_transaction_replay_is_rejected() -> None:
    class OneTimeHandoff(StubConsentHandoff):
        async def get_authorization_details(
            self,
            request: Request,
            authorization_id: str,
        ) -> ConsentDetails | AuthorizationRedirect:
            if self.decisions:
                raise ConsentError()
            return self.details

    handoff = OneTimeHandoff(_details())
    client = _client(handoff)
    data = {"authorization_id": AUTHORIZATION_ID, "decision": "approve"}

    first = client.post(
        "/oauth/consent",
        data=data,
        headers=FORM_HEADERS,
        follow_redirects=False,
    )
    replay = client.post(
        "/oauth/consent",
        data=data,
        headers=FORM_HEADERS,
        follow_redirects=False,
    )

    assert first.status_code == 303
    assert replay.status_code == 400
    assert handoff.decisions == [(AUTHORIZATION_ID, "approve")]


@pytest.mark.parametrize("decision", ("approve", "deny"))
def test_default_browser_sign_in_session_details_and_decision_handoff(
    decision: str,
) -> None:
    access_token = f"supabase-access-token-{decision}"
    refresh_token = f"supabase-refresh-token-{decision}"
    upstream_calls: list[str] = []

    async def supabase(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == PUBLISHABLE_KEY
        if request.url.path.endswith("/token"):
            upstream_calls.append("sign_in")
            assert dict(request.url.params) == {"grant_type": "password"}
            assert "authorization" not in request.headers
            assert json.loads(request.content) == {
                "email": "owner@example.com",
                "password": "correct horse battery staple",
            }
            return httpx.Response(
                200,
                json={
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_in": 3600,
                },
            )

        assert request.headers["authorization"] == f"Bearer {access_token}"
        if request.method == "GET":
            upstream_calls.append("details")
            return httpx.Response(
                200,
                json={
                    "authorization_id": AUTHORIZATION_ID,
                    "redirect_uri": "https://client.example/oauth/callback",
                    "client": {
                        "id": "client-1",
                        "name": "Trusted Desktop Host",
                    },
                    "scope": "openid email profile",
                },
            )

        upstream_calls.append(decision)
        assert json.loads(request.content) == {"action": decision}
        return httpx.Response(
            200,
            json={
                "redirect_url": (
                    "https://client.example/oauth/callback"
                    f"?code=opaque-{decision}&state=opaque-state"
                )
            },
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(supabase))
    try:
        client = TestClient(
            _create_http_app(consent_http_client=async_client),
            base_url=MERCURY_ORIGIN,
            raise_server_exceptions=False,
        )
        unauthenticated = client.get(
            f"/oauth/consent?authorization_id={AUTHORIZATION_ID}",
            follow_redirects=False,
        )
        assert unauthenticated.status_code == 303
        assert unauthenticated.headers["location"] == (
            f"/oauth/sign-in?authorization_id={AUTHORIZATION_ID}"
        )
        assert upstream_calls == []

        sign_in_page = client.get(unauthenticated.headers["location"])
        assert sign_in_page.status_code == 200
        assert 'type="email"' in sign_in_page.text
        assert 'type="password"' in sign_in_page.text
        assert AUTHORIZATION_ID in sign_in_page.text

        signed_in = client.post(
            "/oauth/sign-in",
            data={
                "authorization_id": AUTHORIZATION_ID,
                "email": "owner@example.com",
                "password": "correct horse battery staple",
            },
            headers=FORM_HEADERS,
            follow_redirects=False,
        )
        assert signed_in.status_code == 303
        assert signed_in.headers["location"] == (
            f"/oauth/consent?authorization_id={AUTHORIZATION_ID}"
        )
        cookies = signed_in.headers.get_list("set-cookie")
        assert len(cookies) == 2
        assert any("Path=/oauth" in cookie for cookie in cookies)
        assert any("Path=/auth/providers/peak/setup" in cookie for cookie in cookies)
        assert all("HttpOnly" in cookie for cookie in cookies)
        assert all("Secure" in cookie for cookie in cookies)
        assert all("SameSite=lax" in cookie for cookie in cookies)
        assert all("Max-Age=600" in cookie for cookie in cookies)
        assert all(access_token not in cookie for cookie in cookies)
        assert all(refresh_token not in cookie for cookie in cookies)
        assert "owner@example.com" not in signed_in.text
        assert "correct horse battery staple" not in signed_in.text

        consent = client.get(signed_in.headers["location"])
        assert consent.status_code == 200
        assert "Trusted Desktop Host" in consent.text
        assert access_token not in consent.text
        assert refresh_token not in consent.text

        decided = client.post(
            "/oauth/consent",
            data={
                "authorization_id": AUTHORIZATION_ID,
                "decision": decision,
            },
            headers=FORM_HEADERS,
            follow_redirects=False,
        )
        assert decided.status_code == 303
        assert decided.headers["location"] == (
            f"https://client.example/oauth/callback?code=opaque-{decision}&state=opaque-state"
        )
        assert access_token not in decided.text
        assert refresh_token not in decided.text
    finally:
        asyncio.run(async_client.aclose())

    assert upstream_calls == ["sign_in", "details", "details", decision]


def test_default_handoff_accepts_trusted_auto_approval_response() -> None:
    access_token = "supabase-auto-access-token"

    async def supabase(request: httpx.Request) -> httpx.Response:
        assert request.headers["apikey"] == PUBLISHABLE_KEY
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": access_token, "expires_in": 600},
            )
        assert request.headers["authorization"] == f"Bearer {access_token}"
        return httpx.Response(
            200,
            json={
                "redirect_url": (
                    "https://client.example/oauth/callback?code=auto-approved&state=opaque"
                )
            },
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(supabase))
    try:
        client = TestClient(
            _create_http_app(consent_http_client=async_client),
            base_url=MERCURY_ORIGIN,
            raise_server_exceptions=False,
        )
        signed_in = client.post(
            "/oauth/sign-in",
            data={
                "authorization_id": AUTHORIZATION_ID,
                "email": "owner@example.com",
                "password": "correct horse battery staple",
            },
            headers=FORM_HEADERS,
            follow_redirects=False,
        )
        approved = client.get(
            signed_in.headers["location"],
            follow_redirects=False,
        )
    finally:
        asyncio.run(async_client.aclose())

    assert approved.status_code == 303
    assert approved.headers["location"] == (
        "https://client.example/oauth/callback?code=auto-approved&state=opaque"
    )


@pytest.mark.parametrize(
    "scope",
    (
        None,
        ["openid", "email", "profile"],
        {"openid": True},
    ),
)
def test_default_handoff_rejects_non_string_scope_with_sanitized_error(
    scope: object,
) -> None:
    access_token = "supabase-malformed-scope-access-token"

    async def supabase(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return httpx.Response(
                200,
                json={"access_token": access_token, "expires_in": 600},
            )
        assert request.headers["authorization"] == f"Bearer {access_token}"
        return httpx.Response(
            200,
            json={
                "authorization_id": AUTHORIZATION_ID,
                "redirect_uri": "https://client.example/oauth/callback",
                "client": {
                    "id": "client-1",
                    "name": "Trusted Desktop Host",
                },
                "scope": scope,
            },
        )

    async_client = httpx.AsyncClient(transport=httpx.MockTransport(supabase))
    try:
        client = TestClient(
            _create_http_app(consent_http_client=async_client),
            base_url=MERCURY_ORIGIN,
            raise_server_exceptions=False,
        )
        signed_in = client.post(
            "/oauth/sign-in",
            data={
                "authorization_id": AUTHORIZATION_ID,
                "email": "owner@example.com",
                "password": "correct horse battery staple",
            },
            headers=FORM_HEADERS,
            follow_redirects=False,
        )
        response = client.get(
            signed_in.headers["location"],
            follow_redirects=False,
        )
    finally:
        asyncio.run(async_client.aclose())

    assert response.status_code == 400
    assert response.json() == {"error": "mercury_authorization_invalid"}
    assert access_token not in response.text
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_built_wheel_contains_and_renders_canonical_auth_templates(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(tmp_path.glob("mercury_tools-0.3.1-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert "mercury_tools/auth/templates/consent.html" in names
        assert "mercury_tools/auth/templates/sign_in.html" in names
        archive.extractall(tmp_path / "installed")

    code = """
from mercury_tools.auth.consent import ConsentDetails, _render_consent
details = ConsentDetails(
    authorization_id="auth_txn_0123456789abcdef",
    client_id="client-1",
    client_name="Installed Wheel Host",
    redirect_uri="https://client.example/callback",
    scopes=frozenset({"openid", "email", "profile"}),
)
print(_render_consent(details, canonical_resource="https://resource.example/mcp"))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join((str(tmp_path / "installed"), env.get("PYTHONPATH", "")))
    rendered = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "Installed Wheel Host" in rendered.stdout
    assert "https://resource.example/mcp" in rendered.stdout
