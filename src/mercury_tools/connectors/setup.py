from __future__ import annotations

from typing import Any, Literal

from mercury_tools.connectors.catalog import ConnectorManifest

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
