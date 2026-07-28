from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from uuid import UUID

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.auth.supabase_jwt import authorization_server_metadata_url
from mercury_tools.config import V1ConfigurationError
from mercury_tools.mcp.server import create_http_app
from mercury_tools.providers.oauth import FLOWACCOUNT_CALLBACK_PATH
from mercury_tools.v1.constants import CANONICAL_MCP_RESOURCE

AUTHORIZATION_SERVER = "https://vbnlkqvauqwnjbxngkas.supabase.co/auth/v1"
RESOURCE_METADATA_URL = (
    "https://mercury-tools-mcp.onrender.com/.well-known/oauth-protected-resource/mcp"
)


class StubResolver:
    def __init__(self, result: MercuryPrincipal | MercuryAuthError) -> None:
        self.result = result
        self.tokens: list[str] = []

    async def resolve(self, bearer_token: str) -> MercuryPrincipal:
        self.tokens.append(bearer_token)
        if isinstance(self.result, MercuryAuthError):
            raise self.result
        return self.result


class ConfiguredProviderOAuthService:
    async def complete_callback(self, _callback: object) -> object:
        raise AssertionError("startup wiring test must not dispatch a callback")


class PrincipalCloudDependencies:
    def __init__(self) -> None:
        self.provider_oauth_service = ConfiguredProviderOAuthService()

    async def flowaccount_oauth_callback(self, _request: Request) -> JSONResponse:
        raise AssertionError("callback is not exercised by this dependency stub")

    async def list_actions(self, request: Request) -> JSONResponse:
        principal = request.state.mercury_principal
        return JSONResponse({"subject": str(principal.subject)})

    async def get_action(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def resolve_validation(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def list_connectors(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def list_skills(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def get_skill(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def search_knowledge(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def get_document(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)


@pytest.fixture(autouse=True)
def _v1_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from mercury_tools.mcp import server
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    original_enabled = server._PROCESS_V1_ENABLED
    original_tools = dict(server.mcp._tool_manager._tools)
    values = {
        "MERCURY_V1_ENABLED": "true",
        "MERCURY_CANONICAL_MCP_RESOURCE": CANONICAL_MCP_RESOURCE,
        "SUPABASE_URL": "https://vbnlkqvauqwnjbxngkas.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-test",
        "SUPABASE_AUTH_ISSUER": AUTHORIZATION_SERVER,
        "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_test",
        "SUPABASE_JWKS_URL": f"{AUTHORIZATION_SERVER}/.well-known/jwks.json",
        "SUPABASE_JWT_AUDIENCE": CANONICAL_MCP_RESOURCE,
        "MERCURY_VAULT_ACTIVE_KEY": base64.b64encode(b"a" * 32).decode("ascii"),
        "MERCURY_VAULT_ACTIVE_KEY_VERSION": "v1",
        "FLOWACCOUNT_MCP_SANDBOX_URL": "https://flowaccount-sandbox.example/mcp",
        "FLOWACCOUNT_MCP_PRODUCTION_URL": "https://flowaccount.example/mcp",
        "FLOWACCOUNT_OAUTH_SANDBOX_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity-sandbox.flowaccount.example"
        ),
        "FLOWACCOUNT_OAUTH_PRODUCTION_AUTHORIZATION_SERVER_ORIGIN": (
            "https://identity.flowaccount.example"
        ),
        "PEAK_MCP_UAT_URL": "https://peak-uat.example/mcp",
        "PEAK_MCP_PRODUCTION_URL": "https://peak.example/mcp",
        "MERCURY_PROVIDER_CALLBACK_BASE_URL": "https://mercury-tools-mcp.onrender.com",
        "MERCURY_TOOLS_HTTP_REQUIRE_AUTH": "false",
        "MERCURY_TOOLS_ENABLE_LEGACY_HTTP_API": "false",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    with server._PROCESS_V1_CONFIGURATION_LOCK:
        configure_v1_tools(server.mcp, enabled=True)
        server._PROCESS_V1_ENABLED = True
    try:
        yield
    finally:
        with server._PROCESS_V1_CONFIGURATION_LOCK:
            server.mcp._tool_manager._tools.clear()
            server.mcp._tool_manager._tools.update(original_tools)
            server._PROCESS_V1_ENABLED = original_enabled


def _principal() -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=UUID("12345678-1234-5678-9234-567812345678"),
        client_id="client-1",
        scopes=frozenset({"openid", "email", "profile"}),
        token_id="token-1",
    )


def test_v1_default_http_app_builds_and_runs_production_oauth_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp import server

    events: list[str] = []

    class Composition:
        provider_oauth_service = ConfiguredProviderOAuthService()

        async def startup(self) -> None:
            events.append("startup")

        async def aclose(self) -> None:
            events.append("close")

    def build(*, settings, principal_resolver):
        assert settings.v1_enabled is True
        assert isinstance(principal_resolver, StubResolver)
        events.append("build")
        return Composition()

    monkeypatch.setattr(server, "build_provider_oauth_production_composition", build)
    app = create_http_app(principal_resolver=StubResolver(_principal()))

    assert FLOWACCOUNT_CALLBACK_PATH in {getattr(route, "path", None) for route in app.routes}
    with TestClient(app):
        assert events == ["build", "startup"]
    assert events == ["build", "startup", "close"]


def test_v1_custom_cloud_dependencies_cannot_bypass_oauth_validation() -> None:
    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_missing",
    ):
        create_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=object(),
        )

    incomplete_dependencies = type(
        "IncompleteDependencies",
        (),
        {"provider_oauth_service": ConfiguredProviderOAuthService()},
    )()
    with pytest.raises(
        V1ConfigurationError,
        match="v1_cloud_dependencies_invalid",
    ):
        create_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=incomplete_dependencies,
        )

    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_invalid",
    ):
        create_http_app(
            principal_resolver=StubResolver(_principal()),
            provider_oauth_service=object(),
        )


def test_two_live_v1_apps_reject_contradictory_configuration_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.auth.middleware import MercuryOAuthMiddleware
    from mercury_tools.mcp.server import mcp

    first_app = create_http_app(
        principal_resolver=StubResolver(_principal()),
        cloud_dependencies=PrincipalCloudDependencies(),
    )
    registered_tool = mcp._tool_manager.get_tool("get_mercury_context")
    second_app = create_http_app(
        principal_resolver=StubResolver(_principal()),
        cloud_dependencies=PrincipalCloudDependencies(),
    )

    with (
        TestClient(first_app, raise_server_exceptions=False) as first_client,
        TestClient(second_app, raise_server_exceptions=False) as second_client,
    ):
        assert registered_tool is not None
        for app in (first_app, second_app):
            assert any(
                middleware.cls is MercuryOAuthMiddleware for middleware in app.user_middleware
            )
        assert first_client.get("/mcp").status_code == 401
        assert second_client.get("/mcp").status_code == 401
        assert "get_mercury_context" in {tool.name for tool in asyncio.run(mcp.list_tools())}

        monkeypatch.setenv("MERCURY_V1_ENABLED", "false")

        with pytest.raises(
            RuntimeError,
            match="^mercury_v1_process_configuration_conflict$",
        ):
            create_http_app(require_auth=False)

        assert mcp._tool_manager.get_tool("get_mercury_context") is registered_tool
        for app in (first_app, second_app):
            assert any(
                middleware.cls is MercuryOAuthMiddleware for middleware in app.user_middleware
            )
        assert "get_mercury_context" in {tool.name for tool in asyncio.run(mcp.list_tools())}
        assert first_client.get("/mcp").status_code == 401
        assert second_client.get("/mcp").status_code == 401


def test_missing_or_invalid_bearer_returns_rfc_9728_challenge() -> None:
    resolver = StubResolver(MercuryAuthError("mercury_token_invalid"))
    client = TestClient(
        create_http_app(
            principal_resolver=resolver,
            cloud_dependencies=PrincipalCloudDependencies(),
        ),
        raise_server_exceptions=False,
    )

    missing = client.get("/mcp")
    invalid = client.get("/mcp", headers={"Authorization": "Bearer invalid-token"})

    assert missing.status_code == invalid.status_code == 401
    assert missing.json() == {"error": "mercury_auth_required"}
    assert invalid.json() == {"error": "mercury_token_invalid"}
    assert missing.headers["WWW-Authenticate"] == (
        f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
    )
    assert invalid.headers["WWW-Authenticate"] == (
        f'Bearer error="invalid_token", resource_metadata="{RESOURCE_METADATA_URL}"'
    )
    assert resolver.tokens == ["invalid-token"]
    assert "invalid-token" not in invalid.text


def test_authenticated_but_unauthorized_request_returns_403() -> None:
    resolver = StubResolver(MercuryAuthError("mercury_scope_insufficient"))
    client = TestClient(
        create_http_app(
            principal_resolver=resolver,
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )

    response = client.get(
        "/api/cloud/v1/catalog/actions",
        headers={"Authorization": "Bearer valid-but-insufficient"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "mercury_scope_insufficient"}
    assert "valid-but-insufficient" not in response.text


def test_missing_required_identity_scope_returns_403() -> None:
    principal = _principal().model_copy(update={"scopes": frozenset({"openid", "email"})})
    client = TestClient(
        create_http_app(
            principal_resolver=StubResolver(principal),
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )

    response = client.get(
        "/api/cloud/v1/catalog/actions",
        headers={"Authorization": "Bearer valid-with-missing-scope"},
    )

    assert response.status_code == 403
    assert response.json() == {"error": "mercury_scope_insufficient"}


def test_principal_is_stored_before_v1_api_handler_runs() -> None:
    principal = _principal()
    client = TestClient(
        create_http_app(
            principal_resolver=StubResolver(principal),
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )

    response = client.get(
        "/api/cloud/v1/catalog/actions",
        headers={"Authorization": "Bearer asymmetric-jwt"},
    )

    assert response.status_code == 200
    assert response.json() == {"subject": str(principal.subject)}


def test_root_and_path_metadata_publish_exact_mercury_resource() -> None:
    client = TestClient(
        create_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )
    expected = {
        "resource": CANONICAL_MCP_RESOURCE,
        "authorization_servers": [AUTHORIZATION_SERVER],
        "scopes_supported": ["openid", "email", "profile"],
        "bearer_methods_supported": ["header"],
    }

    root = client.get("/.well-known/oauth-protected-resource")
    path_compatible = client.get("/.well-known/oauth-protected-resource/mcp")

    assert root.status_code == path_compatible.status_code == 200
    assert root.json() == path_compatible.json() == expected
    assert root.headers["Cache-Control"] == "public, max-age=300"


def test_supabase_dynamic_registration_is_discoverable_from_issuer() -> None:
    assert authorization_server_metadata_url(AUTHORIZATION_SERVER) == (
        "https://vbnlkqvauqwnjbxngkas.supabase.co/.well-known/oauth-authorization-server/auth/v1"
    )


def test_challenge_always_names_canonical_resource_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MERCURY_TOOLS_MCP_PATH", "/custom-mcp")
    client = TestClient(
        create_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )

    response = client.get("/custom-mcp")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == (
        f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'
    )


@pytest.mark.parametrize(
    "path",
    (
        "/",
        "/healthz",
        "/readyz",
        "/privacy",
        "/terms",
        "/support",
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-protected-resource/mcp",
    ),
)
def test_health_legal_support_and_metadata_routes_remain_public(path: str) -> None:
    resolver = StubResolver(MercuryAuthError("mercury_token_invalid"))
    client = TestClient(
        create_http_app(
            principal_resolver=resolver,
            cloud_dependencies=PrincipalCloudDependencies(),
        ),
        raise_server_exceptions=False,
    )

    response = client.get(path)

    assert response.status_code != 401
    assert resolver.tokens == []


def test_v1_rejects_legacy_mc_token() -> None:
    resolver = StubResolver(MercuryAuthError("mercury_token_invalid"))
    v1_client = TestClient(
        create_http_app(
            principal_resolver=resolver,
            cloud_dependencies=PrincipalCloudDependencies(),
        )
    )
    assert (
        v1_client.get(
            "/mcp",
            headers={"Authorization": "Bearer mc_legacy"},
        ).status_code
        == 401
    )
