import pytest
from starlette.testclient import TestClient

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
    assert 'data-page="connect"' in page.text
    assert status.status_code == 200
    assert status.json()["mcp_endpoint"] == "https://mercury.example.com/mcp"
    assert status.json()["invite_required"] is True
    assert status.json()["pages"]["connectors"] == "/connectors"
    assert "run_flow" in status.json()["flow_tools"]
    assert status.json()["dashboard"] == "/api/dashboard"
    assert status.json()["connector_credentials"] == "/api/connectors/credentials"


def test_product_console_exposes_separate_pages(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_INVITE_CODE", "invite-demo")
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "signing-secret")

    client = TestClient(create_http_app(require_auth=True), raise_server_exceptions=False)

    for path, page_name in (
        ("/connect", "connect"),
        ("/workspace", "workspace"),
        ("/connectors", "connectors"),
        ("/skills", "skills"),
        ("/audit", "audit"),
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert f'data-page="{page_name}"' in response.text


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
