import pytest
from starlette.testclient import TestClient

from mercury_tools import __version__
from mercury_tools.config import Settings
from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.mcp.server import create_http_app
from mercury_tools.product import ConnectRequest, create_client_token


@pytest.fixture(autouse=True)
def _clear_live_env(monkeypatch) -> None:
    for name in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "OPENAI_APPS_CHALLENGE_TOKEN",
        "MERCURY_TOOLS_PUBLIC_BASE_URL",
        "MERCURY_TOOLS_HTTP_BEARER_TOKEN",
        "MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API",
        "MERCURY_CONNECT_INVITE_CODE",
        "MERCURY_CONNECT_SIGNING_SECRET",
        "MERCURY_CLOUD_BASE_URL",
        "MERCURY_DEPLOYMENT_COMMIT",
        "RENDER_GIT_COMMIT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API", "true")


def ready_connector_profile(connector_id: str = "flowaccount") -> dict:
    return {
        "connector_id": connector_id,
        "connection_mode": "api_driver",
        "environment": "production",
        "status": "ready_read_only",
        "capability_states": {"company.info.read": "observed"},
        "evidence_source": "api_driver_safe_probe",
        "validated_at": "2026-07-19T12:00:00+00:00",
    }


def make_client_token() -> str:
    return create_client_token(
        Settings(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service-role",
            openai_api_key="",
            connect_signing_secret="signing-secret",
        ),
        ConnectRequest(
            email="owner@example.com",
            company="Demo Co",
            host_app="codex",
            invite_code="invite",
        ),
    )


def test_remote_http_app_exposes_healthz(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "false")

    client = TestClient(create_http_app(require_auth=False))
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mcp_path"] == "/mcp"
    assert "private_mcp" not in response.json()
    assert client.post("/private-mcp").status_code == 404


def test_status_exposes_exact_package_version_and_deployment_commit(monkeypatch) -> None:
    commit = "a" * 40
    monkeypatch.setenv("MERCURY_DEPLOYMENT_COMMIT", commit)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["version"] == __version__ == "0.3.0"
    assert payload["deployment_commit"] == commit


def test_status_uses_render_git_commit_when_explicit_commit_is_unset(monkeypatch) -> None:
    commit = "b" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", commit)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["deployment_commit"] == commit


def test_status_prefers_explicit_commit_over_render_fallback(monkeypatch) -> None:
    explicit = "c" * 40
    fallback = "d" * 40
    monkeypatch.setenv("MERCURY_DEPLOYMENT_COMMIT", explicit)
    monkeypatch.setenv("RENDER_GIT_COMMIT", fallback)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["deployment_commit"] == explicit


@pytest.mark.parametrize("value", ("main", "E" * 40, "e" * 39, "e" * 41))
def test_status_rejects_invalid_render_git_commit(monkeypatch, value: str) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", value)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["deployment_commit"] is None
    assert value not in str(payload)


def test_status_invalid_explicit_commit_does_not_fall_through(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_DEPLOYMENT_COMMIT", "main")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "f" * 40)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["deployment_commit"] is None


@pytest.mark.parametrize("value", ("main", "A" * 40, "a" * 39, "a" * 41))
def test_status_rejects_unbounded_deployment_commit_values(monkeypatch, value: str) -> None:
    monkeypatch.setenv("MERCURY_DEPLOYMENT_COMMIT", value)

    payload = TestClient(create_http_app(require_auth=False)).get("/api/status").json()

    assert payload["deployment_commit"] is None
    assert value not in str(payload)


def test_public_contest_app_does_not_mount_legacy_http_api(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.delenv("MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API", raising=False)

    client = TestClient(create_http_app(require_auth=False), raise_server_exceptions=False)

    assert client.get("/start").status_code == 404
    assert client.post("/api/connect", json={}).status_code == 404
    assert client.get("/api/dashboard").status_code == 404
    assert client.post("/api/team/invite", json={}).status_code == 404
    assert client.post("/api/skills/upload", json={}).status_code == 404
    assert client.post("/api/flows/run", json={}).status_code == 404
    assert client.post("/private-mcp").status_code == 404
    status = client.get("/api/status").json()
    assert status["legacy_http_api"] == "disabled"
    assert "dashboard" not in status
    assert "skill_upload" not in status


def test_public_app_exposes_required_plugin_legal_endpoints(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API", raising=False)

    client = TestClient(create_http_app(require_auth=False))

    privacy = client.get("/privacy")
    terms = client.get("/terms")
    support = client.get("/support")
    assert privacy.status_code == terms.status_code == support.status_code == 200
    assert privacy.headers["content-type"].startswith("text/plain")
    assert "Raw ERP API keys" in privacy.text
    assert "does not directly post production ERP transactions" in terms.text
    assert "github.com/natthaphonchop2-creator/mercury-tools/issues" in support.text

    status = client.get("/api/status").json()
    assert status["privacy"] == "/privacy"
    assert status["terms"] == "/terms"
    assert status["support"] == "/support"


def test_openai_apps_challenge_is_exact_and_opt_in(monkeypatch) -> None:
    client = TestClient(create_http_app(require_auth=False))
    missing = client.get("/.well-known/openai-apps-challenge")
    assert missing.status_code == 404

    monkeypatch.setenv("OPENAI_APPS_CHALLENGE_TOKEN", "x")
    configured = TestClient(create_http_app(require_auth=False)).get(
        "/.well-known/openai-apps-challenge"
    )
    assert configured.status_code == 200
    assert configured.text == "x"
    assert configured.headers["content-type"].startswith("text/plain")


def test_public_app_mounts_cloud_reads_without_cloud_write_or_legacy_routes(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.delenv("MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API", raising=False)

    client = TestClient(create_http_app(require_auth=False), raise_server_exceptions=False)

    assert client.get("/api/cloud/v1/catalog/actions").status_code == 503
    assert client.post("/api/cloud/v1/catalog/actions", json={}).status_code == 405
    assert client.post("/api/cloud/v1/connectors", json={}).status_code == 405
    assert client.post("/api/cloud/v1/skills", json={}).status_code == 405
    assert client.post("/api/cloud/v1/documents/document-1", json={}).status_code == 405
    assert client.post("/api/connect", json={}).status_code == 404
    assert client.post("/api/flows/run", json={}).status_code == 404
    assert client.get("/mcp").status_code != 404


def test_remote_http_app_allows_public_base_url_host(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")

    from mercury_tools.mcp.server import mcp

    create_http_app(require_auth=False)

    assert "mercury.example.com" in mcp.settings.transport_security.allowed_hosts
    assert "https://mercury.example.com" in mcp.settings.transport_security.allowed_origins


def test_remote_http_app_requires_bearer_token_for_mcp_path(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_BEARER_TOKEN", "demo-token")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    assert client.get("/healthz").status_code == 200
    assert client.get("/mcp").status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/mcp", headers={"Authorization": "Bearer demo-token"}).status_code != 401


def test_connect_page_and_status(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    page = client.get("/")
    status = client.get("/api/status")

    assert page.status_code == 200
    assert "Mercury Tools MCP Server" in page.text
    assert "not a web app" in page.text
    assert "browser setup workflow" in page.text
    assert "https://mercury.example.com/mcp" in page.text
    assert 'id="connect-form"' not in page.text
    assert status.status_code == 200
    assert status.json()["mcp_endpoint"] == "https://mercury.example.com/mcp"
    assert status.json()["invite_required"] is True
    assert status.json()["surface"] == "mcp-plugin-first"
    assert status.json()["browser_ui"] == "disabled"
    assert "pages" not in status.json()
    assert "console" not in status.json()
    flow_tools = status.json()["flow_tools"]
    assert flow_tools == [
        "flow_cheat_sheet",
        "check_flow_syntax",
        "inspect_flow_files",
        "run_inline_flow",
        "run_flow_files",
        "save_workspace_flow",
        "list_workspace_flows",
        "run_workspace_flow",
    ]
    assert "run_flow" not in flow_tools
    assert "run_mercury_flow" not in flow_tools
    assert status.json()["dashboard"] == "/api/dashboard"
    assert "connector_credentials" not in status.json()
    assert client.post("/api/connectors/credentials", json={}).status_code == 404
    assert status.json()["flow_validate"] == "/api/flows/validate"
    assert status.json()["flow_import"] == "/api/flows/import"


def test_legacy_browser_paths_do_not_expose_setup_console(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    for path, page_name in (
        ("/", "start"),
        ("/start", "start"),
        ("/connect", "connect"),
        ("/workspace", "workspace"),
        ("/connectors", "connectors"),
        ("/knowledge", "knowledge"),
        ("/skills", "skills"),
        ("/flows", "flows"),
        ("/mcp-api", "mcp_api"),
        ("/audit", "audit"),
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert "Mercury Tools MCP Server" in response.text
        assert "not a web app" in response.text
        assert f'data-page="{page_name}"' not in response.text
        assert '<section class="page" data-page=' not in response.text
        assert "history.pushState" not in response.text
        assert 'id="connect-form"' not in response.text


def test_flows_page_is_endpoint_capable_control_plane(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    response = client.get("/flows")

    assert response.status_code == 200
    assert "Mercury Tools MCP Server" in response.text
    assert "not a web app" in response.text
    assert 'id="flow-form"' not in response.text
    assert 'id="flow_yaml"' not in response.text
    assert 'id="flow-run"' not in response.text
    assert 'id="flow-validate"' not in response.text
    assert 'data-flow-load="' not in response.text
    assert "/api/flows/run" not in response.text
    assert "/api/flows/save" not in response.text
    assert "/api/flows/validate" not in response.text


def test_connect_api_rejects_bad_invite(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    response = client.post(
        "/api/connect",
        json={
            "invite_code": "wrong",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    )

    assert response.status_code == 403


def test_connect_api_issues_client_token_for_mcp(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    response = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["token"].startswith("mc_")
    assert "codex mcp add mercury-tools" in payload["codex"]["command"]
    assert "codex plugin marketplace add natthaphonchop2-creator/mercury-tools" in (
        payload["codex"]["setup_command"]
    )
    assert "codex plugin add mercury-finance" in payload["codex"]["setup_command"]
    assert "codex mcp add mercury-tools" in payload["codex"]["setup_command"]
    assert payload["codex"]["plugin_id"] == "mercury-finance"
    assert payload["codex"]["env_var"] == "MERCURY_TOOLS_MCP_TOKEN"
    assert "--bearer-token-env-var MERCURY_TOOLS_MCP_TOKEN" in payload["codex"]["command"]
    assert "MERCURY_TOOLS_MCP_TOKEN" in payload["cursor"]["note"]
    assert payload["endpoint"] == "https://mercury.example.com/mcp"
    assert payload["persistence"]["status"] == "degraded"

    authorized = client.get("/mcp", headers={"Authorization": f"Bearer {payload['token']}"})
    assert authorized.status_code != 401


def test_connect_page_does_not_present_browser_setup_ux(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    response = client.get("/connect")

    assert response.status_code == 200
    assert "Mercury Tools MCP Server" in response.text
    assert "Codex one-command setup" not in response.text
    assert "Advanced MCP client config" not in response.text
    assert "data-copy=\"codex-command\"" not in response.text


def test_product_dashboard_requires_mercury_client_token(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    assert client.get("/api/dashboard").status_code == 401
    admin_response = client.get(
        "/api/dashboard",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert admin_response.status_code == 401


def test_product_dashboard_uses_token_when_supabase_missing(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    response = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    )
    token = response.json()["token"]

    dashboard = client.get("/api/dashboard", headers={"Authorization": f"Bearer {token}"})

    assert dashboard.status_code == 200
    payload = dashboard.json()
    assert payload["status"] == "degraded"
    assert payload["workspace"]["name"] == "Demo Co"
    assert payload["member"]["email"] == "user@example.com"
    assert payload["flows"] == []


def test_product_mutation_requires_supabase(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/connectors/setup",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "connector_id": "flowaccount",
            "environment": "production",
            "company_name": "Demo Co",
        },
    )

    assert response.status_code == 503
    assert response.json()["deprecated_tool"] == "start_connector_setup"
    assert response.json()["replacement_tool"] == "link_connector_profile"


def test_legacy_connector_setup_rejects_missing_mode_and_unsafe_fields(monkeypatch) -> None:
    from mercury_tools.mcp import server

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")
    calls: list[dict] = []

    class FakeStore:
        def set_connector_profile(self, **kwargs):
            calls.append({"method": "set", **kwargs})
            return ready_connector_profile()

        def link_connector_profile(self, **kwargs):
            calls.append({"method": "link", **kwargs})
            return ready_connector_profile()

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())
    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {make_client_token()}"}
    rejected_secret = "arbitrary-extra-field-secret-7f89c2"
    unsafe_bodies = [
        {"connector_id": "flowaccount", "environment": "production"},
        {
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
            "arbitrary_unknown_field": rejected_secret,
        },
        {
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
            "provider_body": {"result": "provider response"},
        },
        {
            "connector_id": "flowaccount",
            "connection_mode": "native_mcp",
            "environment": "production",
            "external_server_name": "192.168.1.10",
        },
    ]

    responses = [
        client.post("/api/connectors/setup", headers=headers, json=body)
        for body in unsafe_bodies
    ]

    assert all(400 <= response.status_code < 500 for response in responses)
    assert all(
        response.json()["deprecated_tool"] == "start_connector_setup"
        and response.json()["replacement_tool"] == "link_connector_profile"
        for response in responses
    )
    assert responses[1].json()["message"] == "Connector setup request validation failed."
    assert rejected_secret not in responses[1].text
    assert calls == []

    safe_response = client.post(
        "/api/connectors/setup",
        headers=headers,
        json={
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
            "company_name": "Demo Co",
        },
    )

    assert safe_response.status_code == 200
    assert safe_response.json()["deprecated_tool"] == "start_connector_setup"
    assert safe_response.json()["replacement_tool"] == "link_connector_profile"
    assert len(calls) == 1
    assert calls[0]["method"] == "link"
    assert {key: calls[0][key] for key in calls[0] if key != "token_payload"} == {
        "method": "link",
        "connector_id": "flowaccount",
        "connection_mode": "api_driver",
        "environment": "production",
        "company_ref": None,
        "company_name": "Demo Co",
        "external_server_name": None,
    }


@pytest.mark.parametrize(
    ("store_error", "expected_status"),
    [
        (ValueError("safe profile validation failed"), 400),
        (RuntimeError("profile storage unavailable"), 503),
    ],
)
def test_legacy_connector_setup_runtime_errors_include_migration_fields(
    monkeypatch,
    store_error: Exception,
    expected_status: int,
) -> None:
    from mercury_tools.mcp import server

    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    class FakeStore:
        def link_connector_profile(self, **kwargs):
            raise store_error

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())
    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    response = client.post(
        "/api/connectors/setup",
        headers={"Authorization": f"Bearer {make_client_token()}"},
        json={
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
        },
    )

    assert response.status_code == expected_status
    assert response.json()["deprecated_tool"] == "start_connector_setup"
    assert response.json()["replacement_tool"] == "link_connector_profile"


def test_legacy_connector_setup_unauthorized_error_includes_migration_fields(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    response = TestClient(
        create_http_app(require_auth=True),
        raise_server_exceptions=False,
    ).post(
        "/api/connectors/setup",
        json={
            "connector_id": "flowaccount",
            "connection_mode": "api_driver",
            "environment": "production",
        },
    )

    assert response.status_code == 401
    assert response.json()["deprecated_tool"] == "start_connector_setup"
    assert response.json()["replacement_tool"] == "link_connector_profile"


def test_workspace_flow_validate_and_dry_run_use_client_token(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")
    local_report_flow = (
        "name: Local Report\n"
        "---\n"
        "- emitReport:\n"
        "    title: Local Report\n"
        "    sections:\n"
        "      - Ready\n"
    )

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    validate = client.post(
        "/api/flows/validate",
        headers={"Authorization": f"Bearer {token}"},
        json={"flow_yaml": COMPANY_HEALTH_TEMPLATE},
    )
    dry_run = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {token}"},
        json={"flow_yaml": local_report_flow, "dry_run": True},
    )
    save = client.post(
        "/api/flows/save",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Company Health Check", "flow_yaml": COMPANY_HEALTH_TEMPLATE},
    )

    assert validate.status_code == 200
    assert validate.json()["flow"]["name"] == "Company Health Check"
    assert dry_run.status_code == 200
    assert dry_run.json()["status"] == "planned"
    assert save.status_code == 503


def test_workspace_flow_run_blocks_connector_backed_raw_yaml_when_unready(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    class FakeStore:
        def upsert_connection(self, connect_request, token_payload):
            return {"workspace": {"id": "workspace-1"}, "member": {"id": "member-1"}}

        def dashboard(self, token_payload):
            return {
                "workspace": {"name": "Demo Co"},
                "connector_profiles": [
                    {
                        "connector_id": "flowaccount",
                        "environment": "production",
                        "status": "credentials_configured",
                        "metadata": {"setup_state": "credentials_received"},
                    }
                ],
            }

        def record_flow_run(self, **kwargs):
            raise AssertionError("blocked connector setup should not record a run")

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "flow_id": "raw-company-health",
            "title": "Company Health Check",
            "flow_yaml": COMPANY_HEALTH_TEMPLATE,
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["reason"] == "not_validated"


def test_workspace_flow_run_blocks_raw_yaml_with_connector_missing_environment(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    connector_flow_missing_environment = (
        "name: FlowAccount Missing Environment\n"
        "tags: [accounting, endpoint-capable, flowaccount]\n"
        "env:\n"
        "  connector: flowaccount\n"
        "---\n"
        "- connectorStatus:\n"
        "    saveAs: connectorState\n"
        "- emitReport:\n"
        "    title: Connector handoff\n"
        "    sections:\n"
        "      - Ready\n"
    )

    class FakeStore:
        def upsert_connection(self, connect_request, token_payload):
            return {"workspace": {"id": "workspace-1"}, "member": {"id": "member-1"}}

        def dashboard(self, token_payload):
            return {
                "workspace": {"name": "Demo Co"},
                "connector_profiles": [ready_connector_profile()],
            }

        def record_flow_run(self, **kwargs):
            raise AssertionError("missing environment should block before recording a run")

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Missing Environment",
            "flow_yaml": connector_flow_missing_environment,
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "not_ready"
    assert payload["reason"] == "environment_mismatch"


def test_workspace_flow_run_allows_non_connector_raw_yaml_without_readiness(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    recorded: list[dict] = []

    class FakeStore:
        def upsert_connection(self, connect_request, token_payload):
            return {"workspace": {"id": "workspace-1"}, "member": {"id": "member-1"}}

        def record_flow_run(
            self, *, token_payload, flow_id, title, result_payload, dry_run, env_keys
        ):
            row = {
                "run_id": "flow_run_1",
                "flow_id": flow_id,
                "title": title,
                "status": result_payload["status"],
                "dry_run": dry_run,
                "env_keys": env_keys,
                "step_count": len(result_payload["steps"]),
                "artifact_count": len(result_payload["artifacts"]),
                "created_at": "2026-07-09T00:00:00+00:00",
            }
            recorded.append(row)
            return row

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Local Report",
            "flow_yaml": (
                "name: Local Report\n"
                "---\n"
                "- emitReport:\n"
                "    title: Local Report\n"
                "    sections:\n"
                "      - Ready\n"
            ),
            "dry_run": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["run_record"]["run_id"] == "flow_run_1"
    assert recorded[0]["step_count"] == 1


def test_workspace_flow_run_records_history_when_supabase_available(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    recorded: list[dict] = []

    class FakeStore:
        def upsert_connection(self, connect_request, token_payload):
            return {"workspace": {"id": "workspace-1"}, "member": {"id": "member-1"}}

        def dashboard(self, token_payload):
            return {"connector_profiles": [ready_connector_profile()]}

        def record_flow_run(
            self, *, token_payload, flow_id, title, result_payload, dry_run, env_keys
        ):
            row = {
                "run_id": "flow_run_1",
                "flow_id": flow_id,
                "title": title,
                "status": result_payload["status"],
                "dry_run": dry_run,
                "env_keys": env_keys,
                "step_count": len(result_payload["steps"]),
                "artifact_count": len(result_payload["artifacts"]),
                "created_at": "2026-07-09T00:00:00+00:00",
            }
            recorded.append(row)
            return row

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/flows/run",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "title": "Company Health Check",
            "flow_yaml": COMPANY_HEALTH_TEMPLATE,
            "dry_run": True,
            "env": {"connector": "flowaccount", "environment": "production"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["variables"]["env"]["connector"] == "flowaccount"
    assert payload["variables"]["env"]["environment"] == "production"
    assert payload["run_record"]["run_id"] == "flow_run_1"
    assert payload["run_record"]["env_keys"] == ["connector", "environment"]
    assert recorded[0]["title"] == "Company Health Check"
    assert recorded[0]["step_count"] == 4
    assert recorded[0]["env_keys"] == ["connector", "environment"]


def test_workspace_flow_import_saves_batch(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    saved: list[dict] = []

    class FakeStore:
        def upsert_connection(self, connect_request, token_payload):
            return {"workspace": {"id": "workspace-1"}, "member": {"id": "member-1"}}

        def save_flow(self, *, token_payload, title, flow_yaml, metadata):
            row = {
                "flow_id": f"flow-{len(saved) + 1}",
                "title": title,
                "name": title,
                "status": "draft",
                "command_count": 3,
                "metadata": metadata,
                "yaml": flow_yaml,
            }
            saved.append(row)
            return row

    monkeypatch.setattr(server, "_product_store", lambda _settings=None: FakeStore())

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)
    token = client.post(
        "/api/connect",
        json={
            "invite_code": "invite-demo",
            "email": "user@example.com",
            "company": "Demo Co",
            "host_app": "codex",
        },
    ).json()["token"]

    response = client.post(
        "/api/flows/import",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "workspace": {"selected_count": 1},
            "flows": [
                {
                    "title": "Company Health Check",
                    "flow_yaml": COMPANY_HEALTH_TEMPLATE,
                    "metadata": {"relative_path": "company-health.yaml"},
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["imported_count"] == 1
    assert response.json()["flows"][0]["flow_id"] == "flow-1"
    assert "yaml" not in response.json()["flows"][0]
    assert saved[0]["metadata"]["source"] == "flow-import"
