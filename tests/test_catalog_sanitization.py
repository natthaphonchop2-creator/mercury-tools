from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from mercury_tools.catalog.identity import validate_credential_safe
from mercury_tools.catalog.importers.sanitize import SanitizationReport, sanitize_spec


def test_sanitize_spec_preserves_schema_names_and_descriptions_but_removes_values() -> None:
    document = {
        "schema": {
            "type": "object",
            "required": ["client_secret", "email"],
            "properties": {
                "client_secret": {
                    "type": "string",
                    "description": "Credential field supplied during setup",
                    "default": "raw-default",
                    "example": "raw-example",
                },
                "email": {
                    "type": "string",
                    "description": "Notification email",
                },
            },
        },
        "headers": [
            {"key": "X-Tenant", "value": "raw-header"},
            {"key": "Authorization", "value": "Bearer raw-token"},
        ],
        "cookies": [{"name": "session", "value": "raw-cookie"}],
        "variable": [{"key": "baseUrl", "value": "https://user:pass@example.test"}],
        "contact": "owner@example.com",
        "url": "https://user:pass@example.test/items?access_token=raw-query",
    }

    sanitized, report = sanitize_spec(document)

    serialized = json.dumps(sanitized)
    assert sanitized["schema"]["required"] == ["client_secret", "email"]
    assert "client_secret" in sanitized["schema"]["properties"]
    assert sanitized["schema"]["x-mercury-property-descriptions"] == [
        {
            "name": "client_secret",
            "description": "Credential field supplied during setup",
        }
    ]
    assert "raw-" not in serialized
    assert "owner@example.com" not in serialized
    assert "user:pass" not in serialized
    assert report == SanitizationReport(redacted_values=8, safe=True)
    assert "raw" not in report.model_dump_json()
    validate_credential_safe(sanitized)


def test_sanitize_spec_is_idempotent_and_report_is_frozen() -> None:
    first, first_report = sanitize_spec(
        {"authorization": "Bearer raw-token", "description": "Keep this description"}
    )
    second, second_report = sanitize_spec(first)

    assert second == first
    assert first_report.redacted_values == 1
    assert second_report == SanitizationReport(redacted_values=0, safe=True)
    with pytest.raises(ValidationError, match="frozen"):
        first_report.redacted_values = 99


def test_sanitize_spec_reuses_task_three_token_and_text_patterns() -> None:
    document = {
        "notes": "password=hunter2 and contact finance@example.com",
        "known_prefix": "ghp_abcdefghijklmnopqrstuvwxyz",
        "tax_contact": "Tax ID 1234567890123",
        "parameter": {"key_name": "X-API-Key", "description": "Preserved"},
    }

    sanitized, report = sanitize_spec(document)

    serialized = json.dumps(sanitized)
    assert "hunter2" not in serialized
    assert "finance@example.com" not in serialized
    assert "1234567890123" not in serialized
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in serialized
    assert sanitized["parameter"] == {
        "key_name": "X-API-Key",
        "description": "Preserved",
    }
    assert report.redacted_values == 3
    validate_credential_safe(sanitized)
