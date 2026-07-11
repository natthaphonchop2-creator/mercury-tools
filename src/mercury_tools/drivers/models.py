"""Public models used by repository-local ERP drivers."""

from __future__ import annotations

from dataclasses import dataclass
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
