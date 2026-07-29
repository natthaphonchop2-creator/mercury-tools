"""AES-256-GCM credential sealing with explicit tenant bindings."""

from __future__ import annotations

import base64
import binascii
import os
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError

from mercury_tools.credentials.models import (
    CredentialBinding,
    CredentialEnvelope,
    credential_aad,
    credential_aad_hash,
    validate_credential_key_version,
)

if TYPE_CHECKING:
    from mercury_tools.config import Settings

_NONCE_BYTES: Final = 12
_VAULT_ERROR_CODES = frozenset(
    {
        "credential_binding_invalid",
        "credential_binding_mismatch",
        "credential_decryption_failed",
        "credential_envelope_invalid",
        "credential_key_version_unknown",
        "credential_plaintext_invalid",
        "credential_revoked",
    }
)


class CredentialVaultError(RuntimeError):
    """A stable credential failure that never includes sensitive input."""

    def __init__(self, code: str) -> None:
        if code not in _VAULT_ERROR_CODES:
            raise ValueError("credential_vault_error_invalid")
        self.code = code
        super().__init__(code)


def _clear_mutable(value: bytearray) -> None:
    value[:] = b"\x00" * len(value)


class CredentialVault:
    """Seal request-scoped plaintext and retain only encrypted envelopes."""

    def __init__(
        self,
        *,
        active_key_version: str,
        keys: Mapping[str, bytes],
        clock: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[int], bytes] | None = None,
    ) -> None:
        try:
            if not isinstance(keys, Mapping) or not keys:
                raise TypeError
            checked_active_key_version = validate_credential_key_version(active_key_version)
            if checked_active_key_version not in keys:
                raise TypeError
            ciphers: dict[str, AESGCM] = {}
            for version, key in keys.items():
                checked_version = validate_credential_key_version(version)
                if not isinstance(key, bytes) or len(key) != 32:
                    raise TypeError
                ciphers[checked_version] = AESGCM(key)
            if len(ciphers) != len(keys):
                raise TypeError
        except (TypeError, ValueError):
            raise ValueError("credential_vault_configuration_invalid") from None

        self._active_key_version = checked_active_key_version
        self._ciphers = ciphers
        self._clock = clock or (lambda: datetime.now(UTC))
        self._nonce_factory = nonce_factory or os.urandom

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        clock: Callable[[], datetime] | None = None,
        nonce_factory: Callable[[int], bytes] | None = None,
    ) -> CredentialVault:
        """Build the vault from the configured active and optional previous keys."""

        try:
            active_version = validate_credential_key_version(settings.vault_active_key_version)
            active_key = cls._decode_configured_key(settings.vault_active_key)
            previous_key_configured = bool(settings.vault_previous_key)
            previous_version_configured = bool(settings.vault_previous_key_version)
            if previous_key_configured != previous_version_configured:
                raise TypeError
            keys = {active_version: active_key}
            if previous_key_configured:
                previous_version = validate_credential_key_version(
                    settings.vault_previous_key_version
                )
                if previous_version == active_version:
                    raise TypeError
                keys[previous_version] = cls._decode_configured_key(settings.vault_previous_key)
        except (AttributeError, TypeError, ValueError, binascii.Error):
            raise ValueError("credential_vault_configuration_invalid") from None
        return cls(
            active_key_version=active_version,
            keys=keys,
            clock=clock,
            nonce_factory=nonce_factory,
        )

    @staticmethod
    def _decode_configured_key(value: str) -> bytes:
        if not isinstance(value, str) or not value:
            raise TypeError
        decoded = base64.b64decode(value, validate=True)
        if len(decoded) != 32:
            raise TypeError
        return decoded

    def __repr__(self) -> str:
        versions = tuple(sorted(self._ciphers))
        return (
            "CredentialVault("
            f"active_key_version={self._active_key_version}, "
            f"available_key_versions={versions}"
            ")"
        )

    def seal(
        self,
        binding: CredentialBinding,
        plaintext: bytes | bytearray,
    ) -> CredentialEnvelope:
        checked_binding = self._binding(binding)
        if not isinstance(plaintext, (bytes, bytearray)) or not plaintext:
            raise CredentialVaultError("credential_plaintext_invalid")
        mutable = bytearray(plaintext)
        try:
            return self._seal(
                checked_binding,
                mutable,
                envelope_id=None,
                created_at=None,
                rotated_at=None,
            )
        finally:
            _clear_mutable(mutable)

    def open(
        self,
        binding: CredentialBinding,
        envelope: CredentialEnvelope,
    ) -> bytearray:
        checked_binding = self._binding(binding)
        checked_envelope = self._envelope(envelope)
        if checked_envelope.revoked_at is not None:
            raise CredentialVaultError("credential_revoked")
        if not self._metadata_matches(checked_binding, checked_envelope):
            raise CredentialVaultError("credential_binding_mismatch")

        cipher = self._ciphers.get(checked_envelope.key_version)
        if cipher is None:
            raise CredentialVaultError("credential_key_version_unknown")
        aad = credential_aad(
            checked_binding,
            key_version=checked_envelope.key_version,
        )
        expected_hash = credential_aad_hash(
            checked_binding,
            key_version=checked_envelope.key_version,
        )
        if not secrets.compare_digest(expected_hash, checked_envelope.aad_hash):
            raise CredentialVaultError("credential_decryption_failed")
        try:
            plaintext = cipher.decrypt(
                checked_envelope.nonce,
                checked_envelope.ciphertext,
                aad,
            )
        except (InvalidTag, TypeError, ValueError):
            raise CredentialVaultError("credential_decryption_failed") from None
        return bytearray(plaintext)

    def rotate(
        self,
        binding: CredentialBinding,
        envelope: CredentialEnvelope,
    ) -> CredentialEnvelope:
        checked_binding = self._binding(binding)
        checked_envelope = self._envelope(envelope)
        plaintext = self.open(checked_binding, checked_envelope)
        try:
            return self._seal(
                checked_binding,
                plaintext,
                envelope_id=checked_envelope.id,
                created_at=checked_envelope.created_at,
                rotated_at=self._timestamp(),
            )
        finally:
            _clear_mutable(plaintext)

    def _seal(
        self,
        binding: CredentialBinding,
        plaintext: bytearray,
        *,
        envelope_id,
        created_at: datetime | None,
        rotated_at: datetime | None,
    ) -> CredentialEnvelope:
        nonce = self._nonce_factory(_NONCE_BYTES)
        if not isinstance(nonce, bytes) or len(nonce) != _NONCE_BYTES:
            raise CredentialVaultError("credential_envelope_invalid")
        aad = credential_aad(binding, key_version=self._active_key_version)
        try:
            ciphertext = self._ciphers[self._active_key_version].encrypt(
                nonce,
                plaintext,
                aad,
            )
        except (TypeError, ValueError):
            raise CredentialVaultError("credential_plaintext_invalid") from None
        timestamp = created_at or self._timestamp()
        try:
            return CredentialEnvelope(
                id=envelope_id or uuid4(),
                tenant_id=binding.tenant_id,
                workspace_id=binding.workspace_id,
                auth_user_id=binding.auth_user_id,
                connection_id=binding.connection_id,
                provider=binding.provider,
                environment=binding.environment,
                credential_type=binding.credential_type,
                key_version=self._active_key_version,
                nonce=nonce,
                ciphertext=ciphertext,
                aad_hash=credential_aad_hash(
                    binding,
                    key_version=self._active_key_version,
                ),
                created_at=timestamp,
                rotated_at=rotated_at,
                revoked_at=None,
            )
        except (TypeError, ValueError, ValidationError):
            raise CredentialVaultError("credential_envelope_invalid") from None

    def _timestamp(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("credential_vault_clock_invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _binding(binding: CredentialBinding) -> CredentialBinding:
        try:
            return CredentialBinding.model_validate(binding)
        except (TypeError, ValueError, ValidationError):
            raise CredentialVaultError("credential_binding_invalid") from None

    @staticmethod
    def _envelope(envelope: CredentialEnvelope) -> CredentialEnvelope:
        try:
            return CredentialEnvelope.model_validate(envelope)
        except (TypeError, ValueError, ValidationError):
            raise CredentialVaultError("credential_envelope_invalid") from None

    @staticmethod
    def _metadata_matches(
        binding: CredentialBinding,
        envelope: CredentialEnvelope,
    ) -> bool:
        return (
            envelope.tenant_id == binding.tenant_id
            and envelope.workspace_id == binding.workspace_id
            and envelope.auth_user_id == binding.auth_user_id
            and envelope.connection_id == binding.connection_id
            and secrets.compare_digest(envelope.provider, binding.provider)
            and secrets.compare_digest(envelope.environment, binding.environment)
            and secrets.compare_digest(
                envelope.credential_type,
                binding.credential_type,
            )
        )


__all__ = ["CredentialVault", "CredentialVaultError"]
