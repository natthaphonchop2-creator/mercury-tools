import pytest

from mercury_tools.config import Settings
from mercury_tools.connectors.catalog import connector_by_id
from mercury_tools.connectors.setup import (
    CONNECTOR_SETUP_STATES,
    next_setup_state,
    required_missing_fields,
)
from mercury_tools.db.product import SupabaseProductStore


def test_setup_states_are_ordered_and_explicit() -> None:
    assert CONNECTOR_SETUP_STATES == [
        "not_started",
        "program_selected",
        "environment_selected",
        "awaiting_credentials",
        "credentials_received",
        "validation_failed",
        "connected_read_only",
        "ready",
    ]


def test_required_missing_fields_uses_manifest() -> None:
    manifest = connector_by_id("flowaccount")
    assert manifest is not None

    assert required_missing_fields(manifest, {}) == ["client_id", "client_secret"]
    assert required_missing_fields(manifest, {"client_id": "abc"}) == ["client_secret"]
    assert required_missing_fields(
        manifest,
        {"client_id": "abc", "client_secret": "def"},
    ) == []


def test_next_setup_state_does_not_skip_credentials() -> None:
    assert (
        next_setup_state(has_environment=False, missing_fields=["client_id"])
        == "program_selected"
    )
    assert (
        next_setup_state(has_environment=True, missing_fields=["client_id"])
        == "awaiting_credentials"
    )
    assert next_setup_state(has_environment=True, missing_fields=[]) == "credentials_received"


class StoreForSetup(SupabaseProductStore):
    def __init__(self):
        super().__init__(
            Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="service-role",
                openai_api_key="",
                connect_signing_secret="signing-secret",
            )
        )
        self.rows: list[dict] = []

    def _request(self, method: str, path: str, **kwargs):
        if path == "mercury_client_tokens" and method == "GET":
            return [
                {
                    "id": "token-1",
                    "status": "active",
                    "workspace_id": "ws-1",
                    "member_id": "member-1",
                    "host_app": "codex",
                    "expires_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_workspaces" and method == "GET":
            return [
                {
                    "id": "ws-1",
                    "workspace_key": "workspace-demo",
                    "name": "Demo Co",
                    "plan": "invite-preview",
                    "status": "active",
                    "metadata": {},
                    "created_at": "2026-07-09T00:00:00+00:00",
                    "updated_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_workspace_members" and method == "GET":
            return [
                {
                    "id": "member-1",
                    "email": "owner@example.com",
                    "role": "owner",
                    "host_app": "codex",
                    "status": "active",
                    "created_at": "2026-07-09T00:00:00+00:00",
                    "last_seen_at": "2026-07-09T00:00:00+00:00",
                }
            ]
        if path == "mercury_connector_profiles" and method == "POST":
            row = {
                **kwargs["json"][0],
                "id": "profile-1",
                "created_at": "2026-07-09T00:00:00+00:00",
                "updated_at": "2026-07-09T00:00:00+00:00",
            }
            self.rows.append(row)
            return [row]
        if path == "mercury_product_events" and method == "POST":
            row = {
                **kwargs["json"][0],
                "id": f"event-{len(self.rows) + 1}",
                "created_at": "2026-07-09T00:00:00+00:00",
            }
            self.rows.append(row)
            return [row]
        raise RuntimeError(f"unexpected request {method} {path}")


def test_start_connector_setup_stores_setup_metadata() -> None:
    store = StoreForSetup()
    profile = store.start_connector_setup(
        token_payload={
            "sub": "owner@example.com",
            "company": "Demo Co",
            "host_app": "codex",
            "iat": 0,
            "exp": 99999,
            "jti": "token-jti",
        },
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )
    assert profile["connector_id"] == "flowaccount"
    assert profile["environment"] == "production"
    assert profile["status"] == "requires_credentials"
    assert profile["metadata"]["setup_state"] == "awaiting_credentials"
    assert profile["metadata"]["required_secret_fields"] == ["client_id", "client_secret"]
    assert profile["metadata"]["preset"]["grant_type"] == "client_credentials"
    assert profile["metadata"]["capabilities"] == [
        "company.info.read",
        "contacts.list",
        "products.list",
        "documents.invoice.list",
        "documents.invoice.get",
        "tax.vat_summary.read",
    ]


def test_start_connector_setup_rejects_unknown_connector() -> None:
    store = StoreForSetup()
    with pytest.raises(ValueError, match="Unknown connector"):
        store.start_connector_setup(
            token_payload={
                "sub": "owner@example.com",
                "company": "Demo Co",
                "host_app": "codex",
                "iat": 0,
                "exp": 99999,
                "jti": "token-jti",
            },
            connector_id="unknown-connector",
            environment="production",
            company_name="Demo Co Books",
        )


def test_start_connector_setup_rejects_invalid_environment() -> None:
    store = StoreForSetup()
    with pytest.raises(ValueError, match="Unsupported environment"):
        store.start_connector_setup(
            token_payload={
                "sub": "owner@example.com",
                "company": "Demo Co",
                "host_app": "codex",
                "iat": 0,
                "exp": 99999,
                "jti": "token-jti",
            },
            connector_id="flowaccount",
            environment="invalid-env",
            company_name="Demo Co Books",
        )
