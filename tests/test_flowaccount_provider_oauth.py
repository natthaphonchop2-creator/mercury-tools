from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mercury_tools.auth.middleware import MercuryOAuthMiddleware
from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.cloud.api import CloudDependencies, cloud_routes
from mercury_tools.config import Settings
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderDiscovery,
    ProviderStatusClass,
    ProviderValidation,
)
from mercury_tools.providers.flowaccount import FlowAccountOAuthTokens
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import (
    ConnectionReadiness,
    ProviderConnectionSummary,
    ProviderId,
)
from mercury_tools.providers.oauth import (
    FLOWACCOUNT_CALLBACK_PATH,
    DownstreamMCPOAuthClient,
    InMemoryProviderOAuthStateStore,
    OAuthAuthorizationSession,
    OAuthCallback,
    ProviderOAuthError,
    ProviderOAuthService,
)
from mercury_tools.providers.store import ProviderConnectionStore
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_provider_manifest(ROOT / "catalog/global/flowaccount/driver.json")
NOW = datetime(2026, 7, 27, 3, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
OTHER_WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
USER_ID = UUID("44444444-4444-4444-8444-444444444444")
OTHER_USER_ID = UUID("55555555-5555-4555-8555-555555555555")
CALLBACK_URI = "https://mercury-tools-mcp.onrender.com/auth/providers/flowaccount/callback"
RESOURCE_URI = "https://flowaccount-sandbox.example/mcp"
AUTHORIZATION_SERVER = "https://identity.flowaccount.example/oauth"
AUTHORIZATION_ENDPOINT = "https://identity.flowaccount.example/oauth/authorize"
TOKEN_ENDPOINT = "https://identity.flowaccount.example/oauth/token"
REGISTRATION_ENDPOINT = "https://identity.flowaccount.example/oauth/register"
MERCURY_ACCESS_TOKEN = "MERCURY_USER_ACCESS_TOKEN_SENTINEL"
PROVIDER_ACCESS_TOKEN = "FLOWACCOUNT_ACCESS_TOKEN_SENTINEL"
PROVIDER_REFRESH_TOKEN = "FLOWACCOUNT_REFRESH_TOKEN_SENTINEL"
DYNAMIC_CLIENT_SECRET = "FLOWACCOUNT_DYNAMIC_CLIENT_SECRET_SENTINEL"


def _settings() -> Settings:
    return Settings(
        supabase_url="https://project.example.supabase.co",
        supabase_service_role_key="service-role",
        openai_api_key="",
        supabase_publishable_key="publishable",
        supabase_auth_issuer="https://project.example.supabase.co/auth/v1",
        flowaccount_mcp_sandbox_url=RESOURCE_URI,
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
        provider_callback_base_url="https://mercury-tools-mcp.onrender.com",
    )


def _principal(subject: UUID = USER_ID) -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=subject,
        client_id="mercury-test-client",
        scopes=frozenset({"openid", "email", "profile"}),
        token_id="mercury-token-id",
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
        self._next = 1

    def __call__(self, size: int) -> bytes:
        value = bytes([self._next]) * size
        self._next += 1
        return value


class FakeWorkspaceService:
    def __init__(self) -> None:
        self.calls: list[tuple[UUID, str, UUID, WorkspaceRole]] = []

    def require_workspace(
        self,
        principal: MercuryPrincipal,
        access_token: str,
        workspace_id: UUID,
        required_role: WorkspaceRole,
    ) -> WorkspaceMembership:
        self.calls.append((principal.subject, access_token, workspace_id, required_role))
        return WorkspaceMembership(
            tenant_id=TENANT_ID,
            tenant_display_name="Mercury Test Tenant",
            workspace_id=workspace_id,
            workspace_display_name="Mercury Test Workspace",
            role=WorkspaceRole.OWNER,
        )


class FakePrincipalResolver:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def resolve(self, bearer_token: str) -> MercuryPrincipal:
        self.tokens.append(bearer_token)
        if bearer_token != MERCURY_ACCESS_TOKEN:
            raise RuntimeError("invalid")
        return _principal()


class FakeOAuthClient:
    def __init__(self) -> None:
        self.starts: list[dict[str, object]] = []
        self.exchanges: list[dict[str, object]] = []

    async def start_authorization(
        self,
        *,
        resource_uri: str,
        callback_uri: str,
        allowed_permissions: tuple[str, ...],
        state: str,
        code_challenge: str,
    ) -> OAuthAuthorizationSession:
        self.starts.append(
            {
                "resource_uri": resource_uri,
                "callback_uri": callback_uri,
                "allowed_permissions": allowed_permissions,
                "state": state,
                "code_challenge": code_challenge,
            }
        )
        authorization_url = httpx.URL(AUTHORIZATION_ENDPOINT).copy_merge_params(
            {
                "response_type": "code",
                "client_id": "dynamic-client-id",
                "redirect_uri": callback_uri,
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "documents.read profile.read",
                "resource": resource_uri,
            }
        )
        return OAuthAuthorizationSession(
            authorization_url=str(authorization_url),
            resource_uri=resource_uri,
            authorization_endpoint=AUTHORIZATION_ENDPOINT,
            token_endpoint=TOKEN_ENDPOINT,
            callback_uri=callback_uri,
            client_id="dynamic-client-id",
            client_secret=DYNAMIC_CLIENT_SECRET,
            token_endpoint_auth_method="client_secret_basic",
            granted_permissions=("documents.read", "profile.read"),
        )

    async def exchange_code(
        self,
        *,
        session: OAuthAuthorizationSession,
        code: str,
        code_verifier: str,
    ) -> FlowAccountOAuthTokens:
        self.exchanges.append(
            {
                "session": session,
                "code": code,
                "code_verifier": code_verifier,
            }
        )
        return FlowAccountOAuthTokens(
            access_token=PROVIDER_ACCESS_TOKEN,
            refresh_token=PROVIDER_REFRESH_TOKEN,
            expires_at=NOW + timedelta(hours=1),
            granted_permissions=session.granted_permissions,
        )


class FakeFlowAccountDriver:
    provider = ProviderId.FLOWACCOUNT

    def __init__(
        self,
        *,
        profile_company_id: str = "company-123",
        discovery_capabilities: tuple[str, ...] = (
            "documents.invoice.list",
            "provider_profile.get",
        ),
    ) -> None:
        self.profile_company_id = profile_company_id
        self.discovery_capabilities = discovery_capabilities
        self.events: list[str] = []

    async def discover(self, _connection) -> ProviderDiscovery:
        self.events.append("discover")
        return ProviderDiscovery(
            provider=ProviderId.FLOWACCOUNT,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "capabilities": list(self.discovery_capabilities),
                "resource_uri_sha256": hashlib.sha256(RESOURCE_URI.encode("utf-8")).hexdigest(),
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )

    async def validate_connection(self, _connection) -> ProviderValidation:
        self.events.append("provider_profile.get")
        return ProviderValidation(
            provider=ProviderId.FLOWACCOUNT,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "company_id": self.profile_company_id,
                "company_display_name": "FlowAccount Test Company",
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )


class RecordingConnectionStore:
    def __init__(self, store: ProviderConnectionStore) -> None:
        self.store = store
        self.saved: list[dict[str, object]] = []

    def save_connection(self, **kwargs):
        self.saved.append(dict(kwargs))
        return self.store.save_connection(**kwargs)


def _service(
    *,
    clock: Callable[[], datetime] | None = None,
    driver: FakeFlowAccountDriver | None = None,
) -> tuple[
    ProviderOAuthService,
    FakeOAuthClient,
    FakeFlowAccountDriver,
    InMemoryProviderOAuthStateStore,
    RecordingConnectionStore,
    CredentialVault,
]:
    active_clock = clock or (lambda: NOW)
    vault = _vault()
    raw_store = ProviderConnectionStore(vault=vault, clock=active_clock)
    state_store = InMemoryProviderOAuthStateStore(
        provider_store=raw_store,
        clock=active_clock,
    )
    connection_store = RecordingConnectionStore(raw_store)
    oauth_client = FakeOAuthClient()
    provider_driver = driver or FakeFlowAccountDriver()
    service = ProviderOAuthService(
        settings=_settings(),
        workspace_service=FakeWorkspaceService(),
        mercury_access_token=lambda _principal: MERCURY_ACCESS_TOKEN,
        principal_resolver=FakePrincipalResolver(),
        manifest=MANIFEST,
        oauth_client=oauth_client,
        state_store=state_store,
        connection_store=connection_store,
        vault=vault,
        driver=provider_driver,
        clock=active_clock,
        random_bytes=FixedRandom(),
    )
    return (
        service,
        oauth_client,
        provider_driver,
        state_store,
        connection_store,
        vault,
    )


@pytest.mark.asyncio
async def test_start_uses_256_bit_state_s256_pkce_and_exact_ten_minute_binding() -> None:
    service, oauth_client, *_ = _service()

    first = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    second = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )

    first_query = parse_qs(urlsplit(first.authorization_url).query)
    second_query = parse_qs(urlsplit(second.authorization_url).query)
    first_state = first_query["state"][0]
    state_bytes = base64.urlsafe_b64decode(first_state + "=")
    verifier_bytes = bytes([2]) * 32
    expected_challenge = (
        base64.urlsafe_b64encode(
            hashlib.sha256(base64.urlsafe_b64encode(verifier_bytes).rstrip(b"=")).digest()
        )
        .rstrip(b"=")
        .decode("ascii")
    )

    assert len(state_bytes) == 32
    assert first_state != second_query["state"][0]
    assert first.expires_at == NOW + timedelta(minutes=10)
    assert first.provider is ProviderId.FLOWACCOUNT
    assert first.environment == "sandbox"
    assert first_query["redirect_uri"] == [CALLBACK_URI]
    assert first_query["code_challenge_method"] == ["S256"]
    assert first_query["code_challenge"] == [expected_challenge]
    assert first_query["scope"] == ["documents.read profile.read"]
    assert "code_verifier" not in first_query
    assert "access_token" not in first.authorization_url
    assert oauth_client.starts[0]["resource_uri"] == RESOURCE_URI
    assert oauth_client.starts[0]["allowed_permissions"] == (
        "documents.create",
        "documents.read",
        "profile.read",
    )


@pytest.mark.asyncio
async def test_strict_downstream_discovery_starts_at_configured_resource_and_intersects_scope() -> (
    None
):
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if str(request.url) == RESOURCE_URI:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="'
                        "https://flowaccount-sandbox.example/"
                        '.well-known/oauth-protected-resource/mcp", '
                        'scope="profile.read documents.read admin.full"'
                    )
                },
            )
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={
                    "resource": RESOURCE_URI,
                    "authorization_servers": [AUTHORIZATION_SERVER],
                    "scopes_supported": [
                        "profile.read",
                        "documents.read",
                        "admin.full",
                    ],
                    "bearer_methods_supported": ["header"],
                },
            )
        if request.url.path == "/.well-known/oauth-authorization-server/oauth":
            return httpx.Response(
                200,
                json={
                    "issuer": AUTHORIZATION_SERVER,
                    "authorization_endpoint": AUTHORIZATION_ENDPOINT,
                    "token_endpoint": TOKEN_ENDPOINT,
                    "registration_endpoint": REGISTRATION_ENDPOINT,
                    "response_types_supported": ["code"],
                    "grant_types_supported": [
                        "authorization_code",
                        "refresh_token",
                    ],
                    "token_endpoint_auth_methods_supported": [
                        "client_secret_basic",
                    ],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if str(request.url) == REGISTRATION_ENDPOINT:
            assert request.json() if False else True
            return httpx.Response(
                201,
                json={
                    "client_id": "dynamic-client-id",
                    "client_secret": DYNAMIC_CLIENT_SECRET,
                    "redirect_uris": [CALLBACK_URI],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "client_secret_basic",
                },
            )
        return httpx.Response(404)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(http_client=client)
        session = await oauth_client.start_authorization(
            resource_uri=RESOURCE_URI,
            callback_uri=CALLBACK_URI,
            allowed_permissions=MANIFEST.allowed_permissions,
            state="A" * 43,
            code_challenge="B" * 43,
        )

    query = parse_qs(urlsplit(session.authorization_url).query)
    assert calls[0] == ("GET", RESOURCE_URI)
    assert calls[1][1] == (
        "https://flowaccount-sandbox.example/.well-known/oauth-protected-resource/mcp"
    )
    assert calls[2][1] == (
        "https://identity.flowaccount.example/.well-known/oauth-authorization-server/oauth"
    )
    assert calls[3] == ("POST", REGISTRATION_ENDPOINT)
    assert query["scope"] == ["documents.read profile.read"]
    assert query["redirect_uri"] == [CALLBACK_URI]
    assert session.client_secret == DYNAMIC_CLIENT_SECRET
    assert repr(session).find(DYNAMIC_CLIENT_SECRET) == -1


@pytest.mark.asyncio
async def test_callback_encrypts_credentials_consumes_verifier_and_requires_both_validations() -> (
    None
):
    service, oauth_client, driver, _state_store, connection_store, vault = _service()
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        code="AUTHORIZATION_CODE_SENTINEL",
        state=state,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
        selected_company_id="company-123",
    )

    summary = await service.complete(_principal(), callback)

    assert summary.readiness is ConnectionReadiness.READY
    assert summary.account_display_name == "FlowAccount Test Company"
    assert summary.granted_permissions == ("documents.read", "profile.read")
    assert driver.events == ["discover", "provider_profile.get"]
    assert len(oauth_client.exchanges) == 1
    assert len(connection_store.saved) == 2
    assert connection_store.saved[0]["readiness"] is ConnectionReadiness.REQUIRES_VALIDATION
    assert connection_store.saved[1]["readiness"] is ConnectionReadiness.READY
    credential_types = {
        envelope.credential_type for envelope in connection_store.saved[-1]["envelopes"]
    }
    assert credential_types == {
        "access_token",
        "client_secret",
        "oauth_token_bundle",
        "refresh_token",
    }
    serialized_envelopes = repr(connection_store.saved[-1]["envelopes"])
    for secret in (
        PROVIDER_ACCESS_TOKEN,
        PROVIDER_REFRESH_TOKEN,
        DYNAMIC_CLIENT_SECRET,
        MERCURY_ACCESS_TOKEN,
        "AUTHORIZATION_CODE_SENTINEL",
    ):
        assert secret not in serialized_envelopes

    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete(_principal(), callback)

    # The encrypted envelopes remain authenticated against the selected company.
    saved_connection = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )[0]
    assert saved_connection.connection_id == summary.connection_id
    assert vault is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda callback: callback.model_copy(update={"workspace_id": OTHER_WORKSPACE_ID}),
            "provider_oauth_state_invalid",
        ),
        (
            lambda callback: callback.model_copy(update={"environment": "production"}),
            "provider_oauth_state_invalid",
        ),
        (
            lambda callback: callback.model_copy(
                update={
                    "redirect_uri": (
                        "https://mercury-tools-mcp.onrender.com/"
                        "auth/providers/flowaccount/callback/extra"
                    )
                }
            ),
            "provider_oauth_callback_invalid",
        ),
        (
            lambda callback: callback.model_copy(update={"state": "Z" * 43}),
            "provider_oauth_state_invalid",
        ),
    ],
)
async def test_callback_rejects_wrong_workspace_environment_redirect_or_state(
    mutation: Callable[[OAuthCallback], OAuthCallback],
    expected_code: str,
) -> None:
    service, *_ = _service()
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        code="authorization-code",
        state=state,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
        selected_company_id="company-123",
    )

    with pytest.raises(ProviderOAuthError, match=f"^{expected_code}$"):
        await service.complete(_principal(), mutation(callback))


@pytest.mark.asyncio
async def test_callback_rejects_wrong_user_expiry_and_provider_company_mismatch() -> None:
    clock = [NOW]
    service, *_ = _service(clock=lambda: clock[0])
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        code="authorization-code",
        state=state,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
        selected_company_id="company-123",
    )

    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete(_principal(OTHER_USER_ID), callback)

    clock[0] = NOW + timedelta(minutes=10, microseconds=1)
    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete(_principal(), callback)

    mismatch_service, *_ = _service(
        driver=FakeFlowAccountDriver(profile_company_id="company-other")
    )
    mismatch_start = await mismatch_service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    mismatch_state = parse_qs(urlsplit(mismatch_start.authorization_url).query)["state"][0]
    with pytest.raises(
        ProviderOAuthError,
        match="^provider_oauth_company_mismatch$",
    ):
        await mismatch_service.complete(
            _principal(),
            callback.model_copy(update={"state": mismatch_state}),
        )


class CallbackService:
    def __init__(self) -> None:
        self.callbacks: list[OAuthCallback] = []

    async def complete_callback(
        self,
        callback: OAuthCallback,
    ) -> ProviderConnectionSummary:
        self.callbacks.append(callback)
        return ProviderConnectionSummary(
            connection_id=UUID("66666666-6666-4666-8666-666666666666"),
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            account_display_name="FlowAccount Test Company",
            authorization_method="oauth2_pkce",
            granted_permissions=("profile.read",),
            readiness=ConnectionReadiness.READY,
            revision=2,
            last_validated_at=NOW,
            provider_revocation_required=False,
        )


class NeverResolvePrincipal:
    async def resolve(self, _bearer_token: str) -> MercuryPrincipal:
        raise AssertionError("exact callback must not require a Mercury bearer")


def test_exact_callback_is_public_and_returns_only_safe_fields() -> None:
    callback_service = CallbackService()
    dependencies = CloudDependencies(
        settings=_settings(),
        provider_oauth_service=callback_service,
    )
    app = Starlette(routes=cloud_routes(dependencies))
    app.add_middleware(
        MercuryOAuthMiddleware,
        principal_resolver=NeverResolvePrincipal(),
        canonical_resource="https://mercury-tools-mcp.onrender.com/mcp",
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={
            "code": "AUTHORIZATION_CODE_SENTINEL",
            "state": "A" * 43,
            "company_id": "company-123",
        },
    )
    sibling = client.get(f"{FLOWACCOUNT_CALLBACK_PATH}/extra")
    other_provider = client.get("/auth/providers/peak/callback")

    assert response.status_code == 200
    assert response.json() == {
        "provider": "flowaccount",
        "company_display_name": "FlowAccount Test Company",
        "environment": "sandbox",
        "readiness": "ready",
        "instruction": "Return to the Mercury host to continue.",
    }
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert sibling.status_code == other_provider.status_code == 401
    assert "AUTHORIZATION_CODE_SENTINEL" not in response.text
    assert "A" * 43 not in response.text


def test_oauth_models_errors_and_repr_do_not_retain_secrets() -> None:
    callback = OAuthCallback(
        code="AUTHORIZATION_CODE_SENTINEL",
        state="A" * 43,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
        selected_company_id="company-123",
    )
    error = ProviderOAuthError("provider_oauth_state_invalid")

    rendered = (
        repr(callback),
        callback.model_dump_json(),
        repr(error),
        str(error),
    )
    assert all("AUTHORIZATION_CODE_SENTINEL" not in item for item in rendered)
    assert all("A" * 43 not in item for item in rendered)
    assert str(error) == "provider_oauth_state_invalid"
