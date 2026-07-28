from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict

from mercury_tools.credentials.models import CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderResponseInvalid,
    ProviderStatusClass,
    ProviderValidation,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.flowaccount import (
    FlowAccountCredentialError,
    FlowAccountMCPDriver,
    FlowAccountOAuthHeaderFactory,
    FlowAccountOAuthTokens,
    FlowAccountProfile,
    FlowAccountProfileRequest,
    FlowAccountRefreshRequest,
    normalize_flowaccount_response,
    seal_flowaccount_credentials,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.registry import ProviderRegistryError, build_provider_registry

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = load_provider_manifest(ROOT / "catalog/global/flowaccount/driver.json")
NOW = datetime(2026, 7, 27, 4, 0, tzinfo=UTC)
TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
WORKSPACE_ID = UUID("22222222-2222-4222-8222-222222222222")
USER_ID = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-8444-444444444444")
OPERATION_ID = UUID("55555555-5555-4555-8555-555555555555")


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _connection(
    readiness: ConnectionReadiness = ConnectionReadiness.READY,
) -> ProviderConnection:
    return ProviderConnection(
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
        readiness=readiness,
        revision=1,
        last_validated_at=(NOW if readiness is ConnectionReadiness.READY else None),
        credential_envelope_ids=(UUID("66666666-6666-4666-8666-666666666666"),),
        created_at=NOW,
        updated_at=NOW,
    )


def _binding(
    capability: str,
    provider_tool: str,
) -> QualifiedCapabilityBinding:
    return QualifiedCapabilityBinding(
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        normalized_capability=capability,
        provider_tool=provider_tool,
        operation_class=ProviderOperationClass.READ,
        qualification_hash="a" * 64,
    )


class FakeRuntime:
    provider = ProviderId.FLOWACCOUNT

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    async def discover(self, _connection: ProviderConnection) -> ProviderDiscovery:
        self.events.append(("discover", None))
        return ProviderDiscovery(
            provider=ProviderId.FLOWACCOUNT,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "capabilities": [
                    "documents.invoice.get",
                    "provider_profile.get",
                ],
                "resource_uri_sha256": "b" * 64,
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )

    async def validate_connection(
        self,
        _connection: ProviderConnection,
    ) -> ProviderValidation:
        raise AssertionError("FlowAccount validation must call provider_profile.get")

    async def call(
        self,
        _connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult:
        self.events.append(
            (
                "call",
                (
                    binding.normalized_capability,
                    binding.provider_tool,
                    type(arguments),
                    operation_id,
                ),
            )
        )
        if binding.normalized_capability == "provider_profile.get":
            data: dict[str, Any] = {
                "company_id": "company-123",
                "company_display_name": "FlowAccount Test Company",
            }
        else:
            data = {"invoice_id": "invoice-123"}
        return ProviderCallResult(
            provider=ProviderId.FLOWACCOUNT,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data=data,
            dispatch_certainty=DispatchCertainty.DISPATCHED,
        )


def _profile_binding(
    _connection: ProviderConnection,
    provider_tool: str,
) -> QualifiedCapabilityBinding:
    return _binding("provider_profile.get", provider_tool)


def _registry_dependencies() -> dict[str, object]:
    return {
        "header_factories": {
            AuthorizationMethod.OAUTH2_PKCE: lambda _connection: None,
        },
        "binding_verifier": lambda _connection, _binding, _resource_hash: None,
        "response_normalizer": lambda _binding, _content: None,
        "request_model_resolver": lambda _binding: FlowAccountProfileRequest,
        "response_model_resolver": lambda _binding: FlowAccountProfile,
        "flowaccount_profile_binding_resolver": _profile_binding,
    }


@pytest.mark.asyncio
async def test_flowaccount_driver_maps_manifest_discovery_and_exact_profile_validation() -> None:
    runtime = FakeRuntime()
    driver = FlowAccountMCPDriver(
        runtime=runtime,
        manifest=MANIFEST,
        profile_binding_resolver=_profile_binding,
    )

    discovery = await driver.discover(_connection(ConnectionReadiness.REQUIRES_VALIDATION))
    validation = await driver.validate_connection(
        _connection(ConnectionReadiness.REQUIRES_VALIDATION)
    )

    assert discovery.normalized_data["capabilities"] == (
        "documents.invoice.get",
        "provider_profile.get",
    )
    assert validation.normalized_data == {
        "company_id": "company-123",
        "company_display_name": "FlowAccount Test Company",
    }
    assert runtime.events == [
        ("discover", None),
        (
            "call",
            (
                "provider_profile.get",
                "get_provider_profile",
                FlowAccountProfileRequest,
                runtime.events[1][1][3],
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_flowaccount_driver_rejects_unmapped_or_mismatched_qualified_calls() -> None:
    runtime = FakeRuntime()
    driver = FlowAccountMCPDriver(
        runtime=runtime,
        manifest=MANIFEST,
        profile_binding_resolver=_profile_binding,
    )

    with pytest.raises(ProviderResponseInvalid):
        await driver.call(
            _connection(),
            _binding("documents.invoice.get", "model_supplied_tool"),
            EmptyArguments(),
            OPERATION_ID,
        )
    with pytest.raises(ProviderResponseInvalid):
        await driver.call(
            _connection(),
            _binding("unknown.capability", "get_invoice"),
            EmptyArguments(),
            OPERATION_ID,
        )
    with pytest.raises(ProviderResponseInvalid):
        await driver.call(
            _connection(ConnectionReadiness.REQUIRES_VALIDATION),
            _binding("documents.invoice.get", "get_invoice"),
            EmptyArguments(),
            OPERATION_ID,
        )

    assert runtime.events == []


def test_profile_normalizer_accepts_only_exact_company_shape() -> None:
    binding = _binding("provider_profile.get", "get_provider_profile")

    normalized = normalize_flowaccount_response(
        binding,
        {
            "company": {
                "id": "company-123",
                "display_name": "FlowAccount Test Company",
            }
        },
    )

    assert normalized == FlowAccountProfile(
        company_id="company-123",
        company_display_name="FlowAccount Test Company",
    )
    with pytest.raises(ValueError, match="^flowaccount_response_invalid$"):
        normalize_flowaccount_response(
            binding,
            {
                "company": {
                    "id": "company-123",
                    "display_name": "FlowAccount Test Company",
                    "access_token": "secret",
                }
            },
        )


class CredentialRepository:
    def __init__(self, envelopes: tuple[CredentialEnvelope, ...]) -> None:
        self.envelopes = envelopes
        self.saved: list[tuple[CredentialEnvelope, ...]] = []

    def load(self, _connection: ProviderConnection) -> tuple[CredentialEnvelope, ...]:
        return self.envelopes

    def save(
        self,
        _connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> None:
        self.envelopes = envelopes
        self.saved.append(envelopes)


def _vault() -> CredentialVault:
    nonces = iter(bytes([index]) * 12 for index in range(1, 64))
    return CredentialVault(
        active_key_version="v1",
        keys={"v1": b"v" * 32},
        clock=lambda: NOW,
        nonce_factory=lambda _size: next(nonces),
    )


@pytest.mark.asyncio
async def test_encrypted_header_factory_refreshes_at_most_once_before_dispatch() -> None:
    vault = _vault()
    connection = _connection()
    expired = FlowAccountOAuthTokens(
        access_token="EXPIRED_ACCESS_TOKEN_SENTINEL",
        refresh_token="REFRESH_TOKEN_SENTINEL",
        token_type="Bearer",
        expires_at=NOW - timedelta(seconds=1),
        granted_permissions=("documents.read", "profile.read"),
    )
    envelopes = seal_flowaccount_credentials(
        vault=vault,
        connection=connection,
        tokens=expired,
        token_endpoint="https://identity.flowaccount.example/oauth/token",
        resource_uri="https://flowaccount-sandbox.example/mcp",
        client_id="dynamic-client-id",
        client_secret="DYNAMIC_CLIENT_SECRET_SENTINEL",
        token_endpoint_auth_method="client_secret_basic",
    )
    repository = CredentialRepository(envelopes)
    refreshes: list[FlowAccountRefreshRequest] = []

    async def refresh(request: FlowAccountRefreshRequest) -> FlowAccountOAuthTokens:
        refreshes.append(request)
        return FlowAccountOAuthTokens(
            access_token="NEW_ACCESS_TOKEN_SENTINEL",
            refresh_token="NEW_REFRESH_TOKEN_SENTINEL",
            token_type="bearer",
            expires_at=NOW + timedelta(hours=1),
            granted_permissions=("documents.read", "profile.read"),
        )

    factory = FlowAccountOAuthHeaderFactory(
        vault=vault,
        load_envelopes=repository.load,
        save_envelopes=repository.save,
        refresh=refresh,
        clock=lambda: NOW,
    )

    first = await factory(connection)
    second = await factory(connection)

    assert len(refreshes) == 1
    assert len(repository.saved) == 1
    assert first.headers[0].name == "Authorization"
    assert first.headers[0].value == "Bearer NEW_ACCESS_TOKEN_SENTINEL"
    assert second.headers[0].value == "Bearer NEW_ACCESS_TOKEN_SENTINEL"
    serialized = (
        first.model_dump_json()
        + second.model_dump_json()
        + repr(refreshes)
        + repr(repository.envelopes)
    )
    for secret in (
        "EXPIRED_ACCESS_TOKEN_SENTINEL",
        "REFRESH_TOKEN_SENTINEL",
        "DYNAMIC_CLIENT_SECRET_SENTINEL",
        "NEW_ACCESS_TOKEN_SENTINEL",
        "NEW_REFRESH_TOKEN_SENTINEL",
    ):
        assert secret not in serialized


@pytest.mark.asyncio
async def test_expired_token_without_refresh_support_fails_closed() -> None:
    vault = _vault()
    connection = _connection()
    envelopes = seal_flowaccount_credentials(
        vault=vault,
        connection=connection,
        tokens=FlowAccountOAuthTokens(
            access_token="EXPIRED_ACCESS_TOKEN_SENTINEL",
            refresh_token=None,
            token_type="Bearer",
            expires_at=NOW - timedelta(seconds=1),
            granted_permissions=("profile.read",),
        ),
        token_endpoint="https://identity.flowaccount.example/oauth/token",
        resource_uri="https://flowaccount-sandbox.example/mcp",
        client_id="dynamic-client-id",
        client_secret=None,
        token_endpoint_auth_method="none",
    )
    repository = CredentialRepository(envelopes)

    async def refresh(_request: FlowAccountRefreshRequest) -> FlowAccountOAuthTokens:
        raise AssertionError("refresh must not run without a refresh token")

    factory = FlowAccountOAuthHeaderFactory(
        vault=vault,
        load_envelopes=repository.load,
        save_envelopes=repository.save,
        refresh=refresh,
        clock=lambda: NOW,
    )

    with pytest.raises(
        FlowAccountCredentialError,
        match="^flowaccount_reauthorization_required$",
    ):
        await factory(connection)


@pytest.mark.asyncio
async def test_refresh_rejects_reduced_or_changed_scope_without_returning_header() -> None:
    vault = _vault()
    connection = _connection()
    envelopes = seal_flowaccount_credentials(
        vault=vault,
        connection=connection,
        tokens=FlowAccountOAuthTokens(
            access_token="EXPIRED_ACCESS_TOKEN_SENTINEL",
            refresh_token="REFRESH_TOKEN_SENTINEL",
            token_type="Bearer",
            expires_at=NOW - timedelta(seconds=1),
            granted_permissions=("documents.read", "profile.read"),
        ),
        token_endpoint="https://identity.flowaccount.example/oauth/token",
        resource_uri="https://flowaccount-sandbox.example/mcp",
        client_id="dynamic-client-id",
        client_secret=None,
        token_endpoint_auth_method="none",
    )
    repository = CredentialRepository(envelopes)

    async def refresh(_request: FlowAccountRefreshRequest) -> FlowAccountOAuthTokens:
        return FlowAccountOAuthTokens(
            access_token="REDUCED_SCOPE_TOKEN_SENTINEL",
            refresh_token="REFRESH_TOKEN_SENTINEL",
            token_type="Bearer",
            expires_at=NOW + timedelta(hours=1),
            granted_permissions=("profile.read",),
        )

    factory = FlowAccountOAuthHeaderFactory(
        vault=vault,
        load_envelopes=repository.load,
        save_envelopes=repository.save,
        refresh=refresh,
        clock=lambda: NOW,
    )

    with pytest.raises(
        FlowAccountCredentialError,
        match="^flowaccount_reauthorization_required$",
    ):
        await factory(connection)
    assert repository.saved == []


def test_registry_can_wrap_only_flowaccount_with_task6_profile_validation() -> None:
    settings = __import__("mercury_tools.config", fromlist=["Settings"]).Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
    )
    registry = build_provider_registry(
        settings=settings,
        manifest_root=ROOT / "catalog/global",
        **_registry_dependencies(),
    )

    assert isinstance(registry.get("flowaccount"), FlowAccountMCPDriver)
    assert not isinstance(registry.get("peak"), FlowAccountMCPDriver)


@pytest.mark.parametrize(
    ("dependency", "invalid_value"),
    [
        ("header_factories", {}),
        (
            "header_factories",
            {AuthorizationMethod.OAUTH2_PKCE: "not-callable"},
        ),
        ("binding_verifier", None),
        ("binding_verifier", 7),
        ("response_normalizer", object()),
        ("request_model_resolver", "not-callable"),
        ("response_model_resolver", None),
        ("flowaccount_profile_binding_resolver", object()),
    ],
)
def test_registry_fails_closed_for_incomplete_or_noncallable_flowaccount_wiring(
    dependency: str,
    invalid_value: object,
) -> None:
    settings = __import__("mercury_tools.config", fromlist=["Settings"]).Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
    )
    dependencies = _registry_dependencies()
    dependencies[dependency] = invalid_value

    with pytest.raises(
        ProviderRegistryError,
        match="^flowaccount_runtime_dependencies_invalid$",
    ):
        build_provider_registry(
            settings=settings,
            manifest_root=ROOT / "catalog/global",
            **dependencies,
        )


def test_v1_hosted_path_does_not_import_or_call_direct_rest_driver() -> None:
    oauth_source = (ROOT / "src/mercury_tools/providers/oauth.py").read_text(encoding="utf-8")
    provider_source = (ROOT / "src/mercury_tools/providers/flowaccount.py").read_text(
        encoding="utf-8"
    )
    registry_source = (ROOT / "src/mercury_tools/providers/registry.py").read_text(encoding="utf-8")

    combined = oauth_source + provider_source + registry_source
    assert "mercury_tools.drivers.flowaccount" not in combined
    assert "FlowAccountDriver" not in combined
