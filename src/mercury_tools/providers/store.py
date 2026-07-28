"""Tenant-isolated provider connection state and encrypted-envelope ownership."""

from __future__ import annotations

import re
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
from pydantic import ValidationError

from mercury_tools.config import Settings, v1_supabase_rest_url
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
        "provider_store_unavailable",
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
            if attempt is None or attempt.consumed_at is not None or attempt.expires_at <= now:
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
        if any(not isinstance(item, str) for item in supplied_permissions) or len(
            supplied_permissions
        ) != len(set(supplied_permissions)):
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
                    self._same_account(item, connection) for item in self._connections.values()
                ):
                    raise ProviderStoreError("provider_connection_conflict")
            else:
                if revision != current.revision + 1 or not self._same_binding(current, connection):
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

            already_disconnected = current.readiness is ConnectionReadiness.DISCONNECTED
            deleted = 0
            if not already_disconnected:
                for envelope_id in current.credential_envelope_ids:
                    if self._envelopes.pop(envelope_id, None) is not None:
                        deleted += 1
                required = current.provider_revocation_required or provider_revocation_required
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
                required = current.provider_revocation_required or provider_revocation_required
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

    def complete_revocation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
    ) -> DisconnectResult:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(connection_id)
        now = self._timestamp()
        with self._lock:
            current = self._connections.get(connection_id)
            if current is None or not (
                current.tenant_id == tenant_id
                and current.workspace_id == workspace_id
                and current.auth_user_id == auth_user_id
            ):
                raise ProviderStoreError("provider_connection_not_found")
            if (
                current.readiness is not ConnectionReadiness.DISCONNECTED
                or current.credential_envelope_ids
                or any(
                    envelope.connection_id == connection_id for envelope in self._envelopes.values()
                )
            ):
                raise ProviderStoreError("provider_connection_invalid")
            if current.provider_revocation_required:
                current = ProviderConnection.model_validate(
                    {
                        **self._connection_values(current),
                        "provider_revocation_required": False,
                        "updated_at": now,
                    }
                )
                self._connections[connection_id] = current
            return DisconnectResult(
                connection_id=connection_id,
                deleted_envelope_count=0,
                already_disconnected=True,
                provider_revocation_required=False,
                revision=current.revision,
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
            checked = tuple(CredentialEnvelope.model_validate(envelope) for envelope in envelopes)
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
                raise ProviderStoreError("provider_credential_binding_invalid") from None
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
                raise ProviderStoreError("provider_credential_binding_invalid") from None
            finally:
                if opened is not None:
                    self._clear_opened_plaintext(opened)
        return checked

    def _timestamp(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
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
            field_name: getattr(attempt, field_name) for field_name in type(attempt).model_fields
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


class SupabaseProviderConnectionStore:
    """Durable service-role adapter over the narrow provider credential RPCs."""

    def __init__(
        self,
        *,
        settings: Settings,
        vault: CredentialVault,
        http_client: httpx.Client,
    ) -> None:
        try:
            self._base_url = v1_supabase_rest_url(
                project_url=settings.supabase_url,
                auth_issuer=settings.supabase_auth_issuer,
            )
            if (
                not settings.supabase_service_role_key
                or not isinstance(vault, CredentialVault)
                or not isinstance(http_client, httpx.Client)
            ):
                raise ValueError
        except Exception:
            raise ProviderStoreError("provider_store_unavailable") from None
        self._service_role_key = settings.supabase_service_role_key
        self._vault = vault
        self._http = http_client

    def __repr__(self) -> str:
        return "SupabaseProviderConnectionStore()"

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
        checked = self._validated_connection(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            connection_id=connection_id,
            provider=provider,
            environment=environment,
            company_or_merchant_id=company_or_merchant_id,
            account_display_name=account_display_name,
            authorization_method=authorization_method,
            granted_permissions=granted_permissions,
            readiness=readiness,
            revision=revision,
            validated_at=validated_at,
            envelopes=envelopes,
        )
        row = self._rpc_one(
            "save_mercury_provider_connection",
            {
                "p_connection_id": str(checked.id),
                "p_tenant_id": str(checked.tenant_id),
                "p_workspace_id": str(checked.workspace_id),
                "p_auth_user_id": str(checked.auth_user_id),
                "p_provider": checked.provider.value,
                "p_environment": checked.environment,
                "p_provider_account_id": checked.provider_account_id,
                "p_account_display_name": checked.account_display_name,
                "p_authorization_method": checked.authorization_method.value,
                "p_granted_permissions": list(checked.granted_permissions),
                "p_readiness": checked.readiness.value,
                "p_revision": checked.revision,
                "p_last_validated_at": (
                    checked.last_validated_at.isoformat()
                    if checked.last_validated_at is not None
                    else None
                ),
                "p_envelopes": [self._envelope_payload(envelope) for envelope in envelopes],
            },
        )
        try:
            if (
                UUID(str(row["connection_id"])) != checked.id
                or ProviderId(row["provider"]) is not checked.provider
                or row["environment"] != checked.environment
                or row["account_display_name"] != checked.account_display_name
                or AuthorizationMethod(row["authorization_method"])
                is not checked.authorization_method
                or tuple(row["granted_permissions"]) != checked.granted_permissions
                or ConnectionReadiness(row["readiness"]) is not checked.readiness
                or row["revision"] != checked.revision
                or bool(row["provider_revocation_required"])
            ):
                raise ValueError
            created_at = _rpc_timestamp(row["created_at"])
            updated_at = _rpc_timestamp(row["updated_at"])
            last_validated_at = (
                _rpc_timestamp(row["last_validated_at"])
                if row["last_validated_at"] is not None
                else None
            )
            if last_validated_at != checked.last_validated_at:
                raise ValueError
            return checked.model_copy(
                update={
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
            )
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def load_envelopes(
        self,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        rows = self._rpc_rows(
            "load_mercury_provider_credential_envelopes",
            {
                "p_tenant_id": str(checked.tenant_id),
                "p_workspace_id": str(checked.workspace_id),
                "p_auth_user_id": str(checked.auth_user_id),
                "p_connection_id": str(checked.id),
            },
        )
        try:
            envelopes = tuple(self._envelope_from_row(row) for row in rows)
            self._validate_envelopes(
                connection=checked,
                envelopes=envelopes,
            )
            if tuple(envelope.id for envelope in envelopes) != checked.credential_envelope_ids:
                raise ValueError
            return envelopes
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_credential_binding_invalid") from None

    def replace_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        return self.save_connection(
            tenant_id=checked.tenant_id,
            workspace_id=checked.workspace_id,
            auth_user_id=checked.auth_user_id,
            connection_id=checked.id,
            provider=checked.provider,
            environment=checked.environment,
            company_or_merchant_id=checked.provider_account_id,
            account_display_name=checked.account_display_name,
            authorization_method=checked.authorization_method,
            granted_permissions=checked.granted_permissions,
            readiness=checked.readiness,
            revision=checked.revision + 1,
            validated_at=checked.last_validated_at,
            envelopes=envelopes,
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
        if not isinstance(provider_revocation_required, bool):
            raise ProviderStoreError("provider_connection_invalid")
        return self._disconnect_result(
            self._rpc_one(
                "disconnect_mercury_provider_connection",
                {
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_connection_id": str(connection_id),
                    "p_provider_revocation_required": provider_revocation_required,
                },
            ),
            connection_id=connection_id,
        )

    def complete_revocation(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
    ) -> DisconnectResult:
        return self._disconnect_result(
            self._rpc_one(
                "complete_mercury_provider_revocation",
                {
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_connection_id": str(connection_id),
                },
            ),
            connection_id=connection_id,
        )

    def _validated_connection(
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
        try:
            checked_envelopes = tuple(
                CredentialEnvelope.model_validate(envelope) for envelope in envelopes
            )
            checked = ProviderConnection(
                id=connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=ProviderId(provider),
                environment=environment,
                provider_account_id=company_or_merchant_id,
                account_display_name=account_display_name,
                authorization_method=AuthorizationMethod(authorization_method),
                granted_permissions=tuple(sorted(granted_permissions)),
                readiness=ConnectionReadiness(readiness),
                revision=revision,
                last_validated_at=validated_at,
                credential_envelope_ids=tuple(envelope.id for envelope in checked_envelopes),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            if (
                isinstance(granted_permissions, (str, bytes, bytearray))
                or tuple(granted_permissions) != checked.granted_permissions
                or len(checked.granted_permissions) != len(set(checked.granted_permissions))
            ):
                raise ValueError
            self._validate_envelopes(
                connection=checked,
                envelopes=checked_envelopes,
            )
            return checked
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def _validate_envelopes(
        self,
        *,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> None:
        if (
            not envelopes
            or len(envelopes) > 16
            or len({item.id for item in envelopes}) != len(envelopes)
            or len({item.credential_type for item in envelopes}) != len(envelopes)
        ):
            raise ProviderStoreError("provider_credential_binding_invalid")
        for envelope in envelopes:
            binding = CredentialBinding(
                tenant_id=connection.tenant_id,
                workspace_id=connection.workspace_id,
                auth_user_id=connection.auth_user_id,
                connection_id=connection.id,
                provider=connection.provider.value,
                company_or_merchant_id=connection.provider_account_id,
                environment=connection.environment,
                credential_type=envelope.credential_type,
            )
            opened: bytearray | None = None
            try:
                opened = self._vault.open(binding, envelope)
            except CredentialVaultError:
                raise ProviderStoreError("provider_credential_binding_invalid") from None
            finally:
                if opened is not None:
                    ProviderConnectionStore._clear_opened_plaintext(opened)

    def _rpc_one(
        self,
        function: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        rows = self._rpc_rows(function, payload)
        if len(rows) != 1:
            raise ProviderStoreError("provider_store_unavailable")
        return rows[0]

    def _rpc_rows(
        self,
        function: str,
        payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            response = self._http.post(
                f"{self._base_url}/rpc/{function}",
                json=dict(payload),
                headers={
                    "apikey": self._service_role_key,
                    "Authorization": f"Bearer {self._service_role_key}",
                    "Content-Type": "application/json",
                },
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise ProviderStoreError("provider_store_unavailable") from None
        if response.status_code < 200 or response.status_code >= 300:
            code = "provider_connection_conflict" if response.status_code == 409 else None
            with suppress(Exception):
                message = response.json().get("message")
                if message in _STORE_ERROR_CODES:
                    code = message
                elif message == "provider_credential_envelope_invalid":
                    code = "provider_credential_binding_invalid"
            raise ProviderStoreError(code or "provider_store_unavailable")
        try:
            value = response.json()
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError
            return value
        except (TypeError, ValueError):
            raise ProviderStoreError("provider_store_unavailable") from None

    @staticmethod
    def _disconnect_result(
        row: Mapping[str, Any],
        *,
        connection_id: UUID,
    ) -> DisconnectResult:
        try:
            result = DisconnectResult(
                connection_id=UUID(str(row["connection_id"])),
                deleted_envelope_count=row["deleted_envelope_count"],
                already_disconnected=row["already_disconnected"],
                provider_revocation_required=row["provider_revocation_required"],
                revision=row["revision"],
            )
            if result.connection_id != connection_id or row.get("status") != "disconnected":
                raise ValueError
            return result
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    @staticmethod
    def _envelope_payload(envelope: CredentialEnvelope) -> dict[str, Any]:
        checked = CredentialEnvelope.model_validate(envelope)
        return {
            "id": str(checked.id),
            "credential_type": checked.credential_type,
            "key_version": checked.key_version,
            "nonce": checked.nonce.hex(),
            "ciphertext": checked.ciphertext.hex(),
            "aad_hash": checked.aad_hash.hex(),
            "created_at": checked.created_at.isoformat(),
            "rotated_at": (
                checked.rotated_at.isoformat() if checked.rotated_at is not None else None
            ),
            "revoked_at": (
                checked.revoked_at.isoformat() if checked.revoked_at is not None else None
            ),
        }

    @staticmethod
    def _envelope_from_row(row: Mapping[str, Any]) -> CredentialEnvelope:
        return CredentialEnvelope(
            id=UUID(str(row["id"])),
            tenant_id=UUID(str(row["tenant_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            auth_user_id=UUID(str(row["auth_user_id"])),
            connection_id=UUID(str(row["connection_id"])),
            provider=row["provider"],
            environment=row["environment"],
            credential_type=row["credential_type"],
            key_version=row["key_version"],
            nonce=_rpc_bytea(row["nonce"]),
            ciphertext=_rpc_bytea(row["ciphertext"]),
            aad_hash=_rpc_bytea(row["aad_hash"]),
            created_at=_rpc_timestamp(row["created_at"]),
            rotated_at=(
                _rpc_timestamp(row["rotated_at"]) if row.get("rotated_at") is not None else None
            ),
            revoked_at=(
                _rpc_timestamp(row["revoked_at"]) if row.get("revoked_at") is not None else None
            ),
        )


def _rpc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    checked = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError
    return checked.astimezone(UTC)


def _rpc_bytea(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith("\\x"):
        raise ValueError
    return bytes.fromhex(value[2:])


__all__ = [
    "ProviderConnectionStore",
    "ProviderStoreError",
    "SupabaseProviderConnectionStore",
]
