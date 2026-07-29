from __future__ import annotations

import asyncio
import base64
import inspect
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.auth.supabase_jwt import authorization_server_metadata_url
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.config import V1ConfigurationError
from mercury_tools.mcp.server import create_http_app, create_test_http_app
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderOperationClass,
    ProviderStatusClass,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.oauth import FLOWACCOUNT_CALLBACK_PATH
from mercury_tools.providers.peak_setup import (
    PEAK_SETUP_EXCHANGE_PATH,
    PEAK_SETUP_PATH,
)
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


class AllowAllResolver:
    async def resolve(self, _bearer_token: str) -> MercuryPrincipal:
        return _principal()


class ConfiguredProviderOAuthService:
    async def complete_callback(self, _callback: object) -> object:
        raise AssertionError("startup wiring test must not dispatch a callback")

    async def disconnect(self, *_args: object) -> object:
        raise AssertionError("startup wiring test must not disconnect FlowAccount")


class ConfiguredPeakSetupService:
    async def start(self, *_args: object) -> object:
        raise AssertionError("startup wiring test must not start PEAK setup")

    async def exchange(self, *_args: object) -> object:
        raise AssertionError("startup wiring test must not exchange PEAK setup")

    async def complete(self, *_args: object) -> object:
        raise AssertionError("startup wiring test must not complete PEAK setup")

    async def disconnect(self, *_args: object) -> object:
        raise AssertionError("startup wiring test must not disconnect PEAK")


class PrincipalCloudDependencies:
    def __init__(self) -> None:
        self.provider_oauth_service = ConfiguredProviderOAuthService()
        self.peak_setup_service = ConfiguredPeakSetupService()

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

    async def peak_setup_page(self, request: Request) -> JSONResponse:
        del request
        return JSONResponse({"public_fragment_page": True})

    async def peak_setup_exchange(self, request: Request) -> JSONResponse:
        return await self.list_actions(request)

    async def peak_setup_submit(self, request: Request) -> JSONResponse:
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


def test_http_lifespan_projects_and_refreshes_generated_provider_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The serving HTTP MCP instance projects catalog tools before it is ready."""

    from mercury_tools.mcp import v1_tools
    from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

    now = datetime(2026, 7, 30, 12, tzinfo=UTC)
    workspace_id = UUID("12345678-1234-5678-9234-567812345678")
    connection_id = UUID("87654321-4321-8765-4321-876543218765")
    tenant_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="PRIVATE_PROVIDER_INVOICE_GET",
        normalized_capability="documents.invoice.get",
        input_schema={
            "type": "object",
            "properties": {"invoice_reference": {"type": "string", "minLength": 1}},
            "required": ["invoice_reference"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    qualification = definition.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": "b" * 64,
            "evidence_revision_sha256": "c" * 64,
            "qualification_evidence_uri": (
                "catalog://global/flowaccount/qualifications/"
                f"{definition.capability_version_sha256}-{'c' * 64}.json"
            ),
            "evidence_evaluated_at": now,
            "evidence_expires_at": now + timedelta(days=1),
        }
    )

    class Catalog:
        qualifications = [qualification]

        def list_provider_mcp_qualifications(self):
            return list(self.qualifications)

    class ConnectionStore:
        def load_connection(self, **_kwargs):
            return ProviderConnection(
                id=connection_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                auth_user_id=_principal().subject,
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                provider_account_id="company",
                account_display_name="Example Company",
                authorization_method=AuthorizationMethod.OAUTH2_PKCE,
                granted_permissions=("documents.read",),
                readiness=ConnectionReadiness.READY,
                revision=1,
                last_validated_at=now,
                credential_envelope_ids=(UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),),
                created_at=now,
                updated_at=now,
            )

    class Resolver:
        async def bind_exact_for_connection(self, _connection, **_kwargs):
            return qualification, QualifiedCapabilityBinding(
                provider=ProviderId.FLOWACCOUNT,
                environment="sandbox",
                normalized_capability=qualification.normalized_capability,
                provider_tool="PRIVATE_PROVIDER_INVOICE_GET",
                operation_class=ProviderOperationClass.READ,
                qualification_hash="c" * 64,
            )

    class Driver:
        async def call(self, *_args, **_kwargs):
            return ProviderCallResult(
                provider=ProviderId.FLOWACCOUNT,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={"invoice_id": "INV-1"},
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )

    runtime = SimpleNamespace(
        qualification_catalog=Catalog(),
        connection_store=ConnectionStore(),
        qualification_resolver=Resolver(),
        registry=SimpleNamespace(get=lambda _provider: Driver()),
    )
    membership = WorkspaceMembership(
        tenant_id=tenant_id,
        tenant_display_name="Example Tenant",
        workspace_id=workspace_id,
        workspace_display_name="Example Workspace",
        role=WorkspaceRole.MEMBER,
    )

    async def require_workspace(_context, *, workspace_id, service_factory):
        del service_factory
        assert workspace_id == membership.workspace_id
        return _principal(), membership

    monkeypatch.setattr(v1_tools, "_require_workspace", require_workspace)
    app = create_test_http_app(
        principal_resolver=StubResolver(_principal()),
        cloud_dependencies=PrincipalCloudDependencies(),
        generated_provider_runtime=runtime,
    )
    notifications: list[str] = []
    context = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: notifications.append("tools/list_changed")
        )
    )

    with TestClient(app):
        serving_mcp = app.state.mercury_mcp
        assert "mercury_flowaccount_invoice_get" in {
            tool.name for tool in asyncio.run(serving_mcp.list_tools())
        }
        serving_mcp.get_context = lambda: context
        _content, structured = asyncio.run(
            serving_mcp.call_tool(
                "mercury_flowaccount_invoice_get",
                {
                    "workspace_id": str(workspace_id),
                    "connection_id": str(connection_id),
                    "capability_version": qualification.capability_version_sha256,
                    "invoice_reference": "INV-1",
                },
            )
        )
        assert structured["data"] == {"invoice_id": "INV-1"}

        runtime.qualification_catalog.qualifications = []
        assert asyncio.run(app.state.refresh_generated_provider_tools(context)) is True
        assert "mercury_flowaccount_invoice_get" not in {
            tool.name for tool in asyncio.run(serving_mcp.list_tools())
        }
        assert notifications == ["tools/list_changed"]


def test_v1_default_http_app_builds_and_runs_production_oauth_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.db.catalog import SupabaseCatalogStore
    from mercury_tools.mcp import server
    from mercury_tools.providers.production import ProviderOAuthProductionComposition

    events: list[str] = []
    original_build = server.build_provider_oauth_production_composition

    def build(*, settings):
        assert settings.v1_enabled is True
        events.append("build")
        return original_build(settings=settings)

    async def startup(self) -> None:
        self.validate_for_runtime(self.settings)
        events.append("startup")

    async def close(self) -> None:
        events.append("close")
        await self.network_guard.aclose()
        if self.owns_state_http_client:
            await self.state_http_client.aclose()
        if self.owns_connection_http_client:
            self.connection_http_client.close()

    monkeypatch.setattr(server, "build_provider_oauth_production_composition", build)
    monkeypatch.setattr(ProviderOAuthProductionComposition, "startup", startup)
    monkeypatch.setattr(ProviderOAuthProductionComposition, "aclose", close)
    monkeypatch.setattr(SupabaseCatalogStore, "list_provider_mcp_qualifications", lambda _self: [])
    app = create_http_app()

    paths = {getattr(route, "path", None) for route in app.routes}
    assert {
        FLOWACCOUNT_CALLBACK_PATH,
        PEAK_SETUP_PATH,
        PEAK_SETUP_EXCHANGE_PATH,
    } <= paths
    with TestClient(app):
        assert events == ["build", "startup"]
    assert events == ["build", "startup", "close"]


def test_v1_custom_cloud_dependencies_cannot_bypass_oauth_validation() -> None:
    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_missing",
    ):
        create_test_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=object(),
        )

    incomplete_dependencies = type(
        "IncompleteDependencies",
        (),
        {
            "provider_oauth_service": ConfiguredProviderOAuthService(),
            "peak_setup_service": ConfiguredPeakSetupService(),
        },
    )()
    with pytest.raises(
        V1ConfigurationError,
        match="v1_cloud_dependencies_invalid",
    ):
        create_test_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=incomplete_dependencies,
        )

    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_invalid",
    ):
        create_test_http_app(
            principal_resolver=StubResolver(_principal()),
            provider_oauth_service=object(),
        )

    class CallbackOnlyProviderOAuthService:
        async def complete_callback(self, _callback: object) -> object:
            raise AssertionError("startup wiring test must not dispatch a callback")

    with pytest.raises(
        V1ConfigurationError,
        match="v1_provider_oauth_service_invalid",
    ):
        create_test_http_app(
            principal_resolver=StubResolver(_principal()),
            provider_oauth_service=CallbackOnlyProviderOAuthService(),
            peak_setup_service=ConfiguredPeakSetupService(),
        )

    missing_peak = PrincipalCloudDependencies()
    del missing_peak.peak_setup_service
    with pytest.raises(
        V1ConfigurationError,
        match="v1_peak_setup_service_missing",
    ):
        create_test_http_app(
            principal_resolver=StubResolver(_principal()),
            cloud_dependencies=missing_peak,
        )


def test_allow_all_resolver_cannot_enter_production_http_graph() -> None:
    from mercury_tools.mcp import server
    from mercury_tools.providers.production import build_provider_oauth_production_composition

    parameters = inspect.signature(create_http_app).parameters

    assert set(parameters) == {"require_auth"}
    assert {
        "provider_oauth_composition",
        "cloud_dependencies",
        "provider_oauth_service",
        "principal_resolver",
        "consent_handoff",
        "consent_http_client",
    }.isdisjoint(parameters)
    assert set(inspect.signature(build_provider_oauth_production_composition).parameters) == {
        "settings"
    }
    with pytest.raises(TypeError):
        build_provider_oauth_production_composition(
            settings=server.load_settings(),
            principal_resolver=AllowAllResolver(),
        )


def test_arbitrary_dependencies_are_isolated_to_explicit_test_factory() -> None:
    from mercury_tools.mcp import server
    from mercury_tools.providers.production import (
        build_provider_oauth_production_composition,
        build_test_provider_oauth_production_composition,
    )

    factory = getattr(server, "create_test_http_app", None)
    assert callable(factory)

    app = factory(
        principal_resolver=StubResolver(_principal()),
        cloud_dependencies=PrincipalCloudDependencies(),
    )

    assert FLOWACCOUNT_CALLBACK_PATH in {getattr(route, "path", None) for route in app.routes}
    assert "test_app_factory" not in inspect.signature(server.serve).parameters
    production_builder_parameters = inspect.signature(
        build_provider_oauth_production_composition
    ).parameters
    assert set(production_builder_parameters) == {"settings"}
    assert {
        "state_http_client",
        "connection_http_client",
        "network_guard",
        "workspace_service",
    } <= set(inspect.signature(build_test_provider_oauth_production_composition).parameters)


def test_serve_uses_exact_production_factory_and_disables_sensitive_access_log(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import uvicorn

    from mercury_tools.mcp import server

    app = object()
    factory_calls: list[dict[str, object]] = []
    run_calls: list[tuple[object, dict[str, object]]] = []
    sensitive_target = (
        f"{FLOWACCOUNT_CALLBACK_PATH}?code=CODE_SENTINEL&state=STATE_SENTINEL"
        "&error_description=DESCRIPTION_SENTINEL&error_uri=https%3A%2F%2Ferror.example"
    )

    def production_factory(**kwargs):
        factory_calls.append(dict(kwargs))
        return app

    def reject_test_factory(**_kwargs):
        raise AssertionError("production serve must not call the test-only app factory")

    def run(selected_app, **kwargs):
        run_calls.append((selected_app, dict(kwargs)))
        if kwargs.get("access_log", True):
            logging.getLogger("uvicorn.access").info(
                '%s - "%s %s HTTP/1.1" %d',
                "127.0.0.1",
                "GET",
                sensitive_target,
                200,
            )

    monkeypatch.setattr(server, "create_http_app", production_factory)
    monkeypatch.setattr(server, "create_test_http_app", reject_test_factory)
    monkeypatch.setattr(uvicorn, "run", run)
    caplog.set_level(logging.INFO, logger="uvicorn.access")

    server.serve(
        transport="streamable-http",
        host="127.0.0.1",
        port=8765,
        require_auth=True,
    )

    assert factory_calls == [{"require_auth": True}]
    assert run_calls == [
        (
            app,
            {
                "host": "127.0.0.1",
                "port": 8765,
                "access_log": False,
            },
        )
    ]
    rendered = caplog.text
    for sensitive in (
        "CODE_SENTINEL",
        "STATE_SENTINEL",
        "DESCRIPTION_SENTINEL",
        "error.example",
    ):
        assert sensitive not in rendered


def test_two_live_v1_apps_reject_contradictory_configuration_without_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.auth.middleware import MercuryOAuthMiddleware
    from mercury_tools.mcp.server import mcp

    first_app = create_test_http_app(
        principal_resolver=StubResolver(_principal()),
        cloud_dependencies=PrincipalCloudDependencies(),
    )
    registered_tool = mcp._tool_manager.get_tool("get_mercury_context")
    second_app = create_test_http_app(
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
        create_test_http_app(
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


def test_peak_setup_page_is_public_but_posts_require_the_browser_session() -> None:
    resolver = StubResolver(_principal())
    client = TestClient(
        create_test_http_app(
            principal_resolver=resolver,
            cloud_dependencies=PrincipalCloudDependencies(),
        ),
        raise_server_exceptions=False,
    )

    page = client.get(PEAK_SETUP_PATH)
    missing_exchange = client.post(PEAK_SETUP_EXCHANGE_PATH)
    authenticated_exchange = client.post(
        PEAK_SETUP_EXCHANGE_PATH,
        headers={"Authorization": "Bearer mercury-principal-token"},
    )
    missing_submit = client.post(PEAK_SETUP_PATH)
    authenticated_submit = client.post(
        PEAK_SETUP_PATH,
        headers={"Authorization": "Bearer mercury-principal-token"},
    )

    assert page.status_code == 200
    assert page.json() == {"public_fragment_page": True}
    assert missing_exchange.status_code == missing_submit.status_code == 401
    assert missing_exchange.json() == missing_submit.json() == {"error": "mercury_auth_required"}
    assert authenticated_exchange.status_code == authenticated_submit.status_code == 401
    assert (
        authenticated_exchange.json()
        == authenticated_submit.json()
        == {"error": "mercury_token_invalid"}
    )
    assert resolver.tokens == []


def test_authenticated_but_unauthorized_request_returns_403() -> None:
    resolver = StubResolver(MercuryAuthError("mercury_scope_insufficient"))
    client = TestClient(
        create_test_http_app(
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
        create_test_http_app(
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
        create_test_http_app(
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
        create_test_http_app(
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
        create_test_http_app(
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
        create_test_http_app(
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
        create_test_http_app(
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
