"""FlowAccount OAuth connector driver."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, unquote_plus

import httpx

from mercury_tools.catalog.models import CatalogAction
from mercury_tools.drivers.base import ConnectorAuthError
from mercury_tools.drivers.generic import _GenericDriver
from mercury_tools.drivers.models import (
    AuthContext,
    ConnectionProbe,
    ConnectorResult,
    CredentialField,
)


class _FlowAccountAuthFailure(ConnectorAuthError):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__("flowaccount_token_failed")
        self.details = details


class FlowAccountDriver(_GenericDriver):
    """Provider-specific OAuth flow and safe company probe for FlowAccount."""

    driver_id = "flowaccount_oauth"
    connector_id = "flowaccount"
    BASE_URLS = {
        "production": "https://openapi.flowaccount.com/v1",
        "sandbox": "https://openapi.flowaccount.com/test",
    }
    TOKEN_URLS = {
        "production": "https://openapi.flowaccount.com/v1/token",
        "sandbox": "https://openapi.flowaccount.com/test/token",
    }
    _credential_fields = (
        CredentialField("client_id", secret=False, label="FlowAccount Client ID"),
        CredentialField("client_secret", secret=True, label="FlowAccount Client Secret"),
    )

    def __init__(self) -> None:
        super().__init__(connector_id=self.connector_id, environments=self.BASE_URLS)

    def safe_probe_action(self, environment: str) -> str:
        self.resolve_base_url(environment)
        return "GET /company/info"

    async def prepare_auth(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> AuthContext:
        auth, _ = await self._prepare_auth_with_status(
            environment=environment,
            credentials=credentials,
            client=client,
        )
        return auth

    async def validate_credentials(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> ConnectionProbe:
        try:
            auth, token_status = await self._prepare_auth_with_status(
                environment=environment,
                credentials=credentials,
                client=client,
            )
        except _FlowAccountAuthFailure as exc:
            return self._failed_probe(environment, exc.details)
        except ConnectorAuthError as exc:
            return self._failed_probe(environment, {"error": str(exc)})

        try:
            response = await client.get(
                f"{self.resolve_base_url(environment)}/company/info",
                headers=dict(auth.headers),
            )
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError) as exc:
            return self._failed_probe(
                environment,
                {"error": "company_info_request_failed", "error_type": type(exc).__name__},
            )

        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._failed_probe(
                environment,
                {
                    "error": "company_info_response_invalid",
                    "error_type": type(exc).__name__,
                    "http_status": response.status_code,
                },
            )
        if not isinstance(payload, Mapping):
            return self._failed_probe(
                environment,
                {"error": "company_info_response_invalid", "http_status": response.status_code},
            )
        if not response.is_success or _flowaccount_body_failed(payload):
            return self._failed_probe(
                environment,
                {"error": "company_info_failed", "http_status": response.status_code},
            )

        sensitive_values = (*credentials.values(), *_auth_sensitive_values(auth))
        return ConnectionProbe(
            status="connected",
            connector_id=self.connector_id,
            environment=environment,
            company_name=_company_name(payload, sensitive_values),
            details={"token_status": token_status, "company_info_status": response.status_code},
        )

    def interpret_response(
        self,
        *,
        action: CatalogAction,
        response: httpx.Response,
        dispatched: bool,
    ) -> ConnectorResult:
        result = super().interpret_response(
            action=action,
            response=response,
            dispatched=dispatched,
        )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            return result
        if isinstance(payload, Mapping) and _flowaccount_body_failed(payload):
            return ConnectorResult(
                status="failed",
                http_status=response.status_code,
                data=result.data,
                summary="provider_response_failed",
                dispatched=dispatched,
            )
        return result

    async def _prepare_auth_with_status(
        self,
        *,
        environment: str,
        credentials: Mapping[str, str],
        client: httpx.AsyncClient,
    ) -> tuple[AuthContext, int]:
        self.resolve_base_url(environment)
        values = self._required_credentials(credentials)
        try:
            response = await client.post(
                self.TOKEN_URLS[environment],
                data={
                    "grant_type": "client_credentials",
                    "scope": "flowaccount-api",
                    "client_id": values["client_id"],
                    "client_secret": values["client_secret"],
                },
            )
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError) as exc:
            raise _FlowAccountAuthFailure(
                {"error": "token_request_failed", "error_type": type(exc).__name__}
            ) from None
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise _FlowAccountAuthFailure(
                {
                    "error": "token_response_invalid",
                    "error_type": type(exc).__name__,
                    "http_status": response.status_code,
                }
            ) from None
        if not isinstance(payload, Mapping):
            raise _FlowAccountAuthFailure(
                {"error": "token_response_invalid", "http_status": response.status_code}
            )
        access_token = payload.get("access_token")
        if (
            not response.is_success
            or not isinstance(access_token, str)
            or not access_token.strip()
            or _flowaccount_body_failed(payload)
            or _credential_token_collision(access_token, values.values())
        ):
            raise _FlowAccountAuthFailure(
                {"error": "flowaccount_token_failed", "http_status": response.status_code}
            )
        return (
            AuthContext(
                headers={"Authorization": f"Bearer {access_token}"},
                query={},
                expires_at=_expires_at(payload.get("expires_in")),
            ),
            response.status_code,
        )

    def _failed_probe(self, environment: str, details: dict[str, Any]) -> ConnectionProbe:
        return ConnectionProbe(
            status="failed",
            connector_id=self.connector_id,
            environment=environment,
            company_name=None,
            details=details,
        )


def _flowaccount_body_failed(payload: Mapping[str, Any]) -> bool:
    if _false_provider_flag(payload.get("status")) or _false_provider_flag(payload.get("success")):
        return True
    return (
        _nonzero_provider_code(payload.get("code"))
        or _nonzero_provider_code(payload.get("resCode"))
        or bool(payload.get("error"))
    )


def _false_provider_flag(value: Any) -> bool:
    return value is False or isinstance(value, str) and value.strip().lower() in {
        "false",
        "failed",
        "failure",
        "error",
    }


def _nonzero_provider_code(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, bool):
        return True
    if isinstance(value, int | float):
        return value != 0
    if isinstance(value, str) and value.strip():
        try:
            return float(value) != 0
        except ValueError:
            return True
    return False


def _expires_at(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return datetime.now(UTC) + timedelta(seconds=value)


def _company_name(payload: Mapping[str, Any], sensitive_values: tuple[str, ...]) -> str | None:
    for key in ("companyName", "company_name", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return _redact_sensitive_text(value, sensitive_values)
    return None


def _redact_sensitive_text(value: str, sensitive_values: tuple[str, ...]) -> str:
    candidates = _reversibly_decoded_values(value)
    if any(
        secret
        and any(
            normalized_secret in candidate
            for normalized_secret in _reversibly_decoded_values(secret)
            for candidate in candidates
        )
        for secret in sensitive_values
    ):
        return "[REDACTED]"
    return value


def _credential_token_collision(token: str, credential_values: Any) -> bool:
    token_variants = _reversibly_decoded_values(token)
    return any(
        token_variants.intersection(_reversibly_decoded_values(value))
        for value in credential_values
        if isinstance(value, str)
    )


def _reversibly_decoded_values(value: str) -> set[str]:
    values = {value}
    pending = {value}
    for _ in range(2):
        decoded = {decoded for item in pending for decoded in (unquote(item), unquote_plus(item))}
        pending = decoded - values
        values.update(pending)
        if not pending:
            break
    return values


def _auth_sensitive_values(auth: AuthContext) -> tuple[str, ...]:
    values: list[str] = []
    for value in auth.headers.values():
        values.append(value)
        if " " in value:
            _, token = value.split(" ", 1)
            if token:
                values.append(token)
    return tuple(values)


__all__ = ["FlowAccountDriver"]
