"""Generic credential drivers for repository-configured ERP connectors."""

from __future__ import annotations

import base64
import json
import mimetypes
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, unquote_plus, urlsplit

import httpx

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.drivers.base import ConnectorAuthError, DriverConfigurationError
from mercury_tools.drivers.models import (
    AuthContext,
    ConnectionProbe,
    ConnectorResult,
    CredentialField,
    PreparedFile,
    immutable_mapping,
)
from mercury_tools.safety.redaction import redact_json

_REDACTED = "[REDACTED]"
_JSON_DECODE_FAILED = object()
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


class _GenericDriver:
    driver_id = "generic"
    _credential_fields: tuple[CredentialField, ...] = ()

    def __init__(self, *, connector_id: str, environments: Mapping[str, str]) -> None:
        self.connector_id = connector_id
        self._environments = immutable_mapping(_configured_environments(environments))

    @property
    def credential_schema(self) -> tuple[CredentialField, ...]:
        return self._credential_fields

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
        self.resolve_base_url(environment)
        return self.credential_schema

    def resolve_base_url(self, environment: str) -> str:
        base_url = self._environments.get(environment)
        if not base_url:
            raise DriverConfigurationError("unsupported_environment")
        return base_url

    def safe_probe_action(self, environment: str) -> str:
        self.resolve_base_url(environment)
        return "GET /"

    def prepare_files(
        self,
        *,
        action: CatalogAction,
        inputs: Mapping[str, Any],
        roots: Sequence[Path],
    ) -> tuple[PreparedFile, ...]:
        files = inputs.get("files", {})
        if not isinstance(files, Mapping):
            raise DriverConfigurationError("multipart_files_invalid")
        if not files:
            return ()
        if not _is_multipart_form_data(action.content_type):
            raise DriverConfigurationError("multipart_content_type_required")

        declared = action.input_schema.get("files", {})
        if not isinstance(declared, Mapping):
            raise DriverConfigurationError("multipart_schema_invalid")
        resolved_roots = _resolve_roots(roots)
        prepared: list[PreparedFile] = []
        for field_name, raw_path in files.items():
            if not isinstance(field_name, str) or field_name not in declared:
                raise DriverConfigurationError("multipart_file_undeclared")
            if not isinstance(raw_path, (str, Path)):
                raise DriverConfigurationError("multipart_file_invalid")
            try:
                path = Path(raw_path).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                raise DriverConfigurationError("multipart_file_invalid") from None
            if not path.is_file():
                raise DriverConfigurationError("multipart_file_invalid")
            if not any(path.is_relative_to(root) for root in resolved_roots):
                raise DriverConfigurationError("multipart_file_outside_roots")
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            prepared.append(
                PreparedFile(
                    field_name=field_name,
                    path=path,
                    filename=path.name,
                    content_type=content_type,
                )
            )
        return tuple(prepared)

    async def validate_credentials(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> ConnectionProbe:
        auth = await self.prepare_auth(
            environment=environment,
            credentials=credentials,
            client=client,
        )
        credential_values = _credential_values(credentials, auth)
        try:
            response = await client.get(
                self.resolve_base_url(environment),
                headers=dict(auth.headers),
                params=dict(auth.query),
            )
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError):
            return ConnectionProbe(
                status="failed",
                connector_id=self.connector_id,
                environment=environment,
                company_name=None,
                details={"error": "probe_request_failed"},
            )

        company_name = _company_name(response, credential_values) if response.is_success else None
        return ConnectionProbe(
            status="connected" if response.is_success else "failed",
            connector_id=self.connector_id,
            environment=environment,
            company_name=company_name,
            details={"http_status": response.status_code},
        )

    def interpret_response(
        self,
        *,
        action: CatalogAction,
        response: httpx.Response,
        dispatched: bool,
    ) -> ConnectorResult:
        value = _response_json(response)
        failed = not response.is_success or _matches_error_rules(
            action.error_rules,
            None if value is _JSON_DECODE_FAILED else value,
            response.status_code,
        )
        if value is not _JSON_DECODE_FAILED:
            return ConnectorResult(
                status="failed" if failed else "succeeded",
                http_status=response.status_code,
                data=self.sanitize_response(action, value),
                summary="provider_response_failed" if failed else "json_response",
                dispatched=dispatched,
            )
        return ConnectorResult(
            status="failed" if failed else "succeeded",
            http_status=response.status_code,
            data=None,
            summary="plaintext_response",
            dispatched=dispatched,
        )

    def sanitize_response(self, action: CatalogAction, value: Any) -> Any:
        return redact_json(_redact_paths(value, action.response_redaction))

    def _required_credentials(self, credentials: Mapping[str, str]) -> dict[str, str]:
        if not isinstance(credentials, Mapping):
            raise ConnectorAuthError("credential_invalid")
        declared = {field.name for field in self.credential_schema}
        if any(not isinstance(name, str) or name not in declared for name in credentials):
            raise ConnectorAuthError("credential_undeclared")
        values: dict[str, str] = {}
        for field in self.credential_schema:
            if field.name not in credentials:
                raise ConnectorAuthError("credential_missing")
            value = credentials[field.name]
            if not isinstance(value, str):
                raise ConnectorAuthError("credential_invalid")
            if not value.strip():
                raise ConnectorAuthError("credential_blank")
            values[field.name] = value
        return values


class GenericBearerDriver(_GenericDriver):
    driver_id = "bearer"
    _credential_fields = (CredentialField("token", secret=True, label="Bearer token"),)

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.resolve_base_url(environment)
        token = self._required_credentials(credentials)["token"]
        return AuthContext(headers={"Authorization": f"Bearer {token}"}, query={}, expires_at=None)


class GenericApiKeyDriver(_GenericDriver):
    _credential_fields = (CredentialField("api_key", secret=True, label="API key"),)

    def __init__(
        self,
        *,
        connector_id: str,
        placement: Literal["header", "query"],
        key_name: str,
        environments: Mapping[str, str],
    ) -> None:
        if (
            placement not in {"header", "query"}
            or not isinstance(key_name, str)
            or not key_name.strip()
        ):
            raise DriverConfigurationError("api_key_configuration_invalid")
        super().__init__(connector_id=connector_id, environments=environments)
        self.placement = placement
        self.key_name = key_name
        self.driver_id = f"api_key_{placement}"

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.resolve_base_url(environment)
        api_key = self._required_credentials(credentials)["api_key"]
        if self.placement == "header":
            return AuthContext(headers={self.key_name: api_key}, query={}, expires_at=None)
        return AuthContext(headers={}, query={self.key_name: api_key}, expires_at=None)


class GenericBasicDriver(_GenericDriver):
    driver_id = "basic"
    _credential_fields = (
        CredentialField("username", secret=False, label="Username"),
        CredentialField("password", secret=True, label="Password"),
    )

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.resolve_base_url(environment)
        values = self._required_credentials(credentials)
        encoded = base64.b64encode(f"{values['username']}:{values['password']}".encode()).decode()
        return AuthContext(headers={"Authorization": f"Basic {encoded}"}, query={}, expires_at=None)


class GenericOAuthClientCredentialsDriver(_GenericDriver):
    driver_id = "oauth_client_credentials"
    _credential_fields = (
        CredentialField("client_id", secret=False, label="Client ID"),
        CredentialField("client_secret", secret=True, label="Client secret"),
    )

    def __init__(
        self,
        *,
        connector_id: str,
        environments: Mapping[str, str],
        token_urls: Mapping[str, str],
    ) -> None:
        super().__init__(connector_id=connector_id, environments=environments)
        configured_token_urls = _configured_environments(token_urls)
        if set(configured_token_urls) != set(self._environments):
            raise DriverConfigurationError("oauth_environment_mismatch")
        self._token_urls = immutable_mapping(configured_token_urls)

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
        self.resolve_base_url(environment)
        if not self._token_urls.get(environment):
            raise DriverConfigurationError("unsupported_environment")
        return self.credential_schema

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        self.resolve_base_url(environment)
        token_url = self._token_urls.get(environment)
        if not token_url:
            raise DriverConfigurationError("unsupported_environment")
        values = self._required_credentials(credentials)
        try:
            response = await client.post(
                token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": values["client_id"],
                    "client_secret": values["client_secret"],
                },
            )
            payload = _response_json(response)
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError):
            raise ConnectorAuthError("oauth_token_failed") from None
        if not response.is_success or not isinstance(payload, Mapping):
            raise ConnectorAuthError("oauth_token_failed")
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ConnectorAuthError("oauth_token_failed")
        expires_at = _expires_at(payload.get("expires_in"))
        return AuthContext(
            headers={"Authorization": f"Bearer {token}"},
            query={},
            expires_at=expires_at,
        )


def _resolve_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    resolved: list[Path] = []
    for root in roots:
        try:
            candidate = Path(root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            raise DriverConfigurationError("multipart_root_invalid") from None
        if not candidate.is_dir():
            raise DriverConfigurationError("multipart_root_invalid")
        resolved.append(candidate)
    if not resolved:
        raise DriverConfigurationError("multipart_roots_required")
    return tuple(resolved)


def _response_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _JSON_DECODE_FAILED


def _company_name(response: httpx.Response, credential_values: Sequence[str]) -> str | None:
    value = _response_json(response)
    if not isinstance(value, Mapping):
        return None
    for key in ("company_name", "companyName", "name"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return _redact_credential_values(candidate, credential_values)
    return None


def _matches_error_rules(
    rules: Mapping[str, Any],
    value: Any | None,
    http_status: int,
) -> bool:
    status_codes = rules.get("status_codes", ())
    if (
        isinstance(status_codes, Sequence)
        and not isinstance(status_codes, str)
        and http_status in status_codes
    ):
        return True
    body_rule = rules.get("body")
    if not isinstance(body_rule, Mapping) or value is None:
        return False
    path = body_rule.get("path")
    if not isinstance(path, str):
        return False
    return _path_value(value, path) == body_rule.get("equals")


def _path_value(value: Any, path: str) -> Any:
    current = value
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return None
        current = current[component]
    return current


def _redact_paths(value: Any, paths: Sequence[str]) -> Any:
    copied = _copy_json(value)
    for path in paths:
        if isinstance(path, str) and path:
            _redact_path(copied, path.split("."))
    return copied


def _copy_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _copy_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_copy_json(item) for item in value]
    return value


def _redact_path(value: Any, components: list[str]) -> None:
    if not components:
        return
    component = components[0]
    remaining = components[1:]
    if isinstance(value, Mapping):
        if component == "*":
            for key in value:
                if remaining:
                    _redact_path(value[key], remaining)
                else:
                    value[key] = _REDACTED
        elif component in value:
            if remaining:
                _redact_path(value[component], remaining)
            else:
                value[component] = _REDACTED
    elif isinstance(value, list):
        if component == "*":
            for index in range(len(value)):
                if remaining:
                    _redact_path(value[index], remaining)
                else:
                    value[index] = _REDACTED
        elif component.isdigit() and int(component) < len(value):
            if remaining:
                _redact_path(value[int(component)], remaining)
            else:
                value[int(component)] = _REDACTED


def _expires_at(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float) and value >= 0:
        return datetime.now(UTC) + timedelta(seconds=value)
    return None


def _is_multipart_form_data(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().casefold() == "multipart/form-data"


def _credential_values(credentials: Mapping[str, str], auth: AuthContext) -> tuple[str, ...]:
    values = [value for value in credentials.values() if isinstance(value, str) and value]
    for value in (*auth.headers.values(), *auth.query.values()):
        if isinstance(value, str) and value:
            values.append(value)
            if " " in value:
                _, token = value.split(" ", 1)
                if token:
                    values.append(token)
    return tuple(sorted(set(values), key=len, reverse=True))


def _redact_credential_values(value: str, credential_values: Sequence[str]) -> str:
    decoded_values = _reversibly_decoded_values(value)
    if any(
        credential
        and any(credential in decoded_value for decoded_value in decoded_values)
        for credential in credential_values
    ):
        return _REDACTED
    return value


def _reversibly_decoded_values(value: str) -> tuple[str, ...]:
    values = {value}
    pending = {value}
    for _ in range(2):
        decoded: set[str] = set()
        for candidate in pending:
            decoded.add(unquote(candidate))
            decoded.add(unquote_plus(candidate))
        pending = decoded - values
        values.update(pending)
        if not pending:
            break
    return tuple(values)


@dataclass(frozen=True)
class GenericDriverFactory:
    """A generic driver recipe that requires real connector configuration."""

    driver_id: str
    credential_schema: tuple[CredentialField, ...]

    def create(
        self,
        *,
        connector_id: str,
        environments: Mapping[str, str],
        key_name: str | None = None,
        token_urls: Mapping[str, str] | None = None,
    ) -> _GenericDriver:
        if self.driver_id == "bearer":
            _require_factory_options(key_name=key_name, token_urls=token_urls)
            return GenericBearerDriver(
                connector_id=connector_id,
                environments=environments,
            )
        if self.driver_id in {"api_key_header", "api_key_query"}:
            if token_urls is not None:
                raise DriverConfigurationError("generic_factory_configuration_invalid")
            placement: Literal["header", "query"] = (
                "header" if self.driver_id == "api_key_header" else "query"
            )
            return GenericApiKeyDriver(
                connector_id=connector_id,
                placement=placement,
                key_name=(
                    key_name
                    if key_name is not None
                    else "X-API-Key"
                    if placement == "header"
                    else "api_key"
                ),
                environments=environments,
            )
        if self.driver_id == "basic":
            _require_factory_options(key_name=key_name, token_urls=token_urls)
            return GenericBasicDriver(
                connector_id=connector_id,
                environments=environments,
            )
        if self.driver_id == "oauth_client_credentials":
            if key_name is not None or token_urls is None:
                raise DriverConfigurationError("generic_factory_configuration_invalid")
            return GenericOAuthClientCredentialsDriver(
                connector_id=connector_id,
                environments=environments,
                token_urls=token_urls,
            )
        raise DriverConfigurationError("generic_factory_not_supported")


def generic_driver_factories() -> tuple[GenericDriverFactory, ...]:
    return (
        GenericDriverFactory("bearer", GenericBearerDriver._credential_fields),
        GenericDriverFactory("api_key_header", GenericApiKeyDriver._credential_fields),
        GenericDriverFactory("api_key_query", GenericApiKeyDriver._credential_fields),
        GenericDriverFactory("basic", GenericBasicDriver._credential_fields),
        GenericDriverFactory(
            "oauth_client_credentials",
            GenericOAuthClientCredentialsDriver._credential_fields,
        ),
    )


def _configured_environments(environments: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(environments, Mapping) or not environments:
        raise DriverConfigurationError("driver_environments_required")
    configured: dict[str, str] = {}
    for environment, base_url in environments.items():
        if not isinstance(environment, str) or not environment.strip():
            raise DriverConfigurationError("driver_environment_invalid")
        if not isinstance(base_url, str) or not base_url.strip():
            raise DriverConfigurationError("driver_environment_invalid")
        _validate_configured_url(base_url)
        configured[environment] = base_url
    return configured


def _validate_configured_url(value: str) -> None:
    if (
        any(ord(character) <= 0x20 or ord(character) == 0x7F for character in value)
        or "\\" in value
        or _INVALID_PERCENT_ESCAPE.search(value) is not None
    ):
        raise DriverConfigurationError("driver_url_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        parsed_url = httpx.URL(value)
    except (httpx.InvalidURL, ValueError):
        raise DriverConfigurationError("driver_url_invalid") from None
    if (
        value != value.strip()
        or parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "#" in value
        or not parsed_url.host
        or parsed.netloc.endswith(":")
        or port is not None and not 0 <= port <= 65535
    ):
        raise DriverConfigurationError("driver_url_invalid")


def _require_factory_options(
    *, key_name: str | None, token_urls: Mapping[str, str] | None
) -> None:
    if key_name is not None or token_urls is not None:
        raise DriverConfigurationError("generic_factory_configuration_invalid")


__all__ = [
    "DriverConfigurationError",
    "GenericApiKeyDriver",
    "GenericBasicDriver",
    "GenericBearerDriver",
    "GenericDriverFactory",
    "GenericOAuthClientCredentialsDriver",
    "generic_driver_factories",
]
