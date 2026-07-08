from starlette.testclient import TestClient

from mercury_tools.mcp.server import create_http_app


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
    assert status.status_code == 200
    assert status.json()["mcp_endpoint"] == "https://mercury.example.com/mcp"
    assert status.json()["invite_required"] is True


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

    authorized = client.get("/mcp", headers={"Authorization": f"Bearer {payload['token']}"})
    assert authorized.status_code != 401
