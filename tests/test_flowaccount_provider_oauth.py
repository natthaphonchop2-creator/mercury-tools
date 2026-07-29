from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import anyio
import httpx
import pytest
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.testclient import TestClient

from mercury_tools.auth.middleware import MercuryOAuthMiddleware
from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.cloud.api import CloudDependencies, cloud_routes
from mercury_tools.config import Settings, V1ConfigurationError
from mercury_tools.credentials.models import CredentialBinding
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderDiscovery,
    ProviderStatusClass,
    ProviderValidation,
)
from mercury_tools.providers.flowaccount import (
    FlowAccountOAuthHeaderFactory,
    FlowAccountOAuthRevocationMaterial,
    FlowAccountOAuthTokens,
    FlowAccountRefreshRequest,
    seal_flowaccount_credentials,
)
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
    PublicOAuthNetworkGuard,
    SupabaseProviderOAuthStateStore,
)
from mercury_tools.providers.store import ProviderConnectionStore, ProviderStoreError
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
REVOCATION_ENDPOINT = "https://identity.flowaccount.example/oauth/revoke"
MERCURY_ACCESS_TOKEN = "MERCURY_USER_ACCESS_TOKEN_SENTINEL"
PROVIDER_ACCESS_TOKEN = "FLOWACCOUNT_ACCESS_TOKEN_SENTINEL"
PROVIDER_REFRESH_TOKEN = "FLOWACCOUNT_REFRESH_TOKEN_SENTINEL"
DYNAMIC_CLIENT_SECRET = "FLOWACCOUNT_DYNAMIC_CLIENT_SECRET_SENTINEL"
AUTHORIZATION_SERVER_ORIGINS = {
    (ProviderId.FLOWACCOUNT, "sandbox"): ("https://identity.flowaccount.example",),
    (ProviderId.FLOWACCOUNT, "production"): ("https://identity.flowaccount.example",),
}


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
        self.revocations = 0

    async def start_authorization(
        self,
        *,
        provider: ProviderId,
        environment: str,
        resource_uri: str,
        callback_uri: str,
        allowed_permissions: tuple[str, ...],
        state: str,
        code_challenge: str,
    ) -> OAuthAuthorizationSession:
        self.starts.append(
            {
                "provider": provider,
                "environment": environment,
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
            revocation_endpoint=REVOCATION_ENDPOINT,
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
            token_type="Bearer",
            expires_at=NOW + timedelta(hours=1),
            granted_permissions=session.granted_permissions,
        )

    async def revoke(
        self,
        *,
        session: OAuthAuthorizationSession,
        tokens: FlowAccountOAuthTokens,
    ) -> bool:
        assert session.revocation_endpoint == REVOCATION_ENDPOINT
        assert tokens.token_type == "Bearer"
        self.revocations += 1
        return True


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
        validation_error: bool = False,
    ) -> None:
        self.profile_company_id = profile_company_id
        self.discovery_capabilities = discovery_capabilities
        self.validation_error = validation_error
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
        if self.validation_error:
            raise RuntimeError("retryable downstream validation failure")
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
        self.events: list[str] = []

    def save_connection(self, **kwargs):
        self.saved.append(dict(kwargs))
        return self.store.save_connection(**kwargs)

    def begin_oauth_attempt(self, **kwargs):
        self.events.append("begin_attempt")
        return self.store.begin_oauth_attempt(**kwargs)

    def attach_oauth_attempt(self, **kwargs):
        self.events.append("attach_attempt")
        self.saved.append(dict(kwargs))
        return self.store.attach_oauth_attempt(**kwargs)

    def finalize_oauth_attempt(self, **kwargs):
        self.events.append("finalize_attempt")
        self.saved.append(dict(kwargs))
        return self.store.finalize_oauth_attempt(**kwargs)

    def acknowledge_oauth_attempt(self, **kwargs):
        self.events.append("acknowledge_attempt")
        return self.store.acknowledge_oauth_attempt(**kwargs)

    def fail_oauth_attempt(self, **kwargs):
        self.events.append("fail_attempt")
        return self.store.fail_oauth_attempt(**kwargs)

    def load_oauth_attempt_envelopes(self, **kwargs):
        self.events.append("load_attempt_material")
        return self.store.load_oauth_attempt_envelopes(**kwargs)

    def load_runtime_envelopes(self, connection):
        self.events.append("load_runtime_material")
        return self.store.load_runtime_envelopes(connection)

    def replace_oauth_attempt_envelopes(self, connection, envelopes):
        self.events.append("replace_attempt_material")
        return self.store.replace_oauth_attempt_envelopes(connection, envelopes)

    def complete_oauth_attempt_revocation(self, **kwargs):
        self.events.append("complete_attempt_revocation")
        return self.store.complete_oauth_attempt_revocation(**kwargs)

    def stage_connection(self, **kwargs):
        self.events.append("stage")
        self.saved.append(dict(kwargs))
        return self.store.stage_connection(**kwargs)

    def resolve_connection_target(self, **kwargs):
        self.events.append("resolve_target")
        return self.store.resolve_connection_target(**kwargs)

    def finalize_connection(self, **kwargs):
        self.events.append("finalize")
        self.saved.append(dict(kwargs))
        return self.store.finalize_connection(**kwargs)

    def record_revocation_obligation(self, **kwargs):
        self.events.append("record_revocation_obligation")
        return self.store.record_revocation_obligation(**kwargs)

    def disconnect(self, **kwargs):
        self.events.append("disconnect")
        return self.store.disconnect(**kwargs)

    def complete_revocation(self, **kwargs):
        self.events.append("complete_revocation")
        return self.store.complete_revocation(**kwargs)

    def load_connection(self, **kwargs):
        self.events.append("load_connection")
        return self.store.load_connection(**kwargs)


class BlockingDisconnectStore:
    """Block an awaitable local disconnect before it mutates durable state."""

    def __init__(self, store: RecordingConnectionStore) -> None:
        self._store = store
        self.disconnect_started = anyio.Event()
        self.allow_disconnect = anyio.Event()
        self.disconnect_completed = False

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def disconnect(self, **kwargs):
        self.disconnect_started.set()
        await self.allow_disconnect.wait()
        result = self._store.disconnect(**kwargs)
        self.disconnect_completed = True
        return result


class BlockingCompleteRevocationStore:
    """Block an awaitable revocation completion before it mutates durable state."""

    def __init__(self, store: RecordingConnectionStore) -> None:
        self._store = store
        self.completion_started = anyio.Event()
        self.allow_completion = anyio.Event()
        self.completion_completed = False

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def complete_revocation(self, **kwargs):
        self.completion_started.set()
        await self.allow_completion.wait()
        result = self._store.complete_revocation(**kwargs)
        self.completion_completed = True
        return result


def _revocation_material_is_cleared(
    material: FlowAccountOAuthRevocationMaterial,
) -> bool:
    return all(
        getattr(material, field_name) is None
        for field_name in (
            "tokens",
            "token_endpoint",
            "resource_uri",
            "revocation_endpoint",
            "client_id",
            "client_secret",
            "token_endpoint_auth_method",
            "granted_permissions",
        )
    )


class CoordinatedDisconnectStore:
    """Coordinate two services after both have loaded the ready connection."""

    def __init__(self, store: RecordingConnectionStore) -> None:
        self._store = store
        self._load_count = 0
        self._runtime_envelopes = None
        self.initial_loads_ready = asyncio.Event()
        self.failure_reconciliation_started = asyncio.Event()
        self.success_completed = asyncio.Event()

    def __getattr__(self, name: str):
        return getattr(self._store, name)

    async def load_connection(self, **kwargs):
        self._load_count += 1
        if self._load_count <= 2:
            loaded = self._store.load_connection(**kwargs)
            if self._load_count == 2:
                self.initial_loads_ready.set()
            await self.initial_loads_ready.wait()
            return loaded
        elif self._load_count == 3:
            self.failure_reconciliation_started.set()
            await self.success_completed.wait()
        return self._store.load_connection(**kwargs)

    def complete_revocation(self, **kwargs):
        result = self._store.complete_revocation(**kwargs)
        self.success_completed.set()
        return result

    def load_runtime_envelopes(self, connection):
        if self._runtime_envelopes is None:
            self._runtime_envelopes = self._store.load_runtime_envelopes(connection)
        return self._runtime_envelopes


class SharedOAuthStateRPCBackend:
    """Deterministic local model of the Task 4 create/consume RPC contract."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.attempts: dict[str, dict[str, object]] = {}
        self.states: dict[str, dict[str, object]] = {}
        self.authorization_headers: list[tuple[str, str]] = []
        self.consume_successes = 0
        self.consume_failures = 0
        self.cancel_successes = 0
        self.cancel_failures = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.authorization_headers.append((path, request.headers.get("authorization", "")))
        if path.endswith("/rpc/create_mercury_provider_setup_attempt"):
            return self._create_attempt(payload)
        if path.endswith("/rpc/create_mercury_provider_oauth_state"):
            return self._create_state(payload)
        if path.endswith("/rpc/consume_mercury_provider_oauth_state"):
            return self._consume_state(payload)
        if path.endswith("/rpc/cancel_mercury_provider_oauth_state"):
            return self._cancel_state(payload)
        if path.endswith("/rpc/cleanup_expired_mercury_provider_oauth_states"):
            return self._cleanup_states(payload)
        if path.endswith("/mercury_provider_oauth_states"):
            return self._peek_state(request)
        return httpx.Response(404, json={"error": "unexpected"})

    def _create_attempt(self, payload: dict[str, object]) -> httpx.Response:
        attempt_id = str(payload["p_attempt_id"])
        row = {
            "attempt_id": attempt_id,
            "tenant_id": payload["p_tenant_id"],
            "workspace_id": payload["p_workspace_id"],
            "auth_user_id": payload["p_auth_user_id"],
            "provider": payload["p_provider"],
            "environment": payload["p_environment"],
            "expires_at": payload["p_expires_at"],
            "consumed_at": None,
            "created_at": NOW.isoformat(),
        }
        with self._lock:
            self.attempts[attempt_id] = row
        return httpx.Response(200, json=[row])

    def _create_state(self, payload: dict[str, object]) -> httpx.Response:
        attempt_id = str(payload["p_setup_attempt_id"])
        state_id = str(payload["p_state_id"])
        with self._lock:
            attempt = self.attempts.get(attempt_id)
            if attempt is None or attempt["consumed_at"] is not None:
                return httpx.Response(400, json={"message": "provider_oauth_state_invalid"})
            attempt["consumed_at"] = NOW.isoformat()
            row = {
                "id": state_id,
                "setup_attempt_id": attempt_id,
                "tenant_id": payload["p_tenant_id"],
                "workspace_id": payload["p_workspace_id"],
                "auth_user_id": payload["p_auth_user_id"],
                "provider": payload["p_provider"],
                "environment": payload["p_environment"],
                "state_hash": payload["p_state_hash"],
                "pkce_verifier_ciphertext": payload["p_pkce_verifier_ciphertext"],
                "pkce_key_version": payload["p_pkce_key_version"],
                "pkce_nonce": payload["p_pkce_nonce"],
                "pkce_aad_hash": payload["p_pkce_aad_hash"],
                "callback_state": payload["p_callback_state"],
                "expires_at": payload["p_expires_at"],
                "consumed_at": None,
                "created_at": NOW.isoformat(),
            }
            self.states[str(payload["p_state_hash"])] = row
        return httpx.Response(
            200,
            json=[
                {
                    "oauth_state_id": state_id,
                    "setup_attempt_id": attempt_id,
                    "expires_at": payload["p_expires_at"],
                    "created_at": NOW.isoformat(),
                }
            ],
        )

    def _peek_state(self, request: httpx.Request) -> httpx.Response:
        requested_hash = request.url.params["state_hash"].removeprefix("eq.")
        with self._lock:
            row = self.states.get(requested_hash)
            rows = [] if row is None or row["consumed_at"] is not None else [dict(row)]
        return httpx.Response(200, json=rows)

    def _consume_state(self, payload: dict[str, object]) -> httpx.Response:
        state_hash = str(payload["p_state_hash"])
        with self._lock:
            row = self.states.get(state_hash)
            if (
                row is None
                or row["consumed_at"] is not None
                or row["tenant_id"] != payload["p_tenant_id"]
                or row["workspace_id"] != payload["p_workspace_id"]
                or row["auth_user_id"] != payload["p_auth_user_id"]
                or row["provider"] != payload["p_provider"]
                or row["environment"] != payload["p_environment"]
            ):
                self.consume_failures += 1
                return httpx.Response(400, json={"message": "provider_oauth_state_invalid"})
            consumed = {
                "oauth_state_id": row["id"],
                "setup_attempt_id": row["setup_attempt_id"],
                "pkce_verifier_ciphertext": row["pkce_verifier_ciphertext"],
                "pkce_key_version": row["pkce_key_version"],
                "pkce_nonce": row["pkce_nonce"],
                "pkce_aad_hash": row["pkce_aad_hash"],
                "callback_state": row["callback_state"],
                "consumed_at": NOW.isoformat(),
            }
            row["consumed_at"] = NOW.isoformat()
            row["pkce_verifier_ciphertext"] = None
            row["pkce_key_version"] = None
            row["pkce_nonce"] = None
            row["pkce_aad_hash"] = None
            self.consume_successes += 1
        return httpx.Response(200, json=[consumed])

    def _cancel_state(self, payload: dict[str, object]) -> httpx.Response:
        state_hash = str(payload["p_state_hash"])
        with self._lock:
            row = self.states.get(state_hash)
            if (
                row is None
                or row["consumed_at"] is not None
                or row["tenant_id"] != payload["p_tenant_id"]
                or row["workspace_id"] != payload["p_workspace_id"]
                or row["auth_user_id"] != payload["p_auth_user_id"]
                or row["provider"] != payload["p_provider"]
                or row["environment"] != payload["p_environment"]
            ):
                self.cancel_failures += 1
                return httpx.Response(400, json={"message": "provider_oauth_state_invalid"})
            row["consumed_at"] = NOW.isoformat()
            row["pkce_verifier_ciphertext"] = None
            row["pkce_key_version"] = None
            row["pkce_nonce"] = None
            row["pkce_aad_hash"] = None
            self.cancel_successes += 1
            result = {
                "oauth_state_id": row["id"],
                "callback_state": row["callback_state"],
                "consumed_at": row["consumed_at"],
            }
        return httpx.Response(200, json=[result])

    def _cleanup_states(self, payload: dict[str, object]) -> httpx.Response:
        assert payload == {"p_limit": 100}
        cleaned = 0
        with self._lock:
            for row in self.states.values():
                expires_at = datetime.fromisoformat(str(row["expires_at"]))
                if row["consumed_at"] is None and expires_at <= NOW:
                    row["consumed_at"] = NOW.isoformat()
                    row["pkce_verifier_ciphertext"] = None
                    row["pkce_key_version"] = None
                    row["pkce_nonce"] = None
                    row["pkce_aad_hash"] = None
                    cleaned += 1
        return httpx.Response(200, json=[{"cleaned_count": cleaned}])


def _service(
    *,
    clock: Callable[[], datetime] | None = None,
    driver: FakeFlowAccountDriver | None = None,
    oauth_client: FakeOAuthClient | None = None,
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
    oauth_client = oauth_client or FakeOAuthClient()
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
async def test_disconnect_remotely_revokes_supported_flowaccount_authorization_before_returning(
) -> None:
    service, oauth_client, _, _, connection_store, _ = _service()
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    result = await service.disconnect(
        _principal(),
        WORKSPACE_ID,
        connected.connection_id,
    )

    assert result.status == "disconnected"
    assert result.local_credentials_deleted is True
    assert result.remote_revocation_status == "revoked"
    assert result.provider_revocation_required is False
    assert oauth_client.revocations == 1
    assert connection_store.store._envelopes == {}
    assert connection_store.events[-4:] == [
        "load_connection",
        "load_runtime_material",
        "disconnect",
        "complete_revocation",
    ]

    repeated = await service.disconnect(
        _principal(),
        WORKSPACE_ID,
        connected.connection_id,
    )
    assert repeated.status == "disconnected"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_disconnect_without_advertised_revocation_endpoint_is_local_only_and_idempotent(
) -> None:
    class NoRevocationEndpointClient(FakeOAuthClient):
        async def start_authorization(self, **kwargs: object) -> OAuthAuthorizationSession:
            session = await super().start_authorization(**kwargs)  # type: ignore[arg-type]
            return session.model_copy(update={"revocation_endpoint": None})

    service, oauth_client, _, _, connection_store, _ = _service(
        oauth_client=NoRevocationEndpointClient()
    )
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    result = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert result.status == "disconnected"
    assert result.local_credentials_deleted is True
    assert result.remote_revocation_status == "not_supported"
    assert result.provider_revocation_required is False
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 0

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "disconnected"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is False
    assert oauth_client.revocations == 0


@pytest.mark.asyncio
async def test_disconnect_records_revocation_obligation_after_remote_failure_and_is_idempotent(
) -> None:
    class FailedRevocationClient(FakeOAuthClient):
        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            return False

    service, oauth_client, _, _, connection_store, _ = _service(
        oauth_client=FailedRevocationClient()
    )
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    result = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert result.status == "provider_revocation_required"
    assert result.local_credentials_deleted is True
    assert result.remote_revocation_status == "failed"
    assert result.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 1

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "provider_revocation_required"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is True
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_disconnect_cancellation_deletes_local_credentials_and_remains_idempotent(
) -> None:
    revocation_started = asyncio.Event()

    class BlockingRevocationClient(FakeOAuthClient):
        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            revocation_started.set()
            await asyncio.Event().wait()
            return True

    service, oauth_client, _, _, connection_store, _ = _service(
        oauth_client=BlockingRevocationClient()
    )
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    task = asyncio.create_task(
        service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)
    )
    await revocation_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    disconnected = connection_store.store._connections[connected.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 1

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "provider_revocation_required"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is True
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_disconnect_persists_awaitable_local_deletion_before_anyio_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared_material: list[FlowAccountOAuthRevocationMaterial] = []
    clear = FlowAccountOAuthRevocationMaterial.clear

    def capture_clear(material: FlowAccountOAuthRevocationMaterial) -> None:
        clear(material)
        cleared_material.append(material)

    monkeypatch.setattr(FlowAccountOAuthRevocationMaterial, "clear", capture_clear)
    service, oauth_client, _, _, connection_store, _ = _service()
    blocking_store = BlockingDisconnectStore(connection_store)
    service._connection_store = blocking_store
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )
    cancellation: list[BaseException] = []
    cancellation_scopes: list[anyio.CancelScope] = []
    cancel_scope_ready = anyio.Event()
    state_when_cancelled: list[tuple[ConnectionReadiness, bool, int, bool]] = []

    async def disconnect_then_capture_cancellation() -> None:
        with anyio.CancelScope() as cancel_scope:
            cancellation_scopes.append(cancel_scope)
            cancel_scope_ready.set()
            try:
                await service.disconnect(
                    _principal(),
                    WORKSPACE_ID,
                    connected.connection_id,
                )
            except BaseException as exc:
                cancellation.append(exc)
                persisted = connection_store.store._connections[connected.connection_id]
                state_when_cancelled.append(
                    (
                        persisted.readiness,
                        persisted.provider_revocation_required,
                        len(connection_store.store._envelopes),
                        len(cleared_material) == 1
                        and _revocation_material_is_cleared(cleared_material[0]),
                    )
                )

    assert len(connection_store.store._envelopes) == 4
    with anyio.fail_after(1):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(disconnect_then_capture_cancellation)
            await cancel_scope_ready.wait()
            await blocking_store.disconnect_started.wait()
            cancellation_scopes[0].cancel()
            await anyio.sleep(0)
            assert blocking_store.disconnect_completed is False
            blocking_store.allow_disconnect.set()

    assert len(cancellation) == 1
    assert isinstance(cancellation[0], anyio.get_cancelled_exc_class())
    assert blocking_store.disconnect_completed is True
    assert state_when_cancelled == [
        (ConnectionReadiness.DISCONNECTED, True, 0, True),
    ]
    disconnected = connection_store.store._connections[connected.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 0
    assert len(cleared_material) == 1
    assert _revocation_material_is_cleared(cleared_material[0])


@pytest.mark.asyncio
async def test_disconnect_persists_awaitable_local_deletion_before_native_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared_material: list[FlowAccountOAuthRevocationMaterial] = []
    clear = FlowAccountOAuthRevocationMaterial.clear

    def capture_clear(material: FlowAccountOAuthRevocationMaterial) -> None:
        clear(material)
        cleared_material.append(material)

    monkeypatch.setattr(FlowAccountOAuthRevocationMaterial, "clear", capture_clear)
    service, oauth_client, _, _, connection_store, _ = _service()
    blocking_store = BlockingDisconnectStore(connection_store)
    service._connection_store = blocking_store
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    assert len(connection_store.store._envelopes) == 4
    task = asyncio.create_task(
        service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)
    )
    await blocking_store.disconnect_started.wait()

    task.cancel("local-disconnect-cancelled")
    await asyncio.sleep(0)
    assert task.done() is False
    assert blocking_store.disconnect_completed is False

    task.cancel("repeated-local-disconnect-cancelled")
    await asyncio.sleep(0)
    assert task.done() is False
    assert blocking_store.disconnect_completed is False

    blocking_store.allow_disconnect.set()
    with pytest.raises(asyncio.CancelledError) as cancellation:
        await task

    assert cancellation.value.args == ("local-disconnect-cancelled",)
    assert blocking_store.disconnect_completed is True
    disconnected = connection_store.store._connections[connected.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 0
    assert len(cleared_material) == 1
    assert _revocation_material_is_cleared(cleared_material[0])


@pytest.mark.asyncio
async def test_disconnect_completes_awaitable_revocation_before_anyio_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared_material: list[FlowAccountOAuthRevocationMaterial] = []
    clear = FlowAccountOAuthRevocationMaterial.clear

    def capture_clear(material: FlowAccountOAuthRevocationMaterial) -> None:
        clear(material)
        cleared_material.append(material)

    monkeypatch.setattr(FlowAccountOAuthRevocationMaterial, "clear", capture_clear)
    service, oauth_client, _, _, connection_store, _ = _service()
    blocking_store = BlockingCompleteRevocationStore(connection_store)
    service._connection_store = blocking_store
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )
    cancellation: list[BaseException] = []
    cancellation_scopes: list[anyio.CancelScope] = []
    cancel_scope_ready = anyio.Event()
    state_when_cancelled: list[tuple[ConnectionReadiness, bool, int]] = []

    async def disconnect_then_capture_cancellation() -> None:
        with anyio.CancelScope() as cancel_scope:
            cancellation_scopes.append(cancel_scope)
            cancel_scope_ready.set()
            try:
                await service.disconnect(
                    _principal(),
                    WORKSPACE_ID,
                    connected.connection_id,
                )
            except BaseException as exc:
                cancellation.append(exc)
                persisted = connection_store.store._connections[connected.connection_id]
                state_when_cancelled.append(
                    (
                        persisted.readiness,
                        persisted.provider_revocation_required,
                        len(connection_store.store._envelopes),
                    )
                )

    with anyio.fail_after(1):
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(disconnect_then_capture_cancellation)
            await cancel_scope_ready.wait()
            await blocking_store.completion_started.wait()
            pending = connection_store.store._connections[connected.connection_id]
            assert pending.readiness is ConnectionReadiness.DISCONNECTED
            assert pending.provider_revocation_required is True
            assert connection_store.store._envelopes == {}
            assert len(cleared_material) == 1
            assert _revocation_material_is_cleared(cleared_material[0])
            cancellation_scopes[0].cancel()
            await anyio.sleep(0)
            assert blocking_store.completion_completed is False
            blocking_store.allow_completion.set()

    assert len(cancellation) == 1
    assert isinstance(cancellation[0], anyio.get_cancelled_exc_class())
    assert blocking_store.completion_completed is True
    assert state_when_cancelled == [
        (ConnectionReadiness.DISCONNECTED, False, 0),
    ]
    assert oauth_client.revocations == 1

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "disconnected"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is False
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_disconnect_completes_awaitable_revocation_before_native_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared_material: list[FlowAccountOAuthRevocationMaterial] = []
    clear = FlowAccountOAuthRevocationMaterial.clear

    def capture_clear(material: FlowAccountOAuthRevocationMaterial) -> None:
        clear(material)
        cleared_material.append(material)

    monkeypatch.setattr(FlowAccountOAuthRevocationMaterial, "clear", capture_clear)
    service, oauth_client, _, _, connection_store, _ = _service()
    blocking_store = BlockingCompleteRevocationStore(connection_store)
    service._connection_store = blocking_store
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )

    task = asyncio.create_task(
        service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)
    )
    await blocking_store.completion_started.wait()

    pending = connection_store.store._connections[connected.connection_id]
    assert pending.readiness is ConnectionReadiness.DISCONNECTED
    assert pending.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 1
    assert len(cleared_material) == 1
    assert _revocation_material_is_cleared(cleared_material[0])

    task.cancel("revocation-completion-cancelled")
    await asyncio.sleep(0)
    assert task.done() is False
    assert blocking_store.completion_completed is False

    task.cancel("repeated-revocation-completion-cancelled")
    await asyncio.sleep(0)
    assert task.done() is False
    assert blocking_store.completion_completed is False

    blocking_store.allow_completion.set()
    with pytest.raises(asyncio.CancelledError) as cancellation:
        await task

    assert cancellation.value.args == ("revocation-completion-cancelled",)
    assert blocking_store.completion_completed is True
    disconnected = connection_store.store._connections[connected.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is False
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 1

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "disconnected"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is False
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_concurrent_disconnect_failure_reconciles_after_another_service_revokes(
) -> None:
    class FailedRevocationClient(FakeOAuthClient):
        def __init__(self) -> None:
            super().__init__()
            self.returned_failure = asyncio.Event()
            self.failure_started = asyncio.Event()
            self.success_started = asyncio.Event()

        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            self.failure_started.set()
            await self.success_started.wait()
            self.returned_failure.set()
            return False

    class SuccessfulRevocationClient(FakeOAuthClient):
        def __init__(
            self,
            failed: FailedRevocationClient,
        ) -> None:
            super().__init__()
            self._failed = failed

        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            self._failed.success_started.set()
            await self._failed.failure_started.wait()
            await self._failed.returned_failure.wait()
            return True

    service, _, driver, state_store, connection_store, vault = _service()
    started = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    connected = await service.complete_callback(
        OAuthCallback(code="authorization-code", state=state)
    )
    coordinated_store = CoordinatedDisconnectStore(connection_store)
    failure_client = FailedRevocationClient()
    success_client = SuccessfulRevocationClient(failure_client)
    service._connection_store = coordinated_store
    service._oauth_client = failure_client
    success_service = ProviderOAuthService(
        settings=_settings(),
        workspace_service=FakeWorkspaceService(),
        mercury_access_token=lambda _principal: MERCURY_ACCESS_TOKEN,
        principal_resolver=FakePrincipalResolver(),
        manifest=MANIFEST,
        oauth_client=success_client,
        state_store=state_store,
        connection_store=coordinated_store,
        vault=vault,
        driver=driver,
        clock=lambda: NOW,
        random_bytes=FixedRandom(),
    )

    async def successful_disconnect():
        return await success_service.disconnect(
            _principal(), WORKSPACE_ID, connected.connection_id
        )

    failed, succeeded = await asyncio.wait_for(
        asyncio.gather(
            service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id),
            successful_disconnect(),
        ),
        timeout=1,
    )

    assert failed.status == "disconnected"
    assert failed.remote_revocation_status == "already_disconnected"
    assert failed.provider_revocation_required is False
    assert succeeded.status == "disconnected"
    assert succeeded.remote_revocation_status == "revoked"
    assert succeeded.provider_revocation_required is False
    disconnected = connection_store.store._connections[connected.connection_id]
    assert disconnected.readiness is ConnectionReadiness.DISCONNECTED
    assert disconnected.provider_revocation_required is False
    assert connection_store.store._envelopes == {}
    assert failure_client.revocations == 1
    assert success_client.revocations == 1
    assert coordinated_store.failure_reconciliation_started.is_set()

    repeated = await service.disconnect(_principal(), WORKSPACE_ID, connected.connection_id)

    assert repeated.status == "disconnected"
    assert repeated.remote_revocation_status == "already_disconnected"
    assert repeated.provider_revocation_required is False
    assert failure_client.revocations == 1
    assert success_client.revocations == 1


@pytest.mark.asyncio
async def test_supabase_state_store_is_cross_instance_atomic_and_clears_verifier() -> None:
    backend = SharedOAuthStateRPCBackend()
    state_id = UUID("77777777-7777-4777-8777-777777777777")
    attempt_id = UUID("88888888-8888-4888-8888-888888888888")
    state_hash = "a" * 64
    vault = _vault()
    encrypted = vault.seal(
        CredentialBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=state_id,
            provider="flowaccount",
            company_or_merchant_id="oauth-state",
            environment="sandbox",
            credential_type="oauth_state",
        ),
        b"ENCRYPTED_STATE_PLAINTEXT_SENTINEL",
    ).model_copy(update={"id": state_id})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(backend),
        follow_redirects=False,
    ) as client:
        first = SupabaseProviderOAuthStateStore(
            settings=_settings(),
            http_client=client,
            uuid_factory=lambda: attempt_id,
            callback_uri=CALLBACK_URI,
            clock=lambda: NOW,
        )
        second = SupabaseProviderOAuthStateStore(
            settings=_settings(),
            http_client=client,
            callback_uri=CALLBACK_URI,
            clock=lambda: NOW,
        )
        created = await first.create(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            state_hash=state_hash,
            callback_uri=CALLBACK_URI,
            requested_permissions=("documents.read", "profile.read"),
            expires_at=NOW + timedelta(minutes=10),
            encrypted_payload=encrypted,
            mercury_access_token=MERCURY_ACCESS_TOKEN,
        )
        first_peek, second_peek = await asyncio.gather(
            first.peek(state_hash=state_hash),
            second.peek(state_hash=state_hash),
        )
        outcomes = await asyncio.gather(
            first.consume(
                record=first_peek,
                mercury_access_token=MERCURY_ACCESS_TOKEN,
            ),
            second.consume(
                record=second_peek,
                mercury_access_token=MERCURY_ACCESS_TOKEN,
            ),
            return_exceptions=True,
        )

    successes = [item for item in outcomes if not isinstance(item, Exception)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert created.id == state_id
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], ProviderOAuthError)
    assert str(failures[0]) == "provider_oauth_state_invalid"
    assert backend.consume_successes == backend.consume_failures == 1
    assert backend.states[state_hash]["pkce_verifier_ciphertext"] is None
    assert backend.states[state_hash]["pkce_key_version"] is None
    assert backend.states[state_hash]["pkce_nonce"] is None
    assert backend.states[state_hash]["pkce_aad_hash"] is None
    assert all(
        authorization == f"Bearer {MERCURY_ACCESS_TOKEN}"
        for path, authorization in backend.authorization_headers
        if "/rpc/" in path
    )
    assert all(
        authorization == "Bearer service-role"
        for path, authorization in backend.authorization_headers
        if path.endswith("/mercury_provider_oauth_states")
    )


@pytest.mark.asyncio
async def test_supabase_state_consume_and_cancel_are_one_atomic_terminal_transition() -> None:
    backend = SharedOAuthStateRPCBackend()
    state_id = UUID("77777777-7777-4777-8777-777777777778")
    state_hash = "b" * 64
    vault = _vault()
    encrypted = vault.seal(
        CredentialBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=state_id,
            provider="flowaccount",
            company_or_merchant_id="oauth-state",
            environment="sandbox",
            credential_type="oauth_state",
        ),
        b"ENCRYPTED_STATE_PLAINTEXT_SENTINEL",
    ).model_copy(update={"id": state_id})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(backend),
        follow_redirects=False,
    ) as client:
        first = SupabaseProviderOAuthStateStore(
            settings=_settings(),
            http_client=client,
            uuid_factory=lambda: UUID("88888888-8888-4888-8888-888888888889"),
            callback_uri=CALLBACK_URI,
            clock=lambda: NOW,
        )
        second = SupabaseProviderOAuthStateStore(
            settings=_settings(),
            http_client=client,
            callback_uri=CALLBACK_URI,
            clock=lambda: NOW,
        )
        await first.create(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            state_hash=state_hash,
            callback_uri=CALLBACK_URI,
            requested_permissions=("documents.read", "profile.read"),
            expires_at=NOW + timedelta(minutes=10),
            encrypted_payload=encrypted,
            mercury_access_token=MERCURY_ACCESS_TOKEN,
        )
        consume_record, cancel_record = await asyncio.gather(
            first.peek(state_hash=state_hash),
            second.peek(state_hash=state_hash),
        )
        outcomes = await asyncio.gather(
            first.consume(
                record=consume_record,
                mercury_access_token=MERCURY_ACCESS_TOKEN,
            ),
            second.cancel(
                record=cancel_record,
                mercury_access_token=MERCURY_ACCESS_TOKEN,
            ),
            return_exceptions=True,
        )

    successes = [item for item in outcomes if not isinstance(item, Exception)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert len(successes) == len(failures) == 1
    assert isinstance(failures[0], ProviderOAuthError)
    assert str(failures[0]) == "provider_oauth_state_invalid"
    assert backend.consume_successes + backend.cancel_successes == 1
    assert backend.consume_failures + backend.cancel_failures == 1
    assert backend.states[state_hash]["pkce_verifier_ciphertext"] is None


@pytest.mark.asyncio
async def test_supabase_state_cleanup_clears_expired_unconsumed_ciphertext() -> None:
    backend = SharedOAuthStateRPCBackend()
    state_id = UUID("77777777-7777-4777-8777-777777777779")
    state_hash = "c" * 64
    vault = _vault()
    encrypted = vault.seal(
        CredentialBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=state_id,
            provider="flowaccount",
            company_or_merchant_id="oauth-state",
            environment="sandbox",
            credential_type="oauth_state",
        ),
        b"EXPIRED_STATE_PLAINTEXT_SENTINEL",
    ).model_copy(update={"id": state_id})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(backend),
        follow_redirects=False,
    ) as client:
        store = SupabaseProviderOAuthStateStore(
            settings=_settings(),
            http_client=client,
            uuid_factory=lambda: UUID("88888888-8888-4888-8888-888888888890"),
            callback_uri=CALLBACK_URI,
        )
        await store.create(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            state_hash=state_hash,
            callback_uri=CALLBACK_URI,
            requested_permissions=("documents.read", "profile.read"),
            expires_at=NOW + timedelta(minutes=10),
            encrypted_payload=encrypted,
            mercury_access_token=MERCURY_ACCESS_TOKEN,
        )
        backend.states[state_hash]["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
        assert await store.cleanup_expired(limit=100) == 1

    row = backend.states[state_hash]
    assert row["consumed_at"] is not None
    assert row["pkce_verifier_ciphertext"] is None
    assert row["pkce_key_version"] is None
    assert row["pkce_nonce"] is None
    assert row["pkce_aad_hash"] is None
    cleanup_auth = [
        authorization
        for path, authorization in backend.authorization_headers
        if path.endswith("/cleanup_expired_mercury_provider_oauth_states")
    ]
    assert cleanup_auth == ["Bearer service-role"]


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
    assert oauth_client.starts[0]["provider"] is ProviderId.FLOWACCOUNT
    assert oauth_client.starts[0]["environment"] == "sandbox"
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
    registration_request: dict[str, object] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal registration_request
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
                    "revocation_endpoint": REVOCATION_ENDPOINT,
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
            registration_request = json.loads(request.content)
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
        network_guard = PublicOAuthNetworkGuard(
            resolver=lambda _host, _port: ("1.1.1.1",),
            http_client=client,
        )
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=network_guard,
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        session = await oauth_client.start_authorization(
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
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
    assert registration_request == {
        "redirect_uris": [CALLBACK_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
    }
    assert query["scope"] == ["documents.read profile.read"]
    assert query["redirect_uri"] == [CALLBACK_URI]
    assert session.client_secret == DYNAMIC_CLIENT_SECRET
    assert session.revocation_endpoint == REVOCATION_ENDPOINT
    assert repr(session).find(DYNAMIC_CLIENT_SECRET) == -1


def _discovery_handler(
    *,
    registration_payload: dict[str, object],
    authorization_server: str = AUTHORIZATION_SERVER,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == RESOURCE_URI:
            return httpx.Response(
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer resource_metadata="'
                        "https://flowaccount-sandbox.example/"
                        '.well-known/oauth-protected-resource/mcp", '
                        'scope="profile.read documents.read"'
                    )
                },
            )
        if request.url.path == "/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(
                200,
                json={
                    "resource": RESOURCE_URI,
                    "authorization_servers": [authorization_server],
                    "scopes_supported": ["profile.read", "documents.read"],
                },
            )
        if request.url.path.endswith("/.well-known/oauth-authorization-server/oauth"):
            return httpx.Response(
                200,
                json={
                    "issuer": authorization_server,
                    "authorization_endpoint": (f"{authorization_server.rstrip('/')}/authorize"),
                    "token_endpoint": f"{authorization_server.rstrip('/')}/token",
                    "registration_endpoint": (f"{authorization_server.rstrip('/')}/register"),
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "token_endpoint_auth_methods_supported": ["client_secret_basic"],
                    "code_challenge_methods_supported": ["S256"],
                },
            )
        if request.url.path.endswith("/register"):
            return httpx.Response(201, json=registration_payload)
        return httpx.Response(404)

    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "challenge"),
    [
        (
            302,
            (
                'Bearer resource_metadata="'
                "https://flowaccount-sandbox.example/"
                '.well-known/oauth-protected-resource/mcp"'
            ),
        ),
        (
            401,
            (
                'NotBearer resource_metadata="'
                "https://flowaccount-sandbox.example/"
                '.well-known/oauth-protected-resource/mcp"'
            ),
        ),
        (
            401,
            (
                'Basic realm="Bearer", resource_metadata="'
                "https://flowaccount-sandbox.example/"
                '.well-known/oauth-protected-resource/mcp"'
            ),
        ),
    ],
)
async def test_protected_resource_requires_401_with_exact_bearer_scheme(
    status_code: int,
    challenge: str,
) -> None:
    registration = {
        "client_id": "dynamic-client-id",
        "client_secret": DYNAMIC_CLIENT_SECRET,
        "redirect_uris": [CALLBACK_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
    }
    downstream = _discovery_handler(registration_payload=registration)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == RESOURCE_URI:
            return httpx.Response(
                status_code,
                headers={"WWW-Authenticate": challenge},
            )
        return downstream(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )

        with pytest.raises(
            ProviderOAuthError,
            match="^provider_oauth_downstream_invalid$",
        ):
            await oauth_client.start_authorization(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                resource_uri=RESOURCE_URI,
                callback_uri=CALLBACK_URI,
                allowed_permissions=MANIFEST.allowed_permissions,
                state="A" * 43,
                code_challenge="B" * 43,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [
            ("WWW-Authenticate", 'Basic realm="Mercury"'),
            (
                "WWW-Authenticate",
                (
                    'bEaReR resource_metadata="'
                    "https://flowaccount-sandbox.example/"
                    '.well-known/oauth-protected-resource/mcp", '
                    'scope="profile.read documents.read"'
                ),
            ),
        ],
        [
            (
                "WWW-Authenticate",
                (
                    'Basic realm="Mercury", Bearer resource_metadata="'
                    "https://flowaccount-sandbox.example/"
                    '.well-known/oauth-protected-resource/mcp", '
                    'scope="profile.read documents.read"'
                ),
            ),
        ],
        [
            (
                "WWW-Authenticate",
                (
                    'Bearer resource_metadata="'
                    "https://flowaccount-sandbox.example/"
                    '.well-known/oauth-protected-resource/mcp"'
                ),
            ),
            (
                "WWW-Authenticate",
                'scope="profile.read documents.read"',
            ),
        ],
        [
            ("WWW-Authenticate", "Bearer"),
            (
                "WWW-Authenticate",
                (
                    'resource_metadata="'
                    "https://flowaccount-sandbox.example/"
                    '.well-known/oauth-protected-resource/mcp"'
                ),
            ),
            (
                "WWW-Authenticate",
                'scope="profile.read documents.read"',
            ),
        ],
    ],
    ids=[
        "multiple-headers",
        "multiple-challenges",
        "parameters-continue-across-fields",
        "scheme-and-parameters-split-across-fields",
    ],
)
async def test_protected_resource_accepts_exact_bearer_among_multiple_challenges(
    headers: list[tuple[str, str]],
) -> None:
    registration = {
        "client_id": "dynamic-client-id",
        "client_secret": DYNAMIC_CLIENT_SECRET,
        "redirect_uris": [CALLBACK_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
    }
    downstream = _discovery_handler(registration_payload=registration)

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == RESOURCE_URI:
            return httpx.Response(401, headers=headers)
        return downstream(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        session = await oauth_client.start_authorization(
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            resource_uri=RESOURCE_URI,
            callback_uri=CALLBACK_URI,
            allowed_permissions=MANIFEST.allowed_permissions,
            state="A" * 43,
            code_challenge="B" * 43,
        )

    assert session.granted_permissions == ("documents.read", "profile.read")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "registration_override",
    [
        {"redirect_uris": [CALLBACK_URI, "https://attacker.example/callback"]},
        {"grant_types": ["authorization_code"]},
        {"response_types": ["code", "token"]},
    ],
)
async def test_dynamic_registration_requires_exact_returned_metadata(
    registration_override: dict[str, object],
) -> None:
    registration = {
        "client_id": "dynamic-client-id",
        "client_secret": DYNAMIC_CLIENT_SECRET,
        "redirect_uris": [CALLBACK_URI],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_basic",
        **registration_override,
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_discovery_handler(registration_payload=registration)),
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        with pytest.raises(
            ProviderOAuthError,
            match="^provider_oauth_downstream_invalid$",
        ):
            await oauth_client.start_authorization(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                resource_uri=RESOURCE_URI,
                callback_uri=CALLBACK_URI,
                allowed_permissions=MANIFEST.allowed_permissions,
                state="A" * 43,
                code_challenge="B" * 43,
            )


@pytest.mark.asyncio
async def test_metadata_authorization_server_must_match_reviewed_environment_origin() -> None:
    attacker_server = "https://attacker.example/oauth"
    calls: list[str] = []
    handler = _discovery_handler(
        registration_payload={},
        authorization_server=attacker_server,
    )

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return handler(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(recording_handler),
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        with pytest.raises(
            ProviderOAuthError,
            match="^provider_oauth_downstream_invalid$",
        ):
            await oauth_client.start_authorization(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                resource_uri=RESOURCE_URI,
                callback_uri=CALLBACK_URI,
                allowed_permissions=MANIFEST.allowed_permissions,
                state="A" * 43,
                code_challenge="B" * 43,
            )

    assert all("attacker.example/.well-known" not in url for url in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answers",
    [
        [("127.0.0.1",)],
        [("224.0.0.1",)],
        [("fec0::1",)],
        [("ff02::1",)],
        [("::ffff:10.0.0.1",)],
        [("fe80::1%eth0",)],
        [("2001:0DB8::1",)],
        [("1.1.1.1", "10.0.0.9")],
        [("1.1.1.1",), ("10.0.0.9",)],
    ],
    ids=[
        "loopback",
        "multicast-v4",
        "site-local-v6",
        "multicast-v6",
        "mapped-private-v4",
        "scoped-v6",
        "noncanonical-v6",
        "mixed-dns-answer",
        "dns-rebinding",
    ],
)
async def test_network_guard_rejects_non_public_or_rebound_resolution(
    answers: list[tuple[str, ...]],
) -> None:
    calls: list[str] = []
    remaining = list(answers)

    def resolver(_host: str, _port: int) -> tuple[str, ...]:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return _discovery_handler(
            registration_payload={
                "client_id": "dynamic-client-id",
                "client_secret": DYNAMIC_CLIENT_SECRET,
                "redirect_uris": [CALLBACK_URI],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_basic",
            }
        )(request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=resolver,
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        with pytest.raises(
            ProviderOAuthError,
            match="^provider_oauth_downstream_invalid$",
        ):
            await oauth_client.start_authorization(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                resource_uri=RESOURCE_URI,
                callback_uri=CALLBACK_URI,
                allowed_permissions=MANIFEST.allowed_permissions,
                state="A" * 43,
                code_challenge="B" * 43,
            )

    assert len(calls) <= 1


@pytest.mark.asyncio
async def test_network_guard_accepts_only_canonical_public_global_unicast() -> None:
    guard = PublicOAuthNetworkGuard(
        resolver=lambda _host, _port: ("1.1.1.1", "2606:4700:4700::1111"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None)),
    )

    try:
        assert await guard.resolve_and_pin("provider.example", 443) == frozenset(
            {"1.1.1.1", "2606:4700:4700::1111"}
        )
    finally:
        await guard._http.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_type", "accepted"),
    [(None, False), ("MAC", False), ("bearer", True)],
)
async def test_exchange_requires_case_insensitive_bearer_token_type(
    token_type: str | None,
    accepted: bool,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = {
            "access_token": PROVIDER_ACCESS_TOKEN,
            "refresh_token": PROVIDER_REFRESH_TOKEN,
            "expires_in": 3600,
            "scope": "documents.read profile.read",
        }
        if token_type is not None:
            payload["token_type"] = token_type
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        exchange = oauth_client.exchange_code(
            session=OAuthAuthorizationSession(
                authorization_url=AUTHORIZATION_ENDPOINT,
                resource_uri=RESOURCE_URI,
                authorization_endpoint=AUTHORIZATION_ENDPOINT,
                token_endpoint=TOKEN_ENDPOINT,
                revocation_endpoint=REVOCATION_ENDPOINT,
                callback_uri=CALLBACK_URI,
                client_id="dynamic-client-id",
                client_secret=DYNAMIC_CLIENT_SECRET,
                token_endpoint_auth_method="client_secret_basic",
                granted_permissions=("documents.read", "profile.read"),
            ),
            code="authorization-code",
            code_verifier="A" * 43,
        )
        if accepted:
            tokens = await exchange
            assert tokens.token_type == "Bearer"
        else:
            with pytest.raises(
                ProviderOAuthError,
                match="^provider_oauth_exchange_failed$",
            ):
                await exchange


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_type", "accepted"),
    [(None, False), ("MAC", False), ("BEARER", True)],
)
async def test_refresh_requires_case_insensitive_bearer_token_type(
    token_type: str | None,
    accepted: bool,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        payload: dict[str, object] = {
            "access_token": PROVIDER_ACCESS_TOKEN,
            "refresh_token": PROVIDER_REFRESH_TOKEN,
            "expires_in": 3600,
            "scope": "documents.read profile.read",
        }
        if token_type is not None:
            payload["token_type"] = token_type
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as client:
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=client,
            ),
            authorization_server_origins=AUTHORIZATION_SERVER_ORIGINS,
        )
        refresh = oauth_client.refresh(
            FlowAccountRefreshRequest(
                token_endpoint=TOKEN_ENDPOINT,
                resource_uri=RESOURCE_URI,
                client_id="dynamic-client-id",
                client_secret=DYNAMIC_CLIENT_SECRET,
                token_endpoint_auth_method="client_secret_basic",
                refresh_token=PROVIDER_REFRESH_TOKEN,
                granted_permissions=("documents.read", "profile.read"),
            )
        )
        if accepted:
            tokens = await refresh
            assert tokens.token_type == "Bearer"
        else:
            with pytest.raises(
                ProviderOAuthError,
                match="^provider_oauth_exchange_failed$",
            ):
                await refresh


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
        selected_company_id="company-123",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        code="AUTHORIZATION_CODE_SENTINEL",
        state=state,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
    )

    summary = await service.complete(_principal(), callback)

    assert summary.readiness is ConnectionReadiness.READY
    assert summary.account_display_name == "FlowAccount Test Company"
    assert summary.granted_permissions == ("documents.read", "profile.read")
    assert driver.events == ["discover", "provider_profile.get"]
    assert len(oauth_client.exchanges) == 1
    assert len(connection_store.saved) == 2
    assert connection_store.events == [
        "begin_attempt",
        "attach_attempt",
        "load_attempt_material",
        "resolve_target",
        "finalize_attempt",
        "acknowledge_attempt",
    ]
    assert connection_store.saved[1]["readiness"] is ConnectionReadiness.REQUIRES_VALIDATION
    attempt_ids = {
        values["attempt_id"] for values in connection_store.saved if "attempt_id" in values
    }
    assert len(attempt_ids) == 1
    attempt_id = attempt_ids.pop()
    assert connection_store.saved[0]["company_or_merchant_id"] == (f"oauth-pending-{attempt_id}")
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
    saved_connection = next(
        item
        for item in connection_store.store.list_for_workspace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
        )
        if item.readiness is ConnectionReadiness.READY
    )
    assert saved_connection.connection_id == summary.connection_id
    assert vault is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("validation_fails", [False, True])
async def test_rotated_provisional_material_is_finalized_or_retained_for_remediation(
    validation_fails: bool,
) -> None:
    expired_access = "EXPIRED_EXCHANGE_ACCESS_SENTINEL"
    expired_refresh = "EXPIRED_EXCHANGE_REFRESH_SENTINEL"
    rotated_access = "ROTATED_CALLBACK_ACCESS_SENTINEL"
    rotated_refresh = "ROTATED_CALLBACK_REFRESH_SENTINEL"

    class RotatingOAuthClient(FakeOAuthClient):
        def __init__(self) -> None:
            super().__init__()
            self.refreshes: list[FlowAccountRefreshRequest] = []
            self.revoked_tokens: list[FlowAccountOAuthTokens] = []

        async def exchange_code(
            self,
            *,
            session: OAuthAuthorizationSession,
            code: str,
            code_verifier: str,
        ) -> FlowAccountOAuthTokens:
            await super().exchange_code(
                session=session,
                code=code,
                code_verifier=code_verifier,
            )
            return FlowAccountOAuthTokens(
                access_token=expired_access,
                refresh_token=expired_refresh,
                token_type="Bearer",
                expires_at=NOW - timedelta(seconds=1),
                granted_permissions=session.granted_permissions,
            )

        async def refresh(
            self,
            request: FlowAccountRefreshRequest,
        ) -> FlowAccountOAuthTokens:
            self.refreshes.append(request)
            return FlowAccountOAuthTokens(
                access_token=rotated_access,
                refresh_token=rotated_refresh,
                token_type="Bearer",
                expires_at=NOW + timedelta(hours=1),
                granted_permissions=request.granted_permissions,
            )

        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            self.revoked_tokens.append(tokens)
            self.revocations += 1
            return False

    class RefreshingDriver(FakeFlowAccountDriver):
        def __init__(self, header_factory: FlowAccountOAuthHeaderFactory) -> None:
            super().__init__(validation_error=validation_fails)
            self.header_factory = header_factory

        async def discover(self, connection) -> ProviderDiscovery:
            headers = await self.header_factory(connection)
            assert headers.headers[0].value == f"Bearer {rotated_access}"
            return await super().discover(connection)

        async def validate_connection(self, connection) -> ProviderValidation:
            headers = await self.header_factory(connection)
            assert headers.headers[0].value == f"Bearer {rotated_access}"
            return await super().validate_connection(connection)

    oauth_client = RotatingOAuthClient()
    service, _, _, _, connection_store, vault = _service(oauth_client=oauth_client)
    raw_store = connection_store.store

    def replace_attempt_material(connection, envelopes):
        return raw_store.replace_oauth_attempt_envelopes(connection, envelopes)

    service._driver = RefreshingDriver(
        FlowAccountOAuthHeaderFactory(
            vault=vault,
            load_envelopes=raw_store.load_runtime_envelopes,
            save_envelopes=replace_attempt_material,
            refresh=oauth_client.refresh,
            clock=lambda: NOW,
        )
    )
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    if validation_fails:
        with pytest.raises(
            ProviderOAuthError,
            match="^provider_oauth_validation_failed$",
        ):
            await service.complete_callback(OAuthCallback(code="authorization-code", state=state))
        attempt = next(iter(raw_store._oauth_attempts.values()))
        assert attempt.status == "failed"
        retained = tuple(
            raw_store._oauth_attempt_envelopes[envelope_id]
            for envelope_id in attempt.credential_envelope_ids
        )
        binding_connection_id = attempt.target_connection_id or attempt.id
        binding_account_id = attempt.provider_account_id
        assert binding_account_id is not None
        retained_plaintext: dict[str, str] = {}
        for envelope in retained:
            opened = vault.open(
                CredentialBinding(
                    tenant_id=attempt.tenant_id,
                    workspace_id=attempt.workspace_id,
                    auth_user_id=attempt.auth_user_id,
                    connection_id=binding_connection_id,
                    provider=attempt.provider.value,
                    company_or_merchant_id=binding_account_id,
                    environment=attempt.environment,
                    credential_type=envelope.credential_type,
                ),
                envelope,
            )
            try:
                retained_plaintext[envelope.credential_type] = opened.decode("utf-8")
            finally:
                opened[:] = b"\x00" * len(opened)
        assert retained_plaintext["access_token"] == rotated_access
        assert retained_plaintext["refresh_token"] == rotated_refresh
        assert oauth_client.revoked_tokens[0].access_token == rotated_access
        assert oauth_client.revoked_tokens[0].refresh_token == rotated_refresh
    else:
        summary = await service.complete_callback(
            OAuthCallback(code="authorization-code", state=state)
        )
        target = raw_store._connections[summary.connection_id]
        finalized_plaintext: dict[str, str] = {}
        for envelope_id in target.credential_envelope_ids:
            envelope = raw_store._envelopes[envelope_id]
            opened = vault.open(
                CredentialBinding(
                    tenant_id=target.tenant_id,
                    workspace_id=target.workspace_id,
                    auth_user_id=target.auth_user_id,
                    connection_id=target.id,
                    provider=target.provider.value,
                    company_or_merchant_id=target.provider_account_id,
                    environment=target.environment,
                    credential_type=envelope.credential_type,
                ),
                envelope,
            )
            try:
                finalized_plaintext[envelope.credential_type] = opened.decode("utf-8")
            finally:
                opened[:] = b"\x00" * len(opened)
        assert finalized_plaintext["access_token"] == rotated_access
        assert finalized_plaintext["refresh_token"] == rotated_refresh
        assert summary.readiness is ConnectionReadiness.READY
        assert oauth_client.revocations == 0

    assert len(oauth_client.refreshes) == 1


@pytest.mark.asyncio
async def test_callback_uses_profile_company_without_nonstandard_query_parameter() -> None:
    service, _, _, _, connection_store, _ = _service()
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    summary = await service.complete_callback(
        OAuthCallback(
            code="authorization-code",
            state=state,
        )
    )

    assert summary.readiness is ConnectionReadiness.READY
    assert summary.account_display_name == "FlowAccount Test Company"
    ready = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert len(ready) == 1
    assert ready[0].connection_id == summary.connection_id
    assert ready[0].readiness is ConnectionReadiness.READY
    assert "oauth-pending-" not in ready[0].account_display_name
    assert len(connection_store.store._connections) == 1


@pytest.mark.asyncio
async def test_ready_disconnect_reconnect_reuses_connection_id_and_advances_revision() -> None:
    service, _, _, _, connection_store, _ = _service()
    first_start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    first_state = parse_qs(urlsplit(first_start.authorization_url).query)["state"][0]
    first = await service.complete_callback(
        OAuthCallback(code="first-authorization-code", state=first_state)
    )
    disconnected = connection_store.disconnect(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        connection_id=first.connection_id,
    )

    second_start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    second_state = parse_qs(urlsplit(second_start.authorization_url).query)["state"][0]
    reconnected = await service.complete_callback(
        OAuthCallback(code="second-authorization-code", state=second_state)
    )
    summaries = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )

    assert reconnected.connection_id == first.connection_id
    assert reconnected.revision == disconnected.revision + 2
    assert reconnected.readiness is ConnectionReadiness.READY
    assert reconnected.provider_revocation_required is False
    assert len(summaries) == 1
    assert len(connection_store.store._connections) == 1
    assert [
        item.connection_id for item in summaries if item.readiness is ConnectionReadiness.READY
    ] == [first.connection_id]


@pytest.mark.asyncio
async def test_process_boundary_after_exchange_keeps_preexisting_internal_obligation() -> None:
    class ProcessBoundary(BaseException):
        pass

    class ProcessBoundaryOAuthClient(FakeOAuthClient):
        async def exchange_code(
            self,
            *,
            session: OAuthAuthorizationSession,
            code: str,
            code_verifier: str,
        ) -> FlowAccountOAuthTokens:
            await super().exchange_code(
                session=session,
                code=code,
                code_verifier=code_verifier,
            )
            raise ProcessBoundary

    oauth_client = ProcessBoundaryOAuthClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(ProcessBoundary):
        await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    assert connection_store.events == ["begin_attempt"]
    assert len(oauth_client.exchanges) == 1
    assert (
        connection_store.store.list_for_workspace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
        )
        == ()
    )
    assert len(connection_store.store._oauth_attempts) == 1
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert attempt.status == "exchange_pending"
    assert attempt.provider_revocation_required is True


@pytest.mark.asyncio
async def test_commit_then_timeout_reconciles_by_attempt_id_without_revocation() -> None:
    oauth_client = FakeOAuthClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)
    original_finalize = connection_store.finalize_oauth_attempt
    calls = 0

    def commit_then_timeout(**kwargs):
        nonlocal calls
        calls += 1
        result = original_finalize(**kwargs)
        if calls == 1:
            raise ProviderStoreError("provider_store_unavailable")
        return result

    connection_store.finalize_oauth_attempt = commit_then_timeout
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    summary = await service.complete_callback(OAuthCallback(code="authorization-code", state=state))
    visible = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )

    assert calls == 2
    assert oauth_client.revocations == 0
    assert [item.connection_id for item in visible] == [summary.connection_id]
    assert visible[0].readiness is ConnectionReadiness.READY
    assert visible[0].provider_revocation_required is False


@pytest.mark.asyncio
async def test_two_lost_finalize_responses_and_unavailable_cleanup_leave_generation_held() -> None:
    oauth_client = FakeOAuthClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)
    original_finalize = connection_store.finalize_oauth_attempt
    finalize_calls = 0
    cleanup_calls = 0

    def commit_then_lose_response(**kwargs):
        nonlocal finalize_calls
        finalize_calls += 1
        original_finalize(**kwargs)
        raise ProviderStoreError("provider_store_unavailable")

    def unavailable_cleanup(**_kwargs):
        nonlocal cleanup_calls
        cleanup_calls += 1
        raise ProviderStoreError("provider_store_unavailable")

    connection_store.finalize_oauth_attempt = commit_then_lose_response
    connection_store.fail_oauth_attempt = unavailable_cleanup
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(ProviderStoreError, match="^provider_store_unavailable$"):
        await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    raw_store = connection_store.store
    attempt = next(iter(raw_store._oauth_attempts.values()))
    target = raw_store._connections[attempt.target_connection_id]
    assert finalize_calls == 2
    assert cleanup_calls == 2
    assert attempt.status == "finalized"
    assert attempt.acknowledged_at is None
    assert target.readiness is ConnectionReadiness.REQUIRES_VALIDATION
    assert (
        raw_store.list_for_workspace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
        )
        == ()
    )
    with pytest.raises(ProviderStoreError, match="^provider_connection_not_found$"):
        raw_store.load_envelopes(target)
    assert oauth_client.revocations == 0


@pytest.mark.asyncio
async def test_lost_ack_response_reconciles_idempotently_without_revocation() -> None:
    oauth_client = FakeOAuthClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)
    original_acknowledge = connection_store.acknowledge_oauth_attempt
    acknowledge_calls = 0

    def commit_once_then_lose_response(**kwargs):
        nonlocal acknowledge_calls
        acknowledge_calls += 1
        ready = original_acknowledge(**kwargs)
        if acknowledge_calls == 1:
            raise ProviderStoreError("provider_store_unavailable")
        return ready

    connection_store.acknowledge_oauth_attempt = commit_once_then_lose_response
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    summary = await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    visible = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert acknowledge_calls == 2
    assert attempt.status == "finalized"
    assert attempt.acknowledged_at is not None
    assert summary.readiness is ConnectionReadiness.READY
    assert len(visible) == 1
    assert visible[0].connection_id == summary.connection_id
    assert oauth_client.revocations == 0


@pytest.mark.asyncio
async def test_lost_ack_with_refresh_revision_quarantines_owned_generation_and_rotated_tokens() -> (
    None
):
    rotated_access = "ROTATED_ACCESS_TOKEN_SENTINEL"
    rotated_refresh = "ROTATED_REFRESH_TOKEN_SENTINEL"

    class CapturingRevocationClient(FakeOAuthClient):
        def __init__(self) -> None:
            super().__init__()
            self.revoked_tokens: list[FlowAccountOAuthTokens] = []

        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            self.revoked_tokens.append(tokens)
            self.revocations += 1
            return False

    oauth_client = CapturingRevocationClient()
    service, _, _, _, connection_store, vault = _service(oauth_client=oauth_client)
    original_acknowledge = connection_store.acknowledge_oauth_attempt
    acknowledge_calls = 0

    def acknowledge_refresh_then_lose_response(**kwargs):
        nonlocal acknowledge_calls
        acknowledge_calls += 1
        acknowledged = original_acknowledge(**kwargs)
        if acknowledge_calls == 1:
            refreshed_tokens = FlowAccountOAuthTokens(
                access_token=rotated_access,
                refresh_token=rotated_refresh,
                token_type="Bearer",
                expires_at=NOW + timedelta(hours=2),
                granted_permissions=acknowledged.granted_permissions,
            )
            replacement = seal_flowaccount_credentials(
                vault=vault,
                connection=acknowledged,
                tokens=refreshed_tokens,
                token_endpoint=TOKEN_ENDPOINT,
                resource_uri=RESOURCE_URI,
                client_id="dynamic-client-id",
                client_secret=DYNAMIC_CLIENT_SECRET,
                token_endpoint_auth_method="client_secret_basic",
            )
            connection_store.store.save_connection(
                tenant_id=acknowledged.tenant_id,
                workspace_id=acknowledged.workspace_id,
                auth_user_id=acknowledged.auth_user_id,
                connection_id=acknowledged.id,
                provider=acknowledged.provider,
                environment=acknowledged.environment,
                company_or_merchant_id=acknowledged.provider_account_id,
                account_display_name=acknowledged.account_display_name,
                authorization_method=acknowledged.authorization_method,
                granted_permissions=acknowledged.granted_permissions,
                readiness=acknowledged.readiness,
                revision=acknowledged.revision + 1,
                validated_at=acknowledged.last_validated_at,
                envelopes=replacement,
            )
        raise ProviderStoreError("provider_store_unavailable")

    connection_store.acknowledge_oauth_attempt = acknowledge_refresh_then_lose_response
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(ProviderStoreError, match="^provider_store_unavailable$"):
        await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    raw_store = connection_store.store
    attempt = next(iter(raw_store._oauth_attempts.values()))
    target = raw_store._connections[attempt.target_connection_id]
    assert acknowledge_calls == 2
    assert attempt.status == "failed"
    assert target.readiness is ConnectionReadiness.DISCONNECTED
    assert target.provider_revocation_required is True
    assert raw_store._envelopes == {}
    assert oauth_client.revocations == 1
    assert len(oauth_client.revoked_tokens) == 1
    assert oauth_client.revoked_tokens[0].access_token == rotated_access
    assert oauth_client.revoked_tokens[0].refresh_token == rotated_refresh


@pytest.mark.asyncio
async def test_ambiguous_finalize_disconnects_actual_target_before_failed_revocation() -> None:
    class FailedRevocationClient(FakeOAuthClient):
        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            return False

    oauth_client = FailedRevocationClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)
    original_finalize = connection_store.finalize_oauth_attempt
    calls = 0

    def commit_then_timeout(**kwargs):
        nonlocal calls
        calls += 1
        original_finalize(**kwargs)
        raise ProviderStoreError("provider_store_unavailable")

    connection_store.finalize_oauth_attempt = commit_then_timeout
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(ProviderStoreError, match="^provider_store_unavailable$"):
        await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    visible = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert calls == 2
    assert visible == ()
    assert connection_store.store._envelopes == {}
    assert set(connection_store.store._oauth_attempt_envelopes) == set(
        attempt.credential_envelope_ids
    )
    assert attempt.credential_envelope_ids
    assert attempt.status == "failed"
    assert attempt.provider_revocation_required is True
    assert oauth_client.revocations == 1


@pytest.mark.asyncio
async def test_finalization_and_revocation_failure_remain_internal_and_durable() -> None:
    class FailedRevocationClient(FakeOAuthClient):
        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            assert session.revocation_endpoint == REVOCATION_ENDPOINT
            assert tokens.token_type == "Bearer"
            self.revocations += 1
            return False

    oauth_client = FailedRevocationClient()
    service, _, _, _, connection_store, _ = _service(oauth_client=oauth_client)

    def fail_finalization(**_kwargs):
        raise ProviderStoreError("provider_connection_conflict")

    connection_store.finalize_oauth_attempt = fail_finalization
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(
        ProviderStoreError,
        match="^provider_connection_conflict$",
    ):
        await service.complete_callback(OAuthCallback(code="authorization-code", state=state))

    summaries = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert summaries == ()
    assert connection_store.store._envelopes == {}
    assert len(connection_store.store._oauth_attempts) == 1
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert attempt.status == "failed"
    assert attempt.provider_revocation_required is True
    assert attempt.credential_envelope_ids
    assert set(connection_store.store._oauth_attempt_envelopes) == set(
        attempt.credential_envelope_ids
    )
    assert oauth_client.revocations == 1
    assert connection_store.events[-3:] == [
        "fail_attempt",
        "load_attempt_material",
        "load_attempt_material",
    ]


@pytest.mark.asyncio
async def test_error_callback_consumes_state_without_exchanging_provider_code() -> None:
    service, oauth_client, _, state_store, _, _ = _service()
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
        selected_company_id="company-123",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        state=state,
        error="access_denied",
    )

    with pytest.raises(
        ProviderOAuthError,
        match="^provider_oauth_authorization_failed$",
    ):
        await service.complete_callback(callback)
    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await state_store.peek(state_hash=hashlib.sha256(state.encode("ascii")).hexdigest())
    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete_callback(callback)

    assert oauth_client.exchanges == []
    rendered = f"{callback!r} {callback.model_dump_json()}"
    assert state not in rendered
    assert "access_denied" not in rendered


@pytest.mark.parametrize(
    "values",
    [
        {"state": "A" * 43},
        {"state": "A" * 43, "code": "code", "error": "access_denied"},
    ],
)
def test_callback_model_requires_exact_success_or_error_union(
    values: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        OAuthCallback(**values)


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
        selected_company_id="company-123",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    callback = OAuthCallback(
        code="authorization-code",
        state=state,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
    )

    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete(_principal(OTHER_USER_ID), callback)

    clock[0] = NOW + timedelta(minutes=10, microseconds=1)
    with pytest.raises(ProviderOAuthError, match="^provider_oauth_state_invalid$"):
        await service.complete(_principal(), callback)

    mismatch_service, mismatch_oauth, _, _, mismatch_store, _ = _service(
        driver=FakeFlowAccountDriver(profile_company_id="company-other")
    )
    mismatch_start = await mismatch_service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
        selected_company_id="company-123",
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
    mismatch_connections = mismatch_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert mismatch_connections == ()
    assert mismatch_store.store._envelopes == {}
    assert mismatch_oauth.revocations == 1
    mismatch_attempt = next(iter(mismatch_store.store._oauth_attempts.values()))
    assert mismatch_attempt.status == "revoked"
    assert mismatch_attempt.provider_revocation_required is False
    assert mismatch_store.events[-3:] == [
        "fail_attempt",
        "load_attempt_material",
        "complete_attempt_revocation",
    ]


@pytest.mark.asyncio
async def test_company_mismatch_cancellation_leaves_no_dispatchable_credential() -> None:
    revocation_started = asyncio.Event()
    hold_revocation = asyncio.Event()

    class BlockingRevocationClient(FakeOAuthClient):
        async def revoke(
            self,
            *,
            session: OAuthAuthorizationSession,
            tokens: FlowAccountOAuthTokens,
        ) -> bool:
            self.revocations += 1
            revocation_started.set()
            await hold_revocation.wait()
            return True

    oauth_client = BlockingRevocationClient()
    service, _, _, _, connection_store, _ = _service(
        driver=FakeFlowAccountDriver(profile_company_id="company-other"),
        oauth_client=oauth_client,
    )
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
        selected_company_id="company-123",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]
    completion = asyncio.create_task(
        service.complete_callback(OAuthCallback(code="authorization-code", state=state))
    )

    await revocation_started.wait()
    connections = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert connections == ()
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert attempt.status == "failed"
    assert attempt.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert attempt.credential_envelope_ids
    assert set(connection_store.store._oauth_attempt_envelopes) == set(
        attempt.credential_envelope_ids
    )
    assert connection_store.events[-2:] == [
        "fail_attempt",
        "load_attempt_material",
    ]

    completion.cancel()
    with pytest.raises(asyncio.CancelledError):
        await completion
    connections = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert connections == ()
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert attempt.provider_revocation_required is True
    assert connection_store.store._envelopes == {}
    assert set(connection_store.store._oauth_attempt_envelopes) == set(
        attempt.credential_envelope_ids
    )
    assert "complete_attempt_revocation" not in connection_store.events


@pytest.mark.asyncio
async def test_retryable_validation_failure_has_zero_dispatchable_credential_retention() -> None:
    service, oauth_client, _, _, connection_store, _ = _service(
        driver=FakeFlowAccountDriver(validation_error=True)
    )
    start = await service.start(
        _principal(),
        WORKSPACE_ID,
        "flowaccount",
        "sandbox",
    )
    state = parse_qs(urlsplit(start.authorization_url).query)["state"][0]

    with pytest.raises(
        ProviderOAuthError,
        match="^provider_oauth_validation_failed$",
    ):
        await service.complete_callback(
            OAuthCallback(
                code="authorization-code",
                state=state,
            )
        )

    connections = connection_store.store.list_for_workspace(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
    )
    assert connections == ()
    assert connection_store.store._envelopes == {}
    assert oauth_client.revocations == 1
    attempt = next(iter(connection_store.store._oauth_attempts.values()))
    assert attempt.status == "revoked"
    assert attempt.provider_revocation_required is False
    assert attempt.credential_envelope_ids == ()
    assert connection_store.store._oauth_attempt_envelopes == {}


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
        },
    )
    sibling = client.get(f"{FLOWACCOUNT_CALLBACK_PATH}/extra")
    other_provider = client.get("/auth/providers/peak/callback")
    nonstandard_company = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={
            "code": "AUTHORIZATION_CODE_SENTINEL",
            "state": "B" * 43,
            "company_id": "company-123",
        },
    )

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
    assert nonstandard_company.status_code == 400
    assert callback_service.callbacks[0].model_fields_set == {"code", "state"}
    assert "AUTHORIZATION_CODE_SENTINEL" not in response.text
    assert "A" * 43 not in response.text


def test_exact_callback_accepts_standard_error_union_and_returns_stable_error() -> None:
    class ErrorCallbackService:
        callbacks: list[OAuthCallback] = []

        async def complete_callback(self, callback: OAuthCallback) -> None:
            self.callbacks.append(callback)
            raise ProviderOAuthError("provider_oauth_authorization_failed")

    service = ErrorCallbackService()
    app = Starlette(
        routes=cloud_routes(
            CloudDependencies(
                settings=_settings(),
                provider_oauth_service=service,
            )
        )
    )
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={
            "error": "access_denied",
            "state": "A" * 43,
            "error_description": "USER_DENIED_DESCRIPTION_SENTINEL",
            "error_uri": "https://provider.example/errors/ERROR_URI_SENTINEL",
        },
    )
    both = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={"code": "code", "error": "access_denied", "state": "B" * 43},
    )
    duplicate_description = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params=[
            ("error", "access_denied"),
            ("state", "C" * 43),
            ("error_description", "first"),
            ("error_description", "second"),
        ],
    )
    success_with_error_metadata = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={
            "code": "code",
            "state": "D" * 43,
            "error_description": "must-not-attach-to-success",
        },
    )
    oversized_description = client.get(
        FLOWACCOUNT_CALLBACK_PATH,
        params={
            "error": "access_denied",
            "state": "E" * 43,
            "error_description": "x" * 1025,
        },
    )

    assert response.status_code == 400
    assert response.json() == {"error": "provider_oauth_authorization_failed"}
    assert both.status_code == 400
    assert both.json() == {"error": "provider_oauth_callback_invalid"}
    assert duplicate_description.status_code == 400
    assert duplicate_description.json() == {"error": "provider_oauth_callback_invalid"}
    assert success_with_error_metadata.status_code == 400
    assert success_with_error_metadata.json() == {"error": "provider_oauth_callback_invalid"}
    assert oversized_description.status_code == 400
    assert oversized_description.json() == {"error": "provider_oauth_callback_invalid"}
    assert service.callbacks[0].model_fields_set == {"error", "state"}
    assert "access_denied" not in response.text
    assert "A" * 43 not in response.text
    rendered = f"{service.callbacks[0]!r} {service.callbacks[0].model_dump_json()}"
    assert "USER_DENIED_DESCRIPTION_SENTINEL" not in rendered
    assert "ERROR_URI_SENTINEL" not in rendered
    assert "USER_DENIED_DESCRIPTION_SENTINEL" not in response.text
    assert "ERROR_URI_SENTINEL" not in response.text


def test_callback_model_rejects_nonstandard_company_parameter() -> None:
    with pytest.raises(ValidationError):
        OAuthCallback(
            code="authorization-code",
            state="A" * 43,
            selected_company_id="company-123",
        )


def test_v1_cloud_dependencies_require_explicit_provider_oauth_service() -> None:
    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_missing",
    ):
        CloudDependencies(settings=replace(_settings(), v1_enabled=True))

    dependencies = CloudDependencies(settings=_settings())
    assert FLOWACCOUNT_CALLBACK_PATH not in {route.path for route in cloud_routes(dependencies)}


def test_oauth_models_errors_and_repr_do_not_retain_secrets() -> None:
    callback = OAuthCallback(
        code="AUTHORIZATION_CODE_SENTINEL",
        state="A" * 43,
        provider="flowaccount",
        environment="sandbox",
        workspace_id=WORKSPACE_ID,
        redirect_uri=CALLBACK_URI,
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
