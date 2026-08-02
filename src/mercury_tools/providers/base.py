"""Closed contracts for hosted downstream provider drivers."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from mercury_tools.catalog.identity import validate_credential_safe
from mercury_tools.providers.models import ProviderConnection, ProviderId

if TYPE_CHECKING:
    from mercury_tools.providers.streamable_mcp import ProviderOperationDeadline

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RESERVED_NORMALIZED_KEYS = frozenset(
    {
        "authorization",
        "cookie",
        "headers",
        "mcpsessionid",
        "sessionid",
        "setcookie",
        "toolname",
    }
)


class ProviderOperationClass(StrEnum):
    READ = "read"
    CREATE = "create"


class ProviderQualificationState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"


class ProviderStatusClass(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    AUTH_REQUIRED = "auth_required"
    SCHEMA_CHANGED = "schema_changed"
    TIMEOUT = "timeout"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RESPONSE_INVALID = "response_invalid"


class DispatchCertainty(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_DISPATCHED = "not_dispatched"
    DISPATCHED = "dispatched"
    UNKNOWN = "unknown"


class _ProviderModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class QualifiedCapabilityBinding(_ProviderModel):
    """A server-side binding already qualified by the Capability Catalog."""

    provider: ProviderId
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    normalized_capability: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER.pattern,
    )
    provider_tool: str = Field(
        min_length=1,
        max_length=200,
        pattern=_TOOL_NAME.pattern,
        exclude=True,
        repr=False,
    )
    operation_class: ProviderOperationClass
    qualification_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_SHA256.pattern,
    )

    @field_validator("provider_tool")
    @classmethod
    def validate_provider_tool(cls, value: str) -> str:
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
            raise ValueError("provider_binding_invalid")
        return value


class VerifiedRuntimeBinding(_ProviderModel):
    """A trusted server-side execution record resolved by Catalog authority."""

    qualification_state: ProviderQualificationState
    provider: ProviderId
    environment: str = Field(min_length=1, max_length=64, pattern=_IDENTIFIER.pattern)
    resource_uri_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_SHA256.pattern,
    )
    normalized_capability: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER.pattern,
    )
    capability_version: str = Field(min_length=1, max_length=64)
    provider_tool: str = Field(
        min_length=1,
        max_length=200,
        pattern=_TOOL_NAME.pattern,
        exclude=True,
        repr=False,
    )
    operation_class: ProviderOperationClass
    request_schema_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_SHA256.pattern,
    )
    response_schema_sha256: str = Field(
        min_length=64,
        max_length=64,
        pattern=_SHA256.pattern,
    )
    qualification_hash: str = Field(
        min_length=64,
        max_length=64,
        pattern=_SHA256.pattern,
    )

    @field_validator("capability_version", "provider_tool")
    @classmethod
    def validate_internal_identifier(cls, value: str) -> str:
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
            raise ValueError("provider_binding_invalid")
        return value


class _ProviderResult(_ProviderModel):
    provider: ProviderId
    status_class: ProviderStatusClass
    normalized_data: Mapping[str, JsonValue]
    dispatch_certainty: DispatchCertainty

    @model_validator(mode="after")
    def freeze_sanitized_data(self) -> _ProviderResult:
        validate_credential_safe(self.normalized_data)
        if _contains_reserved_normalized_key(self.normalized_data):
            raise ValueError("provider_normalized_data_invalid")
        object.__setattr__(self, "normalized_data", _freeze_json(self.normalized_data))
        return self

    @field_serializer("normalized_data")
    def serialize_normalized_data(
        self,
        value: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        serialized = _jsonable(value)
        if not isinstance(serialized, dict):
            raise TypeError("provider_normalized_data_invalid")
        return serialized


class ProviderDiscovery(_ProviderResult):
    pass


class ProviderValidation(_ProviderResult):
    pass


class ProviderCallResult(_ProviderResult):
    pass


class ProviderRuntimeError(RuntimeError):
    """A closed provider error that never retains downstream exception text."""

    code = "provider_unavailable"
    status_class = ProviderStatusClass.UNAVAILABLE

    def __init__(
        self,
        provider: ProviderId,
        *,
        dispatch_certainty: DispatchCertainty,
    ) -> None:
        self.provider = ProviderId(provider)
        self.dispatch_certainty = DispatchCertainty(dispatch_certainty)
        super().__init__(self.code)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}("
            f"provider={self.provider.value!r}, "
            f"code={self.code!r}, "
            f"dispatch_certainty={self.dispatch_certainty.value!r}"
            ")"
        )

    def public_dict(self) -> dict[str, str]:
        return {
            "provider": self.provider.value,
            "status_class": self.status_class.value,
            "code": self.code,
            "dispatch_certainty": self.dispatch_certainty.value,
        }


class ProviderUnavailable(ProviderRuntimeError):
    code = "provider_unavailable"
    status_class = ProviderStatusClass.UNAVAILABLE


class ProviderAuthRequired(ProviderRuntimeError):
    code = "provider_auth_required"
    status_class = ProviderStatusClass.AUTH_REQUIRED


class ProviderSchemaChanged(ProviderRuntimeError):
    code = "provider_schema_changed"
    status_class = ProviderStatusClass.SCHEMA_CHANGED


class ProviderTimeoutPreDispatch(ProviderRuntimeError):
    code = "provider_timeout_pre_dispatch"
    status_class = ProviderStatusClass.TIMEOUT


class ProviderOutcomeUnknown(ProviderRuntimeError):
    code = "provider_outcome_unknown"
    status_class = ProviderStatusClass.OUTCOME_UNKNOWN


class ProviderResponseInvalid(ProviderRuntimeError):
    code = "provider_response_invalid"
    status_class = ProviderStatusClass.RESPONSE_INVALID


class ProviderDriver(Protocol):
    provider: ProviderId

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery: ...

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation: ...

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
        *,
        deadline: ProviderOperationDeadline | None = None,
    ) -> ProviderCallResult: ...


def _contains_reserved_normalized_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            canonical_key = re.sub(r"[^a-z0-9]", "", key.casefold())
            if canonical_key in _RESERVED_NORMALIZED_KEYS:
                return True
            if _contains_reserved_normalized_key(item):
                return True
    elif isinstance(value, list | tuple):
        return any(_contains_reserved_normalized_key(item) for item in value)
    return False


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


__all__ = [
    "DispatchCertainty",
    "ProviderAuthRequired",
    "ProviderCallResult",
    "ProviderDiscovery",
    "ProviderDriver",
    "ProviderOperationClass",
    "ProviderOutcomeUnknown",
    "ProviderQualificationState",
    "ProviderResponseInvalid",
    "ProviderRuntimeError",
    "ProviderSchemaChanged",
    "ProviderStatusClass",
    "ProviderTimeoutPreDispatch",
    "ProviderUnavailable",
    "ProviderValidation",
    "QualifiedCapabilityBinding",
    "VerifiedRuntimeBinding",
]
