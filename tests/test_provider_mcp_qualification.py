from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from mercury_tools.catalog.models import ProviderMCPQualification
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.streamable_mcp import ProviderOperationDeadline
from mercury_tools.qualification.artifacts import (
    build_qualification_artifact,
    load_catalog_qualification_artifact,
    load_qualification_artifact,
    write_qualification_artifact,
)
from mercury_tools.qualification.provider_mcp import (
    CapabilityQualificationGate,
    CapabilitySelection,
    CatalogQualificationResolver,
    OwnerAuthorizedCanary,
    transition_qualification,
)

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


def _definition() -> ProviderMCPQualification:
    return ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="get_invoice",
        normalized_capability="documents.invoice.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )


def _artifact(definition: ProviderMCPQualification):
    return build_qualification_artifact(
        definition=definition,
        company_sha256="b" * 64,
        runner_version="test-runner-v1",
        evaluated_at=NOW,
        input_sha256="c" * 64,
        sanitized_result_identifier="result_test_001",
        checks={"schema_matches": True, "response_shape_matches": True},
        reviewer="release_reviewer",
        evidence_expires_at=NOW + timedelta(days=1),
        passed=True,
    )


def test_artifact_is_sanitized_version_bound_and_written_once(tmp_path: Path) -> None:
    definition = _definition()
    artifact = _artifact(definition)

    path = write_qualification_artifact(tmp_path, artifact)

    assert path == (tmp_path / "flowaccount" / "qualifications" / artifact.filename)
    assert load_qualification_artifact(path) == artifact
    assert write_qualification_artifact(tmp_path, artifact) == path
    serialized = path.read_text(encoding="utf-8")
    for forbidden in ("access_token", "raw_response", "accounting_body", "Bearer "):
        assert forbidden not in serialized


def test_artifact_rejects_unknown_or_malformed_content(tmp_path: Path) -> None:
    payload = _artifact(_definition()).model_dump(mode="json")
    payload["raw_response"] = {}
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="^qualification_artifact_invalid$"):
        load_qualification_artifact(path)

    payload.pop("raw_response")
    payload["sanitized_result_identifier"] = "invalid identifier"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="^qualification_artifact_invalid$"):
        load_qualification_artifact(path)


def test_artifact_writer_rejects_parent_and_target_symlink_escapes(tmp_path: Path) -> None:
    artifact = _artifact(_definition())
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / artifact.provider).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="^qualification_artifact_path_invalid$"):
        write_qualification_artifact(tmp_path, artifact)
    with pytest.raises(ValueError, match="^qualification_artifact_path_invalid$"):
        load_catalog_qualification_artifact(tmp_path, artifact.catalog_uri)

    (tmp_path / artifact.provider).unlink()
    target_dir = tmp_path / artifact.provider / "qualifications"
    target_dir.mkdir(parents=True)
    target = target_dir / artifact.filename
    target.symlink_to(outside / "artifact.json")

    with pytest.raises(ValueError, match="^qualification_artifact_path_invalid$"):
        write_qualification_artifact(tmp_path, artifact)
    with pytest.raises(ValueError, match="^qualification_artifact_path_invalid$"):
        load_catalog_qualification_artifact(tmp_path, artifact.catalog_uri)


def test_same_capability_can_create_an_immutable_new_evidence_revision(tmp_path: Path) -> None:
    definition = _definition()
    first = _artifact(definition)
    renewed = build_qualification_artifact(
        definition=definition,
        company_sha256="b" * 64,
        runner_version="runner-v1",
        evaluated_at=NOW + timedelta(days=1),
        input_sha256="e" * 64,
        sanitized_result_identifier="result-002",
        checks={"schema": True, "permission": True},
        reviewer="reviewer-1",
        evidence_expires_at=NOW + timedelta(days=8),
        passed=True,
    )

    first_path = write_qualification_artifact(tmp_path, first)
    renewed_path = write_qualification_artifact(tmp_path, renewed)

    assert first_path != renewed_path
    assert load_qualification_artifact(first_path) == first
    assert load_qualification_artifact(renewed_path) == renewed


def test_artifact_rejects_expired_or_definition_mismatched_evidence() -> None:
    definition = _definition()
    artifact = _artifact(definition)

    with pytest.raises(ValueError, match="^qualification_evidence_expired$"):
        artifact.require_valid_for(definition, now=NOW + timedelta(days=2))
    with pytest.raises(ValueError, match="^qualification_evidence_mismatch$"):
        artifact.require_valid_for(
            ProviderMCPQualification.discovered(
                provider="flowaccount",
                environment="sandbox",
                provider_tool_name="get_invoice",
                normalized_capability="documents.invoice.get",
                input_schema={"type": "object", "properties": {"id": {"type": "string"}}},
                output_schema={"type": "object", "properties": {}},
                response_shape_hash="a" * 64,
                required_permissions=("documents.read",),
            ),
            now=NOW,
        )


def test_peak_profile_seed_is_not_artifact_evidence_without_a_reviewed_fixture() -> None:
    peak_profile = ProviderMCPQualification.discovered(
        provider="peak",
        environment="uat",
        provider_tool_name="get_provider_profile",
        normalized_capability="provider_profile.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        response_shape_hash="a" * 64,
        required_permissions=("profile.read",),
    )

    assert peak_profile.qualification_evidence_uri is None
    assert (
        CapabilityQualificationGate([peak_profile])
        .resolve(
            CapabilitySelection(
                provider="peak",
                environment="uat",
                normalized_capability="provider_profile.get",
                provider_tool_name="get_provider_profile",
                capability_version_sha256=peak_profile.capability_version_sha256,
            ),
            company_sha256="a" * 64,
        )
        .status
        == "insufficient_evidence"
    )


def test_qualification_cli_writes_only_controlled_sanitized_artifacts(
    tmp_path: Path,
) -> None:
    module_path = ROOT / "scripts" / "qualify_provider_mcp.py"
    spec = importlib.util.spec_from_file_location("qualify_provider_mcp", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    artifact = _artifact(_definition())
    payload = tmp_path / "controlled-input.json"
    payload.write_text(artifact.model_dump_json(), encoding="utf-8")

    assert module.main(["--catalog-root", str(tmp_path), "--input", str(payload)]) == 0
    assert (tmp_path / "flowaccount" / "qualifications" / artifact.filename).is_file()


@pytest.mark.asyncio
async def test_catalog_resolver_status_uses_the_same_connection_bound_gate_as_execution(
    tmp_path: Path,
) -> None:
    definition = _definition()
    company_id = "company-enabled"
    artifact = build_qualification_artifact(
        definition=definition,
        company_sha256=hashlib.sha256(company_id.encode("utf-8")).hexdigest(),
        runner_version="test-runner-v1",
        evaluated_at=NOW,
        input_sha256="c" * 64,
        sanitized_result_identifier="result_test_001",
        checks={"schema_matches": True, "response_shape_matches": True},
        reviewer="release_reviewer",
        evidence_expires_at=NOW + timedelta(days=1),
        passed=True,
    )
    schema_validated = transition_qualification(
        definition,
        target_state="schema_validated",
        now=NOW,
    )
    nonproduction = transition_qualification(
        schema_validated,
        target_state="nonproduction_qualified",
        evidence=artifact,
        now=NOW,
    )
    enabled = transition_qualification(
        nonproduction,
        target_state="enabled",
        evidence=artifact,
        now=NOW,
    )
    write_qualification_artifact(tmp_path, artifact)

    class Catalog:
        def __init__(self, qualifications: tuple[ProviderMCPQualification, ...]) -> None:
            self.qualifications = qualifications

        def list_provider_mcp_qualifications(self) -> tuple[ProviderMCPQualification, ...]:
            return self.qualifications

    connection = ProviderConnection(
        id=UUID("11111111-1111-4111-8111-111111111111"),
        tenant_id=UUID("22222222-2222-4222-8222-222222222222"),
        workspace_id=UUID("33333333-3333-4333-8333-333333333333"),
        auth_user_id=UUID("44444444-4444-4444-8444-444444444444"),
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        provider_account_id="company-mismatch",
        account_display_name="Sanitized Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read",),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=NOW,
        credential_envelope_ids=(UUID("55555555-5555-4555-8555-555555555555"),),
        created_at=NOW,
        updated_at=NOW,
    )
    selection = CapabilitySelection(
        provider=enabled.provider,
        environment=enabled.environment,
        normalized_capability=enabled.normalized_capability,
        provider_tool_name=enabled.provider_tool_name,
        capability_version_sha256=enabled.capability_version_sha256,
    )

    mismatch_resolver = CatalogQualificationResolver(
        catalog=Catalog((enabled,)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    mismatch = await mismatch_resolver.resolve_for_connection(
        connection,
        selection=selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert mismatch.status == "insufficient_evidence"

    expired_resolver = CatalogQualificationResolver(
        catalog=Catalog((enabled,)),
        catalog_root=str(tmp_path),
        now=lambda: NOW + timedelta(days=2),
    )
    matching_connection = connection.model_copy(
        update={"provider_account_id": company_id}
    )
    expired = await expired_resolver.resolve_for_connection(
        matching_connection,
        selection=selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert expired.status == "insufficient_evidence"

    disabled = transition_qualification(
        enabled,
        target_state="disabled",
        disable_reason="reviewed_regression",
        now=NOW,
    )
    disabled_resolver = CatalogQualificationResolver(
        catalog=Catalog((disabled,)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    terminal = await disabled_resolver.resolve_for_connection(
        matching_connection,
        selection=selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert terminal.status == "capability_unavailable"

    superseded = transition_qualification(
        enabled,
        target_state="superseded",
        disable_reason="replaced_by_reviewed_version",
        now=NOW,
    )
    superseded_resolver = CatalogQualificationResolver(
        catalog=Catalog((superseded,)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    superseded_resolution = await superseded_resolver.resolve_for_connection(
        matching_connection,
        selection=selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert superseded_resolution.status == "capability_unavailable"

    production_definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="production",
        provider_tool_name="get_invoice",
        normalized_capability="documents.invoice.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {"id": {"type": "string"}}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    production_artifact = build_qualification_artifact(
        definition=production_definition,
        company_sha256=hashlib.sha256(company_id.encode("utf-8")).hexdigest(),
        runner_version="test-runner-v1",
        evaluated_at=NOW,
        input_sha256="d" * 64,
        sanitized_result_identifier="result_test_002",
        checks={"schema_matches": True, "response_shape_matches": True},
        reviewer="release_reviewer",
        evidence_expires_at=NOW + timedelta(days=1),
        passed=True,
    )
    production_schema = transition_qualification(
        production_definition,
        target_state="schema_validated",
        now=NOW,
    )
    production_nonproduction = transition_qualification(
        production_schema,
        target_state="nonproduction_qualified",
        evidence=production_artifact,
        nonproduction_evidence=(enabled,),
        nonproduction_artifacts=(artifact,),
        now=NOW,
    )
    production_enabled = transition_qualification(
        production_nonproduction,
        target_state="enabled",
        evidence=production_artifact,
        nonproduction_evidence=(enabled,),
        nonproduction_artifacts=(artifact,),
        canary=OwnerAuthorizedCanary(
            provider="flowaccount",
            environment="production",
            normalized_capability=production_definition.normalized_capability,
            provider_tool_name=production_definition.provider_tool_name,
            capability_version_sha256=production_definition.capability_version_sha256,
            owner_authorized_by="release_reviewer",
            authorized_at=NOW,
        ),
        now=NOW,
    )
    write_qualification_artifact(tmp_path, production_artifact)
    production_connection = matching_connection.model_copy(update={"environment": "production"})
    production_selection = CapabilitySelection(
        provider=production_enabled.provider,
        environment=production_enabled.environment,
        normalized_capability=production_enabled.normalized_capability,
        provider_tool_name=production_enabled.provider_tool_name,
        capability_version_sha256=production_enabled.capability_version_sha256,
    )
    production_resolver = CatalogQualificationResolver(
        catalog=Catalog((enabled, production_enabled)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    production = await production_resolver.resolve_for_connection(
        production_connection,
        selection=production_selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert production.status == "enabled"

    missing_reference_resolver = CatalogQualificationResolver(
        catalog=Catalog((production_enabled,)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    missing_reference = await missing_reference_resolver.resolve_for_connection(
        production_connection,
        selection=production_selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert missing_reference.status == "insufficient_evidence"

    future_canary = production_enabled.model_copy(
        update={"production_canary_at": NOW + timedelta(seconds=1)}
    )
    future_canary_resolver = CatalogQualificationResolver(
        catalog=Catalog((enabled, future_canary)),
        catalog_root=str(tmp_path),
        now=lambda: NOW,
    )
    future_canary_resolution = await future_canary_resolver.resolve_for_connection(
        production_connection,
        selection=production_selection,
        deadline=ProviderOperationDeadline.start(5),
    )
    assert future_canary_resolution.status == "insufficient_evidence"
