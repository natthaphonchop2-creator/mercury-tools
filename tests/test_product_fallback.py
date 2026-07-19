import pytest

from mercury_tools.config import Settings
from mercury_tools.db.product import (
    PRODUCT_FALLBACK_LIMIT,
    SKILL_CATALOG_SEED,
    SupabaseProductStore,
    connector_profile_status,
    public_connector_profile,
)
from mercury_tools.flows.runner import MercuryFlowRunner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.product import ConnectRequest


class AuditFallbackStore(SupabaseProductStore):
    def __init__(self):
        super().__init__(
            Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="service-role",
                openai_api_key="",
                connect_signing_secret="signing-secret",
            )
        )
        self.events: list[dict] = []

    def _request(self, method: str, path: str, **kwargs):
        if path.startswith("mercury_"):
            raise RuntimeError(
                f"Supabase product request failed: HTTP 404 could not find {path}"
            )
        if path != "mcp_audit_events":
            raise AssertionError(f"unexpected path: {path}")
        if method == "POST":
            row = {
                **kwargs["json"][0],
                "id": f"evt-{len(self.events) + 1}",
                "created_at": f"2026-07-09T00:00:0{len(self.events)}+00:00",
            }
            self.events.append(row)
            return [row]
        if method == "GET":
            return list(self.events)
        raise AssertionError(f"unexpected method: {method}")


class PaginatedAuditFallbackStore(AuditFallbackStore):
    def __init__(self):
        super().__init__()
        self.state_get_params: list[dict] = []

    def _request(self, method: str, path: str, **kwargs):
        if path == "mcp_audit_events" and method == "GET":
            params = dict(kwargs.get("params") or {})
            self.state_get_params.append(params)
            rows = [
                row
                for row in self.events
                if row.get("tool_name") == params.get("tool_name", "").removeprefix("eq.")
            ]
            workspace_filter = params.get("metadata->>workspace_key")
            if workspace_filter:
                expected_workspace = str(workspace_filter).removeprefix("eq.")
                rows = [
                    row
                    for row in rows
                    if (row.get("metadata") or {}).get("workspace_key")
                    == expected_workspace
                ]
            rows.sort(key=lambda row: (str(row.get("created_at")), str(row.get("id"))))
            offset = int(params.get("offset") or 0)
            limit = int(params.get("limit") or PRODUCT_FALLBACK_LIMIT)
            return rows[offset : offset + limit]
        return super()._request(method, path, **kwargs)


class ProductTableStore(SupabaseProductStore):
    def __init__(self):
        super().__init__(
            Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="service-role",
                openai_api_key="",
                connect_signing_secret="signing-secret",
            )
        )
        self.profiles: dict[tuple[str, str, str, str], dict] = {}

    def _request(self, method: str, path: str, **kwargs):
        if path == "mercury_client_tokens" and method == "GET":
            return [
                {
                    "id": "token-1",
                    "status": "active",
                    "workspace_id": "ws-1",
                    "member_id": "member-1",
                    "host_app": "codex",
                    "scopes": ["mcp:read"],
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "revoked_at": None,
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
                    "created_at": "2026-07-19T00:00:00+00:00",
                    "updated_at": "2026-07-19T00:00:00+00:00",
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
                    "created_at": "2026-07-19T00:00:00+00:00",
                    "last_seen_at": "2026-07-19T00:00:00+00:00",
                }
            ]
        if path == "mercury_connector_profiles" and method == "GET":
            params = kwargs.get("params") or {}
            key = tuple(
                str(params.get(field) or "").removeprefix("eq.")
                for field in (
                    "workspace_id",
                    "connector_id",
                    "connection_mode",
                    "environment",
                )
            )
            profile = self.profiles.get(key)
            return [profile] if profile else []
        if path == "mercury_connector_profiles" and method == "POST":
            payload = kwargs["json"][0]
            key = (
                payload["workspace_id"],
                payload["connector_id"],
                payload["connection_mode"],
                payload["environment"],
            )
            existing = self.profiles.get(key) or {}
            row = {
                **existing,
                **payload,
                "id": existing.get("id") or "profile-1",
                "created_at": existing.get("created_at")
                or "2026-07-19T00:00:00+00:00",
                "updated_at": "2026-07-19T00:00:00+00:00",
            }
            self.profiles[key] = row
            return [row]
        if path == "mercury_product_events" and method == "POST":
            return [
                {
                    **kwargs["json"][0],
                    "id": "event-1",
                    "created_at": "2026-07-19T00:00:00+00:00",
                }
            ]
        raise AssertionError(f"unexpected request: {method} {path}")


def token_payload() -> dict:
    return {
        "sub": "owner@example.com",
        "company": "Demo Co",
        "host_app": "codex",
        "iat": 1783536613,
        "exp": 1786128613,
        "jti": "client-jti",
        "scope": ["mcp:read"],
    }


def test_product_store_creates_public_workspace_and_resolves_dashboard() -> None:
    store = AuditFallbackStore()

    created = store.create_public_workspace("Public Demo Co")
    dashboard = store.public_dashboard(created["workspace_id"])

    assert created["workspace_id"].startswith("mw_")
    assert created["workspace"]["name"] == "Public Demo Co"
    assert dashboard["status"] == "ok"
    assert dashboard["public_mode"] is True
    assert dashboard["workspace_id"] == created["workspace_id"]
    assert dashboard["workspace"]["name"] == "Public Demo Co"


def test_public_workspaces_with_the_same_display_name_are_isolated() -> None:
    store = AuditFallbackStore()

    first = store.create_public_workspace("Shared Company Name")
    second = store.create_public_workspace("Shared Company Name")

    assert first["workspace_id"] != second["workspace_id"]
    assert first["workspace"]["id"] != second["workspace"]["id"]
    assert first["workspace"]["name"] == second["workspace"]["name"]


def test_public_workspace_creation_seeds_bundled_skill_catalog() -> None:
    class SeedTrackingStore(AuditFallbackStore):
        def __init__(self):
            super().__init__()
            self.seed_calls = 0

        def seed_skill_catalog(self) -> int:
            self.seed_calls += 1
            return 0

    store = SeedTrackingStore()

    store.create_public_workspace("Public Demo Co")

    assert store.seed_calls == 1


def test_product_store_uses_audit_fallback_for_workspace_and_dashboard() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )

    connection = store.upsert_connection(request, token_payload())
    dashboard = store.dashboard(token_payload())

    assert connection["workspace"]["name"] == "Demo Co"
    assert connection["workspace"]["metadata"]["storage"] == "audit_fallback"
    assert dashboard["status"] == "ok"
    assert dashboard["storage"] == "audit_fallback"
    assert dashboard["workspace"]["name"] == "Demo Co"
    assert len(dashboard["skills"]) >= 5
    assert dashboard["events"][0]["event_type"] == "connect.token_issued"


def test_product_store_audit_fallback_persists_connector_and_skill_state() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    profile = store.set_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Co Books",
    )
    skill = store.set_skill_enabled(
        token_payload=token_payload(),
        skill_id="vat-summary-th",
        enabled=True,
    )
    dashboard = store.dashboard(token_payload())

    assert profile["status"] == "needs_validation"
    assert profile["connection_mode"] == "api_driver"
    assert skill["enabled"] is True
    assert dashboard["connector_profiles"][0]["connector_id"] == "flowaccount"
    vat_skill = next(item for item in dashboard["skills"] if item["skill_id"] == "vat-summary-th")
    assert vat_skill["enabled"] is True
    assert {event["event_type"] for event in dashboard["events"]} >= {
        "connector.profile_configured",
        "skill.enabled",
    }


def test_fallback_profiles_are_mode_distinct_and_evidence_aware() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    api_profile = store.set_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
        capability_states={"company.info.read": "observed"},
        evidence_source="api_driver_safe_probe",
        validated_at="2026-07-19T12:00:00+00:00",
    )
    native_profile = store.set_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="native_mcp",
        environment="production",
        capability_states={"documents.invoice.list": "observed"},
        evidence_source="native_mcp_safe_read",
        validated_at="2026-07-19T12:00:00+00:00",
    )
    dashboard = store.dashboard(token_payload())

    assert api_profile["status"] == "ready_read_only"
    assert native_profile["status"] == "ready_read_only"
    assert {profile["connection_mode"] for profile in dashboard["connector_profiles"]} == {
        "api_driver",
        "native_mcp",
    }


def test_product_validation_requires_an_existing_exact_linked_profile() -> None:
    store = AuditFallbackStore()

    with pytest.raises(ValueError, match="linked connector profile"):
        store.validate_connector_profile(
            token_payload=token_payload(),
            connector_id="flowaccount",
            connection_mode="api_driver",
            environment="production",
            capability_states={"company.info.read": "observed"},
            evidence_source="api_driver_safe_probe",
            evidence_ref="evidence_missing_link_1234",
            validated_at="2026-07-19T12:00:00+00:00",
        )

    store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )

    with pytest.raises(ValueError, match="linked connector profile"):
        store.validate_connector_profile(
            token_payload=token_payload(),
            connector_id="flowaccount",
            connection_mode="api_driver",
            environment="sandbox",
            capability_states={"company.info.read": "observed"},
            evidence_source="api_driver_safe_probe",
            evidence_ref="evidence_wrong_environment_1234",
            validated_at="2026-07-19T12:00:00+00:00",
        )


def test_fallback_relink_after_unlink_does_not_resurrect_validation_evidence() -> None:
    store = AuditFallbackStore()
    linked = store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )
    assert linked["status"] == "needs_validation"

    validated = store.validate_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
        capability_states={"company.info.read": "observed"},
        evidence_source="api_driver_safe_probe",
        evidence_ref="evidence_relink_history_1234",
        validated_at="2026-07-19T12:00:00+00:00",
    )
    assert validated["status"] == "ready_read_only"

    store.unlink_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )
    relinked = store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )

    assert relinked["status"] == "needs_validation"
    assert relinked["capability_states"] == {}
    assert relinked["evidence_source"] is None
    assert relinked["validated_at"] is None


def test_fallback_state_reconstruction_paginates_past_late_unlink_tombstone() -> None:
    store = PaginatedAuditFallbackStore()
    workspace_key_value = store._fallback_workspace_for_token(token_payload())["workspace"][
        "workspace_key"
    ]
    profile = {
        "id": "profile-old",
        "workspace_id": "workspace-old",
        "connector_id": "flowaccount",
        "connection_mode": "api_driver",
        "environment": "production",
        "status": "ready_read_only",
        "capability_states": {"company.info.read": "observed"},
        "evidence_source": "api_driver_safe_probe",
        "validated_at": "2026-07-19T12:00:00+00:00",
        "metadata": {"evidence_ref": "evidence_old_profile_1234"},
    }
    configured_event = {
        "id": "event-000000",
        "created_at": "000000",
        "tool_name": "mercury_product_state",
        "output_summary": {
            "event_type": "connector.profile_configured",
            "profile": profile,
        },
        "status": "ok",
        "metadata": {"workspace_key": workspace_key_value},
    }
    filler_events = [
        {
            "id": f"event-{index:06d}",
            "created_at": f"{index:06d}",
            "tool_name": "mercury_product_state",
            "output_summary": {"event_type": "flow.run_completed"},
            "status": "ok",
            "metadata": {"workspace_key": workspace_key_value},
        }
        for index in range(1, PRODUCT_FALLBACK_LIMIT + 1)
    ]
    unlink_event = {
        "id": f"event-{PRODUCT_FALLBACK_LIMIT + 1:06d}",
        "created_at": f"{PRODUCT_FALLBACK_LIMIT + 1:06d}",
        "tool_name": "mercury_product_state",
        "output_summary": {
            "event_type": "connector.profile_unlinked",
            "event_summary": {
                "connector_id": "flowaccount",
                "connection_mode": "api_driver",
                "environment": "production",
            },
        },
        "status": "ok",
        "metadata": {"workspace_key": workspace_key_value},
    }
    store.events = [configured_event, *filler_events, unlink_event]

    profiles = store._fallback_current_connector_profiles(workspace_key_value)

    assert profiles == {}
    assert [params["offset"] for params in store.state_get_params] == ["0", "500"]
    assert all(
        params["metadata->>workspace_key"] == f"eq.{workspace_key_value}"
        and params["order"] == "created_at.asc,id.asc"
        and params["limit"] == str(PRODUCT_FALLBACK_LIMIT)
        for params in store.state_get_params
    )


def test_fallback_exact_relink_clears_existing_validation_evidence() -> None:
    store = AuditFallbackStore()
    store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )
    validated = store.validate_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
        capability_states={"company.info.read": "observed"},
        evidence_source="api_driver_safe_probe",
        evidence_ref="evidence_direct_relink_1234",
        validated_at="2026-07-19T12:00:00+00:00",
    )
    assert validated["status"] == "ready_read_only"

    relinked = store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )

    assert relinked["status"] == "needs_validation"
    assert relinked["capability_states"] == {}
    assert relinked["evidence_source"] is None
    assert relinked["validated_at"] is None
    assert "evidence_ref" not in relinked["metadata"]


def test_product_table_exact_relink_clears_existing_validation_evidence() -> None:
    store = ProductTableStore()
    store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )
    validated = store.validate_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
        capability_states={"company.info.read": "observed"},
        evidence_source="api_driver_safe_probe",
        evidence_ref="evidence_product_relink_1234",
        validated_at="2026-07-19T12:00:00+00:00",
    )
    assert validated["status"] == "ready_read_only"

    relinked = store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        connection_mode="api_driver",
        environment="production",
    )

    assert relinked["status"] == "needs_validation"
    assert relinked["capability_states"] == {}
    assert relinked["evidence_source"] is None
    assert relinked["validated_at"] is None
    assert "evidence_ref" not in relinked["metadata"]


def test_fallback_generic_mcp_user_supplied_profile_accepts_discovered_read_evidence() -> None:
    store = AuditFallbackStore()
    linked = store.link_connector_profile(
        token_payload=token_payload(),
        connector_id="generic_mcp",
        connection_mode="native_mcp",
        environment="user_supplied",
        external_server_name="customer-ledger-mcp",
    )

    validated = store.validate_connector_profile(
        token_payload=token_payload(),
        connector_id="generic_mcp",
        connection_mode="native_mcp",
        environment="user_supplied",
        capability_states={"ledger.entries.list": "observed"},
        evidence_source="native_mcp_safe_read",
        evidence_ref="evidence_generic_mcp_1234",
        validated_at="2026-07-19T12:00:00+00:00",
    )

    assert linked["status"] == "needs_validation"
    assert validated["status"] == "ready_read_only"
    assert validated["capability_states"] == {"ledger.entries.list": "observed"}


def test_profile_status_requires_evidence_and_only_observed_mutations_are_write_ready() -> None:
    assert (
        connector_profile_status(
            "express",
            "local_bridge",
            {},
            evidence_source=None,
            validated_at=None,
        )
        == "requires_local_setup"
    )
    assert (
        connector_profile_status(
            "flowaccount",
            "api_driver",
            {"documents.invoice.create": "validation_failed"},
            evidence_source="api_driver_safe_probe",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "needs_validation"
    )
    assert (
        connector_profile_status(
            "flowaccount",
            "api_driver",
            {"documents.invoice.create": "observed"},
            evidence_source="api_driver_safe_probe",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "ready_read_write"
    )


def test_profile_status_binds_readiness_to_matching_evidence_and_reviewed_mode() -> None:
    observed_write = {"documents.invoice.create": "observed"}
    timestamp = "2026-07-19T12:00:00+00:00"

    assert (
        connector_profile_status(
            "flowaccount",
            "api_driver",
            observed_write,
            evidence_source="native_mcp_safe_read",
            validated_at=timestamp,
        )
        == "needs_validation"
    )
    assert (
        connector_profile_status(
            "custom",
            "api_driver",
            observed_write,
            evidence_source="api_driver_safe_probe",
            validated_at=timestamp,
        )
        == "needs_validation"
    )
    assert (
        connector_profile_status(
            "flowaccount",
            "native_mcp",
            observed_write,
            evidence_source="native_mcp_safe_read",
            validated_at=timestamp,
        )
        == "needs_validation"
    )
    assert (
        connector_profile_status(
            "flowaccount",
            "native_mcp",
            {"documents.invoice.list": "observed"},
            evidence_source="native_mcp_safe_read",
            validated_at=timestamp,
        )
        == "ready_read_only"
    )


def test_unknown_flowaccount_native_mcp_read_capability_needs_validation() -> None:
    assert (
        connector_profile_status(
            "flowaccount",
            "native_mcp",
            {"documents.invoice.unknown": "observed"},
            evidence_source="native_mcp_safe_read",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "needs_validation"
    )


def test_unknown_flowaccount_api_driver_read_capability_needs_validation() -> None:
    assert (
        connector_profile_status(
            "flowaccount",
            "api_driver",
            {"documents.invoice.unknown": "observed"},
            evidence_source="api_driver_safe_probe",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "needs_validation"
    )


def test_custom_draft_observed_capability_without_catalog_action_needs_validation() -> None:
    assert (
        connector_profile_status(
            "custom",
            "api_driver",
            {"company.info.read": "observed"},
            evidence_source="api_driver_safe_probe",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "needs_validation"
    )


def test_known_reviewed_read_evidence_remains_ready_read_only() -> None:
    assert (
        connector_profile_status(
            "flowaccount",
            "api_driver",
            {"company.info.read": "observed"},
            evidence_source="api_driver_safe_probe",
            validated_at="2026-07-19T12:00:00+00:00",
        )
        == "ready_read_only"
    )


def test_profile_serialization_drops_unrecognized_and_sensitive_metadata() -> None:
    public = public_connector_profile(
        {
            "id": "profile-1",
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
            "company_ref": "company-123",
            "external_server_name": "connector-host",
            "capability_states": {
                "company.info.read": "observed",
                "api_key": "observed",
                "provider_access_token": "observed",
                "documents.response_body": "observed",
                "vendor.tax_id": "observed",
            },
            "evidence_source": "api_driver_safe_probe",
            "validated_at": "2026-07-19T12:00:00Z",
            "metadata": {
                "setup_state": "awaiting_credentials",
                "validation": {"response": "provider payload"},
                "server_vault": {"ciphertext": "secret"},
                "preset": {"api_base_url": "https://example.test", "api_key": "secret"},
            },
            "provider_payload": {"email": "owner@example.com"},
        }
    )

    assert public["capability_states"] == {"company.info.read": "observed"}
    assert public["metadata"] == {
        "setup_state": "awaiting_credentials",
        "preset": {"api_base_url": "https://example.test"},
    }
    assert "provider_payload" not in public
    assert "server_vault" not in str(public)
    assert "secret" not in str(public)


def test_skill_seed_uses_portable_capability_requirements() -> None:
    health_check = next(
        item for item in SKILL_CATALOG_SEED if item["skill_id"] == "company-health-check-th"
    )
    flow_setup = next(
        item
        for item in SKILL_CATALOG_SEED
        if item["skill_id"] == "flowaccount-connector-setup-th"
    )

    assert health_check["required_connectors"] == []
    assert health_check["required_capabilities"] == ["company.read"]
    assert flow_setup["required_connectors"] == ["flowaccount"]
    assert flow_setup["required_capabilities"] == []


def test_product_store_audit_fallback_records_team_invite() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    member = store.invite_member(
        token_payload=token_payload(),
        email="teammate@example.com",
        role="viewer",
    )
    dashboard = store.dashboard(token_payload())

    assert member["status"] == "invited"
    assert member["email_hash"]
    invited = next(item for item in dashboard["members"] if item.get("status") == "invited")
    assert invited["role"] == "viewer"
    assert dashboard["events"][0]["event_type"] == "team.member_invited"


def test_product_store_audit_fallback_records_uploaded_skill() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    upload = store.record_uploaded_skill(
        token_payload=token_payload(),
        skill_id="workspace-monthly-report-12345678",
        title="Monthly Report",
        markdown="# Monthly Report\n\nDraft report workflow.",
        metadata={"category": "reporting", "document_uri": "mercury://workspace/demo/skill"},
    )
    dashboard = store.dashboard(token_payload())

    assert upload["catalog"]["status"] == "uploaded"
    uploaded_skill = next(
        item
        for item in dashboard["skills"]
        if item["skill_id"] == "workspace-monthly-report-12345678"
    )
    assert uploaded_skill["enabled"] is True


def test_product_store_audit_fallback_records_workspace_flow() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    flow = store.save_flow(
        token_payload=token_payload(),
        title="Company Health Check",
        flow_yaml=COMPANY_HEALTH_TEMPLATE,
        metadata={"source": "test"},
    )
    dashboard = store.dashboard(token_payload())
    fetched = store.get_flow(token_payload=token_payload(), flow_id=flow["flow_id"])

    assert flow["status"] == "draft"
    assert flow["command_count"] == 3
    assert dashboard["flows"][0]["flow_id"] == flow["flow_id"]
    assert fetched is not None
    assert fetched["yaml"] == COMPANY_HEALTH_TEMPLATE


def test_product_store_audit_fallback_records_flow_runs() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())
    flow = store.save_flow(
        token_payload=token_payload(),
        title="Company Health Check",
        flow_yaml=COMPANY_HEALTH_TEMPLATE,
        metadata={"source": "test"},
    )
    result = MercuryFlowRunner(dry_run=True).run_text(COMPANY_HEALTH_TEMPLATE).as_dict()

    run = store.record_flow_run(
        token_payload=token_payload(),
        flow_id=flow["flow_id"],
        title=flow["title"],
        result_payload=result,
        dry_run=True,
        env_keys=["month"],
    )
    dashboard = store.dashboard(token_payload())

    assert run["flow_id"] == flow["flow_id"]
    assert run["status"] == "planned"
    assert run["step_count"] == 4
    assert run["env_keys"] == ["month"]
    assert dashboard["flow_runs"][0]["run_id"] == run["run_id"]
    assert dashboard["flow_runs"][0]["env_keys"] == ["month"]
    assert dashboard["events"][0]["event_type"] == "flow.run_completed"
