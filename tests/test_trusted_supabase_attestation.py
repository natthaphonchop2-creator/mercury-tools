from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from mercury_tools.release.models import (
    EXPECTED_SURFACE_SCANNER_VERSIONS,
    GateStatus,
    SurfaceAttestation,
)
from mercury_tools.release.scanner import ReleaseGateError
from mercury_tools.release.trusted_attestation import (
    CANONICAL_SUPABASE_STORAGE_BUCKETS,
    CANONICAL_SUPABASE_TABLES,
    REQUIRED_SUPABASE_FUNCTIONS,
    REQUIRED_SUPABASE_MIGRATION,
    TRUSTED_RELEASE_CONTROL_SURFACES,
    TrustedFlowAccountCoverage,
    TrustedFunctionDefinition,
    TrustedHostedReleaseAttestation,
    TrustedReleaseControlPreflight,
    TrustedReleaseControlProducer,
    TrustedRenderRelease,
    TrustedStagingRelease,
    TrustedSupabaseProductionState,
    build_supabase_schema_digest,
    load_trusted_hosted_release_attestation,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "release-control" / "scaffold" / "scripts"


def _assembler() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "assemble_trusted_attestation_timestamp_tests",
        SCRIPTS / "assemble_trusted_attestation.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS))
    return module


def _surface_evidence(
    assembler: ModuleType,
    *,
    started_at: str,
    completed_at: str,
) -> list[dict[str, object]]:
    return [
        {
            "surface": surface,
            "status": "passed",
            "scanner_versions": (
                ["1.0.0", "3.88.32", "8.24.3"]
                if surface in {"git_all_refs", "github_pull_request_refs"}
                else ["1.0.0"]
            ),
            "started_at": started_at,
            "completed_at": completed_at,
            "finding_count": 0,
            "evidence_hashes": [_sha(surface)],
            "exit_codes": [0],
            "blocker_codes": [],
            "finding_codes": [],
        }
        for surface in assembler.TRUSTED_SURFACES
    ]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _functions() -> tuple[TrustedFunctionDefinition, ...]:
    return tuple(
        TrustedFunctionDefinition(signature=signature, definition_sha256=_sha(signature))
        for signature in REQUIRED_SUPABASE_FUNCTIONS
    )


def test_trusted_supabase_inventory_pins_runtime_search_and_resolution_rpcs() -> None:
    assert "public.match_knowledge_chunks(" in "\n".join(REQUIRED_SUPABASE_FUNCTIONS)
    assert (
        "public.resolve_erp_action_validation_batch(jsonb,timestamp with time zone)"
        in REQUIRED_SUPABASE_FUNCTIONS
    )


def _attestation(
    *,
    completed_at: datetime | None = None,
) -> TrustedHostedReleaseAttestation:
    completed = completed_at or datetime.now(UTC)
    functions = _functions()
    supabase = TrustedSupabaseProductionState(
        project_ref="a" * 20,
        project_ref_sha256=_sha("a" * 20),
        migration_id=REQUIRED_SUPABASE_MIGRATION,
        migration_history_sha256=_sha("migration-history"),
        tables=CANONICAL_SUPABASE_TABLES,
        storage_buckets=CANONICAL_SUPABASE_STORAGE_BUCKETS,
        functions=functions,
        schema_sha256=build_supabase_schema_digest(
            migration_id=REQUIRED_SUPABASE_MIGRATION,
            migration_history_sha256=_sha("migration-history"),
            tables=CANONICAL_SUPABASE_TABLES,
            storage_buckets=CANONICAL_SUPABASE_STORAGE_BUCKETS,
            functions=functions,
        ),
    )
    return TrustedHostedReleaseAttestation(
        reviewed_repository="natthaphonchop2-creator/mercury-tools",
        reviewed_commit_sha="b" * 40,
        producer=TrustedReleaseControlProducer(
            repository="natthaphonchop2-creator/mercury-release-control",
            commit_sha="c" * 40,
            workflow_run_id=123456,
            workflow_run_attempt=2,
        ),
        preflight=TrustedReleaseControlPreflight(
            environment="production-release",
            repository_visibility="public",
            required_reviewers=1,
            prevent_self_review=True,
            admin_bypass_disabled=True,
            protected_branch_only=True,
            required_configuration_sha256=_sha("remote-config"),
            approved_supabase_project_ref_sha256=_sha("a" * 20),
            approved_supabase_migration_history_sha256=_sha("migration-history"),
            approved_supabase_schema_sha256=supabase.schema_sha256,
        ),
        public_surface_manifest_sha256=_sha("manifest"),
        secret_scan_allowlist_sha256=_sha("allowlist"),
        flowaccount=TrustedFlowAccountCoverage(
            total=190,
            terminal_records=190,
            required_live_test_passed=True,
            report_sha256=_sha("flowaccount-report"),
        ),
        staging=TrustedStagingRelease(
            repository="example/public-staging",
            ref="v0.2.1-rc1",
            commit_sha="d" * 40,
            tree_sha256=_sha("staging-tree"),
            local_tool_count=19,
        ),
        render=TrustedRenderRelease(
            deployment_commit="b" * 40,
            version="0.2.1",
            hosted_tool_count=20,
            evidence_sha256=_sha("render"),
        ),
        supabase=supabase,
        surfaces=tuple(
            SurfaceAttestation(
                surface=surface,
                status=GateStatus.PASSED,
                scanner_versions=EXPECTED_SURFACE_SCANNER_VERSIONS[surface],
                started_at=completed - timedelta(seconds=1),
                completed_at=completed,
                finding_count=0,
                evidence_hashes=(_sha(surface),),
                exit_codes=(0,),
            )
            for surface in TRUSTED_RELEASE_CONTROL_SURFACES
        ),
        completed_at=completed,
    )


def _write_inputs(tmp_path):
    manifest = tmp_path / "public-surface-manifest.json"
    allowlist = tmp_path / "secret-scan-allowlist.json"
    manifest.write_text("manifest", encoding="utf-8")
    allowlist.write_text("allowlist", encoding="utf-8")
    return manifest, allowlist


def test_assembler_normalizes_offset_crossing_timestamps_to_canonical_utc() -> None:
    assembler = _assembler()
    evidence_completed_at = assembler._timestamp(
        "2026-07-15T23:00:00Z",
        "evidence_time_invalid",
    )

    surfaces = assembler._validate_surfaces(
        _surface_evidence(
            assembler,
            started_at="2026-07-16T00:15:00+02:00",
            completed_at="2026-07-16T00:45:00+02:00",
        ),
        evidence_completed_at,
    )

    assert assembler._serialize_timestamp(evidence_completed_at) == "2026-07-15T23:00:00Z"
    assert surfaces[0]["started_at"] == "2026-07-15T22:15:00Z"
    assert surfaces[0]["completed_at"] == "2026-07-15T22:45:00Z"


@pytest.mark.parametrize(
    "value",
    (
        "2026-07-16T00:00:00",
        "2026-07-16 00:00:00Z",
        "2026-07-16T00:00:00z",
        "2026-07-16T00:00:00-00:00",
        "2026-07-16T00:00:00+0000",
        "2026-07-16T00:00:00.1234567Z",
        "2026-02-30T00:00:00Z",
    ),
)
def test_assembler_rejects_noncanonical_ambiguous_and_invalid_timestamps(
    value: str,
) -> None:
    assembler = _assembler()

    with pytest.raises(assembler.AttestationError, match="^evidence_time_invalid$"):
        assembler._timestamp(value, "evidence_time_invalid")


def test_assembler_rejects_reversed_and_late_surface_instants() -> None:
    assembler = _assembler()
    evidence_completed_at = assembler._timestamp(
        "2026-07-16T01:30:00Z",
        "evidence_time_invalid",
    )

    reversed_surface = _surface_evidence(
        assembler,
        started_at="2026-07-16T01:00:00Z",
        completed_at="2026-07-16T02:30:00+02:00",
    )
    with pytest.raises(assembler.AttestationError, match="^surface_invalid$"):
        assembler._validate_surfaces(reversed_surface, evidence_completed_at)

    late_surface = _surface_evidence(
        assembler,
        started_at="2026-07-15T22:00:00-02:00",
        completed_at="2026-07-15T23:30:00-02:00",
    )
    with pytest.raises(assembler.AttestationError, match="^surface_invalid$"):
        assembler._validate_surfaces(
            late_surface,
            assembler._timestamp("2026-07-16T01:00:00Z", "evidence_time_invalid"),
        )


def test_trusted_hosted_attestation_is_strict_digest_sha_run_and_attempt_bound(
    tmp_path,
) -> None:
    path = tmp_path / "trusted-hosted-attestation.json"
    payload = _attestation().model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True).encode()
    path.write_bytes(encoded)
    manifest, allowlist = _write_inputs(tmp_path)

    attestation = load_trusted_hosted_release_attestation(
        path,
        expected_payload_sha256=hashlib.sha256(encoded).hexdigest(),
        expected_repository="natthaphonchop2-creator/mercury-tools",
        expected_commit_sha="b" * 40,
        expected_producer_repository="natthaphonchop2-creator/mercury-release-control",
        expected_producer_sha="c" * 40,
        expected_producer_run_id=123456,
        expected_producer_run_attempt=2,
        expected_staging_repository="example/public-staging",
        expected_staging_ref="v0.2.1-rc1",
        public_surface_manifest=manifest,
        secret_scan_allowlist=allowlist,
    )

    assert tuple(item.surface for item in attestation.surfaces) == (
        TRUSTED_RELEASE_CONTROL_SURFACES
    )
    assert attestation.supabase.migration_id == REQUIRED_SUPABASE_MIGRATION
    serialized = json.dumps(payload, sort_keys=True)
    for forbidden in ("service_role", "apikey", "business_payload", "provider123"):
        assert forbidden not in serialized

    payload["raw_payload"] = "provider123"
    tampered = json.dumps(payload).encode()
    path.write_bytes(tampered)
    with pytest.raises(ReleaseGateError, match="^trusted_hosted_attestation_invalid$"):
        load_trusted_hosted_release_attestation(
            path,
            expected_payload_sha256=hashlib.sha256(tampered).hexdigest(),
            expected_repository="natthaphonchop2-creator/mercury-tools",
            expected_commit_sha="b" * 40,
            expected_producer_repository=(
                "natthaphonchop2-creator/mercury-release-control"
            ),
            expected_producer_sha="c" * 40,
            expected_producer_run_id=123456,
            expected_producer_run_attempt=2,
            expected_staging_repository="example/public-staging",
            expected_staging_ref="v0.2.1-rc1",
            public_surface_manifest=manifest,
            secret_scan_allowlist=allowlist,
        )


def test_trusted_hosted_attestation_rejects_transport_and_producer_mismatch(
    tmp_path,
) -> None:
    path = tmp_path / "trusted-hosted-attestation.json"
    encoded = _attestation().model_dump_json().encode()
    path.write_bytes(encoded)
    manifest, allowlist = _write_inputs(tmp_path)
    kwargs = {
        "expected_payload_sha256": hashlib.sha256(encoded).hexdigest(),
        "expected_repository": "natthaphonchop2-creator/mercury-tools",
        "expected_commit_sha": "b" * 40,
        "expected_producer_repository": (
            "natthaphonchop2-creator/mercury-release-control"
        ),
        "expected_producer_sha": "c" * 40,
        "expected_producer_run_id": 123456,
        "expected_producer_run_attempt": 2,
        "expected_staging_repository": "example/public-staging",
        "expected_staging_ref": "v0.2.1-rc1",
        "public_surface_manifest": manifest,
        "secret_scan_allowlist": allowlist,
    }

    with pytest.raises(ReleaseGateError, match="^trusted_hosted_attestation_digest_mismatch$"):
        load_trusted_hosted_release_attestation(
            path,
            **{**kwargs, "expected_payload_sha256": "0" * 64},
        )
    with pytest.raises(ReleaseGateError, match="^trusted_hosted_attestation_mismatch$"):
        load_trusted_hosted_release_attestation(
            path,
            **{**kwargs, "expected_producer_run_attempt": 3},
        )


def test_trusted_hosted_attestation_rejects_stale_evidence(tmp_path) -> None:
    path = tmp_path / "trusted-hosted-attestation.json"
    encoded = _attestation(
        completed_at=datetime.now(UTC) - timedelta(hours=25)
    ).model_dump_json().encode()
    path.write_bytes(encoded)
    manifest, allowlist = _write_inputs(tmp_path)

    with pytest.raises(ReleaseGateError, match="^trusted_hosted_attestation_stale$"):
        load_trusted_hosted_release_attestation(
            path,
            expected_payload_sha256=hashlib.sha256(encoded).hexdigest(),
            expected_repository="natthaphonchop2-creator/mercury-tools",
            expected_commit_sha="b" * 40,
            expected_producer_repository=(
                "natthaphonchop2-creator/mercury-release-control"
            ),
            expected_producer_sha="c" * 40,
            expected_producer_run_id=123456,
            expected_producer_run_attempt=2,
            expected_staging_repository="example/public-staging",
            expected_staging_ref="v0.2.1-rc1",
            public_surface_manifest=manifest,
            secret_scan_allowlist=allowlist,
        )


def test_supabase_state_rejects_inventory_migration_and_schema_drift() -> None:
    payload = _attestation().supabase.model_dump(mode="json")

    with pytest.raises(ValidationError):
        TrustedSupabaseProductionState.model_validate(
            {**payload, "tables": [*CANONICAL_SUPABASE_TABLES[:-1]]}
        )
    with pytest.raises(ValidationError):
        TrustedSupabaseProductionState.model_validate(
            {**payload, "migration_id": "20260713100000"}
        )
    with pytest.raises(ValidationError):
        TrustedSupabaseProductionState.model_validate(
            {**payload, "schema_sha256": "0" * 64}
        )

    bundle = _attestation().model_dump(mode="json")
    bundle["preflight"]["approved_supabase_project_ref_sha256"] = _sha(
        "different-project"
    )
    with pytest.raises(ValidationError):
        TrustedHostedReleaseAttestation.model_validate(bundle)
