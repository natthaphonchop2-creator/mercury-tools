from mercury_tools.config import Settings
from mercury_tools.db.product import SupabaseProductStore
from mercury_tools.flows.runner import MercuryFlowRunner
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.product import ConnectRequest
from mercury_tools.workspaces.public import public_workspace_token_payload


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


def test_product_store_public_workspace_connector_vault_round_trip() -> None:
    store = AuditFallbackStore()
    created = store.create_public_workspace("Public Demo Co")
    workspace_id = created["workspace_id"]
    payload = public_workspace_token_payload(workspace_id)

    store.set_connector_credentials(
        token_payload=payload,
        connector_id="flowaccount",
        environment="sandbox",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    credentials = store.get_connector_credentials(
        workspace_id=workspace_id,
        connector_id="flowaccount",
        environment="sandbox",
    )
    dashboard = store.public_dashboard(workspace_id)

    assert credentials == {
        "client_id": "demo-client-id",
        "client_secret": "super-secret-value",
    }
    assert "super-secret-value" not in str(dashboard)
    assert "demo-client-id" not in str(dashboard)
    assert "ciphertext" not in str(dashboard)


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

    assert profile["status"] == "requires_credentials"
    assert skill["enabled"] is True
    assert dashboard["connector_profiles"][0]["connector_id"] == "flowaccount"
    vat_skill = next(item for item in dashboard["skills"] if item["skill_id"] == "vat-summary-th")
    assert vat_skill["enabled"] is True
    assert {event["event_type"] for event in dashboard["events"]} >= {
        "connector.profile_configured",
        "skill.enabled",
    }


def test_product_store_audit_fallback_normalizes_connector_ids_for_profile_keys() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    store.set_connector_profile(
        token_payload=token_payload(),
        connector_id=" FlowAccount ",
        environment="production",
        company_name="Demo Co Books",
    )
    result = store.set_connector_credentials(
        token_payload=token_payload(),
        connector_id="FLOWACCOUNT",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    dashboard = store.dashboard(token_payload())

    assert result["connector_id"] == "flowaccount"
    assert {profile["connector_id"] for profile in dashboard["connector_profiles"]} == {
        "flowaccount"
    }


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


def test_product_store_audit_fallback_encrypts_connector_credentials() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())

    result = store.set_connector_credentials(
        token_payload=token_payload(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    dashboard = store.dashboard(token_payload())
    serialized_events = str(store.events)
    profile = dashboard["connector_profiles"][0]
    private_profile = next(iter(store._fallback_private_connector_profiles.values()))
    server_vault = private_profile["metadata"]["server_vault"]

    assert result["status"] == "credentials_configured"
    assert profile["status"] == "credentials_configured"
    assert profile["metadata"]["credentials_configured"] is True
    assert "client_id" in result["credential_fields"]
    assert "ciphertext" in server_vault
    assert "'server_vault':" not in str(profile)
    assert server_vault["ciphertext"] not in str(profile)
    assert "super-secret-value" not in serialized_events
    assert "demo-client-id" not in serialized_events
    assert "'server_vault':" not in serialized_events
    assert "'vault_record':" not in serialized_events
    assert "ciphertext" not in serialized_events


def test_product_store_audit_fallback_validation_preserves_private_vault_metadata() -> None:
    store = AuditFallbackStore()
    request = ConnectRequest(
        email="owner@example.com",
        company="Demo Co",
        host_app="codex",
        invite_code="invite",
    )
    store.upsert_connection(request, token_payload())
    store.set_connector_credentials(
        token_payload=token_payload(),
        connector_id="flowaccount",
        environment="production",
        credentials={
            "client_id": "demo-client-id",
            "client_secret": "super-secret-value",
        },
    )
    initial_private_profile = next(iter(store._fallback_private_connector_profiles.values()))
    credential_metadata = initial_private_profile["metadata"]
    server_vault = credential_metadata["server_vault"]

    ready_profile = store.set_connector_profile(
        token_payload=token_payload(),
        connector_id="flowaccount",
        environment="production",
        company_name="Demo Books",
        metadata={
            "setup_state": "ready",
            "enabled_capabilities": ["company.info.read"],
            "validation": {"token_status": 200, "company_info_status": 200},
        },
    )
    private_profile = store._fallback_private_connector_profiles[initial_private_profile["id"]]
    private_metadata = private_profile["metadata"]
    dashboard_profile = store.dashboard(token_payload())["connector_profiles"][0]
    serialized_events = str(store.events)

    assert ready_profile["status"] == "connected"
    assert private_profile["status"] == "connected"
    assert private_metadata["server_vault"]["ciphertext"] == server_vault["ciphertext"]
    assert private_metadata["credential_storage"] == "encrypted_server_vault"
    assert private_metadata["credential_fields"] == ["client_id", "client_secret"]
    assert private_metadata["credential_fingerprints"] == credential_metadata[
        "credential_fingerprints"
    ]
    assert private_metadata["credentials_configured"] is True
    assert (
        private_metadata["credentials_configured_at"]
        == credential_metadata["credentials_configured_at"]
    )
    assert private_metadata["setup_state"] == "ready"
    assert dashboard_profile["status"] == "connected"
    assert "'server_vault':" not in str(ready_profile)
    assert "'server_vault':" not in str(dashboard_profile)
    assert server_vault["ciphertext"] not in str(ready_profile)
    assert server_vault["ciphertext"] not in str(dashboard_profile)
    assert "super-secret-value" not in serialized_events
    assert "demo-client-id" not in serialized_events
    assert "'server_vault':" not in serialized_events
    assert "ciphertext" not in serialized_events


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
