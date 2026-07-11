"""Generic credential drivers for repository-configured ERP connectors."""

from __future__ import annotations

import base64
import json
import mimetypes
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

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
from mercury_tools.safety.redaction import redact_json, redact_text

_TEXT_SUMMARY_LIMIT = 1024
_REDACTED = "[REDACTED]"


class _GenericDriver:
    driver_id = "generic"
    _credential_fields: tuple[CredentialField, ...] = ()

    def __init__(self, *, connector_id: str, environments: Mapping[str, str]) -> None:
        self.connector_id = connector_id
        self._environments = immutable_mapping(environments)

    def credential_fields(self, environment: str) -> tuple[CredentialField, ...]:
        return self._credential_fields

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
        if not action.content_type.casefold().startswith("multipart/form-data"):
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
            except OSError:
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
        try:
            response = await client.get(
                self.resolve_base_url(environment),
                headers=dict(auth.headers),
                params=dict(auth.query),
            )
        except httpx.HTTPError:
            return ConnectionProbe(
                status="failed",
                connector_id=self.connector_id,
                environment=environment,
                company_name=None,
                details={"error": "probe_request_failed"},
            )

        company_name = _company_name(response) if response.is_success else None
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
            value,
            response.status_code,
        )
        if value is not None:
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
            summary=redact_text(response.text)[:_TEXT_SUMMARY_LIMIT],
            dispatched=dispatched,
        )

    def sanitize_response(self, action: CatalogAction, value: Any) -> Any:
        return redact_json(_redact_paths(value, action.response_redaction))

    def _required_credentials(self, credentials: Mapping[str, str]) -> dict[str, str]:
        values: dict[str, str] = {}
        for field in self._credential_fields:
            value = credentials.get(field.name)
            if not isinstance(value, str) or not value:
                raise ConnectorAuthError("credential_required")
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
        if placement not in {"header", "query"} or not key_name:
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
        self._token_urls = immutable_mapping(token_urls)

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
        except httpx.HTTPError:
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
            resolved.append(Path(root).expanduser().resolve(strict=True))
        except OSError:
            raise DriverConfigurationError("multipart_root_invalid") from None
    if not resolved:
        raise DriverConfigurationError("multipart_roots_required")
    return tuple(resolved)


def _response_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


def _company_name(response: httpx.Response) -> str | None:
    value = _response_json(response)
    if not isinstance(value, Mapping):
        return None
    for key in ("company_name", "companyName", "name"):
        candidate = value.get(key)
        if isinstance(candidate, str):
            return redact_text(candidate)
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
        return {str(key): _copy_json(item) for key, item in value.items()}
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
                _redact_path(value[key], remaining)
        elif component in value:
            if remaining:
                _redact_path(value[component], remaining)
            else:
                value[component] = _REDACTED
    elif isinstance(value, list):
        if component == "*":
            for item in value:
                _redact_path(item, remaining)
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


__all__ = [
    "DriverConfigurationError",
    "GenericApiKeyDriver",
    "GenericBasicDriver",
    "GenericBearerDriver",
    "GenericOAuthClientCredentialsDriver",
]
