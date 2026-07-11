"""PEAK HMAC ClientToken connector driver."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

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


class _PeakAuthFailure(ConnectorAuthError):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__("peak_client_token_failed")
        self.details = details


class PeakDriver(_GenericDriver):
    """Provider-specific HMAC ClientToken flow and safe user probe for PEAK."""

    driver_id = "peak_hmac_sha1"
    connector_id = "peak"
    BASE_URLS = {
        "production": "https://api.peakaccount.com/api/v1",
        "uat": "https://peakengineapidev.azurewebsites.net/api/v1",
        "sandbox": "https://peakengineapidev.azurewebsites.net/api/v1",
    }
    _credential_fields = (
        CredentialField("connect_id", secret=False, label="PEAK Connect ID"),
        CredentialField("connect_key", secret=True, label="PEAK Connect Key"),
        CredentialField("application_code", secret=False, label="PEAK Application Code"),
        CredentialField("user_token", secret=True, label="PEAK User Token"),
    )

    def __init__(self) -> None:
        super().__init__(connector_id=self.connector_id, environments=self.BASE_URLS)

    def safe_probe_action(self, environment: str) -> str:
        self.resolve_base_url(environment)
        return "GET /user"

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
            auth, clienttoken_status = await self._prepare_auth_with_status(
                environment=environment,
                credentials=credentials,
                client=client,
            )
        except _PeakAuthFailure as exc:
            return self._failed_probe(environment, exc.details)
        except ConnectorAuthError as exc:
            return self._failed_probe(environment, {"error": str(exc)})

        try:
            response = await client.get(
                f"{self.resolve_base_url(environment)}/user",
                headers=dict(auth.headers),
            )
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError) as exc:
            return self._failed_probe(
                environment,
                {
                    "error": "peak_user_request_failed",
                    "error_type": type(exc).__name__,
                    "clienttoken_status": clienttoken_status,
                },
            )
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            return self._failed_probe(
                environment,
                {
                    "error": "peak_user_response_invalid",
                    "error_type": type(exc).__name__,
                    "clienttoken_status": clienttoken_status,
                    "user_status": response.status_code,
                },
            )
        node = peak_node(payload, "PeakUser")
        if not response.is_success or not peak_success(node):
            return self._failed_probe(
                environment,
                {
                    "error": "peak_user_failed",
                    "clienttoken_status": clienttoken_status,
                    "user_status": response.status_code,
                },
            )
        return ConnectionProbe(
            status="connected",
            connector_id=self.connector_id,
            environment=environment,
            company_name=None,
            details={
                "clienttoken_status": clienttoken_status,
                "user_status": response.status_code,
                "user_res_code": str(node.get("resCode") or ""),
            },
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
        node = _response_node(payload)
        if isinstance(payload, Mapping) and node is not None and not peak_success(node):
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
        base_url = self.resolve_base_url(environment)
        values = self._required_credentials(credentials)
        try:
            response = await client.post(
                f"{base_url}/clienttoken",
                headers=peak_headers(
                    connect_id=values["connect_id"],
                    application_code=values["application_code"],
                    user_token=values["user_token"],
                ),
                json={
                    "PeakClientToken": {
                        "connectId": values["connect_id"],
                        "password": values["connect_key"],
                    }
                },
            )
        except (httpx.HTTPError, httpx.InvalidURL, TypeError, ValueError) as exc:
            raise _PeakAuthFailure(
                {"error": "peak_client_token_request_failed", "error_type": type(exc).__name__}
            ) from None
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise _PeakAuthFailure(
                {
                    "error": "peak_client_token_response_invalid",
                    "error_type": type(exc).__name__,
                    "clienttoken_status": response.status_code,
                }
            ) from None
        node = peak_node(payload, "PeakClientToken")
        client_token = node.get("token")
        if (
            not response.is_success
            or not isinstance(client_token, str)
            or not client_token.strip()
            or not peak_success(node)
        ):
            raise _PeakAuthFailure(
                {"error": "peak_client_token_failed", "clienttoken_status": response.status_code}
            )
        return (
            AuthContext(
                headers=peak_headers(
                    connect_id=values["connect_id"],
                    application_code=values["application_code"],
                    client_token=client_token,
                    user_token=values["user_token"],
                ),
                query={},
                expires_at=datetime.now(UTC) + timedelta(hours=24),
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


def peak_timestamp() -> str:
    return datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S")


def peak_signature(timestamp: str, connect_id: str) -> str:
    return hmac.new(
        connect_id.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()


def peak_headers(
    *,
    connect_id: str,
    application_code: str,
    client_token: str = "",
    user_token: str = "",
    timestamp: str | None = None,
) -> dict[str, str]:
    selected_timestamp = timestamp or peak_timestamp()
    return {
        "Application-Code": application_code,
        "Client-Token": client_token,
        "User-Token": user_token,
        "Time-Stamp": selected_timestamp,
        "Time-Signature": peak_signature(selected_timestamp, connect_id),
        "Content-Type": "application/json",
    }


def peak_node(payload: Any, node_name: str) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    node = payload.get(node_name)
    if isinstance(node, Mapping):
        return dict(node)
    return dict(payload)


def peak_success(node: Mapping[str, Any]) -> bool:
    return str(node.get("resCode") or "").strip() == "200"


def _response_node(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    for name in ("PeakUser", "PeakClientToken"):
        node = payload.get(name)
        if isinstance(node, Mapping):
            return dict(node)
    if "resCode" in payload:
        return dict(payload)
    return None


__all__ = [
    "PeakDriver",
    "peak_headers",
    "peak_node",
    "peak_signature",
    "peak_success",
    "peak_timestamp",
]
