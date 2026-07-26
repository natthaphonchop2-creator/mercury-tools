"""Secretless downstream provider driver manifest contract."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mercury_tools.catalog.identity import validate_credential_safe
from mercury_tools.config import Settings
from mercury_tools.providers.models import AuthorizationMethod, ProviderId

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MANIFEST_MAX_BYTES = 64 * 1024
_SEED_CAPABILITIES = frozenset(
    {
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
        "documents.invoice.create",
    }
)
_FORBIDDEN_KEYS = frozenset(
    {
        "access_token",
        "authorization",
        "client_secret",
        "cookie",
        "credential",
        "credentials",
        "endpoint",
        "headers",
        "password",
        "refresh_token",
        "secret",
        "session_id",
        "token",
        "url",
        "uri",
    }
)
_EXPECTED_ENVIRONMENT_KEYS = {
    ProviderId.FLOWACCOUNT: {
        "sandbox": "flowaccount_mcp_sandbox_url",
        "production": "flowaccount_mcp_production_url",
    },
    ProviderId.PEAK: {
        "uat": "peak_mcp_uat_url",
        "production": "peak_mcp_production_url",
    },
}
_EXPECTED_AUTH_ADAPTERS = {
    ProviderId.FLOWACCOUNT: AuthorizationMethod.OAUTH2_PKCE,
    ProviderId.PEAK: AuthorizationMethod.PROVIDER_CREDENTIALS,
}


class ProviderManifestError(ValueError):
    """A stable manifest/configuration failure without rejected input."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProviderTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"


class TimeoutClass(StrEnum):
    DISCOVERY = "discovery"
    READ = "read"
    CREATE = "create"


class _ManifestModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class TimeoutPolicy(_ManifestModel):
    connect_seconds: Literal[5]
    operation_seconds: int = Field(ge=1, le=60)


class DiscoveryMapping(_ManifestModel):
    provider_tool: str = Field(
        min_length=1,
        max_length=200,
        pattern=_TOOL_NAME.pattern,
    )
    normalized_capability: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER.pattern,
    )
    timeout_class: Literal[TimeoutClass.READ, TimeoutClass.CREATE]


class ProviderDriverManifest(_ManifestModel):
    manifest_version: Literal["1"]
    provider: ProviderId
    environments: dict[
        str,
        Literal[
            "flowaccount_mcp_sandbox_url",
            "flowaccount_mcp_production_url",
            "peak_mcp_uat_url",
            "peak_mcp_production_url",
        ],
    ]
    transport: Literal[ProviderTransport.STREAMABLE_HTTP]
    protocol_version: Literal["2025-11-25"]
    auth_adapter: AuthorizationMethod
    allowed_permissions: tuple[str, ...]
    timeout_classes: dict[TimeoutClass, TimeoutPolicy]
    discovery_mappings: tuple[DiscoveryMapping, ...]

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_manifest_content(cls, value: Any) -> Any:
        validate_credential_safe(value)
        _reject_unsafe_manifest_content(value)
        return value

    @field_validator("allowed_permissions")
    @classmethod
    def validate_permissions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if (
            not value
            or len(value) != len(set(value))
            or tuple(sorted(value)) != value
            or any(_IDENTIFIER.fullmatch(permission) is None for permission in value)
        ):
            raise ValueError("provider_manifest_invalid")
        return value

    @model_validator(mode="after")
    def validate_exact_contract(self) -> ProviderDriverManifest:
        if self.environments != _EXPECTED_ENVIRONMENT_KEYS[self.provider]:
            raise ValueError("provider_manifest_invalid")
        if self.auth_adapter is not _EXPECTED_AUTH_ADAPTERS[self.provider]:
            raise ValueError("provider_manifest_invalid")
        expected_timeouts = {
            TimeoutClass.DISCOVERY: (5, 30),
            TimeoutClass.READ: (5, 30),
            TimeoutClass.CREATE: (5, 60),
        }
        if set(self.timeout_classes) != set(expected_timeouts):
            raise ValueError("provider_manifest_invalid")
        for timeout_class, (connect_seconds, operation_seconds) in expected_timeouts.items():
            policy = self.timeout_classes[timeout_class]
            if (
                policy.connect_seconds != connect_seconds
                or policy.operation_seconds != operation_seconds
            ):
                raise ValueError("provider_manifest_invalid")

        tools = [mapping.provider_tool for mapping in self.discovery_mappings]
        capabilities = [
            mapping.normalized_capability for mapping in self.discovery_mappings
        ]
        if (
            not tools
            or len(tools) != len(set(tools))
            or len(capabilities) != len(set(capabilities))
            or set(capabilities) != _SEED_CAPABILITIES
        ):
            raise ValueError("provider_manifest_invalid")
        for mapping in self.discovery_mappings:
            expected_timeout = (
                TimeoutClass.CREATE
                if mapping.normalized_capability.endswith(".create")
                else TimeoutClass.READ
            )
            if mapping.timeout_class is not expected_timeout:
                raise ValueError("provider_manifest_invalid")
        return self


@dataclass(frozen=True, repr=False)
class ResolvedProviderResource:
    provider: ProviderId
    environment: str
    uri: str
    uri_sha256: str

    def __repr__(self) -> str:
        return (
            "ResolvedProviderResource("
            f"provider={self.provider.value!r}, "
            f"environment={self.environment!r}, "
            f"uri_sha256={self.uri_sha256!r}"
            ")"
        )


def load_provider_manifest(path: str | Path) -> ProviderDriverManifest:
    failure: ProviderManifestError | None = None
    try:
        raw = Path(path).read_bytes()
        if not raw or len(raw) > _MANIFEST_MAX_BYTES:
            raise ValueError
        payload = json.loads(raw, object_pairs_hook=_unique_object)
        return ProviderDriverManifest.model_validate(payload)
    except ProviderManifestError as error:
        failure = ProviderManifestError(error.code)
    except Exception:
        failure = ProviderManifestError("provider_manifest_invalid")
    raise failure


def resolve_provider_resource(
    *,
    settings: Settings,
    manifest: ProviderDriverManifest,
    environment: str,
) -> ResolvedProviderResource:
    failure: ProviderManifestError | None = None
    try:
        checked_environment = str(environment)
        config_key = manifest.environments[checked_environment]
        if _EXPECTED_ENVIRONMENT_KEYS[manifest.provider][checked_environment] != config_key:
            raise ValueError
        uri = getattr(settings, config_key)
        if not _is_clean_https_url(uri):
            raise ValueError
    except Exception:
        failure = ProviderManifestError("provider_resource_unavailable")
    if failure is not None:
        raise failure
    return ResolvedProviderResource(
        provider=manifest.provider,
        environment=checked_environment,
        uri=uri,
        uri_sha256=hashlib.sha256(uri.encode("utf-8")).hexdigest(),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ProviderManifestError("provider_manifest_invalid")
        value[key] = item
    return value


def _reject_unsafe_manifest_content(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str) or key.casefold() in _FORBIDDEN_KEYS:
                raise ValueError("provider_manifest_invalid")
            _reject_unsafe_manifest_content(item)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_manifest_content(item)
        return
    if isinstance(value, str):
        lowered = value.casefold()
        if (
            lowered.startswith(("http://", "https://", "//"))
            or "bearer " in lowered
            or any(unicodedata.category(character) in {"Cc", "Cf"} for character in value)
        ):
            raise ValueError("provider_manifest_invalid")
        return
    if value is None or isinstance(value, bool | int):
        return
    raise ValueError("provider_manifest_invalid")


def _is_clean_https_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
        return bool(
            parsed.scheme == "https"
            and parsed.hostname
            and (port is None or port > 0)
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and "?" not in value
            and "#" not in value
        )
    except ValueError:
        return False


__all__ = [
    "DiscoveryMapping",
    "ProviderDriverManifest",
    "ProviderManifestError",
    "ProviderTransport",
    "ResolvedProviderResource",
    "TimeoutClass",
    "TimeoutPolicy",
    "load_provider_manifest",
    "resolve_provider_resource",
]
