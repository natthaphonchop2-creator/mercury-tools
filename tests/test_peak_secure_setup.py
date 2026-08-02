from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, TracebackType
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, SecretStr
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mercury_tools.auth.consent import (
    OAUTH_SESSION_COOKIE,
    MercuryConsent,
    OAuthSession,
    OAuthSessionCookie,
)
from mercury_tools.auth.middleware import MercuryOAuthMiddleware
from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.cloud.api import CloudDependencies, cloud_routes
from mercury_tools.config import Settings
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.peak import (
    PeakCredentialMaterial,
    PeakProfile,
    QualifiedPeakProviderContract,
    seal_peak_credentials,
)
from mercury_tools.providers.peak_setup import (
    PEAK_SETUP_BROWSER_COOKIE,
    PEAK_SETUP_EXCHANGE_PATH,
    PEAK_SETUP_PATH,
    InMemoryPeakSetupStore,
    PeakSetupError,
    PeakSetupService,
    PeakSetupSessionRecord,
    PeakSetupSubmission,
    SupabasePeakSetupStore,
)
from mercury_tools.providers.store import (
    ProviderConnectionStore,
    SupabaseProviderConnectionStore,
)
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 29, 4, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_USER_ID = UUID("55555555-5555-4555-8555-555555555555")
MERCURY_ACCESS_TOKEN = "MERCURY_ACCESS_TOKEN_SENTINEL"
ORIGIN = "https://mercury.example.com"
USER_TOKEN = "PEAK_USER_TOKEN_SENTINEL"
CONNECT_ID = "PEAK_CONNECT_ID_SENTINEL"
CONNECT_KEY = "PEAK_CONNECT_KEY_SENTINEL"
APPLICATION_CODE = "PEAK_APPLICATION_CODE_SENTINEL"


class ReviewedProfileRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )


class ReviewedProfileResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    reviewed_merchant_identifier: str
    reviewed_display_name: str


def _settings() -> Settings:
    return Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://mcp.peakaccount.com/mcp",
        peak_application_code=APPLICATION_CODE,
        provider_callback_base_url=ORIGIN,
        vault_active_key=base64.b64encode(b"k" * 32).decode("ascii"),
    )


def _contract() -> QualifiedPeakProviderContract:
    resource_hash = hashlib.sha256(_settings().peak_mcp_production_url.encode("utf-8")).hexdigest()
    return QualifiedPeakProviderContract(
        fixture_id="reviewed-peak-contract-2026-07-29",
        qualification_hash="a" * 64,
        resource_uri_sha256_by_environment={"production": resource_hash},
        credential_header_names={
            "user_token": "X-Reviewed-User",
            "connect_id": "X-Reviewed-Connect",
            "connect_key": "X-Reviewed-Key",
        },
        application_code_header_name="X-Reviewed-Application",
        profile_tool="reviewed_provider_profile",
        profile_request_model=ReviewedProfileRequest,
        profile_response_model=ReviewedProfileResponse,
        profile_normalizer=lambda response: PeakProfile(
            merchant_id=response.reviewed_merchant_identifier,
            merchant_display_name=response.reviewed_display_name,
        ),
    )


def _principal(subject: UUID = USER_ID) -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=subject,
        client_id="mercury-test-client",
        scopes=frozenset({"openid", "email", "profile"}),
        token_id="mercury-test-token",
    )


def _vault() -> CredentialVault:
    nonces = iter(bytes([index]) * 12 for index in range(1, 64))
    return CredentialVault(
        active_key_version="v1",
        keys={"v1": b"k" * 32},
        clock=lambda: NOW,
        nonce_factory=lambda _size: next(nonces),
    )


class FixedRandom:
    def __init__(self) -> None:
        self.next_value = 1

    def __call__(self, size: int) -> bytes:
        value = bytes([self.next_value]) * size
        self.next_value += 1
        return value


class FakeWorkspaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, UUID, WorkspaceRole]] = []
        self.allowed_workspaces = {WORKSPACE_ID, OTHER_WORKSPACE_ID}
        self.tenant_id = TENANT_ID

    def require_workspace(
        self,
        principal: MercuryPrincipal,
        access_token: str,
        workspace_id: UUID,
        required_role: WorkspaceRole,
    ) -> WorkspaceMembership:
        assert access_token == MERCURY_ACCESS_TOKEN
        if workspace_id not in self.allowed_workspaces:
            raise PermissionError("workspace_access_denied")
        self.calls.append((principal.subject, workspace_id, required_role))
        return WorkspaceMembership(
            tenant_id=self.tenant_id,
            tenant_display_name="Mercury Test Tenant",
            workspace_id=workspace_id,
            workspace_display_name="Mercury Test Workspace",
            role=WorkspaceRole.OWNER,
        )


class FakeProfileValidator:
    def __init__(self, *, fail: bool = False, failures_remaining: int = 0) -> None:
        self.failures_remaining = max(failures_remaining, int(fail))
        self.calls = 0

    async def validate_setup(self, _connection, _envelopes) -> PeakProfile:
        self.calls += 1
        if self.failures_remaining:
            self.failures_remaining -= 1
            raw_provider_failure = {
                "provider_payload": "DOWNSTREAM_SECRET_MUST_NOT_ESCAPE",
                "credential": USER_TOKEN,
            }
            raise RuntimeError(raw_provider_failure)
        return PeakProfile(
            merchant_id="merchant-123",
            merchant_display_name="PEAK Test Merchant",
        )


def _service(
    *,
    clock=None,
    contract: QualifiedPeakProviderContract | None = None,
    validator: FakeProfileValidator | None = None,
    workspace_service: FakeWorkspaceService | None = None,
) -> tuple[
    PeakSetupService,
    InMemoryPeakSetupStore,
    ProviderConnectionStore,
    FakeProfileValidator,
]:
    active_clock = clock or (lambda: NOW)
    vault = _vault()
    connections = ProviderConnectionStore(vault=vault, clock=active_clock)
    store = InMemoryPeakSetupStore(
        connection_store=connections,
        clock=active_clock,
    )
    selected_validator = validator or FakeProfileValidator()
    service = PeakSetupService(
        settings=_settings(),
        workspace_service=workspace_service or FakeWorkspaceService(),
        mercury_access_token=lambda _principal: MERCURY_ACCESS_TOKEN,
        setup_store=store,
        connection_store=connections,
        vault=vault,
        contract=contract,
        profile_validator=selected_validator,
        clock=active_clock,
        random_bytes=FixedRandom(),
    )
    return service, store, connections, selected_validator


def _assert_no_internal_secret_references(
    error: BaseException,
    *sentinels: str,
) -> None:
    assert error.__cause__ is None
    assert error.__context__ is None
    pending: list[object] = [error]
    seen: set[int] = set()
    rendered: list[str] = []
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        if isinstance(value, str):
            rendered.append(value)
        elif isinstance(value, (bytes, bytearray)):
            rendered.append(bytes(value).decode("utf-8", errors="ignore"))
        elif isinstance(value, TracebackType):
            if "/src/mercury_tools/" in value.tb_frame.f_code.co_filename:
                pending.extend(value.tb_frame.f_locals.values())
            if value.tb_next is not None:
                pending.append(value.tb_next)
        elif isinstance(value, BaseException):
            pending.extend(value.args)
            if value.__cause__ is not None:
                pending.append(value.__cause__)
            if value.__context__ is not None:
                pending.append(value.__context__)
            if value.__traceback__ is not None:
                pending.append(value.__traceback__)
        elif isinstance(value, Mapping):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, Sequence) and not isinstance(value, str):
            pending.extend(value)
        elif (
            not isinstance(value, (ModuleType, type))
            and not callable(value)
            and hasattr(value, "__dict__")
        ):
            pending.extend(vars(value).values())
    combined = "\n".join(rendered)
    assert all(sentinel not in combined for sentinel in sentinels)


def _submission(exchange) -> PeakSetupSubmission:
    return PeakSetupSubmission(
        setup_session=exchange.setup_session.get_secret_value(),
        csrf_token=exchange.csrf_token.get_secret_value(),
        user_token=USER_TOKEN,
        connect_id=CONNECT_ID,
        connect_key=CONNECT_KEY,
    )


@pytest.mark.asyncio
async def test_setup_url_uses_fragment_and_store_keeps_sha256_for_ten_minutes() -> None:
    service, store, *_ = _service()

    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        ProviderId.PEAK,
        "production",
    )
    parsed = urlsplit(started.setup_url)
    raw_token = parsed.fragment
    attempt = next(iter(store.attempts.values()))

    assert parsed.scheme == "https"
    assert parsed.query == ""
    assert parsed.path == PEAK_SETUP_PATH
    assert raw_token
    assert raw_token not in started.setup_url.split("#", 1)[0]
    assert attempt.token_hash == hashlib.sha256(raw_token.encode("ascii")).hexdigest()
    assert attempt.expires_at == NOW + timedelta(minutes=10)
    assert raw_token not in repr(attempt)
    assert "token_hash" not in attempt.model_dump(mode="json")


@pytest.mark.asyncio
async def test_exchange_and_finalization_enforce_exact_principal_binding_and_replay() -> None:
    service, store, connections, _ = _service(contract=_contract())
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    raw_token = urlsplit(started.setup_url).fragment

    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.exchange(_principal(OTHER_USER_ID), raw_token)
    exchange = await service.exchange(_principal(), raw_token)
    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.exchange(_principal(), raw_token)

    summary = await service.complete(_principal(), _submission(exchange))

    assert summary.provider is ProviderId.PEAK
    assert summary.readiness is ConnectionReadiness.READY
    assert summary.account_display_name == "PEAK Test Merchant"
    assert store.sessions[exchange.session_id].consumed_at == NOW
    assert next(iter(store.attempts.values())).consumed_at == NOW
    assert len(connections._envelopes) == 3
    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.complete(_principal(), _submission(exchange))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        {"csrf_token": "A" * 43},
        {"setup_session": "B" * 43},
    ],
)
async def test_wrong_csrf_or_session_fails_without_consuming_attempt(
    mutation: dict[str, str],
) -> None:
    service, store, connections, _ = _service(contract=_contract())
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)
    submission = _submission(exchange).model_copy(update=mutation)

    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.complete(_principal(), submission)

    assert next(iter(store.attempts.values())).consumed_at is None
    assert store.sessions[exchange.session_id].consumed_at is None
    assert connections._connections == {}
    assert connections._envelopes == {}


@pytest.mark.asyncio
async def test_validation_and_encryption_precede_atomic_consumption_and_failure_is_retryable() -> (
    None
):
    failing = FakeProfileValidator(fail=True)
    service, store, connections, _ = _service(
        contract=_contract(),
        validator=failing,
    )
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)

    with pytest.raises(
        PeakSetupError,
        match="^peak_setup_validation_failed$",
    ) as caught:
        await service.complete(_principal(), _submission(exchange))

    assert failing.calls == 1
    assert next(iter(store.attempts.values())).consumed_at is None
    assert store.sessions[exchange.session_id].consumed_at is None
    assert connections._connections == {}
    assert connections._envelopes == {}
    _assert_no_internal_secret_references(
        caught.value,
        "DOWNSTREAM_SECRET_MUST_NOT_ESCAPE",
        USER_TOKEN,
        CONNECT_ID,
        CONNECT_KEY,
    )


@pytest.mark.asyncio
async def test_unqualified_contract_fails_before_validation_and_keeps_attempt_retryable() -> None:
    service, store, connections, validator = _service(contract=None)
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)

    with pytest.raises(
        PeakSetupError,
        match="^peak_provider_contract_unqualified$",
    ):
        await service.complete(_principal(), _submission(exchange))

    assert validator.calls == 0
    assert next(iter(store.attempts.values())).consumed_at is None
    assert connections._connections == {}


@pytest.mark.asyncio
async def test_concurrent_finalization_has_exactly_one_winner() -> None:
    service, store, connections, _ = _service(contract=_contract())
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)

    outcomes = await asyncio.gather(
        service.complete(_principal(), _submission(exchange)),
        service.complete(_principal(), _submission(exchange)),
        return_exceptions=True,
    )

    successes = [item for item in outcomes if not isinstance(item, Exception)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], PeakSetupError)
    assert str(failures[0]) == "peak_setup_state_invalid"
    assert len(connections._connections) == 1
    assert len(connections._envelopes) == 3
    assert next(iter(store.attempts.values())).consumed_at == NOW


@pytest.mark.asyncio
async def test_expired_setup_fails_closed_without_consuming_attempt() -> None:
    now = [NOW]
    service, store, connections, _ = _service(
        clock=lambda: now[0],
        contract=_contract(),
    )
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    raw_token = urlsplit(started.setup_url).fragment
    now[0] = NOW + timedelta(minutes=10, microseconds=1)

    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.exchange(_principal(), raw_token)

    assert next(iter(store.attempts.values())).consumed_at is None
    assert connections._connections == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_before", ("exchange", "completion"))
async def test_workspace_membership_revocation_fails_without_consumption(
    revoked_before: str,
) -> None:
    workspace_service = FakeWorkspaceService()
    service, store, connections, _ = _service(
        contract=_contract(),
        workspace_service=workspace_service,
    )
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    raw_token = urlsplit(started.setup_url).fragment
    if revoked_before == "exchange":
        workspace_service.allowed_workspaces.remove(WORKSPACE_ID)
        with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
            await service.exchange(_principal(), raw_token)
    else:
        exchange = await service.exchange(_principal(), raw_token)
        workspace_service.allowed_workspaces.remove(WORKSPACE_ID)
        with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
            await service.complete(_principal(), _submission(exchange))

    assert next(iter(store.attempts.values())).consumed_at is None
    assert all(session.consumed_at is None for session in store.sessions.values())
    assert connections._connections == {}
    assert connections._envelopes == {}


@pytest.mark.asyncio
async def test_workspace_membership_tenant_mismatch_fails_without_consumption() -> None:
    workspace_service = FakeWorkspaceService()
    service, store, connections, _ = _service(
        contract=_contract(),
        workspace_service=workspace_service,
    )
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)
    workspace_service.tenant_id = OTHER_TENANT_ID

    with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$"):
        await service.complete(_principal(), _submission(exchange))

    assert next(iter(store.attempts.values())).consumed_at is None
    assert store.sessions[exchange.session_id].consumed_at is None
    assert connections._connections == {}
    assert connections._envelopes == {}


class HeaderPrincipalResolver:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def resolve(self, bearer_token: str) -> MercuryPrincipal:
        self.tokens.append(bearer_token)
        if bearer_token == MERCURY_ACCESS_TOKEN:
            return _principal()
        if bearer_token == "OTHER_USER_TOKEN":
            return _principal(OTHER_USER_ID)
        raise RuntimeError("invalid")


class BrowserSignInHandoff:
    async def sign_in(self, email: str, password: str) -> OAuthSession:
        assert email == "owner@example.com"
        assert password == "browser-password"
        return OAuthSession(
            access_token=SecretStr(MERCURY_ACCESS_TOKEN),
            expires_in=600,
        )


def _client(
    service: PeakSetupService,
    *,
    principal_resolver: HeaderPrincipalResolver | None = None,
    browser_sign_in: bool = False,
) -> TestClient:
    resolver = principal_resolver or HeaderPrincipalResolver()
    dependencies = CloudDependencies(
        settings=_settings(),
        peak_setup_service=service,
    )
    app = Starlette(routes=cloud_routes(dependencies))
    if browser_sign_in:
        session_cookie = OAuthSessionCookie(_settings().vault_active_key)
        consent = MercuryConsent(
            handoff=BrowserSignInHandoff(),
            canonical_resource="https://mercury-tools-mcp.onrender.com/mcp",
            browser_origin=ORIGIN,
            session_cookie=session_cookie,
            additional_session_cookie_paths=(PEAK_SETUP_PATH,),
        )
        app.add_route("/oauth/sign-in", consent.sign_in, methods=["POST"])
    app.add_middleware(
        MercuryOAuthMiddleware,
        principal_resolver=resolver,
        canonical_resource="https://mercury-tools-mcp.onrender.com/mcp",
        peak_browser_session_key=_settings().vault_active_key,
        peak_browser_session_clock=lambda: NOW,
    )
    return TestClient(app, base_url=ORIGIN, raise_server_exceptions=False)


def _browser_sign_in(client: TestClient) -> httpx.Response:
    return client.post(
        "/oauth/sign-in",
        data={
            "authorization_id": "peak_setup_browser_0123456789",
            "email": "owner@example.com",
            "password": "browser-password",
        },
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )


def _hidden_form_value(response: httpx.Response, name: str) -> str:
    return response.text.split(f'name="{name}"', 1)[1].split('value="', 1)[1].split('"', 1)[0]


def _browser_exchange(
    service: PeakSetupService,
    *,
    resolver: HeaderPrincipalResolver | None = None,
) -> tuple[TestClient, HeaderPrincipalResolver, str, httpx.Response]:
    selected_resolver = resolver or HeaderPrincipalResolver()
    client = _client(
        service,
        principal_resolver=selected_resolver,
        browser_sign_in=True,
    )
    signed_in = _browser_sign_in(client)
    assert signed_in.status_code == 303
    assert "Authorization" not in signed_in.request.headers
    assert any(
        f"Path={PEAK_SETUP_PATH}" in cookie for cookie in signed_in.headers.get_list("set-cookie")
    )
    started = asyncio.run(service.start(_principal(), WORKSPACE_ID, "peak", "production"))
    raw_token = urlsplit(started.setup_url).fragment
    page = client.get(f"{PEAK_SETUP_PATH}#{raw_token}")
    response = client.post(
        PEAK_SETUP_EXCHANGE_PATH,
        json={"setup_token": raw_token},
        headers={"Origin": ORIGIN},
    )
    assert page.status_code == 200
    assert raw_token not in page.text
    assert response.status_code == 200
    return client, selected_resolver, raw_token, response


def _browser_submission(response: httpx.Response) -> dict[str, str]:
    return {
        "setup_session": _hidden_form_value(response, "setup_session"),
        "csrf_token": _hidden_form_value(response, "csrf_token"),
        "user_token": USER_TOKEN,
        "connect_id": CONNECT_ID,
        "connect_key": CONNECT_KEY,
    }


def _setup_cookie_value(client: TestClient, name: str) -> str:
    matches = [
        cookie.value
        for cookie in client.cookies.jar
        if cookie.name == name and cookie.path == PEAK_SETUP_PATH
    ]
    assert len(matches) == 1
    return matches[0]


def test_setup_page_clears_fragment_before_network_and_has_strict_browser_policy() -> None:
    service, *_ = _service()
    client = _client(service)

    response = client.get(PEAK_SETUP_PATH)

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "script-src 'sha256-" in csp
    assert "style-src 'sha256-" in csp
    assert "connect-src 'self'" in csp
    assert "form-action 'self'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert response.text.index("history.replaceState") < response.text.index("fetch(")
    lowered = response.text.lower()
    assert "localstorage" not in lowered
    assert "sessionstorage" not in lowered
    assert "analytics" not in lowered
    assert "http://" not in lowered
    assert "https://" not in lowered
    assert "user_token" not in lowered
    assert "connect_id" not in lowered
    assert "connect_key" not in lowered


def test_exchange_requires_origin_same_principal_and_renders_exact_password_form() -> None:
    service, *_ = _service(contract=_contract())
    started = asyncio.run(service.start(_principal(), WORKSPACE_ID, "peak", "production"))
    raw_token = urlsplit(started.setup_url).fragment
    client = _client(service, browser_sign_in=True)
    assert _browser_sign_in(client).status_code == 303
    headers = {"Origin": ORIGIN}

    wrong_origin = client.post(
        PEAK_SETUP_EXCHANGE_PATH,
        json={"setup_token": raw_token},
        headers={**headers, "Origin": "https://attacker.example"},
    )
    wrong_user_cookie = OAuthSessionCookie(_settings().vault_active_key).seal(
        SecretStr("OTHER_USER_TOKEN")
    )
    wrong_user = _client(service).post(
        PEAK_SETUP_EXCHANGE_PATH,
        json={"setup_token": raw_token},
        headers={
            **headers,
            "Cookie": f"{OAUTH_SESSION_COOKIE}={wrong_user_cookie}",
        },
    )
    response = client.post(
        PEAK_SETUP_EXCHANGE_PATH,
        json={"setup_token": raw_token},
        headers=headers,
    )

    assert wrong_origin.status_code == wrong_user.status_code == 400
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Content-Type"].startswith("text/html")
    assert response.text.count('type="hidden"') == 2
    assert response.text.count('type="password"') == 3
    assert 'name="setup_session"' in response.text
    assert 'name="csrf_token"' in response.text
    assert 'name="user_token"' in response.text
    assert 'name="connect_id"' in response.text
    assert 'name="connect_key"' in response.text
    assert "value=" not in response.text.split('name="user_token"', 1)[1].split(">", 1)[0]
    assert raw_token not in response.text
    assert APPLICATION_CODE not in response.text


def test_browser_cookie_lifecycle_completes_without_request_authorization_headers() -> None:
    service, store, connections, _ = _service(contract=_contract())
    resolver = HeaderPrincipalResolver()
    client, resolver, _raw_token, exchange = _browser_exchange(
        service,
        resolver=resolver,
    )

    assert exchange.status_code == 200
    assert "Authorization" not in exchange.request.headers
    assert "HttpOnly" in exchange.headers["set-cookie"]
    assert "Secure" in exchange.headers["set-cookie"]
    assert f"Path={PEAK_SETUP_PATH}" in exchange.headers["set-cookie"]
    assert "SameSite=strict" in exchange.headers["set-cookie"]
    session = next(iter(store.sessions.values()))
    fields = _browser_submission(exchange)

    completed = client.post(
        PEAK_SETUP_PATH,
        data=fields,
        headers={"Origin": ORIGIN},
    )

    assert completed.status_code == 200
    assert "Authorization" not in completed.request.headers
    assert completed.json()["readiness"] == "ready"
    assert resolver.tokens == [MERCURY_ACCESS_TOKEN, MERCURY_ACCESS_TOKEN]
    assert session.consumed_at is None
    assert next(iter(store.sessions.values())).consumed_at == NOW
    assert len(connections._envelopes) == 3
    deleted = completed.headers.get_list("set-cookie")
    assert len(deleted) == 2
    assert any(cookie.startswith(f"{OAUTH_SESSION_COOKIE}=") for cookie in deleted)
    assert any(cookie.startswith(f"{PEAK_SETUP_BROWSER_COOKIE}=") for cookie in deleted)
    assert all("Max-Age=0" in cookie and f"Path={PEAK_SETUP_PATH}" in cookie for cookie in deleted)
    assert all(
        cookie.name not in {OAUTH_SESSION_COOKIE, PEAK_SETUP_BROWSER_COOKIE}
        or cookie.path != PEAK_SETUP_PATH
        for cookie in client.cookies.jar
    )


def test_bearer_only_final_submit_cannot_bypass_browser_session_binding() -> None:
    service, store, connections, validator = _service(contract=_contract())
    started = asyncio.run(service.start(_principal(), WORKSPACE_ID, "peak", "production"))
    exchange = asyncio.run(service.exchange(_principal(), urlsplit(started.setup_url).fragment))
    client = _client(service)

    response = client.post(
        PEAK_SETUP_PATH,
        data={
            "setup_session": exchange.setup_session.get_secret_value(),
            "csrf_token": exchange.csrf_token.get_secret_value(),
            "user_token": USER_TOKEN,
            "connect_id": CONNECT_ID,
            "connect_key": CONNECT_KEY,
        },
        headers={
            "Authorization": f"Bearer {MERCURY_ACCESS_TOKEN}",
            "Origin": ORIGIN,
        },
    )

    assert response.status_code == 401
    assert validator.calls == 0
    assert next(iter(store.attempts.values())).consumed_at is None
    assert store.sessions[exchange.session_id].consumed_at is None
    assert connections._connections == {}
    assert connections._envelopes == {}


@pytest.mark.parametrize("duplicate_name", (OAUTH_SESSION_COOKIE, PEAK_SETUP_BROWSER_COOKIE))
def test_duplicate_peak_browser_cookies_fail_closed_without_consumption(
    duplicate_name: str,
) -> None:
    service, store, connections, validator = _service(contract=_contract())
    client, _resolver, _raw_token, exchange = _browser_exchange(service)
    session_cookie = _setup_cookie_value(client, OAUTH_SESSION_COOKIE)
    binding_cookie = _setup_cookie_value(client, PEAK_SETUP_BROWSER_COOKIE)
    cookies = {
        OAUTH_SESSION_COOKIE: [session_cookie],
        PEAK_SETUP_BROWSER_COOKIE: [binding_cookie],
    }
    cookies[duplicate_name].append(cookies[duplicate_name][0])
    cookie_header = "; ".join(
        f"{name}={value}" for name, values in cookies.items() for value in values
    )
    fresh_client = _client(service)

    response = fresh_client.post(
        PEAK_SETUP_PATH,
        data=_browser_submission(exchange),
        headers={
            "Cookie": cookie_header,
            "Origin": ORIGIN,
        },
    )

    assert response.status_code == 401
    assert validator.calls == 0
    assert next(iter(store.attempts.values())).consumed_at is None
    assert all(session.consumed_at is None for session in store.sessions.values())
    assert connections._connections == {}
    assert connections._envelopes == {}
    assert len(response.headers.get_list("set-cookie")) == 2


@pytest.mark.parametrize(
    ("session_state", "expected_error"),
    (
        ("missing", "mercury_auth_required"),
        ("expired", "mercury_token_invalid"),
    ),
)
def test_missing_or_expired_peak_browser_session_clears_setup_cookies(
    monkeypatch: pytest.MonkeyPatch,
    session_state: str,
    expected_error: str,
) -> None:
    service, store, *_ = _service(contract=_contract())
    started = asyncio.run(service.start(_principal(), WORKSPACE_ID, "peak", "production"))
    raw_token = urlsplit(started.setup_url).fragment
    headers = {"Origin": ORIGIN}
    if session_state == "expired":
        monkeypatch.setattr("mercury_tools.auth.consent.time.time", lambda: 1_000)
        sealed = OAuthSessionCookie(_settings().vault_active_key).seal(
            SecretStr(MERCURY_ACCESS_TOKEN)
        )
        monkeypatch.setattr("mercury_tools.auth.consent.time.time", lambda: 1_601)
        headers["Cookie"] = f"{OAUTH_SESSION_COOKIE}={sealed}"
    client = _client(service)

    response = client.post(
        PEAK_SETUP_EXCHANGE_PATH,
        json={"setup_token": raw_token},
        headers=headers,
    )

    assert response.status_code == 401
    assert response.json() == {"error": expected_error}
    deleted = response.headers.get_list("set-cookie")
    assert len(deleted) == 2
    assert all("Max-Age=0" in cookie and f"Path={PEAK_SETUP_PATH}" in cookie for cookie in deleted)
    assert next(iter(store.attempts.values())).consumed_at is None
    assert store.sessions == {}


def test_final_post_accepts_exact_five_fields_and_never_leaks_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, *_ = _service(contract=_contract())
    client, _resolver, raw_token, exchange = _browser_exchange(service)
    fields = _browser_submission(exchange)
    headers = {"Origin": ORIGIN}
    caplog.set_level(logging.DEBUG)

    extra = client.post(
        PEAK_SETUP_PATH,
        data={**fields, "application_code": "USER_CONTROLLED"},
        headers=headers,
    )
    response = client.post(PEAK_SETUP_PATH, data=fields, headers=headers)

    assert extra.status_code == 400
    assert response.status_code == 200
    assert response.json() == {
        "provider": "peak",
        "merchant_display_name": "PEAK Test Merchant",
        "environment": "production",
        "readiness": "ready",
        "instruction": "Return to the Mercury host to continue.",
    }
    rendered = response.text + extra.text + caplog.text
    assert all(
        sentinel not in rendered
        for sentinel in (
            raw_token,
            USER_TOKEN,
            CONNECT_ID,
            CONNECT_KEY,
            APPLICATION_CODE,
        )
    )


@pytest.mark.parametrize(
    ("path", "canonical", "near_match"),
    (
        (PEAK_SETUP_EXCHANGE_PATH, "application/json", "application/json-patch+json"),
        (
            PEAK_SETUP_PATH,
            "application/x-www-form-urlencoded",
            "application/x-www-form-urlencode",
        ),
    ),
)
def test_peak_post_routes_reject_content_type_parameters_duplicates_and_near_matches(
    path: str,
    canonical: str,
    near_match: str,
) -> None:
    service, *_ = _service(contract=_contract())
    if path == PEAK_SETUP_EXCHANGE_PATH:
        started = asyncio.run(service.start(_principal(), WORKSPACE_ID, "peak", "production"))
        raw_token = urlsplit(started.setup_url).fragment
        body = json.dumps({"setup_token": raw_token})
        base_headers = [("Origin", ORIGIN)]
        client = _client(service, browser_sign_in=True)
        assert _browser_sign_in(client).status_code == 303
    else:
        client, _resolver, _raw_token, exchange = _browser_exchange(service)
        body = urlencode(_browser_submission(exchange))
        base_headers = [("Origin", ORIGIN)]

    parameterized = client.post(
        path,
        content=body,
        headers=[*base_headers, ("Content-Type", f"{canonical}; unexpected=value")],
    )
    duplicated = client.post(
        path,
        content=body,
        headers=[
            *base_headers,
            ("Content-Type", canonical),
            ("Content-Type", canonical),
        ],
    )
    near = client.post(
        path,
        content=body,
        headers=[*base_headers, ("Content-Type", near_match)],
    )
    case_variant = client.post(
        path,
        content=body,
        headers=[*base_headers, ("Content-Type", canonical.upper())],
    )

    assert (
        parameterized.status_code
        == duplicated.status_code
        == near.status_code
        == case_variant.status_code
        == 400
    )


def test_failed_http_validation_returns_blank_retry_form_and_same_session_can_succeed() -> None:
    validator = FakeProfileValidator(failures_remaining=1)
    service, store, connections, _ = _service(
        contract=_contract(),
        validator=validator,
    )
    client, _resolver, _raw_token, exchange = _browser_exchange(service)
    fields = _browser_submission(exchange)
    headers = {"Origin": ORIGIN}

    failed = client.post(PEAK_SETUP_PATH, data=fields, headers=headers)

    assert failed.status_code == 422
    assert failed.headers["Content-Type"].startswith("text/html")
    assert failed.text.count('type="hidden"') == 2
    assert failed.text.count('type="password"') == 3
    assert fields["setup_session"] in failed.text
    assert fields["csrf_token"] in failed.text
    assert all(secret not in failed.text for secret in (USER_TOKEN, CONNECT_ID, CONNECT_KEY))
    assert 'value="' not in failed.text.split('name="user_token"', 1)[1].split(">", 1)[0]
    assert next(iter(store.attempts.values())).consumed_at is None
    assert next(iter(store.sessions.values())).consumed_at is None
    assert "Authorization" not in failed.request.headers
    assert _setup_cookie_value(client, OAUTH_SESSION_COOKIE)
    assert _setup_cookie_value(client, PEAK_SETUP_BROWSER_COOKIE)

    corrected = client.post(PEAK_SETUP_PATH, data=fields, headers=headers)

    assert corrected.status_code == 200
    assert "Authorization" not in corrected.request.headers
    assert corrected.json()["readiness"] == "ready"
    assert validator.calls == 2
    assert next(iter(store.attempts.values())).consumed_at == NOW
    assert len(connections._envelopes) == 3


@pytest.mark.asyncio
async def test_disconnect_deletes_local_envelopes_before_returning_revocation_instruction() -> None:
    service, _store, connections, _ = _service(contract=_contract())
    started = await service.start(_principal(), WORKSPACE_ID, "peak", "production")
    exchange = await service.exchange(_principal(), urlsplit(started.setup_url).fragment)
    summary = await service.complete(_principal(), _submission(exchange))

    result = await service.disconnect(
        _principal(),
        WORKSPACE_ID,
        summary.connection_id,
    )

    assert result.model_dump(mode="json") == {
        "status": "provider_revocation_required",
        "local_credentials_deleted": True,
        "instruction": "Revoke this credential set in PEAK Account.",
    }
    assert connections._envelopes == {}
    disconnected = connections._connections[summary.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is True


def test_supabase_store_lists_exact_provider_binding_before_disconnect() -> None:
    requests: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(
            200,
            json=[
                {
                    "connection_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "provider": "peak",
                    "environment": "production",
                    "account_display_name": "PEAK Test Merchant",
                    "authorization_method": "provider_credentials",
                    "granted_permissions": ["profile.read"],
                    "readiness": "ready",
                    "revision": 1,
                    "last_validated_at": NOW.isoformat(),
                    "provider_revocation_required": False,
                }
            ],
        )

    settings = replace(
        _settings(),
        supabase_url="https://project.example.supabase.co",
        supabase_auth_issuer="https://project.example.supabase.co/auth/v1",
        supabase_service_role_key="SERVICE_ROLE_SENTINEL",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=settings,
            vault=_vault(),
            http_client=client,
        )
        summaries = store.list_for_workspace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
        )

    assert len(summaries) == 1
    assert summaries[0].provider is ProviderId.PEAK
    assert requests == [
        (
            "/rest/v1/rpc/list_mercury_provider_connections_backend",
            {
                "p_tenant_id": str(TENANT_ID),
                "p_workspace_id": str(WORKSPACE_ID),
                "p_auth_user_id": str(USER_ID),
            },
        )
    ]


@pytest.mark.asyncio
async def test_supabase_setup_store_uses_narrow_role_scoped_ciphertext_only_rpcs() -> None:
    attempt_id = UUID("66666666-6666-4666-8666-666666666666")
    session_id = UUID("77777777-7777-4777-8777-777777777777")
    connection_id = UUID("88888888-8888-4888-8888-888888888888")
    token_hash = "a" * 64
    session_hash = "b" * 64
    csrf_hash = "c" * 64
    expires_at = NOW + timedelta(minutes=5)
    requests: list[httpx.Request] = []

    def session_row() -> dict[str, object]:
        return {
            "session_id": str(session_id),
            "setup_attempt_id": str(attempt_id),
            "tenant_id": str(TENANT_ID),
            "workspace_id": str(WORKSPACE_ID),
            "auth_user_id": str(USER_ID),
            "provider": "peak",
            "environment": "production",
            "expires_at": expires_at.isoformat(),
            "consumed_at": None,
            "created_at": NOW.isoformat(),
        }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith("/rpc/create_mercury_provider_setup_attempt"):
            row = {
                "attempt_id": str(attempt_id),
                "tenant_id": str(TENANT_ID),
                "workspace_id": str(WORKSPACE_ID),
                "auth_user_id": str(USER_ID),
                "provider": "peak",
                "environment": "production",
                "expires_at": expires_at.isoformat(),
                "consumed_at": None,
                "created_at": NOW.isoformat(),
            }
        elif path.endswith("/rpc/finalize_mercury_peak_setup"):
            row = {
                "connection_id": str(connection_id),
                "revision": 1,
                "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(),
            }
        else:
            row = session_row()
        return httpx.Response(200, json=[row], request=request)

    settings = replace(
        _settings(),
        supabase_url="https://project.example.supabase.co",
        supabase_auth_issuer="https://project.example.supabase.co/auth/v1",
        supabase_publishable_key="PUBLISHABLE_SENTINEL",
        supabase_service_role_key="SERVICE_ROLE_SENTINEL",
    )
    connection = ProviderConnection(
        id=connection_id,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        provider=ProviderId.PEAK,
        environment="production",
        provider_account_id="merchant-123",
        account_display_name="PEAK Test Merchant",
        authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS,
        granted_permissions=("profile.read",),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=NOW,
        credential_envelope_ids=(
            UUID("99999999-9999-4999-8999-999999999991"),
            UUID("99999999-9999-4999-8999-999999999992"),
            UUID("99999999-9999-4999-8999-999999999993"),
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    material = PeakCredentialMaterial.from_values(
        user_token=USER_TOKEN,
        connect_id=CONNECT_ID,
        connect_key=CONNECT_KEY,
    )
    envelopes = seal_peak_credentials(
        vault=_vault(),
        connection=connection,
        credentials=material,
    )
    material.clear()
    connection = ProviderConnection.model_validate(
        connection.model_copy(
            update={"credential_envelope_ids": tuple(item.id for item in envelopes)}
        )
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = SupabasePeakSetupStore(settings=settings, http_client=client)
        attempt = await store.create_attempt(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.PEAK,
            environment="production",
            token_hash=token_hash,
            expires_at=expires_at,
            mercury_access_token=MERCURY_ACCESS_TOKEN,
            attempt_id=attempt_id,
        )
        exchanged = await store.exchange_attempt(
            session_id=session_id,
            auth_user_id=USER_ID,
            token_hash=token_hash,
            session_hash=session_hash,
            csrf_hash=csrf_hash,
            mercury_access_token=MERCURY_ACCESS_TOKEN,
        )
        peeked = await store.peek_session(
            auth_user_id=USER_ID,
            session_hash=session_hash,
        )
        finalized = await store.finalize(
            session=peeked,
            session_hash=session_hash,
            csrf_hash=csrf_hash,
            connection=connection,
            envelopes=envelopes,
        )

    assert attempt.id == attempt_id
    assert exchanged == PeakSetupSessionRecord(
        id=session_id,
        setup_attempt_id=attempt_id,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        provider=ProviderId.PEAK,
        environment="production",
        session_hash=session_hash,
        csrf_hash=csrf_hash,
        expires_at=expires_at,
        consumed_at=None,
        created_at=NOW,
    )
    assert finalized == connection
    assert [request.url.path for request in requests] == [
        "/rest/v1/rpc/create_mercury_provider_setup_attempt",
        "/rest/v1/rpc/exchange_mercury_peak_setup_attempt",
        "/rest/v1/rpc/peek_mercury_peak_setup_session",
        "/rest/v1/rpc/finalize_mercury_peak_setup",
    ]
    assert all(not request.url.query for request in requests)
    assert [request.headers["apikey"] for request in requests] == [
        "PUBLISHABLE_SENTINEL",
        "PUBLISHABLE_SENTINEL",
        "SERVICE_ROLE_SENTINEL",
        "SERVICE_ROLE_SENTINEL",
    ]
    assert [request.headers["authorization"] for request in requests] == [
        f"Bearer {MERCURY_ACCESS_TOKEN}",
        f"Bearer {MERCURY_ACCESS_TOKEN}",
        "Bearer SERVICE_ROLE_SENTINEL",
        "Bearer SERVICE_ROLE_SENTINEL",
    ]
    rendered_requests = " ".join(request.content.decode("utf-8") for request in requests)
    assert all(
        sentinel not in rendered_requests
        for sentinel in (USER_TOKEN, CONNECT_ID, CONNECT_KEY, APPLICATION_CODE)
    )


@pytest.mark.asyncio
async def test_supabase_setup_store_rejects_mismatched_backend_binding_safely() -> None:
    settings = replace(
        _settings(),
        supabase_url="https://project.example.supabase.co",
        supabase_auth_issuer="https://project.example.supabase.co/auth/v1",
        supabase_publishable_key="PUBLISHABLE_SENTINEL",
        supabase_service_role_key="SERVICE_ROLE_SENTINEL",
    )
    attempt_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "attempt_id": str(attempt_id),
                    "tenant_id": str(TENANT_ID),
                    "workspace_id": str(WORKSPACE_ID),
                    "auth_user_id": str(OTHER_USER_ID),
                    "provider": "peak",
                    "environment": "production",
                    "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
                    "consumed_at": None,
                    "created_at": NOW.isoformat(),
                }
            ],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = SupabasePeakSetupStore(settings=settings, http_client=client)
        with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$") as error:
            await store.create_attempt(
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.PEAK,
                environment="production",
                token_hash="d" * 64,
                expires_at=NOW + timedelta(minutes=5),
                mercury_access_token=MERCURY_ACCESS_TOKEN,
                attempt_id=attempt_id,
            )

    assert error.value.__cause__ is None


@pytest.mark.asyncio
async def test_supabase_setup_store_drops_raw_network_exception_context() -> None:
    settings = replace(
        _settings(),
        supabase_url="https://project.example.supabase.co",
        supabase_auth_issuer="https://project.example.supabase.co/auth/v1",
        supabase_publishable_key="PUBLISHABLE_SENTINEL",
        supabase_service_role_key="SERVICE_ROLE_SENTINEL",
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        raise RuntimeError(USER_TOKEN)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        store = SupabasePeakSetupStore(settings=settings, http_client=client)
        with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$") as error:
            await store.peek_session(
                auth_user_id=USER_ID,
                session_hash="e" * 64,
            )
        with pytest.raises(PeakSetupError, match="^peak_setup_state_invalid$") as bearer_error:
            await store.create_attempt(
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.PEAK,
                environment="production",
                token_hash="d" * 64,
                expires_at=NOW + timedelta(minutes=5),
                mercury_access_token=MERCURY_ACCESS_TOKEN,
                attempt_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
            )

    assert USER_TOKEN not in f"{error.value!s} {error.value!r}"
    _assert_no_internal_secret_references(
        error.value,
        USER_TOKEN,
        "PUBLISHABLE_SENTINEL",
        "SERVICE_ROLE_SENTINEL",
    )
    _assert_no_internal_secret_references(
        bearer_error.value,
        USER_TOKEN,
        MERCURY_ACCESS_TOKEN,
        "PUBLISHABLE_SENTINEL",
        "SERVICE_ROLE_SENTINEL",
    )


def test_setup_public_models_and_errors_hide_all_secret_inputs() -> None:
    submission = PeakSetupSubmission(
        setup_session="A" * 43,
        csrf_token="B" * 43,
        user_token=USER_TOKEN,
        connect_id=CONNECT_ID,
        connect_key=CONNECT_KEY,
    )
    error = PeakSetupError("peak_setup_state_invalid")

    rendered = f"{submission!r} {submission.model_dump_json()} {error!r} {error}"

    assert all(sentinel not in rendered for sentinel in (USER_TOKEN, CONNECT_ID, CONNECT_KEY))
    assert str(error) == "peak_setup_state_invalid"
