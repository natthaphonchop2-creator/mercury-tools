from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from test_document_preview import (
    AUTH_USER_ID,
    CONNECTION_ID,
    NOW,
    PREVIEW_ID,
    SECRET_COUNTERPARTY,
    TENANT_ID,
    WORKSPACE_ID,
    _draft,
    _membership,
    _payload_vault,
    _qualification,
    _service,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260726105000_mercury_v1_operations_previews.sql"
OPERATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OPERATION_ITEM_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
EVENT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
_POSTGRES_OPT_IN = "MERCURY_V1_POSTGRES_TEST"
_POSTGRES_TABLES = (
    "mercury_document_previews",
    "mercury_preview_items",
    "mercury_operations",
    "mercury_operation_items",
    "mercury_operation_events",
)
_POSTGRES_DOCKER_COMMAND_TIMEOUT_SECONDS = 20
_POSTGRES_STARTUP_ATTEMPTS = 60
_POSTGRES_STARTUP_RETRY_SECONDS = 0.5


async def _prepared():
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    qualification = _qualification()
    service, store, connection, qualification, _ = _service()
    result = await service.prepare_document_create(
        service_test_principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )
    preview = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=result.preview_id,
    )
    return preview, store, connection, qualification


def service_test_principal():
    from mercury_tools.auth.models import MercuryPrincipal

    return MercuryPrincipal(
        subject=AUTH_USER_ID,
        client_id="mercury-test-client",
        scopes=frozenset({"mcp:tools"}),
    )


@pytest.mark.asyncio
async def test_in_memory_store_is_tenant_bound_immutable_and_returns_defensive_models() -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, _, _ = await _prepared()

    with pytest.raises(HostedPreviewError, match="^preview_not_found$"):
        store.get_preview(
            tenant_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=preview.preview_id,
        )
    with pytest.raises(HostedPreviewError, match="^preview_conflict$"):
        store.create_preview(preview)

    loaded = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
    )
    assert loaded == preview
    assert SECRET_COUNTERPARTY not in loaded.model_dump_json()
    assert SECRET_COUNTERPARTY not in repr(store)


@pytest.mark.asyncio
async def test_confirmable_load_rechecks_state_expiry_connection_catalog_and_payload_hash() -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, connection, qualification = await _prepared()
    confirmed = store.load_confirmable(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
        expected_state_version=1,
        connection=connection,
        qualification=qualification,
        now=NOW,
    )
    assert confirmed.provider_payload_for("client-item-1")["reference"] == "INV-DRAFT-001"
    assert SECRET_COUNTERPARTY not in repr(confirmed)
    assert SECRET_COUNTERPARTY not in confirmed.model_dump_json()

    cases = (
        ({"expected_state_version": 2}, "preview_state_stale"),
        ({"now": preview.expires_at}, "preview_expired"),
        (
            {"connection": connection.model_copy(update={"revision": connection.revision + 1})},
            "preview_binding_changed",
        ),
        (
            {
                "connection": connection.model_copy(
                    update={"provider_account_id": "changed-provider-company"}
                )
            },
            "preview_binding_changed",
        ),
        (
            {
                "qualification": qualification.model_copy(
                    update={"capability_version_sha256": "f" * 64}
                )
            },
            "preview_binding_changed",
        ),
    )
    base = {
        "tenant_id": TENANT_ID,
        "auth_user_id": AUTH_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "preview_id": preview.preview_id,
        "expected_state_version": 1,
        "connection": connection,
        "qualification": qualification,
        "now": NOW,
    }
    for updates, code in cases:
        with pytest.raises(HostedPreviewError, match=f"^{code}$"):
            store.load_confirmable(**{**base, **updates})

    tampered_envelope = preview.items[0].payload_envelope.model_copy(
        update={
            "ciphertext": preview.items[0].payload_envelope.ciphertext[:-1]
            + bytes([preview.items[0].payload_envelope.ciphertext[-1] ^ 1])
        }
    )
    tampered_item = preview.items[0].model_copy(update={"payload_envelope": tampered_envelope})
    tampered = preview.model_copy(update={"items": (tampered_item,)})
    from mercury_tools.execution.hosted.store import InMemoryHostedPreviewStore

    tampered_store = InMemoryHostedPreviewStore(payload_vault=_payload_vault(), clock=lambda: NOW)
    tampered_store.create_preview(tampered)
    with pytest.raises(HostedPreviewError, match="^preview_payload_changed$"):
        tampered_store.load_confirmable(
            **{
                **base,
                "preview_id": tampered.preview_id,
            }
        )


@pytest.mark.asyncio
async def test_confirmable_load_rejects_a_preview_with_rebound_expiry() -> None:
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        InMemoryHostedPreviewStore,
    )

    preview, _, connection, qualification = await _prepared()
    shifted_by = timedelta(minutes=1)
    shifted_purge_after = preview.payload_purge_after + shifted_by
    rebound = preview.model_copy(
        update={
            "created_at": preview.created_at + shifted_by,
            "expires_at": preview.expires_at + shifted_by,
            "payload_purge_after": shifted_purge_after,
            "items": tuple(
                item.model_copy(
                    update={
                        "created_at": item.created_at + shifted_by,
                        "payload_purge_after": shifted_purge_after,
                    }
                )
                for item in preview.items
            ),
        }
    )
    rebound_store = InMemoryHostedPreviewStore(payload_vault=_payload_vault(), clock=lambda: NOW)
    rebound_store.create_preview(rebound)

    with pytest.raises(HostedPreviewError, match="^preview_payload_changed$"):
        rebound_store.load_confirmable(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=rebound.preview_id,
            expected_state_version=1,
            connection=connection,
            qualification=qualification,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_prepare_normalizes_connection_resolver_errors_without_leaking_values() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.preview_service import HostedPreviewService
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        InMemoryHostedPreviewStore,
    )

    qualification = _qualification()
    payload_vault = _payload_vault()
    store = InMemoryHostedPreviewStore(payload_vault=payload_vault, clock=lambda: NOW)

    def resolve_connection(*_args: object) -> None:
        raise RuntimeError(SECRET_COUNTERPARTY)

    service = HostedPreviewService(
        store=store,
        payload_vault=payload_vault,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=lambda *_args: qualification,
        clock=lambda: NOW,
    )

    with pytest.raises(HostedPreviewError, match="^preview_binding_changed$") as raised:
        await service.prepare_document_create(
            service_test_principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )

    assert SECRET_COUNTERPARTY not in str(raised.value)


@pytest.mark.asyncio
async def test_operation_store_persists_versioned_transitions_and_retention_metadata() -> None:
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationState,
        PreviewState,
    )
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, _, _ = await _prepared()
    operation = HostedOperation.from_preview(
        preview,
        operation_id=OPERATION_ID,
        operation_item_ids=(OPERATION_ITEM_ID,),
        event_id=EVENT_ID,
        now=NOW,
    )
    stored = store.create_operation(operation)
    repeated = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            operation_item_ids=(UUID("14141414-1414-4414-8414-141414141414"),),
            event_id=UUID("15151515-1515-4515-8515-151515151515"),
            now=NOW,
        )
    )

    assert stored.preview_id == preview.preview_id
    assert repeated.operation_id == stored.operation_id
    assert stored.state is OperationState.AWAITING_CONFIRMATION
    assert stored.state_version == 1
    assert stored.payload_purge_after == NOW + timedelta(days=30)
    assert len(stored.items) == 1
    assert len(stored.events) == 1
    confirmed_preview = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
    )
    assert confirmed_preview.state is PreviewState.CONFIRMED
    assert confirmed_preview.state_version == 2

    item_transition = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=OPERATION_ID,
        operation_item_id=OPERATION_ITEM_ID,
        expected_state_version=1,
        target_state=OperationState.DISPATCHING,
        event_id=UUID("12121212-1212-4212-8212-121212121212"),
        occurred_at=NOW + timedelta(milliseconds=500),
        sanitized_reason="provider_create_started",
    )
    assert item_transition.items[0].state is OperationState.DISPATCHING
    assert item_transition.items[0].state_version == 2
    assert item_transition.events[-1].operation_item_id == OPERATION_ITEM_ID

    with pytest.raises(HostedPreviewError, match="^operation_state_stale$"):
        store.transition_operation_item(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            operation_item_id=OPERATION_ITEM_ID,
            expected_state_version=1,
            target_state=OperationState.SUCCEEDED,
            event_id=UUID("13131313-1313-4313-8313-131313131313"),
            occurred_at=NOW + timedelta(milliseconds=750),
            sanitized_reason="provider_succeeded",
        )

    transitioned = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=OPERATION_ID,
        expected_state_version=1,
        target_state=OperationState.DISPATCHING,
        event_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        occurred_at=NOW + timedelta(seconds=1),
        sanitized_reason="explicit_confirmation",
    )
    assert transitioned.state is OperationState.DISPATCHING
    assert transitioned.state_version == 2
    assert transitioned.events[-1].from_state is OperationState.AWAITING_CONFIRMATION
    assert transitioned.events[-1].to_state is OperationState.DISPATCHING

    with pytest.raises(HostedPreviewError, match="^operation_state_stale$"):
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            expected_state_version=1,
            target_state=OperationState.SUCCEEDED,
            event_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            occurred_at=NOW + timedelta(seconds=2),
            sanitized_reason="provider_succeeded",
        )


def _rpc_preview_response(payload: dict[str, object]) -> list[dict[str, object]]:
    preview = dict(payload["p_preview"])
    items = list(payload["p_items"])
    return [{"preview": preview, "items": items}]


@pytest.mark.asyncio
async def test_supabase_store_uses_narrow_rpc_and_never_sends_plaintext_payload() -> None:
    from mercury_tools.config import Settings
    from mercury_tools.execution.hosted.store import SupabaseHostedPreviewStore

    preview, _, _, _ = await _prepared()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if request.url.path.endswith("/rpc/save_mercury_document_preview"):
            return httpx.Response(200, json=_rpc_preview_response(payload))
        if request.url.path.endswith("/rpc/load_mercury_document_preview"):
            return httpx.Response(
                200,
                json=[
                    {
                        "preview": preview.storage_record(),
                        "items": [item.storage_record() for item in preview.items],
                    }
                ],
            )
        return httpx.Response(404, json={"message": "not_found"})

    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="test-service-role-key",
        supabase_auth_issuer="https://example.supabase.co/auth/v1",
        openai_api_key="",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseHostedPreviewStore(
            settings=settings,
            payload_vault=_payload_vault(),
            http_client=client,
        )
        saved = store.create_preview(preview)
        loaded = store.get_preview(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=PREVIEW_ID,
        )

    assert saved == preview
    assert loaded == preview
    assert [request.url.path for request in requests] == [
        "/rest/v1/rpc/save_mercury_document_preview",
        "/rest/v1/rpc/load_mercury_document_preview",
    ]
    serialized_requests = b"\n".join(request.content for request in requests).decode("utf-8")
    assert SECRET_COUNTERPARTY not in serialized_requests
    assert "payload_ciphertext" in serialized_requests
    assert requests[0].headers["authorization"] == "Bearer test-service-role-key"


def test_migration_is_expand_only_rls_bound_and_serializes_state_transitions() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    compact = " ".join(sql.lower().split())

    assert not re.search(r"\b(drop|truncate)\s+(table|column)\b", compact)
    for table in (
        "mercury_document_previews",
        "mercury_preview_items",
        "mercury_operations",
        "mercury_operation_items",
        "mercury_operation_events",
    ):
        assert f"create table if not exists public.{table}" in compact
        assert f"alter table public.{table} enable row level security" in compact
        assert f"grant all on table public.{table} to service_role" in compact

    assert "unique (workspace_id, connection_id, payload_hash)" in compact
    assert "unique (preview_id, client_item_id)" in compact
    assert "unique (preview_id, payload_hash)" in compact
    assert "payload_ciphertext" in compact
    assert "payload_envelope_created_at" in compact
    assert "sanitized_summary" in compact
    assert "payload_purge_after" in compact
    assert "mercury_preview_payload_purge_idx" in compact
    assert "mercury_operation_payload_purge_idx" in compact
    assert "for update" in compact
    assert "state_version = state_version + 1" in compact
    assert "p_expected_state_version" in compact
    assert "p_expected_preview_state_version" in compact
    assert "transition_mercury_operation_item" in compact
    assert "mercury_assert_provider_backend_workspace_access" in compact
    assert "failed_pre_dispatch" in compact
    assert "provider_rejected" in compact
    assert "public.digest" in compact
    assert "pg_catalog.digest" not in compact
    assert "member.tenant_id = tenant_id" not in compact
    assert "jsonb_object_keys" in compact
    assert "update public.mercury_preview_items" in compact
    assert "expires_at = created_at + pg_catalog.make_interval(secs => 1800)" in compact
    assert "payload_purge_after = expires_at + pg_catalog.make_interval(hours => 24)" in compact
    assert "payload_purge_after <= created_at + pg_catalog.make_interval(days => 30)" in compact


def test_hosted_modules_do_not_import_local_repository_sqlite_or_local_ttl_state() -> None:
    hosted = ROOT / "src/mercury_tools/execution/hosted"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(hosted.glob("*.py"))
        if path.name != "read_service.py"
    )
    for forbidden in (
        "RepositoryContext",
        "LocalRequestStore",
        "sqlite3",
        "mercury_tools.local",
    ):
        assert forbidden not in text

    assert "from mercury_tools.execution.models import PREVIEW_TTL" not in text

    from mercury_tools.execution.models import PREVIEW_TTL

    assert timedelta(minutes=15) == PREVIEW_TTL


@pytest.mark.integration
def test_postgresql_preview_migration_applies_twice_with_rls_and_service_acl() -> None:
    if os.environ.get(_POSTGRES_OPT_IN) != "1":
        pytest.skip(f"set {_POSTGRES_OPT_IN}=1 to run disposable PostgreSQL regression")

    def docker(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        command = ["docker", *args]
        try:
            return subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                timeout=_POSTGRES_DOCKER_COMMAND_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(
                command,
                124,
                stdout="",
                stderr="docker_command_timed_out",
            )

    docker_info = docker("info")
    if docker_info.returncode == 124:
        pytest.fail("Docker availability check timed out")
    if docker_info.returncode != 0:
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task12-postgres-{uuid4().hex[:12]}"
    database = "mercury_task12_test"
    started = docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        f"POSTGRES_DB={database}",
        "postgres:17-alpine",
    )
    assert started.returncode == 0, started.stderr

    def psql_result(sql: str) -> subprocess.CompletedProcess[str]:
        return docker(
            "exec",
            "-i",
            container,
            "psql",
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-d",
            database,
            input_text=sql,
        )

    def psql(sql: str) -> str:
        result = psql_result(sql)
        assert result.returncode == 0, f"{result.stderr.strip()}\n{result.stdout[-4000:].strip()}"
        return result.stdout.strip()

    try:
        for _ in range(_POSTGRES_STARTUP_ATTEMPTS):
            readiness = docker(
                "exec",
                container,
                "psql",
                "-qAt",
                "-U",
                "postgres",
                "-d",
                database,
                "-c",
                "select 1",
            )
            if readiness.returncode == 0 and readiness.stdout.strip() == "1":
                break
            if readiness.returncode == 124:
                pytest.fail("Docker readiness probe timed out")
            time.sleep(_POSTGRES_STARTUP_RETRY_SECONDS)
        else:
            pytest.fail("disposable PostgreSQL did not become ready")

        psql(
            """
            do $$
            begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'service_role') then
                create role service_role nologin bypassrls;
              end if;
            end;
            $$;
            create extension if not exists pgcrypto;
            create table public.mercury_tenants (
              id uuid primary key
            );
            create table public.mercury_workspaces (
              id uuid primary key,
              tenant_id uuid not null references public.mercury_tenants(id),
              status text not null
            );
            create table public.mercury_workspace_members (
              tenant_id uuid not null references public.mercury_tenants(id),
              workspace_id uuid not null references public.mercury_workspaces(id),
              auth_user_id uuid not null,
              status text not null
            );
            create table public.mercury_provider_connections (
              id uuid primary key,
              tenant_id uuid not null references public.mercury_tenants(id),
              workspace_id uuid not null references public.mercury_workspaces(id),
              auth_user_id uuid not null,
              provider text not null,
              environment text not null,
              provider_account_id text not null,
              readiness text not null,
              revision bigint not null,
              granted_permissions jsonb not null
            );
            create table public.mercury_provider_capability_qualifications (
              id uuid primary key,
              provider text not null,
              environment text not null,
              provider_tool_name text not null,
              normalized_capability text not null,
              capability_version_sha256 text not null,
              schema_hash text not null,
              response_shape_hash text not null,
              evidence_revision_sha256 text not null,
              company_sha256 text not null,
              qualification_state text not null,
              evidence_expires_at timestamptz not null,
              required_permissions jsonb not null
            );
            grant usage on schema public to anon, authenticated, service_role;
            create schema if not exists auth;
            create or replace function auth.uid()
            returns uuid
            language sql
            stable
            as $$
              select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$;
            grant usage on schema auth to anon, authenticated, service_role;
            grant execute on function auth.uid() to anon, authenticated, service_role;
            create or replace function public.mercury_assert_provider_backend_workspace_access(
              p_tenant_id uuid,
              p_workspace_id uuid,
              p_auth_user_id uuid
            )
            returns void
            language plpgsql
            security definer
            set search_path = ''
            as $$
            begin
              if p_tenant_id is null or p_workspace_id is null or p_auth_user_id is null then
                raise insufficient_privilege using message = 'workspace_access_denied';
              end if;
            end;
            $$;
            """
        )
        migration_sql = MIGRATION.read_text(encoding="utf-8")
        psql(migration_sql)
        psql(migration_sql)

        table_list = ", ".join(f"'{name}'" for name in _POSTGRES_TABLES)
        evidence = json.loads(
            psql(
                f"""
                select json_build_object(
                  'table_count', count(*),
                  'rls_enabled', bool_and(class.relrowsecurity),
                  'authenticated_select', bool_or(
                    has_table_privilege('authenticated', class.oid, 'select')
                  ),
                  'service_all', bool_and(
                    has_table_privilege('service_role', class.oid, 'select')
                    and has_table_privilege('service_role', class.oid, 'insert')
                    and has_table_privilege('service_role', class.oid, 'update')
                    and has_table_privilege('service_role', class.oid, 'delete')
                  ),
                  'select_policies', (
                    select count(*)
                    from pg_policy as policy
                    join pg_class as target on target.oid = policy.polrelid
                    join pg_namespace as policy_namespace
                      on policy_namespace.oid = target.relnamespace
                    where policy_namespace.nspname = 'public'
                      and target.relname in ({table_list})
                      and policy.polcmd = 'r'
                  ),
                  'save_execute_service', has_function_privilege(
                    'service_role',
                    'public.save_mercury_document_preview(jsonb,jsonb)',
                    'execute'
                  ),
                  'save_execute_authenticated', has_function_privilege(
                    'authenticated',
                    'public.save_mercury_document_preview(jsonb,jsonb)',
                    'execute'
                  )
                )::text
                from pg_class as class
                join pg_namespace as namespace on namespace.oid = class.relnamespace
                where namespace.nspname = 'public'
                  and class.relname in ({table_list});
                """
            )
        )

        direct_authenticated = psql_result(
            "set role authenticated;\nselect count(*) from public.mercury_document_previews;"
        )
        assert direct_authenticated.returncode != 0
        assert evidence == {
            "table_count": 5,
            "rls_enabled": True,
            "authenticated_select": False,
            "service_all": True,
            "select_policies": 5,
            "save_execute_service": True,
            "save_execute_authenticated": False,
        }
        assert (
            psql("set role service_role;\nselect count(*) from public.mercury_document_previews;")
            == "0"
        )
    finally:
        cleanup = docker("rm", "-f", container)
        if cleanup.returncode not in (0, 1):
            pytest.fail("disposable PostgreSQL cleanup failed")
