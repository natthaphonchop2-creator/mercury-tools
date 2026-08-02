"""One-time browser handoff for PEAK MCP credentials."""

from __future__ import annotations

import base64
import hashlib
import inspect
import re
import secrets
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from html import escape
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from mercury_tools.auth.consent import OAUTH_SESSION_TTL_SECONDS, OAuthSessionCookie
from mercury_tools.auth.models import (
    MercuryAuthError,
    MercuryPrincipal,
    PrincipalResolver,
)
from mercury_tools.config import Settings, v1_supabase_rest_url
from mercury_tools.credentials.models import CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.manifest import (
    ProviderDriverManifest,
    load_provider_manifest,
    resolve_provider_resource,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderConnectionSummary,
    ProviderId,
    SetupAttempt,
)
from mercury_tools.providers.peak import (
    PeakCredentialMaterial,
    PeakProfile,
    QualifiedPeakProviderContract,
    seal_peak_credentials,
)
from mercury_tools.providers.store import (
    ProviderConnectionStore,
    ProviderConnectionTarget,
    ProviderStoreError,
)
from mercury_tools.workspaces.models import WorkspaceRole

PEAK_SETUP_PATH = "/auth/providers/peak/setup"
PEAK_SETUP_EXCHANGE_PATH = "/auth/providers/peak/setup/exchange"
PEAK_SETUP_BROWSER_COOKIE = "__Secure-mercury-peak-setup-session"
PEAK_SETUP_LIFETIME = timedelta(minutes=10)
PEAK_REVOCATION_INSTRUCTION = "Revoke this credential set in PEAK Account."
_PEAK_BROWSER_BINDING_PREFIX = "mercury-peak-setup-browser-v1"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_ERROR_CODES = frozenset(
    {
        "peak_provider_contract_unqualified",
        "peak_setup_configuration_invalid",
        "peak_setup_request_invalid",
        "peak_setup_state_invalid",
        "peak_setup_unavailable",
        "peak_setup_validation_failed",
    }
)
_SECRET_FIELDS = (
    "setup_session",
    "csrf_token",
    "user_token",
    "connect_id",
    "connect_key",
)


class _PeakSetupModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class PeakSetupError(RuntimeError):
    """Closed setup failure that never retains submitted material."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_CODES:
            raise ValueError("peak_setup_error_invalid")
        self.code = code
        super().__init__(code)


class PeakSetupStart(_PeakSetupModel):
    setup_url: str = Field(repr=False)
    provider: Literal[ProviderId.PEAK] = ProviderId.PEAK
    environment: str
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class PeakSetupExchangeRequest(_PeakSetupModel):
    setup_token: SecretStr

    @field_validator("setup_token")
    @classmethod
    def validate_setup_token(cls, value: SecretStr) -> SecretStr:
        _raw_token(value.get_secret_value())
        return value


class PeakSetupExchange(_PeakSetupModel):
    session_id: UUID = Field(repr=False, exclude=True)
    setup_session: SecretStr
    csrf_token: SecretStr
    expires_at: datetime

    @field_validator("setup_session", "csrf_token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        _raw_token(value.get_secret_value())
        return value

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return _aware_utc(value)


class PeakSetupSubmission(_PeakSetupModel):
    setup_session: SecretStr
    csrf_token: SecretStr
    user_token: SecretStr
    connect_id: SecretStr
    connect_key: SecretStr

    @field_validator("setup_session", "csrf_token")
    @classmethod
    def validate_handoff_token(cls, value: SecretStr) -> SecretStr:
        _raw_token(value.get_secret_value())
        return value

    @field_validator("user_token", "connect_id", "connect_key")
    @classmethod
    def validate_provider_secret(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if (
            not 1 <= len(raw) <= 8192
            or raw != raw.strip()
            or any(character.isspace() for character in raw)
        ):
            raise ValueError("peak_setup_request_invalid")
        return value


class PeakDisconnectOutcome(_PeakSetupModel):
    status: Literal["provider_revocation_required"] = "provider_revocation_required"
    local_credentials_deleted: Literal[True] = True
    instruction: Literal["Revoke this credential set in PEAK Account."] = (
        PEAK_REVOCATION_INSTRUCTION
    )


@dataclass(frozen=True, repr=False)
class PeakSetupSessionRecord:
    id: UUID
    setup_attempt_id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str
    session_hash: str
    csrf_hash: str
    expires_at: datetime
    consumed_at: datetime | None
    created_at: datetime


@dataclass(frozen=True, repr=False)
class PeakBrowserSessionBinding:
    subject: UUID
    session_id: UUID
    session_hash: str
    expires_at: datetime


class PeakBrowserSessionManager:
    """Authenticate and bind the first-party PEAK browser handoff."""

    def __init__(
        self,
        *,
        encoded_key: str,
        principal_resolver: PrincipalResolver,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(getattr(principal_resolver, "resolve", None)):
            raise ValueError("peak_browser_session_configuration_invalid")
        self._cookie = OAuthSessionCookie(encoded_key)
        self._principal_resolver = principal_resolver
        self._clock = clock or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "PeakBrowserSessionManager()"

    async def authenticate(
        self,
        sealed_session: str,
    ) -> tuple[MercuryPrincipal, str]:
        principal: MercuryPrincipal | None = None
        bearer_token: str | None = None
        error_code: str | None = None
        try:
            bearer_token = self._cookie.open(sealed_session)
            principal = MercuryPrincipal.model_validate(
                await self._principal_resolver.resolve(bearer_token)
            )
        except MercuryAuthError as caught:
            error_code = caught.code
            del caught
        except Exception:
            error_code = "mercury_token_invalid"
        if error_code is not None or principal is None or bearer_token is None:
            if bearer_token is not None:
                del bearer_token
            del sealed_session
            del self
            raise MercuryAuthError(error_code or "mercury_token_invalid")
        return principal, bearer_token

    async def authenticate_bound(
        self,
        sealed_session: str,
        sealed_binding: str,
    ) -> tuple[MercuryPrincipal, str, PeakBrowserSessionBinding]:
        principal, bearer_token = await self.authenticate(sealed_session)
        binding: PeakBrowserSessionBinding | None = None
        failed = False
        try:
            raw_binding = self._cookie.open(sealed_binding)
            binding = self._parse_binding(raw_binding)
            if binding.subject != principal.subject:
                raise ValueError
        except Exception:
            failed = True
        if failed or binding is None:
            if "raw_binding" in locals():
                del raw_binding
            del bearer_token
            del principal
            del sealed_session
            del sealed_binding
            del self
            raise MercuryAuthError("mercury_token_invalid")
        return principal, bearer_token, binding

    def seal_binding(
        self,
        principal: MercuryPrincipal,
        exchange: PeakSetupExchange,
    ) -> tuple[str, int]:
        checked_principal = MercuryPrincipal.model_validate(principal)
        checked_exchange = PeakSetupExchange.model_validate(exchange)
        expires_at = _aware_utc(checked_exchange.expires_at)
        remaining = int((expires_at - _aware_utc(self._clock())).total_seconds())
        if remaining <= 0:
            raise PeakSetupError("peak_setup_state_invalid")
        session_hash = _token_hash(checked_exchange.setup_session.get_secret_value())
        payload = SecretStr(
            "|".join(
                (
                    _PEAK_BROWSER_BINDING_PREFIX,
                    str(checked_principal.subject),
                    str(checked_exchange.session_id),
                    session_hash,
                    str(int(expires_at.timestamp())),
                )
            )
        )
        sealed = self._cookie.seal(payload)
        return sealed, min(remaining, OAUTH_SESSION_TTL_SECONDS)

    def matches(
        self,
        binding: PeakBrowserSessionBinding,
        setup_session: SecretStr,
    ) -> bool:
        try:
            checked = PeakBrowserSessionBinding(
                subject=binding.subject,
                session_id=binding.session_id,
                session_hash=binding.session_hash,
                expires_at=_aware_utc(binding.expires_at),
            )
            submitted_hash = _token_hash(setup_session.get_secret_value())
            return bool(
                checked.expires_at > _aware_utc(self._clock())
                and secrets.compare_digest(checked.session_hash, submitted_hash)
            )
        except Exception:
            return False

    def _parse_binding(self, raw: str) -> PeakBrowserSessionBinding:
        prefix, subject, session_id, session_hash, expires_at = raw.split("|")
        if prefix != _PEAK_BROWSER_BINDING_PREFIX or _HASH_RE.fullmatch(session_hash) is None:
            raise ValueError
        binding = PeakBrowserSessionBinding(
            subject=UUID(subject),
            session_id=UUID(session_id),
            session_hash=session_hash,
            expires_at=datetime.fromtimestamp(int(expires_at), tz=UTC),
        )
        if (
            binding.subject.int == 0
            or binding.session_id.int == 0
            or binding.expires_at <= _aware_utc(self._clock())
        ):
            raise ValueError
        return binding


class PeakSetupProfileValidator(Protocol):
    async def validate_setup(
        self,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> PeakProfile: ...


class PeakSetupStore(Protocol):
    def create_attempt(self, **values: Any) -> SetupAttempt: ...

    def exchange_attempt(self, **values: Any) -> PeakSetupSessionRecord: ...

    def peek_session(self, **values: Any) -> PeakSetupSessionRecord: ...

    def finalize(self, **values: Any) -> ProviderConnection: ...


class InMemoryPeakSetupStore:
    """Reference implementation mirroring the atomic PostgreSQL finalizer."""

    def __init__(
        self,
        *,
        connection_store: ProviderConnectionStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(connection_store, ProviderConnectionStore):
            raise TypeError("peak_setup_connection_store_required")
        self._connection_store = connection_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sessions: dict[UUID, PeakSetupSessionRecord] = {}
        self._lock = threading.RLock()

    def __repr__(self) -> str:
        return "InMemoryPeakSetupStore()"

    @property
    def attempts(self) -> Mapping[UUID, SetupAttempt]:
        with self._connection_store._lock:
            return MappingProxyType(dict(self._connection_store._attempts))

    @property
    def sessions(self) -> Mapping[UUID, PeakSetupSessionRecord]:
        with self._lock:
            return MappingProxyType(dict(self._sessions))

    def create_attempt(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        token_hash: str,
        expires_at: datetime,
        mercury_access_token: str,
        attempt_id: UUID,
    ) -> SetupAttempt:
        del mercury_access_token
        created = self._connection_store.create_attempt(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            provider=provider,
            environment=environment,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        if created.id == attempt_id:
            return created
        replacement = created.model_copy(update={"id": attempt_id})
        with self._connection_store._lock:
            self._connection_store._attempts.pop(created.id)
            self._connection_store._attempts[attempt_id] = replacement
        return replacement

    def exchange_attempt(
        self,
        *,
        session_id: UUID,
        auth_user_id: UUID,
        token_hash: str,
        session_hash: str,
        csrf_hash: str,
        mercury_access_token: str,
    ) -> PeakSetupSessionRecord:
        del mercury_access_token
        now = self._timestamp()
        if any(
            not isinstance(value, str) or _HASH_RE.fullmatch(value) is None
            for value in (token_hash, session_hash, csrf_hash)
        ):
            raise PeakSetupError("peak_setup_state_invalid")
        with self._lock, self._connection_store._lock:
            attempt = next(
                (
                    item
                    for item in self._connection_store._attempts.values()
                    if item.auth_user_id == auth_user_id
                    and item.provider is ProviderId.PEAK
                    and secrets.compare_digest(item.token_hash, token_hash)
                ),
                None,
            )
            if (
                attempt is None
                or attempt.consumed_at is not None
                or attempt.expires_at <= now
                or any(
                    session.setup_attempt_id == attempt.id for session in self._sessions.values()
                )
                or any(
                    secrets.compare_digest(session.session_hash, session_hash)
                    or secrets.compare_digest(session.csrf_hash, csrf_hash)
                    for session in self._sessions.values()
                )
            ):
                raise PeakSetupError("peak_setup_state_invalid")
            record = PeakSetupSessionRecord(
                id=session_id,
                setup_attempt_id=attempt.id,
                tenant_id=attempt.tenant_id,
                workspace_id=attempt.workspace_id,
                auth_user_id=attempt.auth_user_id,
                provider=attempt.provider,
                environment=attempt.environment,
                session_hash=session_hash,
                csrf_hash=csrf_hash,
                expires_at=attempt.expires_at,
                consumed_at=None,
                created_at=now,
            )
            self._sessions[session_id] = record
            return record

    def peek_session(
        self,
        *,
        auth_user_id: UUID,
        session_hash: str,
    ) -> PeakSetupSessionRecord:
        now = self._timestamp()
        with self._lock:
            record = next(
                (
                    item
                    for item in self._sessions.values()
                    if item.auth_user_id == auth_user_id
                    and secrets.compare_digest(item.session_hash, session_hash)
                ),
                None,
            )
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                raise PeakSetupError("peak_setup_state_invalid")
            return record

    def finalize(
        self,
        *,
        session: PeakSetupSessionRecord,
        session_hash: str,
        csrf_hash: str,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> ProviderConnection:
        now = self._timestamp()
        with self._lock, self._connection_store._lock:
            current = self._sessions.get(session.id)
            attempt = self._connection_store._attempts.get(session.setup_attempt_id)
            if (
                current is None
                or current != session
                or current.consumed_at is not None
                or current.expires_at <= now
                or not secrets.compare_digest(current.session_hash, session_hash)
                or not secrets.compare_digest(current.csrf_hash, csrf_hash)
                or attempt is None
                or attempt.consumed_at is not None
                or attempt.expires_at <= now
                or not _same_setup_binding(current, attempt, connection)
            ):
                raise PeakSetupError("peak_setup_state_invalid")

            connections_before = dict(self._connection_store._connections)
            envelopes_before = dict(self._connection_store._envelopes)
            try:
                finalized = self._connection_store.save_connection(
                    tenant_id=connection.tenant_id,
                    workspace_id=connection.workspace_id,
                    auth_user_id=connection.auth_user_id,
                    connection_id=connection.id,
                    provider=connection.provider,
                    environment=connection.environment,
                    company_or_merchant_id=connection.provider_account_id,
                    account_display_name=connection.account_display_name,
                    authorization_method=connection.authorization_method,
                    granted_permissions=connection.granted_permissions,
                    readiness=connection.readiness,
                    revision=connection.revision,
                    validated_at=connection.last_validated_at,
                    envelopes=envelopes,
                )
                consumed_attempt = SetupAttempt.model_validate(
                    {
                        **ProviderConnectionStore._attempt_values(attempt),
                        "consumed_at": now,
                    }
                )
                self._connection_store._attempts[attempt.id] = consumed_attempt
                self._sessions[current.id] = replace(current, consumed_at=now)
                return finalized
            except PeakSetupError:
                raise
            except Exception:
                self._connection_store._connections = connections_before
                self._connection_store._envelopes = envelopes_before
                raise PeakSetupError("peak_setup_state_invalid") from None

    def _timestamp(self) -> datetime:
        try:
            return _aware_utc(self._clock())
        except Exception:
            raise PeakSetupError("peak_setup_unavailable") from None


class SupabasePeakSetupStore:
    """Durable adapter over the narrow PEAK exchange/finalizer RPCs."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
    ) -> None:
        try:
            self._base_url = v1_supabase_rest_url(
                project_url=settings.supabase_url,
                auth_issuer=settings.supabase_auth_issuer,
            )
            if (
                not settings.supabase_publishable_key
                or not settings.supabase_service_role_key
                or not isinstance(http_client, httpx.AsyncClient)
            ):
                raise ValueError
        except Exception:
            raise PeakSetupError("peak_setup_configuration_invalid") from None
        self._publishable_key = settings.supabase_publishable_key
        self._service_role_key = settings.supabase_service_role_key
        self._http = http_client

    def __repr__(self) -> str:
        return "SupabasePeakSetupStore()"

    async def create_attempt(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        token_hash: str,
        expires_at: datetime,
        mercury_access_token: str,
        attempt_id: UUID,
    ) -> SetupAttempt:
        row: dict[str, Any] | None = None
        attempt: SetupAttempt | None = None
        failed = False
        try:
            row = await self._rpc_one(
                "create_mercury_provider_setup_attempt",
                payload={
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": provider.value,
                    "p_environment": environment,
                    "p_token_hash": token_hash,
                    "p_expires_at": expires_at.isoformat(),
                },
                bearer_token=mercury_access_token,
            )
            expected_keys = {
                "attempt_id",
                "tenant_id",
                "workspace_id",
                "auth_user_id",
                "provider",
                "environment",
                "expires_at",
                "consumed_at",
                "created_at",
            }
            if set(row) != expected_keys or row["consumed_at"] is not None:
                raise ValueError
            attempt = SetupAttempt(
                id=UUID(str(row["attempt_id"])),
                tenant_id=UUID(str(row["tenant_id"])),
                workspace_id=UUID(str(row["workspace_id"])),
                auth_user_id=UUID(str(row["auth_user_id"])),
                provider=row["provider"],
                environment=row["environment"],
                token_hash=token_hash,
                expires_at=_rpc_timestamp(row["expires_at"]),
                consumed_at=None,
                created_at=_rpc_timestamp(row["created_at"]),
            )
            if (
                attempt.id != attempt_id
                or attempt.tenant_id != tenant_id
                or attempt.workspace_id != workspace_id
                or attempt.auth_user_id != auth_user_id
                or attempt.provider is not provider
                or attempt.environment != environment
                or attempt.expires_at != _aware_utc(expires_at)
            ):
                raise ValueError
        except Exception:
            failed = True
        if failed or attempt is None:
            if row is not None:
                del row
            del mercury_access_token
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return attempt

    async def exchange_attempt(
        self,
        *,
        session_id: UUID,
        auth_user_id: UUID,
        token_hash: str,
        session_hash: str,
        csrf_hash: str,
        mercury_access_token: str,
    ) -> PeakSetupSessionRecord:
        row: dict[str, Any] | None = None
        session: PeakSetupSessionRecord | None = None
        failed = False
        try:
            row = await self._rpc_one(
                "exchange_mercury_peak_setup_attempt",
                payload={
                    "p_session_id": str(session_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_token_hash": token_hash,
                    "p_session_hash": session_hash,
                    "p_csrf_hash": csrf_hash,
                },
                bearer_token=mercury_access_token,
            )
            session = _session_from_row(
                row,
                session_hash=session_hash,
                csrf_hash=csrf_hash,
            )
            if session.id != session_id or session.auth_user_id != auth_user_id:
                raise ValueError
        except Exception:
            failed = True
        if failed or session is None:
            if row is not None:
                del row
            del mercury_access_token
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return session

    async def peek_session(
        self,
        *,
        auth_user_id: UUID,
        session_hash: str,
    ) -> PeakSetupSessionRecord:
        row: dict[str, Any] | None = None
        session: PeakSetupSessionRecord | None = None
        failed = False
        try:
            row = await self._rpc_one(
                "peek_mercury_peak_setup_session",
                payload={
                    "p_auth_user_id": str(auth_user_id),
                    "p_session_hash": session_hash,
                },
            )
            session = _session_from_row(row, session_hash=session_hash)
            if session.auth_user_id != auth_user_id:
                raise ValueError
        except Exception:
            failed = True
        if failed or session is None:
            if row is not None:
                del row
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return session

    async def finalize(
        self,
        *,
        session: PeakSetupSessionRecord,
        session_hash: str,
        csrf_hash: str,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> ProviderConnection:
        checked_connection: ProviderConnection | None = None
        checked_envelopes: tuple[CredentialEnvelope, ...] | None = None
        finalized: ProviderConnection | None = None
        row: dict[str, Any] | None = None
        failed = False
        try:
            checked_connection = ProviderConnection.model_validate(connection)
            checked_envelopes = tuple(
                CredentialEnvelope.model_validate(envelope) for envelope in envelopes
            )
            if (
                session.tenant_id != checked_connection.tenant_id
                or session.workspace_id != checked_connection.workspace_id
                or session.auth_user_id != checked_connection.auth_user_id
                or session.provider is not ProviderId.PEAK
                or checked_connection.provider is not ProviderId.PEAK
                or session.environment != checked_connection.environment
                or checked_connection.authorization_method
                is not AuthorizationMethod.PROVIDER_CREDENTIALS
                or checked_connection.granted_permissions != ("profile.read",)
                or checked_connection.readiness is not ConnectionReadiness.READY
                or _HASH_RE.fullmatch(session_hash) is None
                or _HASH_RE.fullmatch(csrf_hash) is None
                or len(checked_envelopes) != 3
                or {item.credential_type for item in checked_envelopes}
                != {"user_token", "connect_id", "connect_key"}
            ):
                raise ValueError
            row = await self._rpc_one(
                "finalize_mercury_peak_setup",
                payload={
                    "p_tenant_id": str(checked_connection.tenant_id),
                    "p_workspace_id": str(checked_connection.workspace_id),
                    "p_auth_user_id": str(checked_connection.auth_user_id),
                    "p_session_hash": session_hash,
                    "p_csrf_hash": csrf_hash,
                    "p_connection_id": str(checked_connection.id),
                    "p_provider": checked_connection.provider.value,
                    "p_environment": checked_connection.environment,
                    "p_provider_account_id": checked_connection.provider_account_id,
                    "p_account_display_name": checked_connection.account_display_name,
                    "p_granted_permissions": list(checked_connection.granted_permissions),
                    "p_revision": checked_connection.revision,
                    "p_last_validated_at": checked_connection.last_validated_at.isoformat()
                    if checked_connection.last_validated_at is not None
                    else None,
                    "p_envelopes": [envelope.storage_record() for envelope in checked_envelopes],
                },
            )
            if set(row) != {
                "connection_id",
                "revision",
                "created_at",
                "updated_at",
            }:
                raise ValueError
            finalized = ProviderConnection.model_validate(
                checked_connection.model_copy(
                    update={
                        "revision": row["revision"],
                        "created_at": _rpc_timestamp(row["created_at"]),
                        "updated_at": _rpc_timestamp(row["updated_at"]),
                    }
                )
            )
            if (
                UUID(str(row["connection_id"])) != checked_connection.id
                or finalized.revision != checked_connection.revision
            ):
                raise ValueError
        except Exception:
            failed = True
        if failed or finalized is None:
            if row is not None:
                del row
            if checked_envelopes is not None:
                del checked_envelopes
            del envelopes
            del connection
            del session
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return finalized

    async def _rpc_one(
        self,
        function: str,
        *,
        payload: Mapping[str, Any],
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        token = bearer_token or self._service_role_key
        headers = {
            "apikey": (
                self._publishable_key if bearer_token is not None else self._service_role_key
            ),
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        row: dict[str, Any] | None = None
        failed = False
        try:
            response = await self._http.post(
                f"{self._base_url}/rpc/{function}",
                json=dict(payload),
                headers=headers,
                follow_redirects=False,
                timeout=20,
            )
            if response.status_code < 200 or response.status_code >= 300:
                raise ValueError
            rows = response.json()
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError
            row = rows[0]
        except Exception:
            failed = True
        if failed or row is None:
            for name in tuple(headers):
                headers[name] = ""
            headers.clear()
            if "response" in locals():
                del response
            if "rows" in locals():
                del rows
            del token
            del bearer_token
            del payload
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return row


class PeakSetupService:
    """Create, exchange, validate, and atomically finalize one PEAK setup."""

    def __init__(
        self,
        *,
        settings: Settings,
        workspace_service: Any,
        mercury_access_token: Callable[[MercuryPrincipal], str],
        setup_store: PeakSetupStore,
        connection_store: Any,
        vault: CredentialVault,
        contract: QualifiedPeakProviderContract | None,
        profile_validator: PeakSetupProfileValidator,
        manifest: ProviderDriverManifest | None = None,
        clock: Callable[[], datetime] | None = None,
        random_bytes: Callable[[int], bytes] | None = None,
        uuid_factory: Callable[[], UUID] | None = None,
    ) -> None:
        selected_manifest = manifest or load_provider_manifest(
            Path(__file__).resolve().parents[3] / "catalog/global/peak/driver.json"
        )
        if (
            selected_manifest.provider is not ProviderId.PEAK
            or not callable(mercury_access_token)
            or not callable(getattr(workspace_service, "require_workspace", None))
            or not callable(getattr(setup_store, "create_attempt", None))
            or not callable(getattr(setup_store, "exchange_attempt", None))
            or not callable(getattr(setup_store, "peek_session", None))
            or not callable(getattr(setup_store, "finalize", None))
            or not callable(getattr(connection_store, "resolve_connection_target", None))
            or not callable(getattr(connection_store, "list_for_workspace", None))
            or not callable(getattr(connection_store, "disconnect", None))
            or not isinstance(vault, CredentialVault)
            or (contract is not None and not isinstance(contract, QualifiedPeakProviderContract))
            or not callable(getattr(profile_validator, "validate_setup", None))
        ):
            raise PeakSetupError("peak_setup_configuration_invalid")
        _browser_origin(settings.provider_callback_base_url)
        self._settings = settings
        self._workspace_service = workspace_service
        self._mercury_access_token = mercury_access_token
        self._setup_store = setup_store
        self._connection_store = connection_store
        self._vault = vault
        self._contract = contract
        self._profile_validator = profile_validator
        self._manifest = selected_manifest
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random_bytes = random_bytes or secrets.token_bytes
        self._uuid_factory = uuid_factory or uuid4

    def __repr__(self) -> str:
        return (
            "PeakSetupService("
            f"contract_qualified={isinstance(self._contract, QualifiedPeakProviderContract)!r}"
            ")"
        )

    async def start(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        provider: ProviderId | str,
        environment: str,
    ) -> PeakSetupStart:
        result: PeakSetupStart | None = None
        error_code: str | None = None
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            if ProviderId(provider) is not ProviderId.PEAK:
                raise ValueError
            resource = resolve_provider_resource(
                settings=self._settings,
                manifest=self._manifest,
                environment=environment,
            )
            if resource.provider is not ProviderId.PEAK:
                raise ValueError
            access_token = await _await_value(self._mercury_access_token(checked_principal))
            membership = await _await_value(
                self._workspace_service.require_workspace(
                    checked_principal,
                    access_token,
                    workspace_id,
                    WorkspaceRole.MEMBER,
                )
            )
            now = self._timestamp()
            expires_at = now + PEAK_SETUP_LIFETIME
            token = self._random_token()
            attempt_id = self._checked_uuid(self._uuid_factory())
            await _await_value(
                self._setup_store.create_attempt(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                    provider=ProviderId.PEAK,
                    environment=environment,
                    token_hash=_token_hash(token),
                    expires_at=expires_at,
                    mercury_access_token=access_token,
                    attempt_id=attempt_id,
                )
            )
            result = PeakSetupStart(
                setup_url=(
                    f"{self._settings.provider_callback_base_url.rstrip('/')}"
                    f"{PEAK_SETUP_PATH}#{token}"
                ),
                environment=environment,
                expires_at=expires_at,
            )
        except PeakSetupError as caught:
            error_code = caught.code
            del caught
        except Exception:
            error_code = "peak_setup_request_invalid"
        if error_code is not None or result is None:
            if "access_token" in locals():
                del access_token
            if "token" in locals():
                del token
            if "checked_principal" in locals():
                del checked_principal
            if "membership" in locals():
                del membership
            del principal
            del self
            raise PeakSetupError(error_code or "peak_setup_unavailable")
        return result

    async def exchange(
        self,
        principal: MercuryPrincipal,
        setup_token: str,
    ) -> PeakSetupExchange:
        result: PeakSetupExchange | None = None
        error_code: str | None = None
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            checked_token = _raw_token(setup_token)
            access_token = await _await_value(self._mercury_access_token(checked_principal))
            setup_session = self._random_token()
            csrf_token = self._random_token()
            session_id = self._checked_uuid(self._uuid_factory())
            record = await _await_value(
                self._setup_store.exchange_attempt(
                    session_id=session_id,
                    auth_user_id=checked_principal.subject,
                    token_hash=_token_hash(checked_token),
                    session_hash=_token_hash(setup_session),
                    csrf_hash=_token_hash(csrf_token),
                    mercury_access_token=access_token,
                )
            )
            membership = await _await_value(
                self._workspace_service.require_workspace(
                    checked_principal,
                    access_token,
                    record.workspace_id,
                    WorkspaceRole.MEMBER,
                )
            )
            if membership.tenant_id != record.tenant_id or record.provider is not ProviderId.PEAK:
                raise ValueError
            result = PeakSetupExchange(
                session_id=record.id,
                setup_session=SecretStr(setup_session),
                csrf_token=SecretStr(csrf_token),
                expires_at=record.expires_at,
            )
        except PeakSetupError as caught:
            error_code = caught.code
            del caught
        except Exception:
            error_code = "peak_setup_state_invalid"
        if error_code is not None or result is None:
            if "access_token" in locals():
                del access_token
            if "checked_token" in locals():
                del checked_token
            if "setup_session" in locals():
                del setup_session
            if "csrf_token" in locals():
                del csrf_token
            if "checked_principal" in locals():
                del checked_principal
            del setup_token
            del principal
            del self
            raise PeakSetupError(error_code or "peak_setup_unavailable")
        return result

    async def complete(
        self,
        principal: MercuryPrincipal,
        submission: PeakSetupSubmission,
    ) -> ProviderConnectionSummary:
        summary, error_code = await self._complete(principal, submission)
        if error_code is not None or summary is None:
            del submission
            del principal
            del self
            raise PeakSetupError(error_code or "peak_setup_unavailable")
        return summary

    async def _complete(
        self,
        principal: MercuryPrincipal,
        submission: PeakSetupSubmission,
    ) -> tuple[ProviderConnectionSummary | None, str | None]:
        material: PeakCredentialMaterial | None = None
        summary: ProviderConnectionSummary | None = None
        error_code: str | None = None
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            checked = PeakSetupSubmission.model_validate(submission)
            session_token = checked.setup_session.get_secret_value()
            csrf_token = checked.csrf_token.get_secret_value()
            session_hash = _token_hash(session_token)
            csrf_hash = _token_hash(csrf_token)
            session = await _await_value(
                self._setup_store.peek_session(
                    auth_user_id=checked_principal.subject,
                    session_hash=session_hash,
                )
            )
            access_token = await _await_value(self._mercury_access_token(checked_principal))
            membership = await _await_value(
                self._workspace_service.require_workspace(
                    checked_principal,
                    access_token,
                    session.workspace_id,
                    WorkspaceRole.MEMBER,
                )
            )
            if membership.tenant_id != session.tenant_id:
                raise PeakSetupError("peak_setup_state_invalid")
            if self._contract is None:
                raise PeakSetupError("peak_provider_contract_unqualified")

            material = PeakCredentialMaterial.from_values(
                user_token=checked.user_token.get_secret_value(),
                connect_id=checked.connect_id.get_secret_value(),
                connect_key=checked.connect_key.get_secret_value(),
            )
            provisional_id = self._checked_uuid(self._uuid_factory())
            provisional = self._connection(
                session=session,
                connection_id=provisional_id,
                merchant_id=f"peak-setup-{session.id}",
                display_name="PEAK",
                revision=1,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                validated_at=None,
                envelope_ids=(uuid4(), uuid4(), uuid4()),
            )
            provisional_envelopes = seal_peak_credentials(
                vault=self._vault,
                connection=provisional,
                credentials=material,
            )
            provisional = self._connection(
                session=session,
                connection_id=provisional_id,
                merchant_id=provisional.provider_account_id,
                display_name=provisional.account_display_name,
                revision=1,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                validated_at=None,
                envelope_ids=tuple(item.id for item in provisional_envelopes),
            )
            profile = await self._validated_profile(
                provisional,
                provisional_envelopes,
            )
            if profile is None:
                return None, "peak_setup_validation_failed"

            target = await _await_value(
                self._connection_store.resolve_connection_target(
                    tenant_id=session.tenant_id,
                    workspace_id=session.workspace_id,
                    auth_user_id=session.auth_user_id,
                    provider=ProviderId.PEAK,
                    environment=session.environment,
                    company_or_merchant_id=profile.merchant_id,
                    proposed_connection_id=self._checked_uuid(self._uuid_factory()),
                )
            )
            if not isinstance(target, ProviderConnectionTarget):
                raise PeakSetupError("peak_setup_state_invalid")
            validated_at = self._timestamp()
            exact = self._connection(
                session=session,
                connection_id=target.connection_id,
                merchant_id=profile.merchant_id,
                display_name=profile.merchant_display_name,
                revision=target.revision,
                readiness=ConnectionReadiness.READY,
                validated_at=validated_at,
                envelope_ids=(uuid4(), uuid4(), uuid4()),
            )
            envelopes = seal_peak_credentials(
                vault=self._vault,
                connection=exact,
                credentials=material,
            )
            exact = self._connection(
                session=session,
                connection_id=target.connection_id,
                merchant_id=profile.merchant_id,
                display_name=profile.merchant_display_name,
                revision=target.revision,
                readiness=ConnectionReadiness.READY,
                validated_at=validated_at,
                envelope_ids=tuple(item.id for item in envelopes),
            )
            finalized = await _await_value(
                self._setup_store.finalize(
                    session=session,
                    session_hash=session_hash,
                    csrf_hash=csrf_hash,
                    connection=exact,
                    envelopes=envelopes,
                )
            )
            summary = ProviderConnection.model_validate(finalized).summary()
        except PeakSetupError as caught:
            error_code = caught.code
            del caught
        except (
            PermissionError,
            ProviderStoreError,
            TypeError,
            ValueError,
            ValidationError,
        ):
            error_code = "peak_setup_state_invalid"
        except Exception:
            error_code = "peak_setup_unavailable"
        finally:
            if material is not None:
                material.clear()
            if "checked" in locals():
                del checked
            del submission
        return summary, error_code

    async def _validated_profile(
        self,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> PeakProfile | None:
        profile: PeakProfile | None = None
        failed = False
        try:
            raw_profile = await self._profile_validator.validate_setup(
                connection,
                envelopes,
            )
            profile = PeakProfile.model_validate(raw_profile)
        except Exception:
            failed = True
        if failed:
            if "raw_profile" in locals():
                del raw_profile
            return None
        return profile

    async def disconnect(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> PeakDisconnectOutcome:
        result: PeakDisconnectOutcome | None = None
        failed = False
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            access_token = await _await_value(self._mercury_access_token(checked_principal))
            membership = await _await_value(
                self._workspace_service.require_workspace(
                    checked_principal,
                    access_token,
                    workspace_id,
                    WorkspaceRole.MEMBER,
                )
            )
            summaries = await _await_value(
                self._connection_store.list_for_workspace(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                )
            )
            summary = next(
                (
                    item
                    for item in summaries
                    if item.connection_id == connection_id and item.provider is ProviderId.PEAK
                ),
                None,
            )
            if summary is None:
                raise ValueError
            disconnected = await _await_value(
                self._connection_store.disconnect(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                    connection_id=connection_id,
                    provider_revocation_required=True,
                )
            )
            if (
                disconnected.connection_id != connection_id
                or not disconnected.provider_revocation_required
            ):
                raise ValueError
            result = PeakDisconnectOutcome()
        except Exception:
            failed = True
        if failed or result is None:
            if "access_token" in locals():
                del access_token
            if "checked_principal" in locals():
                del checked_principal
            del principal
            del self
            raise PeakSetupError("peak_setup_state_invalid")
        return result

    def _connection(
        self,
        *,
        session: PeakSetupSessionRecord,
        connection_id: UUID,
        merchant_id: str,
        display_name: str,
        revision: int,
        readiness: ConnectionReadiness,
        validated_at: datetime | None,
        envelope_ids: tuple[UUID, UUID, UUID],
    ) -> ProviderConnection:
        now = self._timestamp()
        return ProviderConnection(
            id=connection_id,
            tenant_id=session.tenant_id,
            workspace_id=session.workspace_id,
            auth_user_id=session.auth_user_id,
            provider=ProviderId.PEAK,
            environment=session.environment,
            provider_account_id=merchant_id,
            account_display_name=display_name,
            authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS,
            granted_permissions=("profile.read",),
            readiness=readiness,
            revision=revision,
            last_validated_at=validated_at,
            credential_envelope_ids=envelope_ids,
            created_at=now,
            updated_at=now,
        )

    def _random_token(self) -> str:
        value = self._random_bytes(32)
        if not isinstance(value, bytes) or len(value) != 32:
            raise PeakSetupError("peak_setup_unavailable")
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    def _timestamp(self) -> datetime:
        try:
            return _aware_utc(self._clock())
        except Exception:
            raise PeakSetupError("peak_setup_unavailable") from None

    @staticmethod
    def _checked_uuid(value: UUID) -> UUID:
        if not isinstance(value, UUID) or value.int == 0:
            raise PeakSetupError("peak_setup_unavailable")
        return value


def render_peak_setup_page() -> str:
    return (
        resources.files("mercury_tools.providers")
        .joinpath("templates")
        .joinpath("peak-setup.html")
        .read_text(encoding="utf-8")
    )


def render_peak_setup_form(exchange: PeakSetupExchange) -> str:
    checked = PeakSetupExchange.model_validate(exchange)
    return _render_peak_setup_form(
        setup_session=checked.setup_session,
        csrf_token=checked.csrf_token,
        validation_failed=False,
    )


def render_peak_setup_retry_form(
    setup_session: SecretStr,
    csrf_token: SecretStr,
) -> str:
    return _render_peak_setup_form(
        setup_session=setup_session,
        csrf_token=csrf_token,
        validation_failed=True,
    )


def _render_peak_setup_form(
    *,
    setup_session: SecretStr,
    csrf_token: SecretStr,
    validation_failed: bool,
) -> str:
    safe_session = escape(setup_session.get_secret_value(), quote=True)
    safe_csrf = escape(csrf_token.get_secret_value(), quote=True)
    error = (
        '<p class="error">PEAK could not validate those credentials. Try again.</p>'
        if validation_failed
        else ""
    )
    return f"""<h1>Connect PEAK</h1>
    <p>Enter the PEAK credentials for this Mercury workspace.</p>
    {error}
    <form method="post" action="{PEAK_SETUP_PATH}" autocomplete="off">
      <input type="hidden" name="setup_session" value="{safe_session}">
      <input type="hidden" name="csrf_token" value="{safe_csrf}">
      <label>User Token
        <input type="password" name="user_token" autocomplete="off" required>
      </label>
      <label>Connect ID
        <input type="password" name="connect_id" autocomplete="off" required>
      </label>
      <label>Connect Key
        <input type="password" name="connect_key" autocomplete="off" required>
      </label>
      <button type="submit">Connect PEAK</button>
    </form>"""


def peak_setup_security_headers() -> dict[str, str]:
    content = render_peak_setup_page()
    style = _inline_content(content, "style")
    script = _inline_content(content, "script")
    style_hash = base64.b64encode(hashlib.sha256(style.encode("utf-8")).digest()).decode("ascii")
    script_hash = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode("ascii")
    return {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"script-src 'sha256-{script_hash}'; "
            f"style-src 'sha256-{style_hash}'; "
            "connect-src 'self'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
    }


def peak_setup_browser_origin(settings: Settings) -> str:
    return _browser_origin(settings.provider_callback_base_url)


def peak_setup_secret_fields() -> tuple[str, ...]:
    return _SECRET_FIELDS


def _same_setup_binding(
    session: PeakSetupSessionRecord,
    attempt: SetupAttempt,
    connection: ProviderConnection,
) -> bool:
    return bool(
        session.setup_attempt_id == attempt.id
        and session.tenant_id == attempt.tenant_id == connection.tenant_id
        and session.workspace_id == attempt.workspace_id == connection.workspace_id
        and session.auth_user_id == attempt.auth_user_id == connection.auth_user_id
        and session.provider is attempt.provider is connection.provider is ProviderId.PEAK
        and secrets.compare_digest(session.environment, attempt.environment)
        and secrets.compare_digest(session.environment, connection.environment)
    )


def _session_from_row(
    row: Mapping[str, Any],
    *,
    session_hash: str = "0" * 64,
    csrf_hash: str = "0" * 64,
) -> PeakSetupSessionRecord:
    failure: PeakSetupError | None = None
    record: PeakSetupSessionRecord | None = None
    try:
        if set(row) != {
            "session_id",
            "setup_attempt_id",
            "tenant_id",
            "workspace_id",
            "auth_user_id",
            "provider",
            "environment",
            "expires_at",
            "consumed_at",
            "created_at",
        }:
            raise ValueError
        record = PeakSetupSessionRecord(
            id=UUID(str(row["session_id"])),
            setup_attempt_id=UUID(str(row["setup_attempt_id"])),
            tenant_id=UUID(str(row["tenant_id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            auth_user_id=UUID(str(row["auth_user_id"])),
            provider=ProviderId(row["provider"]),
            environment=row["environment"],
            session_hash=session_hash,
            csrf_hash=csrf_hash,
            expires_at=_rpc_timestamp(row["expires_at"]),
            consumed_at=(
                _rpc_timestamp(row["consumed_at"]) if row.get("consumed_at") is not None else None
            ),
            created_at=_rpc_timestamp(row["created_at"]),
        )
        if (
            record.id.int == 0
            or record.setup_attempt_id.int == 0
            or record.tenant_id.int == 0
            or record.workspace_id.int == 0
            or record.auth_user_id.int == 0
            or record.provider is not ProviderId.PEAK
            or _IDENTIFIER_RE.fullmatch(record.environment) is None
            or len(record.environment) > 64
            or _HASH_RE.fullmatch(record.session_hash) is None
            or _HASH_RE.fullmatch(record.csrf_hash) is None
            or record.consumed_at is not None
            or record.expires_at <= record.created_at
            or record.expires_at > record.created_at + PEAK_SETUP_LIFETIME
        ):
            raise ValueError
    except Exception:
        failure = PeakSetupError("peak_setup_state_invalid")
    if failure is not None:
        raise failure
    if record is None:
        raise PeakSetupError("peak_setup_state_invalid")
    return record


def _inline_content(document: str, tag: str) -> str:
    matched = re.search(rf"<{tag}>(.*?)</{tag}>", document, flags=re.DOTALL)
    if matched is None:
        raise PeakSetupError("peak_setup_configuration_invalid")
    return matched.group(1)


def _raw_token(value: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        raise ValueError("peak_setup_state_invalid")
    try:
        decoded = base64.b64decode(
            (value + "=" * (-len(value) % 4)).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
    except ValueError:
        raise ValueError("peak_setup_state_invalid") from None
    if len(decoded) != 32:
        raise ValueError("peak_setup_state_invalid")
    return value


def _token_hash(value: str) -> str:
    checked = _raw_token(value)
    return hashlib.sha256(checked.encode("ascii")).hexdigest()


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("peak_setup_timestamp_invalid")
    return value.astimezone(UTC)


def _browser_origin(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        return f"https://{parsed.netloc}"
    except Exception:
        raise PeakSetupError("peak_setup_configuration_invalid") from None


def _rpc_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


async def _await_value(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = [
    "InMemoryPeakSetupStore",
    "PEAK_REVOCATION_INSTRUCTION",
    "PEAK_SETUP_BROWSER_COOKIE",
    "PEAK_SETUP_EXCHANGE_PATH",
    "PEAK_SETUP_LIFETIME",
    "PEAK_SETUP_PATH",
    "PeakBrowserSessionBinding",
    "PeakBrowserSessionManager",
    "PeakDisconnectOutcome",
    "PeakSetupError",
    "PeakSetupExchange",
    "PeakSetupExchangeRequest",
    "PeakSetupProfileValidator",
    "PeakSetupService",
    "PeakSetupSessionRecord",
    "PeakSetupStart",
    "PeakSetupStore",
    "PeakSetupSubmission",
    "SupabasePeakSetupStore",
    "peak_setup_browser_origin",
    "peak_setup_secret_fields",
    "peak_setup_security_headers",
    "render_peak_setup_form",
    "render_peak_setup_page",
    "render_peak_setup_retry_form",
]
