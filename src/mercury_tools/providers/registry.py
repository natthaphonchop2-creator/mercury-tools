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
) -> ProviderDriverRegistry:
    """Load the two server-controlled manifests without accepting resource URLs."""

    if flowaccount_profile_binding_resolver is None:
        raise ProviderRegistryError("flowaccount_profile_binding_resolver_missing")

    def provider_scoped_normalizer(binding, structured_content):
        if (
            binding.provider is ProviderId.FLOWACCOUNT
            and binding.normalized_capability == "provider_profile.get"
        ):
            return normalize_flowaccount_response(binding, structured_content)
        if response_normalizer is None:
            raise ValueError("provider_response_normalizer_missing")
        return response_normalizer(binding, structured_content)

    root = Path(manifest_root)
    factories = dict(header_factories or {})
    registry = ProviderDriverRegistry()
    for provider in (ProviderId.FLOWACCOUNT, ProviderId.PEAK):
        manifest = load_provider_manifest(root / provider.value / "driver.json")
        runtime = StreamableMCPDriver(
            settings=settings,
            manifest=manifest,
            header_factory=factories.get(manifest.auth_adapter),
            binding_verifier=binding_verifier,
            response_normalizer=provider_scoped_normalizer,
            request_model_resolver=request_model_resolver,
            response_model_resolver=response_model_resolver,
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
            registry.register(runtime)
    return registry


__all__ = [
    "ProviderDriverRegistry",
    "ProviderRegistryError",
    "build_provider_registry",
]
