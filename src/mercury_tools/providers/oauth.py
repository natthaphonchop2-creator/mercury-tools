"""FlowAccount authorization through downstream MCP OAuth metadata."""

from __future__ import annotations

import base64
import hashlib
import inspect
import ipaddress
import json
import re
import secrets
import socket
import ssl
import threading
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.request import parse_http_list
from uuid import UUID, uuid4

import anyio
import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from mercury_tools.auth.models import MercuryPrincipal, PrincipalResolver
from mercury_tools.config import Settings, v1_supabase_rest_url
from mercury_tools.credentials.models import CredentialBinding, CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault, CredentialVaultError
from mercury_tools.providers.base import ProviderStatusClass
from mercury_tools.providers.flowaccount import (
    FlowAccountOAuthTokens,
    FlowAccountRefreshRequest,
    seal_flowaccount_credentials,
)
from mercury_tools.providers.manifest import (
    ProviderDriverManifest,
    resolve_provider_resource,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderConnectionSummary,
    ProviderId,
)
from mercury_tools.providers.store import ProviderConnectionStore, ProviderStoreError
from mercury_tools.workspaces.models import WorkspaceRole

FLOWACCOUNT_CALLBACK_PATH = "/auth/providers/flowaccount/callback"
_STATE_LIFETIME = timedelta(minutes=10)
_PROFILE_CAPABILITY = "provider_profile.get"
_MAX_METADATA_BYTES = 128 * 1024
_OAUTH_ERROR_CODES = frozenset(
    {
        "provider_oauth_callback_invalid",
        "provider_oauth_authorization_failed",
        "provider_oauth_company_mismatch",
        "provider_oauth_configuration_invalid",
        "provider_oauth_downstream_invalid",
        "provider_oauth_exchange_failed",
        "provider_oauth_request_invalid",
        "provider_oauth_state_invalid",
        "provider_oauth_validation_failed",
    }
)
_AUTH_TOKEN = r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+"
_AUTH_CHALLENGE_START = re.compile(rf"^({_AUTH_TOKEN})(?:[ \t]+(.+))?$")
_AUTH_PARAMETER = re.compile(rf'^({_AUTH_TOKEN})[ \t]*=[ \t]*(?:"([^"\\]*)"|({_AUTH_TOKEN}))$')


class _OAuthModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _aware_utc(value: datetime, *, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(code)
    return value.astimezone(UTC)


def _clean_https_url(value: str, *, code: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if not (
            parsed.scheme == "https"
            and parsed.hostname
            and (port is None or port > 0)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
        ):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(code) from None
    return value


def _same_origin(left: str, right: str) -> bool:
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (
        left_url.scheme,
        left_url.hostname,
        left_url.port or 443,
    ) == (
        right_url.scheme,
        right_url.hostname,
        right_url.port or 443,
    )


def _url_origin(value: str) -> str:
    parsed = urlsplit(value)
    host = parsed.hostname
    if host is None:
        raise ValueError
    port = parsed.port
    default_port = port is None or port == 443
    authority = host if default_port else f"{host}:{port}"
    return f"https://{authority}"


def _clean_https_origin(value: str) -> str:
    checked = _clean_https_url(
        value,
        code="provider_oauth_configuration_invalid",
    )
    parsed = urlsplit(checked)
    if parsed.path not in {"", "/"}:
        raise ValueError
    return _url_origin(checked)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _bearer_challenge_parameters(headers: httpx.Headers) -> dict[str, str]:
    challenges: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    combined = ", ".join(headers.get_list("www-authenticate"))
    for item in parse_http_list(combined):
        segment = item.strip()
        start = _AUTH_CHALLENGE_START.fullmatch(segment)
        if start is not None:
            current = (start.group(1), [])
            challenges.append(current)
            if start.group(2) is not None:
                current[1].append(start.group(2))
        elif current is not None:
            current[1].append(segment)
        else:
            raise ValueError

    bearer = [parameters for scheme, parameters in challenges if scheme.casefold() == "bearer"]
    if len(bearer) != 1:
        raise ValueError

    parsed: dict[str, str] = {}
    for parameter in bearer[0]:
        match = _AUTH_PARAMETER.fullmatch(parameter)
        if match is None:
            raise ValueError
        name = match.group(1).casefold()
        value = match.group(2) if match.group(2) is not None else match.group(3)
        if name in parsed or value is None:
            raise ValueError
        parsed[name] = value
    return parsed


def _checked_selected_company_id(value: str | None, *, code: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 0 < len(value) <= 512
        or value != value.strip()
        or any(
            character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}
            for character in value
        )
    ):
        raise ValueError(code)
    return value


def _checked_permissions(
    value: tuple[str, ...],
    *,
    code: str,
) -> tuple[str, ...]:
    if (
        not isinstance(value, tuple)
        or not value
        or tuple(sorted(value)) != value
        or len(value) != len(set(value))
        or any(not isinstance(item, str) or not item or len(item) > 200 for item in value)
    ):
        raise ValueError(code)
    return value


async def _resolve_public_addresses(host: str, port: int) -> tuple[str, ...]:
    infos = await anyio.getaddrinfo(
        host,
        port,
        type=socket.SOCK_STREAM,
    )
    return tuple(sorted({str(info[4][0]) for info in infos}))


class ProviderOAuthError(RuntimeError):
    """A closed OAuth failure that never retains provider material."""

    def __init__(self, code: str) -> None:
        if code not in _OAUTH_ERROR_CODES:
            raise ValueError("provider_oauth_error_invalid")
        self.code = code
        super().__init__(code)


class OAuthAuthorizationSession(_OAuthModel):
    authorization_url: str
    resource_uri: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None = None
    callback_uri: str
    client_id: str = Field(min_length=1, max_length=1024)
    client_secret: str | None = Field(
        default=None,
        min_length=1,
        max_length=16_384,
        repr=False,
        exclude=True,
    )
    token_endpoint_auth_method: Literal[
        "none",
        "client_secret_basic",
        "client_secret_post",
    ]
    granted_permissions: tuple[str, ...]

    @field_validator(
        "authorization_endpoint",
        "callback_uri",
        "resource_uri",
        "token_endpoint",
    )
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _clean_https_url(value, code="provider_oauth_downstream_invalid")

    @field_validator("revocation_endpoint")
    @classmethod
    def validate_optional_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_https_url(value, code="provider_oauth_downstream_invalid")

    @field_validator("client_secret")
    @classmethod
    def validate_secret(cls, value: str | None) -> str | None:
        if value is not None and (
            value != value.strip() or any(character.isspace() for character in value)
        ):
            raise ValueError("provider_oauth_downstream_invalid")
        return value

    @field_validator("granted_permissions")
    @classmethod
    def validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or tuple(sorted(value)) != value
            or len(value) != len(set(value))
            or any(not item or len(item) > 200 for item in value)
        ):
            raise ValueError("provider_oauth_downstream_invalid")
        return value


class OAuthCallback(_OAuthModel):
    code: str | None = Field(
        default=None,
        min_length=1,
        max_length=16_384,
        repr=False,
        exclude=True,
    )
    error: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z][A-Za-z0-9._~-]*$",
        repr=False,
        exclude=True,
    )
    state: str = Field(
        min_length=43,
        max_length=512,
        pattern=r"^[A-Za-z0-9_-]+$",
        repr=False,
        exclude=True,
    )
    provider: ProviderId = ProviderId.FLOWACCOUNT
    environment: str | None = Field(default=None, min_length=1, max_length=64)
    workspace_id: UUID | None = None
    redirect_uri: str | None = None

    @field_validator("code", "error", "state")
    @classmethod
    def validate_secret(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("provider_oauth_callback_invalid")
        return value

    @model_validator(mode="after")
    def validate_result_union(self) -> OAuthCallback:
        if (self.code is None) == (self.error is None):
            raise ValueError("provider_oauth_callback_invalid")
        return self

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_https_url(value, code="provider_oauth_callback_invalid")


class ProviderAuthorizationStart(_OAuthModel):
    authorization_url: str
    provider: ProviderId
    environment: str
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return _aware_utc(value, code="provider_oauth_request_invalid")


class _StoredOAuthPayload(_OAuthModel):
    code_verifier: str = Field(repr=False, exclude=True)
    authorization_url: str
    resource_uri: str
    authorization_endpoint: str
    token_endpoint: str
    revocation_endpoint: str | None = None
    callback_uri: str
    client_id: str
    client_secret: str | None = Field(default=None, repr=False, exclude=True)
    token_endpoint_auth_method: Literal[
        "none",
        "client_secret_basic",
        "client_secret_post",
    ]
    granted_permissions: tuple[str, ...]
    selected_company_id: str | None = Field(default=None, repr=False, exclude=True)

    @field_validator("selected_company_id")
    @classmethod
    def validate_selected_company_id(cls, value: str | None) -> str | None:
        return _checked_selected_company_id(
            value,
            code="provider_oauth_state_invalid",
        )

    def session(self) -> OAuthAuthorizationSession:
        return OAuthAuthorizationSession(
            authorization_url=self.authorization_url,
            resource_uri=self.resource_uri,
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            revocation_endpoint=self.revocation_endpoint,
            callback_uri=self.callback_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token_endpoint_auth_method=self.token_endpoint_auth_method,
            granted_permissions=self.granted_permissions,
        )


class _StoredOAuthStateAccess(_OAuthModel):
    mercury_access_token: str = Field(repr=False, exclude=True)
    authorization_envelope: Mapping[str, Any] = Field(repr=False, exclude=True)


@dataclass(frozen=True, repr=False)
class ProviderOAuthStateRecord:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str
    state_hash: str
    callback_uri: str
    requested_permissions: tuple[str, ...]
    expires_at: datetime
    encrypted_payload: CredentialEnvelope
    consumed_at: datetime | None = None


class ProviderOAuthStateStore(Protocol):
    async def create(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        state_hash: str,
        callback_uri: str,
        requested_permissions: tuple[str, ...],
        expires_at: datetime,
        encrypted_payload: CredentialEnvelope,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord: ...

    async def peek(self, *, state_hash: str) -> ProviderOAuthStateRecord: ...

    async def consume(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord: ...

    async def cancel(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> None: ...

    async def cleanup_expired(self, *, limit: int = 100) -> int: ...


class InMemoryProviderOAuthStateStore:
    """Reference single-use state store matching the Task 4 RPC lifecycle."""

    def __init__(
        self,
        *,
        provider_store: ProviderConnectionStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(provider_store, ProviderConnectionStore):
            raise TypeError("provider_oauth_state_store_invalid")
        self._provider_store = provider_store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._states: dict[str, ProviderOAuthStateRecord] = {}

    def __repr__(self) -> str:
        return "InMemoryProviderOAuthStateStore()"

    async def create(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        state_hash: str,
        callback_uri: str,
        requested_permissions: tuple[str, ...],
        expires_at: datetime,
        encrypted_payload: CredentialEnvelope,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord:
        try:
            checked_permissions = _checked_permissions(
                requested_permissions,
                code="provider_oauth_state_invalid",
            )
            if not mercury_access_token:
                raise ValueError
            attempt = self._provider_store.create_attempt(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=provider,
                environment=environment,
                token_hash=state_hash,
                expires_at=expires_at,
            )
            self._provider_store.consume_attempt(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=provider,
                environment=environment,
                token_hash=state_hash,
            )
            record = ProviderOAuthStateRecord(
                id=encrypted_payload.connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=provider,
                environment=environment,
                state_hash=state_hash,
                callback_uri=callback_uri,
                requested_permissions=checked_permissions,
                expires_at=expires_at,
                encrypted_payload=encrypted_payload,
            )
            if attempt.expires_at != record.expires_at:
                raise ValueError
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None
        with self._lock:
            if state_hash in self._states:
                raise ProviderOAuthError("provider_oauth_state_invalid")
            self._states[state_hash] = record
        return record

    async def peek(self, *, state_hash: str) -> ProviderOAuthStateRecord:
        now = self._timestamp()
        with self._lock:
            record = self._states.get(state_hash)
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                raise ProviderOAuthError("provider_oauth_state_invalid")
            return record

    async def consume(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord:
        if not mercury_access_token:
            raise ProviderOAuthError("provider_oauth_state_invalid")
        now = self._timestamp()
        with self._lock:
            current = self._states.get(record.state_hash)
            if (
                current is None
                or current != record
                or current.consumed_at is not None
                or current.expires_at <= now
            ):
                raise ProviderOAuthError("provider_oauth_state_invalid")
            consumed = ProviderOAuthStateRecord(
                **{
                    **current.__dict__,
                    "consumed_at": now,
                }
            )
            del self._states[current.state_hash]
            return consumed

    async def cancel(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> None:
        await self.consume(
            record=record,
            mercury_access_token=mercury_access_token,
        )

    async def cleanup_expired(self, *, limit: int = 100) -> int:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ProviderOAuthError("provider_oauth_state_invalid")
        now = self._timestamp()
        with self._lock:
            expired = sorted(
                (
                    state_hash
                    for state_hash, record in self._states.items()
                    if record.consumed_at is None and record.expires_at <= now
                )
            )[:limit]
            for state_hash in expired:
                del self._states[state_hash]
        return len(expired)

    def _timestamp(self) -> datetime:
        try:
            return _aware_utc(
                self._clock(),
                code="provider_oauth_state_invalid",
            )
        except (TypeError, ValueError):
            raise ProviderOAuthError("provider_oauth_state_invalid") from None


class SupabaseProviderOAuthStateStore:
    """Durable OAuth state adapter over the Task 4 atomic RPC contract."""

    def __init__(
        self,
        *,
        settings: Settings,
        http_client: httpx.AsyncClient,
        callback_uri: str,
        uuid_factory: Callable[[], UUID] | None = None,
        clock: Callable[[], datetime] | None = None,
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
            self._callback_uri = _clean_https_url(
                callback_uri,
                code="provider_oauth_configuration_invalid",
            )
        except Exception:
            raise ProviderOAuthError("provider_oauth_configuration_invalid") from None
        self._publishable_key = settings.supabase_publishable_key
        self._service_role_key = settings.supabase_service_role_key
        self._http = http_client
        self._uuid_factory = uuid_factory or uuid4
        self._clock = clock or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "SupabaseProviderOAuthStateStore()"

    async def create(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        state_hash: str,
        callback_uri: str,
        requested_permissions: tuple[str, ...],
        expires_at: datetime,
        encrypted_payload: CredentialEnvelope,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord:
        try:
            checked_provider = ProviderId(provider)
            checked_permissions = _checked_permissions(
                requested_permissions,
                code="provider_oauth_state_invalid",
            )
            checked_envelope = CredentialEnvelope.model_validate(encrypted_payload)
            normalized_expiry = _aware_utc(
                expires_at,
                code="provider_oauth_state_invalid",
            )
            if (
                checked_provider is not ProviderId.FLOWACCOUNT
                or callback_uri != self._callback_uri
                or not mercury_access_token
                or checked_envelope.connection_id != checked_envelope.id
                or checked_envelope.tenant_id != tenant_id
                or checked_envelope.workspace_id != workspace_id
                or checked_envelope.auth_user_id != auth_user_id
                or checked_envelope.provider != ProviderId.FLOWACCOUNT.value
                or checked_envelope.environment != environment
                or checked_envelope.credential_type != "oauth_state"
            ):
                raise ValueError
            attempt_id = self._checked_uuid(self._uuid_factory())
            setup_row = await self._rpc(
                "create_mercury_provider_setup_attempt",
                mercury_access_token=mercury_access_token,
                payload={
                    "p_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                    "p_token_hash": state_hash,
                    "p_expires_at": normalized_expiry.isoformat(),
                },
            )
            if (
                self._checked_uuid(setup_row.get("attempt_id")) != attempt_id
                or self._checked_uuid(setup_row.get("tenant_id")) != tenant_id
                or self._checked_uuid(setup_row.get("workspace_id")) != workspace_id
                or self._checked_uuid(setup_row.get("auth_user_id")) != auth_user_id
                or setup_row.get("provider") != checked_provider.value
                or setup_row.get("environment") != environment
                or self._timestamp(setup_row.get("expires_at")) != normalized_expiry
                or setup_row.get("consumed_at") is not None
            ):
                raise ValueError
            callback_state = self._callback_state(
                state_id=checked_envelope.id,
                permissions=checked_permissions,
            )
            state_row = await self._rpc(
                "create_mercury_provider_oauth_state",
                mercury_access_token=mercury_access_token,
                payload={
                    "p_state_id": str(checked_envelope.id),
                    "p_setup_attempt_id": str(attempt_id),
                    "p_tenant_id": str(tenant_id),
                    "p_workspace_id": str(workspace_id),
                    "p_auth_user_id": str(auth_user_id),
                    "p_provider": checked_provider.value,
                    "p_environment": environment,
                    "p_state_hash": state_hash,
                    "p_pkce_verifier_ciphertext": _postgres_bytea(checked_envelope.ciphertext),
                    "p_pkce_key_version": checked_envelope.key_version,
                    "p_pkce_nonce": _postgres_bytea(checked_envelope.nonce),
                    "p_pkce_aad_hash": _postgres_bytea(checked_envelope.aad_hash),
                    "p_callback_state": callback_state,
                    "p_expires_at": normalized_expiry.isoformat(),
                },
            )
            if (
                self._checked_uuid(state_row.get("oauth_state_id")) != checked_envelope.id
                or self._checked_uuid(state_row.get("setup_attempt_id")) != attempt_id
                or self._timestamp(state_row.get("expires_at")) != normalized_expiry
            ):
                raise ValueError
            return ProviderOAuthStateRecord(
                id=checked_envelope.id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=checked_provider,
                environment=environment,
                state_hash=state_hash,
                callback_uri=callback_uri,
                requested_permissions=checked_permissions,
                expires_at=normalized_expiry,
                encrypted_payload=checked_envelope,
            )
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    async def peek(self, *, state_hash: str) -> ProviderOAuthStateRecord:
        try:
            response = await self._http.get(
                f"{self._base_url}/mercury_provider_oauth_states",
                params={
                    "state_hash": f"eq.{state_hash}",
                    "consumed_at": "is.null",
                    "select": (
                        "id,tenant_id,workspace_id,auth_user_id,provider,"
                        "environment,state_hash,pkce_verifier_ciphertext,"
                        "pkce_key_version,pkce_nonce,pkce_aad_hash,"
                        "callback_state,expires_at,consumed_at,created_at"
                    ),
                    "limit": "1",
                },
                headers=self._service_headers(),
                follow_redirects=False,
            )
            row = _single_response_row(response)
            record = self._record_from_row(row)
            if (
                not secrets.compare_digest(record.state_hash, state_hash)
                or record.consumed_at is not None
                or record.expires_at <= self._now()
            ):
                raise ValueError
            return record
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    async def cancel(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> None:
        try:
            if not mercury_access_token:
                raise ValueError
            row = await self._rpc(
                "cancel_mercury_provider_oauth_state",
                mercury_access_token=mercury_access_token,
                payload={
                    "p_tenant_id": str(record.tenant_id),
                    "p_workspace_id": str(record.workspace_id),
                    "p_auth_user_id": str(record.auth_user_id),
                    "p_provider": record.provider.value,
                    "p_environment": record.environment,
                    "p_state_hash": record.state_hash,
                },
            )
            if (
                self._checked_uuid(row.get("oauth_state_id")) != record.id
                or row.get("callback_state")
                != self._callback_state(
                    state_id=record.id,
                    permissions=record.requested_permissions,
                )
                or self._timestamp(row.get("consumed_at")) < self._now() - timedelta(minutes=1)
            ):
                raise ValueError
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    async def cleanup_expired(self, *, limit: int = 100) -> int:
        try:
            if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
                raise ValueError
            row = await self._service_rpc(
                "cleanup_expired_mercury_provider_oauth_states",
                payload={"p_limit": limit},
            )
            cleaned = row.get("cleaned_count")
            if (
                not isinstance(cleaned, int)
                or isinstance(cleaned, bool)
                or not 0 <= cleaned <= limit
            ):
                raise ValueError
            return cleaned
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    async def consume(
        self,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> ProviderOAuthStateRecord:
        try:
            if not mercury_access_token:
                raise ValueError
            row = await self._rpc(
                "consume_mercury_provider_oauth_state",
                mercury_access_token=mercury_access_token,
                payload={
                    "p_tenant_id": str(record.tenant_id),
                    "p_workspace_id": str(record.workspace_id),
                    "p_auth_user_id": str(record.auth_user_id),
                    "p_provider": record.provider.value,
                    "p_environment": record.environment,
                    "p_state_hash": record.state_hash,
                },
            )
            if self._checked_uuid(row.get("oauth_state_id")) != record.id or row.get(
                "callback_state"
            ) != self._callback_state(
                state_id=record.id,
                permissions=record.requested_permissions,
            ):
                raise ValueError
            envelope = CredentialEnvelope(
                id=record.id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                connection_id=record.id,
                provider=record.provider.value,
                environment=record.environment,
                credential_type="oauth_state",
                key_version=row["pkce_key_version"],
                nonce=_decode_postgres_bytea(row["pkce_nonce"]),
                ciphertext=_decode_postgres_bytea(row["pkce_verifier_ciphertext"]),
                aad_hash=_decode_postgres_bytea(row["pkce_aad_hash"]),
                created_at=record.encrypted_payload.created_at,
            )
            return ProviderOAuthStateRecord(
                **{
                    **record.__dict__,
                    "encrypted_payload": envelope,
                    "consumed_at": self._timestamp(row.get("consumed_at")),
                }
            )
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    async def _rpc(
        self,
        function: str,
        *,
        mercury_access_token: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        response = await self._http.post(
            f"{self._base_url}/rpc/{function}",
            json=dict(payload),
            headers={
                "apikey": self._publishable_key,
                "Authorization": f"Bearer {mercury_access_token}",
                "Content-Type": "application/json",
            },
            follow_redirects=False,
        )
        return _single_response_row(response)

    async def _service_rpc(
        self,
        function: str,
        *,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        response = await self._http.post(
            f"{self._base_url}/rpc/{function}",
            json=dict(payload),
            headers=self._service_headers(),
            follow_redirects=False,
        )
        return _single_response_row(response)

    def _record_from_row(self, row: Mapping[str, Any]) -> ProviderOAuthStateRecord:
        state_id = self._checked_uuid(row.get("id"))
        tenant_id = self._checked_uuid(row.get("tenant_id"))
        workspace_id = self._checked_uuid(row.get("workspace_id"))
        auth_user_id = self._checked_uuid(row.get("auth_user_id"))
        provider = ProviderId(row.get("provider"))
        environment = row.get("environment")
        state_hash = row.get("state_hash")
        expires_at = self._timestamp(row.get("expires_at"))
        created_at = self._timestamp(row.get("created_at"))
        callback_state = row.get("callback_state")
        requested_permissions = (
            callback_state.get("requested_permissions")
            if isinstance(callback_state, Mapping)
            else None
        )
        checked_permissions = (
            _checked_permissions(
                tuple(requested_permissions),
                code="provider_oauth_state_invalid",
            )
            if isinstance(requested_permissions, list)
            else None
        )
        if (
            provider is not ProviderId.FLOWACCOUNT
            or not isinstance(environment, str)
            or not isinstance(state_hash, str)
            or checked_permissions is None
            or callback_state
            != self._callback_state(
                state_id=state_id,
                permissions=checked_permissions,
            )
        ):
            raise ValueError
        envelope = CredentialEnvelope(
            id=state_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            connection_id=state_id,
            provider=provider.value,
            environment=environment,
            credential_type="oauth_state",
            key_version=row["pkce_key_version"],
            nonce=_decode_postgres_bytea(row["pkce_nonce"]),
            ciphertext=_decode_postgres_bytea(row["pkce_verifier_ciphertext"]),
            aad_hash=_decode_postgres_bytea(row["pkce_aad_hash"]),
            created_at=created_at,
        )
        consumed_at = row.get("consumed_at")
        return ProviderOAuthStateRecord(
            id=state_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            auth_user_id=auth_user_id,
            provider=provider,
            environment=environment,
            state_hash=state_hash,
            callback_uri=self._callback_uri,
            requested_permissions=checked_permissions,
            expires_at=expires_at,
            encrypted_payload=envelope,
            consumed_at=(self._timestamp(consumed_at) if consumed_at is not None else None),
        )

    def _service_headers(self) -> dict[str, str]:
        return {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Content-Type": "application/json",
        }

    def _now(self) -> datetime:
        try:
            return _aware_utc(
                self._clock(),
                code="provider_oauth_state_invalid",
            )
        except (TypeError, ValueError):
            raise ProviderOAuthError("provider_oauth_state_invalid") from None

    @staticmethod
    def _callback_state(
        *,
        state_id: UUID,
        permissions: Sequence[str],
    ) -> dict[str, Any]:
        return {
            "return_path": FLOWACCOUNT_CALLBACK_PATH,
            "requested_permissions": list(permissions),
            "connection_attempt_id": str(state_id),
        }

    @staticmethod
    def _checked_uuid(value: Any) -> UUID:
        checked = value if isinstance(value, UUID) else UUID(str(value))
        if checked.int == 0:
            raise ValueError
        return checked

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        if not isinstance(value, str):
            raise ValueError
        return _aware_utc(
            datetime.fromisoformat(value.replace("Z", "+00:00")),
            code="provider_oauth_state_invalid",
        )


OAuthAddressResolver = Callable[
    [str, int],
    Sequence[str] | Awaitable[Sequence[str]],
]


class OAuthNetworkGuard(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response: ...


class _PinnedNetworkBackend:
    def __init__(self, guard: PublicOAuthNetworkGuard) -> None:
        self._guard = guard
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        addresses = await self._guard.resolve_and_pin(host, port)
        return await self._backend.connect_tcp(
            sorted(addresses)[0],
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("provider_oauth_downstream_invalid")

    async def sleep(self, seconds: float) -> None:
        await anyio.sleep(seconds)


class _PinnedOAuthTransport(httpx.AsyncHTTPTransport):
    def __init__(self, guard: PublicOAuthNetworkGuard) -> None:
        super().__init__(verify=ssl.create_default_context(), trust_env=False, retries=0)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            retries=0,
            network_backend=_PinnedNetworkBackend(guard),
        )


class PublicOAuthNetworkGuard:
    """HTTPS request boundary with public-only DNS pinning per origin."""

    def __init__(
        self,
        *,
        resolver: OAuthAddressResolver | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._resolver = resolver or _resolve_public_addresses
        self._pins: dict[tuple[str, int], frozenset[str]] = {}
        self._lock = threading.RLock()
        self._http = http_client
        self._owns_http = self._http is None
        if self._http is None:
            self._http = httpx.AsyncClient(
                transport=_PinnedOAuthTransport(self),
                follow_redirects=False,
                trust_env=False,
            )

    def __repr__(self) -> str:
        return "PublicOAuthNetworkGuard()"

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        parsed = urlsplit(_clean_https_url(url, code="provider_oauth_downstream_invalid"))
        if parsed.hostname is None:
            raise ProviderOAuthError("provider_oauth_downstream_invalid")
        await self.resolve_and_pin(parsed.hostname, parsed.port or 443)
        try:
            return await self._http.request(
                method,
                url,
                follow_redirects=False,
                **kwargs,
            )
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_downstream_invalid") from None

    async def resolve_and_pin(self, host: str, port: int) -> frozenset[str]:
        try:
            resolved = self._resolver(host, port)
            if inspect.isawaitable(resolved):
                resolved = await resolved
            if isinstance(resolved, (str, bytes, bytearray)):
                raise ValueError
            addresses = frozenset(self._public_global_unicast(value) for value in resolved)
            if not addresses or len(addresses) > 16:
                raise ValueError
            key = (host.casefold(), port)
            with self._lock:
                pinned = self._pins.get(key)
                if pinned is not None and pinned != addresses:
                    raise ValueError
                self._pins[key] = addresses
            return addresses
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_downstream_invalid") from None

    @staticmethod
    def _public_global_unicast(value: object) -> str:
        if not isinstance(value, str) or not value or "%" in value:
            raise ValueError
        address = ipaddress.ip_address(value)
        if str(address) != value.casefold():
            raise ValueError
        if (
            address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            or address.is_link_local
            or address.is_loopback
            or address.is_private
            or getattr(address, "is_site_local", False)
            or getattr(address, "ipv4_mapped", None) is not None
            or not address.is_global
        ):
            raise ValueError
        return str(address)


class DownstreamMCPOAuthClient:
    """Strict OAuth discovery rooted only at the configured MCP resource."""

    def __init__(
        self,
        *,
        network_guard: OAuthNetworkGuard,
        authorization_server_origins: Mapping[
            tuple[ProviderId | str, str],
            Sequence[str],
        ],
    ) -> None:
        self._network_guard = network_guard
        try:
            origins: dict[tuple[ProviderId, str], frozenset[str]] = {}
            for (provider, environment), values in authorization_server_origins.items():
                checked_provider = ProviderId(provider)
                if not environment or isinstance(values, (str, bytes, bytearray)) or not values:
                    raise ValueError
                checked_values = frozenset(_clean_https_origin(value) for value in values)
                if len(checked_values) != len(values):
                    raise ValueError
                origins[(checked_provider, environment)] = checked_values
            if not origins:
                raise ValueError
        except Exception:
            raise ProviderOAuthError("provider_oauth_configuration_invalid") from None
        self._authorization_server_origins = origins

    def __repr__(self) -> str:
        return "DownstreamMCPOAuthClient()"

    async def start_authorization(
        self,
        *,
        provider: ProviderId,
        environment: str,
        resource_uri: str,
        callback_uri: str,
        allowed_permissions: tuple[str, ...],
        state: str,
        code_challenge: str,
    ) -> OAuthAuthorizationSession:
        try:
            resource_uri = _clean_https_url(
                resource_uri,
                code="provider_oauth_downstream_invalid",
            )
            callback_uri = _clean_https_url(
                callback_uri,
                code="provider_oauth_downstream_invalid",
            )
            checked_provider = ProviderId(provider)
            allowed_origins = self._authorization_server_origins[(checked_provider, environment)]
            if tuple(sorted(allowed_permissions)) != allowed_permissions or not allowed_permissions:
                raise ValueError

            challenge_response = await self._network_guard.request(
                "GET",
                resource_uri,
                headers={"MCP-Protocol-Version": "2025-11-25"},
            )
            if challenge_response.status_code != 401:
                raise ValueError
            challenge = _bearer_challenge_parameters(challenge_response.headers)
            resource_metadata_url = challenge.get("resource_metadata")
            if resource_metadata_url is None:
                raise ValueError
            if resource_metadata_url != _protected_resource_metadata_url(resource_uri):
                raise ValueError

            resource_metadata = await self._metadata(resource_metadata_url)
            if resource_metadata.get("resource") != resource_uri:
                raise ValueError
            authorization_servers = resource_metadata.get("authorization_servers")
            if (
                not isinstance(authorization_servers, list)
                or len(authorization_servers) != 1
                or not isinstance(authorization_servers[0], str)
            ):
                raise ValueError
            authorization_server = _clean_https_url(
                authorization_servers[0],
                code="provider_oauth_downstream_invalid",
            )
            if _url_origin(authorization_server) not in allowed_origins:
                raise ValueError

            supported = _string_set(
                resource_metadata.get("scopes_supported"),
            )
            challenged_scope = challenge.get("scope")
            if challenged_scope is not None:
                challenged = set(challenged_scope.split())
                supported &= challenged
            granted_permissions = tuple(sorted(set(allowed_permissions) & supported))
            if not granted_permissions:
                raise ValueError

            server_metadata = await self._metadata(
                _authorization_server_metadata_url(authorization_server)
            )
            if server_metadata.get("issuer") != authorization_server:
                raise ValueError
            if "code" not in _string_set(server_metadata.get("response_types_supported")):
                raise ValueError
            if "authorization_code" not in _string_set(
                server_metadata.get("grant_types_supported")
            ):
                raise ValueError
            if "S256" not in _string_set(server_metadata.get("code_challenge_methods_supported")):
                raise ValueError

            authorization_endpoint = _trusted_server_endpoint(
                server_metadata.get("authorization_endpoint"),
                issuer=authorization_server,
            )
            token_endpoint = _trusted_server_endpoint(
                server_metadata.get("token_endpoint"),
                issuer=authorization_server,
            )
            registration_endpoint = _trusted_server_endpoint(
                server_metadata.get("registration_endpoint"),
                issuer=authorization_server,
            )
            revocation_value = server_metadata.get("revocation_endpoint")
            revocation_endpoint = (
                _trusted_server_endpoint(
                    revocation_value,
                    issuer=authorization_server,
                )
                if revocation_value is not None
                else None
            )
            supported_auth_methods = _string_set(
                server_metadata.get(
                    "token_endpoint_auth_methods_supported",
                    ["client_secret_basic"],
                )
            )
            registration = await self._register_client(
                registration_endpoint=registration_endpoint,
                callback_uri=callback_uri,
                supported_auth_methods=supported_auth_methods,
            )

            authorization_url = str(
                httpx.URL(authorization_endpoint).copy_merge_params(
                    {
                        "response_type": "code",
                        "client_id": registration["client_id"],
                        "redirect_uri": callback_uri,
                        "state": state,
                        "code_challenge": code_challenge,
                        "code_challenge_method": "S256",
                        "scope": " ".join(granted_permissions),
                        "resource": resource_uri,
                    }
                )
            )
            return OAuthAuthorizationSession(
                authorization_url=authorization_url,
                resource_uri=resource_uri,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                revocation_endpoint=revocation_endpoint,
                callback_uri=callback_uri,
                client_id=registration["client_id"],
                client_secret=registration["client_secret"],
                token_endpoint_auth_method=registration["auth_method"],
                granted_permissions=granted_permissions,
            )
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_downstream_invalid") from None

    async def exchange_code(
        self,
        *,
        session: OAuthAuthorizationSession,
        code: str,
        code_verifier: str,
    ) -> FlowAccountOAuthTokens:
        try:
            checked = OAuthAuthorizationSession.model_validate(session)
            if (
                not code
                or code != code.strip()
                or any(character.isspace() for character in code)
                or not code_verifier
            ):
                raise ValueError
            form = {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": checked.callback_uri,
                "code_verifier": code_verifier,
                "client_id": checked.client_id,
                "resource": checked.resource_uri,
            }
            response = await self._token_request(checked, form)
            return _oauth_tokens(
                response,
                allowed_permissions=checked.granted_permissions,
            )
        except Exception:
            raise ProviderOAuthError("provider_oauth_exchange_failed") from None

    async def refresh(
        self,
        request: FlowAccountRefreshRequest,
    ) -> FlowAccountOAuthTokens:
        try:
            checked = FlowAccountRefreshRequest.model_validate(request)
            session = OAuthAuthorizationSession(
                authorization_url=checked.resource_uri,
                resource_uri=checked.resource_uri,
                authorization_endpoint=checked.resource_uri,
                token_endpoint=checked.token_endpoint,
                callback_uri=checked.resource_uri,
                client_id=checked.client_id,
                client_secret=checked.client_secret,
                token_endpoint_auth_method=checked.token_endpoint_auth_method,
                granted_permissions=checked.granted_permissions,
            )
            response = await self._token_request(
                session,
                {
                    "grant_type": "refresh_token",
                    "refresh_token": checked.refresh_token,
                    "client_id": checked.client_id,
                    "resource": checked.resource_uri,
                },
            )
            return _oauth_tokens(
                response,
                allowed_permissions=checked.granted_permissions,
            )
        except Exception:
            raise ProviderOAuthError("provider_oauth_exchange_failed") from None

    async def revoke(
        self,
        *,
        session: OAuthAuthorizationSession,
        tokens: FlowAccountOAuthTokens,
    ) -> bool:
        try:
            checked_session = OAuthAuthorizationSession.model_validate(session)
            checked_tokens = FlowAccountOAuthTokens.model_validate(tokens)
            if checked_session.revocation_endpoint is None:
                return False
            response = await self._client_authenticated_request(
                checked_session,
                checked_session.revocation_endpoint,
                {
                    "token": (checked_tokens.refresh_token or checked_tokens.access_token),
                    "token_type_hint": (
                        "refresh_token"
                        if checked_tokens.refresh_token is not None
                        else "access_token"
                    ),
                    "client_id": checked_session.client_id,
                },
            )
            return response.status_code in {200, 204}
        except Exception:
            return False

    async def _metadata(self, url: str) -> dict[str, Any]:
        response = await self._network_guard.request("GET", url)
        if response.status_code != 200:
            raise ValueError
        return _json_object(response)

    async def _register_client(
        self,
        *,
        registration_endpoint: str,
        callback_uri: str,
        supported_auth_methods: set[str],
    ) -> dict[str, str | None]:
        preferred = next(
            (
                method
                for method in (
                    "client_secret_basic",
                    "client_secret_post",
                    "none",
                )
                if method in supported_auth_methods
            ),
            None,
        )
        if preferred is None:
            raise ValueError
        response = await self._network_guard.request(
            "POST",
            registration_endpoint,
            json={
                "redirect_uris": [callback_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": preferred,
            },
        )
        if response.status_code not in {200, 201}:
            raise ValueError
        payload = _json_object(response)
        client_id = payload.get("client_id")
        client_secret = payload.get("client_secret")
        auth_method = payload.get("token_endpoint_auth_method")
        redirect_uris = payload.get("redirect_uris")
        grant_types = payload.get("grant_types")
        response_types = payload.get("response_types")
        if (
            not isinstance(client_id, str)
            or not client_id
            or auth_method != preferred
            or redirect_uris != [callback_uri]
            or grant_types != ["authorization_code", "refresh_token"]
            or response_types != ["code"]
            or (preferred != "none" and (not isinstance(client_secret, str) or not client_secret))
            or (client_secret is not None and not isinstance(client_secret, str))
        ):
            raise ValueError
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_method": preferred,
        }

    async def _token_request(
        self,
        session: OAuthAuthorizationSession,
        form: dict[str, str],
    ) -> httpx.Response:
        auth: httpx.BasicAuth | None = None
        if session.token_endpoint_auth_method == "client_secret_basic":
            if session.client_secret is None:
                raise ValueError
            auth = httpx.BasicAuth(session.client_id, session.client_secret)
        elif session.token_endpoint_auth_method == "client_secret_post":
            if session.client_secret is None:
                raise ValueError
            form["client_secret"] = session.client_secret
        response = await self._network_guard.request(
            "POST",
            session.token_endpoint,
            data=form,
            auth=auth,
        )
        if response.status_code != 200:
            raise ValueError
        return response

    async def _client_authenticated_request(
        self,
        session: OAuthAuthorizationSession,
        endpoint: str,
        form: dict[str, str],
    ) -> httpx.Response:
        auth: httpx.BasicAuth | None = None
        if session.token_endpoint_auth_method == "client_secret_basic":
            if session.client_secret is None:
                raise ValueError
            auth = httpx.BasicAuth(session.client_id, session.client_secret)
        elif session.token_endpoint_auth_method == "client_secret_post":
            if session.client_secret is None:
                raise ValueError
            form["client_secret"] = session.client_secret
        return await self._network_guard.request(
            "POST",
            endpoint,
            data=form,
            auth=auth,
        )


class ProviderOAuthService:
    """Authorize and validate one tenant-bound FlowAccount MCP connection."""

    def __init__(
        self,
        *,
        settings: Settings,
        workspace_service: Any,
        mercury_access_token: Callable[[MercuryPrincipal], str],
        principal_resolver: PrincipalResolver,
        manifest: ProviderDriverManifest,
        oauth_client: Any,
        state_store: ProviderOAuthStateStore,
        connection_store: Any,
        vault: CredentialVault,
        driver: Any,
        clock: Callable[[], datetime] | None = None,
        random_bytes: Callable[[int], bytes] | None = None,
    ) -> None:
        if manifest.provider is not ProviderId.FLOWACCOUNT:
            raise ValueError("provider_oauth_configuration_invalid")
        self._settings = settings
        self._workspace_service = workspace_service
        self._mercury_access_token = mercury_access_token
        self._principal_resolver = principal_resolver
        self._manifest = manifest
        self._oauth_client = oauth_client
        self._state_store = state_store
        self._connection_store = connection_store
        self._vault = vault
        self._driver = driver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._random_bytes = random_bytes or secrets.token_bytes

    def __repr__(self) -> str:
        return "ProviderOAuthService(provider='flowaccount')"

    async def start(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        provider: ProviderId | str,
        environment: str,
        *,
        selected_company_id: str | None = None,
    ) -> ProviderAuthorizationStart:
        try:
            await self._state_store.cleanup_expired(limit=100)
            checked_principal = MercuryPrincipal.model_validate(principal)
            checked_provider = ProviderId(provider)
            checked_selected_company_id = _checked_selected_company_id(
                selected_company_id,
                code="provider_oauth_request_invalid",
            )
            if checked_provider is not ProviderId.FLOWACCOUNT:
                raise ValueError
            access_token = self._mercury_access_token(checked_principal)
            if inspect.isawaitable(access_token):
                access_token = await access_token
            if not isinstance(access_token, str) or not access_token:
                raise ValueError
            membership = self._workspace_service.require_workspace(
                checked_principal,
                access_token,
                workspace_id,
                WorkspaceRole.MEMBER,
            )
            resource = resolve_provider_resource(
                settings=self._settings,
                manifest=self._manifest,
                environment=environment,
            )
            callback_uri = _provider_callback_uri(self._settings)
            now = self._timestamp()
            expires_at = now + _STATE_LIFETIME
            state = _b64url(self._random_exact(32))
            code_verifier = _b64url(self._random_exact(32))
            code_challenge = _b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
            session = await self._oauth_client.start_authorization(
                provider=ProviderId.FLOWACCOUNT,
                environment=environment,
                resource_uri=resource.uri,
                callback_uri=callback_uri,
                allowed_permissions=self._manifest.allowed_permissions,
                state=state,
                code_challenge=code_challenge,
            )
            session = OAuthAuthorizationSession.model_validate(session)
            if (
                session.resource_uri != resource.uri
                or session.callback_uri != callback_uri
                or not set(session.granted_permissions).issubset(self._manifest.allowed_permissions)
            ):
                raise ValueError

            state_id = uuid4()
            authorization_envelope = self._vault.seal(
                _oauth_authorization_binding(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                    state_id=state_id,
                    environment=environment,
                ),
                _serialize_authorization_payload(
                    code_verifier=code_verifier,
                    session=session,
                    selected_company_id=checked_selected_company_id,
                ),
            )
            encrypted_payload = self._vault.seal(
                _oauth_state_binding(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                    state_id=state_id,
                    environment=environment,
                ),
                _serialize_state_access(
                    mercury_access_token=access_token,
                    authorization_envelope=authorization_envelope,
                ),
            ).model_copy(update={"id": state_id})
            await self._state_store.create(
                tenant_id=membership.tenant_id,
                workspace_id=workspace_id,
                auth_user_id=checked_principal.subject,
                provider=ProviderId.FLOWACCOUNT,
                environment=environment,
                state_hash=_state_hash(state),
                callback_uri=callback_uri,
                requested_permissions=session.granted_permissions,
                expires_at=expires_at,
                encrypted_payload=encrypted_payload,
                mercury_access_token=access_token,
            )
            return ProviderAuthorizationStart(
                authorization_url=session.authorization_url,
                provider=ProviderId.FLOWACCOUNT,
                environment=environment,
                expires_at=expires_at,
            )
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_request_invalid") from None

    async def complete(
        self,
        principal: MercuryPrincipal,
        callback: OAuthCallback,
    ) -> ProviderConnectionSummary:
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            checked_callback = OAuthCallback.model_validate(callback)
        except (TypeError, ValueError, ValidationError):
            raise ProviderOAuthError("provider_oauth_callback_invalid") from None
        try:
            record = await self._state_store.peek(state_hash=_state_hash(checked_callback.state))
            access = self._open_state_access(record)
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None
        return await self._complete(
            checked_principal,
            checked_callback,
            record=record,
            mercury_access_token=access.mercury_access_token,
        )

    async def complete_callback(
        self,
        callback: OAuthCallback,
    ) -> ProviderConnectionSummary:
        try:
            checked_callback = OAuthCallback.model_validate(callback)
            record = await self._state_store.peek(state_hash=_state_hash(checked_callback.state))
            access = self._open_state_access(record)
            principal = await self._principal_resolver.resolve(access.mercury_access_token)
            if principal.subject != record.auth_user_id:
                raise ValueError
            enriched = checked_callback.model_copy(
                update={
                    "provider": record.provider,
                    "environment": record.environment,
                    "workspace_id": record.workspace_id,
                    "redirect_uri": record.callback_uri,
                }
            )
        except Exception:
            raise ProviderOAuthError("provider_oauth_state_invalid") from None
        return await self._complete(
            principal,
            enriched,
            record=record,
            mercury_access_token=access.mercury_access_token,
        )

    async def _complete(
        self,
        principal: MercuryPrincipal,
        callback: OAuthCallback,
        *,
        record: ProviderOAuthStateRecord,
        mercury_access_token: str,
    ) -> ProviderConnectionSummary:
        state_hash = _state_hash(callback.state)
        if (
            not secrets.compare_digest(record.state_hash, state_hash)
            or record.auth_user_id != principal.subject
            or callback.workspace_id != record.workspace_id
            or callback.provider is not record.provider
            or callback.environment != record.environment
        ):
            raise ProviderOAuthError("provider_oauth_state_invalid")
        if callback.redirect_uri != record.callback_uri:
            raise ProviderOAuthError("provider_oauth_callback_invalid")

        if callback.error is not None:
            await self._state_store.cancel(
                record=record,
                mercury_access_token=mercury_access_token,
            )
            raise ProviderOAuthError("provider_oauth_authorization_failed")
        if callback.code is None:
            raise ProviderOAuthError("provider_oauth_callback_invalid")

        consumed = await self._state_store.consume(
            record=record,
            mercury_access_token=mercury_access_token,
        )
        access = self._open_state_access(consumed)
        payload = self._open_authorization_payload(
            consumed,
            access.authorization_envelope,
        )
        if (
            payload.callback_uri != consumed.callback_uri
            or payload.granted_permissions != consumed.requested_permissions
        ):
            raise ProviderOAuthError("provider_oauth_state_invalid")
        attempt_id = consumed.id
        provisional_account_id = f"oauth-pending-{attempt_id}"
        self._connection_store.begin_oauth_attempt(
            attempt_id=attempt_id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            auth_user_id=record.auth_user_id,
            provider=ProviderId.FLOWACCOUNT,
            environment=record.environment,
            granted_permissions=payload.granted_permissions,
        )
        try:
            tokens = await self._oauth_client.exchange_code(
                session=payload.session(),
                code=callback.code,
                code_verifier=payload.code_verifier,
            )
            tokens = FlowAccountOAuthTokens.model_validate(tokens)
            if tokens.granted_permissions != payload.granted_permissions:
                raise ValueError
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_exchange_failed") from None

        try:
            now = self._timestamp()
            provisional = ProviderConnection(
                id=attempt_id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                provider=ProviderId.FLOWACCOUNT,
                environment=record.environment,
                provider_account_id=provisional_account_id,
                account_display_name="FlowAccount",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=tokens.granted_permissions,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                last_validated_at=None,
                credential_envelope_ids=(uuid4(),),
                created_at=now,
                updated_at=now,
            )
            provisional_envelopes = seal_flowaccount_credentials(
                vault=self._vault,
                connection=provisional,
                tokens=tokens,
                token_endpoint=payload.token_endpoint,
                resource_uri=payload.resource_uri,
                client_id=payload.client_id,
                client_secret=payload.client_secret,
                token_endpoint_auth_method=payload.token_endpoint_auth_method,
            )
            connection = self._connection_store.attach_oauth_attempt(
                attempt_id=attempt_id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                provider=ProviderId.FLOWACCOUNT,
                environment=record.environment,
                company_or_merchant_id=provisional_account_id,
                account_display_name="FlowAccount",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=tokens.granted_permissions,
                readiness=ConnectionReadiness.REQUIRES_VALIDATION,
                revision=1,
                validated_at=None,
                envelopes=provisional_envelopes,
            )

            try:
                discovery = await self._driver.discover(connection)
                if (
                    discovery.status_class is not ProviderStatusClass.SUCCESS
                    or _PROFILE_CAPABILITY not in discovery.normalized_data["capabilities"]
                ):
                    raise ValueError
                validation = await self._driver.validate_connection(connection)
                if validation.status_class is not ProviderStatusClass.SUCCESS:
                    raise ValueError
                company_id = validation.normalized_data["company_id"]
                display_name = validation.normalized_data["company_display_name"]
                if not isinstance(company_id, str) or not isinstance(display_name, str):
                    raise ValueError
                if payload.selected_company_id is not None and not secrets.compare_digest(
                    company_id,
                    payload.selected_company_id,
                ):
                    raise ProviderOAuthError("provider_oauth_company_mismatch")
            except ProviderOAuthError:
                raise
            except Exception:
                raise ProviderOAuthError("provider_oauth_validation_failed") from None

            target = self._connection_store.resolve_connection_target(
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                provider=ProviderId.FLOWACCOUNT,
                environment=record.environment,
                company_or_merchant_id=company_id,
                proposed_connection_id=uuid4(),
            )
            validated_at = self._timestamp()
            exact_connection = ProviderConnection(
                id=target.connection_id,
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                provider=ProviderId.FLOWACCOUNT,
                environment=record.environment,
                provider_account_id=company_id,
                account_display_name=display_name,
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=tokens.granted_permissions,
                readiness=ConnectionReadiness.READY,
                revision=target.revision,
                last_validated_at=validated_at,
                credential_envelope_ids=(uuid4(),),
                created_at=now,
                updated_at=validated_at,
            )
            exact_envelopes = seal_flowaccount_credentials(
                vault=self._vault,
                connection=exact_connection,
                tokens=tokens,
                token_endpoint=payload.token_endpoint,
                resource_uri=payload.resource_uri,
                client_id=payload.client_id,
                client_secret=payload.client_secret,
                token_endpoint_auth_method=payload.token_endpoint_auth_method,
            )
            finalize = {
                "attempt_id": attempt_id,
                "tenant_id": record.tenant_id,
                "workspace_id": record.workspace_id,
                "auth_user_id": record.auth_user_id,
                "connection_id": target.connection_id,
                "provider": ProviderId.FLOWACCOUNT,
                "environment": record.environment,
                "company_or_merchant_id": company_id,
                "account_display_name": display_name,
                "authorization_method": AuthorizationMethod.OAUTH2_PKCE,
                "granted_permissions": tokens.granted_permissions,
                "readiness": ConnectionReadiness.READY,
                "revision": target.revision,
                "validated_at": validated_at,
                "envelopes": exact_envelopes,
            }
            ready = self._finalize_oauth_attempt(finalize)
            return ready.summary()
        except BaseException:
            cleanup_confirmed = self._fail_oauth_attempt(
                attempt_id=attempt_id,
                record=record,
            )
            revoked = False
            if cleanup_confirmed and payload.revocation_endpoint is not None:
                try:
                    revoked = await self._oauth_client.revoke(
                        session=payload.session(),
                        tokens=tokens,
                    )
                except Exception:
                    revoked = False
            if revoked:
                with suppress(Exception):
                    self._connection_store.complete_oauth_attempt_revocation(
                        attempt_id=attempt_id,
                        tenant_id=record.tenant_id,
                        workspace_id=record.workspace_id,
                        auth_user_id=record.auth_user_id,
                        provider=ProviderId.FLOWACCOUNT,
                        environment=record.environment,
                    )
            raise

    def _finalize_oauth_attempt(
        self,
        values: Mapping[str, Any],
    ) -> ProviderConnection:
        try:
            return self._connection_store.finalize_oauth_attempt(**values)
        except ProviderStoreError as exc:
            if exc.code != "provider_store_unavailable":
                raise
        return self._connection_store.finalize_oauth_attempt(**values)

    def _fail_oauth_attempt(
        self,
        *,
        attempt_id: UUID,
        record: ProviderOAuthStateRecord,
    ) -> bool:
        for retry in range(2):
            try:
                self._connection_store.fail_oauth_attempt(
                    attempt_id=attempt_id,
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    auth_user_id=record.auth_user_id,
                    provider=ProviderId.FLOWACCOUNT,
                    environment=record.environment,
                )
                return True
            except ProviderStoreError as exc:
                if exc.code != "provider_store_unavailable" or retry == 1:
                    return False
            except Exception:
                return False
        return False

    def _open_state_access(
        self,
        record: ProviderOAuthStateRecord,
    ) -> _StoredOAuthStateAccess:
        plaintext: bytearray | None = None
        try:
            plaintext = self._vault.open(
                _oauth_state_binding(
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    auth_user_id=record.auth_user_id,
                    state_id=record.id,
                    environment=record.environment,
                ),
                record.encrypted_payload,
            )
            return _StoredOAuthStateAccess.model_validate_json(plaintext)
        except (CredentialVaultError, TypeError, ValueError, ValidationError):
            raise ProviderOAuthError("provider_oauth_state_invalid") from None
        finally:
            if plaintext is not None:
                with suppress(Exception):
                    plaintext[:] = b"\x00" * len(plaintext)

    def _open_authorization_payload(
        self,
        record: ProviderOAuthStateRecord,
        envelope_record: Mapping[str, Any],
    ) -> _StoredOAuthPayload:
        plaintext: bytearray | None = None
        try:
            envelope = _authorization_envelope_from_record(
                envelope_record,
                state=record,
            )
            plaintext = self._vault.open(
                _oauth_authorization_binding(
                    tenant_id=record.tenant_id,
                    workspace_id=record.workspace_id,
                    auth_user_id=record.auth_user_id,
                    state_id=record.id,
                    environment=record.environment,
                ),
                envelope,
            )
            return _StoredOAuthPayload.model_validate_json(plaintext)
        except (CredentialVaultError, TypeError, ValueError, ValidationError):
            raise ProviderOAuthError("provider_oauth_state_invalid") from None
        finally:
            if plaintext is not None:
                with suppress(Exception):
                    plaintext[:] = b"\x00" * len(plaintext)

    def _timestamp(self) -> datetime:
        try:
            return _aware_utc(
                self._clock(),
                code="provider_oauth_request_invalid",
            )
        except (TypeError, ValueError):
            raise ProviderOAuthError("provider_oauth_request_invalid") from None

    def _random_exact(self, size: int) -> bytes:
        value = self._random_bytes(size)
        if not isinstance(value, bytes) or len(value) != size:
            raise ProviderOAuthError("provider_oauth_request_invalid")
        return value


def _provider_callback_uri(settings: Settings) -> str:
    base = _clean_https_url(
        settings.provider_callback_base_url,
        code="provider_oauth_configuration_invalid",
    )
    parsed = urlsplit(base)
    if parsed.path not in {"", "/"}:
        raise ProviderOAuthError("provider_oauth_configuration_invalid")
    return f"{base.rstrip('/')}{FLOWACCOUNT_CALLBACK_PATH}"


def _oauth_state_binding(
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    auth_user_id: UUID,
    state_id: UUID,
    environment: str,
) -> CredentialBinding:
    return CredentialBinding(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        auth_user_id=auth_user_id,
        connection_id=state_id,
        provider=ProviderId.FLOWACCOUNT.value,
        company_or_merchant_id="oauth-state",
        environment=environment,
        credential_type="oauth_state",
    )


def _oauth_authorization_binding(
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    auth_user_id: UUID,
    state_id: UUID,
    environment: str,
) -> CredentialBinding:
    return CredentialBinding(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        auth_user_id=auth_user_id,
        connection_id=state_id,
        provider=ProviderId.FLOWACCOUNT.value,
        company_or_merchant_id="oauth-state",
        environment=environment,
        credential_type="oauth_authorization",
    )


def _serialize_state_access(
    *,
    mercury_access_token: str,
    authorization_envelope: CredentialEnvelope,
) -> bytes:
    return json.dumps(
        {
            "mercury_access_token": mercury_access_token,
            "authorization_envelope": _authorization_envelope_record(authorization_envelope),
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _serialize_authorization_payload(
    *,
    code_verifier: str,
    session: OAuthAuthorizationSession,
    selected_company_id: str | None,
) -> bytes:
    return json.dumps(
        {
            "code_verifier": code_verifier,
            "authorization_url": session.authorization_url,
            "resource_uri": session.resource_uri,
            "authorization_endpoint": session.authorization_endpoint,
            "token_endpoint": session.token_endpoint,
            "revocation_endpoint": session.revocation_endpoint,
            "callback_uri": session.callback_uri,
            "client_id": session.client_id,
            "client_secret": session.client_secret,
            "token_endpoint_auth_method": session.token_endpoint_auth_method,
            "granted_permissions": session.granted_permissions,
            "selected_company_id": selected_company_id,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


def _authorization_envelope_record(
    envelope: CredentialEnvelope,
) -> dict[str, Any]:
    checked = CredentialEnvelope.model_validate(envelope)
    return {
        "id": str(checked.id),
        "tenant_id": str(checked.tenant_id),
        "workspace_id": str(checked.workspace_id),
        "auth_user_id": str(checked.auth_user_id),
        "connection_id": str(checked.connection_id),
        "provider": checked.provider,
        "environment": checked.environment,
        "credential_type": checked.credential_type,
        "key_version": checked.key_version,
        "nonce": checked.nonce.hex(),
        "ciphertext": checked.ciphertext.hex(),
        "aad_hash": checked.aad_hash.hex(),
        "created_at": checked.created_at.isoformat(),
    }


def _authorization_envelope_from_record(
    value: Mapping[str, Any],
    *,
    state: ProviderOAuthStateRecord,
) -> CredentialEnvelope:
    envelope = CredentialEnvelope(
        id=UUID(str(value["id"])),
        tenant_id=UUID(str(value["tenant_id"])),
        workspace_id=UUID(str(value["workspace_id"])),
        auth_user_id=UUID(str(value["auth_user_id"])),
        connection_id=UUID(str(value["connection_id"])),
        provider=value["provider"],
        environment=value["environment"],
        credential_type=value["credential_type"],
        key_version=value["key_version"],
        nonce=bytes.fromhex(value["nonce"]),
        ciphertext=bytes.fromhex(value["ciphertext"]),
        aad_hash=bytes.fromhex(value["aad_hash"]),
        created_at=datetime.fromisoformat(value["created_at"]),
    )
    if (
        envelope.tenant_id != state.tenant_id
        or envelope.workspace_id != state.workspace_id
        or envelope.auth_user_id != state.auth_user_id
        or envelope.connection_id != state.id
        or envelope.provider != state.provider.value
        or envelope.environment != state.environment
        or envelope.credential_type != "oauth_authorization"
    ):
        raise ValueError
    return envelope


def _protected_resource_metadata_url(resource_uri: str) -> str:
    resource = urlsplit(resource_uri)
    suffix = "" if resource.path in {"", "/"} else resource.path
    return urlunsplit(
        (
            resource.scheme,
            resource.netloc,
            f"/.well-known/oauth-protected-resource{suffix}",
            "",
            "",
        )
    )


def _authorization_server_metadata_url(issuer: str) -> str:
    server = urlsplit(issuer)
    suffix = "" if server.path in {"", "/"} else server.path
    return urlunsplit(
        (
            server.scheme,
            server.netloc,
            f"/.well-known/oauth-authorization-server{suffix}",
            "",
            "",
        )
    )


def _trusted_server_endpoint(value: Any, *, issuer: str) -> str:
    if not isinstance(value, str):
        raise ValueError
    endpoint = _clean_https_url(
        value,
        code="provider_oauth_downstream_invalid",
    )
    if not _same_origin(endpoint, issuer):
        raise ValueError
    return endpoint


def _string_set(value: Any) -> set[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        raise ValueError
    return set(value)


def _json_value(response: httpx.Response) -> Any:
    if not 0 < len(response.content) <= _MAX_METADATA_BYTES:
        raise ValueError

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    return json.loads(response.content, object_pairs_hook=unique_object)


def _json_object(response: httpx.Response) -> dict[str, Any]:
    payload = _json_value(response)
    if not isinstance(payload, dict):
        raise ValueError
    return payload


def _single_response_row(response: httpx.Response) -> dict[str, Any]:
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError
    payload = _json_value(response)
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise ValueError
    return payload[0]


def _postgres_bytea(value: bytes) -> str:
    return f"\\x{value.hex()}"


def _decode_postgres_bytea(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith("\\x"):
        raise ValueError
    return bytes.fromhex(value[2:])


def _oauth_tokens(
    response: httpx.Response,
    *,
    allowed_permissions: tuple[str, ...],
) -> FlowAccountOAuthTokens:
    payload = _json_object(response)
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    scope = payload.get("scope")
    token_type = payload.get("token_type")
    if (
        not isinstance(access_token, str)
        or not access_token
        or (refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token))
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int | float)
        or not 0 < expires_in <= 31_536_000
        or (scope is not None and not isinstance(scope, str))
        or not isinstance(token_type, str)
        or token_type.casefold() != "bearer"
    ):
        raise ValueError
    permissions = tuple(sorted(scope.split())) if scope is not None else allowed_permissions
    if not permissions or not set(permissions).issubset(allowed_permissions):
        raise ValueError
    return FlowAccountOAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type=token_type,
        expires_at=datetime.now(UTC) + timedelta(seconds=float(expires_in)),
        granted_permissions=permissions,
    )


__all__ = [
    "DownstreamMCPOAuthClient",
    "FLOWACCOUNT_CALLBACK_PATH",
    "InMemoryProviderOAuthStateStore",
    "OAuthNetworkGuard",
    "OAuthAuthorizationSession",
    "OAuthCallback",
    "ProviderAuthorizationStart",
    "ProviderOAuthError",
    "ProviderOAuthStateRecord",
    "ProviderOAuthStateStore",
    "ProviderOAuthService",
    "PublicOAuthNetworkGuard",
    "SupabaseProviderOAuthStateStore",
]
