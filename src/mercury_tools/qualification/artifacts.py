"""Sanitized, immutable qualification evidence for provider-MCP capabilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import (
    canonical_json,
    deep_freeze,
    validate_credential_safe,
    validate_credential_safe_paths,
)
from mercury_tools.catalog.models import ProviderMCPQualification

_SHA256 = r"^[0-9a-f]{64}$"
_IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_PROVIDER = r"^(?:flowaccount|peak)$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_TOOL = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"
_CATALOG_URI = re.compile(
    r"^catalog://global/(?P<provider>flowaccount|peak)/qualifications/"
    r"(?P<version>[0-9a-f]{64})-(?P<revision>[0-9a-f]{64})\.json$"
)
_DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


class QualificationArtifact(BaseModel):
    """Only hashes and reviewed identifiers may cross the evidence boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    provider: str = Field(pattern=_PROVIDER)
    environment: str = Field(pattern=_ENVIRONMENT)
    company_sha256: str = Field(pattern=_SHA256)
    normalized_capability: str = Field(pattern=_CAPABILITY)
    provider_tool_name: str = Field(pattern=_TOOL)
    capability_version_sha256: str = Field(pattern=_SHA256)
    runner_version: str = Field(pattern=_IDENTIFIER)
    evaluated_at: datetime
    input_schema_sha256: str = Field(pattern=_SHA256)
    output_schema_sha256: str = Field(pattern=_SHA256)
    schema_hash: str = Field(pattern=_SHA256)
    response_shape_hash: str = Field(pattern=_SHA256)
    input_sha256: str = Field(pattern=_SHA256)
    sanitized_result_identifier: str = Field(pattern=_IDENTIFIER)
    checks: dict[str, bool]
    reviewer: str = Field(pattern=_IDENTIFIER)
    evidence_expires_at: datetime
    passed: bool
    evidence_revision_sha256: str | None = Field(default=None, pattern=_SHA256)

    @model_validator(mode="before")
    @classmethod
    def reject_unsafe_content(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value

    @model_validator(mode="after")
    def validate_artifact(self) -> QualificationArtifact:
        if (
            not self.checks
            or any(re.fullmatch(_IDENTIFIER, key) is None for key in self.checks)
            or (self.passed and not all(self.checks.values()))
            or (not self.passed and all(self.checks.values()))
        ):
            raise ValueError("qualification_artifact_invalid")
        for field_name in ("evaluated_at", "evidence_expires_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("qualification_artifact_invalid")
            object.__setattr__(self, field_name, value.astimezone(UTC))
        if self.evidence_expires_at <= self.evaluated_at:
            raise ValueError("qualification_artifact_invalid")
        expected_revision = _sha256(
            self.model_dump(mode="json", exclude={"evidence_revision_sha256"})
        )
        if self.evidence_revision_sha256 is None:
            object.__setattr__(self, "evidence_revision_sha256", expected_revision)
        elif self.evidence_revision_sha256 != expected_revision:
            raise ValueError("qualification_artifact_invalid")
        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, deep_freeze(getattr(self, field_name)))
        return self

    @property
    def catalog_uri(self) -> str:
        return (
            f"catalog://global/{self.provider}/qualifications/"
            f"{self.capability_version_sha256}-{self.evidence_revision_sha256}.json"
        )

    @property
    def filename(self) -> str:
        return f"{self.capability_version_sha256}-{self.evidence_revision_sha256}.json"

    def require_valid_for(
        self,
        definition: ProviderMCPQualification,
        *,
        now: datetime,
        expected_company_sha256: str | None = None,
    ) -> None:
        checked_now = _aware_now(now)
        if self.evaluated_at > checked_now:
            raise ValueError("qualification_evidence_future")
        if self.evidence_expires_at <= checked_now:
            raise ValueError("qualification_evidence_expired")
        expected = {
            "provider": definition.provider,
            "environment": definition.environment,
            "normalized_capability": definition.normalized_capability,
            "provider_tool_name": definition.provider_tool_name,
            "capability_version_sha256": definition.capability_version_sha256,
            "input_schema_sha256": _json_sha256(definition.input_schema),
            "output_schema_sha256": _json_sha256(definition.output_schema),
            "schema_hash": definition.schema_hash,
            "response_shape_hash": definition.response_shape_hash,
        }
        if any(getattr(self, key) != value for key, value in expected.items()):
            raise ValueError("qualification_evidence_mismatch")
        required_company = expected_company_sha256 or definition.company_sha256
        if required_company is not None and self.company_sha256 != required_company:
            raise ValueError("qualification_evidence_company_mismatch")
        if (
            definition.evidence_revision_sha256 is not None
            and self.evidence_revision_sha256 != definition.evidence_revision_sha256
        ):
            raise ValueError("qualification_evidence_revision_mismatch")
        if not self.passed:
            raise ValueError("qualification_evidence_failed")


def build_qualification_artifact(
    *,
    definition: ProviderMCPQualification,
    company_sha256: str,
    runner_version: str,
    evaluated_at: datetime,
    input_sha256: str,
    sanitized_result_identifier: str,
    checks: dict[str, bool],
    reviewer: str,
    evidence_expires_at: datetime,
    passed: bool,
    environment: str | None = None,
) -> QualificationArtifact:
    """Build evidence from controlled results, never raw provider content."""

    if environment is not None and environment != definition.environment:
        raise ValueError("qualification_artifact_environment_mismatch")
    return QualificationArtifact(
        provider=definition.provider,
        environment=definition.environment,
        company_sha256=company_sha256,
        normalized_capability=definition.normalized_capability,
        provider_tool_name=definition.provider_tool_name,
        capability_version_sha256=definition.capability_version_sha256,
        runner_version=runner_version,
        evaluated_at=evaluated_at,
        input_schema_sha256=_json_sha256(definition.input_schema),
        output_schema_sha256=_json_sha256(definition.output_schema),
        schema_hash=definition.schema_hash,
        response_shape_hash=definition.response_shape_hash,
        input_sha256=input_sha256,
        sanitized_result_identifier=sanitized_result_identifier,
        checks=checks,
        reviewer=reviewer,
        evidence_expires_at=evidence_expires_at,
        passed=passed,
    )


def write_qualification_artifact(
    catalog_root: str | Path,
    artifact: QualificationArtifact,
) -> Path:
    """Atomically create one no-follow, revision-bound artifact below a trusted root."""

    checked = QualificationArtifact.model_validate(artifact)
    root = _absolute_root(catalog_root)
    root_fd = _open_root(root)
    provider_fd = qualifications_fd = None
    temporary_name: str | None = None
    try:
        provider_fd = _open_directory(root_fd, checked.provider, create=True)
        qualifications_fd = _open_directory(provider_fd, "qualifications", create=True)
        existing = _read_file(qualifications_fd, checked.filename, missing_ok=True)
        if existing is not None:
            _require_matching_existing_artifact_bytes(existing, checked)
            return root / checked.provider / "qualifications" / checked.filename

        serialized = f"{checked.model_dump_json(indent=2)}\n".encode()
        temporary_name = f".qualification-{os.urandom(16).hex()}.tmp"
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=qualifications_fd,
        )
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(
                    temporary_name,
                    checked.filename,
                    src_dir_fd=qualifications_fd,
                    dst_dir_fd=qualifications_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                existing = _read_file(qualifications_fd, checked.filename, missing_ok=False)
                _require_matching_existing_artifact_bytes(existing, checked)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=qualifications_fd)
        return root / checked.provider / "qualifications" / checked.filename
    except ValueError:
        raise
    except OSError:
        raise ValueError("qualification_artifact_write_failed") from None
    finally:
        if qualifications_fd is not None:
            os.close(qualifications_fd)
        if provider_fd is not None:
            os.close(provider_fd)
        os.close(root_fd)


def load_catalog_qualification_artifact(
    catalog_root: str | Path,
    catalog_uri: str,
) -> QualificationArtifact:
    """Load an artifact only by its catalog URI through no-follow descriptors."""

    matched = _CATALOG_URI.fullmatch(catalog_uri)
    if matched is None:
        raise ValueError("qualification_artifact_path_invalid")
    root_fd = _open_root(_absolute_root(catalog_root))
    provider_fd = qualifications_fd = None
    try:
        provider = matched.group("provider")
        filename = f"{matched.group('version')}-{matched.group('revision')}.json"
        provider_fd = _open_directory(root_fd, provider, create=False)
        qualifications_fd = _open_directory(provider_fd, "qualifications", create=False)
        artifact = _artifact_from_bytes(_read_file(qualifications_fd, filename, missing_ok=False))
        if artifact.catalog_uri != catalog_uri:
            raise ValueError("qualification_artifact_invalid")
        return artifact
    except ValueError:
        raise
    except OSError:
        raise ValueError("qualification_artifact_path_invalid") from None
    finally:
        if qualifications_fd is not None:
            os.close(qualifications_fd)
        if provider_fd is not None:
            os.close(provider_fd)
        os.close(root_fd)


def load_qualification_artifact(path: str | Path) -> QualificationArtifact:
    try:
        target = Path(path)
        target_stat = target.lstat()
        if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISREG(target_stat.st_mode):
            raise ValueError
        with target.open("rb") as handle:
            return _artifact_from_bytes(handle.read())
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("qualification_artifact_invalid") from None


def _absolute_root(catalog_root: str | Path) -> Path:
    root = Path(catalog_root).absolute()
    if not root.is_absolute():
        raise ValueError("qualification_artifact_path_invalid")
    return root


def _open_root(root: Path) -> int:
    parts = root.parts
    if not parts or parts[0] != root.anchor:
        raise ValueError("qualification_artifact_path_invalid")
    descriptor = os.open(root.anchor, _DIRECTORY_FLAGS)
    try:
        for part in parts[1:]:
            next_descriptor = _open_directory(descriptor, part, create=False)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except (OSError, ValueError):
        os.close(descriptor)
        raise ValueError("qualification_artifact_path_invalid") from None


def _open_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("qualification_artifact_path_invalid")
    if create:
        with suppress(FileExistsError):
            os.mkdir(name, mode=0o755, dir_fd=parent_fd)
    try:
        metadata = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("qualification_artifact_path_invalid")
        return os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except ValueError:
        raise
    except OSError:
        raise ValueError("qualification_artifact_path_invalid") from None


def _read_file(directory_fd: int, name: str, *, missing_ok: bool) -> bytes | None:
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise ValueError("qualification_artifact_path_invalid") from None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("qualification_artifact_path_invalid")
    try:
        descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
        with os.fdopen(descriptor, "rb") as handle:
            return handle.read()
    except OSError:
        raise ValueError("qualification_artifact_path_invalid") from None


def _artifact_from_bytes(value: bytes) -> QualificationArtifact:
    try:
        return QualificationArtifact.model_validate(json.loads(value.decode("utf-8")))
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("qualification_artifact_invalid") from None


def _require_matching_existing_artifact_bytes(
    serialized: bytes | None,
    expected: QualificationArtifact,
) -> None:
    if serialized is None:
        raise ValueError("qualification_artifact_path_invalid")
    try:
        if _artifact_from_bytes(serialized) != expected:
            raise ValueError("qualification_artifact_conflict")
    except ValueError as error:
        if str(error) == "qualification_artifact_conflict":
            raise
        raise ValueError("qualification_artifact_conflict") from None


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _json_sha256(value: Any) -> str:
    return _sha256(value)


def _aware_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification_evidence_time_invalid")
    return value.astimezone(UTC)


__all__ = [
    "QualificationArtifact",
    "build_qualification_artifact",
    "load_catalog_qualification_artifact",
    "load_qualification_artifact",
    "write_qualification_artifact",
]
