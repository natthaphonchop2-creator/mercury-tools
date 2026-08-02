"""Tenant-isolated provider connection state and encrypted-envelope ownership."""

from __future__ import annotations

import re
import secrets
import threading
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
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
_PROVIDER_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_MAX_ATTEMPT_LIFETIME = timedelta(minutes=10)
_OAUTH_ATTEMPT_STATUSES = frozenset(
    {
        "exchange_pending",
        "material_attached",
        "finalized",
        "failed",
        "revoked",
    }
)
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


@dataclass(frozen=True)
class ProviderConnectionTarget:
    """Optimistic target selected for one atomic connection finalization."""

    connection_id: UUID
    revision: int
    reuses_existing: bool

    def __post_init__(self) -> None:
        if (
            not isinstance(self.connection_id, UUID)
            or self.connection_id.int == 0
            or not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 1
            or not isinstance(self.reuses_existing, bool)
        ):
            raise ProviderStoreError("provider_connection_invalid")


@dataclass(frozen=True, repr=False)
class ProviderOAuthAttempt:
    """Internal recovery record for one consumed provider OAuth state."""

    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str
    granted_permissions: tuple[str, ...]
    status: str
    provider_account_id: str | None
    account_display_name: str | None
    authorization_method: AuthorizationMethod | None
    credential_envelope_ids: tuple[UUID, ...]
    material_revision: int
    target_connection_id: UUID | None
    target_revision: int | None
    acknowledged_at: datetime | None
    provider_revocation_required: bool
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if (
            any(
                value.int == 0
                for value in (
                    self.id,
                    self.tenant_id,
                    self.workspace_id,
                    self.auth_user_id,
                )
            )
            or self.status not in _OAUTH_ATTEMPT_STATUSES
            or tuple(sorted(self.granted_permissions)) != self.granted_permissions
            or len(set(self.granted_permissions)) != len(self.granted_permissions)
            or not isinstance(self.material_revision, int)
            or isinstance(self.material_revision, bool)
            or self.material_revision < 0
            or not isinstance(self.provider_revocation_required, bool)
            or self.created_at.tzinfo is None
            or self.updated_at.tzinfo is None
            or self.updated_at < self.created_at
            or (
                self.acknowledged_at is not None
                and (self.acknowledged_at.tzinfo is None or self.acknowledged_at < self.created_at)
            )
            or (self.status == "exchange_pending" and self.material_revision != 0)
            or (self.status in {"material_attached", "finalized"} and self.material_revision < 1)
            or (self.target_connection_id is None and self.target_revision is not None)
            or (
                self.target_connection_id is not None
                and (
                    self.target_connection_id.int == 0
                    or self.target_revision is None
                    or self.target_revision < 1
                )
            )
        ):
            raise ProviderStoreError("provider_connection_invalid")


@dataclass(frozen=True)
class ProviderOAuthAttemptResult:
    """Secretless result of an internal attempt transition."""

    attempt_id: UUID
    status: str
    target_connection_id: UUID | None
    target_revision: int | None
    provider_revocation_required: bool

    def __post_init__(self) -> None:
        if (
            self.attempt_id.int == 0
            or self.status not in _OAUTH_ATTEMPT_STATUSES
            or not isinstance(self.provider_revocation_required, bool)
            or (self.target_connection_id is None and self.target_revision is not None)
            or (
                self.target_connection_id is not None
                and (
                    self.target_connection_id.int == 0
                    or self.target_revision is None
                    or self.target_revision < 1
                )
            )
        ):
            raise ProviderStoreError("provider_connection_invalid")


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
        self._oauth_attempts: dict[UUID, ProviderOAuthAttempt] = {}
        self._oauth_attempt_envelopes: dict[UUID, CredentialEnvelope] = {}
        self._connection_oauth_generations: dict[UUID, UUID] = {}

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

    def begin_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        granted_permissions: Sequence[str],
    ) -> ProviderOAuthAttempt:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(attempt_id)
        checked_provider = self._provider(provider, "provider_connection_invalid")
        permissions = self._permissions(granted_permissions)
        now = self._timestamp()
        attempt = ProviderOAuthAttempt(
            id=attempt_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            provider=checked_provider,
            environment=environment,
            granted_permissions=permissions,
            status="exchange_pending",
            provider_account_id=None,
            account_display_name=None,
            authorization_method=None,
            credential_envelope_ids=(),
            material_revision=0,
            target_connection_id=None,
            target_revision=None,
            acknowledged_at=None,
            provider_revocation_required=True,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            existing = self._oauth_attempts.get(attempt_id)
            if existing is not None:
                if not self._same_oauth_attempt_binding(existing, attempt):
                    raise ProviderStoreError("provider_connection_conflict")
                return existing
            if attempt_id in self._connections:
                raise ProviderStoreError("provider_connection_conflict")
            self._oauth_attempts[attempt_id] = attempt
            return attempt

    def attach_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or revision != 1
            or validated_at is not None
        ):
            raise ProviderStoreError("provider_connection_invalid")
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(attempt_id)
        checked_provider = self._provider(provider, "provider_connection_invalid")
        permissions = self._permissions(granted_permissions)
        try:
            checked_method = AuthorizationMethod(authorization_method)
        except (TypeError, ValueError):
            raise ProviderStoreError("provider_connection_invalid") from None
        checked_envelopes = self._validate_envelopes(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            connection_id=attempt_id,
            provider=checked_provider,
            environment=environment,
            company_or_merchant_id=company_or_merchant_id,
            envelopes=envelopes,
        )
        now = self._timestamp()
        try:
            provisional = ProviderConnection(
                id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                provider_account_id=company_or_merchant_id,
                account_display_name=account_display_name,
                authorization_method=checked_method,
                granted_permissions=permissions,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                last_validated_at=None,
                credential_envelope_ids=tuple(item.id for item in checked_envelopes),
                provider_revocation_required=True,
                disconnected_at=None,
                created_at=now,
                updated_at=now,
            )
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None

        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            if attempt.granted_permissions != permissions:
                raise ProviderStoreError("provider_connection_conflict")
            if attempt.status == "material_attached":
                stored = tuple(
                    self._oauth_attempt_envelopes[envelope_id]
                    for envelope_id in attempt.credential_envelope_ids
                )
                if (
                    attempt.provider_account_id != company_or_merchant_id
                    or attempt.account_display_name != account_display_name
                    or attempt.authorization_method is not checked_method
                    or stored != checked_envelopes
                ):
                    raise ProviderStoreError("provider_connection_conflict")
                return provisional.model_copy(
                    update={
                        "created_at": attempt.created_at,
                        "updated_at": attempt.updated_at,
                    }
                )
            if attempt.status != "exchange_pending":
                raise ProviderStoreError("provider_connection_conflict")
            if any(
                envelope.id in self._envelopes or envelope.id in self._oauth_attempt_envelopes
                for envelope in checked_envelopes
            ):
                raise ProviderStoreError("provider_credential_binding_invalid")
            for envelope in checked_envelopes:
                self._oauth_attempt_envelopes[envelope.id] = envelope
            updated = replace(
                attempt,
                status="material_attached",
                provider_account_id=company_or_merchant_id,
                account_display_name=account_display_name,
                authorization_method=checked_method,
                credential_envelope_ids=tuple(envelope.id for envelope in checked_envelopes),
                material_revision=1,
                updated_at=now,
            )
            self._oauth_attempts[attempt_id] = updated
            return provisional.model_copy(
                update={
                    "created_at": attempt.created_at,
                    "updated_at": now,
                }
            )

    def load_runtime_envelopes(
        self,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if checked.provider_account_id == f"oauth-pending-{checked.id}":
            return self.load_oauth_attempt_envelopes(
                attempt_id=checked.id,
                tenant_id=checked.tenant_id,
                workspace_id=checked.workspace_id,
                auth_user_id=checked.auth_user_id,
                provider=checked.provider,
                environment=checked.environment,
                connection=checked,
            )
        return self.load_envelopes(checked)

    def replace_runtime_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if checked.provider_account_id == f"oauth-pending-{checked.id}":
            return self.replace_oauth_attempt_envelopes(checked, envelopes)
        return self.replace_envelopes(checked, envelopes)

    def load_oauth_attempt_envelopes(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
            checked_provider = self._provider(provider, "provider_connection_invalid")
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            expected_connection_id = attempt.target_connection_id or attempt.id
            if (
                attempt.status not in {"material_attached", "failed"}
                or not attempt.provider_revocation_required
                or not attempt.credential_envelope_ids
                or attempt.provider_account_id is None
                or checked.id != expected_connection_id
                or checked.tenant_id != tenant_id
                or checked.workspace_id != workspace_id
                or checked.auth_user_id != auth_user_id
                or checked.provider is not checked_provider
                or checked.environment != environment
                or checked.provider_account_id != attempt.provider_account_id
                or checked.authorization_method is not attempt.authorization_method
            ):
                raise ProviderStoreError("provider_connection_not_found")
            try:
                return tuple(
                    self._oauth_attempt_envelopes[envelope_id]
                    for envelope_id in attempt.credential_envelope_ids
                )
            except KeyError:
                raise ProviderStoreError("provider_credential_binding_invalid") from None

    def replace_oauth_attempt_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if (
            checked.readiness is not ConnectionReadiness.REQUIRES_VALIDATION
            or checked.provider_account_id != f"oauth-pending-{checked.id}"
        ):
            raise ProviderStoreError("provider_connection_invalid")
        checked_envelopes = self._validate_envelopes(
            tenant_id=checked.tenant_id,
            workspace_id=checked.workspace_id,
            auth_user_id=checked.auth_user_id,
            connection_id=checked.id,
            provider=checked.provider,
            environment=checked.environment,
            company_or_merchant_id=checked.provider_account_id,
            envelopes=envelopes,
        )
        now = self._timestamp()
        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=checked.id,
                tenant_id=checked.tenant_id,
                workspace_id=checked.workspace_id,
                auth_user_id=checked.auth_user_id,
                provider=checked.provider,
                environment=checked.environment,
            )
            if (
                attempt.status != "material_attached"
                or attempt.provider_account_id != checked.provider_account_id
                or attempt.account_display_name != checked.account_display_name
                or attempt.authorization_method is not checked.authorization_method
                or attempt.granted_permissions != checked.granted_permissions
            ):
                raise ProviderStoreError("provider_connection_conflict")
            try:
                stored = tuple(
                    self._oauth_attempt_envelopes[envelope_id]
                    for envelope_id in attempt.credential_envelope_ids
                )
            except KeyError:
                raise ProviderStoreError("provider_credential_binding_invalid") from None
            if checked.revision + 1 == attempt.material_revision and stored == checked_envelopes:
                return checked.model_copy(
                    update={
                        "revision": attempt.material_revision,
                        "credential_envelope_ids": attempt.credential_envelope_ids,
                        "created_at": attempt.created_at,
                        "updated_at": attempt.updated_at,
                    }
                )
            if (
                checked.revision != attempt.material_revision
                or checked.credential_envelope_ids != attempt.credential_envelope_ids
            ):
                raise ProviderStoreError("provider_connection_conflict")
            if any(
                envelope.id in self._envelopes
                or (
                    envelope.id in self._oauth_attempt_envelopes
                    and envelope.id not in attempt.credential_envelope_ids
                )
                for envelope in checked_envelopes
            ):
                raise ProviderStoreError("provider_credential_binding_invalid")
            for envelope_id in attempt.credential_envelope_ids:
                self._oauth_attempt_envelopes.pop(envelope_id, None)
            for envelope in checked_envelopes:
                self._oauth_attempt_envelopes[envelope.id] = envelope
            updated = replace(
                attempt,
                credential_envelope_ids=tuple(envelope.id for envelope in checked_envelopes),
                material_revision=attempt.material_revision + 1,
                updated_at=now,
            )
            self._oauth_attempts[attempt.id] = updated
            return checked.model_copy(
                update={
                    "revision": updated.material_revision,
                    "credential_envelope_ids": updated.credential_envelope_ids,
                    "created_at": updated.created_at,
                    "updated_at": updated.updated_at,
                }
            )

    def load_envelopes(
        self,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        with self._lock:
            current = self._connections.get(checked.id)
            if (
                current != checked
                or checked.readiness is ConnectionReadiness.DISCONNECTED
                or checked.provider_account_id == f"oauth-pending-{checked.id}"
                or not self._connection_generation_is_dispatchable(checked)
            ):
                raise ProviderStoreError("provider_connection_not_found")
            try:
                return tuple(
                    self._envelopes[envelope_id] for envelope_id in checked.credential_envelope_ids
                )
            except KeyError:
                raise ProviderStoreError("provider_credential_binding_invalid") from None

    def finalize_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or attempt_id == connection_id
            or validated_at is None
        ):
            raise ProviderStoreError("provider_connection_invalid")
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(attempt_id)
        checked_provider = self._provider(provider, "provider_connection_invalid")
        permissions = self._permissions(granted_permissions)
        try:
            checked_method = AuthorizationMethod(authorization_method)
        except (TypeError, ValueError):
            raise ProviderStoreError("provider_connection_invalid") from None
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

        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            if attempt.granted_permissions != permissions or attempt.status not in {
                "material_attached",
                "finalized",
            }:
                raise ProviderStoreError("provider_connection_conflict")
            if attempt.status == "finalized":
                current = self._connections.get(connection_id)
                expected_readiness = (
                    ConnectionReadiness.READY
                    if attempt.acknowledged_at is not None
                    else ConnectionReadiness.REQUIRES_VALIDATION
                )
                try:
                    current_envelopes = (
                        tuple(
                            self._envelopes[envelope_id]
                            for envelope_id in current.credential_envelope_ids
                        )
                        if current is not None
                        else ()
                    )
                except KeyError:
                    raise ProviderStoreError("provider_credential_binding_invalid") from None
                if (
                    current is None
                    or attempt.target_connection_id != connection_id
                    or attempt.target_revision is None
                    or current.revision < attempt.target_revision
                    or self._connection_oauth_generations.get(connection_id) != attempt_id
                    or current.tenant_id != tenant_id
                    or current.workspace_id != workspace_id
                    or current.auth_user_id != auth_user_id
                    or current.provider is not checked_provider
                    or current.environment != environment
                    or current.provider_account_id != company_or_merchant_id
                    or current.account_display_name != account_display_name
                    or current.authorization_method is not checked_method
                    or current.granted_permissions != permissions
                    or current.readiness is not expected_readiness
                    or current.last_validated_at != validated_at
                    or not current_envelopes
                ):
                    raise ProviderStoreError("provider_connection_conflict")
                return current

            connections_before = dict(self._connections)
            envelopes_before = dict(self._envelopes)
            oauth_attempts_before = dict(self._oauth_attempts)
            oauth_envelopes_before = dict(self._oauth_attempt_envelopes)
            generations_before = dict(self._connection_oauth_generations)
            try:
                finalized = self.save_connection(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=auth_user_id,
                    connection_id=connection_id,
                    provider=checked_provider,
                    environment=environment,
                    company_or_merchant_id=company_or_merchant_id,
                    account_display_name=account_display_name,
                    authorization_method=authorization_method,
                    granted_permissions=permissions,
                    readiness=readiness,
                    revision=revision,
                    validated_at=validated_at,
                    envelopes=checked_envelopes,
                )
                self._connection_oauth_generations[connection_id] = attempt_id
                for envelope_id in attempt.credential_envelope_ids:
                    self._oauth_attempt_envelopes.pop(envelope_id, None)
                self._oauth_attempts[attempt_id] = replace(
                    attempt,
                    status="finalized",
                    provider_account_id=company_or_merchant_id,
                    account_display_name=account_display_name,
                    authorization_method=checked_method,
                    credential_envelope_ids=(),
                    target_connection_id=connection_id,
                    target_revision=finalized.revision,
                    acknowledged_at=None,
                    provider_revocation_required=False,
                    updated_at=self._timestamp(),
                )
                return finalized
            except BaseException:
                self._connections = connections_before
                self._envelopes = envelopes_before
                self._oauth_attempts = oauth_attempts_before
                self._oauth_attempt_envelopes = oauth_envelopes_before
                self._connection_oauth_generations = generations_before
                raise

    def acknowledge_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        connection: ProviderConnection,
    ) -> ProviderConnection:
        checked_provider = self._provider(provider, "provider_connection_invalid")
        try:
            supplied = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            target_id = attempt.target_connection_id
            current = self._connections.get(target_id) if target_id is not None else None
            if (
                attempt.status != "finalized"
                or target_id is None
                or current is None
                or supplied.id != target_id
                or supplied.tenant_id != tenant_id
                or supplied.workspace_id != workspace_id
                or supplied.auth_user_id != auth_user_id
                or supplied.provider is not checked_provider
                or supplied.environment != environment
                or current.tenant_id != tenant_id
                or current.workspace_id != workspace_id
                or current.auth_user_id != auth_user_id
                or current.provider is not checked_provider
                or current.environment != environment
                or current.provider_account_id != supplied.provider_account_id
                or current.authorization_method is not supplied.authorization_method
                or current.granted_permissions != supplied.granted_permissions
                or current.last_validated_at != supplied.last_validated_at
                or current.provider_revocation_required
                or not current.credential_envelope_ids
                or self._connection_oauth_generations.get(target_id) != attempt_id
            ):
                raise ProviderStoreError("provider_connection_conflict")
            if attempt.acknowledged_at is not None:
                if current.readiness is not ConnectionReadiness.READY:
                    raise ProviderStoreError("provider_connection_conflict")
                return current
            if (
                current.readiness is not ConnectionReadiness.REQUIRES_VALIDATION
                or attempt.target_revision is None
                or current.revision < attempt.target_revision
            ):
                raise ProviderStoreError("provider_connection_conflict")
            now = self._timestamp()
            ready = ProviderConnection.model_validate(
                {
                    **self._connection_values(current),
                    "readiness": ConnectionReadiness.READY,
                    "revision": current.revision + 1,
                    "updated_at": now,
                }
            )
            self._connections[target_id] = ready
            self._oauth_attempts[attempt_id] = replace(
                attempt,
                target_revision=ready.revision,
                acknowledged_at=now,
                updated_at=now,
            )
            return ready

    def fail_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> ProviderOAuthAttempt:
        checked_provider = self._provider(provider, "provider_connection_invalid")
        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            if attempt.status == "failed":
                return attempt
            if attempt.status == "revoked":
                raise ProviderStoreError("provider_connection_conflict")

            connections_before = dict(self._connections)
            envelopes_before = dict(self._envelopes)
            oauth_attempts_before = dict(self._oauth_attempts)
            oauth_envelopes_before = dict(self._oauth_attempt_envelopes)
            try:
                target_revision = attempt.target_revision
                retained_envelope_ids = attempt.credential_envelope_ids
                provider_account_id = attempt.provider_account_id
                account_display_name = attempt.account_display_name
                authorization_method = attempt.authorization_method
                if attempt.target_connection_id is not None:
                    target = self._connections.get(attempt.target_connection_id)
                    owns_generation = (
                        self._connection_oauth_generations.get(attempt.target_connection_id)
                        == attempt.id
                    )
                    if owns_generation:
                        if (
                            target is None
                            or target.tenant_id != tenant_id
                            or target.workspace_id != workspace_id
                            or target.auth_user_id != auth_user_id
                            or target.provider is not checked_provider
                            or target.environment != environment
                        ):
                            raise ProviderStoreError("provider_connection_conflict")
                        provider_account_id = target.provider_account_id
                        account_display_name = target.account_display_name
                        authorization_method = target.authorization_method
                        target_revision = target.revision
                        if target.readiness is not ConnectionReadiness.DISCONNECTED:
                            if (
                                target.provider_revocation_required
                                or not target.credential_envelope_ids
                            ):
                                raise ProviderStoreError("provider_connection_conflict")
                            try:
                                recovery_envelopes = tuple(
                                    self._envelopes[envelope_id]
                                    for envelope_id in target.credential_envelope_ids
                                )
                            except KeyError:
                                raise ProviderStoreError(
                                    "provider_credential_binding_invalid"
                                ) from None
                            if any(
                                envelope.id in self._oauth_attempt_envelopes
                                and self._oauth_attempt_envelopes[envelope.id] != envelope
                                for envelope in recovery_envelopes
                            ):
                                raise ProviderStoreError("provider_credential_binding_invalid")
                            for envelope in recovery_envelopes:
                                self._oauth_attempt_envelopes[envelope.id] = envelope
                            retained_envelope_ids = target.credential_envelope_ids
                            disconnected = self.disconnect(
                                tenant_id=tenant_id,
                                workspace_id=workspace_id,
                                auth_user_id=auth_user_id,
                                connection_id=attempt.target_connection_id,
                                provider_revocation_required=True,
                            )
                            target_revision = disconnected.revision
                failed = replace(
                    attempt,
                    status="failed",
                    provider_account_id=provider_account_id,
                    account_display_name=account_display_name,
                    authorization_method=authorization_method,
                    credential_envelope_ids=retained_envelope_ids,
                    target_revision=target_revision,
                    provider_revocation_required=True,
                    updated_at=self._timestamp(),
                )
                self._oauth_attempts[attempt_id] = failed
                return failed
            except BaseException:
                self._connections = connections_before
                self._envelopes = envelopes_before
                self._oauth_attempts = oauth_attempts_before
                self._oauth_attempt_envelopes = oauth_envelopes_before
                raise

    def complete_oauth_attempt_revocation(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> ProviderOAuthAttempt:
        checked_provider = self._provider(provider, "provider_connection_invalid")
        with self._lock:
            attempt = self._require_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
            )
            if attempt.status == "revoked":
                return attempt
            if attempt.status != "failed" or not attempt.provider_revocation_required:
                raise ProviderStoreError("provider_connection_conflict")
            connections_before = dict(self._connections)
            oauth_attempts_before = dict(self._oauth_attempts)
            oauth_envelopes_before = dict(self._oauth_attempt_envelopes)
            generations_before = dict(self._connection_oauth_generations)
            try:
                if (
                    attempt.target_connection_id is not None
                    and self._connection_oauth_generations.get(attempt.target_connection_id)
                    == attempt.id
                ):
                    self.complete_revocation(
                        tenant_id=tenant_id,
                        workspace_id=workspace_id,
                        auth_user_id=auth_user_id,
                        connection_id=attempt.target_connection_id,
                    )
                    self._connection_oauth_generations.pop(
                        attempt.target_connection_id,
                        None,
                    )
                for envelope_id in attempt.credential_envelope_ids:
                    self._oauth_attempt_envelopes.pop(envelope_id, None)
                completed = replace(
                    attempt,
                    status="revoked",
                    credential_envelope_ids=(),
                    provider_revocation_required=False,
                    updated_at=self._timestamp(),
                )
                self._oauth_attempts[attempt_id] = completed
                return completed
            except BaseException:
                self._connections = connections_before
                self._oauth_attempts = oauth_attempts_before
                self._oauth_attempt_envelopes = oauth_envelopes_before
                self._connection_oauth_generations = generations_before
                raise

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

    def stage_connection(
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or revision != 1
            or validated_at is not None
        ):
            raise ProviderStoreError("provider_connection_invalid")
        with self._lock:
            staged = self.save_connection(
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
            ).model_copy(update={"provider_revocation_required": True})
            self._connections[connection_id] = staged
            return staged

    def resolve_connection_target(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        company_or_merchant_id: str,
        proposed_connection_id: UUID,
    ) -> ProviderConnectionTarget:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(proposed_connection_id)
        now = self._timestamp()
        try:
            probe = ProviderConnection(
                id=proposed_connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=ProviderId(provider),
                environment=environment,
                provider_account_id=company_or_merchant_id,
                account_display_name="Provider account",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=(),
                readiness=ConnectionReadiness.DISCONNECTED,
                revision=1,
                credential_envelope_ids=(),
                provider_revocation_required=False,
                disconnected_at=now,
                created_at=now,
                updated_at=now,
            )
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None

        with self._lock:
            matching = [
                connection
                for connection in self._connections.values()
                if self._same_account(connection, probe)
            ]
            if len(matching) > 1:
                raise ProviderStoreError("provider_connection_conflict")
            if matching:
                current = matching[0]
                if (
                    current.readiness is not ConnectionReadiness.DISCONNECTED
                    or current.credential_envelope_ids
                    or current.provider_revocation_required
                ):
                    raise ProviderStoreError("provider_connection_conflict")
                return ProviderConnectionTarget(
                    connection_id=current.id,
                    revision=current.revision + 1,
                    reuses_existing=True,
                )
            if proposed_connection_id in self._connections:
                raise ProviderStoreError("provider_connection_conflict")
            return ProviderConnectionTarget(
                connection_id=proposed_connection_id,
                revision=1,
                reuses_existing=False,
            )

    def finalize_connection(
        self,
        *,
        staged_connection_id: UUID,
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
        if readiness != ConnectionReadiness.READY or staged_connection_id == connection_id:
            raise ProviderStoreError("provider_connection_invalid")
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(staged_connection_id)

        with self._lock:
            staged = self._connections.get(staged_connection_id)
            if (
                staged is None
                or staged.tenant_id != tenant_id
                or staged.workspace_id != workspace_id
                or staged.auth_user_id != auth_user_id
                or staged.provider
                is not self._provider(
                    provider,
                    "provider_connection_invalid",
                )
                or staged.environment != environment
                or staged.readiness is ConnectionReadiness.DISCONNECTED
                or not staged.provider_revocation_required
                or not staged.credential_envelope_ids
            ):
                raise ProviderStoreError("provider_connection_invalid")

            connections_before = dict(self._connections)
            envelopes_before = dict(self._envelopes)
            try:
                finalized = self.save_connection(
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
                self.disconnect(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=auth_user_id,
                    connection_id=staged_connection_id,
                    provider_revocation_required=True,
                )
                self.complete_revocation(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=auth_user_id,
                    connection_id=staged_connection_id,
                )
                return finalized
            except BaseException:
                self._connections = connections_before
                self._envelopes = envelopes_before
                raise

    def record_revocation_obligation(
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
    ) -> DisconnectResult:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(connection_id)
        now = self._timestamp()
        try:
            checked_provider = ProviderId(provider)
            checked_method = AuthorizationMethod(authorization_method)
            checked_permissions = tuple(sorted(granted_permissions))
            obligation = ProviderConnection(
                id=connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                provider_account_id=company_or_merchant_id,
                account_display_name=account_display_name,
                authorization_method=checked_method,
                granted_permissions=checked_permissions,
                readiness=ConnectionReadiness.DISCONNECTED,
                revision=1,
                credential_envelope_ids=(),
                provider_revocation_required=True,
                disconnected_at=now,
                created_at=now,
                updated_at=now,
            )
            if (
                isinstance(granted_permissions, (str, bytes, bytearray))
                or tuple(granted_permissions) != checked_permissions
            ):
                raise ValueError
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None

        with self._lock:
            current = self._connections.get(connection_id)
            if current is not None:
                if not self._same_binding(current, obligation):
                    raise ProviderStoreError("provider_connection_conflict")
                return self.disconnect(
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=auth_user_id,
                    connection_id=connection_id,
                    provider_revocation_required=True,
                )
            if any(
                self._same_account(existing, obligation) for existing in self._connections.values()
            ):
                raise ProviderStoreError("provider_connection_conflict")
            self._connections[connection_id] = obligation
            return DisconnectResult(
                connection_id=connection_id,
                deleted_envelope_count=0,
                already_disconnected=True,
                provider_revocation_required=True,
                revision=1,
            )

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
                and connection.provider_account_id != f"oauth-pending-{connection.id}"
                and self._connection_generation_is_dispatchable(connection)
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

    def load_connection(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
    ) -> ProviderConnection:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(connection_id)
        with self._lock:
            connection = self._connections.get(connection_id)
            if connection is None or (
                connection.tenant_id,
                connection.workspace_id,
                connection.auth_user_id,
            ) != (tenant_id, workspace_id, auth_user_id):
                raise ProviderStoreError("provider_connection_not_found")
            if (
                connection.provider_account_id == f"oauth-pending-{connection.id}"
                or not self._connection_generation_is_dispatchable(connection)
            ):
                raise ProviderStoreError("provider_connection_not_found")
            return connection

    def _connection_generation_is_dispatchable(
        self,
        connection: ProviderConnection,
    ) -> bool:
        generation_id = self._connection_oauth_generations.get(connection.id)
        if generation_id is None:
            return True
        attempt = self._oauth_attempts.get(generation_id) if generation_id is not None else None
        return bool(
            attempt is not None
            and attempt.status == "finalized"
            and attempt.target_connection_id == connection.id
            and attempt.acknowledged_at is not None
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
                # A completed remote revocation is terminal. A stale process that
                # loaded the former ready connection must not restore its obligation.
                required = current.provider_revocation_required

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
    def _permissions(value: Sequence[str]) -> tuple[str, ...]:
        if isinstance(value, (str, bytes, bytearray)):
            raise ProviderStoreError("provider_connection_invalid")
        try:
            supplied = tuple(value)
        except TypeError:
            raise ProviderStoreError("provider_connection_invalid") from None
        if (
            not supplied
            or any(not isinstance(item, str) for item in supplied)
            or tuple(sorted(supplied)) != supplied
            or len(supplied) != len(set(supplied))
        ):
            raise ProviderStoreError("provider_connection_invalid")
        return supplied

    def _require_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
    ) -> ProviderOAuthAttempt:
        self._bound_ids(tenant_id, workspace_id, auth_user_id)
        self._require_uuid(attempt_id)
        attempt = self._oauth_attempts.get(attempt_id)
        if (
            attempt is None
            or attempt.tenant_id != tenant_id
            or attempt.workspace_id != workspace_id
            or attempt.auth_user_id != auth_user_id
            or attempt.provider is not provider
            or attempt.environment != environment
        ):
            raise ProviderStoreError("provider_connection_not_found")
        return attempt

    @staticmethod
    def _same_oauth_attempt_binding(
        left: ProviderOAuthAttempt,
        right: ProviderOAuthAttempt,
    ) -> bool:
        return (
            left.id == right.id
            and left.tenant_id == right.tenant_id
            and left.workspace_id == right.workspace_id
            and left.auth_user_id == right.auth_user_id
            and left.provider is right.provider
            and left.environment == right.environment
            and left.granted_permissions == right.granted_permissions
        )

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

    def begin_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        granted_permissions: Sequence[str],
    ) -> ProviderOAuthAttempt:
        try:
            ProviderConnectionStore._bound_ids(
                tenant_id,
                workspace_id,
                auth_user_id,
            )
            ProviderConnectionStore._require_uuid(attempt_id)
            checked_provider = ProviderId(provider)
            permissions = ProviderConnectionStore._permissions(granted_permissions)
            row = self._rpc_one(
                "begin_mercury_provider_oauth_attempt",
                {
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                    "p_granted_permissions": list(permissions),
                },
            )
            result = self._oauth_attempt_result(
                row,
                attempt_id=attempt_id,
                expected_status="exchange_pending",
            )
            if result.target_connection_id is not None or not result.provider_revocation_required:
                raise ValueError
            created_at = _rpc_timestamp(row["created_at"])
            updated_at = _rpc_timestamp(row["updated_at"])
            return ProviderOAuthAttempt(
                id=attempt_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                granted_permissions=permissions,
                status=result.status,
                provider_account_id=None,
                account_display_name=None,
                authorization_method=None,
                credential_envelope_ids=(),
                material_revision=0,
                target_connection_id=None,
                target_revision=None,
                acknowledged_at=None,
                provider_revocation_required=True,
                created_at=created_at,
                updated_at=updated_at,
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def attach_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or revision != 1
            or validated_at is not None
            or attempt_id == UUID(int=0)
        ):
            raise ProviderStoreError("provider_connection_invalid")
        checked = self._validated_connection(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            connection_id=attempt_id,
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
            "attach_mercury_provider_oauth_attempt",
            {
                "p_attempt_id": str(attempt_id),
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
                "p_last_validated_at": None,
                "p_envelopes": [self._envelope_payload(envelope) for envelope in envelopes],
            },
        )
        result = self._oauth_attempt_result(
            row,
            attempt_id=attempt_id,
            expected_status="material_attached",
        )
        if result.target_connection_id is not None or not result.provider_revocation_required:
            raise ProviderStoreError("provider_connection_invalid")
        return checked.model_copy(
            update={
                "provider_revocation_required": True,
                "created_at": _rpc_timestamp(row["created_at"]),
                "updated_at": _rpc_timestamp(row["updated_at"]),
            }
        )

    def load_runtime_envelopes(
        self,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if (
            checked.provider is ProviderId.FLOWACCOUNT
            and checked.readiness is ConnectionReadiness.REQUIRES_VALIDATION
            and checked.provider_account_id == f"oauth-pending-{checked.id}"
        ):
            return self.load_oauth_attempt_envelopes(
                attempt_id=checked.id,
                tenant_id=checked.tenant_id,
                workspace_id=checked.workspace_id,
                auth_user_id=checked.auth_user_id,
                provider=checked.provider,
                environment=checked.environment,
                connection=checked,
            )
        return self.load_envelopes(checked)

    def replace_runtime_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
        except (TypeError, ValueError, ValidationError):
            raise ProviderStoreError("provider_connection_invalid") from None
        if checked.provider_account_id == f"oauth-pending-{checked.id}":
            return self.replace_oauth_attempt_envelopes(checked, envelopes)
        return self.replace_envelopes(checked, envelopes)

    def load_oauth_attempt_envelopes(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        connection: ProviderConnection,
    ) -> tuple[CredentialEnvelope, ...]:
        try:
            checked = ProviderConnection.model_validate(connection)
            checked_provider = ProviderId(provider)
            if (
                checked.tenant_id != tenant_id
                or checked.workspace_id != workspace_id
                or checked.auth_user_id != auth_user_id
                or checked.provider is not checked_provider
                or checked.environment != environment
            ):
                raise ValueError
            rows = self._rpc_rows(
                "load_mercury_provider_oauth_attempt_envelopes",
                {
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                },
            )
            envelopes = tuple(self._envelope_from_row(row) for row in rows)
            self._validate_envelopes(
                connection=checked,
                envelopes=envelopes,
            )
            return envelopes
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_credential_binding_invalid") from None

    def replace_oauth_attempt_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: tuple[CredentialEnvelope, ...],
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
            checked_envelopes = tuple(
                CredentialEnvelope.model_validate(envelope) for envelope in envelopes
            )
            if (
                checked.provider is not ProviderId.FLOWACCOUNT
                or checked.readiness is not ConnectionReadiness.REQUIRES_VALIDATION
                or checked.provider_account_id != f"oauth-pending-{checked.id}"
            ):
                raise ValueError
            self._validate_envelopes(
                connection=checked,
                envelopes=checked_envelopes,
            )
            payload = {
                "p_attempt_id": str(checked.id),
                "p_tenant_id": str(checked.tenant_id),
                "p_workspace_id": str(checked.workspace_id),
                "p_auth_user_id": str(checked.auth_user_id),
                "p_provider": checked.provider.value,
                "p_environment": checked.environment,
                "p_expected_revision": checked.revision,
                "p_envelopes": [self._envelope_payload(envelope) for envelope in checked_envelopes],
            }
            try:
                row = self._rpc_one(
                    "replace_mercury_provider_oauth_attempt_envelopes",
                    payload,
                )
            except ProviderStoreError as exc:
                if exc.code != "provider_store_unavailable":
                    raise
                row = self._rpc_one(
                    "replace_mercury_provider_oauth_attempt_envelopes",
                    payload,
                )
            envelope_ids = tuple(UUID(str(value)) for value in row["credential_envelope_ids"])
            material_revision = row["material_revision"]
            if (
                UUID(str(row["attempt_id"])) != checked.id
                or material_revision != checked.revision + 1
                or envelope_ids != tuple(envelope.id for envelope in checked_envelopes)
            ):
                raise ValueError
            return checked.model_copy(
                update={
                    "revision": material_revision,
                    "credential_envelope_ids": envelope_ids,
                    "created_at": _rpc_timestamp(row["created_at"]),
                    "updated_at": _rpc_timestamp(row["updated_at"]),
                }
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def finalize_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or attempt_id == connection_id
            or validated_at is None
        ):
            raise ProviderStoreError("provider_connection_invalid")
        return self._persist_connection(
            function="finalize_mercury_provider_oauth_attempt",
            expected_revocation_required=False,
            allow_generation_reconciliation=True,
            extra_payload={"p_attempt_id": str(attempt_id)},
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

    def acknowledge_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        connection: ProviderConnection,
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
            checked_provider = ProviderId(provider)
            if (
                checked.id == attempt_id
                or checked.tenant_id != tenant_id
                or checked.workspace_id != workspace_id
                or checked.auth_user_id != auth_user_id
                or checked.provider is not checked_provider
                or checked.environment != environment
            ):
                raise ValueError
            row = self._rpc_one(
                "acknowledge_mercury_provider_oauth_attempt",
                {
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                },
            )
            row_readiness = ConnectionReadiness(row["readiness"])
            row_revision = row["revision"]
            if (
                UUID(str(row["connection_id"])) != checked.id
                or ProviderId(row["provider"]) is not checked.provider
                or row["environment"] != checked.environment
                or row["account_display_name"] != checked.account_display_name
                or AuthorizationMethod(row["authorization_method"])
                is not checked.authorization_method
                or tuple(row["granted_permissions"]) != checked.granted_permissions
                or row_readiness is not ConnectionReadiness.READY
                or (
                    checked.readiness is ConnectionReadiness.REQUIRES_VALIDATION
                    and row_revision <= checked.revision
                )
                or (
                    checked.readiness is ConnectionReadiness.READY
                    and row_revision < checked.revision
                )
                or checked.readiness
                not in {
                    ConnectionReadiness.REQUIRES_VALIDATION,
                    ConnectionReadiness.READY,
                }
                or row["last_validated_at"] is None
                or bool(row["provider_revocation_required"])
            ):
                raise ValueError
            last_validated_at = _rpc_timestamp(row["last_validated_at"])
            if last_validated_at != checked.last_validated_at:
                raise ValueError
            return checked.model_copy(
                update={
                    "readiness": ConnectionReadiness.READY,
                    "revision": row_revision,
                    "last_validated_at": last_validated_at,
                    "provider_revocation_required": False,
                    "created_at": _rpc_timestamp(row["created_at"]),
                    "updated_at": _rpc_timestamp(row["updated_at"]),
                }
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def fail_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> ProviderOAuthAttemptResult:
        return self._oauth_attempt_transition(
            function="fail_mercury_provider_oauth_attempt",
            expected_status="failed",
            attempt_id=attempt_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            provider=provider,
            environment=environment,
        )

    def complete_oauth_attempt_revocation(
        self,
        *,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> ProviderOAuthAttemptResult:
        return self._oauth_attempt_transition(
            function="complete_mercury_provider_oauth_attempt_revocation",
            expected_status="revoked",
            attempt_id=attempt_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            provider=provider,
            environment=environment,
        )

    def _oauth_attempt_transition(
        self,
        *,
        function: str,
        expected_status: str,
        attempt_id: UUID,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> ProviderOAuthAttemptResult:
        try:
            checked_provider = ProviderId(provider)
            row = self._rpc_one(
                function,
                {
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                },
            )
            return self._oauth_attempt_result(
                row,
                attempt_id=attempt_id,
                expected_status=expected_status,
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

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
        return self._persist_connection(
            function="save_mercury_provider_connection",
            expected_revocation_required=False,
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

    def stage_connection(
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
        if (
            readiness != ConnectionReadiness.REQUIRES_VALIDATION
            or revision != 1
            or validated_at is not None
        ):
            raise ProviderStoreError("provider_connection_invalid")
        return self._persist_connection(
            function="stage_mercury_provider_connection",
            expected_revocation_required=True,
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

    def resolve_connection_target(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId | str,
        environment: str,
        company_or_merchant_id: str,
        proposed_connection_id: UUID,
    ) -> ProviderConnectionTarget:
        try:
            row = self._rpc_one(
                "resolve_mercury_provider_connection_target",
                {
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": ProviderId(provider).value,
                    "p_environment": environment,
                    "p_provider_account_id": company_or_merchant_id,
                    "p_proposed_connection_id": str(proposed_connection_id),
                },
            )
            target = ProviderConnectionTarget(
                connection_id=UUID(str(row["connection_id"])),
                revision=row["revision"],
                reuses_existing=row["reuses_existing"],
            )
            if not target.reuses_existing and target.connection_id != proposed_connection_id:
                raise ValueError
            return target
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def list_for_workspace(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
    ) -> tuple[ProviderConnectionSummary, ...]:
        try:
            ProviderConnectionStore._bound_ids(
                tenant_id,
                workspace_id,
                auth_user_id,
            )
            rows = self._rpc_rows(
                "list_mercury_provider_connections_backend",
                {
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                },
            )
            summaries: list[ProviderConnectionSummary] = []
            expected_keys = {
                "connection_id",
                "provider",
                "environment",
                "account_display_name",
                "authorization_method",
                "granted_permissions",
                "readiness",
                "revision",
                "last_validated_at",
                "provider_revocation_required",
            }
            for row in rows:
                if set(row) != expected_keys:
                    raise ValueError
                connection_id = UUID(str(row["connection_id"]))
                environment = row["environment"]
                account_display_name = row["account_display_name"]
                permissions = ProviderConnectionStore._permissions(row["granted_permissions"])
                revision = row["revision"]
                revocation_required = row["provider_revocation_required"]
                last_validated_at = (
                    _rpc_timestamp(row["last_validated_at"])
                    if row["last_validated_at"] is not None
                    else None
                )
                if (
                    connection_id.int == 0
                    or not isinstance(environment, str)
                    or _PROVIDER_IDENTIFIER.fullmatch(environment) is None
                    or len(environment) > 64
                    or not isinstance(account_display_name, str)
                    or not 1 <= len(account_display_name) <= 200
                    or any(
                        unicodedata.category(character) in {"Cc", "Cf"}
                        for character in account_display_name
                    )
                    or not isinstance(revision, int)
                    or isinstance(revision, bool)
                    or revision < 1
                    or not isinstance(revocation_required, bool)
                ):
                    raise ValueError
                summary = ProviderConnectionSummary(
                    connection_id=connection_id,
                    provider=row["provider"],
                    environment=environment,
                    account_display_name=account_display_name,
                    authorization_method=row["authorization_method"],
                    granted_permissions=permissions,
                    readiness=row["readiness"],
                    revision=revision,
                    last_validated_at=last_validated_at,
                    provider_revocation_required=revocation_required,
                )
                if (
                    summary.readiness is ConnectionReadiness.READY
                    and summary.last_validated_at is None
                ):
                    raise ValueError
                summaries.append(summary)
            return tuple(summaries)
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def load_connection(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        connection_id: UUID,
    ) -> ProviderConnection:
        try:
            ProviderConnectionStore._bound_ids(tenant_id, workspace_id, auth_user_id)
            ProviderConnectionStore._require_uuid(connection_id)
            row = self._rpc_one(
                "load_mercury_provider_connection_backend",
                {
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_connection_id": str(connection_id),
                },
            )
            expected_keys = {
                "connection_id",
                "provider",
                "environment",
                "provider_account_id",
                "account_display_name",
                "authorization_method",
                "granted_permissions",
                "readiness",
                "revision",
                "last_validated_at",
                "credential_envelope_ids",
                "provider_revocation_required",
                "disconnected_at",
                "created_at",
                "updated_at",
            }
            if set(row) != expected_keys:
                raise ValueError
            envelope_ids = tuple(UUID(str(item)) for item in row["credential_envelope_ids"])
            return ProviderConnection(
                id=UUID(str(row["connection_id"])),
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=row["provider"],
                environment=row["environment"],
                provider_account_id=row["provider_account_id"],
                account_display_name=row["account_display_name"],
                authorization_method=row["authorization_method"],
                granted_permissions=ProviderConnectionStore._permissions(
                    row["granted_permissions"]
                ),
                readiness=row["readiness"],
                revision=row["revision"],
                last_validated_at=(
                    _rpc_timestamp(row["last_validated_at"])
                    if row["last_validated_at"] is not None
                    else None
                ),
                credential_envelope_ids=envelope_ids,
                provider_revocation_required=row["provider_revocation_required"],
                disconnected_at=(
                    _rpc_timestamp(row["disconnected_at"])
                    if row["disconnected_at"] is not None
                    else None
                ),
                created_at=_rpc_timestamp(row["created_at"]),
                updated_at=_rpc_timestamp(row["updated_at"]),
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def finalize_connection(
        self,
        *,
        staged_connection_id: UUID,
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
        if readiness != ConnectionReadiness.READY or staged_connection_id == connection_id:
            raise ProviderStoreError("provider_connection_invalid")
        return self._persist_connection(
            function="finalize_mercury_provider_connection",
            expected_revocation_required=False,
            extra_payload={"p_staged_connection_id": str(staged_connection_id)},
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

    def record_revocation_obligation(
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
    ) -> DisconnectResult:
        try:
            if isinstance(granted_permissions, (str, bytes, bytearray)):
                raise ValueError
            checked_permissions = tuple(granted_permissions)
            if tuple(sorted(checked_permissions)) != checked_permissions or len(
                set(checked_permissions)
            ) != len(checked_permissions):
                raise ValueError
            payload = {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_connection_id": str(connection_id),
                "p_provider": ProviderId(provider).value,
                "p_environment": environment,
                "p_provider_account_id": company_or_merchant_id,
                "p_account_display_name": account_display_name,
                "p_authorization_method": AuthorizationMethod(authorization_method).value,
                "p_granted_permissions": list(checked_permissions),
            }
            return self._disconnect_result(
                self._rpc_one(
                    "record_mercury_provider_revocation_obligation",
                    payload,
                ),
                connection_id=connection_id,
            )
        except ProviderStoreError:
            raise
        except Exception:
            raise ProviderStoreError("provider_connection_invalid") from None

    def _persist_connection(
        self,
        *,
        function: str,
        expected_revocation_required: bool,
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
        extra_payload: Mapping[str, Any] | None = None,
        allow_generation_reconciliation: bool = False,
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
        payload = {
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
        }
        if extra_payload is not None:
            payload.update(extra_payload)
        row = self._rpc_one(
            function,
            payload,
        )
        try:
            row_readiness = ConnectionReadiness(row["readiness"])
            row_revision = row["revision"]
            response_matches_generation = (
                allow_generation_reconciliation
                and row_readiness
                in {
                    ConnectionReadiness.REQUIRES_VALIDATION,
                    ConnectionReadiness.READY,
                }
                and row_revision >= checked.revision
            )
            if (
                UUID(str(row["connection_id"])) != checked.id
                or ProviderId(row["provider"]) is not checked.provider
                or row["environment"] != checked.environment
                or row["account_display_name"] != checked.account_display_name
                or AuthorizationMethod(row["authorization_method"])
                is not checked.authorization_method
                or tuple(row["granted_permissions"]) != checked.granted_permissions
                or (not response_matches_generation and row_readiness is not checked.readiness)
                or (not response_matches_generation and row_revision != checked.revision)
                or bool(row["provider_revocation_required"]) is not expected_revocation_required
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
                    "readiness": row_readiness,
                    "revision": row_revision,
                    "created_at": created_at,
                    "updated_at": updated_at,
                    "provider_revocation_required": expected_revocation_required,
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
    def _oauth_attempt_result(
        row: Mapping[str, Any],
        *,
        attempt_id: UUID,
        expected_status: str,
    ) -> ProviderOAuthAttemptResult:
        try:
            target_value = row.get("target_connection_id")
            result = ProviderOAuthAttemptResult(
                attempt_id=UUID(str(row["attempt_id"])),
                status=row["status"],
                target_connection_id=(
                    UUID(str(target_value)) if target_value is not None else None
                ),
                target_revision=row.get("target_revision"),
                provider_revocation_required=row["provider_revocation_required"],
            )
            if result.attempt_id != attempt_id or result.status != expected_status:
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
    "ProviderConnectionTarget",
    "ProviderConnectionStore",
    "ProviderStoreError",
    "SupabaseProviderConnectionStore",
]
