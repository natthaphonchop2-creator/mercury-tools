"""Deterministic local registry for connector drivers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mercury_tools.drivers.base import ConnectorDriver
from mercury_tools.drivers.generic import (
    GenericDriverFactory,
    generic_driver_factories,
)
from mercury_tools.drivers.models import immutable_mapping


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
        summaries = [
            (connector_id, driver.driver_id, driver.credential_schema)
            for connector_id, driver in self._drivers.items()
        ]
        summaries.extend(
            (factory.driver_id, factory.driver_id, factory.credential_schema)
            for factory in self._factories.values()
        )
        return tuple(
            immutable_mapping(
                {
                    "connector_id": connector_id,
                    "driver_id": driver_id,
                    "credential_fields": tuple(field.name for field in credential_schema),
                }
            )
            for connector_id, driver_id, credential_schema in sorted(summaries)
        )


def build_generic_registry() -> DriverRegistry:
    registry = DriverRegistry()
    for factory in generic_driver_factories():
        registry.register_factory(factory)
    return registry


__all__ = [
    "DriverRegistry",
    "DuplicateDriverError",
    "UnknownDriverError",
    "build_generic_registry",
]
