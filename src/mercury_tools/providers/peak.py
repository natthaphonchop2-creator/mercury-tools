"""Fail-closed PEAK MCP credential and reviewed-contract boundary."""

from __future__ import annotations

import inspect
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mercury_tools.credentials.models import CredentialBinding, CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderQualificationState,
    ProviderResponseInvalid,
    ProviderStatusClass,
    ProviderValidation,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
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
    wire_schema_sha256,
)
from mercury_tools.qualification.provider_mcp import CatalogQualificationResolver

_PROFILE_CAPABILITY = "provider_profile.get"
_CREDENTIAL_TYPES = ("connect_id", "connect_key", "user_token")
_CREDENTIAL_TYPE_SET = frozenset(_CREDENTIAL_TYPES)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIXTURE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CREDENTIAL_ERROR_CODES = frozenset(
    {
        "peak_credentials_invalid",
        "peak_provider_contract_invalid",
        "peak_provider_contract_unqualified",
    }
)


class _PeakModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class PeakCredentialError(RuntimeError):
    """Closed PEAK credential/contract failure without rejected material."""

    def __init__(self, code: str) -> None:
        if code not in _CREDENTIAL_ERROR_CODES:
            raise ValueError("peak_credential_error_invalid")
        self.code = code
        super().__init__(code)


class PeakProfile(_PeakModel):
    """Canonical merchant identity produced only by a reviewed profile fixture."""

    merchant_id: str = Field(min_length=1, max_length=512, repr=False, exclude=True)
    merchant_display_name: str = Field(min_length=1, max_length=200)

    @field_validator("merchant_id", "merchant_display_name")
    @classmethod
    def reject_unsafe_text(cls, value: str) -> str:
        if value != value.strip() or any(
            unicodedata.category(character) in {"Cc", "Cf"} for character in value
        ):
            raise ValueError("peak_profile_invalid")
        return value


@dataclass(repr=False)
class PeakCredentialMaterial:
    """Mutable request-scoped credential bytes that can be cleared in place."""

    user_token: bytearray
    connect_id: bytearray
    connect_key: bytearray

    @classmethod
    def from_values(
        cls,
        *,
        user_token: str,
        connect_id: str,
        connect_key: str,
    ) -> PeakCredentialMaterial:
        values: dict[str, bytearray] = {}
        failed = False
        try:
            values["user_token"] = _credential_bytes(user_token)
            values["connect_id"] = _credential_bytes(connect_id)
            values["connect_key"] = _credential_bytes(connect_key)
        except Exception:
            failed = True
        if failed:
            for value in values.values():
                with suppress(Exception):
                    value[:] = b"\x00" * len(value)
            values.clear()
            del user_token
            del connect_id
            del connect_key
            raise PeakCredentialError("peak_credentials_invalid")
        return cls(**values)

    @property
    def cleared(self) -> bool:
        return all(not any(value) for value in self._values())

    def clear(self) -> None:
        for value in self._values():
            with suppress(Exception):
                value[:] = b"\x00" * len(value)

    def _values(self) -> tuple[bytearray, bytearray, bytearray]:
        return (self.user_token, self.connect_id, self.connect_key)


PeakProfileNormalizer = Callable[[BaseModel], PeakProfile]


class QualifiedPeakProviderContract:
    """Explicit reviewed fixture for unpublished PEAK MCP wire details."""

    def __init__(
        self,
        *,
        fixture_id: str,
        qualification_hash: str,
        resource_uri_sha256_by_environment: Mapping[str, str],
        credential_header_names: Mapping[str, str],
        application_code_header_name: str | None,
        profile_tool: str,
        profile_request_model: type[BaseModel],
        profile_response_model: type[BaseModel],
        profile_normalizer: PeakProfileNormalizer,
        review_status: Literal["reviewed_qualified"] = "reviewed_qualified",
    ) -> None:
        try:
            if (
                review_status != "reviewed_qualified"
                or not isinstance(fixture_id, str)
                or _FIXTURE_ID.fullmatch(fixture_id) is None
                or not isinstance(qualification_hash, str)
                or _SHA256.fullmatch(qualification_hash) is None
                or not isinstance(resource_uri_sha256_by_environment, Mapping)
                or not resource_uri_sha256_by_environment
                or any(
                    not isinstance(environment, str)
                    or not environment
                    or not isinstance(resource_hash, str)
                    or _SHA256.fullmatch(resource_hash) is None
                    for environment, resource_hash in resource_uri_sha256_by_environment.items()
                )
                or not isinstance(credential_header_names, Mapping)
                or set(credential_header_names) != _CREDENTIAL_TYPE_SET
                or not isinstance(profile_tool, str)
                or _FIXTURE_ID.fullmatch(profile_tool) is None
                or not callable(profile_normalizer)
            ):
                raise ValueError
            checked_headers = {
                credential_type: ProviderAuthHeader(
                    name=credential_header_names[credential_type],
                    value="fixture-validation",
                ).name
                for credential_type in _CREDENTIAL_TYPES
            }
            if len({value.casefold() for value in checked_headers.values()}) != len(
                checked_headers
            ):
                raise ValueError
            checked_application_header = None
            if application_code_header_name is not None:
                checked_application_header = ProviderAuthHeader(
                    name=application_code_header_name,
                    value="fixture-validation",
                ).name
                if application_code_header_name.casefold() in {
                    value.casefold() for value in checked_headers.values()
                }:
                    raise ValueError
            request_hash = wire_schema_sha256(profile_request_model)
            response_hash = wire_schema_sha256(profile_response_model)
        except Exception:
            raise PeakCredentialError("peak_provider_contract_invalid") from None

        self.fixture_id = fixture_id
        self.qualification_hash = qualification_hash
        self.resource_uri_sha256_by_environment = MappingProxyType(
            dict(resource_uri_sha256_by_environment)
        )
        self.credential_header_names = MappingProxyType(checked_headers)
        self.application_code_header_name = checked_application_header
        self.profile_tool = profile_tool
        self.profile_request_model = profile_request_model
        self.profile_response_model = profile_response_model
        self.profile_normalizer = profile_normalizer
        self.profile_request_schema_sha256 = request_hash
        self.profile_response_schema_sha256 = response_hash
        self.review_status = review_status

    def __repr__(self) -> str:
        return (
            "QualifiedPeakProviderContract("
            f"fixture_id={self.fixture_id!r}, "
            f"review_status={self.review_status!r}"
            ")"
        )

    def authorization_headers(
        self,
        credentials: PeakCredentialMaterial,
        *,
        application_code: str,
    ) -> ProviderAuthHeaders:
        result: ProviderAuthHeaders | None = None
        values: dict[str, str] = {}
        headers: list[ProviderAuthHeader] = []
        failed = False
        try:
            if credentials.cleared:
                raise ValueError
            for credential_type in _CREDENTIAL_TYPES:
                values[credential_type] = _decode_credential(getattr(credentials, credential_type))
                headers.append(
                    ProviderAuthHeader(
                        name=self.credential_header_names[credential_type],
                        value=values[credential_type],
                    )
                )
            if self.application_code_header_name is not None:
                if not isinstance(application_code, str) or not application_code:
                    raise ValueError
                headers.append(
                    ProviderAuthHeader(
                        name=self.application_code_header_name,
                        value=application_code,
                    )
                )
            result = ProviderAuthHeaders(
                provider=ProviderId.PEAK,
                authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS,
                headers=tuple(sorted(headers, key=lambda item: item.name.casefold())),
            )
        except Exception:
            failed = True
        if failed or result is None:
            credentials.clear()
            values.clear()
            headers.clear()
            del credentials
            del application_code
            del self
            raise PeakCredentialError("peak_credentials_invalid")
        return result

    def request_model(self, binding: VerifiedRuntimeBinding) -> type[BaseModel]:
        self._require_profile_binding(binding)
        return self.profile_request_model

    def response_model(self, binding: VerifiedRuntimeBinding) -> type[BaseModel]:
        self._require_profile_binding(binding)
        return self.profile_response_model

    def normalize_profile(
        self,
        binding: VerifiedRuntimeBinding,
        structured_content: Mapping[str, Any],
    ) -> PeakProfile:
        self._require_profile_binding(binding)
        normalized: PeakProfile | None = None
        failed = False
        try:
            response = self.profile_response_model.model_validate(structured_content)
            normalized = PeakProfile.model_validate(self.profile_normalizer(response))
        except Exception:
            failed = True
        if failed or normalized is None:
            if "response" in locals():
                del response
            del structured_content
            del self
            raise ValueError("peak_profile_invalid")
        return normalized

    def _require_profile_binding(self, binding: VerifiedRuntimeBinding) -> None:
        expected_resource = self.resource_uri_sha256_by_environment.get(binding.environment)
        if (
            binding.qualification_state is not ProviderQualificationState.ENABLED
            or binding.provider is not ProviderId.PEAK
            or binding.normalized_capability != _PROFILE_CAPABILITY
            or binding.provider_tool != self.profile_tool
            or binding.operation_class is not ProviderOperationClass.READ
            or expected_resource is None
            or binding.resource_uri_sha256 != expected_resource
            or binding.request_schema_sha256 != self.profile_request_schema_sha256
            or binding.response_schema_sha256 != self.profile_response_schema_sha256
        ):
            raise PeakCredentialError("peak_provider_contract_invalid")


PeakEnvelopeLoader = Callable[
    [ProviderConnection],
    Sequence[CredentialEnvelope] | Awaitable[Sequence[CredentialEnvelope]],
]


def seal_peak_credentials(
    *,
    vault: CredentialVault,
    connection: ProviderConnection,
    credentials: PeakCredentialMaterial,
) -> tuple[CredentialEnvelope, ...]:
    """Seal the three canonical PEAK MCP credentials under one merchant binding."""

    result: tuple[CredentialEnvelope, ...] | None = None
    failed = False
    try:
        checked = _peak_connection(connection, allow_validation=True)
        if not isinstance(credentials, PeakCredentialMaterial) or credentials.cleared:
            raise ValueError
        result = tuple(
            vault.seal(
                _credential_binding(checked, credential_type),
                getattr(credentials, credential_type),
            )
            for credential_type in _CREDENTIAL_TYPES
        )
    except Exception:
        failed = True
    if failed or result is None:
        if isinstance(credentials, PeakCredentialMaterial):
            credentials.clear()
        if result is not None:
            del result
        del credentials
        del connection
        del vault
        raise PeakCredentialError("peak_credentials_invalid")
    return result


@contextmanager
def open_peak_credentials(
    *,
    vault: CredentialVault,
    connection: ProviderConnection,
    envelopes: Sequence[CredentialEnvelope],
):
    """Open PEAK credentials for one request and clear every buffer on exit."""

    opened: dict[str, bytearray] = {}
    material: PeakCredentialMaterial | None = None
    failed = False
    try:
        checked_connection = _peak_connection(connection, allow_validation=True)
        checked_envelopes = tuple(
            CredentialEnvelope.model_validate(envelope) for envelope in envelopes
        )
        if (
            len(checked_envelopes) != len(_CREDENTIAL_TYPES)
            or {envelope.credential_type for envelope in checked_envelopes} != _CREDENTIAL_TYPE_SET
        ):
            raise ValueError
        for envelope in checked_envelopes:
            opened[envelope.credential_type] = vault.open(
                _credential_binding(checked_connection, envelope.credential_type),
                envelope,
            )
        material = PeakCredentialMaterial(
            user_token=opened.pop("user_token"),
            connect_id=opened.pop("connect_id"),
            connect_key=opened.pop("connect_key"),
        )
    except Exception:
        failed = True
    if failed or material is None:
        if material is not None:
            material.clear()
        for value in opened.values():
            with suppress(Exception):
                value[:] = b"\x00" * len(value)
        if "checked_envelopes" in locals():
            del checked_envelopes
        if "checked_connection" in locals():
            del checked_connection
        del opened
        del material
        del envelopes
        del connection
        del vault
        raise PeakCredentialError("peak_credentials_invalid")
    try:
        yield material
    finally:
        material.clear()
        for value in opened.values():
            with suppress(Exception):
                value[:] = b"\x00" * len(value)


class PeakCredentialHeaderFactory:
    """Derive PEAK headers only through an explicit reviewed contract fixture."""

    def __init__(
        self,
        *,
        vault: CredentialVault,
        load_envelopes: PeakEnvelopeLoader,
        contract: QualifiedPeakProviderContract,
        application_code: str = "",
    ) -> None:
        if (
            not isinstance(vault, CredentialVault)
            or not callable(load_envelopes)
            or not isinstance(contract, QualifiedPeakProviderContract)
            or not isinstance(application_code, str)
        ):
            raise PeakCredentialError("peak_provider_contract_invalid")
        self._vault = vault
        self._load_envelopes = load_envelopes
        self._contract = contract
        self._application_code = application_code
        self._request_envelopes: ContextVar[
            tuple[ProviderConnection, tuple[CredentialEnvelope, ...]] | None
        ] = ContextVar("mercury_peak_request_envelopes", default=None)

    def __repr__(self) -> str:
        return "PeakCredentialHeaderFactory()"

    async def __call__(self, connection: ProviderConnection) -> ProviderAuthHeaders:
        result: ProviderAuthHeaders | None = None
        failed = False
        try:
            checked = _peak_connection(connection, allow_validation=True)
            request_envelopes = self._request_envelopes.get()
            if request_envelopes is not None:
                request_connection, envelopes = request_envelopes
                if request_connection != checked:
                    raise ValueError
            else:
                envelopes = self._load_envelopes(checked)
                if inspect.isawaitable(envelopes):
                    envelopes = await envelopes
            with open_peak_credentials(
                vault=self._vault,
                connection=checked,
                envelopes=envelopes,
            ) as credentials:
                result = self._contract.authorization_headers(
                    credentials,
                    application_code=self._application_code,
                )
        except Exception:
            failed = True
        if failed or result is None:
            if "credentials" in locals():
                credentials.clear()
                del credentials
            if "envelopes" in locals():
                del envelopes
            if "checked" in locals():
                del checked
            del connection
            del self
            raise PeakCredentialError("peak_credentials_invalid")
        return result

    @contextmanager
    def use_request_envelopes(
        self,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ):
        """Bind provisional encrypted material to one validation task."""

        try:
            checked = _peak_connection(connection, allow_validation=True)
            checked_envelopes = tuple(
                CredentialEnvelope.model_validate(envelope) for envelope in envelopes
            )
            if self._request_envelopes.get() is not None:
                raise ValueError
            token = self._request_envelopes.set((checked, checked_envelopes))
        except PeakCredentialError:
            raise
        except Exception:
            raise PeakCredentialError("peak_credentials_invalid") from None
        try:
            yield
        finally:
            self._request_envelopes.reset(token)


class PeakMCPDriver:
    """PEAK guard that exposes no transport until its MCP contract is qualified."""

    provider = ProviderId.PEAK

    def __init__(
        self,
        *,
        runtime: Any,
        manifest: ProviderDriverManifest,
        contract: QualifiedPeakProviderContract | None,
        qualification_resolver: CatalogQualificationResolver,
    ) -> None:
        checked_manifest = ProviderDriverManifest.model_validate(manifest.model_dump(mode="json"))
        if checked_manifest.provider is not ProviderId.PEAK:
            raise ValueError("peak_driver_manifest_invalid")
        if getattr(runtime, "provider", None) is not ProviderId.PEAK:
            raise ValueError("peak_driver_runtime_invalid")
        if contract is not None and not isinstance(contract, QualifiedPeakProviderContract):
            raise ValueError("peak_driver_contract_invalid")
        if not isinstance(qualification_resolver, CatalogQualificationResolver):
            raise ValueError("peak_driver_qualification_resolver_invalid")
        self._runtime = runtime
        self._manifest = checked_manifest
        self._contract = contract
        self._qualification_resolver = qualification_resolver

    def __repr__(self) -> str:
        return f"PeakMCPDriver(contract_qualified={self.contract_qualified!r})"

    @property
    def contract_qualified(self) -> bool:
        return isinstance(self._contract, QualifiedPeakProviderContract)

    async def discover(self, connection: ProviderConnection) -> ProviderDiscovery:
        contract = self._require_contract()
        checked = self._connection(connection, allow_validation=True)
        try:
            self._qualification_resolver.bind_bootstrap(
                checked,
                normalized_capability=_PROFILE_CAPABILITY,
                provider_tool_name=contract.profile_tool,
            )
        except Exception:
            raise self._invalid() from None
        return await self._runtime.discover(checked)

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation:
        contract = self._require_contract()
        checked = self._connection(connection, allow_validation=True)
        validation: ProviderValidation | None = None
        failed = False
        try:
            binding = self._qualification_resolver.bind_bootstrap(
                checked,
                normalized_capability=_PROFILE_CAPABILITY,
                provider_tool_name=contract.profile_tool,
            )
            result = await self._runtime.call(
                checked,
                binding,
                contract.profile_request_model(),
                uuid4(),
            )
            if result.status_class is not ProviderStatusClass.SUCCESS:
                raise ValueError
            profile = PeakProfile.model_validate(result.normalized_data)
            validation = ProviderValidation(
                provider=ProviderId.PEAK,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={
                    "merchant_id": profile.merchant_id,
                    "merchant_display_name": profile.merchant_display_name,
                },
                dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
            )
        except Exception:
            failed = True
        if failed or validation is None:
            if "result" in locals():
                del result
            if "profile" in locals():
                del profile
            if "binding" in locals():
                del binding
            del checked
            del contract
            del connection
            error = self._invalid()
            del self
            raise error
        return validation

    async def validate_setup(
        self,
        connection: ProviderConnection,
        envelopes: Sequence[CredentialEnvelope],
    ) -> PeakProfile:
        """Validate provisional envelopes without persisting plaintext or ciphertext."""

        contract = self._require_contract()
        checked = self._connection(connection, allow_validation=True)
        header_factory = getattr(self._runtime, "_header_factory", None)
        if (
            not isinstance(header_factory, PeakCredentialHeaderFactory)
            or header_factory._contract is not contract
        ):
            raise self._invalid()
        try:
            with header_factory.use_request_envelopes(checked, envelopes):
                validation = await self.validate_connection(checked)
            if validation.status_class is not ProviderStatusClass.SUCCESS:
                raise ValueError
            return PeakProfile.model_validate(validation.normalized_data)
        except ProviderResponseInvalid:
            raise
        except Exception:
            raise self._invalid() from None

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult:
        self._require_contract()
        checked = self._connection(connection, allow_validation=False)
        try:
            self._qualification_resolver.assert_binding(checked, binding)
        except Exception:
            raise self._invalid() from None
        raise self._invalid()

    def _connection(
        self,
        connection: ProviderConnection,
        *,
        allow_validation: bool,
    ) -> ProviderConnection:
        try:
            checked = _peak_connection(
                connection,
                allow_validation=allow_validation,
            )
            if checked.environment not in self._manifest.environments:
                raise ValueError
            return checked
        except Exception:
            raise self._invalid() from None

    def _require_contract(self) -> QualifiedPeakProviderContract:
        if self._contract is None:
            raise self._invalid()
        return self._contract

    @staticmethod
    def _invalid() -> ProviderResponseInvalid:
        return ProviderResponseInvalid(
            ProviderId.PEAK,
            dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
        )


def _validate_credential_text(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 8192
        or value != value.strip()
        or any(character.isspace() for character in value)
        or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
    ):
        raise ValueError
    return value


def _credential_bytes(value: str) -> bytearray:
    return bytearray(_validate_credential_text(value).encode("utf-8"))


def _decode_credential(value: bytearray) -> str:
    decoded = bytes(value).decode("utf-8")
    return _validate_credential_text(decoded)


def _credential_binding(
    connection: ProviderConnection,
    credential_type: str,
) -> CredentialBinding:
    return CredentialBinding(
        tenant_id=connection.tenant_id,
        workspace_id=connection.workspace_id,
        auth_user_id=connection.auth_user_id,
        connection_id=connection.id,
        provider=ProviderId.PEAK.value,
        company_or_merchant_id=connection.provider_account_id,
        environment=connection.environment,
        credential_type=credential_type,
    )


def _peak_connection(
    connection: ProviderConnection,
    *,
    allow_validation: bool,
) -> ProviderConnection:
    try:
        checked = ProviderConnection.model_validate(connection)
        readiness = {ConnectionReadiness.READY}
        if allow_validation:
            readiness.add(ConnectionReadiness.REQUIRES_VALIDATION)
        if (
            checked.provider is not ProviderId.PEAK
            or checked.authorization_method is not AuthorizationMethod.PROVIDER_CREDENTIALS
            or checked.readiness not in readiness
        ):
            raise ValueError
        return checked
    except Exception:
        raise PeakCredentialError("peak_credentials_invalid") from None


__all__ = [
    "PeakCredentialError",
    "PeakCredentialHeaderFactory",
    "PeakCredentialMaterial",
    "PeakEnvelopeLoader",
    "PeakMCPDriver",
    "PeakProfile",
    "PeakProfileNormalizer",
    "QualifiedPeakProviderContract",
    "open_peak_credentials",
    "seal_peak_credentials",
]
