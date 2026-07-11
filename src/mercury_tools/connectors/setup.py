from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from mercury_tools.connectors.catalog import ConnectorManifest
from mercury_tools.drivers.models import ConnectionProbe, to_jsonable

ConnectorSetupStatus = Literal[
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected",
    "ready",
]

CONNECTOR_SETUP_STATES: list[ConnectorSetupStatus] = [
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected",
    "ready",
]

_HEALTHCHECK_MESSAGES = {
    "token_request_failed": "Token request failed before a valid response was received.",
    "token_response_invalid": "Token response was not valid JSON.",
    "flowaccount_token_failed": "Token request failed.",
    "company_info_request_failed": (
        "Company info request failed before a valid response was received."
    ),
    "company_info_response_invalid": "Company info response was not valid JSON.",
    "company_info_failed": "Company info request failed.",
    "peak_client_token_request_failed": (
        "PEAK ClientToken request failed before a valid response was received."
    ),
    "peak_client_token_response_invalid": "PEAK ClientToken response was not valid JSON.",
    "peak_client_token_failed": "PEAK ClientToken request failed.",
    "peak_user_request_failed": "PEAK user request failed before a valid response was received.",
    "peak_user_response_invalid": "PEAK user response was not valid JSON.",
    "peak_user_failed": "PEAK user request failed.",
    "credential_missing": "Connector credentials are incomplete.",
    "credential_blank": "Connector credentials are incomplete.",
    "credential_invalid": "Connector credentials are invalid.",
    "credential_undeclared": "Connector credentials are invalid.",
}


def required_missing_fields(
    manifest: ConnectorManifest,
    credentials: dict[str, Any],
) -> list[str]:
    return [
        field
        for field in manifest.required_secret_fields
        if not str(credentials.get(field) or "").strip()
    ]


async def validate_connector_connection_healthcheck_async(
    manifest: ConnectorManifest,
    *,
    credentials: Mapping[str, Any],
    environment: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Validate built-in provider credentials without exposing provider payloads."""

    if manifest.connector_id not in {"flowaccount", "peak"}:
        return {
            "status": "validation_failed",
            "message": (
                f"Connection healthcheck adapter is not available for {manifest.connector_id}."
            ),
        }
    if environment not in manifest.environments:
        return {
            "status": "validation_failed",
            "message": f"Unsupported environment for {manifest.connector_id}: {environment}",
        }

    missing = required_missing_fields(manifest, dict(credentials))
    if missing:
        return {"status": "awaiting_credentials", "missing_fields": missing}

    if client is None:
        async with httpx.AsyncClient(timeout=60) as owned_client:
            return await validate_connector_connection_healthcheck_async(
                manifest,
                credentials=credentials,
                environment=environment,
                client=owned_client,
            )

    driver = _provider_driver(manifest.connector_id)
    probe = await driver.validate_credentials(
        environment=environment,
        credentials={key: str(value) for key, value in credentials.items()},
        client=client,
    )
    return _compatibility_result(manifest, probe)


def validate_connector_connection_healthcheck(
    manifest: ConnectorManifest,
    *,
    credentials: Mapping[str, Any],
    environment: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Synchronous compatibility wrapper for callers outside an event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _healthcheck_with_transport(
                manifest,
                credentials=credentials,
                environment=environment,
                transport=transport,
            )
        )
    raise RuntimeError("connector_healthcheck_async_required")


async def _healthcheck_with_transport(
    manifest: ConnectorManifest,
    *,
    credentials: Mapping[str, Any],
    environment: str,
    transport: httpx.AsyncBaseTransport | None,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=60, transport=transport) as client:
        return await validate_connector_connection_healthcheck_async(
            manifest,
            credentials=credentials,
            environment=environment,
            client=client,
        )


def _provider_driver(connector_id: str):
    if connector_id == "flowaccount":
        from mercury_tools.drivers.flowaccount import FlowAccountDriver

        return FlowAccountDriver()
    if connector_id == "peak":
        from mercury_tools.drivers.peak import PeakDriver

        return PeakDriver()
    raise ValueError("connector_driver_not_found")


def _compatibility_result(
    manifest: ConnectorManifest,
    probe: ConnectionProbe,
) -> dict[str, Any]:
    details = to_jsonable(probe.details)
    if probe.status == "connected":
        return {
            "status": "connected",
            "connector_id": manifest.connector_id,
            "environment": probe.environment,
            "company_name": probe.company_name,
            "enabled_capabilities": list(manifest.capabilities),
            "validation": details,
        }

    error = details.get("error") if isinstance(details, dict) else None
    result: dict[str, Any] = {
        "status": "validation_failed",
        "message": _HEALTHCHECK_MESSAGES.get(
            error if isinstance(error, str) else "",
            "Connection healthcheck failed.",
        ),
    }
    if isinstance(details, dict):
        if isinstance(details.get("http_status"), int):
            result["http_status"] = details["http_status"]
        if isinstance(details.get("error_type"), str):
            result["error_type"] = details["error_type"]
    return result


def resolve_setup_state(
    *,
    has_program: bool,
    has_environment: bool,
    missing_fields: list[str],
    credentials_received: bool = False,
    validation_status: str | None = None,
    validated_capability_count: int = 0,
) -> ConnectorSetupStatus:
    if not has_program:
        return "not_started"
    if not has_environment:
        return "program_selected"
    if missing_fields and not credentials_received:
        return "awaiting_credentials"

    normalized_validation_status = (validation_status or "").strip().lower()
    if normalized_validation_status in {"failed", "failure", "invalid", "error"}:
        return "validation_failed"
    if normalized_validation_status in {"ready"}:
        return "ready"
    if normalized_validation_status in {"passed", "success", "valid", "validated"}:
        if validated_capability_count > 0:
            return "ready"
        return "connected"
    if normalized_validation_status in {"connected", "connected_read_only"}:
        return "connected"

    if credentials_received:
        return "credentials_received"
    return "environment_selected"


def validate_connector_read_only(
    manifest: ConnectorManifest,
    *,
    credentials: Mapping[str, Any],
    environment: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Deprecated alias for connection healthcheck validation."""
    return validate_connector_connection_healthcheck(
        manifest,
        credentials=credentials,
        environment=environment,
        transport=transport,
    )


def next_setup_state(
    *,
    has_environment: bool,
    missing_fields: list[str],
) -> ConnectorSetupStatus:
    return resolve_setup_state(
        has_program=True,
        has_environment=has_environment,
        missing_fields=missing_fields,
        credentials_received=not missing_fields,
    )
