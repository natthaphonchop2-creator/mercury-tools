from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = (
    ROOT / "supabase/migrations/20260711090000_erp_action_catalog.sql",
    ROOT / "supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql",
)
_OPT_IN = "MERCURY_V1_POSTGRES_TEST"
_TABLE = "public.mercury_provider_capability_qualifications"
_EVIDENCE_URI = f"catalog://global/flowaccount/qualifications/{'a' * 64}.json"


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "info"], check=False, capture_output=True, text=True
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def _docker(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        check=False,
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
        "mercury_task8_test",
        input_text=sql,
    )


def _psql(container: str, sql: str) -> str:
    result = _psql_result(container, sql)
    if result.returncode != 0:
        raise AssertionError(result.stderr.strip())
    return result.stdout.strip()


def _service(sql: str) -> str:
    return f"set role service_role;\n{sql}"


@pytest.fixture(scope="module")
def postgres_container() -> str:
    if os.environ.get(_OPT_IN) != "1":
        pytest.skip(f"set {_OPT_IN}=1 to run disposable PostgreSQL regression")
    if not _docker_available():
        pytest.skip("Docker is unavailable for disposable PostgreSQL regression")
    container = f"mercury-task8-postgres-{uuid4().hex[:12]}"
    started = _docker(
        "run",
        "--rm",
        "-d",
        "--name",
        container,
        "-e",
        "POSTGRES_PASSWORD=postgres",
        "-e",
        "POSTGRES_DB=mercury_task8_test",
        "postgres:17-alpine",
    )
    assert started.returncode == 0, started.stderr
    try:
        for _ in range(120):
            if (
                _docker(
                    "exec",
                    container,
                    "psql",
                    "-qAt",
                    "-U",
                    "postgres",
                    "-d",
                    "mercury_task8_test",
                    "-c",
                    "select 1",
                ).stdout.strip()
                == "1"
            ):
                break
            time.sleep(0.25)
        else:
            pytest.fail("disposable PostgreSQL did not become ready")
        _psql(
            container,
            """
            create role anon nologin;
            create role authenticated nologin;
            create role service_role nologin bypassrls;
            """,
        )
        for _ in range(2):
            for migration in MIGRATIONS:
                _psql(container, migration.read_text(encoding="utf-8"))
        yield container
    finally:
        _docker("rm", "-f", container)


def _insert_discovered(*, environment: str = "sandbox") -> str:
    version = "a" * 64 if environment == "sandbox" else "b" * 64
    return f"""
        insert into public.mercury_provider_capability_qualifications (
          provider, environment, provider_tool_name, normalized_capability,
          input_schema, output_schema, schema_hash, response_shape_hash,
          required_permissions, capability_version_sha256, qualification_state
        ) values (
          'flowaccount', '{environment}', 'get_invoice', 'documents.invoice.get',
          '{{}}'::jsonb, '{{}}'::jsonb, '{"c" * 64}', '{"d" * 64}',
          '["documents.read"]'::jsonb, '{version}', 'discovered_unreviewed'
        ) returning id;
    """


def _transition_to_nonproduction(row_id: str) -> str:
    return f"""
        update public.mercury_provider_capability_qualifications
        set qualification_state = 'schema_validated'
        where id = '{row_id}';
        update public.mercury_provider_capability_qualifications
        set qualification_state = 'nonproduction_qualified',
            qualification_evidence_uri = '{_EVIDENCE_URI}',
            evidence_expires_at = statement_timestamp() + interval '1 day'
        where id = '{row_id}';
    """


def test_migration_applies_twice_and_keeps_public_access_closed(
    postgres_container: str,
) -> None:
    access = _psql(
        postgres_container,
        f"""
        select concat_ws(
          '|',
          has_table_privilege('authenticated', '{_TABLE}', 'select'),
          has_table_privilege('anon', '{_TABLE}', 'select'),
          has_table_privilege('service_role', '{_TABLE}', 'select'),
          has_table_privilege('service_role', '{_TABLE}', 'delete')
        );
        """,
    )

    assert access == "f|f|t|f"


def test_database_enforces_immutable_versions_transitions_and_production_evidence(
    postgres_container: str,
) -> None:
    sandbox_id = _psql(postgres_container, _service(_insert_discovered()))
    direct_enable = _psql_result(
        postgres_container,
        _service(
            """
            insert into public.mercury_provider_capability_qualifications (
              provider, environment, provider_tool_name, normalized_capability,
              input_schema, output_schema, schema_hash, response_shape_hash,
              required_permissions, capability_version_sha256, qualification_state,
              qualification_evidence_uri, evidence_expires_at
            ) values (
              'flowaccount', 'sandbox', 'list_invoices', 'documents.invoice.list',
              '{}'::jsonb, '{}'::jsonb, repeat('e', 64), repeat('f', 64),
              '["documents.read"]'::jsonb, repeat('1', 64), 'enabled',
              'catalog://global/flowaccount/qualifications/1111111111111111111111111111111111111111111111111111111111111111.json',
              statement_timestamp() + interval '1 day'
            );
            """
        ),
    )

    assert direct_enable.returncode != 0
    assert "mercury_provider_capability_initial_state_invalid" in direct_enable.stderr
    _psql(postgres_container, _service(_transition_to_nonproduction(sandbox_id)))
    _psql(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'enabled'
            where id = '{sandbox_id}';
            """
        ),
    )
    immutable = _psql_result(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set input_schema = '{{"changed": true}}'::jsonb
            where id = '{sandbox_id}';
            """
        ),
    )
    assert immutable.returncode != 0
    assert "mercury_provider_capability_versions_are_immutable" in immutable.stderr

    wrong_uri_id = _psql(
        postgres_container,
        _service(_insert_discovered(environment="uat")),
    )
    wrong_uri = _psql_result(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'schema_validated'
            where id = '{wrong_uri_id}';
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'nonproduction_qualified',
                qualification_evidence_uri = 'catalog://global/peak/qualifications/{"b" * 64}.json',
                evidence_expires_at = statement_timestamp() + interval '1 day'
            where id = '{wrong_uri_id}';
            """
        ),
    )
    assert wrong_uri.returncode != 0

    production_id = _psql(
        postgres_container,
        _service(_insert_discovered(environment="production")),
    )
    _psql(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'schema_validated'
            where id = '{production_id}';
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'nonproduction_qualified',
                qualification_evidence_uri = '{_EVIDENCE_URI}',
                evidence_expires_at = statement_timestamp() + interval '1 day'
            where id = '{production_id}';
            """
        ),
    )
    missing_canary = _psql_result(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'enabled'
            where id = '{production_id}';
            """
        ),
    )
    assert missing_canary.returncode != 0
    _psql(
        postgres_container,
        _service(
            f"""
            update public.mercury_provider_capability_qualifications
            set qualification_state = 'enabled',
                production_canary_at = statement_timestamp(),
                owner_authorized_by = 'workspace_owner'
            where id = '{production_id}';
            """
        ),
    )
