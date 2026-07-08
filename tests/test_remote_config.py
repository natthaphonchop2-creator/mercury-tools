from mercury_tools.config import load_settings


def test_remote_settings_use_port_and_normalize_path(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_TOOLS_PORT", raising=False)
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "mcp")
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com/")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_BEARER_TOKEN", "demo-token")

    settings = load_settings()

    assert settings.mcp_transport == "streamable-http"
    assert settings.mcp_port == 9001
    assert settings.mcp_path == "/mcp"
    assert settings.mcp_endpoint == "https://mercury.example.com/mcp"
    assert settings.http_require_auth is True
    assert settings.http_auth_configured is True
