from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mercury_tools.config import Settings
from mercury_tools.db.journal_writes import SupabaseJournalWriteStore
from mercury_tools.db.product import (
    SupabaseProductStore,
    encrypt_connector_credentials,
)

WORKSPACE_UUID = "10000000-0000-0000-0000-000000000001"
PROFILE_UUID = "20000000-0000-0000-0000-000000000001"


def settings() -> Settings:
    return Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="service-role",
        openai_api_key="",
        connect_signing_secret="vault-secret",
    )


def test_preview_payload_is_encrypted_and_can_be_loaded(monkeypatch) -> None:
    store = SupabaseJournalWriteStore(settings())
    captured: dict = {}

    def fake_request(method, path, **kwargs):
        assert path == "connector_write_requests"
        if method == "POST":
            captured.update(kwargs["json"][0])
            return [{**captured, "id": "30000000-0000-0000-0000-000000000001"}]
        assert method == "GET"
        return [{**captured, "id": "30000000-0000-0000-0000-000000000001"}]

    monkeypatch.setattr(store, "_request", fake_request)
    row = store.create_preview(
        workspace_uuid=WORKSPACE_UUID,
        connector_profile_id=PROFILE_UUID,
        workspace_key="demo-workspace",
        environment="production",
        input_hash="a" * 64,
        payload={
            "flowaccount_payload": {"reference": "REF-1"},
            "preview": {"total_debit": "4236.00"},
        },
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=10),
    )

    assert row["request_key"].startswith("mjp_")
    assert "REF-1" not in captured["encrypted_payload"]
    loaded = store.load_request(
        request_key=row["request_key"],
        workspace_uuid=WORKSPACE_UUID,
        workspace_key="demo-workspace",
    )
    assert loaded is not None
    assert loaded["payload"]["flowaccount_payload"]["reference"] == "REF-1"


def test_claim_preview_is_an_atomic_state_transition(monkeypatch) -> None:
    store = SupabaseJournalWriteStore(settings())
    captured: dict = {}

    def fake_request(method, path, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return [{"request_key": "mjp_preview", "status": "executing"}]

    monkeypatch.setattr(store, "_request", fake_request)
    row = store.claim_preview(
        request_key="mjp_preview",
        workspace_uuid=WORKSPACE_UUID,
        now=datetime(2026, 7, 10, tzinfo=UTC),
    )

    assert row["status"] == "executing"
    assert captured["method"] == "PATCH"
    assert captured["params"]["status"] == "eq.previewed"
    assert captured["params"]["expires_at"].startswith("gt.")
    assert captured["json"]["status"] == "executing"


def test_duplicate_lookup_ignores_the_current_preview(monkeypatch) -> None:
    store = SupabaseJournalWriteStore(settings())
    captured: dict = {}

    def fake_request(method, path, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(store, "_request", fake_request)
    duplicate = store.find_blocking_duplicate(
        workspace_uuid=WORKSPACE_UUID,
        connector_profile_id=PROFILE_UUID,
        input_hash="b" * 64,
        exclude_request_key="mjp_current",
    )

    assert duplicate is None
    assert captured["params"]["request_key"] == "neq.mjp_current"
    assert "draft_created" in captured["params"]["status"]


def test_private_connector_context_returns_decrypted_ready_profile(monkeypatch) -> None:
    product = SupabaseProductStore(settings())
    workspace = {
        "id": WORKSPACE_UUID,
        "workspace_key": "demo-workspace",
        "name": "Demo Co",
    }
    vault = encrypt_connector_credentials(
        settings(),
        workspace_key_value="demo-workspace",
        connector_id="flowaccount",
        environment="production",
        credentials={"client_id": "demo-id", "client_secret": "demo-secret"},
    )
    monkeypatch.setattr(
        product,
        "workspace_for_token",
        lambda token_payload: {"workspace": workspace},
    )
    monkeypatch.setattr(
        product,
        "_request",
        lambda method, path, **kwargs: [
            {
                "id": PROFILE_UUID,
                "connector_id": "flowaccount",
                "environment": "production",
                "status": "ready",
                "metadata": {"setup_state": "ready", "server_vault": vault},
            }
        ],
    )

    context = product.get_private_connector_context(
        "mw_publiccontestworkspace001",
        "flowaccount",
    )

    assert context["workspace_uuid"] == WORKSPACE_UUID
    assert context["connector_profile_id"] == PROFILE_UUID
    assert context["environment"] == "production"
    assert context["preset"]["token_url"] == (
        "https://openapi.flowaccount.com/v1/token"
    )
    assert context["credentials"] == {
        "client_id": "demo-id",
        "client_secret": "demo-secret",
    }


def test_private_connector_context_rejects_multiple_ready_profiles(monkeypatch) -> None:
    product = SupabaseProductStore(settings())
    monkeypatch.setattr(
        product,
        "workspace_for_token",
        lambda token_payload: {
            "workspace": {"id": WORKSPACE_UUID, "workspace_key": "demo-workspace"}
        },
    )
    profile = {
        "id": PROFILE_UUID,
        "connector_id": "flowaccount",
        "environment": "production",
        "status": "ready",
        "metadata": {"setup_state": "ready", "server_vault": {}},
    }
    monkeypatch.setattr(
        product,
        "_request",
        lambda method, path, **kwargs: [profile, {**profile, "id": "other"}],
    )

    with pytest.raises(ValueError, match="Exactly one ready FlowAccount"):
        product.get_private_connector_context(
            "mw_publiccontestworkspace001",
            "flowaccount",
        )
