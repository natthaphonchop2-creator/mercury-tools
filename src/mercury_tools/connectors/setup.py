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


def next_setup_state(
    *,
    has_environment: bool,
    missing_fields: list[str],
) -> ConnectorSetupStatus:
    if not has_environment:
        return "program_selected"
    if missing_fields:
        return "awaiting_credentials"
    return "credentials_received"
