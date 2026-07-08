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
