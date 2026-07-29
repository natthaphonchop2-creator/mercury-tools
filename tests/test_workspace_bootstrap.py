from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from starlette.requests import Request

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.config import Settings, V1ConfigurationError

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql"
)
PRODUCT_LAYER_PATH = ROOT / "supabase/migrations/0002_mercury_product_layer.sql"

SUBJECT = UUID("11111111-1111-4111-8111-111111111111")
OTHER_SUBJECT = UUID("22222222-2222-4222-8222-222222222222")
TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
WORKSPACE_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
OTHER_WORKSPACE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
ACCESS_TOKEN = "header.payload.signature"


def _principal(subject: UUID = SUBJECT) -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=subject,
        client_id="mercury-public-client",
        scopes=frozenset({"openid", "email", "profile"}),
        token_id="token-id",
    )


def _context_payload(
    *,
    workspace_id: UUID = WORKSPACE_ID,
    tenant_id: UUID = TENANT_ID,
    role: str = "owner",
) -> dict[str, object]:
    return {
        "status": "ok",
        "active_workspace_id": str(workspace_id),
        "memberships": [
            {
                "tenant_id": str(tenant_id),
                "tenant_display_name": "Personal",
                "workspace_id": str(workspace_id),
                "workspace_display_name": "Mercury Workspace",
                "role": role,
            }
        ],
        "next_allowed_actions": [
            "list_accounting_providers",
            "start_provider_connection",
        ],
    }


def _request_context(
    principal: MercuryPrincipal,
    access_token: str,
) -> SimpleNamespace:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [
                (b"authorization", f"Bearer {access_token}".encode("ascii")),
            ],
        }
    )
    request.state.mercury_principal = principal
    return SimpleNamespace(
        request_context=SimpleNamespace(request=request),
    )


def _normalized_sql(path: Path = MIGRATION_PATH) -> str:
    return " ".join(path.read_text(encoding="utf-8").lower().split())


def _function_body(sql: str, function_name: str) -> str:
    match = re.search(
        rf"create or replace function public\.{function_name}\(\).*?\$\$(.*?)\$\$;",
        sql,
        flags=re.DOTALL,
    )
    assert match is not None
    return " ".join(match.group(1).split())


def test_workspace_service_bootstrap_returns_closed_sanitized_context() -> None:
    from mercury_tools.workspaces.service import WorkspaceService

    calls: list[str] = []

    class FakeClient:
        def bootstrap_context(self) -> dict[str, object]:
            calls.append("called")
            return _context_payload()

    service = WorkspaceService(user_client_factory=lambda token: FakeClient())

    context = service.bootstrap(_principal(), ACCESS_TOKEN)

    assert context.active_workspace_id == WORKSPACE_ID
    assert context.memberships[0].role.value == "owner"
    assert calls == ["called"]
    serialized = context.model_dump(mode="json")
    assert "email" not in str(serialized).lower()
    assert "token" not in str(serialized).lower()
    assert "provider_state" not in serialized
    assert "provider_credentials" not in serialized
    assert ACCESS_TOKEN not in repr(service)


def test_repeated_and_concurrent_bootstrap_converges_without_service_state() -> None:
    from mercury_tools.workspaces.service import WorkspaceService

    class IdempotentBackend:
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self._contexts: dict[str, dict[str, object]] = {}
            self.created_tenants = 0
            self.created_workspaces = 0
            self.created_memberships = 0

        def bootstrap(self, access_token: str) -> dict[str, object]:
            with self._lock:
                context = self._contexts.get(access_token)
                if context is None:
                    context = _context_payload()
                    self._contexts[access_token] = context
                    self.created_tenants += 1
                    self.created_workspaces += 1
                    self.created_memberships += 1
                return context

    backend = IdempotentBackend()

    class FakeClient:
        def __init__(self, access_token: str) -> None:
            self.access_token = access_token

        def bootstrap_context(self) -> dict[str, object]:
            return backend.bootstrap(self.access_token)

    service = WorkspaceService(user_client_factory=FakeClient)
    with ThreadPoolExecutor(max_workers=12) as executor:
        contexts = list(
            executor.map(
                lambda _: service.bootstrap(_principal(), ACCESS_TOKEN),
                range(24),
            )
        )

    assert {context.active_workspace_id for context in contexts} == {WORKSPACE_ID}
    assert backend.created_tenants == 1
    assert backend.created_workspaces == 1
    assert backend.created_memberships == 1


def test_require_workspace_uses_explicit_uuid_and_rejects_cross_tenant_access() -> None:
    from mercury_tools.workspaces.models import WorkspaceRole
    from mercury_tools.workspaces.service import WorkspaceAccessError, WorkspaceService

    class FakeClient:
        def bootstrap_context(self) -> dict[str, object]:
            return _context_payload()

    service = WorkspaceService(user_client_factory=lambda token: FakeClient())

    membership = service.require_workspace(
        _principal(),
        ACCESS_TOKEN,
        WORKSPACE_ID,
        WorkspaceRole.OWNER,
    )
    assert membership.workspace_id == WORKSPACE_ID

    with pytest.raises(WorkspaceAccessError) as exc_info:
        service.require_workspace(
            _principal(OTHER_SUBJECT),
            "other.header.signature",
            OTHER_WORKSPACE_ID,
            WorkspaceRole.VIEWER,
        )
    assert exc_info.value.code == "workspace_access_denied"
    assert ACCESS_TOKEN not in repr(exc_info.value)

    with pytest.raises(ValueError, match="workspace_id_invalid"):
        service.require_workspace(  # type: ignore[arg-type]
            _principal(),
            ACCESS_TOKEN,
            str(WORKSPACE_ID),
            WorkspaceRole.OWNER,
        )

    signature = inspect.signature(WorkspaceService.require_workspace)
    assert signature.parameters["workspace_id"].default is inspect.Parameter.empty
    assert signature.parameters["workspace_id"].annotation in {UUID, "UUID"}


def test_require_workspace_enforces_role_without_ambient_workspace_selection() -> None:
    from mercury_tools.workspaces.models import WorkspaceRole
    from mercury_tools.workspaces.service import WorkspaceAccessError, WorkspaceService

    class FakeClient:
        def bootstrap_context(self) -> dict[str, object]:
            return _context_payload(role="viewer")

    service = WorkspaceService(user_client_factory=lambda token: FakeClient())

    membership = service.require_workspace(
        _principal(),
        ACCESS_TOKEN,
        WORKSPACE_ID,
        WorkspaceRole.VIEWER,
    )
    assert membership.role is WorkspaceRole.VIEWER

    with pytest.raises(WorkspaceAccessError) as exc_info:
        service.require_workspace(
            _principal(),
            ACCESS_TOKEN,
            WORKSPACE_ID,
            WorkspaceRole.MEMBER,
        )
    assert exc_info.value.code == "workspace_role_insufficient"


def test_supabase_user_client_uses_publishable_key_and_end_user_bearer_only() -> None:
    from mercury_tools.db.user_client import SupabaseUserClient

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[_context_payload()])

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    settings = Settings(
        supabase_url="https://isolated.supabase.co",
        supabase_service_role_key="must-not-be-used",
        supabase_publishable_key="sb_publishable_isolated",
        openai_api_key="",
        supabase_auth_issuer="https://isolated.supabase.co/auth/v1",
    )
    client = SupabaseUserClient.from_settings(
        settings,
        access_token=ACCESS_TOKEN,
        http_client=http_client,
    )
    try:
        payload = client.bootstrap_context()
    finally:
        http_client.close()

    assert payload["active_workspace_id"] == str(WORKSPACE_ID)
    assert len(seen) == 1
    request = seen[0]
    assert request.url == (
        "https://isolated.supabase.co/rest/v1/rpc/bootstrap_mercury_context"
    )
    assert request.headers["authorization"] == f"Bearer {ACCESS_TOKEN}"
    assert request.headers["apikey"] == "sb_publishable_isolated"
    assert "must-not-be-used" not in str(request.headers)
    assert ACCESS_TOKEN not in repr(client)
    assert "must-not-be-used" not in repr(client)


def test_supabase_user_client_default_path_explicitly_refuses_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.db.user_client import SupabaseUserClient

    request_kwargs: list[dict[str, object]] = []

    def respond(
        _method: str,
        _url: str,
        **kwargs: object,
    ) -> httpx.Response:
        request_kwargs.append(kwargs)
        return httpx.Response(200, json=[_context_payload()])

    monkeypatch.setattr(httpx, "request", respond)
    client = SupabaseUserClient(
        project_url="https://isolated.supabase.co",
        auth_issuer="https://isolated.supabase.co/auth/v1",
        publishable_key="sb_publishable_isolated",
        access_token=ACCESS_TOKEN,
    )

    payload = client.bootstrap_context()

    assert payload["active_workspace_id"] == str(WORKSPACE_ID)
    assert request_kwargs[0]["follow_redirects"] is False


def test_supabase_user_client_refuses_redirects_from_injected_following_client() -> None:
    from mercury_tools.db.user_client import SupabaseUserClient

    seen: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "isolated.supabase.co":
            return httpx.Response(
                307,
                headers={"Location": "https://attacker.example/collect"},
            )
        return httpx.Response(200, json=[_context_payload()])

    http_client = httpx.Client(
        transport=httpx.MockTransport(respond),
        follow_redirects=True,
    )
    client = SupabaseUserClient(
        project_url="https://isolated.supabase.co",
        auth_issuer="https://isolated.supabase.co/auth/v1",
        publishable_key="sb_publishable_isolated",
        access_token=ACCESS_TOKEN,
        http_client=http_client,
    )
    try:
        with pytest.raises(RuntimeError) as exc_info:
            client.bootstrap_context()
    finally:
        http_client.close()

    assert str(exc_info.value) == "supabase_user_request_failed"
    assert [request.url.host for request in seen] == ["isolated.supabase.co"]
    assert [
        request.headers.get("apikey")
        for request in seen
        if request.url.host == "attacker.example"
    ] == []
    assert ACCESS_TOKEN not in repr(exc_info.value)


def test_supabase_user_client_errors_are_closed_and_secret_safe() -> None:
    from mercury_tools.db.user_client import SupabaseUserClient

    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": f"provider echoed {ACCESS_TOKEN}"},
        )

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = SupabaseUserClient(
        project_url="https://isolated.supabase.co",
        auth_issuer="https://isolated.supabase.co/auth/v1",
        publishable_key="sb_publishable_isolated",
        access_token=ACCESS_TOKEN,
        http_client=http_client,
    )
    try:
        with pytest.raises(RuntimeError) as exc_info:
            client.bootstrap_context()
    finally:
        http_client.close()

    assert str(exc_info.value) == "supabase_user_request_failed"
    assert ACCESS_TOKEN not in repr(exc_info.value)


@pytest.mark.parametrize(
    ("project_url", "auth_issuer", "error_code"),
    (
        (
            "http://isolated.supabase.co",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_url_invalid",
        ),
        (
            "https://embedded-secret@isolated.supabase.co",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_url_invalid",
        ),
        (
            "https://isolated.supabase.co?redirect=attacker",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_url_invalid",
        ),
        (
            "https://isolated.supabase.co#attacker",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_url_invalid",
        ),
        (
            "https://isolated.supabase.co/rest/v1",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_url_invalid",
        ),
        (
            "https://attacker.supabase.co",
            "https://isolated.supabase.co/auth/v1",
            "v1_supabase_origin_mismatch",
        ),
    ),
)
def test_supabase_user_client_rejects_untrusted_url_before_bearer_forwarding(
    project_url: str,
    auth_issuer: str,
    error_code: str,
) -> None:
    from mercury_tools.db.user_client import SupabaseUserClient

    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[_context_payload()])

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(V1ConfigurationError) as exc_info:
            SupabaseUserClient(
                project_url=project_url,
                auth_issuer=auth_issuer,
                publishable_key="sb_publishable_isolated",
                access_token=ACCESS_TOKEN,
                http_client=http_client,
            )
    finally:
        http_client.close()

    assert exc_info.value.code == error_code
    assert requests == []
    assert ACCESS_TOKEN not in repr(exc_info.value)
    assert "embedded-secret" not in repr(exc_info.value)
    assert "attacker.supabase.co" not in repr(exc_info.value)


def test_get_mercury_context_reads_actual_request_and_isolates_concurrent_tokens() -> None:
    from mercury_tools.mcp.v1_tools import get_mercury_context
    from mercury_tools.workspaces.models import MercuryContext

    calls: list[tuple[UUID, str]] = []
    lock = threading.Lock()

    class RecordingService:
        def bootstrap(
            self,
            principal: MercuryPrincipal,
            access_token: str,
        ) -> MercuryContext:
            with lock:
                calls.append((principal.subject, access_token))
            workspace_id = (
                WORKSPACE_ID if principal.subject == SUBJECT else OTHER_WORKSPACE_ID
            )
            return MercuryContext.model_validate(
                _context_payload(workspace_id=workspace_id)
            )

    async def run() -> tuple[object, object]:
        return await asyncio.gather(
            get_mercury_context(
                _request_context(_principal(SUBJECT), "token.a.signature"),
                service_factory=RecordingService,
            ),
            get_mercury_context(
                _request_context(_principal(OTHER_SUBJECT), "token.b.signature"),
                service_factory=RecordingService,
            ),
        )

    first, second = asyncio.run(run())

    assert first.active_workspace_id == WORKSPACE_ID
    assert second.active_workspace_id == OTHER_WORKSPACE_ID
    assert set(calls) == {
        (SUBJECT, "token.a.signature"),
        (OTHER_SUBJECT, "token.b.signature"),
    }
    assert "token.a.signature" not in repr(first)
    assert "token.b.signature" not in repr(second)


@pytest.mark.parametrize(
    "authorization",
    (
        None,
        "Basic abc",
        "Bearer",
        "Bearer token with spaces",
        "Bearer  token",
    ),
)
def test_get_mercury_context_rejects_missing_or_malformed_request_bearer(
    authorization: str | None,
) -> None:
    from mercury_tools.mcp.v1_tools import get_mercury_context

    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": headers,
        }
    )
    request.state.mercury_principal = _principal()
    context = SimpleNamespace(
        request_context=SimpleNamespace(request=request),
    )

    with pytest.raises(MercuryAuthError) as exc_info:
        asyncio.run(
            get_mercury_context(
                context,
                service_factory=lambda: pytest.fail("service must not be called"),
            )
        )

    assert exc_info.value.code == "mercury_auth_required"
    if authorization is not None:
        assert authorization not in repr(exc_info.value)


def test_get_mercury_context_is_gated_and_has_closed_public_schemas() -> None:
    from mercury_tools.mcp.contracts import V1_HOSTED_TOOL_NAMES
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 contract")
    configure_v1_tools(server, enabled=False)
    assert asyncio.run(server.list_tools()) == []

    configure_v1_tools(server, enabled=True)
    tools = asyncio.run(server.list_tools())
    registered_tool = server._tool_manager.get_tool("get_mercury_context")

    assert {tool.name for tool in tools} == V1_HOSTED_TOOL_NAMES
    tool = next(tool for tool in tools if tool.name == "get_mercury_context")
    assert tool.inputSchema == {
        "additionalProperties": False,
        "properties": {},
        "title": "get_mercury_contextArguments",
        "type": "object",
    }
    assert tool.outputSchema is not None
    assert tool.outputSchema["oneOf"] == [
        {"$ref": "#/$defs/Success"},
        {"$ref": "#/$defs/MercuryV1ErrorOutput"},
    ]
    success_schema = tool.outputSchema["$defs"]["Success"]
    assert success_schema["additionalProperties"] is False
    assert set(success_schema["properties"]) == {
        "status",
        "active_workspace_id",
        "memberships",
        "next_allowed_actions",
    }
    output_schema_text = str(tool.outputSchema).lower()
    assert "email" not in output_schema_text
    assert "token" not in output_schema_text
    assert "provider_state" not in output_schema_text
    assert "provider_credentials" not in output_schema_text
    assert tool.annotations is not None
    assert (
        tool.annotations.readOnlyHint,
        tool.annotations.destructiveHint,
        tool.annotations.idempotentHint,
        tool.annotations.openWorldHint,
    ) == (False, False, True, False)

    configure_v1_tools(server, enabled=True)
    assert server._tool_manager.get_tool("get_mercury_context") is registered_tool

    configure_v1_tools(server, enabled=False)
    configure_v1_tools(server, enabled=False)
    assert asyncio.run(server.list_tools()) == []


def test_enabled_registry_is_stable_during_concurrent_list_and_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP, mcp
    from mercury_tools.mcp.v1_tools import (
        GET_MERCURY_CONTEXT_TOOL,
        configure_v1_tools,
    )
    from mercury_tools.workspaces.models import MercuryContext

    global_registered_tool = mcp._tool_manager.get_tool(GET_MERCURY_CONTEXT_TOOL)

    class StubService:
        def bootstrap(
            self,
            _principal_value: MercuryPrincipal,
            _access_token: str,
        ) -> MercuryContext:
            return MercuryContext.model_validate(_context_payload())

    class RepeatedStartupConfigurationFastMCP(StrictInputFastMCP):
        async def list_tools(self):
            configure_v1_tools(
                self,
                enabled=True,
                service_factory=StubService,
            )
            return await super().list_tools()

    server = RepeatedStartupConfigurationFastMCP(
        "Mercury V1 concurrency contract"
    )
    configure_v1_tools(server, enabled=True, service_factory=StubService)
    original_remove_tool = server.remove_tool
    original_add_tool = server.add_tool
    registry_mutations: list[tuple[str, str]] = []
    listed_tools: list[set[str]] = []
    call_results: list[object] = []
    result_lock = threading.Lock()

    def tracked_remove_tool(name: str) -> None:
        registry_mutations.append(("remove", name))
        original_remove_tool(name)
        time.sleep(0.001)

    def tracked_add_tool(
        fn: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        name = kwargs.get("name")
        registry_mutations.append(("add", str(name)))
        original_add_tool(fn, *args, **kwargs)  # type: ignore[arg-type]

    def run_worker(worker_index: int) -> None:
        for operation_index in range(30):
            if (worker_index + operation_index) % 2 == 0:
                names = {
                    tool.name for tool in asyncio.run(server.list_tools())
                }
                with result_lock:
                    listed_tools.append(names)
            else:
                result = asyncio.run(
                    server.call_tool(GET_MERCURY_CONTEXT_TOOL, {})
                )
                with result_lock:
                    call_results.append(result)

    monkeypatch.setattr(
        server,
        "get_context",
        lambda: _request_context(_principal(), ACCESS_TOKEN),
    )
    monkeypatch.setattr(server, "remove_tool", tracked_remove_tool)
    monkeypatch.setattr(server, "add_tool", tracked_add_tool)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(run_worker, range(8)))

    assert registry_mutations == []
    assert len(listed_tools) == 120
    assert all(GET_MERCURY_CONTEXT_TOOL in names for names in listed_tools)
    assert len(call_results) == 120
    assert all(str(WORKSPACE_ID) in str(result) for result in call_results)
    assert mcp._tool_manager.get_tool(GET_MERCURY_CONTEXT_TOOL) is global_registered_tool


def test_identity_migration_is_expand_only_and_preserves_legacy_product_state() -> None:
    sql = _normalized_sql()
    product_sql = _normalized_sql(PRODUCT_LAYER_PATH)

    legacy_tables = set(
        re.findall(
            r"create table if not exists public\.(mercury_[a-z_]+)",
            product_sql,
        )
    )
    assert legacy_tables
    assert "drop table" not in sql
    assert "truncate table" not in sql
    assert "delete from public." not in sql
    assert "mercury_client_tokens" not in sql
    assert "public-demo" not in sql
    assert "alter column email drop not null" in sql
    assert sql.index("add column if not exists auth_user_id uuid") < sql.index(
        "alter column email drop not null"
    )
    assert "add column if not exists tenant_id uuid" in sql
    assert "add column if not exists owner_auth_user_id uuid" in sql
    assert "update public.mercury_workspaces" not in sql
    assert "update public.mercury_workspace_members" not in sql
    for table in legacy_tables:
        assert f"drop table public.{table}" not in sql


def test_identity_migration_enforces_idempotent_personal_workspace_uniqueness() -> None:
    sql = _normalized_sql()
    body = _function_body(sql, "bootstrap_mercury_context")

    assert "create table if not exists public.mercury_tenants" in sql
    assert (
        "create unique index if not exists "
        "mercury_tenants_one_personal_per_auth_user_idx"
        in sql
    )
    assert (
        "create unique index if not exists "
        "mercury_workspaces_one_automatic_default_per_auth_user_idx"
        in sql
    )
    assert (
        "create unique index if not exists mercury_workspace_members_auth_user_idx"
        in sql
    )
    assert body.count("insert into public.mercury_tenants") == 1
    assert body.count("insert into public.mercury_workspaces") == 1
    assert body.count("insert into public.mercury_workspace_members") == 1
    assert body.count("on conflict") >= 3
    assert "'owner'" in body
    assert "'personal'" in body
    assert "'mercury workspace'" in body
    workspace_conflict = body.split(
        "on conflict (owner_auth_user_id) where is_automatic_default",
        1,
    )[1].split("returning id into v_workspace_id", 1)[0]
    for field in (
        "workspace_key",
        "name",
        "plan",
        "status",
        "tenant_id",
        "owner_auth_user_id",
        "is_automatic_default",
    ):
        assert f"{field} = excluded.{field}" in workspace_conflict
    assert "updated_at = pg_catalog.statement_timestamp()" in workspace_conflict
    membership_conflict = body.split(
        "on conflict (workspace_id, auth_user_id) "
        "where auth_user_id is not null",
        1,
    )[1].split("select coalesce(", 1)[0]
    assert "host_app = excluded.host_app" in membership_conflict


def test_identity_migration_rls_and_rpc_derive_identity_only_from_auth_uid() -> None:
    sql = _normalized_sql()
    body = _function_body(sql, "bootstrap_mercury_context")

    for table in (
        "mercury_tenants",
        "mercury_workspaces",
        "mercury_workspace_members",
    ):
        assert f"alter table public.{table} enable row level security" in sql
    assert "auth.uid()" in sql
    assert "security definer" in sql
    assert "set search_path = ''" in sql
    assert "pg_temp" not in sql
    assert "auth.uid()" in body
    assert "pg_catalog.statement_timestamp()" in body
    assert "pg_catalog.jsonb_agg(" in body
    assert "pg_catalog.jsonb_build_object(" in body
    assert "v_auth_user_id pg_catalog.uuid" in body
    assert "v_memberships pg_catalog.jsonb" in body
    assert "::pg_catalog.text" in body
    assert "::pg_catalog.jsonb" in body
    assert "auth_user_id is null" in body
    assert "raise insufficient_privilege" in body
    assert "current_user" not in body
    assert "session_user" not in body
    assert "service_role" not in body
    assert re.search(
        r"create or replace function public\.bootstrap_mercury_context\(\)",
        sql,
    )
    assert (
        "revoke all on function public.bootstrap_mercury_context() "
        "from public, anon, authenticated"
    ) in sql
    assert (
        "grant execute on function public.bootstrap_mercury_context() "
        "to authenticated"
    ) in sql
    assert "grant execute on function public.bootstrap_mercury_context() to anon" not in sql
    assert (
        "grant select on table public.mercury_tenants, "
        "public.mercury_workspaces, public.mercury_workspace_members "
        "to authenticated"
    ) in sql
    assert (
        "grant insert on table public.mercury_tenants" not in sql
        and "grant update on table public.mercury_tenants" not in sql
    )


def test_identity_migration_policy_creation_is_rerun_safe_without_policy_drops() -> None:
    sql = _normalized_sql()

    assert "drop policy" not in sql
    assert sql.count("from pg_catalog.pg_policy") == 3
    assert sql.count("create policy mercury_") == 3
    assert sql.count("do $$") >= 3


def test_bootstrap_rpc_return_contract_is_sanitized() -> None:
    sql = _normalized_sql()
    signature_match = re.search(
        r"create or replace function public\.bootstrap_mercury_context\(\) "
        r"returns table \((.*?)\) language plpgsql",
        sql,
    )
    assert signature_match is not None
    signature = signature_match.group(1)

    assert "status pg_catalog.text" in signature
    assert "active_workspace_id pg_catalog.uuid" in signature
    assert "memberships pg_catalog.jsonb" in signature
    assert "next_allowed_actions pg_catalog.text[]" in signature
    for forbidden in (
        "email",
        "token",
        "credential",
        "provider",
        "service_role",
    ):
        assert forbidden not in signature
