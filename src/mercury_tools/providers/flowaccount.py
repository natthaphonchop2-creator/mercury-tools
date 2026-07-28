"""FlowAccount normalization and encrypted OAuth request headers."""

from __future__ import annotations

import inspect
import json
import secrets
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from mercury_tools.credentials.models import CredentialBinding, CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault, CredentialVaultError
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderResponseInvalid,
    ProviderStatusClass,
    ProviderValidation,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.manifest import ProviderDriverManifest
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.streamable_mcp import (
    ProviderAuthHeader,
    ProviderAuthHeaders,
)

_PROFILE_CAPABILITY = "provider_profile.get"
_PROFILE_TOOL = "get_provider_profile"
_CREDENTIAL_TYPES = frozenset(
    {
        "access_token",
        "client_secret",
        "oauth_token_bundle",
        "refresh_token",
    }
)
_FLOWACCOUNT_CREDENTIAL_ERROR_CODES = frozenset(
    {
        "flowaccount_credentials_invalid",
        "flowaccount_reauthorization_required",
    }
)


class _FlowAccountModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _safe_text(value: str, *, code: str) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(code)
    return value


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


class FlowAccountProfileRequest(_FlowAccountModel):
    """The exact empty request for ``provider_profile.get``."""


class FlowAccountProfile(_FlowAccountModel):
    company_id: str = Field(min_length=1, max_length=512)
    company_display_name: str = Field(min_length=1, max_length=200)

    @field_validator("company_id", "company_display_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _safe_text(value, code="flowaccount_response_invalid")


class _ProviderCompany(_FlowAccountModel):
    id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=200)

    @field_validator("id", "display_name")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _safe_text(value, code="flowaccount_response_invalid")


class _ProviderProfileResponse(_FlowAccountModel):
    company: _ProviderCompany


class FlowAccountOAuthTokens(_FlowAccountModel):
    access_token: str = Field(min_length=1, max_length=16_384, repr=False, exclude=True)
    refresh_token: str | None = Field(
        default=None,
        min_length=1,
        max_length=16_384,
        repr=False,
        exclude=True,
    )
    token_type: str
    expires_at: datetime
    granted_permissions: tuple[str, ...]

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return _aware_utc(value, code="flowaccount_credentials_invalid")

    @field_validator("access_token", "refresh_token")
    @classmethod
    def validate_secret(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("flowaccount_credentials_invalid")
        return value

    @field_validator("token_type")
    @classmethod
    def validate_token_type(cls, value: str) -> str:
        if value.casefold() != "bearer":
            raise ValueError("flowaccount_credentials_invalid")
        return "Bearer"

    @field_validator("granted_permissions")
    @classmethod
    def validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or tuple(sorted(value)) != value
            or len(value) != len(set(value))
            or any(not item or len(item) > 200 for item in value)
        ):
            raise ValueError("flowaccount_credentials_invalid")
        return value


class FlowAccountRefreshRequest(_FlowAccountModel):
    token_endpoint: str = Field(repr=False, exclude=True)
    resource_uri: str = Field(repr=False, exclude=True)
    client_id: str = Field(min_length=1, max_length=1024, repr=False, exclude=True)
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
    refresh_token: str = Field(
        min_length=1,
        max_length=16_384,
        repr=False,
        exclude=True,
    )
    granted_permissions: tuple[str, ...]

    @field_validator("token_endpoint", "resource_uri")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _clean_https_url(value, code="flowaccount_credentials_invalid")


class _CredentialBundle(_FlowAccountModel):
    token_endpoint: str
    resource_uri: str
    client_id: str = Field(min_length=1, max_length=1024)
    token_endpoint_auth_method: Literal[
        "none",
        "client_secret_basic",
        "client_secret_post",
    ]
    expires_at: datetime
    granted_permissions: tuple[str, ...]

    @field_validator("token_endpoint", "resource_uri")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        return _clean_https_url(value, code="flowaccount_credentials_invalid")

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime) -> datetime:
        return _aware_utc(value, code="flowaccount_credentials_invalid")

    @field_validator("granted_permissions")
    @classmethod
    def validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or tuple(sorted(value)) != value or len(value) != len(set(value)):
            raise ValueError("flowaccount_credentials_invalid")
        return value


class FlowAccountCredentialError(RuntimeError):
    """A closed credential failure that retains no provider material."""

    def __init__(self, code: str) -> None:
        if code not in _FLOWACCOUNT_CREDENTIAL_ERROR_CODES:
            raise ValueError("flowaccount_credential_error_invalid")
        self.code = code
        super().__init__(code)


@dataclass(repr=False)
class _OpenedCredentials:
    access_token: bytearray
    refresh_token: bytearray | None
    client_secret: bytearray | None
    bundle: _CredentialBundle

    def clear(self) -> None:
        for value in (self.access_token, self.refresh_token, self.client_secret):
            if value is not None:
                with suppress(Exception):
                    value[:] = b"\x00" * len(value)


CredentialEnvelopeLoader = Callable[
    [ProviderConnection],
    Sequence[CredentialEnvelope] | Awaitable[Sequence[CredentialEnvelope]],
]
CredentialEnvelopeSaver = Callable[
    [ProviderConnection, tuple[CredentialEnvelope, ...]],
    object | Awaitable[object],
]
FlowAccountTokenRefresher = Callable[
    [FlowAccountRefreshRequest],
    FlowAccountOAuthTokens | Awaitable[FlowAccountOAuthTokens],
]
FlowAccountProfileBindingResolver = Callable[
    [ProviderConnection, str],
    QualifiedCapabilityBinding | Awaitable[QualifiedCapabilityBinding],
]


def _credential_binding(
    connection: ProviderConnection,
    credential_type: str,
) -> CredentialBinding:
    return CredentialBinding(
        tenant_id=connection.tenant_id,
        workspace_id=connection.workspace_id,
        auth_user_id=connection.auth_user_id,
        connection_id=connection.id,
        provider=connection.provider.value,
        company_or_merchant_id=connection.provider_account_id,
        environment=connection.environment,
        credential_type=credential_type,
    )


def seal_flowaccount_credentials(
    *,
    vault: CredentialVault,
    connection: ProviderConnection,
    tokens: FlowAccountOAuthTokens,
    token_endpoint: str,
    resource_uri: str,
    client_id: str,
    client_secret: str | None,
    token_endpoint_auth_method: Literal[
        "none",
        "client_secret_basic",
        "client_secret_post",
    ],
) -> tuple[CredentialEnvelope, ...]:
    """Encrypt every FlowAccount OAuth credential under the company binding."""

    try:
        checked_connection = ProviderConnection.model_validate(connection)
        checked_tokens = FlowAccountOAuthTokens.model_validate(tokens)
        bundle = _CredentialBundle(
            token_endpoint=token_endpoint,
            resource_uri=resource_uri,
            client_id=client_id,
            token_endpoint_auth_method=token_endpoint_auth_method,
            expires_at=checked_tokens.expires_at,
            granted_permissions=checked_tokens.granted_permissions,
        )
        plaintexts: dict[str, bytes] = {
            "access_token": checked_tokens.access_token.encode("utf-8"),
            "oauth_token_bundle": json.dumps(
                bundle.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii"),
        }
        if checked_tokens.refresh_token is not None:
            plaintexts["refresh_token"] = checked_tokens.refresh_token.encode("utf-8")
        if client_secret is not None:
            if (
                not isinstance(client_secret, str)
                or not client_secret
                or client_secret != client_secret.strip()
                or any(character.isspace() for character in client_secret)
            ):
                raise ValueError
            plaintexts["client_secret"] = client_secret.encode("utf-8")

        envelopes = tuple(
            vault.seal(
                _credential_binding(checked_connection, credential_type),
                plaintext,
            )
            for credential_type, plaintext in sorted(plaintexts.items())
        )
    except (CredentialVaultError, TypeError, ValueError, ValidationError):
        raise FlowAccountCredentialError("flowaccount_credentials_invalid") from None
    return envelopes


def _open_flowaccount_credentials(
    *,
    vault: CredentialVault,
    connection: ProviderConnection,
    envelopes: Sequence[CredentialEnvelope],
) -> _OpenedCredentials:
    opened: dict[str, bytearray] = {}
    try:
        checked = tuple(CredentialEnvelope.model_validate(envelope) for envelope in envelopes)
        if (
            not checked
            or len({envelope.credential_type for envelope in checked}) != len(checked)
            or any(envelope.credential_type not in _CREDENTIAL_TYPES for envelope in checked)
        ):
            raise ValueError
        for envelope in checked:
            opened[envelope.credential_type] = vault.open(
                _credential_binding(connection, envelope.credential_type),
                envelope,
            )
        access_token = opened.pop("access_token")
        bundle_plaintext = opened.pop("oauth_token_bundle")
        try:
            bundle = _CredentialBundle.model_validate_json(bundle_plaintext)
        finally:
            bundle_plaintext[:] = b"\x00" * len(bundle_plaintext)
        refresh_token = opened.pop("refresh_token", None)
        client_secret = opened.pop("client_secret", None)
        if opened:
            raise ValueError
        return _OpenedCredentials(
            access_token=access_token,
            refresh_token=refresh_token,
            client_secret=client_secret,
            bundle=bundle,
        )
    except (CredentialVaultError, KeyError, TypeError, ValueError, ValidationError):
        for value in opened.values():
            with suppress(Exception):
                value[:] = b"\x00" * len(value)
        raise FlowAccountCredentialError("flowaccount_credentials_invalid") from None


def open_flowaccount_tokens(
    *,
    vault: CredentialVault,
    connection: ProviderConnection,
    envelopes: Sequence[CredentialEnvelope],
) -> FlowAccountOAuthTokens:
    """Open the latest encrypted OAuth token generation for a bound connection."""

    opened = _open_flowaccount_credentials(
        vault=vault,
        connection=connection,
        envelopes=envelopes,
    )
    try:
        return FlowAccountOAuthTokens(
            access_token=opened.access_token.decode("utf-8"),
            refresh_token=(
                opened.refresh_token.decode("utf-8") if opened.refresh_token is not None else None
            ),
            token_type="Bearer",
            expires_at=opened.bundle.expires_at,
            granted_permissions=opened.bundle.granted_permissions,
        )
    except (TypeError, UnicodeDecodeError, ValueError, ValidationError):
        raise FlowAccountCredentialError("flowaccount_credentials_invalid") from None
    finally:
        opened.clear()


class FlowAccountOAuthHeaderFactory:
    """Open one encrypted token bundle and refresh no more than once."""

    def __init__(
        self,
        *,
        vault: CredentialVault,
        load_envelopes: CredentialEnvelopeLoader,
        save_envelopes: CredentialEnvelopeSaver,
        refresh: FlowAccountTokenRefresher,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._vault = vault
        self._load_envelopes = load_envelopes
        self._save_envelopes = save_envelopes
        self._refresh = refresh
        self._clock = clock or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return "FlowAccountOAuthHeaderFactory()"

    async def __call__(self, connection: ProviderConnection) -> ProviderAuthHeaders:
        try:
            checked = ProviderConnection.model_validate(connection)
            if (
                checked.provider is not ProviderId.FLOWACCOUNT
                or checked.authorization_method is not AuthorizationMethod.OAUTH2_PKCE
                or checked.readiness is ConnectionReadiness.DISCONNECTED
            ):
                raise ValueError
            envelopes = self._load_envelopes(checked)
            if inspect.isawaitable(envelopes):
                envelopes = await envelopes
            opened = _open_flowaccount_credentials(
                vault=self._vault,
                connection=checked,
                envelopes=envelopes,
            )
        except FlowAccountCredentialError:
            raise
        except Exception:
            raise FlowAccountCredentialError("flowaccount_credentials_invalid") from None

        try:
            now = _aware_utc(
                self._clock(),
                code="flowaccount_credentials_invalid",
            )
            access_token = opened.access_token.decode("utf-8")
            if opened.bundle.expires_at <= now:
                if opened.refresh_token is None:
                    raise FlowAccountCredentialError("flowaccount_reauthorization_required")
                refresh_request = FlowAccountRefreshRequest(
                    token_endpoint=opened.bundle.token_endpoint,
                    resource_uri=opened.bundle.resource_uri,
                    client_id=opened.bundle.client_id,
                    client_secret=(
                        opened.client_secret.decode("utf-8")
                        if opened.client_secret is not None
                        else None
                    ),
                    token_endpoint_auth_method=(opened.bundle.token_endpoint_auth_method),
                    refresh_token=opened.refresh_token.decode("utf-8"),
                    granted_permissions=opened.bundle.granted_permissions,
                )
                refreshed = self._refresh(refresh_request)
                if inspect.isawaitable(refreshed):
                    refreshed = await refreshed
                refreshed = FlowAccountOAuthTokens.model_validate(refreshed)
                if (
                    refreshed.expires_at <= now
                    or refreshed.granted_permissions != opened.bundle.granted_permissions
                ):
                    raise FlowAccountCredentialError("flowaccount_reauthorization_required")
                if refreshed.refresh_token is None:
                    refreshed = refreshed.model_copy(
                        update={"refresh_token": opened.refresh_token.decode("utf-8")}
                    )
                replacement = seal_flowaccount_credentials(
                    vault=self._vault,
                    connection=checked,
                    tokens=refreshed,
                    token_endpoint=opened.bundle.token_endpoint,
                    resource_uri=opened.bundle.resource_uri,
                    client_id=opened.bundle.client_id,
                    client_secret=(
                        opened.client_secret.decode("utf-8")
                        if opened.client_secret is not None
                        else None
                    ),
                    token_endpoint_auth_method=(opened.bundle.token_endpoint_auth_method),
                )
                saved = self._save_envelopes(checked, replacement)
                if inspect.isawaitable(saved):
                    await saved
                access_token = refreshed.access_token
            return ProviderAuthHeaders(
                provider=ProviderId.FLOWACCOUNT,
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                headers=(
                    ProviderAuthHeader(
                        name="Authorization",
                        value=f"Bearer {access_token}",
                    ),
                ),
            )
        except FlowAccountCredentialError:
            raise
        except Exception:
            raise FlowAccountCredentialError("flowaccount_credentials_invalid") from None
        finally:
            opened.clear()


def normalize_flowaccount_response(
    binding: Any,
    structured_content: Mapping[str, Any],
) -> BaseModel:
    """Normalize only reviewed FlowAccount MCP response shapes."""

    try:
        if (
            getattr(binding, "provider", None) is not ProviderId.FLOWACCOUNT
            or getattr(binding, "normalized_capability", None) != _PROFILE_CAPABILITY
            or getattr(binding, "provider_tool", None) != _PROFILE_TOOL
        ):
            raise ValueError
        raw = _ProviderProfileResponse.model_validate(structured_content)
        return FlowAccountProfile(
            company_id=raw.company.id,
            company_display_name=raw.company.display_name,
        )
    except (TypeError, ValueError, ValidationError):
        raise ValueError("flowaccount_response_invalid") from None


class FlowAccountMCPDriver:
    """FlowAccount-specific guard around the shared Streamable MCP runtime."""

    provider = ProviderId.FLOWACCOUNT

    def __init__(
        self,
        *,
        runtime: Any,
        manifest: ProviderDriverManifest,
        profile_binding_resolver: FlowAccountProfileBindingResolver,
    ) -> None:
        checked_manifest = ProviderDriverManifest.model_validate(manifest.model_dump(mode="json"))
        if checked_manifest.provider is not ProviderId.FLOWACCOUNT:
            raise ValueError("flowaccount_driver_manifest_invalid")
        if getattr(runtime, "provider", None) is not ProviderId.FLOWACCOUNT:
            raise ValueError("flowaccount_driver_runtime_invalid")
        self._runtime = runtime
        self._manifest = checked_manifest
        self._profile_binding_resolver = profile_binding_resolver
        self._mappings = {
            mapping.normalized_capability: mapping.provider_tool
            for mapping in checked_manifest.discovery_mappings
        }

    def __repr__(self) -> str:
        return "FlowAccountMCPDriver()"

    async def discover(self, connection: ProviderConnection) -> ProviderDiscovery:
        checked = self._connection(connection, allow_validation=True)
        result = await self._runtime.discover(checked)
        try:
            capabilities = result.normalized_data["capabilities"]
            if isinstance(capabilities, (str, bytes, bytearray)):
                raise TypeError
            normalized = tuple(capabilities)
            if tuple(sorted(normalized)) != normalized or any(
                capability not in self._mappings for capability in normalized
            ):
                raise ValueError
            return ProviderDiscovery(
                provider=ProviderId.FLOWACCOUNT,
                status_class=result.status_class,
                normalized_data={
                    **dict(result.normalized_data),
                    "capabilities": list(normalized),
                },
                dispatch_certainty=result.dispatch_certainty,
            )
        except Exception:
            raise self._invalid() from None

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation:
        checked = self._connection(connection, allow_validation=True)
        provider_tool = self._mappings.get(_PROFILE_CAPABILITY)
        if provider_tool != _PROFILE_TOOL:
            raise self._invalid()
        try:
            binding = self._profile_binding_resolver(checked, provider_tool)
            if inspect.isawaitable(binding):
                binding = await binding
            binding = QualifiedCapabilityBinding.model_validate(binding)
            if (
                binding.provider is not ProviderId.FLOWACCOUNT
                or binding.environment != checked.environment
                or binding.normalized_capability != _PROFILE_CAPABILITY
                or binding.provider_tool != provider_tool
                or binding.operation_class is not ProviderOperationClass.READ
            ):
                raise ValueError
            result = await self._runtime.call(
                checked,
                binding,
                FlowAccountProfileRequest(),
                uuid4(),
            )
            if result.status_class is not ProviderStatusClass.SUCCESS:
                raise ValueError
            profile = FlowAccountProfile.model_validate(result.normalized_data)
            return ProviderValidation(
                provider=ProviderId.FLOWACCOUNT,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data=profile.model_dump(mode="json"),
                dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
            )
        except ProviderResponseInvalid:
            raise
        except Exception:
            raise self._invalid() from None

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id,
    ) -> ProviderCallResult:
        checked = self._connection(connection, allow_validation=False)
        try:
            checked_binding = QualifiedCapabilityBinding.model_validate(binding)
            expected_tool = self._mappings[checked_binding.normalized_capability]
            if (
                checked_binding.provider is not ProviderId.FLOWACCOUNT
                or checked_binding.environment != checked.environment
                or not secrets.compare_digest(
                    checked_binding.provider_tool,
                    expected_tool,
                )
            ):
                raise ValueError
        except Exception:
            raise self._invalid() from None
        return await self._runtime.call(
            checked,
            checked_binding,
            arguments,
            operation_id,
        )

    def _connection(
        self,
        connection: ProviderConnection,
        *,
        allow_validation: bool,
    ) -> ProviderConnection:
        try:
            checked = ProviderConnection.model_validate(connection)
            allowed = {ConnectionReadiness.READY}
            if allow_validation:
                allowed.add(ConnectionReadiness.REQUIRES_VALIDATION)
            if (
                checked.provider is not ProviderId.FLOWACCOUNT
                or checked.authorization_method is not AuthorizationMethod.OAUTH2_PKCE
                or checked.environment not in self._manifest.environments
                or checked.readiness not in allowed
            ):
                raise ValueError
            return checked
        except Exception:
            raise self._invalid() from None

    @staticmethod
    def _invalid() -> ProviderResponseInvalid:
        return ProviderResponseInvalid(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
        )


__all__ = [
    "CredentialEnvelopeLoader",
    "CredentialEnvelopeSaver",
    "FlowAccountCredentialError",
    "FlowAccountMCPDriver",
    "FlowAccountOAuthHeaderFactory",
    "FlowAccountOAuthTokens",
    "FlowAccountProfile",
    "FlowAccountProfileBindingResolver",
    "FlowAccountProfileRequest",
    "FlowAccountRefreshRequest",
    "FlowAccountTokenRefresher",
    "normalize_flowaccount_response",
    "open_flowaccount_tokens",
    "seal_flowaccount_credentials",
]
