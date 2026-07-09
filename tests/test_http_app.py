import pytest
from starlette.testclient import TestClient

from mercury_tools.flows.templates import COMPANY_HEALTH_TEMPLATE
from mercury_tools.mcp.server import create_http_app


@pytest.fixture(autouse=True)
def _clear_live_env(monkeypatch) -> None:
    for name in (
        "SUPABASE_URL",
        "SUPABASE_SERVICE_ROLE_KEY",
        "OPENAI_API_KEY",
        "MERCURY_TOOLS_PUBLIC_BASE_URL",
        "MERCURY_TOOLS_HTTP_BEARER_TOKEN",
        "MERCURY_CONNECT_INVITE_CODE",
        "MERCURY_CONNECT_SIGNING_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)


def ready_connector_profile(connector_id: str = "flowaccount") -> dict:
    return {
        "connector_id": connector_id,
        "environment": "production",
        "status": "ready",
        "metadata": {
            "setup_state": "ready",
            "enabled_capabilities": ["company.info.read"],
        },
    }


def test_remote_http_app_exposes_healthz(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/mcp")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "false")

    client = TestClient(create_http_app(require_auth=False))
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["mcp_path"] == "/mcp"


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
    assert "Mercury Connect" in page.text
    assert "MCP setup console for accounting AI hosts" in page.text
    assert "not the chat app" in page.text
    assert 'data-page="start"' in page.text
    assert "Mercury is the accounting agent tool layer" in page.text
    assert "Setup sections" in page.text
    assert 'id="connect-form"' not in page.text
    assert status.status_code == 200
    assert status.json()["mcp_endpoint"] == "https://mercury.example.com/mcp"
    assert status.json()["invite_required"] is True
    assert status.json()["console"]["purpose"] == "setup-console"
    assert status.json()["console"]["product_surface"] == "mcp-host"
    assert status.json()["pages"]["start"] == "/"
    assert status.json()["pages"]["connectors"] == "/connectors"
    assert status.json()["pages"]["knowledge"] == "/knowledge"
    assert status.json()["pages"]["flows"] == "/flows"
    assert status.json()["pages"]["mcp_api"] == "/mcp-api"
    assert "run_flow" in status.json()["flow_tools"]
    assert "run_flow_files" in status.json()["flow_tools"]
    assert "save_workspace_flow" in status.json()["flow_tools"]
    assert "list_workspace_flows" in status.json()["flow_tools"]
    assert "run_workspace_flow" in status.json()["flow_tools"]
    assert status.json()["dashboard"] == "/api/dashboard"
    assert status.json()["connector_credentials"] == "/api/connectors/credentials"
    assert status.json()["flow_validate"] == "/api/flows/validate"
    assert status.json()["flow_import"] == "/api/flows/import"


def test_product_console_exposes_separate_pages(monkeypatch) -> None:
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
        assert f'data-page="{page_name}"' in response.text
        assert response.text.count('<section class="page" data-page=') == 1
        assert "history.pushState" not in response.text
        if page_name != "connect":
            assert 'id="connect-form"' not in response.text


def test_flows_page_is_read_only_control_plane(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    response = client.get("/flows")

    assert response.status_code == 200
    assert "MCP/CLI only" in response.text
    assert "not browser UX" in response.text
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
    assert payload["endpoint"] == "https://mercury.example.com/mcp"
    assert payload["persistence"]["status"] == "degraded"

    authorized = client.get("/mcp", headers={"Authorization": f"Bearer {payload['token']}"})
    assert authorized.status_code != 401


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
    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


def test_workspace_flow_run_blocks_raw_yaml_with_connector_missing_environment(monkeypatch) -> None:
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-role")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    from mercury_tools.mcp import server

    connector_flow_missing_environment = (
        "name: FlowAccount Missing Environment\n"
        "tags: [accounting, read-only, flowaccount]\n"
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
    assert payload["status"] == "blocked"
    assert "connector credential setup" in payload["message"]


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
            return {"connector_profiles": [ready_connector_profile("peak")]}

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
            "env": {"connector": "peak", "environment": "production"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "planned"
    assert payload["variables"]["env"]["connector"] == "peak"
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
