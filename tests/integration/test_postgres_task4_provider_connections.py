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
MIGRATIONS = (
    ROOT / "supabase/migrations/0002_mercury_product_layer.sql",
    ROOT / "supabase/migrations/20260726100000_mercury_v1_identity.sql",
    ROOT / "supabase/migrations/20260726101000_mercury_v1_provider_connections.sql",
    ROOT / "supabase/migrations/20260726102000_mercury_v1_credential_vault.sql",
    ROOT / "supabase/migrations/20260727100000_mercury_v1_provider_oauth_cleanup.sql",
    ROOT / "supabase/migrations/20260728100000_mercury_v1_provider_oauth_reconnect.sql",
    ROOT / "supabase/migrations/20260728110000_mercury_v1_provider_oauth_attempts.sql",
    ROOT / "supabase/migrations/20260728120000_mercury_v1_provider_oauth_generations.sql",
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
        "mercury_task4_test",
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
            "mercury_task4_test",
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

    container = f"mercury-task4-postgres-{uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task4_test",
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


def _save_sql(
    context: PostgresContext,
    *,
    connection_id: UUID,
    envelope_id: UUID,
    account_id: str,
    revision: int = 1,
    permissions: str = '["documents.read","profile.read"]',
    auth_user_id: UUID = AUTH_USER_ID,
) -> str:
    return f"""
        select pg_catalog.row_to_json(saved)::pg_catalog.text
        from public.save_mercury_provider_connection(
          '{connection_id}',
          '{context.tenant_id}',
          '{context.workspace_id}',
          '{auth_user_id}',
          'flowaccount',
          'sandbox',
          '{account_id}',
          'Mercury Test Company',
          'oauth2_pkce',
          '{permissions}'::pg_catalog.jsonb,
          'ready',
          {revision},
          pg_catalog.statement_timestamp(),
          pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object(
              'id', '{envelope_id}',
              'credential_type', 'access_token',
              'key_version', 'v1',
              'nonce', pg_catalog.repeat('ab', 12),
              'ciphertext', pg_catalog.repeat('cd', 16),
              'aad_hash', pg_catalog.repeat('ef', 32),
              'created_at', pg_catalog.statement_timestamp(),
              'rotated_at', null,
              'revoked_at', null
            )
          )
        ) as saved;
    """


def _create_setup_sql(
    context: PostgresContext,
    *,
    attempt_id: UUID,
    token_hash: str,
) -> str:
    return f"""
        select attempt_id
        from public.create_mercury_provider_setup_attempt(
          '{attempt_id}',
          '{context.tenant_id}',
          '{context.workspace_id}',
          '{AUTH_USER_ID}',
          'flowaccount',
          'sandbox',
          '{token_hash}',
          pg_catalog.statement_timestamp() + interval '5 minutes'
        );
    """


def _create_oauth_sql(
    context: PostgresContext,
    *,
    state_id: UUID,
    attempt_id: UUID,
    state_hash: str,
    callback_state: str = '{"requested_permissions":["profile.read"]}',
    pkce_ciphertext_hex: str = "ab" * 16,
) -> str:
    return f"""
        select oauth_state_id
        from public.create_mercury_provider_oauth_state(
          '{state_id}',
          '{attempt_id}',
          '{context.tenant_id}',
          '{context.workspace_id}',
          '{AUTH_USER_ID}',
          'flowaccount',
          'sandbox',
          '{state_hash}',
          pg_catalog.decode('{pkce_ciphertext_hex}', 'hex'),
          'v1',
          pg_catalog.decode('{("cd" * 12)}', 'hex'),
          pg_catalog.decode('{("ef" * 32)}', 'hex'),
          '{callback_state}'::pg_catalog.jsonb,
          pg_catalog.statement_timestamp() + interval '5 minutes'
        );
    """


def _assert_secret_safe_error(
    result: subprocess.CompletedProcess[str],
    code: str,
    *sentinels: str,
) -> None:
    complete_error = f"{result.stdout}\n{result.stderr}"
    assert result.returncode != 0
    assert code in complete_error
    for sentinel in sentinels:
        assert sentinel not in complete_error


def test_task4_migrations_apply_twice_on_postgresql_17(
    postgres_context: PostgresContext,
) -> None:
    version = _psql(
        postgres_context.container,
        "select current_setting('server_version_num');",
    )

    assert version.startswith("17")


def test_disconnect_deletes_envelopes_and_increments_revision_once(
    postgres_context: PostgresContext,
) -> None:
    connection_id = uuid4()
    envelope_id = uuid4()
    account_id = f"disconnect-{connection_id}"
    _psql(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=connection_id,
                envelope_id=envelope_id,
                account_id=account_id,
            )
        ),
    )
    disconnect_sql = f"""
        select pg_catalog.row_to_json(disconnected)::pg_catalog.text
        from public.disconnect_mercury_provider_connection(
          '{postgres_context.tenant_id}',
          '{postgres_context.workspace_id}',
          '{AUTH_USER_ID}',
          '{connection_id}',
          true
        ) as disconnected;
    """

    first = json.loads(_psql(postgres_context.container, _service(disconnect_sql)))
    second = json.loads(_psql(postgres_context.container, _service(disconnect_sql)))
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'revision', connection.revision,
                  'readiness', connection.readiness,
                  'envelope_count', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_credential_envelopes as envelope
                    where envelope.connection_id = connection.id
                  )
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                where connection.id = '{connection_id}';
                """
            ),
        )
    )

    assert first["deleted_envelope_count"] == 1
    assert first["already_disconnected"] is False
    assert first["revision"] == 2
    assert second["deleted_envelope_count"] == 0
    assert second["already_disconnected"] is True
    assert second["revision"] == 2
    assert persisted == {
        "revision": 2,
        "readiness": "disconnected",
        "envelope_count": 0,
    }


def test_oauth_setup_attempt_is_claimed_once_sequentially_and_by_schema(
    postgres_context: PostgresContext,
) -> None:
    attempt_id = uuid4()
    state_id = uuid4()
    token_hash = uuid4().hex * 2
    _psql(
        postgres_context.container,
        _authenticated(
            _create_setup_sql(
                postgres_context,
                attempt_id=attempt_id,
                token_hash=token_hash,
            )
        ),
    )

    first = _psql_result(
        postgres_context.container,
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=state_id,
                attempt_id=attempt_id,
                state_hash=uuid4().hex * 2,
            )
        ),
    )
    replay = _psql_result(
        postgres_context.container,
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=uuid4(),
                attempt_id=attempt_id,
                state_hash=uuid4().hex * 2,
            )
        ),
    )
    schema_replay = _psql_result(
        postgres_context.container,
        _service(
            f"""
            insert into public.mercury_provider_oauth_states (
              id,
              setup_attempt_id,
              tenant_id,
              workspace_id,
              auth_user_id,
              provider,
              environment,
              state_hash,
              pkce_verifier_ciphertext,
              pkce_key_version,
              pkce_nonce,
              pkce_aad_hash,
              callback_state,
              expires_at,
              created_at
            )
            select
              '{uuid4()}',
              state.setup_attempt_id,
              state.tenant_id,
              state.workspace_id,
              state.auth_user_id,
              state.provider,
              state.environment,
              '{uuid4().hex * 2}',
              state.pkce_verifier_ciphertext,
              state.pkce_key_version,
              state.pkce_nonce,
              state.pkce_aad_hash,
              state.callback_state,
              state.expires_at,
              state.created_at
            from public.mercury_provider_oauth_states as state
            where state.id = '{state_id}';
            """
        ),
    )

    assert first.returncode == 0
    _assert_secret_safe_error(replay, "provider_oauth_state_invalid")
    assert schema_replay.returncode != 0
    assert "unique" in schema_replay.stderr.lower()
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'state_count', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_oauth_states
                    where setup_attempt_id = '{attempt_id}'
                  ),
                  'attempt_consumed', attempt.consumed_at is not null
                )::pg_catalog.text
                from public.mercury_provider_setup_attempts as attempt
                where attempt.id = '{attempt_id}';
                """
            ),
        )
    )
    assert persisted == {"state_count": 1, "attempt_consumed": True}


def test_oauth_setup_attempt_is_claimed_once_under_concurrent_replay(
    postgres_context: PostgresContext,
) -> None:
    attempt_id = uuid4()
    _psql(
        postgres_context.container,
        _authenticated(
            _create_setup_sql(
                postgres_context,
                attempt_id=attempt_id,
                token_hash=uuid4().hex * 2,
            )
        ),
    )
    barrier = threading.Barrier(2)
    calls = [
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=uuid4(),
                attempt_id=attempt_id,
                state_hash=uuid4().hex * 2,
            )
        )
        for _ in range(2)
    ]

    def invoke(sql: str) -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return _psql_result(postgres_context.container, sql)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, calls))

    successes = [result for result in results if result.returncode == 0]
    failures = [result for result in results if result.returncode != 0]
    assert len(successes) == 1
    assert len(failures) == 1
    _assert_secret_safe_error(failures[0], "provider_oauth_state_invalid")
    assert (
        _psql(
            postgres_context.container,
            _service(
                f"""
            select pg_catalog.count(*)
            from public.mercury_provider_oauth_states
            where setup_attempt_id = '{attempt_id}';
            """
            ),
        )
        == "1"
    )


def test_oauth_state_consume_and_cancel_are_atomic_under_concurrency(
    postgres_context: PostgresContext,
) -> None:
    attempt_id = uuid4()
    state_id = uuid4()
    state_hash = uuid4().hex * 2
    _psql(
        postgres_context.container,
        _authenticated(
            _create_setup_sql(
                postgres_context,
                attempt_id=attempt_id,
                token_hash=uuid4().hex * 2,
            )
        ),
    )
    _psql(
        postgres_context.container,
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=state_id,
                attempt_id=attempt_id,
                state_hash=state_hash,
            )
        ),
    )
    barrier = threading.Barrier(2)
    calls = (
        _authenticated(
            f"""
            select oauth_state_id
            from public.consume_mercury_provider_oauth_state(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              'flowaccount',
              'sandbox',
              '{state_hash}'
            );
            """
        ),
        _authenticated(
            f"""
            select oauth_state_id
            from public.cancel_mercury_provider_oauth_state(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              'flowaccount',
              'sandbox',
              '{state_hash}'
            );
            """
        ),
    )

    def invoke(sql: str) -> subprocess.CompletedProcess[str]:
        barrier.wait()
        return _psql_result(postgres_context.container, sql)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(invoke, calls))

    successes = [result for result in results if result.returncode == 0]
    failures = [result for result in results if result.returncode != 0]
    assert len(successes) == len(failures) == 1
    _assert_secret_safe_error(failures[0], "provider_oauth_state_invalid")
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'consumed', state.consumed_at is not null,
                  'ciphertext_cleared', state.pkce_verifier_ciphertext is null,
                  'key_version_cleared', state.pkce_key_version is null,
                  'nonce_cleared', state.pkce_nonce is null,
                  'aad_hash_cleared', state.pkce_aad_hash is null
                )::pg_catalog.text
                from public.mercury_provider_oauth_states as state
                where state.id = '{state_id}';
                """
            ),
        )
    )
    assert persisted == {
        "consumed": True,
        "ciphertext_cleared": True,
        "key_version_cleared": True,
        "nonce_cleared": True,
        "aad_hash_cleared": True,
    }


def test_expired_oauth_cleanup_clears_unconsumed_verifier_material(
    postgres_context: PostgresContext,
) -> None:
    attempt_id = uuid4()
    state_id = uuid4()
    state_hash = uuid4().hex * 2
    _psql(
        postgres_context.container,
        _authenticated(
            _create_setup_sql(
                postgres_context,
                attempt_id=attempt_id,
                token_hash=uuid4().hex * 2,
            )
        ),
    )
    _psql(
        postgres_context.container,
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=state_id,
                attempt_id=attempt_id,
                state_hash=state_hash,
            )
        ),
    )
    _psql(
        postgres_context.container,
        _service(
            f"""
            update public.mercury_provider_oauth_states
            set created_at = pg_catalog.statement_timestamp() - interval '2 minutes',
                expires_at = pg_catalog.statement_timestamp() - interval '1 minute'
            where id = '{state_id}';
            """
        ),
    )

    cleaned = _psql(
        postgres_context.container,
        _service(
            """
            select cleaned_count
            from public.cleanup_expired_mercury_provider_oauth_states(100);
            """
        ),
    )
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'consumed', state.consumed_at is not null,
                  'ciphertext_cleared', state.pkce_verifier_ciphertext is null,
                  'key_version_cleared', state.pkce_key_version is null,
                  'nonce_cleared', state.pkce_nonce is null,
                  'aad_hash_cleared', state.pkce_aad_hash is null
                )::pg_catalog.text
                from public.mercury_provider_oauth_states as state
                where state.id = '{state_id}';
                """
            ),
        )
    )

    assert cleaned == "1"
    assert persisted == {
        "consumed": True,
        "ciphertext_cleared": True,
        "key_version_cleared": True,
        "nonce_cleared": True,
        "aad_hash_cleared": True,
    }


def test_authenticated_cannot_persist_load_or_disconnect_credentials(
    postgres_context: PostgresContext,
) -> None:
    connection_id = uuid4()
    account_id = f"service-only-{connection_id}"
    save_sql = _save_sql(
        postgres_context,
        connection_id=connection_id,
        envelope_id=uuid4(),
        account_id=account_id,
    )

    denied_save = _psql_result(
        postgres_context.container,
        _authenticated(save_sql),
    )
    _assert_secret_safe_error(denied_save, "permission denied")
    _psql(postgres_context.container, _service(save_sql))

    denied_load = _psql_result(
        postgres_context.container,
        _authenticated(
            f"""
            select *
            from public.load_mercury_provider_credential_envelopes(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{connection_id}'
            );
            """
        ),
    )
    denied_disconnect = _psql_result(
        postgres_context.container,
        _authenticated(
            f"""
            select *
            from public.disconnect_mercury_provider_connection(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{connection_id}',
              false
            );
            """
        ),
    )
    wrong_member = _psql_result(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=uuid4(),
                envelope_id=uuid4(),
                account_id=f"wrong-member-{uuid4()}",
                auth_user_id=OTHER_AUTH_USER_ID,
            )
        ),
    )

    _assert_secret_safe_error(denied_load, "permission denied")
    _assert_secret_safe_error(denied_disconnect, "permission denied")
    _assert_secret_safe_error(wrong_member, "workspace_access_denied")
    listed = json.loads(
        _psql(
            postgres_context.container,
            _authenticated(
                f"""
                select pg_catalog.row_to_json(connection)::pg_catalog.text
                from public.list_mercury_provider_connections(
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}'
                ) as connection
                where connection.connection_id = '{connection_id}';
                """
            ),
        )
    )
    assert "provider_account_id" not in listed


def test_setup_oauth_and_envelope_errors_hide_complete_sentinel_material(
    postgres_context: PostgresContext,
) -> None:
    token_sentinel = "feedface" * 8
    malformed_setup = _psql_result(
        postgres_context.container,
        _authenticated(
            f"""
            select *
            from public.create_mercury_provider_setup_attempt(
              '{uuid4()}',
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              null,
              'sandbox',
              '{token_sentinel}',
              pg_catalog.statement_timestamp() + interval '5 minutes'
            );
            """
        ),
    )
    _assert_secret_safe_error(
        malformed_setup,
        "provider_setup_attempt_invalid",
        token_sentinel,
    )

    attempt_id = uuid4()
    _psql(
        postgres_context.container,
        _authenticated(
            _create_setup_sql(
                postgres_context,
                attempt_id=attempt_id,
                token_hash=uuid4().hex * 2,
            )
        ),
    )
    pkce_sentinel = "deadbeef" * 4
    malformed_oauth = _psql_result(
        postgres_context.container,
        _authenticated(
            _create_oauth_sql(
                postgres_context,
                state_id=uuid4(),
                attempt_id=attempt_id,
                state_hash=uuid4().hex * 2,
                callback_state='{"requested_permissions":[null]}',
                pkce_ciphertext_hex=pkce_sentinel,
            )
        ),
    )
    _assert_secret_safe_error(
        malformed_oauth,
        "provider_oauth_state_invalid",
        pkce_sentinel,
    )

    envelope_id_sentinel = "not-a-uuid-envelope-sentinel"
    ciphertext_sentinel = "cafebabe" * 4
    connection_id = uuid4()
    malformed_envelope = _psql_result(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.save_mercury_provider_connection(
              '{connection_id}',
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              'flowaccount',
              'sandbox',
              'malformed-envelope-{connection_id}',
              'Mercury Test Company',
              'oauth2_pkce',
              '[]'::pg_catalog.jsonb,
              'ready',
              1,
              pg_catalog.statement_timestamp(),
              pg_catalog.jsonb_build_array(
                pg_catalog.jsonb_build_object(
                  'id', '{envelope_id_sentinel}',
                  'credential_type', 'access_token',
                  'key_version', 'v1',
                  'nonce', pg_catalog.repeat('ab', 12),
                  'ciphertext', '{ciphertext_sentinel}',
                  'aad_hash', pg_catalog.repeat('ef', 32),
                  'created_at', pg_catalog.statement_timestamp(),
                  'rotated_at', null,
                  'revoked_at', null
                )
              )
            );
            """
        ),
    )
    _assert_secret_safe_error(
        malformed_envelope,
        "provider_credential_envelope_invalid",
        envelope_id_sentinel,
        ciphertext_sentinel,
    )
    assert (
        _psql(
            postgres_context.container,
            _service(
                f"""
            select pg_catalog.count(*)
            from public.mercury_provider_connections
            where id = '{connection_id}';
            """
            ),
        )
        == "0"
    )


def test_permission_arrays_reject_null_non_string_duplicates_and_unsorted_values(
    postgres_context: PostgresContext,
) -> None:
    result = json.loads(
        _psql(
            postgres_context.container,
            _service(
                """
                select pg_catalog.json_build_object(
                  'valid', public.mercury_provider_permissions_are_safe(
                    '["documents.read","profile.read"]'::pg_catalog.jsonb
                  ),
                  'null_element', public.mercury_provider_permissions_are_safe(
                    '["documents.read",null]'::pg_catalog.jsonb
                  ),
                  'number_element', public.mercury_provider_permissions_are_safe(
                    '["documents.read",7]'::pg_catalog.jsonb
                  ),
                  'duplicate', public.mercury_provider_permissions_are_safe(
                    '["documents.read","documents.read"]'::pg_catalog.jsonb
                  ),
                  'unsorted', public.mercury_provider_permissions_are_safe(
                    '["profile.read","documents.read"]'::pg_catalog.jsonb
                  ),
                  'callback_null',
                    public.mercury_provider_callback_state_is_safe(
                      '{"requested_permissions":[null]}'::pg_catalog.jsonb
                    ),
                  'callback_duplicate',
                    public.mercury_provider_callback_state_is_safe(
                      '{"requested_permissions":["profile.read","profile.read"]}'
                        ::pg_catalog.jsonb
                    )
                )::pg_catalog.text;
                """
            ),
        )
    )

    assert result == {
        "valid": True,
        "null_element": False,
        "number_element": False,
        "duplicate": False,
        "unsorted": False,
        "callback_null": False,
        "callback_duplicate": False,
    }


def test_reconnect_requires_same_id_next_revision_and_exact_account_binding(
    postgres_context: PostgresContext,
) -> None:
    connection_id = uuid4()
    account_id = f"reconnect-{connection_id}"
    _psql(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=connection_id,
                envelope_id=uuid4(),
                account_id=account_id,
            )
        ),
    )
    _psql(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.disconnect_mercury_provider_connection(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{connection_id}',
              true
            );
            """
        ),
    )
    stale_revision = _psql_result(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=connection_id,
                envelope_id=uuid4(),
                account_id=account_id,
                revision=2,
            )
        ),
    )
    changed_account = _psql_result(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=connection_id,
                envelope_id=uuid4(),
                account_id=f"{account_id}-changed",
                revision=3,
            )
        ),
    )
    new_id_conflict = _psql_result(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=uuid4(),
                envelope_id=uuid4(),
                account_id=account_id,
            )
        ),
    )
    _assert_secret_safe_error(
        stale_revision,
        "provider_connection_conflict",
    )
    _assert_secret_safe_error(
        changed_account,
        "provider_connection_conflict",
    )
    _assert_secret_safe_error(
        new_id_conflict,
        "provider_connection_conflict",
    )

    replacement_envelope_id = uuid4()
    reconnected = json.loads(
        _psql(
            postgres_context.container,
            _service(
                _save_sql(
                    postgres_context,
                    connection_id=connection_id,
                    envelope_id=replacement_envelope_id,
                    account_id=account_id,
                    revision=3,
                )
            ),
        )
    )
    persisted = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'revision', connection.revision,
                  'readiness', connection.readiness,
                  'revocation', connection.provider_revocation_required,
                  'disconnected_at', connection.disconnected_at,
                  'envelope_ids', connection.credential_envelope_ids
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                where connection.id = '{connection_id}';
                """
            ),
        )
    )

    assert reconnected["connection_id"] == str(connection_id)
    assert reconnected["revision"] == 3
    assert reconnected["readiness"] == "ready"
    assert persisted == {
        "revision": 3,
        "readiness": "ready",
        "revocation": False,
        "disconnected_at": None,
        "envelope_ids": [str(replacement_envelope_id)],
    }


def test_oauth_finalize_atomically_reuses_disconnected_id_and_preserves_failed_stage(
    postgres_context: PostgresContext,
) -> None:
    connection_id = uuid4()
    original_envelope_id = uuid4()
    account_id = f"oauth-reconnect-{connection_id}"
    _psql(
        postgres_context.container,
        _service(
            _save_sql(
                postgres_context,
                connection_id=connection_id,
                envelope_id=original_envelope_id,
                account_id=account_id,
            )
        ),
    )
    _psql(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.disconnect_mercury_provider_connection(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{connection_id}',
              false
            );
            """
        ),
    )

    def stage_sql(staged_id: UUID, envelope_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(staged)::pg_catalog.text
          from public.stage_mercury_provider_connection(
            '{staged_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            'oauth-pending-{staged_id}',
            'FlowAccount',
            'oauth2_pkce',
            '["documents.read","profile.read"]'::pg_catalog.jsonb,
            'requires_validation',
            1,
            null,
            pg_catalog.jsonb_build_array(
              pg_catalog.jsonb_build_object(
                'id', '{envelope_id}',
                'credential_type', 'access_token',
                'key_version', 'v1',
                'nonce', pg_catalog.repeat('ab', 12),
                'ciphertext', pg_catalog.repeat('cd', 16),
                'aad_hash', pg_catalog.repeat('ef', 32),
                'created_at', pg_catalog.statement_timestamp(),
                'rotated_at', null,
                'revoked_at', null
              )
            )
          ) as staged;
        """

    def resolve_target() -> dict[str, object]:
        return json.loads(
            _psql(
                postgres_context.container,
                _service(
                    f"""
                    select pg_catalog.row_to_json(target)::pg_catalog.text
                    from public.resolve_mercury_provider_connection_target(
                      '{postgres_context.tenant_id}',
                      '{postgres_context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox',
                      '{account_id}',
                      '{uuid4()}'
                    ) as target;
                    """
                ),
            )
        )

    def finalize_sql(
        staged_id: UUID,
        envelope_id: UUID,
        revision: int,
    ) -> str:
        return f"""
          select pg_catalog.row_to_json(finalized)::pg_catalog.text
          from public.finalize_mercury_provider_connection(
            '{staged_id}',
            '{connection_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            '{account_id}',
            'FlowAccount Test Company',
            'oauth2_pkce',
            '["documents.read","profile.read"]'::pg_catalog.jsonb,
            'ready',
            {revision},
            pg_catalog.statement_timestamp(),
            pg_catalog.jsonb_build_array(
              pg_catalog.jsonb_build_object(
                'id', '{envelope_id}',
                'credential_type', 'access_token',
                'key_version', 'v1',
                'nonce', pg_catalog.repeat('ab', 12),
                'ciphertext', pg_catalog.repeat('cd', 16),
                'aad_hash', pg_catalog.repeat('ef', 32),
                'created_at', pg_catalog.statement_timestamp(),
                'rotated_at', null,
                'revoked_at', null
              )
            )
          ) as finalized;
        """

    first_stage_id = uuid4()
    _psql(
        postgres_context.container,
        stage_sql(first_stage_id, uuid4()),
    )
    first_target = resolve_target()
    first_finalized = json.loads(
        _psql(
            postgres_context.container,
            finalize_sql(
                first_stage_id,
                uuid4(),
                int(first_target["revision"]),
            ),
        )
    )

    assert first_target == {
        "connection_id": str(connection_id),
        "revision": 3,
        "reuses_existing": True,
    }
    assert first_finalized["connection_id"] == str(connection_id)
    assert first_finalized["revision"] == 3
    assert first_finalized["readiness"] == "ready"

    _psql(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.disconnect_mercury_provider_connection(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{connection_id}',
              false
            );
            """
        ),
    )
    failed_stage_id = uuid4()
    failed_stage_envelope_id = uuid4()
    _psql(
        postgres_context.container,
        stage_sql(failed_stage_id, failed_stage_envelope_id),
    )
    second_target = resolve_target()
    failed_finalize = _psql_result(
        postgres_context.container,
        finalize_sql(
            failed_stage_id,
            uuid4(),
            int(second_target["revision"]) - 1,
        ),
    )
    _assert_secret_safe_error(
        failed_finalize,
        "provider_connection_conflict",
    )

    staged_after_failure = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'readiness', connection.readiness,
                  'revision', connection.revision,
                  'revocation', connection.provider_revocation_required,
                  'envelopes', connection.credential_envelope_ids
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                where connection.id = '{failed_stage_id}';
                """
            ),
        )
    )
    assert staged_after_failure == {
        "readiness": "requires_validation",
        "revision": 1,
        "revocation": True,
        "envelopes": [str(failed_stage_envelope_id)],
    }

    _psql(
        postgres_context.container,
        f"""
            select *
            from public.record_mercury_provider_revocation_obligation(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{failed_stage_id}',
              'flowaccount',
              'sandbox',
              'oauth-pending-{failed_stage_id}',
              'FlowAccount',
              'oauth2_pkce',
              '["documents.read","profile.read"]'::pg_catalog.jsonb
            );
            """,
    )
    cleaned_stage = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'readiness', connection.readiness,
                  'revision', connection.revision,
                  'revocation', connection.provider_revocation_required,
                  'envelopes', connection.credential_envelope_ids,
                  'persisted_envelopes', (
                    select pg_catalog.count(*)
                    from public.mercury_provider_credential_envelopes as envelope
                    where envelope.connection_id = connection.id
                  )
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                where connection.id = '{failed_stage_id}';
                """
            ),
        )
    )
    assert cleaned_stage == {
        "readiness": "disconnected",
        "revision": 2,
        "revocation": True,
        "envelopes": [],
        "persisted_envelopes": 0,
    }


def test_internal_oauth_attempts_reconcile_finalize_and_never_create_public_ghosts(
    postgres_context: PostgresContext,
) -> None:
    def begin_sql(attempt_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(attempt)::pg_catalog.text
          from public.begin_mercury_provider_oauth_attempt(
            '{attempt_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            '["documents.read","profile.read"]'::pg_catalog.jsonb
          ) as attempt;
        """

    def attach_sql(attempt_id: UUID, envelope_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(attempt)::pg_catalog.text
          from public.attach_mercury_provider_oauth_attempt(
            '{attempt_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            'oauth-pending-{attempt_id}',
            'FlowAccount',
            'oauth2_pkce',
            '["documents.read","profile.read"]'::pg_catalog.jsonb,
            'requires_validation',
            1,
            null,
            pg_catalog.jsonb_build_array(
              pg_catalog.jsonb_build_object(
                'id', '{envelope_id}',
                'credential_type', 'access_token',
                'key_version', 'v1',
                'nonce', pg_catalog.repeat('ab', 12),
                'ciphertext', pg_catalog.repeat('cd', 16),
                'aad_hash', pg_catalog.repeat('ef', 32),
                'created_at', '2026-07-28T00:00:00+00:00',
                'rotated_at', null,
                'revoked_at', null
              )
            )
          ) as attempt;
        """

    def resolve_sql(account_id: str, proposed_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(target)::pg_catalog.text
          from public.resolve_mercury_provider_connection_target(
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            '{account_id}',
            '{proposed_id}'
          ) as target;
        """

    def finalize_sql(
        attempt_id: UUID,
        target_id: UUID,
        account_id: str,
        revision: int,
        envelope_id: UUID,
    ) -> str:
        return f"""
          select pg_catalog.row_to_json(finalized)::pg_catalog.text
          from public.finalize_mercury_provider_oauth_attempt(
            '{attempt_id}',
            '{target_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox',
            '{account_id}',
            'FlowAccount Test Company',
            'oauth2_pkce',
            '["documents.read","profile.read"]'::pg_catalog.jsonb,
            'requires_validation',
            {revision},
            '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
            pg_catalog.jsonb_build_array(
              pg_catalog.jsonb_build_object(
                'id', '{envelope_id}',
                'credential_type', 'access_token',
                'key_version', 'v1',
                'nonce', pg_catalog.repeat('ab', 12),
                'ciphertext', pg_catalog.repeat('cd', 16),
                'aad_hash', pg_catalog.repeat('ef', 32),
                'created_at', '2026-07-28T00:00:00+00:00',
                'rotated_at', null,
                'revoked_at', null
              )
            )
          ) as finalized;
        """

    def transition_sql(function: str, attempt_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(attempt)::pg_catalog.text
          from public.{function}(
            '{attempt_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox'
          ) as attempt;
        """

    def acknowledge_sql(attempt_id: UUID) -> str:
        return f"""
          select pg_catalog.row_to_json(connection)::pg_catalog.text
          from public.acknowledge_mercury_provider_oauth_attempt(
            '{attempt_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox'
          ) as connection;
        """

    def visible_count() -> int:
        return int(
            _psql(
                postgres_context.container,
                _authenticated(
                    f"""
                    select pg_catalog.count(*)
                    from public.list_mercury_provider_connections(
                      '{postgres_context.tenant_id}',
                      '{postgres_context.workspace_id}',
                      '{AUTH_USER_ID}'
                    );
                    """
                ),
            )
        )

    def public_attempt_artifact_count(*attempt_ids: UUID) -> int:
        ids = ", ".join(f"'{attempt_id}'::pg_catalog.uuid" for attempt_id in attempt_ids)
        account_ids = ", ".join(
            f"'oauth-pending-{attempt_id}'::pg_catalog.text" for attempt_id in attempt_ids
        )
        return int(
            _psql(
                postgres_context.container,
                _service(
                    f"""
                    select pg_catalog.count(*)
                    from public.mercury_provider_connections
                    where id in ({ids})
                      or provider_account_id in ({account_ids});
                    """
                ),
            )
        )

    baseline_visible_count = visible_count()
    account_id = f"internal-attempt-{uuid4()}"
    first_attempt_id = uuid4()
    _psql(
        postgres_context.container,
        _service(begin_sql(first_attempt_id)),
    )
    attached = json.loads(
        _psql(
            postgres_context.container,
            _service(attach_sql(first_attempt_id, uuid4())),
        )
    )
    assert attached["status"] == "material_attached"
    loaded_attempt_material = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'count', pg_catalog.count(*),
                  'connection_ids', pg_catalog.json_agg(connection_id)
                )::pg_catalog.text
                from public.load_mercury_provider_oauth_attempt_envelopes(
                  '{first_attempt_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox'
                );
                """
            ),
        )
    )
    assert loaded_attempt_material == {
        "count": 1,
        "connection_ids": [str(first_attempt_id)],
    }
    assert visible_count() == baseline_visible_count

    first_target = json.loads(
        _psql(
            postgres_context.container,
            _service(resolve_sql(account_id, uuid4())),
        )
    )
    target_id = UUID(first_target["connection_id"])
    target_revision = int(first_target["revision"])
    final_envelope_id = uuid4()
    first_finalize = json.loads(
        _psql(
            postgres_context.container,
            _service(
                finalize_sql(
                    first_attempt_id,
                    target_id,
                    account_id,
                    target_revision,
                    final_envelope_id,
                )
            ),
        )
    )
    reconciled_finalize = json.loads(
        _psql(
            postgres_context.container,
            _service(
                finalize_sql(
                    first_attempt_id,
                    target_id,
                    account_id,
                    target_revision,
                    final_envelope_id,
                )
            ),
        )
    )

    assert first_finalize == reconciled_finalize
    assert first_finalize["readiness"] == "requires_validation"
    assert visible_count() == baseline_visible_count
    acknowledged = json.loads(
        _psql(
            postgres_context.container,
            _service(acknowledge_sql(first_attempt_id)),
        )
    )
    assert acknowledged["readiness"] == "ready"
    assert visible_count() == baseline_visible_count + 1
    assert public_attempt_artifact_count(first_attempt_id) == 0

    failed = json.loads(
        _psql(
            postgres_context.container,
            _service(
                transition_sql(
                    "fail_mercury_provider_oauth_attempt",
                    first_attempt_id,
                )
            ),
        )
    )
    target_after_failure = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'readiness', readiness,
                  'revocation', provider_revocation_required,
                  'envelopes', credential_envelope_ids
                )::pg_catalog.text
                from public.mercury_provider_connections
                where id = '{target_id}';
                """
            ),
        )
    )
    finalized_attempt_material_count = int(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.jsonb_array_length(credential_envelopes)
                from public.mercury_provider_oauth_attempts
                where id = '{first_attempt_id}';
                """
            ),
        )
    )
    finalized_remediation_material = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'count', pg_catalog.count(*),
                  'connection_ids', pg_catalog.json_agg(connection_id)
                )::pg_catalog.text
                from public.load_mercury_provider_oauth_attempt_envelopes(
                  '{first_attempt_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox'
                );
                """
            ),
        )
    )
    assert failed["status"] == "failed"
    assert failed["provider_revocation_required"] is True
    assert finalized_attempt_material_count == 1
    assert finalized_remediation_material == {
        "count": 1,
        "connection_ids": [str(target_id)],
    }
    assert target_after_failure == {
        "readiness": "disconnected",
        "revocation": True,
        "envelopes": [],
    }

    revoked = json.loads(
        _psql(
            postgres_context.container,
            _service(
                transition_sql(
                    "complete_mercury_provider_oauth_attempt_revocation",
                    first_attempt_id,
                )
            ),
        )
    )
    assert revoked["status"] == "revoked"
    assert revoked["provider_revocation_required"] is False
    assert (
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.jsonb_array_length(credential_envelopes)
                from public.mercury_provider_oauth_attempts
                where id = '{first_attempt_id}';
                """
            ),
        )
        == "0"
    )

    reconnect_attempt_id = uuid4()
    _psql(postgres_context.container, _service(begin_sql(reconnect_attempt_id)))
    _psql(
        postgres_context.container,
        _service(attach_sql(reconnect_attempt_id, uuid4())),
    )
    reconnect_target = json.loads(
        _psql(
            postgres_context.container,
            _service(resolve_sql(account_id, uuid4())),
        )
    )
    assert reconnect_target["connection_id"] == str(target_id)
    reconnect_envelope_id = uuid4()
    reconnect_finalize_sql = finalize_sql(
        reconnect_attempt_id,
        target_id,
        account_id,
        int(reconnect_target["revision"]),
        reconnect_envelope_id,
    )
    _psql(postgres_context.container, _service(reconnect_finalize_sql))
    _psql(postgres_context.container, _service(reconnect_finalize_sql))
    reconnect_acknowledged = json.loads(
        _psql(
            postgres_context.container,
            _service(acknowledge_sql(reconnect_attempt_id)),
        )
    )
    assert reconnect_acknowledged["readiness"] == "ready"
    assert visible_count() == baseline_visible_count + 1

    process_boundary_attempt_id = uuid4()
    pending = json.loads(
        _psql(
            postgres_context.container,
            _service(begin_sql(process_boundary_attempt_id)),
        )
    )
    assert pending["status"] == "exchange_pending"
    assert pending["provider_revocation_required"] is True

    failed_revocation_attempt_id = uuid4()
    _psql(
        postgres_context.container,
        _service(begin_sql(failed_revocation_attempt_id)),
    )
    _psql(
        postgres_context.container,
        _service(attach_sql(failed_revocation_attempt_id, uuid4())),
    )
    durable_failure = json.loads(
        _psql(
            postgres_context.container,
            _service(
                transition_sql(
                    "fail_mercury_provider_oauth_attempt",
                    failed_revocation_attempt_id,
                )
            ),
        )
    )
    assert durable_failure["status"] == "failed"
    assert durable_failure["provider_revocation_required"] is True
    assert (
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.jsonb_array_length(credential_envelopes)
                from public.mercury_provider_oauth_attempts
                where id = '{failed_revocation_attempt_id}';
                """
            ),
        )
        == "1"
    )
    failed_remediation_material = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'count', pg_catalog.count(*),
                  'connection_ids', pg_catalog.json_agg(connection_id)
                )::pg_catalog.text
                from public.load_mercury_provider_oauth_attempt_envelopes(
                  '{failed_revocation_attempt_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox'
                );
                """
            ),
        )
    )
    assert failed_remediation_material == {
        "count": 1,
        "connection_ids": [str(failed_revocation_attempt_id)],
    }
    assert visible_count() == baseline_visible_count + 1
    assert (
        public_attempt_artifact_count(
            first_attempt_id,
            reconnect_attempt_id,
            process_boundary_attempt_id,
            failed_revocation_attempt_id,
        )
        == 0
    )


def test_oauth_generation_stays_held_until_ack_and_cleanup_follows_refresh_revision(
    postgres_context: PostgresContext,
) -> None:
    attempt_id = uuid4()
    target_id = uuid4()
    provisional_envelope_id = uuid4()
    held_envelope_id = uuid4()
    held_refresh_envelope_id = uuid4()
    ready_refresh_envelope_id = uuid4()
    account_id = f"generation-account-{uuid4()}"

    def envelope(envelope_id: UUID) -> str:
        return f"""
          pg_catalog.jsonb_build_array(
            pg_catalog.jsonb_build_object(
              'id', '{envelope_id}',
              'credential_type', 'access_token',
              'key_version', 'v1',
              'nonce', pg_catalog.repeat('ab', 12),
              'ciphertext', pg_catalog.repeat('cd', 16),
              'aad_hash', pg_catalog.repeat('ef', 32),
              'created_at', '2026-07-28T00:00:00+00:00',
              'rotated_at', null,
              'revoked_at', null
            )
          )
        """

    _psql(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.begin_mercury_provider_oauth_attempt(
              '{attempt_id}',
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              'flowaccount',
              'sandbox',
              '["documents.read","profile.read"]'::pg_catalog.jsonb
            );
            select *
            from public.attach_mercury_provider_oauth_attempt(
              '{attempt_id}',
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              'flowaccount',
              'sandbox',
              'oauth-pending-{attempt_id}',
              'FlowAccount',
              'oauth2_pkce',
              '["documents.read","profile.read"]'::pg_catalog.jsonb,
              'requires_validation',
              1,
              null,
              {envelope(provisional_envelope_id)}
            );
            """
        ),
    )
    held = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.row_to_json(result)::pg_catalog.text
                from public.finalize_mercury_provider_oauth_attempt(
                  '{attempt_id}',
                  '{target_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{account_id}',
                  'FlowAccount Generation Company',
                  'oauth2_pkce',
                  '["documents.read","profile.read"]'::pg_catalog.jsonb,
                  'requires_validation',
                  1,
                  '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                  {envelope(held_envelope_id)}
                ) as result;
                """
            ),
        )
    )
    assert held["readiness"] == "requires_validation"
    assert (
        _psql(
            postgres_context.container,
            _authenticated(
                f"""
                select pg_catalog.count(*)
                from public.list_mercury_provider_connections(
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}'
                )
                where connection_id = '{target_id}';
                """
            ),
        )
        == "0"
    )
    held_load = _psql_result(
        postgres_context.container,
        _service(
            f"""
            select *
            from public.load_mercury_provider_credential_envelopes(
              '{postgres_context.tenant_id}',
              '{postgres_context.workspace_id}',
              '{AUTH_USER_ID}',
              '{target_id}'
            );
            """
        ),
    )
    _assert_secret_safe_error(held_load, "provider_connection_not_found")

    _psql(
        postgres_context.container,
        MIGRATIONS[-1].read_text(encoding="utf-8"),
    )
    replayed_hold = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'readiness', connection.readiness,
                  'acknowledged', attempt.acknowledged_at is not null
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                join public.mercury_provider_oauth_attempts as attempt
                  on attempt.id = '{attempt_id}'
                where connection.id = '{target_id}';
                """
            ),
        )
    )
    assert replayed_hold == {
        "readiness": "requires_validation",
        "acknowledged": False,
    }
    assert (
        _psql(
            postgres_context.container,
            _authenticated(
                f"""
                select pg_catalog.count(*)
                from public.list_mercury_provider_connections(
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}'
                )
                where connection_id = '{target_id}';
                """
            ),
        )
        == "0"
    )

    refreshed_hold = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.row_to_json(result)::pg_catalog.text
                from public.save_mercury_provider_connection(
                  '{target_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{account_id}',
                  'FlowAccount Generation Company',
                  'oauth2_pkce',
                  '["documents.read","profile.read"]'::pg_catalog.jsonb,
                  'requires_validation',
                  2,
                  '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                  {envelope(held_refresh_envelope_id)}
                ) as result;
                """
            ),
        )
    )
    reconciled_hold = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.row_to_json(result)::pg_catalog.text
                from public.finalize_mercury_provider_oauth_attempt(
                  '{attempt_id}',
                  '{target_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{account_id}',
                  'FlowAccount Generation Company',
                  'oauth2_pkce',
                  '["documents.read","profile.read"]'::pg_catalog.jsonb,
                  'requires_validation',
                  1,
                  '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                  {envelope(held_envelope_id)}
                ) as result;
                """
            ),
        )
    )
    assert refreshed_hold["revision"] == 2
    assert reconciled_hold["revision"] == 2
    assert reconciled_hold["readiness"] == "requires_validation"

    def acknowledge_sql() -> str:
        return f"""
          select pg_catalog.row_to_json(result)::pg_catalog.text
          from public.acknowledge_mercury_provider_oauth_attempt(
            '{attempt_id}',
            '{postgres_context.tenant_id}',
            '{postgres_context.workspace_id}',
            '{AUTH_USER_ID}',
            'flowaccount',
            'sandbox'
          ) as result;
        """

    acknowledged = json.loads(
        _psql(
            postgres_context.container,
            _service(acknowledge_sql()),
        )
    )
    acknowledged_again = json.loads(
        _psql(
            postgres_context.container,
            _service(acknowledge_sql()),
        )
    )
    assert acknowledged == acknowledged_again
    assert acknowledged["readiness"] == "ready"
    assert acknowledged["revision"] == 3

    refreshed_ready = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.row_to_json(result)::pg_catalog.text
                from public.save_mercury_provider_connection(
                  '{target_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{account_id}',
                  'FlowAccount Generation Company',
                  'oauth2_pkce',
                  '["documents.read","profile.read"]'::pg_catalog.jsonb,
                  'ready',
                  4,
                  '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                  {envelope(ready_refresh_envelope_id)}
                ) as result;
                """
            ),
        )
    )
    failed = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.row_to_json(result)::pg_catalog.text
                from public.fail_mercury_provider_oauth_attempt(
                  '{attempt_id}',
                  '{postgres_context.tenant_id}',
                  '{postgres_context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox'
                ) as result;
                """
            ),
        )
    )
    durable = json.loads(
        _psql(
            postgres_context.container,
            _service(
                f"""
                select pg_catalog.json_build_object(
                  'target_readiness', connection.readiness,
                  'target_revision', connection.revision,
                  'target_revocation', connection.provider_revocation_required,
                  'attempt_status', attempt.status,
                  'attempt_material', attempt.credential_envelopes -> 0 ->> 'id'
                )::pg_catalog.text
                from public.mercury_provider_connections as connection
                join public.mercury_provider_oauth_attempts as attempt
                  on attempt.id = '{attempt_id}'
                where connection.id = '{target_id}';
                """
            ),
        )
    )
    assert refreshed_ready["revision"] == 4
    assert failed["status"] == "failed"
    assert durable == {
        "target_readiness": "disconnected",
        "target_revision": 5,
        "target_revocation": True,
        "attempt_status": "failed",
        "attempt_material": str(ready_refresh_envelope_id),
    }


def test_base_to_head_upgrade_moves_exact_legacy_ghosts_and_replays_cleanly() -> None:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task6-upgrade-{uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task4_test",
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
        for migration in MIGRATIONS[:-2]:
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
        context = PostgresContext(
            container=container,
            tenant_id=UUID(payload["memberships"][0]["tenant_id"]),
            workspace_id=UUID(payload["active_workspace_id"]),
        )
        failed_id = uuid4()
        failed_envelope_id = uuid4()
        safe_id = uuid4()
        safe_envelope_id = uuid4()
        similar_id = uuid4()
        similar_envelope_id = uuid4()

        def stage_legacy(connection_id: UUID, envelope_id: UUID) -> str:
            return f"""
              select *
              from public.stage_mercury_provider_connection(
                '{connection_id}',
                '{context.tenant_id}',
                '{context.workspace_id}',
                '{AUTH_USER_ID}',
                'flowaccount',
                'sandbox',
                'oauth-pending-{connection_id}',
                'FlowAccount',
                'oauth2_pkce',
                '["documents.read","profile.read"]'::pg_catalog.jsonb,
                'requires_validation',
                1,
                null,
                pg_catalog.jsonb_build_array(
                  pg_catalog.jsonb_build_object(
                    'id', '{envelope_id}',
                    'credential_type', 'access_token',
                    'key_version', 'v1',
                    'nonce', pg_catalog.repeat('ab', 12),
                    'ciphertext', pg_catalog.repeat('cd', 16),
                    'aad_hash', pg_catalog.repeat('ef', 32),
                    'created_at', '2026-07-28T00:00:00+00:00',
                    'rotated_at', null,
                    'revoked_at', null
                  )
                )
              );
            """

        _psql(container, _service(stage_legacy(failed_id, failed_envelope_id)))
        _psql(container, _service(stage_legacy(safe_id, safe_envelope_id)))
        _psql(
            container,
            _service(
                f"""
                select *
                from public.disconnect_mercury_provider_connection(
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  '{safe_id}',
                  true
                );
                select *
                from public.complete_mercury_provider_revocation(
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  '{safe_id}'
                );
                select *
                from public.save_mercury_provider_connection(
                  '{similar_id}',
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  'oauth-pending-{safe_id}-customer',
                  'Real Similar Customer',
                  'oauth2_pkce',
                  '["profile.read"]'::pg_catalog.jsonb,
                  'ready',
                  1,
                  '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                  pg_catalog.jsonb_build_array(
                    pg_catalog.jsonb_build_object(
                      'id', '{similar_envelope_id}',
                      'credential_type', 'access_token',
                      'key_version', 'v1',
                      'nonce', pg_catalog.repeat('ab', 12),
                      'ciphertext', pg_catalog.repeat('cd', 16),
                      'aad_hash', pg_catalog.repeat('ef', 32),
                      'created_at', '2026-07-28T00:00:00+00:00',
                      'rotated_at', null,
                      'revoked_at', null
                    )
                  )
                );
                """
            ),
        )

        for _ in range(2):
            for migration in MIGRATIONS[-2:]:
                _psql(container, migration.read_text(encoding="utf-8"))

        upgraded = json.loads(
            _psql(
                container,
                _service(
                    f"""
                    select pg_catalog.json_build_object(
                      'exact_public_rows', (
                        select pg_catalog.count(*)
                        from public.mercury_provider_connections as connection
                        where connection.provider_account_id
                          = 'oauth-pending-' || connection.id::pg_catalog.text
                      ),
                      'failed_attempt_status', (
                        select attempt.status
                        from public.mercury_provider_oauth_attempts as attempt
                        where attempt.id = '{failed_id}'
                      ),
                      'failed_attempt_revocation', (
                        select attempt.provider_revocation_required
                        from public.mercury_provider_oauth_attempts as attempt
                        where attempt.id = '{failed_id}'
                      ),
                      'failed_attempt_envelope', (
                        select attempt.credential_envelopes -> 0 ->> 'id'
                        from public.mercury_provider_oauth_attempts as attempt
                        where attempt.id = '{failed_id}'
                      ),
                      'safe_attempt_rows', (
                        select pg_catalog.count(*)
                        from public.mercury_provider_oauth_attempts as attempt
                        where attempt.id = '{safe_id}'
                      ),
                      'similar_public_rows', (
                        select pg_catalog.count(*)
                        from public.mercury_provider_connections as connection
                        where connection.id = '{similar_id}'
                      ),
                      'legacy_rpc_execute', (
                        select pg_catalog.bool_or(
                          pg_catalog.has_function_privilege(
                            'service_role',
                            routine.oid,
                            'EXECUTE'
                          )
                        )
                        from pg_catalog.pg_proc as routine
                        join pg_catalog.pg_namespace as namespace
                          on namespace.oid = routine.pronamespace
                        where namespace.nspname = 'public'
                          and routine.proname in (
                            'stage_mercury_provider_connection',
                            'finalize_mercury_provider_connection',
                            'record_mercury_provider_revocation_obligation'
                          )
                      )
                    )::pg_catalog.text;
                    """
                ),
            )
        )
        listed_similar = int(
            _psql(
                container,
                _authenticated(
                    f"""
                    select pg_catalog.count(*)
                    from public.list_mercury_provider_connections(
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}'
                    )
                    where connection_id = '{similar_id}';
                    """
                ),
            )
        )

        assert upgraded == {
            "exact_public_rows": 0,
            "failed_attempt_status": "failed",
            "failed_attempt_revocation": True,
            "failed_attempt_envelope": str(failed_envelope_id),
            "safe_attempt_rows": 0,
            "similar_public_rows": 1,
            "legacy_rpc_execute": False,
        }
        assert listed_similar == 1
    finally:
        _docker("rm", "-f", container, check=False)


def test_base_to_head_upgrade_selects_only_proven_oauth_generation_owners() -> None:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")

    container = f"mercury-task6-owner-upgrade-{uuid4().hex[:12]}"
    _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task4_test",
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
        for migration in MIGRATIONS[:-1]:
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
        context = PostgresContext(
            container=container,
            tenant_id=UUID(payload["memberships"][0]["tenant_id"]),
            workspace_id=UUID(payload["active_workspace_id"]),
        )
        permissions = '["documents.read","profile.read"]'

        def envelope(envelope_id: UUID) -> str:
            return f"""
              pg_catalog.jsonb_build_array(
                pg_catalog.jsonb_build_object(
                  'id', '{envelope_id}',
                  'credential_type', 'access_token',
                  'key_version', 'v1',
                  'nonce', pg_catalog.repeat('ab', 12),
                  'ciphertext', pg_catalog.repeat('cd', 16),
                  'aad_hash', pg_catalog.repeat('ef', 32),
                  'created_at', '2026-07-28T00:00:00+00:00',
                  'rotated_at', null,
                  'revoked_at', null
                )
              )
            """

        def finalize_legacy_generation(
            *,
            attempt_id: UUID,
            proposed_target_id: UUID,
            account_id: str,
            envelope_id: UUID,
        ) -> tuple[UUID, int]:
            _psql(
                container,
                _service(
                    f"""
                    select *
                    from public.begin_mercury_provider_oauth_attempt(
                      '{attempt_id}',
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox',
                      '{permissions}'::pg_catalog.jsonb
                    );
                    select *
                    from public.attach_mercury_provider_oauth_attempt(
                      '{attempt_id}',
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox',
                      'oauth-pending-{attempt_id}',
                      'FlowAccount',
                      'oauth2_pkce',
                      '{permissions}'::pg_catalog.jsonb,
                      'requires_validation',
                      1,
                      null,
                      {envelope(uuid4())}
                    );
                    """
                ),
            )
            target = json.loads(
                _psql(
                    container,
                    _service(
                        f"""
                        select pg_catalog.row_to_json(result)::pg_catalog.text
                        from public.resolve_mercury_provider_connection_target(
                          '{context.tenant_id}',
                          '{context.workspace_id}',
                          '{AUTH_USER_ID}',
                          'flowaccount',
                          'sandbox',
                          '{account_id}',
                          '{proposed_target_id}'
                        ) as result;
                        """
                    ),
                )
            )
            target_id = UUID(target["connection_id"])
            target_revision = int(target["revision"])
            _psql(
                container,
                _service(
                    f"""
                    select *
                    from public.finalize_mercury_provider_oauth_attempt(
                      '{attempt_id}',
                      '{target_id}',
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox',
                      '{account_id}',
                      'FlowAccount Upgrade Company',
                      'oauth2_pkce',
                      '{permissions}'::pg_catalog.jsonb,
                      'ready',
                      {target_revision},
                      '2026-07-28T00:05:00+00:00'::pg_catalog.timestamptz,
                      {envelope(envelope_id)}
                    );
                    """
                ),
            )
            return target_id, target_revision

        def disconnect_and_complete(connection_id: UUID) -> None:
            _psql(
                container,
                _service(
                    f"""
                    select *
                    from public.disconnect_mercury_provider_connection(
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      '{connection_id}',
                      true
                    );
                    select *
                    from public.complete_mercury_provider_revocation(
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      '{connection_id}'
                    );
                    """
                ),
            )

        def fail_attempt(attempt_id: UUID) -> None:
            _psql(
                container,
                _service(
                    f"""
                    select *
                    from public.fail_mercury_provider_oauth_attempt(
                      '{attempt_id}',
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox'
                    );
                    """
                ),
            )

        def complete_attempt_revocation(attempt_id: UUID) -> None:
            _psql(
                container,
                _service(
                    f"""
                    select *
                    from public.complete_mercury_provider_oauth_attempt_revocation(
                      '{attempt_id}',
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      'flowaccount',
                      'sandbox'
                    );
                    """
                ),
            )

        def listed_count(connection_id: UUID) -> int:
            return int(
                _psql(
                    container,
                    _authenticated(
                        f"""
                        select pg_catalog.count(*)
                        from public.list_mercury_provider_connections(
                          '{context.tenant_id}',
                          '{context.workspace_id}',
                          '{AUTH_USER_ID}'
                        )
                        where connection_id = '{connection_id}';
                        """
                    ),
                )
            )

        def load_result(connection_id: UUID) -> subprocess.CompletedProcess[str]:
            return _psql_result(
                container,
                _service(
                    f"""
                    select *
                    from public.load_mercury_provider_credential_envelopes(
                      '{context.tenant_id}',
                      '{context.workspace_id}',
                      '{AUTH_USER_ID}',
                      '{connection_id}'
                    );
                    """
                ),
            )

        # A normal pre-generation reconnect leaves finalized A in history while
        # finalized B owns the newer revision and envelope on the reused target.
        account_id = f"owner-upgrade-{uuid4()}"
        attempt_a_id = uuid4()
        attempt_b_id = uuid4()
        envelope_a_id = uuid4()
        envelope_b_id = uuid4()
        target_id, revision_a = finalize_legacy_generation(
            attempt_id=attempt_a_id,
            proposed_target_id=uuid4(),
            account_id=account_id,
            envelope_id=envelope_a_id,
        )
        assert revision_a == 1
        disconnect_and_complete(target_id)
        reconnected_target_id, revision_b = finalize_legacy_generation(
            attempt_id=attempt_b_id,
            proposed_target_id=uuid4(),
            account_id=account_id,
            envelope_id=envelope_b_id,
        )
        assert reconnected_target_id == target_id
        assert revision_b == 3

        # Equal ownership ranks are not evidence. UUID ordering may make query
        # output stable, but it must not choose either finalized attempt.
        ambiguous_account_id = f"ambiguous-upgrade-{uuid4()}"
        ambiguous_attempt_a_id = uuid4()
        ambiguous_attempt_b_id = uuid4()
        ambiguous_target_id, ambiguous_revision = finalize_legacy_generation(
            attempt_id=ambiguous_attempt_a_id,
            proposed_target_id=uuid4(),
            account_id=ambiguous_account_id,
            envelope_id=uuid4(),
        )
        _psql(
            container,
            _service(
                f"""
                update public.mercury_provider_oauth_attempts
                set created_at = '2026-07-28T00:00:00+00:00',
                    updated_at = '2026-07-28T00:10:00+00:00'
                where id = '{ambiguous_attempt_a_id}';
                insert into public.mercury_provider_oauth_attempts (
                  id,
                  tenant_id,
                  workspace_id,
                  auth_user_id,
                  provider,
                  environment,
                  granted_permissions,
                  status,
                  provider_account_id,
                  account_display_name,
                  authorization_method,
                  credential_envelopes,
                  target_connection_id,
                  target_revision,
                  provider_revocation_required,
                  created_at,
                  updated_at
                )
                values (
                  '{ambiguous_attempt_b_id}',
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{permissions}'::pg_catalog.jsonb,
                  'finalized',
                  '{ambiguous_account_id}',
                  'FlowAccount Upgrade Company',
                  'oauth2_pkce',
                  '[]'::pg_catalog.jsonb,
                  '{ambiguous_target_id}',
                  {ambiguous_revision},
                  false,
                  '2026-07-28T00:00:00+00:00',
                  '2026-07-28T00:10:00+00:00'
                );
                """
            ),
        )

        # A ready target with only completed revoked history has no current
        # generation owner and must not inherit that historical attempt.
        absent_account_id = f"absent-upgrade-{uuid4()}"
        revoked_attempt_id = uuid4()
        absent_target_id, _ = finalize_legacy_generation(
            attempt_id=revoked_attempt_id,
            proposed_target_id=uuid4(),
            account_id=absent_account_id,
            envelope_id=uuid4(),
        )
        fail_attempt(revoked_attempt_id)
        complete_attempt_revocation(revoked_attempt_id)
        absent_envelope_id = uuid4()
        _psql(
            container,
            _service(
                f"""
                select *
                from public.save_mercury_provider_connection(
                  '{absent_target_id}',
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{absent_account_id}',
                  'FlowAccount Upgrade Company',
                  'oauth2_pkce',
                  '{permissions}'::pg_catalog.jsonb,
                  'ready',
                  3,
                  '2026-07-28T00:15:00+00:00'::pg_catalog.timestamptz,
                  {envelope(absent_envelope_id)}
                );
                """
            ),
        )

        # A disconnected target with a current revocation obligation is owned
        # only by its exact failed generation and is never acknowledged.
        failed_account_id = f"failed-upgrade-{uuid4()}"
        failed_attempt_id = uuid4()
        failed_target_id, _ = finalize_legacy_generation(
            attempt_id=failed_attempt_id,
            proposed_target_id=uuid4(),
            account_id=failed_account_id,
            envelope_id=uuid4(),
        )
        fail_attempt(failed_attempt_id)

        head_migration = MIGRATIONS[-1].read_text(encoding="utf-8")
        _psql(container, head_migration)
        _psql(container, head_migration)

        upgraded = json.loads(
            _psql(
                container,
                _service(
                    f"""
                    select pg_catalog.json_build_object(
                      'owner', (
                        select oauth_generation_id
                        from public.mercury_provider_connections
                        where id = '{target_id}'
                      ),
                      'attempt_a_acknowledged', (
                        select acknowledged_at is not null
                        from public.mercury_provider_oauth_attempts
                        where id = '{attempt_a_id}'
                      ),
                      'attempt_b_acknowledged', (
                        select acknowledged_at is not null
                        from public.mercury_provider_oauth_attempts
                        where id = '{attempt_b_id}'
                      ),
                      'ambiguous_owner', (
                        select oauth_generation_id
                        from public.mercury_provider_connections
                        where id = '{ambiguous_target_id}'
                      ),
                      'ambiguous_readiness', (
                        select readiness
                        from public.mercury_provider_connections
                        where id = '{ambiguous_target_id}'
                      ),
                      'ambiguous_acknowledged', (
                        select pg_catalog.count(*)
                        from public.mercury_provider_oauth_attempts
                        where id in (
                          '{ambiguous_attempt_a_id}',
                          '{ambiguous_attempt_b_id}'
                        )
                          and acknowledged_at is not null
                      ),
                      'absent_owner', (
                        select oauth_generation_id
                        from public.mercury_provider_connections
                        where id = '{absent_target_id}'
                      ),
                      'absent_readiness', (
                        select readiness
                        from public.mercury_provider_connections
                        where id = '{absent_target_id}'
                      ),
                      'revoked_acknowledged', (
                        select acknowledged_at is not null
                        from public.mercury_provider_oauth_attempts
                        where id = '{revoked_attempt_id}'
                      ),
                      'failed_owner', (
                        select oauth_generation_id
                        from public.mercury_provider_connections
                        where id = '{failed_target_id}'
                      ),
                      'failed_acknowledged', (
                        select acknowledged_at is not null
                        from public.mercury_provider_oauth_attempts
                        where id = '{failed_attempt_id}'
                      )
                    )::pg_catalog.text;
                    """
                ),
            )
        )
        assert upgraded == {
            "owner": str(attempt_b_id),
            "attempt_a_acknowledged": False,
            "attempt_b_acknowledged": True,
            "ambiguous_owner": None,
            "ambiguous_readiness": "requires_validation",
            "ambiguous_acknowledged": 0,
            "absent_owner": None,
            "absent_readiness": "requires_validation",
            "revoked_acknowledged": False,
            "failed_owner": str(failed_attempt_id),
            "failed_acknowledged": False,
        }

        for held_target_id in (ambiguous_target_id, absent_target_id):
            assert listed_count(held_target_id) == 0
            _assert_secret_safe_error(
                load_result(held_target_id),
                "provider_connection_not_found",
            )

        # Once B is a valid acknowledged owner, migration replay must not
        # replace it or acknowledge another same-target finalized row.
        contender_id = uuid4()
        _psql(
            container,
            _service(
                f"""
                insert into public.mercury_provider_oauth_attempts (
                  id,
                  tenant_id,
                  workspace_id,
                  auth_user_id,
                  provider,
                  environment,
                  granted_permissions,
                  status,
                  provider_account_id,
                  account_display_name,
                  authorization_method,
                  credential_envelopes,
                  material_revision,
                  target_connection_id,
                  target_revision,
                  acknowledged_at,
                  provider_revocation_required,
                  created_at,
                  updated_at
                )
                values (
                  '{contender_id}',
                  '{context.tenant_id}',
                  '{context.workspace_id}',
                  '{AUTH_USER_ID}',
                  'flowaccount',
                  'sandbox',
                  '{permissions}'::pg_catalog.jsonb,
                  'finalized',
                  '{account_id}',
                  'FlowAccount Upgrade Company',
                  'oauth2_pkce',
                  '[]'::pg_catalog.jsonb,
                  1,
                  '{target_id}',
                  {revision_b},
                  null,
                  false,
                  '2026-07-28T00:20:00+00:00',
                  '2026-07-28T00:20:00+00:00'
                );
                """
            ),
        )
        _psql(container, head_migration)
        preserved = json.loads(
            _psql(
                container,
                _service(
                    f"""
                    select pg_catalog.json_build_object(
                      'owner', connection.oauth_generation_id,
                      'owner_acknowledged', owner.acknowledged_at is not null,
                      'contender_acknowledged', contender.acknowledged_at is not null
                    )::pg_catalog.text
                    from public.mercury_provider_connections as connection
                    join public.mercury_provider_oauth_attempts as owner
                      on owner.id = connection.oauth_generation_id
                    join public.mercury_provider_oauth_attempts as contender
                      on contender.id = '{contender_id}'
                    where connection.id = '{target_id}';
                    """
                ),
            )
        )
        assert preserved == {
            "owner": str(attempt_b_id),
            "owner_acknowledged": True,
            "contender_acknowledged": False,
        }

        fail_attempt(attempt_b_id)
        quarantined = json.loads(
            _psql(
                container,
                _service(
                    f"""
                    select pg_catalog.json_build_object(
                      'readiness', connection.readiness,
                      'revocation', connection.provider_revocation_required,
                      'connection_envelopes', connection.credential_envelope_ids,
                      'persisted_envelopes', (
                        select pg_catalog.count(*)
                        from public.mercury_provider_credential_envelopes
                        where connection_id = connection.id
                      ),
                      'attempt_status', attempt.status,
                      'retained_envelope', attempt.credential_envelopes -> 0 ->> 'id'
                    )::pg_catalog.text
                    from public.mercury_provider_connections as connection
                    join public.mercury_provider_oauth_attempts as attempt
                      on attempt.id = '{attempt_b_id}'
                    where connection.id = '{target_id}';
                    """
                ),
            )
        )
        assert quarantined == {
            "readiness": "disconnected",
            "revocation": True,
            "connection_envelopes": [],
            "persisted_envelopes": 0,
            "attempt_status": "failed",
            "retained_envelope": str(envelope_b_id),
        }
        assert listed_count(target_id) == 0
        _assert_secret_safe_error(
            load_result(target_id),
            "provider_connection_not_found",
        )
    finally:
        _docker("rm", "-f", container, check=False)
