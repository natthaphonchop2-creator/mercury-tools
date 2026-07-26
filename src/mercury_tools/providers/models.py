"""Tenant-bound provider connection state without credential material."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_PERMISSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/-]{0,199}$")
_TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")


def _require_aware(value: datetime | None, code: str) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(code)
    return value


def _reject_unsafe_display(value: str) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError("provider_connection_invalid")
    return value


class _ProviderModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ProviderId(StrEnum):
    FLOWACCOUNT = "flowaccount"
    PEAK = "peak"


class AuthorizationMethod(StrEnum):
    OAUTH2_PKCE = "oauth2_pkce"
    PROVIDER_CREDENTIALS = "provider_credentials"


class ConnectionReadiness(StrEnum):
    REQUIRES_VALIDATION = "requires_validation"
    READY = "ready"
    VALIDATION_FAILED = "validation_failed"
    REQUIRES_REAUTHORIZATION = "requires_reauthorization"
    DISCONNECTED = "disconnected"


class SetupAttempt(_ProviderModel):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    token_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_TOKEN_HASH.pattern,
        repr=False,
        exclude=True,
    )
    expires_at: datetime
    consumed_at: datetime | None = None
    created_at: datetime

    @field_validator("expires_at", "consumed_at", "created_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, "provider_setup_attempt_invalid")

    @model_validator(mode="after")
    def validate_lifecycle(self) -> SetupAttempt:
        if any(
            value.int == 0
            for value in (
                self.id,
                self.tenant_id,
                self.workspace_id,
                self.auth_user_id,
            )
        ):
            raise ValueError("provider_setup_attempt_invalid")
        if self.expires_at <= self.created_at:
            raise ValueError("provider_setup_attempt_invalid")
        if self.consumed_at is not None and self.consumed_at < self.created_at:
            raise ValueError("provider_setup_attempt_invalid")
        return self


class ProviderConnection(_ProviderModel):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    provider_account_id: str = Field(
        min_length=1,
        max_length=512,
        repr=False,
        exclude=True,
    )
    account_display_name: str = Field(min_length=1, max_length=200)
    authorization_method: AuthorizationMethod
    granted_permissions: tuple[str, ...] = ()
    readiness: ConnectionReadiness
    revision: int = Field(ge=1)
    last_validated_at: datetime | None = None
    credential_envelope_ids: tuple[UUID, ...] = Field(
        max_length=16,
        repr=False,
        exclude=True,
    )
    provider_revocation_required: bool = False
    disconnected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("provider_account_id", "account_display_name")
    @classmethod
    def validate_display_text(cls, value: str) -> str:
        return _reject_unsafe_display(value)

    @field_validator("granted_permissions")
    @classmethod
    def validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            len(value) != len(set(value))
            or tuple(sorted(value)) != value
            or any(_PERMISSION.fullmatch(item) is None for item in value)
        ):
            raise ValueError("provider_connection_invalid")
        return value

    @field_validator(
        "last_validated_at",
        "disconnected_at",
        "created_at",
        "updated_at",
    )
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_aware(value, "provider_connection_invalid")

    @model_validator(mode="after")
    def validate_state(self) -> ProviderConnection:
        if any(
            value.int == 0
            for value in (
                self.id,
                self.tenant_id,
                self.workspace_id,
                self.auth_user_id,
            )
        ):
            raise ValueError("provider_connection_invalid")
        if len(self.credential_envelope_ids) != len(set(self.credential_envelope_ids)):
            raise ValueError("provider_connection_invalid")
        if self.updated_at < self.created_at:
            raise ValueError("provider_connection_invalid")
        if self.readiness is ConnectionReadiness.READY and self.last_validated_at is None:
            raise ValueError("provider_connection_invalid")
        if self.readiness is ConnectionReadiness.DISCONNECTED:
            if self.disconnected_at is None or self.credential_envelope_ids:
                raise ValueError("provider_connection_invalid")
        elif self.disconnected_at is not None or not self.credential_envelope_ids:
            raise ValueError("provider_connection_invalid")
        return self

    def summary(self) -> ProviderConnectionSummary:
        return ProviderConnectionSummary(
            connection_id=self.id,
            provider=self.provider,
            environment=self.environment,
            account_display_name=self.account_display_name,
            authorization_method=self.authorization_method,
            granted_permissions=self.granted_permissions,
            readiness=self.readiness,
            revision=self.revision,
            last_validated_at=self.last_validated_at,
            provider_revocation_required=self.provider_revocation_required,
        )

    def audit_reference(self) -> dict[str, Any]:
        return {
            "connection_id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "workspace_id": str(self.workspace_id),
            "provider": self.provider.value,
            "environment": self.environment,
            "readiness": self.readiness.value,
            "revision": self.revision,
        }


class ProviderConnectionSummary(_ProviderModel):
    connection_id: UUID
    provider: ProviderId
    environment: str
    account_display_name: str
    authorization_method: AuthorizationMethod
    granted_permissions: tuple[str, ...]
    readiness: ConnectionReadiness
    revision: int
    last_validated_at: datetime | None
    provider_revocation_required: bool


class DisconnectResult(_ProviderModel):
    connection_id: UUID
    status: Literal["disconnected"] = "disconnected"
    deleted_envelope_count: int = Field(ge=0, le=16)
    already_disconnected: bool
    provider_revocation_required: bool
    revision: int = Field(ge=1)


__all__ = [
    "AuthorizationMethod",
    "ConnectionReadiness",
    "DisconnectResult",
    "ProviderConnection",
    "ProviderConnectionSummary",
    "ProviderId",
    "SetupAttempt",
]
