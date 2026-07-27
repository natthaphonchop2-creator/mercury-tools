"""Internal, tenant-bound credential envelope contracts."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_AAD_VERSION = "mercury-provider-credential-aad-v1"
_SECRET_ENVELOPE_FIELDS = {"nonce", "ciphertext", "aad_hash"}


def _reject_unsafe_text(value: str) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError("credential_binding_invalid")
    return value


def _require_aware_timestamp(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("credential_envelope_timestamp_invalid")
    return value


def validate_credential_key_version(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 64
        or _IDENTIFIER.fullmatch(value) is None
    ):
        raise ValueError("credential_key_version_invalid")
    return value


class _CredentialModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        ser_json_bytes="base64",
    )


class CredentialBinding(_CredentialModel):
    """The complete identity included in credential additional authenticated data."""

    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    connection_id: UUID
    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    company_or_merchant_id: str = Field(
        min_length=1,
        max_length=512,
        repr=False,
        exclude=True,
    )
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    credential_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=_IDENTIFIER.pattern,
    )

    @field_validator("company_or_merchant_id")
    @classmethod
    def validate_company_or_merchant_id(cls, value: str) -> str:
        return _reject_unsafe_text(value)

    @model_validator(mode="after")
    def reject_nil_uuids(self) -> CredentialBinding:
        if any(
            value.int == 0
            for value in (
                self.tenant_id,
                self.workspace_id,
                self.auth_user_id,
                self.connection_id,
            )
        ):
            raise ValueError("credential_binding_invalid")
        return self


def credential_aad(binding: CredentialBinding, *, key_version: str) -> bytes:
    """Return deterministic AAD without credential values."""

    checked_key_version = validate_credential_key_version(key_version)
    checked = CredentialBinding.model_validate(binding)
    payload = {
        "aad_version": _AAD_VERSION,
        "auth_user_id": str(checked.auth_user_id),
        "company_or_merchant_id": checked.company_or_merchant_id,
        "connection_id": str(checked.connection_id),
        "credential_type": checked.credential_type,
        "environment": checked.environment,
        "key_version": checked_key_version,
        "provider": checked.provider,
        "tenant_id": str(checked.tenant_id),
        "workspace_id": str(checked.workspace_id),
    }
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def credential_aad_hash(binding: CredentialBinding, *, key_version: str) -> bytes:
    return hashlib.sha256(credential_aad(binding, key_version=key_version)).digest()


class CredentialEnvelope(_CredentialModel):
    """Encrypted material plus the non-secret binding required to locate it."""

    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    connection_id: UUID
    provider: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    credential_type: str = Field(
        min_length=1,
        max_length=64,
        pattern=_IDENTIFIER.pattern,
    )
    key_version: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    nonce: bytes = Field(min_length=12, max_length=12, repr=False, exclude=True)
    ciphertext: bytes = Field(min_length=16, repr=False, exclude=True)
    aad_hash: bytes = Field(min_length=32, max_length=32, repr=False, exclude=True)
    created_at: datetime
    rotated_at: datetime | None = None
    revoked_at: datetime | None = None

    @field_validator("created_at", "rotated_at", "revoked_at")
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return _require_aware_timestamp(value)

    @field_validator("key_version")
    @classmethod
    def validate_key_version(cls, value: str) -> str:
        return validate_credential_key_version(value)

    @model_validator(mode="after")
    def validate_lifecycle(self) -> CredentialEnvelope:
        if any(
            value.int == 0
            for value in (
                self.id,
                self.tenant_id,
                self.workspace_id,
                self.auth_user_id,
                self.connection_id,
            )
        ):
            raise ValueError("credential_envelope_invalid")
        if self.rotated_at is not None and self.rotated_at < self.created_at:
            raise ValueError("credential_envelope_timestamp_invalid")
        if self.revoked_at is not None and self.revoked_at < self.created_at:
            raise ValueError("credential_envelope_timestamp_invalid")
        return self

    def storage_record(self) -> dict[str, Any]:
        """Serialize the encrypted envelope fields accepted by the save RPC."""

        return {
            "id": str(self.id),
            "credential_type": self.credential_type,
            "key_version": self.key_version,
            "nonce": self.nonce.hex(),
            "ciphertext": self.ciphertext.hex(),
            "aad_hash": self.aad_hash.hex(),
            "created_at": self.created_at.isoformat(),
            "rotated_at": (self.rotated_at.isoformat() if self.rotated_at is not None else None),
            "revoked_at": (self.revoked_at.isoformat() if self.revoked_at is not None else None),
        }

    def audit_reference(self) -> dict[str, Any]:
        """Return an opaque reference with no encrypted or provider-account material."""

        return {
            "credential_envelope_id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "workspace_id": str(self.workspace_id),
            "connection_id": str(self.connection_id),
            "provider": self.provider,
            "environment": self.environment,
            "credential_type": self.credential_type,
            "key_version": self.key_version,
        }

    @classmethod
    def secret_field_names(cls) -> frozenset[str]:
        return frozenset(_SECRET_ENVELOPE_FIELDS)


__all__ = [
    "CredentialBinding",
    "CredentialEnvelope",
    "credential_aad",
    "credential_aad_hash",
    "validate_credential_key_version",
]
