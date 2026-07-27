"""FlowAccount authorization through downstream MCP OAuth metadata."""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import re
import secrets
import threading
import unicodedata
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from mercury_tools.auth.models import MercuryPrincipal, PrincipalResolver
from mercury_tools.config import Settings
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
from mercury_tools.providers.store import ProviderConnectionStore
from mercury_tools.workspaces.models import WorkspaceRole

FLOWACCOUNT_CALLBACK_PATH = "/auth/providers/flowaccount/callback"
_STATE_LIFETIME = timedelta(minutes=10)
_PROFILE_CAPABILITY = "provider_profile.get"
_MAX_METADATA_BYTES = 128 * 1024
_OAUTH_ERROR_CODES = frozenset(
    {
        "provider_oauth_callback_invalid",
        "provider_oauth_company_mismatch",
        "provider_oauth_configuration_invalid",
        "provider_oauth_downstream_invalid",
        "provider_oauth_exchange_failed",
        "provider_oauth_request_invalid",
        "provider_oauth_state_invalid",
        "provider_oauth_validation_failed",
    }
)
_WWW_RESOURCE_METADATA = re.compile(r'(?:^|[\s,])resource_metadata="([^"\\]+)"')
_WWW_SCOPE = re.compile(r'(?:^|[\s,])scope="([^"\\]+)"')


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


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _state_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


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
    code: str = Field(
        min_length=1,
        max_length=16_384,
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
    selected_company_id: str = Field(
        min_length=1,
        max_length=512,
        repr=False,
        exclude=True,
    )

    @field_validator("code", "state")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("provider_oauth_callback_invalid")
        return value

    @field_validator("redirect_uri")
    @classmethod
    def validate_redirect(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _clean_https_url(value, code="provider_oauth_callback_invalid")

    @field_validator("selected_company_id")
    @classmethod
    def validate_company(cls, value: str) -> str:
        if any(
            character.isspace() or unicodedata.category(character) in {"Cc", "Cf"}
            for character in value
        ):
            raise ValueError("provider_oauth_callback_invalid")
        return value


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
    mercury_access_token: str = Field(repr=False, exclude=True)
    code_verifier: str = Field(repr=False, exclude=True)
    authorization_url: str
    resource_uri: str
    authorization_endpoint: str
    token_endpoint: str
    callback_uri: str
    client_id: str
    client_secret: str | None = Field(default=None, repr=False, exclude=True)
    token_endpoint_auth_method: Literal[
        "none",
        "client_secret_basic",
        "client_secret_post",
    ]
    granted_permissions: tuple[str, ...]

    def session(self) -> OAuthAuthorizationSession:
        return OAuthAuthorizationSession(
            authorization_url=self.authorization_url,
            resource_uri=self.resource_uri,
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            callback_uri=self.callback_uri,
            client_id=self.client_id,
            client_secret=self.client_secret,
            token_endpoint_auth_method=self.token_endpoint_auth_method,
            granted_permissions=self.granted_permissions,
        )


@dataclass(frozen=True, repr=False)
class _OAuthStateRecord:
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    auth_user_id: UUID
    provider: ProviderId
    environment: str
    state_hash: str
    callback_uri: str
    expires_at: datetime
    encrypted_payload: CredentialEnvelope
    consumed_at: datetime | None = None


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
        self._states: dict[str, _OAuthStateRecord] = {}

    def __repr__(self) -> str:
        return "InMemoryProviderOAuthStateStore()"

    def create(
        self,
        *,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
        state_hash: str,
        callback_uri: str,
        expires_at: datetime,
        encrypted_payload: CredentialEnvelope,
    ) -> _OAuthStateRecord:
        try:
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
            record = _OAuthStateRecord(
                id=encrypted_payload.connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=auth_user_id,
                provider=provider,
                environment=environment,
                state_hash=state_hash,
                callback_uri=callback_uri,
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

    def peek(self, *, state_hash: str) -> _OAuthStateRecord:
        now = self._timestamp()
        with self._lock:
            record = self._states.get(state_hash)
            if record is None or record.consumed_at is not None or record.expires_at <= now:
                raise ProviderOAuthError("provider_oauth_state_invalid")
            return record

    def consume(
        self,
        *,
        state_hash: str,
        tenant_id: UUID,
        workspace_id: UUID,
        auth_user_id: UUID,
        provider: ProviderId,
        environment: str,
    ) -> _OAuthStateRecord:
        now = self._timestamp()
        with self._lock:
            record = self._states.get(state_hash)
            if (
                record is None
                or record.consumed_at is not None
                or record.expires_at <= now
                or record.tenant_id != tenant_id
                or record.workspace_id != workspace_id
                or record.auth_user_id != auth_user_id
                or record.provider is not provider
                or not secrets.compare_digest(record.environment, environment)
            ):
                raise ProviderOAuthError("provider_oauth_state_invalid")
            consumed = _OAuthStateRecord(
                **{
                    **record.__dict__,
                    "consumed_at": now,
                }
            )
            del self._states[state_hash]
            return consumed

    def _timestamp(self) -> datetime:
        try:
            return _aware_utc(
                self._clock(),
                code="provider_oauth_state_invalid",
            )
        except (TypeError, ValueError):
            raise ProviderOAuthError("provider_oauth_state_invalid") from None


class DownstreamMCPOAuthClient:
    """Strict OAuth discovery rooted only at the configured MCP resource."""

    def __init__(self, *, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    def __repr__(self) -> str:
        return "DownstreamMCPOAuthClient()"

    async def start_authorization(
        self,
        *,
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
            if tuple(sorted(allowed_permissions)) != allowed_permissions or not allowed_permissions:
                raise ValueError

            challenge_response = await self._http.get(
                resource_uri,
                headers={"MCP-Protocol-Version": "2025-11-25"},
                follow_redirects=False,
            )
            challenge = challenge_response.headers.get("www-authenticate", "")
            if "bearer" not in challenge.casefold():
                raise ValueError
            metadata_match = _WWW_RESOURCE_METADATA.search(challenge)
            if metadata_match is None:
                raise ValueError
            resource_metadata_url = metadata_match.group(1)
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

            supported = _string_set(
                resource_metadata.get("scopes_supported"),
            )
            scope_match = _WWW_SCOPE.search(challenge)
            if scope_match is not None:
                challenged = set(scope_match.group(1).split())
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

    async def _metadata(self, url: str) -> dict[str, Any]:
        response = await self._http.get(url, follow_redirects=False)
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
        response = await self._http.post(
            registration_endpoint,
            json={
                "redirect_uris": [callback_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": preferred,
            },
            follow_redirects=False,
        )
        if response.status_code not in {200, 201}:
            raise ValueError
        payload = _json_object(response)
        client_id = payload.get("client_id")
        client_secret = payload.get("client_secret")
        auth_method = payload.get("token_endpoint_auth_method")
        redirect_uris = payload.get("redirect_uris")
        if (
            not isinstance(client_id, str)
            or not client_id
            or auth_method != preferred
            or not isinstance(redirect_uris, list)
            or callback_uri not in redirect_uris
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
        response = await self._http.post(
            session.token_endpoint,
            data=form,
            auth=auth,
            follow_redirects=False,
        )
        if response.status_code != 200:
            raise ValueError
        return response


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
        state_store: InMemoryProviderOAuthStateStore,
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
    ) -> ProviderAuthorizationStart:
        try:
            checked_principal = MercuryPrincipal.model_validate(principal)
            checked_provider = ProviderId(provider)
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
            encrypted_payload = self._vault.seal(
                _oauth_state_binding(
                    tenant_id=membership.tenant_id,
                    workspace_id=workspace_id,
                    auth_user_id=checked_principal.subject,
                    state_id=state_id,
                    environment=environment,
                ),
                _serialize_state_payload(
                    mercury_access_token=access_token,
                    code_verifier=code_verifier,
                    session=session,
                ),
            )
            self._state_store.create(
                tenant_id=membership.tenant_id,
                workspace_id=workspace_id,
                auth_user_id=checked_principal.subject,
                provider=ProviderId.FLOWACCOUNT,
                environment=environment,
                state_hash=_state_hash(state),
                callback_uri=callback_uri,
                expires_at=expires_at,
                encrypted_payload=encrypted_payload,
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
        return await self._complete(checked_principal, checked_callback)

    async def complete_callback(
        self,
        callback: OAuthCallback,
    ) -> ProviderConnectionSummary:
        try:
            checked_callback = OAuthCallback.model_validate(callback)
            record = self._state_store.peek(state_hash=_state_hash(checked_callback.state))
            payload = self._open_state_payload(record)
            principal = await self._principal_resolver.resolve(payload.mercury_access_token)
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
        return await self._complete(principal, enriched)

    async def _complete(
        self,
        principal: MercuryPrincipal,
        callback: OAuthCallback,
    ) -> ProviderConnectionSummary:
        state_hash = _state_hash(callback.state)
        record = self._state_store.peek(state_hash=state_hash)
        if (
            record.auth_user_id != principal.subject
            or callback.workspace_id != record.workspace_id
            or callback.provider is not record.provider
            or callback.environment != record.environment
        ):
            raise ProviderOAuthError("provider_oauth_state_invalid")
        if callback.redirect_uri != record.callback_uri:
            raise ProviderOAuthError("provider_oauth_callback_invalid")

        payload = self._open_state_payload(record)
        self._state_store.consume(
            state_hash=state_hash,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            auth_user_id=record.auth_user_id,
            provider=record.provider,
            environment=record.environment,
        )
        try:
            tokens = await self._oauth_client.exchange_code(
                session=payload.session(),
                code=callback.code,
                code_verifier=payload.code_verifier,
            )
            tokens = FlowAccountOAuthTokens.model_validate(tokens)
            if not set(tokens.granted_permissions).issubset(payload.granted_permissions):
                raise ValueError
        except ProviderOAuthError:
            raise
        except Exception:
            raise ProviderOAuthError("provider_oauth_exchange_failed") from None

        connection_id = uuid4()
        now = self._timestamp()
        provisional = ProviderConnection(
            id=connection_id,
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            auth_user_id=record.auth_user_id,
            provider=ProviderId.FLOWACCOUNT,
            environment=record.environment,
            provider_account_id=callback.selected_company_id,
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
        envelopes = seal_flowaccount_credentials(
            vault=self._vault,
            connection=provisional,
            tokens=tokens,
            token_endpoint=payload.token_endpoint,
            resource_uri=payload.resource_uri,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            token_endpoint_auth_method=payload.token_endpoint_auth_method,
        )
        connection = self._connection_store.save_connection(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            auth_user_id=record.auth_user_id,
            connection_id=connection_id,
            provider=ProviderId.FLOWACCOUNT,
            environment=record.environment,
            company_or_merchant_id=callback.selected_company_id,
            account_display_name="FlowAccount",
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=tokens.granted_permissions,
            readiness=ConnectionReadiness.REQUIRES_VALIDATION,
            revision=1,
            validated_at=None,
            envelopes=envelopes,
        )

        display_name = "FlowAccount"
        failure: ProviderOAuthError | None = None
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
            if not secrets.compare_digest(
                company_id,
                callback.selected_company_id,
            ):
                failure = ProviderOAuthError("provider_oauth_company_mismatch")
        except ProviderOAuthError as exc:
            failure = exc
        except Exception:
            failure = ProviderOAuthError("provider_oauth_validation_failed")

        if failure is not None:
            self._connection_store.save_connection(
                tenant_id=record.tenant_id,
                workspace_id=record.workspace_id,
                auth_user_id=record.auth_user_id,
                connection_id=connection_id,
                provider=ProviderId.FLOWACCOUNT,
                environment=record.environment,
                company_or_merchant_id=callback.selected_company_id,
                account_display_name="FlowAccount",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=tokens.granted_permissions,
                readiness=ConnectionReadiness.VALIDATION_FAILED,
                revision=2,
                validated_at=None,
                envelopes=envelopes,
            )
            raise failure

        ready = self._connection_store.save_connection(
            tenant_id=record.tenant_id,
            workspace_id=record.workspace_id,
            auth_user_id=record.auth_user_id,
            connection_id=connection_id,
            provider=ProviderId.FLOWACCOUNT,
            environment=record.environment,
            company_or_merchant_id=callback.selected_company_id,
            account_display_name=display_name,
            authorization_method=AuthorizationMethod.OAUTH2_PKCE,
            granted_permissions=tokens.granted_permissions,
            readiness=ConnectionReadiness.READY,
            revision=2,
            validated_at=self._timestamp(),
            envelopes=envelopes,
        )
        return ready.summary()

    def _open_state_payload(
        self,
        record: _OAuthStateRecord,
    ) -> _StoredOAuthPayload:
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


def _serialize_state_payload(
    *,
    mercury_access_token: str,
    code_verifier: str,
    session: OAuthAuthorizationSession,
) -> bytes:
    return json.dumps(
        {
            "mercury_access_token": mercury_access_token,
            "code_verifier": code_verifier,
            "authorization_url": session.authorization_url,
            "resource_uri": session.resource_uri,
            "authorization_endpoint": session.authorization_endpoint,
            "token_endpoint": session.token_endpoint,
            "callback_uri": session.callback_uri,
            "client_id": session.client_id,
            "client_secret": session.client_secret,
            "token_endpoint_auth_method": session.token_endpoint_auth_method,
            "granted_permissions": session.granted_permissions,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")


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


def _json_object(response: httpx.Response) -> dict[str, Any]:
    if not 0 < len(response.content) <= _MAX_METADATA_BYTES:
        raise ValueError

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    payload = json.loads(response.content, object_pairs_hook=unique_object)
    if not isinstance(payload, dict):
        raise ValueError
    return payload


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
    if (
        not isinstance(access_token, str)
        or not access_token
        or (refresh_token is not None and (not isinstance(refresh_token, str) or not refresh_token))
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, int | float)
        or not 0 < expires_in <= 31_536_000
        or (scope is not None and not isinstance(scope, str))
    ):
        raise ValueError
    permissions = tuple(sorted(scope.split())) if scope is not None else allowed_permissions
    if not permissions or not set(permissions).issubset(allowed_permissions):
        raise ValueError
    return FlowAccountOAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=datetime.now(UTC) + timedelta(seconds=float(expires_in)),
        granted_permissions=permissions,
    )


__all__ = [
    "DownstreamMCPOAuthClient",
    "FLOWACCOUNT_CALLBACK_PATH",
    "InMemoryProviderOAuthStateStore",
    "OAuthAuthorizationSession",
    "OAuthCallback",
    "ProviderAuthorizationStart",
    "ProviderOAuthError",
    "ProviderOAuthService",
]
