"""Build the bounded schema-v3 handoff consumed by release-control v2."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from mercury_tools.release.models import StrictReleaseModel
from mercury_tools.release.scanner import ReleaseGateError
from mercury_tools.release.trusted_attestation_v2 import (
    TRUSTED_REVIEWED_REPOSITORY,
    TrustedAttestationV2,
    load_trusted_attestation_v2,
)

_MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
_WORKFLOW_PATH = ".github/workflows/release-v0.2.2.yml"


class _HandoffModel(StrictReleaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
        strict=True,
    )


class ReleaseArtifactReceipt(_HandoffModel):
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,199}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=_MAX_ARTIFACT_BYTES)


class MercuryWorkflowReceipt(_HandoffModel):
    repository_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    run_id: int = Field(gt=0)
    workflow_path: Literal[".github/workflows/release-v0.2.2.yml"]


class OriginalControlReceipt(_HandoffModel):
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: int = Field(gt=0)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    repository_id: int = Field(gt=0)
    run_attempt: int = Field(gt=0)
    run_id: int = Field(gt=0)


class ReleaseBundleReceipt(_HandoffModel):
    artifact_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_id: int = Field(gt=0)
    name: str = Field(
        pattern=(
            r"^mercury-v0\.2\.2-release-artifacts-"
            r"[1-9][0-9]*-attempt-[1-9][0-9]*$"
        )
    )


class ReleaseReadyHandoffV3(_HandoffModel):
    artifacts: tuple[ReleaseArtifactReceipt, ...] = Field(
        min_length=1, max_length=20
    )
    created_at: datetime
    expires_at: datetime
    mercury_workflow: MercuryWorkflowReceipt
    original_release_control: OriginalControlReceipt
    public_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_bundle: ReleaseBundleReceipt
    reviewed_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    schema_version: Literal[3]
    staging_ref: str = Field(pattern=r"^v0\.2\.2-rc\.[0-9a-f]{12}$")
    version: Literal["0.2.2"]

    @model_validator(mode="after")
    def validate_attempt_binding(self) -> ReleaseReadyHandoffV3:
        expected_bundle = (
            "mercury-v0.2.2-release-artifacts-"
            f"{self.mercury_workflow.run_id}-attempt-"
            f"{self.mercury_workflow.run_attempt}"
        )
        names = tuple(item.name for item in self.artifacts)
        if (
            self.release_bundle.name != expected_bundle
            or self.staging_ref != f"v0.2.2-rc.{self.reviewed_sha[:12]}"
            or names != tuple(sorted(set(names)))
        ):
            raise ValueError("release_handoff_identity_invalid")
        return self


def write_release_ready_handoff(
    *,
    artifacts: Path,
    attestation: TrustedAttestationV2,
    control_artifact_id: int,
    control_artifact_digest: str,
    control_payload_sha256: str,
    mercury_repository_id: int,
    mercury_run_id: int,
    mercury_run_attempt: int,
    release_bundle_artifact_id: int,
    release_bundle_artifact_digest: str,
    output: Path,
    now: datetime | None = None,
) -> ReleaseReadyHandoffV3:
    """Write a new handoff after collecting an exact regular-file inventory."""

    created_at = _utc(now or datetime.now(UTC))
    inventory = _artifact_inventory(artifacts)
    handoff = ReleaseReadyHandoffV3(
        artifacts=inventory,
        created_at=created_at,
        expires_at=created_at + timedelta(minutes=60),
        mercury_workflow=MercuryWorkflowReceipt(
            repository_id=mercury_repository_id,
            run_attempt=mercury_run_attempt,
            run_id=mercury_run_id,
            workflow_path=_WORKFLOW_PATH,
        ),
        original_release_control=OriginalControlReceipt(
            artifact_digest=control_artifact_digest,
            artifact_id=control_artifact_id,
            commit=attestation.workflow.control_commit,
            payload_sha256=control_payload_sha256,
            repository_id=attestation.workflow.repository_id,
            run_attempt=attestation.workflow.attempt,
            run_id=attestation.workflow.run_id,
        ),
        public_tree_digest=attestation.public_tree_digest,
        release_bundle=ReleaseBundleReceipt(
            artifact_digest=release_bundle_artifact_digest,
            artifact_id=release_bundle_artifact_id,
            name=(
                "mercury-v0.2.2-release-artifacts-"
                f"{mercury_run_id}-attempt-{mercury_run_attempt}"
            ),
        ),
        reviewed_sha=attestation.reviewed_sha,
        schema_version=3,
        staging_ref=attestation.staging.ref,
        version="0.2.2",
    )
    encoded = (
        json.dumps(
            handoff.model_dump(mode="json"),
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        + b"\n"
    )
    _write_new(output, encoded)
    return handoff


def _artifact_inventory(path: Path) -> tuple[ReleaseArtifactReceipt, ...]:
    try:
        root = path.lstat()
        entries = tuple(path.iterdir())
    except OSError as exc:
        raise ReleaseGateError("release_handoff_artifacts_invalid") from exc
    if stat.S_ISLNK(root.st_mode) or not stat.S_ISDIR(root.st_mode):
        raise ReleaseGateError("release_handoff_artifacts_invalid")
    expected = {
        "SHA256SUMS.json",
        "mercury-finance-plugin-0.2.2.zip",
        "mercury-tools-0.2.2-source.tar.gz",
        "mercury_tools-0.2.2-py3-none-any.whl",
        "mercury_tools-0.2.2.tar.gz",
    }
    if {entry.name for entry in entries} != expected:
        raise ReleaseGateError("release_handoff_artifacts_invalid")
    receipts: list[ReleaseArtifactReceipt] = []
    total = 0
    for entry in sorted(entries, key=lambda item: item.name):
        try:
            metadata = entry.lstat()
        except OSError as exc:
            raise ReleaseGateError("release_handoff_artifacts_invalid") from exc
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise ReleaseGateError("release_handoff_artifacts_invalid")
        total += metadata.st_size
        if metadata.st_size <= 0 or total > _MAX_ARTIFACT_BYTES:
            raise ReleaseGateError("release_handoff_artifacts_invalid")
        receipts.append(
            ReleaseArtifactReceipt(
                name=entry.name,
                sha256=_sha256_file(entry),
                size=metadata.st_size,
            )
        )
    return tuple(receipts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseGateError("release_handoff_artifacts_invalid") from exc
    return digest.hexdigest()


def _write_new(path: Path, encoded: bytes) -> None:
    try:
        parent = path.parent.resolve(strict=True)
        parent_metadata = parent.lstat()
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise OSError
        with tempfile.NamedTemporaryFile(
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        raise ReleaseGateError("release_handoff_output_invalid") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReleaseGateError("release_handoff_time_invalid")
    return value.astimezone(UTC)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts", required=True, type=Path)
    parser.add_argument("--attestation", required=True, type=Path)
    parser.add_argument("--attestation-file-sha256", required=True)
    parser.add_argument("--reviewed-repository-id", required=True, type=int)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--control-repository-id", required=True, type=int)
    parser.add_argument("--control-sha", required=True)
    parser.add_argument("--control-run-id", required=True, type=int)
    parser.add_argument("--control-run-attempt", required=True, type=int)
    parser.add_argument("--control-artifact-id", required=True, type=int)
    parser.add_argument("--control-artifact-digest", required=True)
    parser.add_argument("--public-tree-digest", required=True)
    parser.add_argument("--mercury-repository-id", required=True, type=int)
    parser.add_argument("--mercury-run-id", required=True, type=int)
    parser.add_argument("--mercury-run-attempt", required=True, type=int)
    parser.add_argument("--release-bundle-artifact-id", required=True, type=int)
    parser.add_argument("--release-bundle-artifact-digest", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    attestation = load_trusted_attestation_v2(
        args.attestation,
        expected_payload_sha256=args.attestation_file_sha256,
        expected_reviewed_repository=TRUSTED_REVIEWED_REPOSITORY,
        expected_reviewed_repository_id=args.reviewed_repository_id,
        expected_reviewed_sha=args.reviewed_sha,
        expected_control_repository_id=args.control_repository_id,
        expected_control_sha=args.control_sha,
        expected_control_run_id=args.control_run_id,
        expected_control_run_attempt=args.control_run_attempt,
        expected_public_tree_digest=args.public_tree_digest,
    )
    write_release_ready_handoff(
        artifacts=args.artifacts,
        attestation=attestation,
        control_artifact_id=args.control_artifact_id,
        control_artifact_digest=args.control_artifact_digest,
        control_payload_sha256=args.attestation_file_sha256,
        mercury_repository_id=args.mercury_repository_id,
        mercury_run_id=args.mercury_run_id,
        mercury_run_attempt=args.mercury_run_attempt,
        release_bundle_artifact_id=args.release_bundle_artifact_id,
        release_bundle_artifact_digest=args.release_bundle_artifact_digest,
        output=args.output,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
