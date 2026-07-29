from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.qualification.artifacts import (
    QualificationArtifact,
    build_qualification_artifact,
)
from mercury_tools.qualification.provider_mcp import (
    OwnerAuthorizedCanary,
    transition_qualification,
)

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = (
    ROOT / "supabase/migrations/20260711090000_erp_action_catalog.sql",
    ROOT / "supabase/migrations/20260726103000_mercury_v1_catalog_qualification.sql",
)
_OPT_IN = "MERCURY_V1_POSTGRES_TEST"
_TABLE = "public.mercury_provider_capability_qualifications"
# PostgreSQL validates evidence against its statement timestamp, so keep controlled
# evidence narrowly in the past while preserving deterministic relationships.
NOW = datetime.now(UTC) - timedelta(minutes=1)


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
        migration_sql = "\n".join(migration.read_text(encoding="utf-8") for migration in MIGRATIONS)
        for _ in range(2):
            _psql(container, migration_sql)
        yield container
    finally:
        _docker("rm", "-f", container)


def _definition(*, environment: str = "sandbox") -> ProviderMCPQualification:
    return ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment=environment,
        provider_tool_name="get_invoice",
        normalized_capability="documents.invoice.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )


def _artifact(
    definition: ProviderMCPQualification,
    *,
    company_sha256: str,
) -> QualificationArtifact:
    return build_qualification_artifact(
        definition=definition,
        company_sha256=company_sha256,
        runner_version="postgres-test-runner-v1",
        evaluated_at=NOW,
        input_sha256="b" * 64,
        sanitized_result_identifier="result-postgres-001",
        checks={"schema": True, "permission": True},
        reviewer="release-reviewer",
        evidence_expires_at=NOW + timedelta(days=7),
        passed=True,
    )


def _publish_sql(
    qualification: ProviderMCPQualification,
    artifact: QualificationArtifact | None = None,
) -> str:
    payload = json.dumps(qualification.model_dump(mode="json"), sort_keys=True)
    artifact_payload = "null" if artifact is None else json.dumps(artifact.model_dump(mode="json"))
    return (
        "select public.publish_mercury_provider_capability_qualification("
        f"$qualification${payload}$qualification$::jsonb, "
        f"$artifact${artifact_payload}$artifact$::jsonb"
        ");"
    )


def _publish(
    container: str,
    qualification: ProviderMCPQualification,
    artifact: QualificationArtifact | None = None,
) -> ProviderMCPQualification:
    identifier = UUID(_psql(container, _service(_publish_sql(qualification, artifact))))
    return qualification.model_copy(update={"id": identifier})


def test_migration_applies_twice_and_keeps_direct_mutation_closed(
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
          has_table_privilege('service_role', '{_TABLE}', 'insert')
        );
        """,
    )
    direct = _psql_result(
        postgres_container,
        _service(
            """
            insert into public.mercury_provider_capability_qualifications (
              provider, environment, provider_tool_name, normalized_capability,
              input_schema, output_schema, schema_hash, response_shape_hash,
              required_permissions, capability_version_sha256, qualification_state
            ) values (
              'flowaccount', 'sandbox', 'get_invoice', 'documents.invoice.get',
              '{}'::jsonb, '{}'::jsonb, repeat('a', 64), repeat('a', 64),
              '["documents.read"]'::jsonb, repeat('a', 64), 'discovered_unreviewed'
            );
            """
        ),
    )

    assert access == "f|f|t|f"
    assert direct.returncode != 0
    assert "permission denied" in direct.stderr


def test_publisher_enforces_computed_hashes_permissions_lifecycle_and_exact_production_reference(
    postgres_container: str,
) -> None:
    sandbox_definition = _definition()
    bad_hash = sandbox_definition.model_copy(update={"schema_hash": "c" * 64})
    bad_hash_result = _psql_result(postgres_container, _service(_publish_sql(bad_hash)))
    bad_permissions_payload = sandbox_definition.model_dump(mode="json")
    bad_permissions_payload["required_permissions"] = ["documents.read", "documents.read"]
    bad_permissions_result = _psql_result(
        postgres_container,
        _service(
            "select public.publish_mercury_provider_capability_qualification("
            f"$qualification${json.dumps(bad_permissions_payload)}$qualification$::jsonb, null);"
        ),
    )

    assert bad_hash_result.returncode != 0
    assert "mercury_provider_capability_schema_hash_invalid" in bad_hash_result.stderr
    assert bad_permissions_result.returncode != 0
    assert "mercury_provider_capability_permissions_invalid" in bad_permissions_result.stderr

    sandbox_discovered = _publish(postgres_container, sandbox_definition)
    sandbox_schema = _publish(
        postgres_container,
        transition_qualification(
            sandbox_discovered,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        ),
    )
    sandbox_artifact = _artifact(sandbox_definition, company_sha256="b" * 64)
    sandbox_nonproduction = _publish(
        postgres_container,
        transition_qualification(
            sandbox_schema,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=sandbox_artifact,
            now=NOW,
        ),
        sandbox_artifact,
    )
    sandbox_enabled = _publish(
        postgres_container,
        transition_qualification(
            sandbox_nonproduction,
            QualificationState.ENABLED,
            evidence=sandbox_artifact,
            now=NOW,
        ),
        sandbox_artifact,
    )

    production_definition = _definition(environment="production")
    production_discovered = _publish(postgres_container, production_definition)
    production_schema = _publish(
        postgres_container,
        transition_qualification(
            production_discovered,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        ),
    )
    production_artifact = _artifact(production_definition, company_sha256="d" * 64)
    production_nonproduction = _publish(
        postgres_container,
        transition_qualification(
            production_schema,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=production_artifact,
            nonproduction_evidence=(sandbox_enabled,),
            nonproduction_artifacts=(sandbox_artifact,),
            now=NOW,
        ),
        production_artifact,
    )
    no_canary_payload = production_nonproduction.model_dump(mode="json")
    no_canary_payload["qualification_state"] = QualificationState.ENABLED.value
    no_canary = _psql_result(
        postgres_container,
        _service(
            "select public.publish_mercury_provider_capability_qualification("
            f"$qualification${json.dumps(no_canary_payload)}$qualification$::jsonb, "
            f"$artifact${json.dumps(production_artifact.model_dump(mode='json'))}$artifact$::jsonb"
            ");"
        ),
    )
    assert no_canary.returncode != 0

    production_enabled = transition_qualification(
        production_nonproduction,
        QualificationState.ENABLED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_enabled,),
        nonproduction_artifacts=(sandbox_artifact,),
        canary=OwnerAuthorizedCanary(
            provider="flowaccount",
            environment="production",
            normalized_capability=production_definition.normalized_capability,
            provider_tool_name=production_definition.provider_tool_name,
            capability_version_sha256=production_definition.capability_version_sha256,
            owner_authorized_by="workspace_owner",
            authorized_at=NOW,
        ),
        now=NOW,
    )
    persisted = _publish(postgres_container, production_enabled, production_artifact)

    assert persisted.id is not None
    assert (
        persisted.nonproduction_evidence_revision_sha256 == sandbox_enabled.evidence_revision_sha256
    )
    assert persisted.nonproduction_company_sha256 == sandbox_enabled.company_sha256
