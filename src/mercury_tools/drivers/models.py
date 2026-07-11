"""Public models used by repository-local ERP drivers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CredentialField:
    name: str
    secret: bool
    label: str


@dataclass(frozen=True)
class CredentialStatus:
    connector_id: str
    environment: str
    required_fields: tuple[str, ...]
    present_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    configured: bool

    def public_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "environment": self.environment,
            "required_fields": list(self.required_fields),
            "present_fields": list(self.present_fields),
            "missing_fields": list(self.missing_fields),
            "configured": self.configured,
        }


class FrozenDict(dict[str, Any]):
    """JSON-serializable mapping that cannot be mutated after construction."""

    def _immutable(self, *args: Any, **kwargs: Any) -> None:
        raise TypeError("immutable_mapping")

    __delitem__ = _immutable
    __ior__ = _immutable
    __setitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


def immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a public mapping so callers cannot alter driver state."""

    return FrozenDict(value)


@dataclass(frozen=True)
class AuthContext:
    headers: Mapping[str, str]
    query: Mapping[str, str]
    expires_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", immutable_mapping(self.headers))
        object.__setattr__(self, "query", immutable_mapping(self.query))


@dataclass(frozen=True)
class PreparedFile:
    field_name: str
    path: Path
    filename: str
    content_type: str


@dataclass(frozen=True)
class ConnectionProbe:
    status: str
    connector_id: str
    environment: str
    company_name: str | None
    details: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", immutable_mapping(self.details))


@dataclass(frozen=True)
class ConnectorResult:
    status: str
    http_status: int
    data: Any
    summary: str
    dispatched: bool
