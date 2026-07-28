"""Fail-closed production composition for hosted FlowAccount OAuth."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
from pydantic import BaseModel

from mercury_tools.auth.middleware import current_mercury_access_token
from mercury_tools.auth.models import PrincipalResolver
from mercury_tools.config import Settings, V1ConfigurationError
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    ProviderOperationClass,
    ProviderQualificationState,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.flowaccount import (
    FlowAccountOAuthHeaderFactory,
    FlowAccountProfile,
    FlowAccountProfileRequest,
)
from mercury_tools.providers.manifest import (
    ProviderDriverManifest,
    load_provider_manifest,
    resolve_provider_resource,
)
from mercury_tools.providers.models import AuthorizationMethod, ProviderConnection, ProviderId
from mercury_tools.providers.oauth import (
    FLOWACCOUNT_CALLBACK_PATH,
    DownstreamMCPOAuthClient,
    ProviderOAuthService,
    PublicOAuthNetworkGuard,
    SupabaseProviderOAuthStateStore,
)
from mercury_tools.providers.registry import build_provider_registry
from mercury_tools.providers.store import SupabaseProviderConnectionStore
from mercury_tools.providers.streamable_mcp import wire_schema_sha256
from mercury_tools.workspaces.service import WorkspaceService

_PROFILE_CAPABILITY = "provider_profile.get"
_PROFILE_TOOL = "get_provider_profile"


@dataclass(repr=False)
class ProviderOAuthProductionComposition:
    provider_oauth_service: ProviderOAuthService
    state_store: SupabaseProviderOAuthStateStore
    connection_store: SupabaseProviderConnectionStore
    registry: Any
    network_guard: PublicOAuthNetworkGuard
    state_http_client: httpx.AsyncClient = field(repr=False)
    connection_http_client: httpx.Client = field(repr=False)
    owns_state_http_client: bool = field(default=True, repr=False)
    owns_connection_http_client: bool = field(default=True, repr=False)

    async def startup(self) -> None:
        await self.state_store.cleanup_expired(limit=100)

    async def aclose(self) -> None:
        await self.network_guard.aclose()
        if self.owns_state_http_client:
            await self.state_http_client.aclose()
        if self.owns_connection_http_client:
            self.connection_http_client.close()


def build_provider_oauth_production_composition(
    *,
    settings: Settings,
    principal_resolver: PrincipalResolver,
    state_http_client: httpx.AsyncClient | None = None,
    connection_http_client: httpx.Client | None = None,
    network_guard: PublicOAuthNetworkGuard | None = None,
    workspace_service: WorkspaceService | None = None,
) -> ProviderOAuthProductionComposition:
    """Compose the hosted path without provider URLs or credentials from input."""

    try:
        settings.validate_v1()
        if not settings.v1_enabled:
            raise V1ConfigurationError("v1_provider_oauth_composition_disabled")
        if not callable(getattr(principal_resolver, "resolve", None)):
            raise V1ConfigurationError("v1_principal_resolver_invalid")

        origins = MappingProxyType(
            {
                (ProviderId.FLOWACCOUNT, "sandbox"): (
                    settings.flowaccount_oauth_sandbox_authorization_server_origin,
                ),
                (ProviderId.FLOWACCOUNT, "production"): (
                    settings.flowaccount_oauth_production_authorization_server_origin,
                ),
            }
        )
        vault = CredentialVault.from_settings(settings)
        selected_state_http = state_http_client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=20,
        )
        selected_connection_http = connection_http_client or httpx.Client(
            follow_redirects=False,
            trust_env=False,
            timeout=20,
        )
        selected_network_guard = network_guard or PublicOAuthNetworkGuard()
        callback_uri = (
            f"{settings.provider_callback_base_url.rstrip('/')}{FLOWACCOUNT_CALLBACK_PATH}"
        )
        state_store = SupabaseProviderOAuthStateStore(
            settings=settings,
            http_client=selected_state_http,
            callback_uri=callback_uri,
        )
        connection_store = SupabaseProviderConnectionStore(
            settings=settings,
            vault=vault,
            http_client=selected_connection_http,
        )
        oauth_client = DownstreamMCPOAuthClient(
            network_guard=selected_network_guard,
            authorization_server_origins=origins,
        )
        manifest_root = Path(__file__).resolve().parents[3] / "catalog/global"
        manifest = load_provider_manifest(manifest_root / "flowaccount/driver.json")
        runtime_dependencies = _flowaccount_runtime_dependencies(
            settings=settings,
            manifest=manifest,
            vault=vault,
            connection_store=connection_store,
            oauth_client=oauth_client,
        )
        registry = build_provider_registry(
            settings=settings,
            manifest_root=manifest_root,
            **runtime_dependencies,
        )
        service = ProviderOAuthService(
            settings=settings,
            workspace_service=workspace_service or WorkspaceService.from_settings(settings),
            mercury_access_token=current_mercury_access_token,
            principal_resolver=principal_resolver,
            manifest=manifest,
            oauth_client=oauth_client,
            state_store=state_store,
            connection_store=connection_store,
            vault=vault,
            driver=registry.get(ProviderId.FLOWACCOUNT),
        )
        return ProviderOAuthProductionComposition(
            provider_oauth_service=service,
            state_store=state_store,
            connection_store=connection_store,
            registry=registry,
            network_guard=selected_network_guard,
            state_http_client=selected_state_http,
            connection_http_client=selected_connection_http,
            owns_state_http_client=state_http_client is None,
            owns_connection_http_client=connection_http_client is None,
        )
    except V1ConfigurationError:
        raise
    except Exception:
        raise V1ConfigurationError("v1_provider_oauth_composition_invalid") from None


def _flowaccount_runtime_dependencies(
    *,
    settings: Settings,
    manifest: ProviderDriverManifest,
    vault: CredentialVault,
    connection_store: SupabaseProviderConnectionStore,
    oauth_client: DownstreamMCPOAuthClient,
) -> dict[str, Any]:
    request_hash = wire_schema_sha256(FlowAccountProfileRequest)
    response_hash = wire_schema_sha256(FlowAccountProfile)

    def profile_binding(
        connection: ProviderConnection,
        provider_tool: str,
    ) -> QualifiedCapabilityBinding:
        if (
            connection.provider is not ProviderId.FLOWACCOUNT
            or connection.environment not in manifest.environments
            or provider_tool != _PROFILE_TOOL
        ):
            raise ValueError("provider_binding_invalid")
        return QualifiedCapabilityBinding(
            provider=ProviderId.FLOWACCOUNT,
            environment=connection.environment,
            normalized_capability=_PROFILE_CAPABILITY,
            provider_tool=_PROFILE_TOOL,
            operation_class=ProviderOperationClass.READ,
            qualification_hash=_profile_qualification_hash(
                manifest,
                environment=connection.environment,
            ),
        )

    def verify_binding(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        expected = profile_binding(connection, _PROFILE_TOOL)
        resource = resolve_provider_resource(
            settings=settings,
            manifest=manifest,
            environment=connection.environment,
        )
        if binding != expected or resource_uri_sha256 != resource.uri_sha256:
            raise ValueError("provider_binding_invalid")
        return VerifiedRuntimeBinding(
            qualification_state=ProviderQualificationState.ENABLED,
            provider=ProviderId.FLOWACCOUNT,
            environment=connection.environment,
            resource_uri_sha256=resource.uri_sha256,
            normalized_capability=_PROFILE_CAPABILITY,
            capability_version=manifest.manifest_version,
            provider_tool=_PROFILE_TOOL,
            operation_class=ProviderOperationClass.READ,
            request_schema_sha256=request_hash,
            response_schema_sha256=response_hash,
            qualification_hash=expected.qualification_hash,
        )

    def request_model(binding: VerifiedRuntimeBinding) -> type[BaseModel]:
        _require_profile_binding(binding)
        return FlowAccountProfileRequest

    def response_model(binding: VerifiedRuntimeBinding) -> type[BaseModel]:
        _require_profile_binding(binding)
        return FlowAccountProfile

    def unsupported_normalizer(
        _binding: VerifiedRuntimeBinding,
        _structured_content,
    ) -> BaseModel:
        raise ValueError("provider_response_invalid")

    header_factory = FlowAccountOAuthHeaderFactory(
        vault=vault,
        load_envelopes=connection_store.load_envelopes,
        save_envelopes=connection_store.replace_envelopes,
        refresh=oauth_client.refresh,
    )
    return {
        "header_factories": {
            AuthorizationMethod.OAUTH2_PKCE: header_factory,
        },
        "binding_verifier": verify_binding,
        "response_normalizer": unsupported_normalizer,
        "request_model_resolver": request_model,
        "response_model_resolver": response_model,
        "flowaccount_profile_binding_resolver": profile_binding,
    }


def _profile_qualification_hash(
    manifest: ProviderDriverManifest,
    *,
    environment: str,
) -> str:
    payload = json.dumps(
        {
            "manifest_version": manifest.manifest_version,
            "provider": ProviderId.FLOWACCOUNT.value,
            "environment": environment,
            "normalized_capability": _PROFILE_CAPABILITY,
            "provider_tool": _PROFILE_TOOL,
            "operation_class": ProviderOperationClass.READ.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _require_profile_binding(binding: VerifiedRuntimeBinding) -> None:
    if (
        binding.qualification_state is not ProviderQualificationState.ENABLED
        or binding.provider is not ProviderId.FLOWACCOUNT
        or binding.normalized_capability != _PROFILE_CAPABILITY
        or binding.provider_tool != _PROFILE_TOOL
        or binding.operation_class is not ProviderOperationClass.READ
    ):
        raise ValueError("provider_binding_invalid")


__all__ = [
    "ProviderOAuthProductionComposition",
    "build_provider_oauth_production_composition",
]
