"""Registry for hosted downstream provider MCP drivers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from mercury_tools.config import Settings
from mercury_tools.providers.base import ProviderDriver
from mercury_tools.providers.flowaccount import (
    FlowAccountMCPDriver,
    FlowAccountProfileBindingResolver,
    normalize_flowaccount_response,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import AuthorizationMethod, ProviderId
from mercury_tools.providers.peak import (
    PeakMCPDriver,
    QualifiedPeakProviderContract,
)
from mercury_tools.providers.streamable_mcp import (
    BindingVerifier,
    HeaderFactory,
    RequestModelResolver,
    ResponseModelResolver,
    ResponseNormalizer,
    StreamableMCPDriver,
)


class ProviderRegistryError(LookupError):
    pass


class ProviderDriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[ProviderId, ProviderDriver] = {}

    def register(self, driver: ProviderDriver) -> None:
        provider = ProviderId(driver.provider)
        if provider in self._drivers:
            raise ProviderRegistryError("duplicate_provider_driver")
        self._drivers[provider] = driver

    def get(self, provider: ProviderId | str) -> ProviderDriver:
        failure: ProviderRegistryError | None = None
        try:
            checked = ProviderId(provider)
            return self._drivers[checked]
        except (KeyError, ValueError):
            failure = ProviderRegistryError("provider_driver_not_found")
        raise failure

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(provider.value for provider in self._drivers))


def build_provider_registry(
    *,
    settings: Settings,
    manifest_root: str | Path,
    header_factories: Mapping[AuthorizationMethod, HeaderFactory] | None = None,
    binding_verifier: BindingVerifier | None = None,
    response_normalizer: ResponseNormalizer | None = None,
    request_model_resolver: RequestModelResolver | None = None,
    response_model_resolver: ResponseModelResolver | None = None,
    flowaccount_profile_binding_resolver: (FlowAccountProfileBindingResolver | None) = None,
    peak_contract: QualifiedPeakProviderContract | None = None,
) -> ProviderDriverRegistry:
    """Load the two server-controlled manifests without accepting resource URLs."""

    if not isinstance(header_factories, Mapping):
        raise ProviderRegistryError("flowaccount_runtime_dependencies_invalid")
    flowaccount_header_factory = header_factories.get(AuthorizationMethod.OAUTH2_PKCE)
    required_callables = (
        flowaccount_header_factory,
        binding_verifier,
        response_normalizer,
        request_model_resolver,
        response_model_resolver,
        flowaccount_profile_binding_resolver,
    )
    if not all(callable(dependency) for dependency in required_callables):
        raise ProviderRegistryError("flowaccount_runtime_dependencies_invalid")
    peak_header_factory = header_factories.get(AuthorizationMethod.PROVIDER_CREDENTIALS)
    if peak_contract is not None and (
        not isinstance(peak_contract, QualifiedPeakProviderContract)
        or not callable(peak_header_factory)
    ):
        raise ProviderRegistryError("peak_runtime_dependencies_invalid")

    def provider_scoped_normalizer(binding, structured_content):
        if binding.provider is ProviderId.PEAK:
            if peak_contract is None:
                raise ValueError("peak_provider_contract_unqualified")
            return peak_contract.normalize_profile(binding, structured_content)
        if (
            binding.provider is ProviderId.FLOWACCOUNT
            and binding.normalized_capability == "provider_profile.get"
        ):
            return normalize_flowaccount_response(binding, structured_content)
        if response_normalizer is None:
            raise ValueError("provider_response_normalizer_missing")
        return response_normalizer(binding, structured_content)

    def provider_scoped_verifier(connection, binding, resource_uri_sha256):
        if connection.provider is ProviderId.PEAK:
            if peak_contract is None:
                raise ValueError("peak_provider_contract_unqualified")
            return peak_contract.verify_binding(
                connection,
                binding,
                resource_uri_sha256,
            )
        if binding_verifier is None:
            raise ValueError("provider_binding_verifier_missing")
        return binding_verifier(connection, binding, resource_uri_sha256)

    def provider_scoped_request_model(binding):
        if binding.provider is ProviderId.PEAK:
            if peak_contract is None:
                raise ValueError("peak_provider_contract_unqualified")
            return peak_contract.request_model(binding)
        if request_model_resolver is None:
            raise ValueError("provider_request_model_resolver_missing")
        return request_model_resolver(binding)

    def provider_scoped_response_model(binding):
        if binding.provider is ProviderId.PEAK:
            if peak_contract is None:
                raise ValueError("peak_provider_contract_unqualified")
            return peak_contract.response_model(binding)
        if response_model_resolver is None:
            raise ValueError("provider_response_model_resolver_missing")
        return response_model_resolver(binding)

    root = Path(manifest_root)
    factories = dict(header_factories)
    registry = ProviderDriverRegistry()
    for provider in (ProviderId.FLOWACCOUNT, ProviderId.PEAK):
        manifest = load_provider_manifest(root / provider.value / "driver.json")
        runtime = StreamableMCPDriver(
            settings=settings,
            manifest=manifest,
            header_factory=factories.get(manifest.auth_adapter),
            binding_verifier=provider_scoped_verifier,
            response_normalizer=provider_scoped_normalizer,
            request_model_resolver=provider_scoped_request_model,
            response_model_resolver=provider_scoped_response_model,
        )
        if provider is ProviderId.FLOWACCOUNT:
            registry.register(
                FlowAccountMCPDriver(
                    runtime=runtime,
                    manifest=manifest,
                    profile_binding_resolver=flowaccount_profile_binding_resolver,
                )
            )
        else:
            registry.register(
                PeakMCPDriver(
                    runtime=runtime,
                    manifest=manifest,
                    contract=peak_contract,
                )
            )
    return registry


__all__ = [
    "ProviderDriverRegistry",
    "ProviderRegistryError",
    "build_provider_registry",
]
