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
    assert "OPENAI_API_KEY on remote service" in result.missing
    assert "local bearer token for MCP verification" in result.missing
