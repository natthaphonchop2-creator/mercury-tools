from __future__ import annotations

import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from mercury_tools.config import Settings, V1ConfigurationError
from mercury_tools.credentials.models import CredentialBinding, CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.flowaccount import (
    FlowAccountMCPDriver,
    FlowAccountOAuthTokens,
    open_flowaccount_tokens,
    seal_flowaccount_credentials,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.oauth import (
    DownstreamMCPOAuthClient,
    PublicOAuthNetworkGuard,
    SupabaseProviderOAuthStateStore,
)
from mercury_tools.providers.production import (
    build_provider_oauth_production_composition,
    build_test_provider_oauth_production_composition,
)
from mercury_tools.providers.store import ProviderStoreError, SupabaseProviderConnectionStore

NOW = datetime(2026, 7, 27, 8, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-8444-444444444444")
STAGED_CONNECTION_ID = UUID("55555555-5555-4555-8555-555555555555")
PROPOSED_CONNECTION_ID = UUID("66666666-6666-4666-8666-666666666666")


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "supabase_url": "https://project.example.supabase.co",
        "supabase_service_role_key": "SERVICE_ROLE_SENTINEL",
        "openai_api_key": "",
        "supabase_publishable_key": "publishable",
        "supabase_auth_issuer": "https://project.example.supabase.co/auth/v1",
        "supabase_jwks_url": "https://project.example.supabase.co/auth/v1/jwks",
        "supabase_jwt_audience": "https://mercury-tools-mcp.onrender.com/mcp",
        "vault_active_key": base64.b64encode(b"k" * 32).decode("ascii"),
        "vault_active_key_version": "v1",
        "v1_enabled": True,
        "flowaccount_mcp_sandbox_url": "https://flowaccount-sandbox.example/mcp",
        "flowaccount_mcp_production_url": "https://flowaccount.example/mcp",
        "flowaccount_oauth_sandbox_authorization_server_origin": (
            "https://identity-sandbox.flowaccount.example"
        ),
        "flowaccount_oauth_production_authorization_server_origin": (
            "https://identity.flowaccount.example"
        ),
        "peak_mcp_uat_url": "https://peak-uat.example/mcp",
        "peak_mcp_production_url": "https://peak.example/mcp",
        "provider_callback_base_url": "https://mercury-tools-mcp.onrender.com",
    }
    values.update(updates)
    return Settings(**values)


def _vault() -> CredentialVault:
    return CredentialVault(
        active_key_version="v1",
        keys={"v1": b"k" * 32},
        clock=lambda: NOW,
    )


def _envelopes(
    *,
    connection_id: UUID = CONNECTION_ID,
    account_id: str = "company-123",
):
    vault = _vault()

    def binding(credential_type: str) -> CredentialBinding:
        return CredentialBinding(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=connection_id,
            provider="flowaccount",
            company_or_merchant_id=account_id,
            environment="sandbox",
            credential_type=credential_type,
        )

    return (
        vault.seal(binding("access_token"), b"ACCESS_TOKEN_SENTINEL"),
        vault.seal(binding("refresh_token"), b"REFRESH_TOKEN_SENTINEL"),
    )


def _save_row(
    *,
    revision: int,
    connection_id: UUID = CONNECTION_ID,
    readiness: str = "ready",
    account_display_name: str = "FlowAccount Test Company",
    last_validated_at: str | None = NOW.isoformat(),
    provider_revocation_required: bool = False,
) -> dict[str, object]:
    return {
        "connection_id": str(connection_id),
        "provider": "flowaccount",
        "environment": "sandbox",
        "account_display_name": account_display_name,
        "authorization_method": "oauth2_pkce",
        "granted_permissions": ["documents.read", "profile.read"],
        "readiness": readiness,
        "revision": revision,
        "last_validated_at": last_validated_at,
        "provider_revocation_required": provider_revocation_required,
        "created_at": NOW.isoformat(),
        "updated_at": NOW.isoformat(),
    }


def _save(
    store: SupabaseProviderConnectionStore,
    *,
    revision: int = 1,
    envelopes: tuple[CredentialEnvelope, ...] | None = None,
):
    return store.save_connection(
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        connection_id=CONNECTION_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        company_or_merchant_id="company-123",
        account_display_name="FlowAccount Test Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read", "profile.read"),
        readiness=ConnectionReadiness.READY,
        revision=revision,
        validated_at=NOW,
        envelopes=envelopes or _envelopes(),
    )


def test_supabase_connection_store_uses_exact_service_role_rpc_bindings() -> None:
    requests: list[tuple[str, dict[str, object], str]] = []
    persisted_envelopes = _envelopes()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(
            (
                request.url.path,
                payload,
                request.headers["authorization"],
            )
        )
        if request.url.path.endswith("/save_mercury_provider_connection"):
            return httpx.Response(200, json=[_save_row(revision=1)])
        if request.url.path.endswith("/load_mercury_provider_credential_envelopes"):
            return httpx.Response(
                200,
                json=[
                    {
                        **envelope.model_dump(mode="json"),
                        "id": str(envelope.id),
                        "tenant_id": str(envelope.tenant_id),
                        "workspace_id": str(envelope.workspace_id),
                        "auth_user_id": str(envelope.auth_user_id),
                        "connection_id": str(envelope.connection_id),
                        "nonce": f"\\x{envelope.nonce.hex()}",
                        "ciphertext": f"\\x{envelope.ciphertext.hex()}",
                        "aad_hash": f"\\x{envelope.aad_hash.hex()}",
                    }
                    for envelope in persisted_envelopes
                ],
            )
        if request.url.path.endswith("/disconnect_mercury_provider_connection"):
            return httpx.Response(
                200,
                json=[
                    {
                        "connection_id": str(CONNECTION_ID),
                        "status": "disconnected",
                        "deleted_envelope_count": 2,
                        "already_disconnected": False,
                        "provider_revocation_required": True,
                        "revision": 2,
                    }
                ],
            )
        if request.url.path.endswith("/complete_mercury_provider_revocation"):
            return httpx.Response(
                200,
                json=[
                    {
                        "connection_id": str(CONNECTION_ID),
                        "status": "disconnected",
                        "deleted_envelope_count": 0,
                        "already_disconnected": True,
                        "provider_revocation_required": False,
                        "revision": 2,
                    }
                ],
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=_settings(),
            vault=_vault(),
            http_client=client,
        )
        connection = _save(store, envelopes=persisted_envelopes)
        loaded = store.load_envelopes(connection)
        disconnected = store.disconnect(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=CONNECTION_ID,
            provider_revocation_required=True,
        )
        completed = store.complete_revocation(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=CONNECTION_ID,
        )

    assert connection.id == CONNECTION_ID
    assert len(loaded) == 2
    assert disconnected.provider_revocation_required is True
    assert completed.provider_revocation_required is False
    assert [path.rsplit("/", 1)[-1] for path, _, _ in requests] == [
        "save_mercury_provider_connection",
        "load_mercury_provider_credential_envelopes",
        "disconnect_mercury_provider_connection",
        "complete_mercury_provider_revocation",
    ]
    for _, payload, authorization in requests:
        assert payload["p_tenant_id"] == str(TENANT_ID)
        assert payload["p_workspace_id"] == str(WORKSPACE_ID)
        assert payload["p_auth_user_id"] == str(USER_ID)
        assert authorization == "Bearer SERVICE_ROLE_SENTINEL"
    save_payload = requests[0][1]
    assert set(save_payload) == {
        "p_connection_id",
        "p_tenant_id",
        "p_workspace_id",
        "p_auth_user_id",
        "p_provider",
        "p_environment",
        "p_provider_account_id",
        "p_account_display_name",
        "p_authorization_method",
        "p_granted_permissions",
        "p_readiness",
        "p_revision",
        "p_last_validated_at",
        "p_envelopes",
    }
    serialized = json.dumps(requests)
    assert "ACCESS_TOKEN_SENTINEL" not in serialized
    assert "REFRESH_TOKEN_SENTINEL" not in serialized


def test_supabase_connection_store_maps_concurrent_revision_conflict() -> None:
    lock = threading.Lock()
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        with lock:
            calls += 1
            current = calls
        if current == 1:
            return httpx.Response(200, json=[_save_row(revision=1)])
        return httpx.Response(
            409,
            json={"message": "provider_connection_conflict"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=_settings(),
            vault=_vault(),
            http_client=client,
        )

        def save_once(_value: int):
            try:
                return _save(store)
            except Exception as exc:
                return exc

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(save_once, range(2)))

    successes = [item for item in outcomes if not isinstance(item, Exception)]
    failures = [item for item in outcomes if isinstance(item, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ProviderStoreError)
    assert str(failures[0]) == "provider_connection_conflict"


def test_supabase_runtime_refresh_routes_ready_connection_to_public_store() -> None:
    persisted = _envelopes()
    replacement = _envelopes()
    requests: list[tuple[str, dict[str, object]]] = []
    connection = ProviderConnection(
        id=CONNECTION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        auth_user_id=USER_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        provider_account_id="company-123",
        account_display_name="FlowAccount Test Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read", "profile.read"),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=NOW,
        credential_envelope_ids=tuple(envelope.id for envelope in persisted),
        created_at=NOW,
        updated_at=NOW,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((request.url.path.rsplit("/", 1)[-1], payload))
        return httpx.Response(200, json=[_save_row(revision=2)])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=_settings(),
            vault=_vault(),
            http_client=client,
        )
        refreshed = store.replace_runtime_envelopes(connection, replacement)

    assert refreshed.readiness is ConnectionReadiness.READY
    assert refreshed.revision == 2
    assert requests[0][0] == "save_mercury_provider_connection"
    assert requests[0][1]["p_revision"] == 2
    assert requests[0][1]["p_envelopes"]


def test_supabase_reconnect_store_uses_exact_atomic_rpc_bindings() -> None:
    requests: list[tuple[str, dict[str, object]]] = []
    pending_account_id = "oauth-pending-state"
    staged_envelopes = _envelopes(
        connection_id=STAGED_CONNECTION_ID,
        account_id=pending_account_id,
    )
    exact_envelopes = _envelopes()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        function = request.url.path.rsplit("/", 1)[-1]
        requests.append((function, payload))
        if function == "stage_mercury_provider_connection":
            return httpx.Response(
                200,
                json=[
                    _save_row(
                        revision=1,
                        connection_id=STAGED_CONNECTION_ID,
                        readiness="requires_validation",
                        account_display_name="FlowAccount",
                        last_validated_at=None,
                        provider_revocation_required=True,
                    )
                ],
            )
        if function == "resolve_mercury_provider_connection_target":
            return httpx.Response(
                200,
                json=[
                    {
                        "connection_id": str(CONNECTION_ID),
                        "revision": 3,
                        "reuses_existing": True,
                    }
                ],
            )
        if function == "finalize_mercury_provider_connection":
            return httpx.Response(200, json=[_save_row(revision=3)])
        if function == "record_mercury_provider_revocation_obligation":
            return httpx.Response(
                200,
                json=[
                    {
                        "connection_id": str(STAGED_CONNECTION_ID),
                        "status": "disconnected",
                        "deleted_envelope_count": 0,
                        "already_disconnected": True,
                        "provider_revocation_required": True,
                        "revision": 2,
                    }
                ],
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=_settings(),
            vault=_vault(),
            http_client=client,
        )
        staged = store.stage_connection(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=STAGED_CONNECTION_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id=pending_account_id,
            account_display_name="FlowAccount",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=("documents.read", "profile.read"),
            readiness=ConnectionReadiness.REQUIRES_VALIDATION,
            revision=1,
            validated_at=None,
            envelopes=staged_envelopes,
        )
        target = store.resolve_connection_target(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id="company-123",
            proposed_connection_id=PROPOSED_CONNECTION_ID,
        )
        finalized = store.finalize_connection(
            staged_connection_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=target.connection_id,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id="company-123",
            account_display_name="FlowAccount Test Company",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=("documents.read", "profile.read"),
            readiness=ConnectionReadiness.READY,
            revision=target.revision,
            validated_at=NOW,
            envelopes=exact_envelopes,
        )
        obligation = store.record_revocation_obligation(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=STAGED_CONNECTION_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id=pending_account_id,
            account_display_name="FlowAccount",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=("documents.read", "profile.read"),
        )

    assert staged.provider_revocation_required is True
    assert target.connection_id == CONNECTION_ID
    assert target.revision == 3
    assert target.reuses_existing is True
    assert finalized.id == CONNECTION_ID
    assert finalized.revision == 3
    assert obligation.provider_revocation_required is True
    assert [function for function, _payload in requests] == [
        "stage_mercury_provider_connection",
        "resolve_mercury_provider_connection_target",
        "finalize_mercury_provider_connection",
        "record_mercury_provider_revocation_obligation",
    ]
    assert requests[1][1]["p_proposed_connection_id"] == str(PROPOSED_CONNECTION_ID)
    assert requests[2][1]["p_staged_connection_id"] == str(STAGED_CONNECTION_ID)
    for _function, payload in requests:
        assert payload["p_tenant_id"] == str(TENANT_ID)
        assert payload["p_workspace_id"] == str(WORKSPACE_ID)
        assert payload["p_auth_user_id"] == str(USER_ID)


def test_supabase_oauth_attempt_store_uses_stable_attempt_rpc_bindings() -> None:
    requests: list[tuple[str, dict[str, object]]] = []
    pending_account_id = f"oauth-pending-{STAGED_CONNECTION_ID}"
    attempt_envelopes = _envelopes(
        connection_id=STAGED_CONNECTION_ID,
        account_id=pending_account_id,
    )
    rotated_attempt_envelopes = _envelopes(
        connection_id=STAGED_CONNECTION_ID,
        account_id=pending_account_id,
    )
    exact_envelopes = _envelopes()
    replacement_calls = 0

    def attempt_row(
        status: str,
        *,
        revocation_required: bool,
        target_connection_id: UUID | None = None,
        target_revision: int | None = None,
    ) -> dict[str, object]:
        return {
            "attempt_id": str(STAGED_CONNECTION_ID),
            "status": status,
            "provider_revocation_required": revocation_required,
            "target_connection_id": (
                str(target_connection_id) if target_connection_id is not None else None
            ),
            "target_revision": target_revision,
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal replacement_calls
        payload = json.loads(request.content)
        function = request.url.path.rsplit("/", 1)[-1]
        requests.append((function, payload))
        if function == "begin_mercury_provider_oauth_attempt":
            return httpx.Response(
                200,
                json=[
                    attempt_row(
                        "exchange_pending",
                        revocation_required=True,
                    )
                ],
            )
        if function == "attach_mercury_provider_oauth_attempt":
            return httpx.Response(
                200,
                json=[
                    attempt_row(
                        "material_attached",
                        revocation_required=True,
                    )
                ],
            )
        if function == "load_mercury_provider_oauth_attempt_envelopes":
            return httpx.Response(
                200,
                json=[
                    {
                        **envelope.model_dump(mode="json"),
                        "id": str(envelope.id),
                        "tenant_id": str(envelope.tenant_id),
                        "workspace_id": str(envelope.workspace_id),
                        "auth_user_id": str(envelope.auth_user_id),
                        "connection_id": str(envelope.connection_id),
                        "nonce": f"\\x{envelope.nonce.hex()}",
                        "ciphertext": f"\\x{envelope.ciphertext.hex()}",
                        "aad_hash": f"\\x{envelope.aad_hash.hex()}",
                    }
                    for envelope in attempt_envelopes
                ],
            )
        if function == "replace_mercury_provider_oauth_attempt_envelopes":
            replacement_calls += 1
            if replacement_calls == 1:
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(
                200,
                json=[
                    {
                        "attempt_id": str(STAGED_CONNECTION_ID),
                        "material_revision": 2,
                        "credential_envelope_ids": [
                            str(envelope.id) for envelope in rotated_attempt_envelopes
                        ],
                        "created_at": NOW.isoformat(),
                        "updated_at": NOW.isoformat(),
                    }
                ],
            )
        if function == "resolve_mercury_provider_connection_target":
            return httpx.Response(
                200,
                json=[
                    {
                        "connection_id": str(CONNECTION_ID),
                        "revision": 3,
                        "reuses_existing": True,
                    }
                ],
            )
        if function == "finalize_mercury_provider_oauth_attempt":
            return httpx.Response(
                200,
                json=[
                    _save_row(
                        revision=4,
                        readiness="requires_validation",
                    )
                ],
            )
        if function == "acknowledge_mercury_provider_oauth_attempt":
            return httpx.Response(200, json=[_save_row(revision=5)])
        if function == "fail_mercury_provider_oauth_attempt":
            return httpx.Response(
                200,
                json=[
                    attempt_row(
                        "failed",
                        revocation_required=True,
                        target_connection_id=CONNECTION_ID,
                        target_revision=5,
                    )
                ],
            )
        if function == "complete_mercury_provider_oauth_attempt_revocation":
            return httpx.Response(
                200,
                json=[
                    attempt_row(
                        "revoked",
                        revocation_required=False,
                        target_connection_id=CONNECTION_ID,
                        target_revision=5,
                    )
                ],
            )
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseProviderConnectionStore(
            settings=_settings(),
            vault=_vault(),
            http_client=client,
        )
        attempt = store.begin_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            granted_permissions=("documents.read", "profile.read"),
        )
        provisional = store.attach_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id=pending_account_id,
            account_display_name="FlowAccount",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=("documents.read", "profile.read"),
            readiness=ConnectionReadiness.REQUIRES_VALIDATION,
            revision=1,
            validated_at=None,
            envelopes=attempt_envelopes,
        )
        loaded = store.load_runtime_envelopes(provisional)
        rotated = store.replace_oauth_attempt_envelopes(
            provisional,
            rotated_attempt_envelopes,
        )
        target = store.resolve_connection_target(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id="company-123",
            proposed_connection_id=PROPOSED_CONNECTION_ID,
        )
        finalized = store.finalize_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            connection_id=target.connection_id,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            company_or_merchant_id="company-123",
            account_display_name="FlowAccount Test Company",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=("documents.read", "profile.read"),
            readiness=ConnectionReadiness.REQUIRES_VALIDATION,
            revision=target.revision,
            validated_at=NOW,
            envelopes=exact_envelopes,
        )
        acknowledged = store.acknowledge_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            connection=finalized,
        )
        acknowledged_again = store.acknowledge_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
            connection=acknowledged,
        )
        failed = store.fail_oauth_attempt(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
        )
        revoked = store.complete_oauth_attempt_revocation(
            attempt_id=STAGED_CONNECTION_ID,
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            auth_user_id=USER_ID,
            provider=ProviderId.FLOWACCOUNT,
            environment="sandbox",
        )

    assert attempt.status == "exchange_pending"
    assert provisional.id == STAGED_CONNECTION_ID
    assert loaded == attempt_envelopes
    assert rotated.revision == 2
    assert rotated.credential_envelope_ids == tuple(
        envelope.id for envelope in rotated_attempt_envelopes
    )
    assert finalized.id == CONNECTION_ID
    assert finalized.readiness is ConnectionReadiness.REQUIRES_VALIDATION
    assert finalized.revision == 4
    assert acknowledged.readiness is ConnectionReadiness.READY
    assert acknowledged.revision == 5
    assert acknowledged_again == acknowledged
    assert failed.status == "failed"
    assert failed.target_connection_id == CONNECTION_ID
    assert revoked.status == "revoked"
    assert revoked.provider_revocation_required is False
    assert [function for function, _payload in requests] == [
        "begin_mercury_provider_oauth_attempt",
        "attach_mercury_provider_oauth_attempt",
        "load_mercury_provider_oauth_attempt_envelopes",
        "replace_mercury_provider_oauth_attempt_envelopes",
        "replace_mercury_provider_oauth_attempt_envelopes",
        "resolve_mercury_provider_connection_target",
        "finalize_mercury_provider_oauth_attempt",
        "acknowledge_mercury_provider_oauth_attempt",
        "acknowledge_mercury_provider_oauth_attempt",
        "fail_mercury_provider_oauth_attempt",
        "complete_mercury_provider_oauth_attempt_revocation",
    ]
    for _function, payload in requests:
        assert payload["p_tenant_id"] == str(TENANT_ID)
        assert payload["p_workspace_id"] == str(WORKSPACE_ID)
        assert payload["p_auth_user_id"] == str(USER_ID)
    for function, payload in requests:
        if function != "resolve_mercury_provider_connection_target":
            assert payload["p_attempt_id"] == str(STAGED_CONNECTION_ID)
    serialized = json.dumps(requests)
    assert "ACCESS_TOKEN_SENTINEL" not in serialized
    assert "REFRESH_TOKEN_SENTINEL" not in serialized


def test_production_composition_fails_closed_without_reviewed_origins() -> None:
    settings = _settings(
        flowaccount_oauth_sandbox_authorization_server_origin="",
    )
    with pytest.raises(
        V1ConfigurationError,
        match="v1_flowaccount_authorization_server_origin_missing",
    ):
        build_provider_oauth_production_composition(settings=settings)

    rendered = repr(settings)
    assert "SERVICE_ROLE_SENTINEL" not in rendered
    assert settings.vault_active_key not in rendered


@pytest.mark.asyncio
async def test_production_composition_rotates_attempt_material_before_validation_sealing() -> None:
    expired_access = "EXPIRED_PRODUCTION_COMPOSITION_ACCESS_SENTINEL"
    expired_refresh = "EXPIRED_PRODUCTION_COMPOSITION_REFRESH_SENTINEL"
    rotated_access = "ROTATED_PRODUCTION_COMPOSITION_ACCESS_SENTINEL"
    rotated_refresh = "ROTATED_PRODUCTION_COMPOSITION_REFRESH_SENTINEL"
    pending_account_id = f"oauth-pending-{STAGED_CONNECTION_ID}"
    token_endpoint = "https://identity-sandbox.flowaccount.example/token"
    settings = _settings()
    persisted_attempt_payloads: list[dict[str, object]] = []
    finalized_payloads: list[dict[str, object]] = []
    connection_requests: list[str] = []
    oauth_requests: list[httpx.Request] = []

    def rpc_envelope_rows(
        payloads: list[dict[str, object]],
        *,
        connection_id: UUID,
    ) -> list[dict[str, object]]:
        return [
            {
                **payload,
                "tenant_id": str(TENANT_ID),
                "workspace_id": str(WORKSPACE_ID),
                "auth_user_id": str(USER_ID),
                "connection_id": str(connection_id),
                "provider": "flowaccount",
                "environment": "sandbox",
                "nonce": f"\\x{payload['nonce']}",
                "ciphertext": f"\\x{payload['ciphertext']}",
                "aad_hash": f"\\x{payload['aad_hash']}",
            }
            for payload in payloads
        ]

    def connection_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        function = request.url.path.rsplit("/", 1)[-1]
        connection_requests.append(function)
        if function == "load_mercury_provider_oauth_attempt_envelopes":
            return httpx.Response(
                200,
                json=rpc_envelope_rows(
                    persisted_attempt_payloads,
                    connection_id=STAGED_CONNECTION_ID,
                ),
            )
        if function == "replace_mercury_provider_oauth_attempt_envelopes":
            persisted_attempt_payloads[:] = payload["p_envelopes"]
            return httpx.Response(
                200,
                json=[
                    {
                        "attempt_id": str(STAGED_CONNECTION_ID),
                        "material_revision": 2,
                        "credential_envelope_ids": [
                            item["id"] for item in persisted_attempt_payloads
                        ],
                        "created_at": NOW.isoformat(),
                        "updated_at": NOW.isoformat(),
                    }
                ],
            )
        if function == "finalize_mercury_provider_oauth_attempt":
            finalized_payloads[:] = payload["p_envelopes"]
            return httpx.Response(
                200,
                json=[
                    _save_row(
                        revision=1,
                        readiness="requires_validation",
                    )
                ],
            )
        if function == "acknowledge_mercury_provider_oauth_attempt":
            return httpx.Response(200, json=[_save_row(revision=2)])
        raise AssertionError(f"unexpected connection RPC: {function}")

    def oauth_handler(request: httpx.Request) -> httpx.Response:
        oauth_requests.append(request)
        assert request.url == token_endpoint
        assert b"grant_type=refresh_token" in request.content
        assert expired_refresh.encode() in request.content
        return httpx.Response(
            200,
            json={
                "access_token": rotated_access,
                "refresh_token": rotated_refresh,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "documents.read profile.read",
            },
        )

    class Resolver:
        async def resolve(self, _token: str):
            raise AssertionError("principal resolution is outside this regression")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=[], request=request)
            ),
            follow_redirects=False,
        ) as state_http,
        httpx.AsyncClient(
            transport=httpx.MockTransport(oauth_handler),
            follow_redirects=False,
        ) as guarded_http,
    ):
        with httpx.Client(
            transport=httpx.MockTransport(connection_handler),
            follow_redirects=False,
        ) as connection_http:
            network_guard = PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=guarded_http,
            )
            composition = build_test_provider_oauth_production_composition(
                settings=settings,
                principal_resolver=Resolver(),
                state_http_client=state_http,
                connection_http_client=connection_http,
                network_guard=network_guard,
                workspace_service=object(),
            )
            vault = composition.provider_oauth_service._vault
            provisional = ProviderConnection(
                id=STAGED_CONNECTION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                provider_account_id=pending_account_id,
                account_display_name="FlowAccount",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=("documents.read", "profile.read"),
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                credential_envelope_ids=(PROPOSED_CONNECTION_ID,),
                created_at=NOW,
                updated_at=NOW,
            )
            expired_envelopes = seal_flowaccount_credentials(
                vault=vault,
                connection=provisional,
                tokens=FlowAccountOAuthTokens(
                    access_token=expired_access,
                    refresh_token=expired_refresh,
                    token_type="Bearer",
                    expires_at=NOW - timedelta(seconds=1),
                    granted_permissions=provisional.granted_permissions,
                ),
                token_endpoint=token_endpoint,
                resource_uri=settings.flowaccount_mcp_sandbox_url,
                client_id="production-composition-client",
                client_secret="PRODUCTION_COMPOSITION_CLIENT_SECRET_SENTINEL",
                token_endpoint_auth_method="client_secret_basic",
            )
            persisted_attempt_payloads[:] = [
                composition.connection_store._envelope_payload(envelope)
                for envelope in expired_envelopes
            ]
            provisional = provisional.model_copy(
                update={
                    "credential_envelope_ids": tuple(envelope.id for envelope in expired_envelopes)
                }
            )

            flowaccount = composition.registry.get(ProviderId.FLOWACCOUNT)
            header_factory = flowaccount._runtime._header_factory
            discovery_headers = await header_factory(provisional)
            validation_headers = await header_factory(provisional)
            assert discovery_headers.headers[0].value == f"Bearer {rotated_access}"
            assert validation_headers.headers[0].value == f"Bearer {rotated_access}"

            latest_tokens = composition.provider_oauth_service._load_oauth_attempt_tokens(
                attempt_id=STAGED_CONNECTION_ID,
                record=SimpleNamespace(
                    tenant_id=TENANT_ID,
                    workspace_id=WORKSPACE_ID,
                    auth_user_id=USER_ID,
                    environment="sandbox",
                ),
                connection=provisional,
            )
            target = ProviderConnection(
                id=CONNECTION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                provider_account_id="company-123",
                account_display_name="FlowAccount Test Company",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=latest_tokens.granted_permissions,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                last_validated_at=NOW,
                credential_envelope_ids=(STAGED_CONNECTION_ID,),
                created_at=NOW,
                updated_at=NOW,
            )
            exact_envelopes = seal_flowaccount_credentials(
                vault=vault,
                connection=target,
                tokens=latest_tokens,
                token_endpoint=token_endpoint,
                resource_uri=settings.flowaccount_mcp_sandbox_url,
                client_id="production-composition-client",
                client_secret="PRODUCTION_COMPOSITION_CLIENT_SECRET_SENTINEL",
                token_endpoint_auth_method="client_secret_basic",
            )
            held = composition.connection_store.finalize_oauth_attempt(
                attempt_id=STAGED_CONNECTION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                connection_id=CONNECTION_ID,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                company_or_merchant_id=target.provider_account_id,
                account_display_name=target.account_display_name,
                authorization_method=target.authorization_method,
                granted_permissions=target.granted_permissions,
                readiness=target.readiness,
                revision=target.revision,
                validated_at=target.last_validated_at,
                envelopes=exact_envelopes,
            )
            ready = composition.connection_store.acknowledge_oauth_attempt(
                attempt_id=STAGED_CONNECTION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                connection=held,
            )
            finalized_envelopes = tuple(
                composition.connection_store._envelope_from_row(row)
                for row in rpc_envelope_rows(
                    finalized_payloads,
                    connection_id=CONNECTION_ID,
                )
            )
            finalized_tokens = open_flowaccount_tokens(
                vault=vault,
                connection=ready,
                envelopes=finalized_envelopes,
            )

    assert len(oauth_requests) == 1
    assert connection_requests == [
        "load_mercury_provider_oauth_attempt_envelopes",
        "replace_mercury_provider_oauth_attempt_envelopes",
        "load_mercury_provider_oauth_attempt_envelopes",
        "load_mercury_provider_oauth_attempt_envelopes",
        "finalize_mercury_provider_oauth_attempt",
        "acknowledge_mercury_provider_oauth_attempt",
    ]
    assert latest_tokens.access_token == rotated_access
    assert latest_tokens.refresh_token == rotated_refresh
    assert finalized_tokens.access_token == rotated_access
    assert finalized_tokens.refresh_token == rotated_refresh
    assert ready.readiness is ConnectionReadiness.READY


@pytest.mark.asyncio
async def test_production_composition_binds_durable_stores_guard_and_exact_registry() -> None:
    state_requests: list[tuple[str, dict[str, object], str]] = []

    def state_handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        state_requests.append(
            (
                request.url.path,
                payload,
                request.headers["authorization"],
            )
        )
        return httpx.Response(200, json=[{"cleaned_count": 0}])

    def no_sync_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected sync request: {request.url.path}")

    def no_oauth_network(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected OAuth request: {request.url.path}")

    class Resolver:
        async def resolve(self, _token: str):
            raise AssertionError("principal resolution is not part of composition")

    async with (
        httpx.AsyncClient(
            transport=httpx.MockTransport(state_handler),
            follow_redirects=False,
        ) as state_http,
        httpx.AsyncClient(
            transport=httpx.MockTransport(no_oauth_network),
            follow_redirects=False,
        ) as guarded_http,
    ):
        with httpx.Client(
            transport=httpx.MockTransport(no_sync_network),
            follow_redirects=False,
        ) as connection_http:
            network_guard = PublicOAuthNetworkGuard(
                resolver=lambda _host, _port: ("1.1.1.1",),
                http_client=guarded_http,
            )
            settings = _settings()
            composition = build_test_provider_oauth_production_composition(
                settings=settings,
                principal_resolver=Resolver(),
                state_http_client=state_http,
                connection_http_client=connection_http,
                network_guard=network_guard,
                workspace_service=object(),
            )
            with pytest.raises(
                V1ConfigurationError,
                match="v1_provider_oauth_composition_invalid",
            ):
                composition.validate_for_runtime(settings)
            composition.validate_for_test(settings)

            service = composition.provider_oauth_service
            flowaccount = composition.registry.get(ProviderId.FLOWACCOUNT)
            assert isinstance(composition.state_store, SupabaseProviderOAuthStateStore)
            assert isinstance(
                composition.connection_store,
                SupabaseProviderConnectionStore,
            )
            assert isinstance(flowaccount, FlowAccountMCPDriver)
            assert service._state_store is composition.state_store
            assert service._connection_store is composition.connection_store
            assert service._driver is flowaccount
            assert composition.connection_store._vault is service._vault
            assert isinstance(service._oauth_client, DownstreamMCPOAuthClient)
            assert service._oauth_client._network_guard is network_guard
            assert (
                flowaccount._runtime._header_factory._save_envelopes
                == composition.connection_store.replace_runtime_envelopes
            )
            assert service._oauth_client._authorization_server_origins == {
                (ProviderId.FLOWACCOUNT, "sandbox"): frozenset(
                    {"https://identity-sandbox.flowaccount.example"}
                ),
                (ProviderId.FLOWACCOUNT, "production"): frozenset(
                    {"https://identity.flowaccount.example"}
                ),
            }

            connection = ProviderConnection(
                id=CONNECTION_ID,
                tenant_id=TENANT_ID,
                workspace_id=WORKSPACE_ID,
                auth_user_id=USER_ID,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                provider_account_id="company-123",
                account_display_name="FlowAccount Test Company",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=("documents.read", "profile.read"),
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                credential_envelope_ids=(UUID("55555555-5555-4555-8555-555555555555"),),
                created_at=NOW,
                updated_at=NOW,
            )
            binding = flowaccount._profile_binding_resolver(
                connection,
                "get_provider_profile",
            )
            assert binding.normalized_capability == "provider_profile.get"
            assert binding.provider_tool == "get_provider_profile"

            await composition.startup()
            await composition.aclose()

    assert state_requests == [
        (
            "/rest/v1/rpc/cleanup_expired_mercury_provider_oauth_states",
            {"p_limit": 100},
            "Bearer SERVICE_ROLE_SENTINEL",
        )
    ]
