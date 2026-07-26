"""Registry for hosted downstream provider MCP drivers."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from mercury_tools.config import Settings
from mercury_tools.providers.base import ProviderDriver
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import AuthorizationMethod, ProviderId
from mercury_tools.providers.streamable_mcp import (
    BindingVerifier,
    HeaderFactory,
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
    response_model_resolver: ResponseModelResolver | None = None,
) -> ProviderDriverRegistry:
    """Load the two server-controlled manifests without accepting resource URLs."""

    root = Path(manifest_root)
    factories = dict(header_factories or {})
    registry = ProviderDriverRegistry()
    for provider in (ProviderId.FLOWACCOUNT, ProviderId.PEAK):
        manifest = load_provider_manifest(root / provider.value / "driver.json")
        registry.register(
            StreamableMCPDriver(
                settings=settings,
                manifest=manifest,
                header_factory=factories.get(manifest.auth_adapter),
                binding_verifier=binding_verifier,
                response_normalizer=response_normalizer,
                response_model_resolver=response_model_resolver,
            )
        )
    return registry


__all__ = [
    "ProviderDriverRegistry",
    "ProviderRegistryError",
    "build_provider_registry",
]
