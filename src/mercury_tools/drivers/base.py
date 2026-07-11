"""Shared contract and error types for repository-local connector drivers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

import httpx

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.drivers.models import (
    AuthContext,
    ConnectionProbe,
    ConnectorResult,
    CredentialField,
    PreparedFile,
)


class DriverError(RuntimeError):
    """A public driver failure identified by a credential-safe code."""


class DriverConfigurationError(DriverError):
    """Driver configuration or request inputs are invalid."""


class ConnectorAuthError(DriverError):
    """A connector authentication operation failed."""


class ConnectorDriver(Protocol):
    driver_id: str
    connector_id: str

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]: ...

    def resolve_base_url(self, environment: str) -> str: ...

    def safe_probe_action(self, environment: str) -> str: ...

    def prepare_files(
        self,
        *,
        action: CatalogAction,
        inputs: Mapping[str, Any],
        roots: Sequence[Path],
    ) -> tuple[PreparedFile, ...]: ...

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext: ...

    async def validate_credentials(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> ConnectionProbe: ...

    def interpret_response(
        self,
        *,
        action: CatalogAction,
        response: httpx.Response,
        dispatched: bool,
    ) -> ConnectorResult: ...

    def sanitize_response(self, action: CatalogAction, value: Any) -> Any: ...


__all__ = [
    "AuthContext",
    "ConnectionProbe",
    "ConnectorAuthError",
    "ConnectorDriver",
    "ConnectorResult",
    "DriverConfigurationError",
    "DriverError",
    "PreparedFile",
]
