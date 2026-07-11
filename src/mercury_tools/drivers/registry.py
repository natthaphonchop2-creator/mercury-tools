"""Deterministic local registry for connector drivers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from mercury_tools.drivers.base import ConnectorDriver, DriverConfigurationError
from mercury_tools.drivers.generic import (
    GenericDriverFactory,
    generic_driver_factories,
)
from mercury_tools.drivers.models import CredentialField, immutable_mapping, to_jsonable

if TYPE_CHECKING:
    from mercury_tools.local.repository import RepositoryConfig


_AUTH_SETTINGS_BY_DRIVER = {
    "bearer": frozenset(),
    "basic": frozenset(),
    "api_key_header": frozenset({"key_name"}),
    "api_key_query": frozenset({"key_name"}),
    "oauth_client_credentials": frozenset(
        {"client_id_name", "client_secret_name", "grant_type", "scope", "token_url"}
    ),
}


class DuplicateDriverError(ValueError):
    pass


class UnknownDriverError(LookupError):
    pass


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, ConnectorDriver] = {}
        self._factories: dict[str, GenericDriverFactory] = {}

    def register(self, driver: ConnectorDriver) -> None:
        if driver.connector_id in self._drivers:
            raise DuplicateDriverError("duplicate_connector_driver")
        self._drivers[driver.connector_id] = driver

    def get(self, connector_id: str) -> ConnectorDriver:
        try:
            return self._drivers[connector_id]
        except KeyError:
            raise UnknownDriverError("connector_driver_not_found") from None

    def register_factory(self, factory: GenericDriverFactory) -> None:
        if factory.driver_id in self._factories:
            raise DuplicateDriverError("duplicate_connector_driver_factory")
        self._factories[factory.driver_id] = factory

    def get_factory(self, driver_id: str) -> GenericDriverFactory:
        try:
            return self._factories[driver_id]
        except KeyError:
            raise UnknownDriverError("connector_driver_factory_not_found") from None

    def create(
        self,
        driver_id: str,
        *,
        connector_id: str,
        environments: Mapping[str, str],
        key_name: str | None = None,
        token_urls: Mapping[str, str] | None = None,
    ) -> ConnectorDriver:
        return self.get_factory(driver_id).create(
            connector_id=connector_id,
            environments=environments,
            key_name=key_name,
            token_urls=token_urls,
        )

    def summaries(self) -> tuple[Mapping[str, Any], ...]:
        summaries: list[tuple[tuple[str, str], dict[str, Any]]] = []
        for connector_id, driver in self._drivers.items():
            summaries.append(
                (
                    ("connector", connector_id),
                    {
                        "entry_type": "connector",
                        "connector_id": connector_id,
                        "driver_id": driver.driver_id,
                        "credential_fields": _credential_field_names(driver),
                    },
                )
            )
        for factory in self._factories.values():
            summaries.append(
                (
                    ("factory", factory.driver_id),
                    {
                        "entry_type": "factory",
                        "driver_id": factory.driver_id,
                        "credential_fields": _credential_field_names(factory),
                    },
                )
            )
        return tuple(
            immutable_mapping(summary)
            for _, summary in sorted(summaries, key=lambda entry: entry[0])
        )

    def public_summaries(self) -> list[dict[str, Any]]:
        """Return registry summaries as JSON-ready data at a public boundary."""

        summaries = to_jsonable(self.summaries())
        if not isinstance(summaries, list):
            raise TypeError("public_data_invalid")
        return summaries

    @classmethod
    def for_repository(cls, config: RepositoryConfig) -> DriverRegistry:
        """Build a complete registry from validated repository-local configuration."""

        if not _repository_config_shape_valid(config):
            raise DriverConfigurationError("repository_connector_invalid")

        # Provider imports intentionally remain local so build_generic_registry()
        # keeps the Task 7 generic-only import boundary.
        from mercury_tools.drivers.flowaccount import FlowAccountDriver
        from mercury_tools.drivers.peak import PeakDriver

        registry = cls()
        registry.register(FlowAccountDriver())
        registry.register(PeakDriver())
        for factory in generic_driver_factories():
            registry.register_factory(factory)

        for connector_id, environments in config.connectors.items():
            if connector_id in {"flowaccount", "peak"}:
                raise DriverConfigurationError("repository_connector_conflict")
            _register_repository_connector(registry, config, connector_id, environments)
        return registry


def _credential_field_names(source: object) -> tuple[str, ...]:
    schema = getattr(source, "credential_schema", ())
    if not isinstance(schema, tuple):
        return ()
    return tuple(field.name for field in schema if isinstance(field, CredentialField))


def build_generic_registry() -> DriverRegistry:
    registry = DriverRegistry()
    for factory in generic_driver_factories():
        registry.register_factory(factory)
    return registry


def _repository_config_shape_valid(config: object) -> bool:
    return hasattr(config, "connectors") and hasattr(config, "trusted_hosts")


def _register_repository_connector(
    registry: DriverRegistry,
    config: RepositoryConfig,
    connector_id: object,
    environments: object,
) -> None:
    if (
        not isinstance(connector_id, str)
        or not connector_id
        or not isinstance(environments, Mapping)
    ):
        raise DriverConfigurationError("repository_connector_invalid")
    if not environments:
        raise DriverConfigurationError("repository_connector_invalid")

    configured_driver_id: str | None = None
    configured_auth_settings: Mapping[str, Any] | None = None
    base_urls: dict[str, str] = {}
    token_urls: dict[str, str] = {}
    key_name: str | None = None
    for environment, record in environments.items():
        if not isinstance(environment, str) or not environment or not isinstance(record, Mapping):
            raise DriverConfigurationError("repository_connector_invalid")
        if set(record) != {"driver_id", "base_url", "auth_settings", "network_policy"}:
            raise DriverConfigurationError("repository_connector_invalid")
        driver_id = record["driver_id"]
        base_url = record["base_url"]
        auth_settings = record["auth_settings"]
        network_policy = record["network_policy"]
        if (
            not isinstance(driver_id, str)
            or not isinstance(base_url, str)
            or not isinstance(auth_settings, Mapping)
            or not isinstance(network_policy, Mapping)
            or set(network_policy) != {"allow_private_network"}
            or not isinstance(network_policy["allow_private_network"], bool)
        ):
            raise DriverConfigurationError("repository_connector_invalid")
        if network_policy["allow_private_network"] and environment not in {"local", "gateway"}:
            raise DriverConfigurationError("repository_connector_invalid")
        if not _auth_settings_valid(driver_id, auth_settings):
            raise DriverConfigurationError("repository_connector_invalid")
        if configured_driver_id is None:
            configured_driver_id = driver_id
            configured_auth_settings = auth_settings
            candidate_key_name = auth_settings.get("key_name")
            if candidate_key_name is not None and not isinstance(candidate_key_name, str):
                raise DriverConfigurationError("repository_connector_invalid")
            key_name = candidate_key_name
        elif configured_driver_id != driver_id or configured_auth_settings != auth_settings:
            raise DriverConfigurationError("repository_connector_mismatch")

        required_hosts = _record_hosts(base_url, auth_settings)
        trusted_hosts = _trusted_hosts_for(config, connector_id, environment)
        if not required_hosts.issubset(trusted_hosts):
            raise DriverConfigurationError("repository_trusted_hosts_mismatch")
        base_urls[environment] = base_url
        token_url = auth_settings.get("token_url")
        if token_url is not None:
            if not isinstance(token_url, str):
                raise DriverConfigurationError("repository_connector_invalid")
            token_urls[environment] = token_url

    if configured_driver_id is None:
        raise DriverConfigurationError("repository_connector_invalid")
    try:
        driver = registry.create(
            configured_driver_id,
            connector_id=connector_id,
            environments=base_urls,
            key_name=key_name,
            token_urls=token_urls or None,
        )
    except (DriverConfigurationError, UnknownDriverError):
        raise DriverConfigurationError("repository_connector_invalid") from None
    registry.register(driver)


def _trusted_hosts_for(config: RepositoryConfig, connector_id: str, environment: str) -> set[str]:
    trusted_hosts = config.trusted_hosts
    if not isinstance(trusted_hosts, Mapping):
        raise DriverConfigurationError("repository_connector_invalid")
    connector_hosts = trusted_hosts.get(connector_id)
    if not isinstance(connector_hosts, Mapping):
        raise DriverConfigurationError("repository_trusted_hosts_mismatch")
    values = connector_hosts.get(environment)
    if isinstance(values, str) or not isinstance(values, tuple):
        raise DriverConfigurationError("repository_trusted_hosts_mismatch")
    if not values or any(not isinstance(host, str) or not host for host in values):
        raise DriverConfigurationError("repository_trusted_hosts_mismatch")
    return set(values)


def _auth_settings_valid(driver_id: str, auth_settings: Mapping[str, Any]) -> bool:
    allowed = _AUTH_SETTINGS_BY_DRIVER.get(driver_id)
    if allowed is None or not set(auth_settings).issubset(allowed):
        return False
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in auth_settings.items()
    ):
        return False
    return driver_id != "oauth_client_credentials" or "token_url" in auth_settings


def _record_hosts(base_url: str, auth_settings: Mapping[str, Any]) -> set[str]:
    urls = [base_url]
    token_url = auth_settings.get("token_url")
    if token_url is not None:
        urls.append(token_url)
    hosts: set[str] = set()
    for value in urls:
        if not isinstance(value, str):
            raise DriverConfigurationError("repository_connector_invalid")
        try:
            host = urlsplit(value).hostname
        except ValueError:
            raise DriverConfigurationError("repository_connector_invalid") from None
        if not host:
            raise DriverConfigurationError("repository_connector_invalid")
        hosts.add(host.lower().rstrip("."))
    return hosts


__all__ = [
    "DriverRegistry",
    "DuplicateDriverError",
    "UnknownDriverError",
    "build_generic_registry",
]
