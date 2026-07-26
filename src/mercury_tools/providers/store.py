"""Tenant-isolated provider connection state and encrypted-envelope ownership."""

from __future__ import annotations

import re
import secrets
import threading
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError

from mercury_tools.credentials.models import (
    CredentialBinding,
    CredentialEnvelope,
    credential_aad_hash,
)
from mercury_tools.credentials.vault import CredentialVault, CredentialVaultError
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    DisconnectResult,
    ProviderConnection,
    ProviderConnectionSummary,
    ProviderId,
    SetupAttempt,
)

_TOKEN_HASH = re.compile(r"^[0-9a-f]{64}$")
_MAX_ATTEMPT_LIFETIME = timedelta(minutes=10)
_STORE_ERROR_CODES = frozenset(
    {
        "provider_connection_conflict",
        "provider_connection_invalid",
        "provider_connection_not_found",
        "provider_credential_binding_invalid",
        "provider_setup_attempt_conflict",
        "provider_setup_attempt_invalid",
    }
)


class ProviderStoreError(RuntimeError):
    """A stable provider-state failure without credentials or raw payloads."""

    def __init__(self, code: str) -> None:
        if code not in _STORE_ERROR_CODES:
            raise ValueError("provider_store_error_invalid")
        self.code = code
        super().__init__(code)


class ProviderConnectionStore:
    """Reference store enforcing the same bindings as the narrow database RPCs."""

    def __init__(
        self,
        *,
        vault: CredentialVault,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(vault, CredentialVault):
            raise TypeError("provider_connection_store_vault_required")
        self._vault = vault
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._attempts: dict[UUID, SetupAttempt] = {}
        self._connections: dict[UUID, ProviderConnection] = {}
        self._envelopes: dict[UUID, CredentialEnvelope] = {}

    def __repr__(self) -> str:
        return "ProviderConnectionStore()"

    def create_attempt(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        token_hash: str,
        expires_at: datetime,
    ) -> SetupAttempt:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        checked_provider = self._provider(provider, "provider_setup_attempt_invalid")
        now = self._timestamp()
        if (
            not isinstance(token_hash, str)
            or _TOKEN_HASH.fullmatch(token_hash) is None
            or not isinstance(expires_at, datetime)
            or expires_at.tzinfo is None
            or expires_at.utcoffset() is None
        ):
            raise ProviderStoreError("provider_setup_attempt_invalid")
        normalized_expiry = expires_at.astimezone(UTC)
        if not now < normalized_expiry <= now + _MAX_ATTEMPT_LIFETIME:
            raise ProviderStoreError("provider_setup_attempt_invalid")
        try:
            attempt = SetupAttempt(
                id=uuid4(),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                token_hash=token_hash,
                expires_at=normalized_expiry,
                consumed_at=None,
                created_at=now,
            )
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_setup_attempt_invalid") from None

        with self._lock:
            if any(
                secrets.compare_digest(existing.token_hash, token_hash)
                for existing in self._attempts.values()
            ):
                raise ProviderStoreError("provider_setup_attempt_conflict")
            self._attempts[attempt.id] = attempt
        return attempt

    def consume_attempt(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        token_hash: str,
    ) -> SetupAttempt:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        checked_provider = self._provider(provider, "provider_setup_attempt_invalid")
        if not isinstance(token_hash, str) or _TOKEN_HASH.fullmatch(token_hash) is None:
            raise ProviderStoreError("provider_setup_attempt_invalid")
        now = self._timestamp()
        with self._lock:
            attempt = next(
                (
                    item
                    for item in self._attempts.values()
                    if item.tenant_id == tenant_id
                    and item.workspace_id == workspace_id
                    and item.auth_user_id == auth_user_id
                    and item.provider is checked_provider
                    and secrets.compare_digest(item.environment, environment)
                    and secrets.compare_digest(item.token_hash, token_hash)
                ),
                None,
            )
            if (
                attempt is None
                or attempt.consumed_at is not None
                or attempt.expires_at <= now
            ):
                raise ProviderStoreError("provider_setup_attempt_invalid")
            consumed = SetupAttempt.model_validate(
                {
                    **self._attempt_values(attempt),
                    "consumed_at": now,
                }
            )
            self._attempts[attempt.id] = consumed
            return consumed

    def save_connection(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
        provider: ProviderId | str,
        environment: str,
        company_or_merchant_id: str,
        account_display_name: str,
        authorization_method: AuthorizationMethod | str,
        granted_permissions: Sequence[str],
        readiness: ConnectionReadiness | str,
        revision: int,
        validated_at: datetime | None,
        envelopes: Sequence[CredentialEnvelope],
    ) -> ProviderConnection:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(connection_id)
        checked_provider = self._provider(provider, "provider_connection_invalid")
        try:
            checked_method = AuthorizationMethod(authorization_method)
            checked_readiness = ConnectionReadiness(readiness)
        except (TypeError, ValueError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if isinstance(granted_permissions, (str, bytes, bytearray)):
            raise ProviderStoreError("provider_connection_invalid")
        try:
            supplied_permissions = tuple(granted_permissions)
        except TypeError:
            raise ProviderStoreError("provider_connection_invalid") from None
        if (
            any(not isinstance(item, str) for item in supplied_permissions)
            or len(supplied_permissions) != len(set(supplied_permissions))
        ):
            raise ProviderStoreError("provider_connection_invalid")
        permissions = tuple(sorted(supplied_permissions))
        checked_envelopes = self._validate_envelopes(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            connection_id=connection_id,
            provider=checked_provider,
            environment=environment,
            company_or_merchant_id=company_or_merchant_id,
            envelopes=envelopes,
        )
        now = self._timestamp()
        try:
            connection = ProviderConnection(
                id=connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                provider_account_id=company_or_merchant_id,
                account_display_name=account_display_name,
                authorization_method=checked_method,
                granted_permissions=permissions,
                readiness=checked_readiness,
                revision=revision,
                last_validated_at=validated_at,
                credential_envelope_ids=tuple(item.id for item in checked_envelopes),
                provider_revocation_required=False,
                disconnected_at=None,
                created_at=now,
                updated_at=now,
            )
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None

        with self._lock:
            current = self._connections.get(connection_id)
            if any(
                (existing := self._envelopes.get(envelope.id)) is not None
                and existing.connection_id != connection_id
                for envelope in checked_envelopes
            ):
                raise ProviderStoreError("provider_credential_binding_invalid")
            if current is None:
                if revision != 1 or any(
                    self._same_account(item, connection)
                    for item in self._connections.values()
                ):
                    raise ProviderStoreError("provider_connection_conflict")
            else:
                if (
                    revision != current.revision + 1
                    or not self._same_binding(current, connection)
                ):
                    raise ProviderStoreError("provider_connection_conflict")
                connection = ProviderConnection.model_validate(
                    {
                        **self._connection_values(connection),
                        "created_at": current.created_at,
                    }
                )
                for envelope_id in current.credential_envelope_ids:
                    self._envelopes.pop(envelope_id, None)

            self._connections[connection_id] = connection
            for envelope in checked_envelopes:
                self._envelopes[envelope.id] = envelope
        return connection

    def list_for_workspace(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
    ) -> tuple[ProviderConnectionSummary, ...]:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        with self._lock:
            matching = [
                connection.summary()
                for connection in self._connections.values()
                if connection.tenant_id == tenant_id
                and connection.workspace_id == workspace_id
                and connection.auth_user_id == auth_user_id
            ]
        return tuple(
            sorted(
                matching,
                key=lambda item: (
                    item.provider.value,
                    item.environment,
                    item.account_display_name,
                    str(item.connection_id),
                ),
            )
        )

    def disconnect(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
        provider_revocation_required: bool = False,
    ) -> DisconnectResult:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(connection_id)
        if not isinstance(provider_revocation_required, bool):
            raise ProviderStoreError("provider_connection_invalid")
        now = self._timestamp()
        with self._lock:
            current = self._connections.get(connection_id)
            if current is None or not (
                current.tenant_id == tenant_id
                and current.workspace_id == workspace_id
                and current.auth_user_id == auth_user_id
            ):
                raise ProviderStoreError("provider_connection_not_found")

            already_disconnected = (
                current.readiness is ConnectionReadiness.DISCONNECTED
            )
            deleted = 0
            if not already_disconnected:
                for envelope_id in current.credential_envelope_ids:
                    if self._envelopes.pop(envelope_id, None) is not None:
                        deleted += 1
                required = (
                    current.provider_revocation_required
                    or provider_revocation_required
                )
                disconnected = ProviderConnection.model_validate(
                    {
                        **self._connection_values(current),
                        "readiness": ConnectionReadiness.DISCONNECTED,
                        "revision": current.revision + 1,
                        "credential_envelope_ids": (),
                        "provider_revocation_required": required,
                        "disconnected_at": now,
                        "updated_at": now,
                    }
                )
                self._connections[connection_id] = disconnected
            else:
                disconnected = current
                required = (
                    current.provider_revocation_required
                    or provider_revocation_required
                )
                if required != current.provider_revocation_required:
                    disconnected = ProviderConnection.model_validate(
                        {
                            **self._connection_values(current),
                            "provider_revocation_required": required,
                            "updated_at": now,
                        }
                    )
                    self._connections[connection_id] = disconnected

            return DisconnectResult(
                connection_id=connection_id,
                deleted_envelope_count=deleted,
                already_disconnected=already_disconnected,
                provider_revocation_required=required,
                revision=disconnected.revision,
            )

    def _validate_envelopes(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
        provider: ProviderId,
        environment: str,
        company_or_merchant_id: str,
        envelopes: Sequence[CredentialEnvelope],
    ) -> tuple[CredentialEnvelope, ...]:
        if isinstance(envelopes, (str, bytes, bytearray)):
            raise ProviderStoreError("provider_credential_binding_invalid")
        try:
            checked = tuple(
                CredentialEnvelope.model_validate(envelope) for envelope in envelopes
            )
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_credential_binding_invalid") from None
        if (
            not checked
            or len(checked) > 16
            or len({item.id for item in checked}) != len(checked)
            or len({item.credential_type for item in checked}) != len(checked)
        ):
            raise ProviderStoreError("provider_credential_binding_invalid")

        for envelope in checked:
            if (
                envelope.tenant_id != tenant_id
                or envelope.workspace_id != workspace_id
                or envelope.auth_user_id != auth_user_id
                or envelope.connection_id != connection_id
                or envelope.provider != provider.value
                or envelope.environment != environment
                or envelope.revoked_at is not None
            ):
                raise ProviderStoreError("provider_credential_binding_invalid")
            try:
                binding = CredentialBinding(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=auth_user_id,
                    connection_id=connection_id,
                    provider=provider.value,
                    company_or_merchant_id=company_or_merchant_id,
                    environment=environment,
                    credential_type=envelope.credential_type,
                )
            except (TypeError, ValueError, ValidationError):
                raise ProviderStoreError(
                    "provider_credential_binding_invalid"
                ) from None
            if not secrets.compare_digest(
                envelope.aad_hash,
                credential_aad_hash(binding, key_version=envelope.key_version),
            ):
                raise ProviderStoreError("provider_credential_binding_invalid")
            opened: bytearray | None = None
            try:
                opened = self._vault.open(binding, envelope)
                if not isinstance(opened, bytearray):
                    raise ProviderStoreError("provider_credential_binding_invalid")
            except CredentialVaultError:
                raise ProviderStoreError(
                    "provider_credential_binding_invalid"
                ) from None
            finally:
                if opened is not None:
                    self._clear_opened_plaintext(opened)
        return checked

    def _timestamp(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise ValueError("provider_store_clock_invalid")
        return value.astimezone(UTC)

    @staticmethod
    def _bound_ids(
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
    ) -> None:
        for value in (tenant_id, workspace_id, auth_user_id):
            ProviderConnectionStore._require_uuid(value)

    @staticmethod
    def _require_uuid(value: UUID) -> None:
        if not isinstance(value, UUID) or value.int == 0:
            raise ProviderStoreError("provider_connection_invalid")

    @staticmethod
    def _provider(value: ProviderId | str, error_code: str) -> ProviderId:
        try:
            return ProviderId(value)
        except (TypeError, ValueError):
            raise ProviderStoreError(error_code) from None

    @staticmethod
    def _attempt_values(attempt: SetupAttempt) -> dict[str, object]:
        return {
            field_name: getattr(attempt, field_name)
            for field_name in type(attempt).model_fields
        }

    @staticmethod
    def _connection_values(connection: ProviderConnection) -> dict[str, object]:
        return {
            field_name: getattr(connection, field_name)
            for field_name in type(connection).model_fields
        }

    @staticmethod
    def _same_binding(
        current: ProviderConnection,
        replacement: ProviderConnection,
    ) -> bool:
        return (
            current.tenant_id == replacement.tenant_id
            and current.workspace_id == replacement.workspace_id
            and current.auth_user_id == replacement.auth_user_id
            and current.provider is replacement.provider
            and current.environment == replacement.environment
            and current.provider_account_id == replacement.provider_account_id
        )

    @staticmethod
    def _same_account(
        left: ProviderConnection,
        right: ProviderConnection,
    ) -> bool:
        return ProviderConnectionStore._same_binding(left, right)

    @staticmethod
    def _clear_opened_plaintext(value: bytearray) -> None:
        with suppress(Exception):
            value[:] = b"\x00" * len(value)


__all__ = ["ProviderConnectionStore", "ProviderStoreError"]
