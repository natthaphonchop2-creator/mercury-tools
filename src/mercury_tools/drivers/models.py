"""Public models used by repository-local ERP drivers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from pathlib import Path
from types import MappingProxyType
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


def immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Copy a public mapping so callers cannot alter driver state."""

    frozen = deep_freeze(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("public_data_invalid")
    return frozen


def deep_freeze(value: Any) -> Any:
    """Recursively freeze JSON-compatible public response data."""

    return _deep_freeze(value, active=set())


def _deep_freeze(value: Any, *, active: set[int]) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if isfinite(value):
            return value
        raise TypeError("public_data_invalid")
    if isinstance(value, Mapping):
        return MappingProxyType(_freeze_mapping(value, active=active))
    if isinstance(value, list | tuple):
        return tuple(_freeze_sequence(value, active=active))
    raise TypeError("public_data_invalid")


def _freeze_mapping(value: Mapping[Any, Any], *, active: set[int]) -> dict[str, Any]:
    identity = id(value)
    if identity in active:
        raise TypeError("public_data_invalid")
    active.add(identity)
    try:
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("public_data_invalid")
            frozen[key] = _deep_freeze(item, active=active)
        return frozen
    finally:
        active.remove(identity)


def _freeze_sequence(value: list[Any] | tuple[Any, ...], *, active: set[int]) -> tuple[Any, ...]:
    identity = id(value)
    if identity in active:
        raise TypeError("public_data_invalid")
    active.add(identity)
    try:
        return tuple(_deep_freeze(item, active=active) for item in value)
    finally:
        active.remove(identity)


def to_jsonable(value: Any) -> Any:
    """Return a plain JSON-compatible copy at an explicit serialization boundary."""

    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if isfinite(value):
            return value
        raise TypeError("public_data_invalid")
    if isinstance(value, Mapping):
        return {key: to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    raise TypeError("public_data_invalid")


@dataclass(frozen=True, repr=False)
class AuthContext:
    headers: Mapping[str, str]
    query: Mapping[str, str]
    expires_at: datetime | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", immutable_mapping(self.headers))
        object.__setattr__(self, "query", immutable_mapping(self.query))

    def __repr__(self) -> str:
        return (
            "AuthContext("
            f"header_names={tuple(self.headers)}, "
            f"query_names={tuple(self.query)}, "
            f"expires_at={self.expires_at!r}"
            ")"
        )


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

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "connector_id": self.connector_id,
            "environment": self.environment,
            "company_name": self.company_name,
            "details": to_jsonable(self.details),
        }


@dataclass(frozen=True)
class ConnectorResult:
    status: str
    http_status: int
    data: Any
    summary: str
    dispatched: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "data", deep_freeze(self.data))

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "http_status": self.http_status,
            "data": to_jsonable(self.data),
            "summary": self.summary,
            "dispatched": self.dispatched,
        }
