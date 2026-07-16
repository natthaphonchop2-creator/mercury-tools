"""Strict, sanitized attestations produced by pinned release-control code."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from mercury_tools.release.models import (
    REQUIRED_PUBLIC_SURFACES,
    GateStatus,
    StrictReleaseModel,
    SurfaceAttestation,
)
from mercury_tools.release.scanner import ReleaseGateError

TRUSTED_HOSTED_ATTESTATION_ENV = "MERCURY_TRUSTED_HOSTED_ATTESTATION"
TRUSTED_HOSTED_ATTESTATION_SHA256_ENV = (
    "MERCURY_TRUSTED_HOSTED_ATTESTATION_SHA256"
)
TRUSTED_RELEASE_CONTROL_REPOSITORY_ENV = "MERCURY_RELEASE_CONTROL_REPOSITORY"
TRUSTED_RELEASE_CONTROL_SHA_ENV = "MERCURY_RELEASE_CONTROL_SHA"
TRUSTED_RELEASE_CONTROL_RUN_ID_ENV = "MERCURY_RELEASE_CONTROL_RUN_ID"
TRUSTED_RELEASE_CONTROL_RUN_ATTEMPT_ENV = (
    "MERCURY_RELEASE_CONTROL_RUN_ATTEMPT"
)
TRUSTED_STAGING_REPOSITORY_ENV = "MERCURY_RELEASE_STAGING_REPOSITORY"
TRUSTED_STAGING_REF_ENV = "MERCURY_RELEASE_STAGING_REF"

TRUSTED_RELEASE_CONTROL_SURFACES = tuple(
    surface
    for surface in REQUIRED_PUBLIC_SURFACES
    if surface != "wheel_sdist_plugin_source_archives"
)
REQUIRED_SUPABASE_MIGRATION = "20260716100000"
CANONICAL_SUPABASE_TABLES = (
    "erp_action_catalog",
    "erp_action_observations",
    "erp_action_validation_knowledge",
    "erp_action_versions",
    "erp_spec_sources",
    "knowledge_chunks",
    "knowledge_documents",
    "knowledge_sources",
    "mcp_audit_events",
    "mercury_client_tokens",
    "mercury_connector_profiles",
    "mercury_product_events",
    "mercury_skill_catalog",
    "mercury_skill_uploads",
    "mercury_workspace_members",
    "mercury_workspace_skills",
    "mercury_workspaces",
)
CANONICAL_SUPABASE_STORAGE_BUCKETS: tuple[str, ...] = ()
REQUIRED_SUPABASE_FUNCTIONS = (
    "public.jsonb_has_forbidden_validation_key(jsonb)",
    "public.jsonb_has_forbidden_validation_value(jsonb)",
    "public.jsonb_is_safe_validation_response_shape(jsonb)",
    (
        "public.match_knowledge_chunks("
        "text,vector,integer,text,text,text,text,text,date,text,text,text,text,text)"
    ),
    "public.reject_validation_evidence_mutation()",
    "public.resolve_erp_action_validation_batch(jsonb,timestamp with time zone)",
    "public.validation_label_kind(text)",
    "public.validation_text_has_forbidden_value(text)",
    "public.validation_text_has_label_assignment_contamination(text)",
    "public.validation_text_has_safe_label_assignment(text)",
)

_MAX_ATTESTATION_BYTES = 512 * 1024
_MAX_ATTESTATION_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_REPOSITORY_PATTERN = r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$"


class TrustedFunctionDefinition(StrictReleaseModel):
    signature: str
    definition_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def build_supabase_schema_digest(
    *,
    migration_id: str,
    migration_history_sha256: str,
    tables: tuple[str, ...],
    storage_buckets: tuple[str, ...],
    functions: tuple[TrustedFunctionDefinition, ...],
) -> str:
    payload = {
        "functions": [item.model_dump(mode="json") for item in functions],
        "migration_history_sha256": migration_history_sha256,
        "migration_id": migration_id,
        "storage_buckets": list(storage_buckets),
        "tables": list(tables),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class TrustedSupabaseProductionState(StrictReleaseModel):
    project_ref: str = Field(pattern=r"^[a-z0-9]{20}$")
    project_ref_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    migration_id: str
    migration_history_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tables: tuple[str, ...]
    storage_buckets: tuple[str, ...]
    functions: tuple[TrustedFunctionDefinition, ...]
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_exact_production_state(self) -> TrustedSupabaseProductionState:
        if self.project_ref_sha256 != hashlib.sha256(
            self.project_ref.encode("utf-8")
        ).hexdigest():
            raise ValueError("trusted_supabase_project_ref_digest_invalid")
        if self.migration_id != REQUIRED_SUPABASE_MIGRATION:
            raise ValueError("trusted_supabase_migration_invalid")
        if self.tables != CANONICAL_SUPABASE_TABLES:
            raise ValueError("trusted_supabase_table_inventory_invalid")
        if self.storage_buckets != CANONICAL_SUPABASE_STORAGE_BUCKETS:
            raise ValueError("trusted_supabase_bucket_inventory_invalid")
        if tuple(item.signature for item in self.functions) != (
            REQUIRED_SUPABASE_FUNCTIONS
        ):
            raise ValueError("trusted_supabase_function_inventory_invalid")
        expected_digest = build_supabase_schema_digest(
            migration_id=self.migration_id,
            migration_history_sha256=self.migration_history_sha256,
            tables=self.tables,
            storage_buckets=self.storage_buckets,
            functions=self.functions,
        )
        if self.schema_sha256 != expected_digest:
            raise ValueError("trusted_supabase_schema_digest_invalid")
        return self


class TrustedReleaseControlProducer(StrictReleaseModel):
    repository: str = Field(pattern=_REPOSITORY_PATTERN)
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    workflow_run_id: int = Field(gt=0)
    workflow_run_attempt: int = Field(gt=0)


class TrustedReleaseControlPreflight(StrictReleaseModel):
    environment: Literal["production-release"] = "production-release"
    repository_visibility: Literal["public"] = "public"
    required_reviewers: int = Field(ge=1)
    prevent_self_review: Literal[True] = True
    admin_bypass_disabled: Literal[True] = True
    protected_branch_only: Literal[True] = True
    required_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_supabase_project_ref_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    approved_supabase_migration_history_sha256: str = Field(
        pattern=r"^[0-9a-f]{64}$"
    )
    approved_supabase_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrustedFlowAccountCoverage(StrictReleaseModel):
    total: Literal[190] = 190
    terminal_records: Literal[190] = 190
    required_live_test_passed: Literal[True] = True
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrustedStagingRelease(StrictReleaseModel):
    repository: str = Field(pattern=_REPOSITORY_PATTERN)
    ref: str = Field(pattern=r"^v0\.2\.1-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*$")
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    local_tool_count: Literal[19] = 19


class TrustedRenderRelease(StrictReleaseModel):
    deployment_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    version: Literal["0.2.1"] = "0.2.1"
    hosted_tool_count: Literal[20] = 20
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrustedHostedReleaseAttestation(StrictReleaseModel):
    schema_version: Literal[2] = 2
    reviewed_repository: str = Field(pattern=_REPOSITORY_PATTERN)
    reviewed_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    producer: TrustedReleaseControlProducer
    preflight: TrustedReleaseControlPreflight
    public_surface_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    secret_scan_allowlist_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    flowaccount: TrustedFlowAccountCoverage
    staging: TrustedStagingRelease
    render: TrustedRenderRelease
    supabase: TrustedSupabaseProductionState
    surfaces: tuple[SurfaceAttestation, ...]
    completed_at: datetime

    @model_validator(mode="after")
    def validate_exact_release(self) -> TrustedHostedReleaseAttestation:
        if self.render.deployment_commit != self.reviewed_commit_sha:
            raise ValueError("trusted_render_commit_mismatch")
        if tuple(item.surface for item in self.surfaces) != (
            TRUSTED_RELEASE_CONTROL_SURFACES
        ):
            raise ValueError("trusted_surface_inventory_invalid")
        if any(item.status is not GateStatus.PASSED for item in self.surfaces):
            raise ValueError("trusted_surface_blocked")
        if any(item.completed_at > self.completed_at for item in self.surfaces):
            raise ValueError("trusted_surface_time_invalid")
        approved_supabase_state = (
            self.preflight.approved_supabase_project_ref_sha256,
            self.preflight.approved_supabase_migration_history_sha256,
            self.preflight.approved_supabase_schema_sha256,
        )
        observed_supabase_state = (
            self.supabase.project_ref_sha256,
            self.supabase.migration_history_sha256,
            self.supabase.schema_sha256,
        )
        if observed_supabase_state != approved_supabase_state:
            raise ValueError("trusted_supabase_approved_state_mismatch")
        return self

    def surface_map(self) -> dict[str, SurfaceAttestation]:
        return {item.surface: item for item in self.surfaces}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ReleaseGateError("trusted_hosted_attestation_input_unavailable") from exc
    return digest.hexdigest()


def load_trusted_hosted_release_attestation(
    path: Path,
    *,
    expected_payload_sha256: str,
    expected_repository: str,
    expected_commit_sha: str,
    expected_producer_repository: str,
    expected_producer_sha: str,
    expected_producer_run_id: int,
    expected_producer_run_attempt: int,
    expected_staging_repository: str,
    expected_staging_ref: str,
    public_surface_manifest: Path,
    secret_scan_allowlist: Path,
    now: datetime | None = None,
) -> TrustedHostedReleaseAttestation:
    """Load a strict external attestation and bind every transport identity."""

    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ReleaseGateError("trusted_hosted_attestation_invalid") from exc
    if not encoded or len(encoded) > _MAX_ATTESTATION_BYTES:
        raise ReleaseGateError("trusted_hosted_attestation_invalid")
    if hashlib.sha256(encoded).hexdigest() != expected_payload_sha256:
        raise ReleaseGateError("trusted_hosted_attestation_digest_mismatch")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError
            payload[key] = value
        return payload

    try:
        payload = json.loads(encoded, object_pairs_hook=reject_duplicate_keys)
        attestation = TrustedHostedReleaseAttestation.model_validate(payload)
    except Exception as exc:
        raise ReleaseGateError("trusted_hosted_attestation_invalid") from exc

    expected_identity = (
        expected_repository,
        expected_commit_sha,
        expected_producer_repository,
        expected_producer_sha,
        expected_producer_run_id,
        expected_producer_run_attempt,
        expected_staging_repository,
        expected_staging_ref,
        _sha256_file(public_surface_manifest),
        _sha256_file(secret_scan_allowlist),
    )
    actual_identity = (
        attestation.reviewed_repository,
        attestation.reviewed_commit_sha,
        attestation.producer.repository,
        attestation.producer.commit_sha,
        attestation.producer.workflow_run_id,
        attestation.producer.workflow_run_attempt,
        attestation.staging.repository,
        attestation.staging.ref,
        attestation.public_surface_manifest_sha256,
        attestation.secret_scan_allowlist_sha256,
    )
    if actual_identity != expected_identity:
        raise ReleaseGateError("trusted_hosted_attestation_mismatch")

    checked_at = now or datetime.now(UTC)
    completed_at = attestation.completed_at
    if (
        completed_at > checked_at + _MAX_FUTURE_SKEW
        or checked_at - completed_at > _MAX_ATTESTATION_AGE
    ):
        raise ReleaseGateError("trusted_hosted_attestation_stale")
    return attestation
