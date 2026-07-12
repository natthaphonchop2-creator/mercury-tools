from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from purge_cloud_erp_secrets import (  # noqa: E402, I001
    CONFIRMATION,
    redact_high_confidence_secret_values,
    run_purge,
)

class FakePurgeClient:
    def __init__(self) -> None:
        self.rows = {
            "mercury_connector_profiles": [
                {
                    "id": "profile-1",
                    "status": "connected",
                    "metadata": {
                        "setup_state": "ready",
                        "server_vault": {"ciphertext": "secret"},
                        "credential_fields": ["client_secret"],
                        "credential_fingerprints": {"client_secret": "hash"},
                        "credentials_configured": True,
                        "credentials_configured_at": "2026-07-11T13:00:00Z",
                        "credential_storage": "encrypted_server_vault",
                        "configured_at": "2026-07-10T12:00:00Z",
                        "storage": "product_profile",
                    },
                },
                {
                    "id": "profile-2",
                    "status": "available",
                    "metadata": {
                        "setup_state": "available",
                        "configured_at": "2026-07-09T12:00:00Z",
                        "storage": "product_profile",
                    },
                },
            ],
            "connector_write_requests": [{"id": "write-1"}],
            "mercury_skill_uploads": [
                {
                    "id": "upload-1",
                    "markdown": (
                        "Authorization: Bearer "
                        "abc1234567890tokenvalue\nAPI key: YOUR_API_KEY"
                    ),
                }
            ],
            "knowledge_documents": [],
            "knowledge_chunks": [],
            "mercury_product_events": [
                {"id": "event-1", "summary": {"api_key": "live-api-key-123456789"}, "metadata": {}}
            ],
            "mcp_audit_events": [],
        }
        self.patches: list[tuple[str, str, dict]] = []
        self.deletions: list[tuple[str, str]] = []

    def list_rows(self, table: str, select: str) -> list[dict]:
        return [dict(row) for row in self.rows.get(table, [])]

    def patch_row(self, table: str, row_id: str, payload: dict) -> None:
        self.patches.append((table, row_id, payload))
        for row in self.rows.get(table, []):
            if row.get("id") == row_id:
                row.update(payload)

    def delete_row(self, table: str, row_id: str) -> None:
        self.deletions.append((table, row_id))
        self.rows[table] = [row for row in self.rows.get(table, []) if row.get("id") != row_id]


def test_cleanup_migration_removes_vault_data_and_write_table() -> None:
    sql = (ROOT / "supabase/migrations/20260711130000_remove_cloud_erp_secrets.sql").read_text()
    normalized = sql.lower()

    assert "drop table if exists public.connector_write_requests" in normalized
    for key in (
        "server_vault",
        "credential_fingerprints",
        "credential_fields",
        "credentials_configured",
        "credentials_configured_at",
        "credential_storage",
    ):
        assert f"- '{key}'" in normalized
    assert "status = 'requires_credentials'" in normalized
    assert "where metadata ?| array[" in normalized
    assert "tags = tags - 'private'" in normalized


def test_render_blueprint_has_no_private_or_vault_env() -> None:
    text = (ROOT / "render.yaml").read_text()

    assert "MERCURY_PRIVATE_MCP" not in text
    assert "MERCURY_CREDENTIAL_VAULT_SECRET" not in text
    assert "MERCURY_CONNECT_SIGNING_SECRET" in text


def test_live_source_has_no_cloud_secret_or_private_runtime_surface() -> None:
    source = "\n".join(
        path.read_text()
        for path in (ROOT / "src").rglob("*.py")
    )
    for forbidden in (
        "PrivateBearerAuthMiddleware",
        "submit_connector_credentials",
        "server_vault",
        "credential_fingerprints",
        "MERCURY_PRIVATE_MCP",
        "MERCURY_CREDENTIAL_VAULT_SECRET",
        "Fernet",
        "get_private_connector_context",
    ):
        assert forbidden not in source


def test_purge_defaults_to_value_free_dry_run() -> None:
    client = FakePurgeClient()

    report = run_purge(client)

    assert report == {
        "mode": "dry-run",
        "profile_rows": 1,
        "connector_write_request_rows": 1,
        "high_confidence_secret_matches": 2,
        "redacted_rows": 0,
    }
    assert client.patches == []
    assert client.deletions == []
    assert "live-api-key-123456789" not in str(report)


def test_purge_apply_requires_exact_confirmation() -> None:
    with pytest.raises(ValueError, match="DELETE_SERVER_ERP_SECRETS"):
        run_purge(FakePurgeClient(), apply=True, confirm="wrong")


def test_purge_apply_redacts_and_is_idempotent() -> None:
    client = FakePurgeClient()

    report = run_purge(client, apply=True, confirm=CONFIRMATION)

    assert report["mode"] == "apply"
    assert report["profile_rows"] == 1
    assert report["connector_write_request_rows"] == 1
    assert report["high_confidence_secret_matches"] == 2
    assert report["redacted_rows"] == 2
    profile = client.rows["mercury_connector_profiles"][0]
    assert profile["status"] == "requires_credentials"
    assert profile["metadata"] == {
        "setup_state": "ready",
        "configured_at": "2026-07-10T12:00:00Z",
        "storage": "product_profile",
    }
    untouched_profile = client.rows["mercury_connector_profiles"][1]
    assert untouched_profile["status"] == "available"
    assert untouched_profile["metadata"]["storage"] == "product_profile"
    profile_patches = [
        patch for patch in client.patches if patch[0] == "mercury_connector_profiles"
    ]
    assert [patch[1] for patch in profile_patches] == ["profile-1"]
    assert client.rows["connector_write_requests"] == []
    assert "[REDACTED]" in client.rows["mercury_skill_uploads"][0]["markdown"]
    assert client.rows["mercury_product_events"][0]["summary"]["api_key"] == "[REDACTED]"
    assert "YOUR_API_KEY" in client.rows["mercury_skill_uploads"][0]["markdown"]

    assert run_purge(client) == {
        "mode": "dry-run",
        "profile_rows": 0,
        "connector_write_request_rows": 0,
        "high_confidence_secret_matches": 0,
        "redacted_rows": 0,
    }


def test_redactor_preserves_documentation_placeholders() -> None:
    redacted, matches = redact_high_confidence_secret_values(
        "Bearer abc1234567890tokenvalue; API key: YOUR_API_KEY"
    )

    assert matches == 1
    assert "Bearer [REDACTED]" in redacted
    assert "YOUR_API_KEY" in redacted
