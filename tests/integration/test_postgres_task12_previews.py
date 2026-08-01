from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from test_document_preview import (
    AUTH_USER_ID,
    COMPANY_SHA256,
    CONNECTION_ID,
    TENANT_ID,
    WORKSPACE_ID,
    _connection,
    _draft,
    _principal,
    _qualification,
    _service,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PREDECESSOR_MIGRATIONS = (
    ROOT / "supabase/migrations/0002_mercury_product_layer.sql",
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql",
    ROOT / "supabase/migrations/20260726101000_mercury_v1_provider_connections.sql",
    ROOT / "supabase/migrations/20260726102000_mercury_v1_credential_vault.sql",
    ROOT / "supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql",
)
TASK12_MIGRATION = ROOT / "supabase/migrations/20260726105000_mercury_v1_operations_previews.sql"
OPT_IN = "MERCURY_V1_POSTGRES_TEST"
DATABASE = "mercury_task12_test"
OTHER_AUTH_USER_ID = UUID("abababab-abab-4bab-8bab-abababababab")
OTHER_TENANT_ID = UUID("bcbcbcbc-bcbc-4cbc-8cbc-bcbcbcbcbcbc")
OTHER_WORKSPACE_ID = UUID("cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd")
RAW_PROVIDER_RESULT_IDENTIFIER = (
    "Result jane@example.com 1234567890123 081-234-5678 "
    "sk-qrstuvwxyz123456 Bearer provider-secret-value api_key=provider-api-secret"
)


@dataclass(frozen=True)
class PostgresContext:
    container: str


def _docker(
    *args: str,
    input_text: str | None = None,
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["docker", *args],
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            ["docker", *args],
            124,
            stdout="",
            stderr="docker_command_timed_out",
        )


def _psql_result(context: PostgresContext, sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
        "exec",
        "-i",
        context.container,
        "psql",
        "-X",
        "-qAt",
        "-v",
        "ON_ERROR_STOP=1",
        "-U",
        "postgres",
        "-d",
        DATABASE,
        input_text=sql,
    )


def _psql(context: PostgresContext, sql: str) -> str:
    result = _psql_result(context, sql)
    assert result.returncode == 0, f"{result.stderr}\n{result.stdout[-4000:]}"
    return result.stdout.strip()


def _service_role(sql: str) -> str:
    return f"set role service_role;\n{sql}"


def _jsonb(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert "$mercury_json$" not in encoded
    return f"$mercury_json${encoded}$mercury_json$::pg_catalog.jsonb"


def _expect_error(context: PostgresContext, sql: str, code: str) -> None:
    result = _psql_result(context, _service_role(sql))
    assert result.returncode != 0, result.stdout
    assert code in result.stderr, result.stderr


def _expect_owner_error(context: PostgresContext, sql: str, code: str) -> None:
    result = _psql_result(context, sql)
    assert result.returncode != 0, result.stdout
    assert code in result.stderr, result.stderr


def _set_connection_revision_sql(revision: int) -> str:
    return f"""
        update public.mercury_provider_connections
        set revision = {revision}
        where id = '{CONNECTION_ID}';
    """


def _set_connection_readiness_sql(readiness: str) -> str:
    return f"""
        update public.mercury_provider_connections
        set readiness = '{readiness}'
        where id = '{CONNECTION_ID}';
    """


def _set_qualification_state_sql(qualification_id: UUID, *, enabled: bool) -> str:
    state = "enabled" if enabled else "disabled"
    reason = "null" if enabled else "'test'"
    return f"""
        update public.mercury_provider_capability_qualifications
        set qualification_state = '{state}', disable_reason = {reason}
        where id = '{qualification_id}';
    """


def _set_qualification_expiry_sql(qualification_id: UUID, expires_at: datetime) -> str:
    return f"""
        update public.mercury_provider_capability_qualifications
        set evidence_expires_at = '{expires_at.isoformat()}'
        where id = '{qualification_id}';
    """


@pytest.fixture(scope="module")
def postgres_context() -> PostgresContext:
    if os.environ.get(OPT_IN) != "1":
        pytest.skip(f"set {OPT_IN}=1 to run disposable PostgreSQL regression")
    availability = _docker("info")
    if availability.returncode == 124:
        pytest.fail("Docker availability check timed out")
    if availability.returncode != 0:
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task12-postgres-{uuid4().hex[:12]}"
    started = _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        f"POSTGRES_DB={DATABASE}",
        "postgres:17-alpine",
    )
    assert started.returncode == 0, started.stderr
    context = PostgresContext(container=container)
    try:
        for _ in range(60):
            ready = _docker(
                "exec",
                container,
                "psql",
                "-qAt",
                "-U",
                "postgres",
                "-d",
                DATABASE,
                "-c",
                "select 1",
            )
            if ready.returncode == 0 and ready.stdout.strip() == "1":
                break
            if ready.returncode == 124:
                pytest.fail("Docker readiness probe timed out")
            time.sleep(0.5)
        else:
            pytest.fail("disposable PostgreSQL did not become ready")

        _psql(
            context,
            """
            create role anon nologin;
            create role authenticated nologin;
            create role service_role nologin bypassrls;
            create schema auth;
            create function auth.uid()
            returns uuid
            language sql
            stable
            as $$
              select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid
            $$;
            grant usage on schema auth to anon, authenticated, service_role;
            grant execute on function auth.uid() to anon, authenticated, service_role;
            """,
        )
        for migration in PREDECESSOR_MIGRATIONS:
            _psql(context, migration.read_text(encoding="utf-8"))
        task12_sql = TASK12_MIGRATION.read_text(encoding="utf-8")
        _psql(context, task12_sql)
        _psql(context, task12_sql)
        _psql(
            context,
            f"""
            insert into public.mercury_tenants (
              id, tenant_type, display_name, personal_owner_auth_user_id
            ) values
              ('{TENANT_ID}', 'personal', 'Primary', '{AUTH_USER_ID}'),
              ('{OTHER_TENANT_ID}', 'personal', 'Other', '{OTHER_AUTH_USER_ID}');
            insert into public.mercury_workspaces (
              id, workspace_key, name, plan, status, metadata, tenant_id,
              owner_auth_user_id, is_automatic_default
            ) values
              ('{WORKSPACE_ID}', 'task12-primary', 'Primary', 'test', 'active', '{{}}',
               '{TENANT_ID}', '{AUTH_USER_ID}', true),
              ('{OTHER_WORKSPACE_ID}', 'task12-other', 'Other', 'test', 'active', '{{}}',
               '{OTHER_TENANT_ID}', '{OTHER_AUTH_USER_ID}', true);
            insert into public.mercury_workspace_members (
              workspace_id, email, role, host_app, status, auth_user_id, tenant_id
            ) values
              ('{WORKSPACE_ID}', null, 'owner', 'test', 'active',
               '{AUTH_USER_ID}', '{TENANT_ID}'),
              ('{OTHER_WORKSPACE_ID}', null, 'owner', 'test', 'active',
               '{OTHER_AUTH_USER_ID}', '{OTHER_TENANT_ID}');
            """,
        )
        yield context
    finally:
        cleanup = _docker("rm", "-f", container)
        if cleanup.returncode not in (0, 1):
            pytest.fail("disposable PostgreSQL cleanup failed")


def _authority(now: datetime):
    connection = _connection(
        created_at=now - timedelta(days=1),
        updated_at=now,
        last_validated_at=now,
    )
    qualification = _qualification().model_copy(
        update={
            "evidence_evaluated_at": now - timedelta(hours=1),
            "evidence_expires_at": now + timedelta(days=1),
        }
    )
    return connection, qualification


def _seed_authority(
    context: PostgresContext,
    *,
    now: datetime,
    connection,
    qualification,
) -> None:
    _psql(
        context,
        f"""
        insert into public.mercury_provider_connections (
          id, tenant_id, workspace_id, auth_user_id, provider, environment,
          provider_account_id, account_display_name, authorization_method,
          granted_permissions, readiness, revision, last_validated_at,
          credential_envelope_ids, created_at, updated_at
        ) values (
          '{connection.id}', '{connection.tenant_id}', '{connection.workspace_id}',
          '{connection.auth_user_id}', '{connection.provider.value}',
          '{connection.environment}', 'provider-company-sensitive-42',
          'Mercury Test Company', 'oauth2_pkce', '["documents.create"]',
          'ready', {connection.revision}, '{now.isoformat()}', '{{}}',
          '{(now - timedelta(days=1)).isoformat()}', '{now.isoformat()}'
        ) on conflict (id) do nothing;
        insert into public.mercury_provider_capability_qualifications (
          id, provider, environment, provider_tool_name, normalized_capability,
          input_schema, output_schema, schema_hash, response_shape_hash,
          required_permissions, capability_version_sha256, qualification_state,
          company_sha256, evidence_revision_sha256, qualification_evidence_uri,
          evidence_evaluated_at, evidence_expires_at
        ) values (
          '{qualification.id}', '{qualification.provider}', '{qualification.environment}',
          '{qualification.provider_tool_name}', '{qualification.normalized_capability}',
          {_jsonb(qualification.input_schema)}, {_jsonb(qualification.output_schema)},
          '{qualification.schema_hash}', '{qualification.response_shape_hash}',
          {_jsonb(list(qualification.required_permissions))},
          '{qualification.capability_version_sha256}', 'enabled',
          '{COMPANY_SHA256}', '{qualification.evidence_revision_sha256}',
          '{qualification.qualification_evidence_uri}',
          '{qualification.evidence_evaluated_at.isoformat()}',
          '{qualification.evidence_expires_at.isoformat()}'
        ) on conflict (id) do nothing;
        """,
    )


async def _build_preview(
    now: datetime,
    *,
    metadata_variant: bool = False,
    document_count: int = 2,
    ids: tuple[UUID, ...] | None = None,
    reference_prefix: str = "INV-PG",
):
    from mercury_tools.execution.hosted.models import BatchDocumentCreate

    connection, qualification = _authority(now)
    service, store, _, _, _ = _service(
        connection=connection,
        qualification=qualification,
        ids=ids
        or (
            UUID("60606060-6060-4060-8060-606060606060")
            if not metadata_variant
            else UUID("61616161-6161-4161-8161-616161616161"),
            UUID("70707070-7070-4070-8070-707070707070")
            if not metadata_variant
            else UUID("71717171-7171-4171-8171-717171717171"),
            UUID("80808080-8080-4080-8080-808080808080")
            if not metadata_variant
            else UUID("81818181-8181-4181-8181-818181818181"),
        ),
        clock=lambda: now,
    )
    documents = (
        _draft(
            client_item_id="first" if not metadata_variant else "changed-first",
            reference=f"{reference_prefix}-1",
            warnings=("review_one",) if not metadata_variant else ("changed_warning",),
        ),
        _draft(client_item_id="second", reference=f"{reference_prefix}-2"),
    )[:document_count]
    request = BatchDocumentCreate(
        mode="batch",
        documents=documents,
    )
    prepared = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        request,
    )
    preview = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=prepared.preview_id,
    )
    return preview, connection, qualification


def _save_preview_sql(preview) -> str:
    return f"""
        select pg_catalog.row_to_json(saved)::pg_catalog.text
        from public.save_mercury_document_preview(
          {_jsonb(preview.storage_record())},
          {_jsonb([item.storage_record() for item in preview.items])}
        ) as saved;
    """


def _save_operation_sql(operation) -> str:
    from mercury_tools.execution.hosted.store import operation_rpc_payload

    payload = operation_rpc_payload(operation)
    return f"""
        select pg_catalog.row_to_json(saved)::pg_catalog.text
        from public.save_mercury_operation(
          {_jsonb(payload["p_operation"])},
          {_jsonb(payload["p_items"])},
          {_jsonb(payload["p_events"])},
          1
        ) as saved;
    """


def _parent_transition_sql(
    operation_id: UUID,
    *,
    expected_version: int,
    target: str,
    event_id: UUID,
    occurred_at: datetime,
) -> str:
    return f"""
      select pg_catalog.row_to_json(changed)::pg_catalog.text
      from public.transition_mercury_operation(
        '{TENANT_ID}', '{WORKSPACE_ID}', '{AUTH_USER_ID}', '{operation_id}',
        {expected_version}, '{target}', '{event_id}', '{occurred_at.isoformat()}',
        'task12_behavior_test'
      ) as changed;
    """


def _item_transition_sql(
    operation_id: UUID,
    operation_item_id: UUID,
    *,
    expected_version: int,
    target: str,
    event_id: UUID,
    occurred_at: datetime,
    provider_result_identifier: str | None = None,
) -> str:
    result_identifier = (
        "null" if provider_result_identifier is None else f"'{provider_result_identifier}'"
    )
    return f"""
      select pg_catalog.row_to_json(changed)::pg_catalog.text
      from public.transition_mercury_operation_item(
        '{TENANT_ID}', '{WORKSPACE_ID}', '{AUTH_USER_ID}', '{operation_id}',
        '{operation_item_id}', {expected_version}, '{target}', '{event_id}',
        '{occurred_at.isoformat()}', 'task12_behavior_test', {result_identifier}
      ) as changed;
    """


def _text_array(values: tuple[str, ...]) -> str:
    if not values:
        return "array[]::pg_catalog.text[]"
    return "array[" + ",".join(f"'{value}'" for value in values) + "]::pg_catalog.text[]"


def test_postgresql_task12_rpcs_enforce_identity_authority_and_state(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.execution.hosted.models import HostedOperation

    now = datetime.now(UTC).replace(microsecond=0)
    preview, connection, qualification = asyncio.run(_build_preview(now))
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    saved = json.loads(_psql(postgres_context, _service_role(_save_preview_sql(preview))))
    assert saved["preview"]["id"] == str(preview.preview_id)
    assert saved["items"][0]["payload_ciphertext"]
    assert "provider_arguments" not in json.dumps(saved, sort_keys=True)

    _expect_error(
        postgres_context,
        f"""
        select * from public.load_mercury_document_preview(
          '{TENANT_ID}', '{WORKSPACE_ID}', '{OTHER_AUTH_USER_ID}', '{preview.preview_id}'
        );
        """,
        "workspace_access_denied",
    )
    direct_rls = _psql_result(
        postgres_context,
        f"""
        set role authenticated;
        set request.jwt.claim.sub = '{AUTH_USER_ID}';
        select count(*) from public.mercury_document_previews;
        """,
    )
    assert direct_rls.returncode != 0

    repeated, _, _ = asyncio.run(_build_preview(now))
    recovered = json.loads(_psql(postgres_context, _service_role(_save_preview_sql(repeated))))
    assert recovered["preview"]["id"] == str(preview.preview_id)

    metadata_variant, _, _ = asyncio.run(_build_preview(now, metadata_variant=True))
    assert metadata_variant.provider_call_hash == preview.provider_call_hash
    assert metadata_variant.preview_integrity_hash != preview.preview_integrity_hash
    _expect_error(
        postgres_context,
        _save_preview_sql(metadata_variant),
        "duplicate_provider_call",
    )

    operation = HostedOperation.from_preview(
        preview,
        operation_id=UUID("90909090-9090-4090-8090-909090909090"),
        operation_item_ids=(
            UUID("91919191-9191-4191-8191-919191919191"),
            UUID("92929292-9292-4292-8292-929292929292"),
        ),
        event_id=UUID("93939393-9393-4393-8393-939393939393"),
        now=now + timedelta(seconds=1),
    )

    _psql(
        postgres_context,
        _set_connection_revision_sql(8),
    )
    _expect_error(postgres_context, _save_operation_sql(operation), "preview_binding_changed")
    _psql(
        postgres_context,
        _set_connection_revision_sql(7),
    )
    _psql(
        postgres_context,
        _set_connection_readiness_sql("validation_failed"),
    )
    _expect_error(postgres_context, _save_operation_sql(operation), "preview_binding_changed")
    _psql(
        postgres_context,
        _set_connection_readiness_sql("ready"),
    )
    _psql(
        postgres_context,
        _set_qualification_state_sql(qualification.id, enabled=False),
    )
    _expect_error(postgres_context, _save_operation_sql(operation), "capability_unavailable")
    _psql(
        postgres_context,
        _set_qualification_state_sql(qualification.id, enabled=True),
    )
    _psql(
        postgres_context,
        _set_qualification_expiry_sql(qualification.id, now - timedelta(seconds=1)),
    )
    _expect_error(postgres_context, _save_operation_sql(operation), "capability_unavailable")
    _psql(
        postgres_context,
        _set_qualification_expiry_sql(qualification.id, now + timedelta(days=1)),
    )
    _psql(postgres_context, _service_role(_save_operation_sql(operation)))

    first_item, second_item = operation.items
    _expect_error(
        postgres_context,
        _item_transition_sql(
            operation.operation_id,
            first_item.operation_item_id,
            expected_version=1,
            target="dispatching",
            event_id=UUID("a1a1a1a1-a1a1-41a1-81a1-a1a1a1a1a1a1"),
            occurred_at=now + timedelta(seconds=2),
        ),
        "operation_transition_invalid",
    )
    _expect_error(
        postgres_context,
        _parent_transition_sql(
            operation.operation_id,
            expected_version=1,
            target="succeeded",
            event_id=UUID("a2a2a2a2-a2a2-42a2-82a2-a2a2a2a2a2a2"),
            occurred_at=now + timedelta(seconds=2),
        ),
        "operation_transition_invalid",
    )

    _psql(
        postgres_context,
        _set_qualification_state_sql(qualification.id, enabled=False),
    )
    _expect_error(
        postgres_context,
        _parent_transition_sql(
            operation.operation_id,
            expected_version=1,
            target="dispatching",
            event_id=UUID("a3a3a3a3-a3a3-43a3-83a3-a3a3a3a3a3a3"),
            occurred_at=now + timedelta(seconds=2),
        ),
        "capability_unavailable",
    )
    _psql(
        postgres_context,
        _set_qualification_state_sql(qualification.id, enabled=True),
    )

    calls = (
        _parent_transition_sql(
            operation.operation_id,
            expected_version=1,
            target="dispatching",
            event_id=UUID("a4a4a4a4-a4a4-44a4-84a4-a4a4a4a4a4a4"),
            occurred_at=now + timedelta(seconds=2),
        ),
        _parent_transition_sql(
            operation.operation_id,
            expected_version=1,
            target="dispatching",
            event_id=UUID("a5a5a5a5-a5a5-45a5-85a5-a5a5a5a5a5a5"),
            occurred_at=now + timedelta(seconds=2),
        ),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda sql: _psql_result(postgres_context, _service_role(sql)),
                calls,
            )
        )
    assert sorted(result.returncode == 0 for result in results) == [False, True]
    assert any("operation_state_stale" in result.stderr for result in results)

    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                first_item.operation_item_id,
                expected_version=1,
                target="dispatching",
                event_id=UUID("a6a6a6a6-a6a6-46a6-86a6-a6a6a6a6a6a6"),
                occurred_at=now + timedelta(seconds=3),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                first_item.operation_item_id,
                expected_version=2,
                target="outcome_unknown",
                event_id=UUID("a7a7a7a7-a7a7-47a7-87a7-a7a7a7a7a7a7"),
                occurred_at=now + timedelta(seconds=4),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                second_item.operation_item_id,
                expected_version=1,
                target="not_dispatched",
                event_id=UUID("a8a8a8a8-a8a8-48a8-88a8-a8a8a8a8a8a8"),
                occurred_at=now + timedelta(seconds=5),
            )
        ),
    )
    terminal = json.loads(
        _psql(
            postgres_context,
            _service_role(
                _parent_transition_sql(
                    operation.operation_id,
                    expected_version=2,
                    target="outcome_unknown",
                    event_id=UUID("a9a9a9a9-a9a9-49a9-89a9-a9a9a9a9a9a9"),
                    occurred_at=now + timedelta(seconds=6),
                )
            ),
        )
    )
    assert terminal["operation"]["status"] == "outcome_unknown"
    assert [item["status"] for item in terminal["items"]] == [
        "outcome_unknown",
        "not_dispatched",
    ]


def test_postgresql_schema_and_transition_contract_matches_python(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.execution.hosted.models import (
        OperationItemState,
        ParentOperationState,
        item_operation_transition_allowed,
        parent_operation_transition_allowed,
    )

    now = datetime.now(UTC).replace(microsecond=0)
    connection, qualification = _authority(now)
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    _expect_owner_error(
        postgres_context,
        """
        update public.mercury_provider_capability_qualifications
        set input_schema = '{}'::pg_catalog.jsonb
        where id = (select id from public.mercury_provider_capability_qualifications limit 1);
        """,
        "capability_unavailable",
    )

    child_vectors = (
        (),
        *((state,) for state in OperationItemState),
        (OperationItemState.SUCCEEDED, OperationItemState.SUCCEEDED),
        (OperationItemState.PROVIDER_REJECTED, OperationItemState.NOT_DISPATCHED),
        (OperationItemState.OUTCOME_UNKNOWN, OperationItemState.NOT_DISPATCHED),
        (OperationItemState.NEEDS_MANUAL_REVIEW, OperationItemState.NOT_DISPATCHED),
        (OperationItemState.CANCELLED, OperationItemState.CANCELLED),
        (OperationItemState.EXPIRED, OperationItemState.EXPIRED),
        (OperationItemState.FAILED_PRE_DISPATCH, OperationItemState.NOT_DISPATCHED),
        (OperationItemState.AWAITING_CONFIRMATION, OperationItemState.CANCELLED),
        (OperationItemState.DISPATCHING, OperationItemState.AWAITING_CONFIRMATION),
    )
    parent_cases = tuple(
        (case_id, current, target, child_states)
        for case_id, (current, target, child_states) in enumerate(
            (current, target, child_states)
            for child_states in child_vectors
            for current in ParentOperationState
            for target in ParentOperationState
        )
    )
    parent_values = ", ".join(
        f"({case_id}, '{current.value}', '{target.value}', "
        f"{_text_array(tuple(state.value for state in child_states))})"
        for case_id, current, target, child_states in parent_cases
    )
    parent_rows = _psql(
        postgres_context,
        _service_role(
            f"""
            select case_id, source_state, target_state,
              public.mercury_parent_operation_transition_is_allowed(
                source_state, target_state, child_states
              )
            from (values {parent_values})
              as cases(case_id, source_state, target_state, child_states)
            order by case_id;
            """
        ),
    )
    parent_actual = {
        int(case_id): allowed == "t"
        for case_id, _source, _target, allowed in (
            line.split("|", maxsplit=3) for line in parent_rows.splitlines()
        )
    }
    for case_id, current, target, child_states in parent_cases:
        assert parent_actual[case_id] is parent_operation_transition_allowed(
            current,
            target,
            child_states=child_states,
        )

    parent_expected = (
        (
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.DISPATCHING,
            (OperationItemState.AWAITING_CONFIRMATION,),
            True,
        ),
        (
            ParentOperationState.DISPATCHING,
            ParentOperationState.PROVIDER_REJECTED,
            (OperationItemState.PROVIDER_REJECTED, OperationItemState.NOT_DISPATCHED),
            True,
        ),
        (
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.FAILED_PRE_DISPATCH,
            (OperationItemState.FAILED_PRE_DISPATCH, OperationItemState.NOT_DISPATCHED),
            True,
        ),
        (
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.CANCELLED,
            (OperationItemState.CANCELLED,),
            True,
        ),
        (
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.EXPIRED,
            (OperationItemState.EXPIRED,),
            True,
        ),
        (
            ParentOperationState.OUTCOME_UNKNOWN,
            ParentOperationState.SUCCEEDED,
            (OperationItemState.SUCCEEDED,),
            True,
        ),
        (
            ParentOperationState.OUTCOME_UNKNOWN,
            ParentOperationState.NEEDS_MANUAL_REVIEW,
            (OperationItemState.NEEDS_MANUAL_REVIEW,),
            True,
        ),
        (
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.CANCELLED,
            (OperationItemState.AWAITING_CONFIRMATION,),
            False,
        ),
    )
    for current, target, child_states, expected in parent_expected:
        case_id = next(
            case_id
            for case_id, case_current, case_target, case_children in parent_cases
            if (case_current, case_target, case_children) == (current, target, child_states)
        )
        assert parent_actual[case_id] is expected

    item_cases = tuple(
        (case_id, current, target, parent)
        for case_id, (current, target, parent) in enumerate(
            (current, target, parent)
            for parent in ParentOperationState
            for current in OperationItemState
            for target in OperationItemState
        )
    )
    item_values = ", ".join(
        f"({case_id}, '{current.value}', '{target.value}', '{parent.value}')"
        for case_id, current, target, parent in item_cases
    )
    item_rows = _psql(
        postgres_context,
        _service_role(
            f"""
            select case_id, current_state, target_state, parent_state,
              public.mercury_item_operation_transition_is_allowed(
                current_state, target_state, parent_state
              )
            from (values {item_values})
              as cases(case_id, current_state, target_state, parent_state)
            order by case_id;
            """
        ),
    )
    item_actual = {
        int(case_id): allowed == "t"
        for case_id, _current, _target, _parent, allowed in (
            line.split("|", maxsplit=4) for line in item_rows.splitlines()
        )
    }
    for case_id, current, target, parent in item_cases:
        assert item_actual[case_id] is item_operation_transition_allowed(
            current,
            target,
            parent_state=parent,
        )

    item_expected = (
        (
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.DISPATCHING,
            ParentOperationState.DISPATCHING,
        ),
        (
            OperationItemState.DISPATCHING,
            OperationItemState.PROVIDER_REJECTED,
            ParentOperationState.DISPATCHING,
        ),
        (
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.FAILED_PRE_DISPATCH,
            ParentOperationState.AWAITING_CONFIRMATION,
        ),
        (
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.NOT_DISPATCHED,
            ParentOperationState.AWAITING_CONFIRMATION,
        ),
        (
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.CANCELLED,
            ParentOperationState.AWAITING_CONFIRMATION,
        ),
        (
            OperationItemState.AWAITING_CONFIRMATION,
            OperationItemState.EXPIRED,
            ParentOperationState.AWAITING_CONFIRMATION,
        ),
        (
            OperationItemState.OUTCOME_UNKNOWN,
            OperationItemState.SUCCEEDED,
            ParentOperationState.OUTCOME_UNKNOWN,
        ),
        (
            OperationItemState.OUTCOME_UNKNOWN,
            OperationItemState.NEEDS_MANUAL_REVIEW,
            ParentOperationState.OUTCOME_UNKNOWN,
        ),
    )
    for current, target, parent in item_expected:
        case_id = next(
            case_id
            for case_id, case_current, case_target, case_parent in item_cases
            if (case_current, case_target, case_parent) == (current, target, parent)
        )
        assert item_actual[case_id] is True

    assert item_operation_transition_allowed(
        OperationItemState.OUTCOME_UNKNOWN,
        OperationItemState.SUCCEEDED,
        parent_state=ParentOperationState.OUTCOME_UNKNOWN,
    )
    assert not parent_operation_transition_allowed(
        ParentOperationState.AWAITING_CONFIRMATION,
        ParentOperationState.CANCELLED,
        child_states=(OperationItemState.AWAITING_CONFIRMATION,),
    )


def test_postgresql_preview_transition_supports_prepared_awaiting_lifecycle(
    postgres_context: PostgresContext,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    preview, connection, qualification = asyncio.run(
        _build_preview(
            now,
            ids=(uuid4(), uuid4(), uuid4()),
            reference_prefix=f"INV-LIFECYCLE-{uuid4().hex}",
        )
    )
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    _psql(postgres_context, _service_role(_save_preview_sql(preview)))
    _psql(
        postgres_context,
        _service_role(
            f"""
            update public.mercury_document_previews
            set status = 'prepared'
            where id = '{preview.preview_id}';
            """
        ),
    )

    awaiting = json.loads(
        _psql(
            postgres_context,
            _service_role(
                f"""
                select pg_catalog.row_to_json(changed)::pg_catalog.text
                from public.transition_mercury_document_preview(
                  '{TENANT_ID}', '{WORKSPACE_ID}', '{AUTH_USER_ID}', '{preview.preview_id}',
                  1, 'awaiting_confirmation', '{(now + timedelta(seconds=1)).isoformat()}'
                ) as changed;
                """
            ),
        )
    )
    cancelled = json.loads(
        _psql(
            postgres_context,
            _service_role(
                f"""
                select pg_catalog.row_to_json(changed)::pg_catalog.text
                from public.transition_mercury_document_preview(
                  '{TENANT_ID}', '{WORKSPACE_ID}', '{AUTH_USER_ID}', '{preview.preview_id}',
                  2, 'cancelled', '{(now + timedelta(seconds=2)).isoformat()}'
                ) as changed;
                """
            ),
        )
    )

    assert awaiting["preview"]["status"] == "awaiting_confirmation"
    assert awaiting["preview"]["state_version"] == 2
    assert cancelled["preview"]["status"] == "cancelled"
    assert cancelled["preview"]["state_version"] == 3


@pytest.mark.parametrize(
    ("item_target", "parent_target", "provider_result_identifier"),
    (
        ("succeeded", "succeeded", RAW_PROVIDER_RESULT_IDENTIFIER),
        ("needs_manual_review", "needs_manual_review", None),
    ),
)
def test_postgresql_unknown_outcome_recovery_is_reachable(
    postgres_context: PostgresContext,
    item_target: str,
    parent_target: str,
    provider_result_identifier: str | None,
) -> None:
    from mercury_tools.execution.hosted.models import HostedOperation

    now = datetime.now(UTC).replace(microsecond=0)
    preview, connection, qualification = asyncio.run(
        _build_preview(
            now,
            document_count=1,
            ids=(uuid4(), uuid4()),
            reference_prefix=f"INV-RECOVERY-{uuid4().hex}",
        )
    )
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    _psql(postgres_context, _service_role(_save_preview_sql(preview)))
    operation = HostedOperation.from_preview(
        preview,
        operation_id=uuid4(),
        operation_item_ids=(uuid4(),),
        event_id=uuid4(),
        now=now + timedelta(seconds=1),
    )
    _psql(postgres_context, _service_role(_save_operation_sql(operation)))
    _psql(
        postgres_context,
        _service_role(
            _parent_transition_sql(
                operation.operation_id,
                expected_version=1,
                target="dispatching",
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=2),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                operation.items[0].operation_item_id,
                expected_version=1,
                target="dispatching",
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=3),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                operation.items[0].operation_item_id,
                expected_version=2,
                target="outcome_unknown",
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=4),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _parent_transition_sql(
                operation.operation_id,
                expected_version=2,
                target="outcome_unknown",
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=5),
            )
        ),
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                operation.items[0].operation_item_id,
                expected_version=3,
                target=item_target,
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=6),
                provider_result_identifier=provider_result_identifier,
            )
        ),
    )
    recovered = json.loads(
        _psql(
            postgres_context,
            _service_role(
                _parent_transition_sql(
                    operation.operation_id,
                    expected_version=3,
                    target=parent_target,
                    event_id=uuid4(),
                    occurred_at=now + timedelta(seconds=7),
                )
            ),
        )
    )

    assert recovered["operation"]["status"] == parent_target
    assert recovered["items"][0]["status"] == item_target
    serialized = json.dumps(recovered, sort_keys=True)
    if provider_result_identifier is not None:
        for raw in (
            "jane@example.com",
            "1234567890123",
            "081-234-5678",
            "sk-qrstuvwxyz123456",
            "provider-secret-value",
            "provider-api-secret",
        ):
            assert raw not in serialized
        assert "[REDACTED_" in serialized


def test_postgresql_operation_text_sanitization_matches_python(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.execution.hosted.sanitization import sanitize_public_text

    raw_values = (
        RAW_PROVIDER_RESULT_IDENTIFIER,
        "+66 81 234 5678",
        "02-123-4567",
        "Authorization: Bearer provider-secret",
        "Cookie: session=provider-secret",
        "client_secret=abc123",
        "gho_abcdefghijklmnop",
        "https://example.com/path?api_key=provider-secret",
    )
    values = ", ".join(
        f"({case_id}, $mercury_text_{case_id}${value}$mercury_text_{case_id}$)"
        for case_id, value in enumerate(raw_values)
    )
    rows = _psql(
        postgres_context,
        _service_role(
            f"""
            select case_id, public.mercury_public_text(value)
            from (values {values}) as cases(case_id, value)
            order by case_id;
            """
        ),
    )
    actual = tuple(line.split("|", maxsplit=1)[1] for line in rows.splitlines())
    expected = tuple(
        sanitize_public_text(value, code="operation_transition_invalid") for value in raw_values
    )
    reason_is_safe = _psql(
        postgres_context,
        _service_role("select public.mercury_public_identifier_is_safe('sk-qrstuvwxyz123456');"),
    )

    assert actual == expected
    assert reason_is_safe == "f"


def test_postgresql_schema_policy_is_total_and_normalizes_authority_errors(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.catalog.models import ProviderMCPQualification
    from mercury_tools.execution.hosted.models import HostedOperation

    now = datetime.now(UTC).replace(microsecond=0)
    preview, connection, qualification = asyncio.run(
        _build_preview(
            now,
            document_count=1,
            ids=(uuid4(), uuid4()),
            reference_prefix=f"INV-SCHEMA-{uuid4().hex}",
        )
    )
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    _psql(postgres_context, _service_role(_save_preview_sql(preview)))

    cases: list[dict[str, object]] = []
    for keyword, value in (
        ("maxItems", 1.5),
        ("maxItems", "100"),
        ("maxItems", -1),
        ("maxItems", True),
        ("maxItems", 2_147_483_648),
        ("minItems", 1.5),
        ("minItems", "1"),
        ("minItems", -1),
        ("minItems", True),
        ("minItems", 2_147_483_648),
    ):
        schema = json.loads(json.dumps(qualification.input_schema))
        schema["properties"]["lines"][keyword] = value
        cases.append(schema)
    overflow_length = json.loads(json.dumps(qualification.input_schema))
    overflow_length["properties"]["reference"]["minLength"] = 2_147_483_648
    cases.append(overflow_length)

    values = ", ".join(f"({case_id}, {_jsonb(schema)})" for case_id, schema in enumerate(cases))
    rows = _psql(
        postgres_context,
        _service_role(
            f"""
            select case_id, public.mercury_create_schema_is_closed(schema, true)
            from (values {values}) as cases(case_id, schema)
            order by case_id;
            """
        ),
    )
    assert rows.splitlines() == [f"{case_id}|f" for case_id in range(len(cases))]

    invalid_definition = ProviderMCPQualification.discovered(
        provider=qualification.provider,
        environment=qualification.environment,
        provider_tool_name=f"create_invalid_{uuid4().hex}",
        normalized_capability=qualification.normalized_capability,
        input_schema=cases[0],
        output_schema=qualification.output_schema,
        response_shape_hash=qualification.response_shape_hash,
        required_permissions=qualification.required_permissions,
    )
    publication_payload = invalid_definition.model_dump(mode="json")
    publication_payload.pop("public_output_field_paths", None)
    publication_payload["capability_version_sha256"] = _psql(
        postgres_context,
        f"""
        with payload as (select {_jsonb(publication_payload)} as value)
        select pg_catalog.encode(public.digest(
          pg_catalog.convert_to(public.mercury_canonical_jsonb(pg_catalog.jsonb_build_object(
            'provider', value->>'provider',
            'environment', value->>'environment',
            'provider_tool_name', value->>'provider_tool_name',
            'normalized_capability', value->>'normalized_capability',
            'input_schema', value->'input_schema',
            'output_schema', value->'output_schema',
            'schema_hash', value->>'schema_hash',
            'response_shape_hash', value->>'response_shape_hash',
            'required_permissions', value->'required_permissions'
          )), 'UTF8'), 'sha256'
        ), 'hex')
        from payload;
        """,
    )
    publication = _psql_result(
        postgres_context,
        _service_role(
            "select public.publish_mercury_provider_capability_qualification("
            f"{_jsonb(publication_payload)}, null);"
        ),
    )
    assert publication.returncode != 0
    assert "capability_unavailable" in publication.stderr
    assert "invalid input syntax" not in publication.stderr

    operation = HostedOperation.from_preview(
        preview,
        operation_id=uuid4(),
        operation_item_ids=(uuid4(),),
        event_id=uuid4(),
        now=now + timedelta(seconds=1),
    )
    authority = _psql_result(
        postgres_context,
        f"""
        begin;
        alter table public.mercury_provider_capability_qualifications
          disable trigger mercury_reject_open_create_qualification_trigger;
        update public.mercury_provider_capability_qualifications
        set input_schema = {_jsonb(cases[0])}
        where id = '{qualification.id}';
        set role service_role;
        {_save_operation_sql(operation)}
        rollback;
        """,
    )
    assert authority.returncode != 0
    assert "capability_unavailable" in authority.stderr
    assert "invalid input syntax" not in authority.stderr


@pytest.mark.parametrize(
    ("parent_target", "item_target"),
    (("cancelled", "cancelled"), ("expired", "expired")),
)
def test_postgresql_terminal_parent_requires_closed_children(
    postgres_context: PostgresContext,
    parent_target: str,
    item_target: str,
) -> None:
    from mercury_tools.execution.hosted.models import HostedOperation

    now = datetime.now(UTC).replace(microsecond=0)
    preview, connection, qualification = asyncio.run(
        _build_preview(
            now,
            document_count=1,
            ids=(uuid4(), uuid4()),
            reference_prefix=f"INV-TERMINAL-{uuid4().hex}",
        )
    )
    _seed_authority(
        postgres_context,
        now=now,
        connection=connection,
        qualification=qualification,
    )
    _psql(postgres_context, _service_role(_save_preview_sql(preview)))
    operation = HostedOperation.from_preview(
        preview,
        operation_id=uuid4(),
        operation_item_ids=(uuid4(),),
        event_id=uuid4(),
        now=now + timedelta(seconds=1),
    )
    _psql(postgres_context, _service_role(_save_operation_sql(operation)))
    _expect_error(
        postgres_context,
        _parent_transition_sql(
            operation.operation_id,
            expected_version=1,
            target=parent_target,
            event_id=uuid4(),
            occurred_at=now + timedelta(seconds=2),
        ),
        "operation_transition_invalid",
    )
    _psql(
        postgres_context,
        _service_role(
            _item_transition_sql(
                operation.operation_id,
                operation.items[0].operation_item_id,
                expected_version=1,
                target=item_target,
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=3),
            )
        ),
    )
    closed = json.loads(
        _psql(
            postgres_context,
            _service_role(
                _parent_transition_sql(
                    operation.operation_id,
                    expected_version=1,
                    target=parent_target,
                    event_id=uuid4(),
                    occurred_at=now + timedelta(seconds=4),
                )
            ),
        )
    )

    assert closed["operation"]["status"] == parent_target
    assert closed["items"][0]["status"] == item_target


def test_postgresql_terminal_aggregate_ordering_covers_predispatch_and_rejection(
    postgres_context: PostgresContext,
) -> None:
    from mercury_tools.execution.hosted.models import HostedOperation

    for scenario in ("failed_pre_dispatch", "provider_rejected"):
        now = datetime.now(UTC).replace(microsecond=0)
        preview, connection, qualification = asyncio.run(
            _build_preview(
                now,
                ids=(uuid4(), uuid4(), uuid4()),
                reference_prefix=f"INV-{scenario.upper()}-{uuid4().hex}",
            )
        )
        _seed_authority(
            postgres_context,
            now=now,
            connection=connection,
            qualification=qualification,
        )
        _psql(postgres_context, _service_role(_save_preview_sql(preview)))
        operation = HostedOperation.from_preview(
            preview,
            operation_id=uuid4(),
            operation_item_ids=(uuid4(), uuid4()),
            event_id=uuid4(),
            now=now + timedelta(seconds=1),
        )
        _psql(postgres_context, _service_role(_save_operation_sql(operation)))

        parent_version = 1
        first_item_version = 1
        if scenario == "provider_rejected":
            _psql(
                postgres_context,
                _service_role(
                    _parent_transition_sql(
                        operation.operation_id,
                        expected_version=parent_version,
                        target="dispatching",
                        event_id=uuid4(),
                        occurred_at=now + timedelta(seconds=2),
                    )
                ),
            )
            parent_version += 1
            _psql(
                postgres_context,
                _service_role(
                    _item_transition_sql(
                        operation.operation_id,
                        operation.items[0].operation_item_id,
                        expected_version=first_item_version,
                        target="dispatching",
                        event_id=uuid4(),
                        occurred_at=now + timedelta(seconds=3),
                    )
                ),
            )
            first_item_version += 1

        _expect_error(
            postgres_context,
            _parent_transition_sql(
                operation.operation_id,
                expected_version=parent_version,
                target=scenario,
                event_id=uuid4(),
                occurred_at=now + timedelta(seconds=4),
            ),
            "operation_transition_invalid",
        )
        _psql(
            postgres_context,
            _service_role(
                _item_transition_sql(
                    operation.operation_id,
                    operation.items[0].operation_item_id,
                    expected_version=first_item_version,
                    target=scenario,
                    event_id=uuid4(),
                    occurred_at=now + timedelta(seconds=5),
                )
            ),
        )
        _psql(
            postgres_context,
            _service_role(
                _item_transition_sql(
                    operation.operation_id,
                    operation.items[1].operation_item_id,
                    expected_version=1,
                    target="not_dispatched",
                    event_id=uuid4(),
                    occurred_at=now + timedelta(seconds=6),
                )
            ),
        )
        closed = json.loads(
            _psql(
                postgres_context,
                _service_role(
                    _parent_transition_sql(
                        operation.operation_id,
                        expected_version=parent_version,
                        target=scenario,
                        event_id=uuid4(),
                        occurred_at=now + timedelta(seconds=7),
                    )
                ),
            )
        )

        assert closed["operation"]["status"] == scenario
        assert [item["status"] for item in closed["items"]] == [
            scenario,
            "not_dispatched",
        ]
