from __future__ import annotations

from typing import Any, Literal

import httpx

from mercury_tools.connectors.catalog import ConnectorManifest
from mercury_tools.safety.redaction import redact_json

_SENSITIVE_VALIDATION_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "ciphertext",
    "client_id",
    "client_secret",
    "credential_fingerprints",
    "id_token",
    "password",
    "refresh_token",
}

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


def _collect_sensitive_values(value: Any, collected: list[str]) -> None:
    if isinstance(value, dict):
        for item in value.values():
            _collect_sensitive_values(item, collected)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            _collect_sensitive_values(item, collected)
        return
    if value is None:
        return

    text = str(value)
    if text.strip():
        collected.append(text)


def _collect_values_from_sensitive_keys(value: Any, collected: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if _is_sensitive_validation_key(key):
                _collect_sensitive_values(item, collected)
            else:
                _collect_values_from_sensitive_keys(item, collected)
        return
    if isinstance(value, list | tuple | set):
        for item in value:
            _collect_values_from_sensitive_keys(item, collected)


def _sensitive_values(
    credentials: dict[str, Any],
    extra_sensitive_values: tuple[Any, ...] = (),
    provider_response: Any | None = None,
) -> tuple[str, ...]:
    values: list[str] = []
    _collect_sensitive_values(credentials, values)
    for item in extra_sensitive_values:
        _collect_sensitive_values(item, values)
    if provider_response is not None:
        _collect_values_from_sensitive_keys(provider_response, values)

    return tuple(dict.fromkeys(sorted(values, key=len, reverse=True)))


def _is_sensitive_validation_key(key: Any) -> bool:
    normalized = str(key).strip().lower().replace("-", "_")
    return normalized in _SENSITIVE_VALIDATION_KEYS or any(
        marker in normalized
        for marker in (
            "api_key",
            "apikey",
            "authorization",
            "ciphertext",
            "client_id",
            "client_secret",
            "credential",
            "fingerprint",
            "password",
            "secret",
            "token",
        )
    )


def _mask_sensitive_text(text: str, sensitive_values: tuple[str, ...]) -> str:
    masked = text
    for value in sensitive_values:
        masked = masked.replace(value, "[REDACTED]")
    return str(redact_json(masked))


def _sanitize_validation_failure_key(
    key: Any,
    sensitive_values: tuple[str, ...],
) -> Any:
    if _is_sensitive_validation_key(key):
        return "[REDACTED_KEY]"

    key_text = str(key)
    masked_key = _mask_sensitive_text(key_text, sensitive_values)
    if masked_key != key_text:
        return masked_key
    return key


def _dedupe_validation_key(key: Any, existing: dict[Any, Any]) -> Any:
    if key not in existing:
        return key

    base = str(key)
    counter = 2
    candidate = f"{base}_{counter}"
    while candidate in existing:
        counter += 1
        candidate = f"{base}_{counter}"
    return candidate


def _sanitize_validation_failure_value(
    value: Any,
    sensitive_values: tuple[str, ...],
) -> Any:
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            redacted_key = _dedupe_validation_key(
                _sanitize_validation_failure_key(key, sensitive_values),
                redacted,
            )
            if _is_sensitive_validation_key(key):
                redacted[redacted_key] = "[REDACTED]"
            else:
                redacted[redacted_key] = _sanitize_validation_failure_value(
                    item,
                    sensitive_values,
                )
        return redact_json(redacted)
    if isinstance(value, list):
        return [
            _sanitize_validation_failure_value(item, sensitive_values)
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _sanitize_validation_failure_value(item, sensitive_values)
            for item in value
        )
    if isinstance(value, str):
        return _mask_sensitive_text(value, sensitive_values)
    if value is not None and str(value) in sensitive_values:
        return "[REDACTED]"
    return value


def _validation_failed(
    message: str,
    *,
    credentials: dict[str, Any],
    http_status: int | None = None,
    provider_response: Any | None = None,
    error: BaseException | None = None,
    extra_sensitive_values: tuple[Any, ...] = (),
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "validation_failed",
        "message": message,
    }
    if http_status is not None:
        payload["http_status"] = http_status
    if provider_response is not None:
        payload["provider_response"] = provider_response
    if error is not None:
        payload["error_type"] = error.__class__.__name__
        payload["error"] = str(error)

    return _sanitize_validation_failure_value(
        payload,
        _sensitive_values(
            credentials,
            extra_sensitive_values,
            provider_response=provider_response,
        ),
    )


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

    preset = manifest.preset_for_environment(environment)
    try:
        token_response = httpx.post(
            preset["token_url"],
            data={
                "grant_type": preset["grant_type"],
                "scope": preset["scope"],
                "client_id": str(credentials["client_id"]),
                "client_secret": str(credentials["client_secret"]),
            },
            timeout=60,
        )
    except httpx.HTTPError as exc:
        return _validation_failed(
            "Token request failed before a valid response was received.",
            credentials=credentials,
            error=exc,
        )

    try:
        token_payload = token_response.json()
    except ValueError as exc:
        return _validation_failed(
            "Token response was not valid JSON.",
            credentials=credentials,
            http_status=token_response.status_code,
            error=exc,
        )
    if not isinstance(token_payload, dict):
        return _validation_failed(
            "Token response JSON was not an object.",
            credentials=credentials,
            http_status=token_response.status_code,
            provider_response=token_payload,
        )

    access_token = str(token_payload.get("access_token") or "")
    if token_response.status_code >= 300 or not access_token:
        return _validation_failed(
            "Token request failed.",
            credentials=credentials,
            http_status=token_response.status_code,
            provider_response=token_payload,
            extra_sensitive_values=(access_token,),
        )

    info_url = f"{preset['api_base_url'].rstrip('/')}/company/info"
    try:
        info_response = httpx.get(
            info_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=60,
        )
    except httpx.HTTPError as exc:
        return _validation_failed(
            "Company info request failed before a valid response was received.",
            credentials=credentials,
            error=exc,
            extra_sensitive_values=(access_token,),
        )

    try:
        info_payload = info_response.json()
    except ValueError as exc:
        return _validation_failed(
            "Company info response was not valid JSON.",
            credentials=credentials,
            http_status=info_response.status_code,
            error=exc,
            extra_sensitive_values=(access_token,),
        )
    if not isinstance(info_payload, dict):
        return _validation_failed(
            "Company info response JSON was not an object.",
            credentials=credentials,
            http_status=info_response.status_code,
            provider_response=info_payload,
            extra_sensitive_values=(access_token,),
        )

    if info_response.status_code >= 300:
        return _validation_failed(
            "Company info request failed.",
            credentials=credentials,
            http_status=info_response.status_code,
            provider_response=info_payload,
            extra_sensitive_values=(access_token,),
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
