from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
PEAK_MIGRATION = ROOT / "supabase/migrations/20260729100000_mercury_v1_peak_secure_setup.sql"
MIGRATIONS = (
    ROOT / "supabase/migrations/0002_mercury_product_layer.sql",
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql",
    ROOT / "supabase/migrations/20260726101000_mercury_v1_provider_connections.sql",
    ROOT / "supabase/migrations/20260726102000_mercury_v1_credential_vault.sql",
    ROOT / "supabase/migrations/20260727100000_mercury_v1_provider_oauth_cleanup.sql",
    ROOT / "supabase/migrations/20260728100000_mercury_v1_provider_oauth_reconnect.sql",
    ROOT / "supabase/migrations/20260728110000_mercury_v1_provider_oauth_attempts.sql",
    ROOT / "supabase/migrations/20260728120000_mercury_v1_provider_oauth_generations.sql",
    PEAK_MIGRATION,
)
AUTH_USER_ID = UUID("33333333-3333-4333-8333-333333333333")
OTHER_AUTH_USER_ID = UUID("44444444-4444-4444-8444-444444444444")
_OPT_IN = "MERCURY_V1_POSTGRES_TEST"


@dataclass(frozen=True)
class PostgresContext:
    container: str
    tenant_id: UUID
    workspace_id: UUID


def _docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _docker(
    *args: str,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=True,
        text=True,
        input=input_text,
    )


def _psql_result(container: str, sql: str) -> subprocess.CompletedProcess[str]:
    return _docker(
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
        "mercury_task7_test",
        input_text=sql,
        check=False,
    )


def _psql(container: str, sql: str) -> str:
    result = _psql_result(container, sql)
    if result.returncode != 0:
        raise AssertionError(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _wait_for_postgres(container: str) -> None:
    for _ in range(120):
        ready = _docker(
            "exec",
            container,
            "psql",
            "-qAt",
            "-U",
            "postgres",
            "-d",
            "mercury_task7_test",
            "-c",
            "select 1",
            check=False,
        )
        if ready.returncode == 0 and ready.stdout.strip() == "1":
            return
        time.sleep(0.25)
    pytest.fail("disposable PostgreSQL did not become ready")


def _authenticated(sql: str, *, auth_user_id: UUID = AUTH_USER_ID) -> str:
    return f"set role authenticated;\nset request.jwt.claim.sub = '{auth_user_id}';\n{sql}"


def _service(sql: str) -> str:
    return f"set role service_role;\n{sql}"


@pytest.fixture(scope="module")
def postgres_context() -> PostgresContext:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task7-postgres-{uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task7_test",
        "postgres:17-alpine",
    )
    try:
        _wait_for_postgres(container)
        _psql(
            container,
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
              select nullif(
                current_setting('request.jwt.claim.sub', true),
                ''
              )::uuid
            $$;
            grant usage on schema auth to anon, authenticated, service_role;
            grant execute on function auth.uid()
              to anon, authenticated, service_role;
            """,
        )
        for _ in range(2):
            for migration in MIGRATIONS:
                _psql(container, migration.read_text(encoding="utf-8"))
        payload = json.loads(
            _psql(
                container,
                _authenticated(
                    """
                    select pg_catalog.row_to_json(context)::pg_catalog.text
                    from public.bootstrap_mercury_context() as context;
                    """
                ),
            )
        )
        yield PostgresContext(
            container=container,
            tenant_id=UUID(payload["memberships"][0]["tenant_id"]),
            workspace_id=UUID(payload["active_workspace_id"]),
        )
    finally:
        _docker("rm", "-f", container, check=False)


def _create_attempt(
    context: PostgresContext,
    *,
    attempt_id: UUID,
    token_hash: str,
    expires: str = "5 minutes",
) -> str:
    return f"""
        select attempt_id
        from public.create_mercury_provider_setup_attempt(
          '{attempt_id}',
          '{context.tenant_id}',
          '{context.workspace_id}',
          '{AUTH_USER_ID}',
          'peak',
          'production',
          '{token_hash}',
          pg_catalog.statement_timestamp() + interval '{expires}'
        );
    """


def _exchange(
    context: PostgresContext,
    *,
    session_id: UUID,
    token_hash: str,
    session_hash: str,
    csrf_hash: str,
    auth_user_id: UUID = AUTH_USER_ID,
) -> str:
    return f"""
        select pg_catalog.row_to_json(exchanged)::pg_catalog.text
        from public.exchange_mercury_peak_setup_attempt(
          '{session_id}',
          '{auth_user_id}',
          '{token_hash}',
          '{session_hash}',
          '{csrf_hash}'
        ) as exchanged;
    """


def _envelopes(*, invalid: bool = False) -> str:
    types = ("user_token", "connect_id", "connect_key")
    items = []
    for index, credential_type in enumerate(types, start=1):
        ciphertext = "zz" if invalid and index == 1 else "cd" * 16
        items.append(
            f"""
            pg_catalog.jsonb_build_object(
              'id', '{uuid4()}',
              'credential_type', '{credential_type}',
              'key_version', 'v1',
              'nonce', pg_catalog.repeat('{index:02x}', 12),
              'ciphertext', '{ciphertext}',
              'aad_hash', pg_catalog.repeat('ef', 32),
              'created_at', pg_catalog.statement_timestamp(),
              'rotated_at', null,
              'revoked_at', null
            )
            """
        )
    return f"pg_catalog.jsonb_build_array({','.join(items)})"


def _finalize(
    context: PostgresContext,
    *,
    session_hash: str,
    csrf_hash: str,
    connection_id: UUID,
    invalid_envelope: bool = False,
    workspace_id: UUID | None = None,
    auth_user_id: UUID = AUTH_USER_ID,
    environment: str = "production",
) -> str:
    return f"""
        select pg_catalog.row_to_json(finalized)::pg_catalog.text
        from public.finalize_mercury_peak_setup(
          '{context.tenant_id}',
          '{workspace_id or context.workspace_id}',
          '{auth_user_id}',
          '{session_hash}',
          '{csrf_hash}',
          '{connection_id}',
          'peak',
          '{environment}',
          'merchant-{connection_id}',
          'PEAK Test Merchant',
          '["profile.read"]'::pg_catalog.jsonb,
          1,
          pg_catalog.statement_timestamp(),
          {_envelopes(invalid=invalid_envelope)}
        ) as finalized;
    """


def _prepare_session(
    context: PostgresContext,
) -> tuple[UUID, str, str, str]:
    attempt_id = uuid4()
    token_hash = uuid4().hex * 2
    session_hash = uuid4().hex * 2
    csrf_hash = uuid4().hex * 2
    _psql(
        context.container,
        _authenticated(
            _create_attempt(
                context,
                attempt_id=attempt_id,
                token_hash=token_hash,
            )
        ),
    )
    _psql(
        context.container,
        _authenticated(
            _exchange(
                context,
                session_id=uuid4(),
                token_hash=token_hash,
                session_hash=session_hash,
                csrf_hash=csrf_hash,
            )
        ),
    )
    return attempt_id, token_hash, session_hash, csrf_hash


def _assert_safe_error(
    result: subprocess.CompletedProcess[str],
    code: str,
    *sentinels: str,
) -> None:
    rendered = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert code in rendered
    assert all(sentinel not in rendered for sentinel in sentinels)


def test_peak_migration_applies_twice_on_postgresql_17(
    postgres_context: PostgresContext,
) -> None:
    version = _psql(
        postgres_context.container,
        "select current_setting('server_version_num');",
    )
    assert version.startswith("17")


def test_peak_setup_schema_is_hash_only_and_public_surfaces_are_closed(
    postgres_context: PostgresContext,
) -> None:
    permissions = json.loads(
        _psql(
            postgres_context.container,
            """
            select pg_catalog.json_build_object(
              'columns', (
                select pg_catalog.json_agg(column_name order by ordinal_position)
                from information_schema.columns
                where table_schema = 'public'
                  and table_name = 'mercury_peak_setup_sessions'
              ),
              'authenticated_table_select',
                pg_catalog.has_table_privilege(
                  'authenticated',
                  'public.mercury_peak_setup_sessions',
                  'select'
                ),
              'anon_exchange',
                pg_catalog.has_function_privilege(
                  'anon',
                  'public.exchange_mercury_peak_setup_attempt(uuid,uuid,text,text,text)',
                  'execute'
                ),
              'authenticated_exchange',
                pg_catalog.has_function_privilege(
                  'authenticated',
                  'public.exchange_mercury_peak_setup_attempt(uuid,uuid,text,text,text)',
                  'execute'
                ),
              'authenticated_peek',
                pg_catalog.has_function_privilege(
                  'authenticated',
                  'public.peek_mercury_peak_setup_session(uuid,text)',
                  'execute'
                ),
              'authenticated_finalize',
                pg_catalog.has_function_privilege(
                  'authenticated',
                  'public.finalize_mercury_peak_setup('
                    'uuid,uuid,uuid,text,text,uuid,text,text,text,text,jsonb,'
                    'bigint,timestamp with time zone,jsonb'
                  ')',
                  'execute'
                ),
              'service_peek',
                pg_catalog.has_function_privilege(
                  'service_role',
                  'public.peek_mercury_peak_setup_session(uuid,text)',
                  'execute'
                ),
              'service_finalize',
                pg_catalog.has_function_privilege(
                  'service_role',
                  'public.finalize_mercury_peak_setup('
                    'uuid,uuid,uuid,text,text,uuid,text,text,text,text,jsonb,'
                    'bigint,timestamp with time zone,jsonb'
                  ')',
                  'execute'
                )
            )::pg_catalog.text;
            """,
        )
    )

    assert permissions == {
        "columns": [
            "id",
            "setup_attempt_id",
            "tenant_id",
            "workspace_id",
            "auth_user_id",
            "provider",
            "environment",
            "session_hash",
            "csrf_hash",
            "expires_at",
            "consumed_at",
            "created_at",
        ],
        "authenticated_table_select": False,
        "anon_exchange": False,
        "authenticated_exchange": True,
        "authenticated_peek": False,
        "authenticated_finalize": False,
        "service_peek": True,
        "service_finalize": True,
    }


def test_peak_finalizer_is_one_winner_and_replay_safe(
    postgres_context: PostgresContext,
) -> None:
    attempt_id, _token_hash, session_hash, csrf_hash = _prepare_session(postgres_context)
    connection_id = uuid4()
    sql = _service(
        _finalize(
            postgres_context,
            session_hash=session_hash,
            csrf_hash=csrf_hash,
            connection_id=connection_id,
        )
    )
    barrier = threading.Barrier(2)

    def invoke() -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return _psql_result(postgres_context.container, sql)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: invoke(), range(2)))

    successes = [item for item in results if item.returncode == 0]
    failures = [item for item in results if item.returncode != 0]
    assert len(successes) == len(failures) == 1
    _assert_safe_error(failures[0], "peak_setup_state_invalid")
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'attempt_consumed', attempt.consumed_at is not null,
                  'session_consumed', session.consumed_at is not null,
                  'connections', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_connections
                    where id = '{connection_id}'
                  ),
                  'envelopes', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_credential_envelopes
                    where connection_id = '{connection_id}'
                  )
                )::pg_catalog.text
                from public.mercury_provider_setup_attempts as attempt
                join public.mercury_peak_setup_sessions as session
                  on session.setup_attempt_id = attempt.id
                where attempt.id = '{attempt_id}';
                """
            ),
        )
    )
    assert persisted == {
        "attempt_consumed": True,
        "session_consumed": True,
        "connections": 1,
        "envelopes": 3,
    }


def test_peak_finalizer_rolls_back_consumption_and_connection_on_failure(
    postgres_context: PostgresContext,
) -> None:
    attempt_id, _token_hash, session_hash, csrf_hash = _prepare_session(postgres_context)
    connection_id = uuid4()

    result = _psql_result(
        postgres_context.container,
        _service(
            _finalize(
                postgres_context,
                session_hash=session_hash,
                csrf_hash=csrf_hash,
                connection_id=connection_id,
                invalid_envelope=True,
            )
        ),
    )

    _assert_safe_error(result, "provider_credential_envelope_invalid", "zz")
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'attempt_consumed', attempt.consumed_at is not null,
                  'session_consumed', session.consumed_at is not null,
                  'connections', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_connections
                    where id = '{connection_id}'
                  )
                )::pg_catalog.text
                from public.mercury_provider_setup_attempts as attempt
                join public.mercury_peak_setup_sessions as session
                  on session.setup_attempt_id = attempt.id
                where attempt.id = '{attempt_id}';
                """
            ),
        )
    )
    assert persisted == {
        "attempt_consumed": False,
        "session_consumed": False,
        "connections": 0,
    }


@pytest.mark.parametrize(
    "mutation",
    ("wrong_user", "wrong_workspace", "wrong_environment", "wrong_csrf"),
)
def test_peak_finalizer_isolation_failures_do_not_consume(
    postgres_context: PostgresContext,
    mutation: str,
) -> None:
    attempt_id, _token_hash, session_hash, csrf_hash = _prepare_session(postgres_context)
    kwargs = {
        "session_hash": session_hash,
        "csrf_hash": csrf_hash,
        "connection_id": uuid4(),
    }
    if mutation == "wrong_user":
        kwargs["auth_user_id"] = OTHER_AUTH_USER_ID
    elif mutation == "wrong_workspace":
        kwargs["workspace_id"] = uuid4()
    elif mutation == "wrong_environment":
        kwargs["environment"] = "uat"
    else:
        kwargs["csrf_hash"] = uuid4().hex * 2

    result = _psql_result(
        postgres_context.container,
        _service(_finalize(postgres_context, **kwargs)),
    )

    _assert_safe_error(result, "peak_setup_state_invalid")
    assert (
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'attempt_consumed', attempt.consumed_at is not null,
                  'session_consumed', session.consumed_at is not null
                )::pg_catalog.text
                from public.mercury_provider_setup_attempts as attempt
                join public.mercury_peak_setup_sessions as session
                  on session.setup_attempt_id = attempt.id
                where attempt.id = '{attempt_id}';
                """
            ),
        )
        == '{"attempt_consumed" : false, "session_consumed" : false}'
    )


def test_expired_peak_session_and_exchange_replay_fail_closed(
    postgres_context: PostgresContext,
) -> None:
    attempt_id, token_hash, session_hash, csrf_hash = _prepare_session(postgres_context)
    _psql(
        postgres_context.container,
        _service(
            f"""
            update public.mercury_provider_setup_attempts
            set created_at = pg_catalog.statement_timestamp() - interval '10 minutes',
                expires_at = pg_catalog.statement_timestamp() - interval '1 second'
            where id = '{attempt_id}';
            update public.mercury_peak_setup_sessions
            set created_at = pg_catalog.statement_timestamp() - interval '10 minutes',
                expires_at = pg_catalog.statement_timestamp() - interval '1 second'
            where setup_attempt_id = '{attempt_id}';
            """
        ),
    )

    expired = _psql_result(
        postgres_context.container,
        _service(
            _finalize(
                postgres_context,
                session_hash=session_hash,
                csrf_hash=csrf_hash,
                connection_id=uuid4(),
            )
        ),
    )
    replay = _psql_result(
        postgres_context.container,
        _authenticated(
            _exchange(
                postgres_context,
                session_id=uuid4(),
                token_hash=token_hash,
                session_hash=uuid4().hex * 2,
                csrf_hash=uuid4().hex * 2,
            )
        ),
    )

    _assert_safe_error(expired, "peak_setup_state_invalid")
    _assert_safe_error(replay, "peak_setup_state_invalid")
