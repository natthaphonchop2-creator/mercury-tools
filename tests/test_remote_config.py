from mercury_tools.config import load_settings


def test_remote_settings_use_port_and_normalize_path(monkeypatch) -> None:
    monkeypatch.delenv("MERCURY_TOOLS_PORT", raising=False)
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "mcp")
    monkeypatch.setenv("MERCURY_TOOLS_PUBLIC_BASE_URL", "https://mercury.example.com/")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_REQUIRE_AUTH", "true")
    monkeypatch.setenv("MERCURY_TOOLS_HTTP_BEARER_TOKEN", "demo-token")
    monkeypatch.setenv("MERCURY_TOOLS_EMBEDDING_PROVIDER", "hash")

    settings = load_settings()

    assert settings.mcp_transport == "streamable-http"
    assert settings.mcp_port == 9001
    assert settings.mcp_path == "/mcp"
    assert settings.mcp_endpoint == "https://mercury.example.com/mcp"
    assert settings.http_require_auth is True
    assert settings.http_auth_configured is True
    assert settings.embedding_provider == "hash"
    assert settings.embedding_configured is True


def test_openai_embedding_provider_requires_api_key(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_EMBEDDING_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    settings = load_settings()

    assert settings.embedding_provider == "openai"
    assert settings.openai_configured is False
    assert settings.embedding_configured is False


def test_connect_signing_secret_does_not_fallback_to_legacy_vault_secret(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CREDENTIAL_VAULT_SECRET", "legacy-secret")
    monkeypatch.delenv("MERCURY_CONNECT_SIGNING_SECRET", raising=False)

    settings = load_settings()

    assert settings.connect_signing_secret == ""
    assert settings.http_auth_configured is False


def test_connect_signing_secret_is_loaded_independently(monkeypatch) -> None:
    monkeypatch.setenv("MERCURY_CONNECT_SIGNING_SECRET", "connect-secret")
    monkeypatch.setenv("MERCURY_CREDENTIAL_VAULT_SECRET", "legacy-secret")

    settings = load_settings()

    assert settings.connect_signing_secret == "connect-secret"
