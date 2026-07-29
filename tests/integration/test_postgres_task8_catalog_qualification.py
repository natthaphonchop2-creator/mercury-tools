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


def _definition(
    *,
    environment: str = "sandbox",
    case_name: str = "invoice",
) -> ProviderMCPQualification:
    return ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment=environment,
        provider_tool_name=f"get_{case_name}",
        normalized_capability=f"documents.{case_name}.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )


def _artifact(
    definition: ProviderMCPQualification,
    *,
    company_sha256: str,
    result_identifier: str = "result-postgres-001",
) -> QualificationArtifact:
    return build_qualification_artifact(
        definition=definition,
        company_sha256=company_sha256,
        runner_version="postgres-test-runner-v1",
        evaluated_at=NOW,
        input_sha256="b" * 64,
        sanitized_result_identifier=result_identifier,
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


def _attempt_publish(
    container: str,
    qualification: ProviderMCPQualification,
    artifact: QualificationArtifact | None = None,
) -> subprocess.CompletedProcess[str]:
    return _psql_result(
        container,
        _service(f"begin;\n{_publish_sql(qualification, artifact)}\nrollback;"),
    )


def _assert_closed_error(
    result: subprocess.CompletedProcess[str],
    identifier: str,
) -> None:
    assert result.returncode != 0
    assert f"ERROR:  {identifier}" in result.stderr
    assert "catalog://" not in result.stderr
    assert '"company_sha256"' not in result.stderr
    assert "f" * 64 not in result.stderr


def _publish_nonproduction(
    container: str,
    definition: ProviderMCPQualification,
    artifact: QualificationArtifact,
) -> ProviderMCPQualification:
    discovered = _publish(container, definition)
    schema_validated = _publish(
        container,
        transition_qualification(
            discovered,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        ),
    )
    return _publish(
        container,
        transition_qualification(
            schema_validated,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=artifact,
            now=NOW,
        ),
        artifact,
    )


def _canary(definition: ProviderMCPQualification) -> OwnerAuthorizedCanary:
    return OwnerAuthorizedCanary(
        provider="flowaccount",
        environment="production",
        normalized_capability=definition.normalized_capability,
        provider_tool_name=definition.provider_tool_name,
        capability_version_sha256=definition.capability_version_sha256,
        owner_authorized_by="workspace_owner",
        authorized_at=NOW,
    )


def _publish_production_nonproduction(
    container: str,
    *,
    case_name: str,
    sandbox_reference: tuple[
        ProviderMCPQualification,
        QualificationArtifact,
    ]
    | None = None,
) -> tuple[
    ProviderMCPQualification,
    QualificationArtifact,
    ProviderMCPQualification,
    QualificationArtifact,
]:
    if sandbox_reference is None:
        sandbox_definition = _definition(case_name=case_name)
        sandbox_artifact = _artifact(
            sandbox_definition,
            company_sha256="b" * 64,
            result_identifier=f"result-{case_name}-sandbox",
        )
        sandbox_nonproduction = _publish_nonproduction(
            container,
            sandbox_definition,
            sandbox_artifact,
        )
    else:
        sandbox_nonproduction, sandbox_artifact = sandbox_reference
    production_definition = _definition(
        environment="production",
        case_name=case_name,
    )
    production_artifact = _artifact(
        production_definition,
        company_sha256="d" * 64,
        result_identifier=f"result-{case_name}-production",
    )
    production_discovered = _publish(container, production_definition)
    production_schema = _publish(
        container,
        transition_qualification(
            production_discovered,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        ),
    )
    production_nonproduction = _publish(
        container,
        transition_qualification(
            production_schema,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=production_artifact,
            nonproduction_evidence=(sandbox_nonproduction,),
            nonproduction_artifacts=(sandbox_artifact,),
            now=NOW,
        ),
        production_artifact,
    )
    return (
        production_nonproduction,
        production_artifact,
        sandbox_nonproduction,
        sandbox_artifact,
    )


def _publish_production_enabled(
    container: str,
    *,
    case_name: str,
) -> ProviderMCPQualification:
    (
        production_nonproduction,
        production_artifact,
        sandbox_nonproduction,
        sandbox_artifact,
    ) = _publish_production_nonproduction(container, case_name=case_name)
    enabled = transition_qualification(
        production_nonproduction,
        QualificationState.ENABLED,
        evidence=production_artifact,
        nonproduction_evidence=(sandbox_nonproduction,),
        nonproduction_artifacts=(sandbox_artifact,),
        canary=_canary(production_nonproduction),
        now=NOW,
    )
    return _publish(container, enabled, production_artifact)


def _load_qualification(
    container: str,
    qualification_id: UUID,
) -> ProviderMCPQualification:
    payload = _psql(
        container,
        _service(
            "select (to_jsonb(qualification) - 'created_at' - 'updated_at')::text "
            f"from {_TABLE} as qualification where id = '{qualification_id}';"
        ),
    )
    return ProviderMCPQualification.model_validate_json(payload)


def _identity_swap(
    qualification: ProviderMCPQualification,
    field_name: str,
) -> object:
    if field_name == "qualification_evidence_uri":
        return (
            f"catalog://global/{qualification.provider}/qualifications/"
            f"{qualification.capability_version_sha256}-{'f' * 64}.json"
        )
    if field_name == "evidence_evaluated_at":
        assert qualification.evidence_evaluated_at is not None
        return qualification.evidence_evaluated_at + timedelta(seconds=1)
    if field_name == "evidence_expires_at":
        assert qualification.evidence_expires_at is not None
        return qualification.evidence_expires_at - timedelta(seconds=1)
    if field_name == "production_canary_at":
        assert qualification.production_canary_at is not None
        return qualification.production_canary_at - timedelta(seconds=1)
    if field_name == "owner_authorized_by":
        return "different_workspace_owner"
    return "f" * 64


@pytest.fixture(scope="module")
def reviewed_nonproduction(
    postgres_container: str,
) -> tuple[ProviderMCPQualification, QualificationArtifact]:
    definition = _definition(environment="identity-fields")
    artifact = _artifact(definition, company_sha256="b" * 64)
    return _publish_nonproduction(postgres_container, definition, artifact), artifact


@pytest.fixture(scope="module")
def production_nonproduction(
    postgres_container: str,
) -> tuple[
    ProviderMCPQualification,
    QualificationArtifact,
    ProviderMCPQualification,
    QualificationArtifact,
]:
    return _publish_production_nonproduction(
        postgres_container,
        case_name="production_reference_fields",
    )


@pytest.fixture(scope="module")
def production_enabled(
    postgres_container: str,
) -> ProviderMCPQualification:
    return _publish_production_enabled(
        postgres_container,
        case_name="terminal_identity_fields",
    )


def test_enablement_rejects_replacement_evidence_artifact_like_python(
    postgres_container: str,
) -> None:
    definition = _definition(environment="replacement-red")
    discovered = _publish(postgres_container, definition)
    schema_validated = _publish(
        postgres_container,
        transition_qualification(
            discovered,
            QualificationState.SCHEMA_VALIDATED,
            now=NOW,
        ),
    )
    artifact_a = _artifact(definition, company_sha256="b" * 64)
    nonproduction = _publish(
        postgres_container,
        transition_qualification(
            schema_validated,
            QualificationState.NONPRODUCTION_QUALIFIED,
            evidence=artifact_a,
            now=NOW,
        ),
        artifact_a,
    )
    artifact_b = _artifact(definition, company_sha256="c" * 64)
    replacement = nonproduction.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": artifact_b.company_sha256,
            "evidence_revision_sha256": artifact_b.evidence_revision_sha256,
            "qualification_evidence_uri": artifact_b.catalog_uri,
            "evidence_evaluated_at": artifact_b.evaluated_at,
            "evidence_expires_at": artifact_b.evidence_expires_at,
        }
    )

    with pytest.raises(
        ValueError,
        match="^qualification_evidence_company_mismatch$",
    ):
        transition_qualification(
            nonproduction,
            QualificationState.ENABLED,
            evidence=artifact_b,
            now=NOW,
        )

    result = _attempt_publish(postgres_container, replacement, artifact_b)

    _assert_closed_error(
        result,
        "mercury_provider_capability_evidence_identity_mismatch",
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "company_sha256",
        "evidence_revision_sha256",
        "qualification_evidence_uri",
        "evidence_evaluated_at",
        "evidence_expires_at",
    ],
)
def test_enablement_rejects_each_reviewed_evidence_identity_swap(
    postgres_container: str,
    reviewed_nonproduction: tuple[ProviderMCPQualification, QualificationArtifact],
    field_name: str,
) -> None:
    qualification, artifact = reviewed_nonproduction
    python_enabled = transition_qualification(
        qualification,
        QualificationState.ENABLED,
        evidence=artifact,
        now=NOW,
    )
    candidate = qualification.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            field_name: _identity_swap(qualification, field_name),
        }
    )

    result = _attempt_publish(postgres_container, candidate, artifact)

    assert getattr(python_enabled, field_name) == getattr(qualification, field_name)
    _assert_closed_error(
        result,
        "mercury_provider_capability_evidence_identity_mismatch",
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "nonproduction_evidence_revision_sha256",
        "nonproduction_company_sha256",
    ],
)
def test_production_enablement_rejects_each_reviewed_nonproduction_reference_swap(
    postgres_container: str,
    production_nonproduction: tuple[
        ProviderMCPQualification,
        QualificationArtifact,
        ProviderMCPQualification,
        QualificationArtifact,
    ],
    field_name: str,
) -> None:
    qualification, artifact, sandbox, sandbox_artifact = production_nonproduction
    python_enabled = transition_qualification(
        qualification,
        QualificationState.ENABLED,
        evidence=artifact,
        nonproduction_evidence=(sandbox,),
        nonproduction_artifacts=(sandbox_artifact,),
        canary=_canary(qualification),
        now=NOW,
    )
    candidate = qualification.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "production_canary_at": NOW,
            "owner_authorized_by": "workspace_owner",
            field_name: _identity_swap(qualification, field_name),
        }
    )

    result = _attempt_publish(postgres_container, candidate, artifact)

    assert getattr(python_enabled, field_name) == getattr(qualification, field_name)
    _assert_closed_error(
        result,
        "mercury_provider_capability_evidence_identity_mismatch",
    )


def test_production_enablement_rejects_different_valid_nonproduction_reference_like_python(
    postgres_container: str,
) -> None:
    case_name = "production_reference_replacement"
    sandbox_definition = _definition(case_name=case_name)
    sandbox_artifact_a = _artifact(
        sandbox_definition,
        company_sha256="b" * 64,
        result_identifier="result-production-reference-a",
    )
    sandbox_a = _publish_nonproduction(
        postgres_container,
        sandbox_definition,
        sandbox_artifact_a,
    )
    sandbox_artifact_b = _artifact(
        sandbox_definition,
        company_sha256="b" * 64,
        result_identifier="result-production-reference-b",
    )
    sandbox_b = _publish_nonproduction(
        postgres_container,
        sandbox_definition,
        sandbox_artifact_b,
    )
    production, production_artifact, _, _ = _publish_production_nonproduction(
        postgres_container,
        case_name=case_name,
        sandbox_reference=(sandbox_a, sandbox_artifact_a),
    )

    with pytest.raises(ValueError, match="^nonproduction_evidence_required$"):
        transition_qualification(
            production,
            QualificationState.ENABLED,
            evidence=production_artifact,
            nonproduction_evidence=(sandbox_b,),
            nonproduction_artifacts=(sandbox_artifact_b,),
            canary=_canary(production),
            now=NOW,
        )

    candidate = production.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "nonproduction_evidence_revision_sha256": sandbox_b.evidence_revision_sha256,
            "nonproduction_company_sha256": sandbox_b.company_sha256,
            "production_canary_at": NOW,
            "owner_authorized_by": "workspace_owner",
        }
    )
    result = _attempt_publish(postgres_container, candidate, production_artifact)

    assert sandbox_a.evidence_revision_sha256 != sandbox_b.evidence_revision_sha256
    assert sandbox_a.evidence_revision_sha256 == production.nonproduction_evidence_revision_sha256
    _assert_closed_error(
        result,
        "mercury_provider_capability_evidence_identity_mismatch",
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "company_sha256",
        "evidence_revision_sha256",
        "qualification_evidence_uri",
        "evidence_evaluated_at",
        "evidence_expires_at",
        "nonproduction_evidence_revision_sha256",
        "nonproduction_company_sha256",
        "production_canary_at",
        "owner_authorized_by",
    ],
)
@pytest.mark.parametrize(
    "target_state",
    [QualificationState.DISABLED, QualificationState.SUPERSEDED],
)
def test_terminal_transition_rejects_each_preserved_identity_swap(
    postgres_container: str,
    production_enabled: ProviderMCPQualification,
    field_name: str,
    target_state: QualificationState,
) -> None:
    python_terminal = transition_qualification(
        production_enabled,
        target_state,
        disable_reason="reviewed_regression",
        now=NOW,
    )
    candidate = production_enabled.model_copy(
        update={
            "qualification_state": target_state,
            "disable_reason": "reviewed_regression",
            field_name: _identity_swap(production_enabled, field_name),
        }
    )

    result = _attempt_publish(postgres_container, candidate)

    assert getattr(python_terminal, field_name) == getattr(production_enabled, field_name)
    _assert_closed_error(
        result,
        "mercury_provider_capability_terminal_invalid",
    )


@pytest.mark.parametrize(
    "target_state",
    [QualificationState.DISABLED, QualificationState.SUPERSEDED],
)
def test_terminal_transition_preserves_identity_and_updates_only_allowed_fields(
    postgres_container: str,
    target_state: QualificationState,
) -> None:
    enabled = _publish_production_enabled(
        postgres_container,
        case_name=f"terminal_positive_{target_state.value}",
    )
    terminal = transition_qualification(
        enabled,
        target_state,
        disable_reason="reviewed_regression",
        now=NOW,
    )

    persisted = _publish(postgres_container, terminal)
    reloaded = _load_qualification(postgres_container, persisted.id)

    assert reloaded == persisted
    assert reloaded.qualification_state is target_state
    assert reloaded.disable_reason == "reviewed_regression"


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
