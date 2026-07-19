from __future__ import annotations

import re
import sys
from pathlib import Path

import httpx
import pytest

from mercury_tools.db.product import (
    public_connector_profile,
    public_product_event,
    public_product_value,
)

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from purge_cloud_erp_secrets import (  # noqa: E402, I001
    CONFIRMATION,
    SupabaseRestClient,
    build_parser,
    redact_high_confidence_secret_values,
    run_purge,
)


MISSING_WRITE_TABLE_ERROR = (
    "Supabase purge request failed: HTTP 404 code=PGRST205 "
    "message=Could not find the table 'public.connector_write_requests' "
    "in the schema cache"
)


class FakePurgeClient:
    def __init__(self, *, missing_write_table: bool = False) -> None:
        self.missing_write_table = missing_write_table
        self.profile_reads = 0
        self.rows = {
            "mercury_connector_profiles": [
                {
                    "id": "profile-1",
                    "status": "connected",
                    "updated_at": "2026-07-11T13:00:00Z",
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
                    "updated_at": "2026-07-11T12:00:00Z",
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
        self.patches: list[tuple[str, str, dict, str | None]] = []
        self.deletions: list[tuple[str, str]] = []

    def list_rows(self, table: str, select: str) -> list[dict]:
        if table == "connector_write_requests" and self.missing_write_table:
            raise RuntimeError(MISSING_WRITE_TABLE_ERROR)
        if table == "mercury_connector_profiles":
            self.profile_reads += 1
        return [dict(row) for row in self.rows.get(table, [])]

    def patch_row(
        self,
        table: str,
        row_id: str,
        payload: dict,
        *,
        expected_updated_at: str | None = None,
    ) -> None:
        self.patches.append((table, row_id, payload, expected_updated_at))
        for row in self.rows.get(table, []):
            if row.get("id") == row_id:
                if (
                    table == "mercury_connector_profiles"
                    and row.get("updated_at") != expected_updated_at
                ):
                    raise RuntimeError("Supabase profile cleanup conflict")
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
        "ciphertext",
        "credential_vault",
        "encrypted_credentials",
        "vault_record",
    ):
        assert f"- '{key}'" in normalized
    assert "status = 'requires_credentials'" in normalized
    assert "where metadata ?| array[" in normalized
    skill_updates = re.findall(
        r"update public\.mercury_skill_catalog\b.*?;",
        normalized,
        flags=re.DOTALL,
    )
    assert len(skill_updates) == 1
    assert "where skill_id = 'flowaccount-journal-posting-th'" in skill_updates[0]
    assert "tags ? 'private'" in skill_updates[0]
    assert "is distinct from" in skill_updates[0]


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
        "credential_fingerprints",
        "MERCURY_PRIVATE_MCP",
        "MERCURY_CREDENTIAL_VAULT_SECRET",
        "Fernet",
        "get_private_connector_context",
    ):
        assert forbidden not in source
    assert source.count('"server_vault"') == 1


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


def test_purge_cli_accepts_explicit_dry_run_and_rejects_conflicting_modes() -> None:
    args = build_parser().parse_args(["--dry-run"])

    assert args.dry_run is True
    assert args.apply is False
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--dry-run", "--apply"])


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
    assert profile_patches[0][3] == "2026-07-11T13:00:00Z"
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


def test_redactor_catches_compact_jwt_payloads() -> None:
    redacted, matches = redact_high_confidence_secret_values(
        "eyJhbGciOiJIUzI1NiJ9.e30.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    )

    assert matches == 1
    assert redacted == "[REDACTED]"


def test_historical_product_event_recursively_strips_legacy_vault_fields() -> None:
    event = {
        "id": "event-legacy",
        "summary": {
            "connector_id": "flowaccount",
            "details": {
                "server_vault": {"ciphertext": "legacy-secret"},
                "safe_note": "keep me",
                "records": [
                    {"credential_vault": "legacy-secret", "status": "ready"},
                    {"encrypted_credentials": {"client_secret": "legacy-secret"}},
                ],
            },
        },
        "metadata": {
            "vault_record": {"ciphertext": "legacy-secret"},
            "source": "historical-import",
        },
    }

    public = public_product_event(event)

    serialized = str(public)
    for forbidden in (
        "server_vault",
        "ciphertext",
        "credential_vault",
        "encrypted_credentials",
        "vault_record",
        "legacy-secret",
    ):
        assert forbidden not in serialized
    assert public["summary"]["connector_id"] == "flowaccount"
    assert public["summary"]["details"]["safe_note"] == "keep me"
    assert public["summary"]["details"]["records"][0]["status"] == "ready"
    assert public["metadata"]["source"] == "historical-import"


def test_public_connector_profile_never_serializes_legacy_vault_or_credential_values() -> None:
    public = public_connector_profile(
        {
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
            "company_ref": "owner@example.com",
            "capability_states": {
                "company.info.read": "observed",
                "access_token": "observed",
            },
            "metadata": {
                "setup_state": "ready",
                "server_vault": {"ciphertext": "legacy-secret"},
                "validation": {"response_body": "provider payload"},
            },
        }
    )

    serialized = str(public)
    for forbidden in (
        "server_vault",
        "ciphertext",
        "legacy-secret",
        "response_body",
        "owner@example.com",
        "access_token",
    ):
        assert forbidden not in serialized
    assert public["capability_states"] == {"company.info.read": "observed"}


def test_public_flow_run_value_strips_spaced_legacy_vault_keys() -> None:
    public = public_product_value(
        {
            "run_id": "flow-run-1",
            "details": {
                "server vault": {"ciphertext": "historical-secret"},
                "status": "completed",
            },
        }
    )

    assert public == {
        "run_id": "flow-run-1",
        "details": {"status": "completed"},
    }


def test_purge_recursively_removes_vault_fields_and_preserves_token_url() -> None:
    client = FakePurgeClient()
    client.rows["mcp_audit_events"] = [
        {
            "id": "audit-legacy",
            "output_summary": {"status": "ok"},
            "metadata": {
                "connector": {
                    "token_url": "https://openapi.flowaccount.com/v1/token",
                    "server_vault": {"ciphertext": "legacy-secret"},
                    "history": [{"vault_record": "legacy-secret"}],
                }
            },
        }
    ]

    report = run_purge(client, apply=True, confirm=CONFIRMATION)

    assert report["high_confidence_secret_matches"] == 4
    metadata = client.rows["mcp_audit_events"][0]["metadata"]
    assert metadata["connector"]["token_url"] == ("https://openapi.flowaccount.com/v1/token")
    assert metadata["connector"]["history"] == [{}]
    assert "server_vault" not in str(metadata)
    assert "vault_record" not in str(metadata)
    assert "legacy-secret" not in str(metadata)


def test_redactor_detects_aws_jwt_prefixed_bearer_and_explicit_secret_values() -> None:
    redacted, matches = redact_high_confidence_secret_values(
        "\n".join(
            (
                "aws=AKIA1234567890ABCDEF",
                "jwt=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue1234",
                "prefixed=sk-live_1234567890abcdef",
                "Authorization: Bearer abc1234567890tokenvalue",
                "secret_key=super-secret-value-1234",
                "api_key=YOUR_API_KEY",
            )
        )
    )

    assert matches == 5
    assert redacted.count("[REDACTED]") == 5
    assert "YOUR_API_KEY" in redacted
    for secret in (
        "AKIA1234567890ABCDEF",
        "eyJhbGciOiJIUzI1NiJ9",
        "sk-live_1234567890abcdef",
        "abc1234567890tokenvalue",
        "super-secret-value-1234",
    ):
        assert secret not in redacted


def test_missing_connector_write_requests_table_counts_zero() -> None:
    client = FakePurgeClient(missing_write_table=True)

    report = run_purge(client, apply=True, confirm=CONFIRMATION)

    assert report["connector_write_request_rows"] == 0
    assert client.deletions == []


def test_non_matching_postgrest_error_still_fails() -> None:
    class BrokenClient(FakePurgeClient):
        def list_rows(self, table: str, select: str) -> list[dict]:
            if table == "connector_write_requests":
                raise RuntimeError(
                    "Supabase purge request failed: HTTP 404 code=PGRST205 "
                    "message=Could not find the table 'public.other_table' in the schema cache"
                )
            return super().list_rows(table, select)

    with pytest.raises(RuntimeError, match="public.other_table"):
        run_purge(BrokenClient())


def test_profile_cleanup_fails_on_optimistic_update_conflict() -> None:
    class ConflictingClient(FakePurgeClient):
        def list_rows(self, table: str, select: str) -> list[dict]:
            rows = super().list_rows(table, select)
            if table == "mercury_connector_profiles" and self.profile_reads == 2:
                self.rows[table][0]["updated_at"] = "2026-07-11T13:00:01Z"
            return rows

    with pytest.raises(RuntimeError, match="profile cleanup conflict"):
        run_purge(ConflictingClient(), apply=True, confirm=CONFIRMATION)


def test_supabase_profile_patch_uses_updated_at_and_requires_exactly_one_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict] = []
    responses = [
        [{"id": "profile-1"}],
        [],
    ]

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append({"method": method, "url": url, **kwargs})
        return httpx.Response(
            200,
            request=httpx.Request(method, url),
            json=responses.pop(0),
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    client = SupabaseRestClient("https://example.supabase.co/rest/v1", "service-role")

    client.patch_row(
        "mercury_connector_profiles",
        "profile-1",
        {"metadata": {"safe": True}},
        expected_updated_at="2026-07-11T13:00:00Z",
    )

    assert calls[0]["params"] == {
        "id": "eq.profile-1",
        "updated_at": "eq.2026-07-11T13:00:00Z",
    }
    assert calls[0]["headers"]["Prefer"] == "return=representation"

    with pytest.raises(RuntimeError, match="profile cleanup conflict"):
        client.patch_row(
            "mercury_connector_profiles",
            "profile-1",
            {"metadata": {"safe": True}},
            expected_updated_at="2026-07-11T13:00:00Z",
        )
