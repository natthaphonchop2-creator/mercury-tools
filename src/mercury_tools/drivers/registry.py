"""Deterministic local registry for connector drivers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mercury_tools.drivers.base import ConnectorDriver
from mercury_tools.drivers.generic import (
    GenericApiKeyDriver,
    GenericBasicDriver,
    GenericBearerDriver,
    GenericOAuthClientCredentialsDriver,
)
from mercury_tools.drivers.models import immutable_mapping


class DuplicateDriverError(ValueError):
    pass


class UnknownDriverError(LookupError):
    pass


class DriverRegistry:
    def __init__(self) -> None:
        self._drivers: dict[str, ConnectorDriver] = {}

    def register(self, driver: ConnectorDriver) -> None:
        if driver.connector_id in self._drivers:
            raise DuplicateDriverError("duplicate_connector_driver")
        self._drivers[driver.connector_id] = driver

    def get(self, connector_id: str) -> ConnectorDriver:
        try:
            return self._drivers[connector_id]
        except KeyError:
            raise UnknownDriverError("connector_driver_not_found") from None

    def summaries(self) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            immutable_mapping(
                {
                    "connector_id": connector_id,
                    "driver_id": driver.driver_id,
                    "credential_fields": tuple(
                        field.name for field in driver.credential_fields("summary")
                    ),
                }
            )
            for connector_id, driver in sorted(self._drivers.items())
        )


def build_generic_registry() -> DriverRegistry:
    registry = DriverRegistry()
    registry.register(GenericBearerDriver(connector_id="bearer", environments={}))
    registry.register(
        GenericApiKeyDriver(
            connector_id="api_key_header",
            placement="header",
            key_name="X-API-Key",
            environments={},
        )
    )
    registry.register(
        GenericApiKeyDriver(
            connector_id="api_key_query",
            placement="query",
            key_name="api_key",
            environments={},
        )
    )
    registry.register(GenericBasicDriver(connector_id="basic", environments={}))
    registry.register(
        GenericOAuthClientCredentialsDriver(
            connector_id="oauth_client_credentials",
            environments={},
            token_urls={},
        )
    )
    return registry


__all__ = [
    "DriverRegistry",
    "DuplicateDriverError",
    "UnknownDriverError",
    "build_generic_registry",
]
