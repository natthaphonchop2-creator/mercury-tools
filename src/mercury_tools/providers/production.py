"""Fail-closed production composition for hosted FlowAccount OAuth."""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

import httpx
from pydantic import BaseModel

from mercury_tools.auth.middleware import current_mercury_access_token
from mercury_tools.auth.models import PrincipalResolver
from mercury_tools.auth.supabase_jwt import SupabaseJwtValidator, validator_from_settings
from mercury_tools.config import Settings, V1ConfigurationError, v1_supabase_rest_url
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
from mercury_tools.providers.registry import ProviderDriverRegistry, build_provider_registry
from mercury_tools.providers.store import SupabaseProviderConnectionStore
from mercury_tools.providers.streamable_mcp import wire_schema_sha256
from mercury_tools.workspaces.service import WorkspaceService

_PROFILE_CAPABILITY = "provider_profile.get"
_PROFILE_TOOL = "get_provider_profile"


@dataclass(frozen=True, repr=False)
class ProviderOAuthProductionComposition:
    settings: Settings = field(repr=False)
    principal_resolver: PrincipalResolver = field(repr=False)
    provider_oauth_service: ProviderOAuthService
    state_store: SupabaseProviderOAuthStateStore
    connection_store: SupabaseProviderConnectionStore
    registry: ProviderDriverRegistry
    network_guard: PublicOAuthNetworkGuard
    state_http_client: httpx.AsyncClient = field(repr=False)
    connection_http_client: httpx.Client = field(repr=False)
    owns_state_http_client: bool = field(default=True, repr=False)
    owns_connection_http_client: bool = field(default=True, repr=False)
    test_only_dependencies: bool = field(default=False, repr=False)

    def validate_for_runtime(self, settings: Settings) -> None:
        """Reject incomplete or cross-wired production dependency bundles."""

        self._validate(settings, allow_test_dependencies=False)

    def validate_for_test(self, settings: Settings) -> None:
        """Validate a bundle built by the explicitly test-only factory."""

        self._validate(settings, allow_test_dependencies=True)

    def _validate(
        self,
        settings: Settings,
        *,
        allow_test_dependencies: bool,
    ) -> None:
        try:
            settings.validate_v1()
            if (
                not settings.v1_enabled
                or self.settings != settings
                or (self.test_only_dependencies and not allow_test_dependencies)
                or (
                    not self.test_only_dependencies
                    and (
                        not self.owns_state_http_client
                        or not self.owns_connection_http_client
                        or not self.network_guard._owns_http
                    )
                )
            ):
                raise ValueError
            if (
                (
                    not allow_test_dependencies
                    and (
                        type(self.principal_resolver) is not SupabaseJwtValidator
                        or self.principal_resolver.issuer
                        != settings.supabase_auth_issuer.rstrip("/")
                        or self.principal_resolver.audience != settings.supabase_jwt_audience
                        or self.principal_resolver.jwks_url != settings.supabase_jwks_url
                    )
                )
                or not callable(getattr(self.principal_resolver, "resolve", None))
                or not isinstance(self.provider_oauth_service, ProviderOAuthService)
                or not isinstance(self.state_store, SupabaseProviderOAuthStateStore)
                or not isinstance(
                    self.connection_store,
                    SupabaseProviderConnectionStore,
                )
                or not isinstance(self.registry, ProviderDriverRegistry)
                or not isinstance(self.network_guard, PublicOAuthNetworkGuard)
                or not isinstance(self.state_http_client, httpx.AsyncClient)
                or not isinstance(self.connection_http_client, httpx.Client)
                or not isinstance(self.owns_state_http_client, bool)
                or not isinstance(self.owns_connection_http_client, bool)
            ):
                raise ValueError

            service = self.provider_oauth_service
            flowaccount = self.registry.get(ProviderId.FLOWACCOUNT)
            expected_rest_url = v1_supabase_rest_url(
                project_url=settings.supabase_url,
                auth_issuer=settings.supabase_auth_issuer,
            )
            expected_callback_uri = (
                f"{settings.provider_callback_base_url.rstrip('/')}{FLOWACCOUNT_CALLBACK_PATH}"
            )
            expected_origins = {
                (ProviderId.FLOWACCOUNT, "sandbox"): frozenset(
                    {
                        settings.flowaccount_oauth_sandbox_authorization_server_origin,
                    }
                ),
                (ProviderId.FLOWACCOUNT, "production"): frozenset(
                    {
                        settings.flowaccount_oauth_production_authorization_server_origin,
                    }
                ),
            }
            expected_vault_versions = {
                settings.vault_active_key_version,
                *(
                    (settings.vault_previous_key_version,)
                    if settings.vault_previous_key_version
                    else ()
                ),
            }
            if (
                self.registry.providers() != ("flowaccount", "peak")
                or service._settings != settings
                or service._principal_resolver is not self.principal_resolver
                or service._state_store is not self.state_store
                or service._connection_store is not self.connection_store
                or service._driver is not flowaccount
                or service._mercury_access_token is not current_mercury_access_token
                or (
                    not allow_test_dependencies
                    and not isinstance(service._workspace_service, WorkspaceService)
                )
                or service._manifest != flowaccount._manifest
                or service._oauth_client._network_guard is not self.network_guard
                or self.connection_store._vault is not service._vault
                or self.state_store._http is not self.state_http_client
                or self.connection_store._http is not self.connection_http_client
                or self.state_store._base_url != expected_rest_url
                or self.connection_store._base_url != expected_rest_url
                or self.state_store._callback_uri != expected_callback_uri
                or not secrets.compare_digest(
                    self.state_store._publishable_key,
                    settings.supabase_publishable_key,
                )
                or not secrets.compare_digest(
                    self.state_store._service_role_key,
                    settings.supabase_service_role_key,
                )
                or not secrets.compare_digest(
                    self.connection_store._service_role_key,
                    settings.supabase_service_role_key,
                )
                or service._oauth_client._authorization_server_origins != expected_origins
                or service._vault._active_key_version != settings.vault_active_key_version
                or set(service._vault._ciphers) != expected_vault_versions
                or not callable(getattr(service, "complete_callback", None))
            ):
                raise ValueError
        except V1ConfigurationError:
            raise
        except Exception:
            raise V1ConfigurationError("v1_provider_oauth_composition_invalid") from None

    async def startup(self) -> None:
        self._validate(
            self.settings,
            allow_test_dependencies=self.test_only_dependencies,
        )
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
) -> ProviderOAuthProductionComposition:
    """Build the production-owned hosted FlowAccount OAuth dependency bundle."""

    return _build_provider_oauth_composition(
        settings=settings,
        principal_resolver=validator_from_settings(settings),
        test_only_dependencies=False,
    )


def build_test_provider_oauth_production_composition(
    *,
    settings: Settings,
    principal_resolver: PrincipalResolver,
    state_http_client: httpx.AsyncClient | None = None,
    connection_http_client: httpx.Client | None = None,
    network_guard: PublicOAuthNetworkGuard | None = None,
    workspace_service: WorkspaceService | None = None,
) -> ProviderOAuthProductionComposition:
    """Build a typed composition with explicitly test-only dependency overrides."""

    return _build_provider_oauth_composition(
        settings=settings,
        principal_resolver=principal_resolver,
        state_http_client=state_http_client,
        connection_http_client=connection_http_client,
        network_guard=network_guard,
        workspace_service=workspace_service,
        test_only_dependencies=True,
    )


def _build_provider_oauth_composition(
    *,
    settings: Settings,
    principal_resolver: PrincipalResolver,
    state_http_client: httpx.AsyncClient | None = None,
    connection_http_client: httpx.Client | None = None,
    network_guard: PublicOAuthNetworkGuard | None = None,
    workspace_service: WorkspaceService | None = None,
    test_only_dependencies: bool,
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
        composition = ProviderOAuthProductionComposition(
            settings=settings,
            principal_resolver=principal_resolver,
            provider_oauth_service=service,
            state_store=state_store,
            connection_store=connection_store,
            registry=registry,
            network_guard=selected_network_guard,
            state_http_client=selected_state_http,
            connection_http_client=selected_connection_http,
            owns_state_http_client=state_http_client is None,
            owns_connection_http_client=connection_http_client is None,
            test_only_dependencies=test_only_dependencies,
        )
        if test_only_dependencies:
            composition.validate_for_test(settings)
        else:
            composition.validate_for_runtime(settings)
        return composition
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
        load_envelopes=connection_store.load_runtime_envelopes,
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
    "build_test_provider_oauth_production_composition",
]
