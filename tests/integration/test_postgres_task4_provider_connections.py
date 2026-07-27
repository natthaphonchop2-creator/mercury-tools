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
