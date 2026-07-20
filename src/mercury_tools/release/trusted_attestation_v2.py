"""Strict reader for sanitized Mercury release-control attestations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from mercury_tools.release.models import (
    GateStatus,
    StrictReleaseModel,
    SurfaceAttestation,
)
from mercury_tools.release.scanner import ReleaseGateError

TRUSTED_REVIEWED_REPOSITORY = "natthaphonchop2-creator/mercury-tools"
TRUSTED_STAGING_REPOSITORY = (
    "natthaphonchop2-creator/mercury-tools-staging"
)
TRUSTED_RELEASE_CONTROL_SURFACES = (
    "git_all_refs",
    "github_pull_request_refs",
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
)

TRUSTED_HOSTED_ATTESTATION_ENV = "MERCURY_TRUSTED_HOSTED_ATTESTATION"
TRUSTED_HOSTED_ATTESTATION_SHA256_ENV = (
    "MERCURY_TRUSTED_HOSTED_ATTESTATION_SHA256"
)
TRUSTED_RELEASE_CONTROL_REPOSITORY_ID_ENV = (
    "MERCURY_RELEASE_CONTROL_REPOSITORY_ID"
)
TRUSTED_REVIEWED_REPOSITORY_ID_ENV = "MERCURY_REVIEWED_REPOSITORY_ID"
TRUSTED_RELEASE_CONTROL_SHA_ENV = "MERCURY_RELEASE_CONTROL_SHA"
TRUSTED_RELEASE_CONTROL_RUN_ID_ENV = "MERCURY_RELEASE_CONTROL_RUN_ID"
TRUSTED_RELEASE_CONTROL_RUN_ATTEMPT_ENV = (
    "MERCURY_RELEASE_CONTROL_RUN_ATTEMPT"
)

_MAX_ATTESTATION_BYTES = 512 * 1024
_MAX_FUTURE_SKEW = timedelta(minutes=5)
_MAX_LIFETIME = timedelta(minutes=60)
_REQUIRED_MIGRATION_BY_VERSION = {
    "0.2.2": "20260716100000",
    "0.3.0": "20260719120000",
}


class _TrustedModel(StrictReleaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class TrustedPreflightReceipt(_TrustedModel):
    admin_bypass_disabled: Literal[True]
    control_repository_id: int = Field(gt=0)
    environment: Literal["production-release"]
    prevent_self_review: Literal[True]
    protected_branch_only: Literal[True]
    required_configuration_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    required_reviewers: int = Field(gt=0, le=100)
    target_repository_id: int = Field(gt=0)


class TrustedFlowAccountEvidence(_TrustedModel):
    environment: Literal["sandbox"]
    read_only: Literal[True]
    status: Literal[200]


class TrustedPublicMcpEvidence(_TrustedModel):
    catalog_action_count: int = Field(ge=1, le=10_000)
    flowaccount_citations: int = Field(ge=1, le=1_000)
    hosted_tool_count: int = Field(ge=1, le=1_000)
    peak_citations: int = Field(ge=1, le=1_000)
    status: Literal[200]
    write_tools_exposed: Literal[False]


class TrustedRenderEvidence(_TrustedModel):
    catalog_action_count: int = Field(ge=1, le=10_000)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    hosted_tool_count: int = Field(ge=1, le=1_000)
    logs_scanned: Literal[True]
    status: Literal["live"]
    version: Literal["0.2.2", "0.3.0"]


class TrustedSupabaseEvidence(_TrustedModel):
    function_count: int = Field(ge=1, le=1_000)
    migration_id: Literal["20260716100000", "20260719120000"]
    project_ref_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rag_identity_count: int = Field(ge=1, le=100_000)
    read_only: Literal[True]
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    table_count: int = Field(ge=1, le=1_000)


class TrustedProviderEvidence(_TrustedModel):
    flowaccount: TrustedFlowAccountEvidence
    public_mcp: TrustedPublicMcpEvidence
    render: TrustedRenderEvidence
    reviewed_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    supabase: TrustedSupabaseEvidence
    version: Literal["0.2.2", "0.3.0"]


class TrustedStagingReceipt(_TrustedModel):
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    ref: str = Field(pattern=r"^v0\.(?:2\.2|3\.0)-rc\.[0-9a-f]{12}$")
    repository: Literal[
        "natthaphonchop2-creator/mercury-tools-staging"
    ]
    tag_object_sha: str = Field(pattern=r"^[0-9a-f]{40}$")


class TrustedWorkflowReceipt(_TrustedModel):
    attempt: int = Field(gt=0)
    control_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository_id: int = Field(gt=0)
    run_id: int = Field(gt=0)


class TrustedAttestationV2(_TrustedModel):
    expires_at: datetime
    issued_at: datetime
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    preflight: TrustedPreflightReceipt
    provider_evidence: TrustedProviderEvidence
    public_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    schema_version: Literal[2]
    staging: TrustedStagingReceipt
    surface_evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    surface_count: Literal[8]
    surfaces: tuple[SurfaceAttestation, ...] = Field(min_length=8, max_length=8)
    version: Literal["0.2.2", "0.3.0"]
    workflow: TrustedWorkflowReceipt

    @model_validator(mode="after")
    def validate_release_identity(self) -> TrustedAttestationV2:
        if (
            self.reviewed_sha != self.provider_evidence.reviewed_sha
            or self.provider_evidence.render.commit != self.reviewed_sha
            or self.provider_evidence.version != self.version
            or self.provider_evidence.render.version != self.version
            or self.provider_evidence.supabase.migration_id
            != _REQUIRED_MIGRATION_BY_VERSION[self.version]
            or self.staging.ref != f"v{self.version}-rc.{self.reviewed_sha[:12]}"
            or tuple(item.surface for item in self.surfaces)
            != TRUSTED_RELEASE_CONTROL_SURFACES
            or any(item.status is not GateStatus.PASSED for item in self.surfaces)
            or any(item.completed_at > self.issued_at for item in self.surfaces)
        ):
            raise ValueError("trusted_attestation_v2_identity_invalid")
        return self

    def surface_map(self) -> dict[str, SurfaceAttestation]:
        return {item.surface: item for item in self.surfaces}


def load_trusted_attestation_v2(
    path: Path,
    *,
    expected_payload_sha256: str,
    expected_reviewed_repository: str,
    expected_reviewed_repository_id: int,
    expected_reviewed_sha: str,
    expected_control_repository_id: int,
    expected_control_sha: str,
    expected_control_run_id: int,
    expected_control_run_attempt: int,
    expected_public_tree_digest: str,
    now: datetime | None = None,
) -> TrustedAttestationV2:
    """Load an attestation and bind it to exact source and workflow identities."""

    try:
        encoded = path.read_bytes()
    except OSError as exc:
        raise ReleaseGateError("trusted_attestation_v2_invalid") from exc
    if not encoded or len(encoded) > _MAX_ATTESTATION_BYTES:
        raise ReleaseGateError("trusted_attestation_v2_invalid")
    if hashlib.sha256(encoded).hexdigest() != expected_payload_sha256:
        raise ReleaseGateError("trusted_attestation_v2_digest_mismatch")

    try:
        payload = _strict_json_loads(encoded)
        normalized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        attestation = TrustedAttestationV2.model_validate_json(normalized)
    except (UnicodeError, ValueError, TypeError, ValidationError) as exc:
        raise ReleaseGateError("trusted_attestation_v2_invalid") from exc

    actual_identity = (
        expected_reviewed_repository,
        attestation.preflight.target_repository_id,
        attestation.reviewed_sha,
        attestation.preflight.control_repository_id,
        attestation.workflow.repository_id,
        attestation.workflow.control_commit,
        attestation.workflow.run_id,
        attestation.workflow.attempt,
        attestation.public_tree_digest,
    )
    expected_identity = (
        TRUSTED_REVIEWED_REPOSITORY,
        expected_reviewed_repository_id,
        expected_reviewed_sha,
        expected_control_repository_id,
        expected_control_repository_id,
        expected_control_sha,
        expected_control_run_id,
        expected_control_run_attempt,
        expected_public_tree_digest,
    )
    if actual_identity != expected_identity:
        raise ReleaseGateError("trusted_attestation_v2_mismatch")

    payload_without_digest = attestation.model_dump(exclude={"payload_sha256"})
    if _payload_digest(payload_without_digest) != attestation.payload_sha256:
        raise ReleaseGateError("trusted_attestation_v2_digest_mismatch")

    checked_at = _utc(now or datetime.now(UTC))
    issued_at = _utc(attestation.issued_at)
    expires_at = _utc(attestation.expires_at)
    if issued_at > checked_at + _MAX_FUTURE_SKEW:
        raise ReleaseGateError("trusted_attestation_v2_time_invalid")
    if (
        expires_at <= issued_at
        or expires_at > issued_at + _MAX_LIFETIME
        or checked_at > expires_at
    ):
        raise ReleaseGateError("trusted_attestation_v2_expired")
    return attestation


def _payload_digest(payload: dict[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            _jsonable(payload), separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def _strict_json_loads(encoded: bytes) -> object:
    def reject_duplicate_keys(
        pairs: list[tuple[str, object]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {}
        for key, value in pairs:
            if key in payload:
                raise ValueError("duplicate_json_key")
            payload[key] = value
        return payload

    return json.loads(encoded, object_pairs_hook=reject_duplicate_keys)


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("trusted_attestation_v2_time_invalid")
    return value.astimezone(UTC)
