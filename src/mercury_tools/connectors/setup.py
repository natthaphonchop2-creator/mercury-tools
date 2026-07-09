from __future__ import annotations

from typing import Any, Literal

import httpx

from mercury_tools.connectors.catalog import ConnectorManifest
from mercury_tools.safety.redaction import redact_json

ConnectorSetupStatus = Literal[
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected_read_only",
    "ready",
]

CONNECTOR_SETUP_STATES: list[ConnectorSetupStatus] = [
    "not_started",
    "program_selected",
    "environment_selected",
    "awaiting_credentials",
    "credentials_received",
    "validation_failed",
    "connected_read_only",
    "ready",
]


def required_missing_fields(
    manifest: ConnectorManifest,
    credentials: dict[str, Any],
) -> list[str]:
    return [
        field
        for field in manifest.required_secret_fields
        if not str(credentials.get(field) or "").strip()
    ]


def _flowaccount_company_name(payload: dict[str, Any]) -> str | None:
    for key in ("companyName", "company_name", "name"):
        value = payload.get(key)
        if value:
            return str(value)
    return None


def validate_connector_read_only(
    manifest: ConnectorManifest,
    *,
    credentials: dict[str, Any],
    environment: str,
) -> dict[str, Any]:
    if manifest.connector_id != "flowaccount":
        return {
            "status": "validation_failed",
            "message": (
                f"Read-only validation adapter is not available for {manifest.connector_id}."
            ),
        }
    if environment not in manifest.environments:
        return {
            "status": "validation_failed",
            "message": f"Unsupported environment for {manifest.connector_id}: {environment}",
        }

    missing = required_missing_fields(manifest, credentials)
    if missing:
        return {"status": "awaiting_credentials", "missing_fields": missing}

    token_response = httpx.post(
        manifest.preset["token_url"],
        data={
            "grant_type": manifest.preset["grant_type"],
            "scope": manifest.preset["scope"],
            "client_id": str(credentials["client_id"]),
            "client_secret": str(credentials["client_secret"]),
        },
        timeout=60,
    )
    token_payload = token_response.json()
    access_token = str(token_payload.get("access_token") or "")
    if token_response.status_code >= 300 or not access_token:
        return redact_json(
            {
                "status": "validation_failed",
                "http_status": token_response.status_code,
                "message": "Token request failed.",
                "provider_response": token_payload,
            }
        )

    info_url = f"{manifest.preset['api_base_url'].rstrip('/')}/company/info"
    info_response = httpx.get(
        info_url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
    )
    info_payload = info_response.json()
    if info_response.status_code >= 300:
        return redact_json(
            {
                "status": "validation_failed",
                "http_status": info_response.status_code,
                "message": "Company info request failed.",
                "provider_response": info_payload,
            }
        )

    return {
        "status": "connected_read_only",
        "connector_id": manifest.connector_id,
        "environment": environment,
        "company_name": _flowaccount_company_name(info_payload),
        "enabled_capabilities": manifest.capabilities,
        "validation": {
            "token_status": token_response.status_code,
            "company_info_status": info_response.status_code,
        },
    }


def resolve_setup_state(
    *,
    has_program: bool,
    has_environment: bool,
    missing_fields: list[str],
    credentials_received: bool = False,
    validation_status: str | None = None,
    read_only_capability_count: int = 0,
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
        if read_only_capability_count > 0:
            return "ready"
        return "connected_read_only"
    if normalized_validation_status == "connected_read_only":
        return "connected_read_only"

    if credentials_received:
        return "credentials_received"
    return "environment_selected"


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
