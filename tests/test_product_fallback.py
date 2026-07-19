from mercury_tools.config import Settings
from mercury_tools.db.product import (
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
