import httpx

from mercury_tools.remote import read_token, verify_remote


def test_remote_verify_ready_when_health_and_auth_are_good(tmp_path) -> None:
    token_file = tmp_path / "token.txt"
    token_file.write_text("demo-token\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "supabase": True,
                    "openai": True,
                    "embedding_provider": "hash",
                    "embedding_configured": True,
                    "mcp_path": "/mcp",
                    "http_auth_required": True,
                    "http_auth_configured": True,
                },
            )
        if request.url.path == "/mcp" and request.headers.get("authorization") is None:
            return httpx.Response(401, json={"error": "unauthorized"})
        if (
            request.url.path == "/mcp"
            and request.headers.get("authorization") == "Bearer demo-token"
        ):
            return httpx.Response(400, json={"error": "raw GET is not an MCP request"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = verify_remote(
        base_url="https://mercury.example.com",
        token=read_token(token_file=token_file),
        client=client,
    )

    assert result.ready is True
    assert result.unauthenticated_mcp_status_code == 401
    assert result.authenticated_mcp_status_code == 400
    assert result.authenticated_mcp_reachable is True
    assert result.missing == []
    assert result.errors == []


def test_remote_verify_reports_missing_remote_secrets() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "supabase": False,
                    "openai": False,
                    "embedding_provider": "hash",
                    "embedding_configured": True,
                    "mcp_path": "/mcp",
                    "http_auth_required": True,
                    "http_auth_configured": True,
                },
            )
        return httpx.Response(401, json={"error": "unauthorized"})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = verify_remote(base_url="https://mercury.example.com", client=client)

    assert result.ready is False
    assert "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY on remote service" in result.missing
    assert "local bearer token for MCP verification" in result.missing


def test_remote_verify_reports_openai_key_only_when_openai_embeddings_selected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "supabase": True,
                    "openai": False,
                    "embedding_provider": "openai",
                    "embedding_configured": False,
                    "mcp_path": "/mcp",
                    "http_auth_required": False,
                    "http_auth_configured": False,
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = verify_remote(base_url="https://mercury.example.com", client=client)

    assert result.ready is False
    assert "OPENAI_API_KEY on remote service" in result.missing


def test_remote_verify_rejects_public_service_with_legacy_http_api_enabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "supabase": True,
                    "embedding_provider": "hash",
                    "embedding_configured": True,
                    "mcp_path": "/mcp",
                    "http_auth_required": False,
                    "http_auth_configured": False,
                    "legacy_http_api": "enabled",
                },
            )
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))

    result = verify_remote(base_url="https://mercury.example.com", client=client)

    assert result.ready is False
    assert "disabled legacy HTTP API on public remote service" in result.missing
