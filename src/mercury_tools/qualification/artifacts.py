"""Sanitized, immutable qualification evidence for provider-MCP capabilities."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
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
        for field_name in type(self).model_fields:
            object.__setattr__(self, field_name, deep_freeze(getattr(self, field_name)))
        return self

    @property
    def catalog_uri(self) -> str:
        return (
            f"catalog://global/{self.provider}/qualifications/{self.capability_version_sha256}.json"
        )

    def require_valid_for(
        self,
        definition: ProviderMCPQualification,
        *,
        now: datetime,
    ) -> None:
        checked_now = _aware_now(now)
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
    """Atomically create one version-bound artifact without replacing evidence."""

    checked = QualificationArtifact.model_validate(artifact)
    root = Path(catalog_root).resolve()
    directory = root / checked.provider / "qualifications"
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError("qualification_artifact_path_invalid")
    target = directory / f"{checked.capability_version_sha256}.json"
    if target.parent.resolve() != directory.resolve():
        raise ValueError("qualification_artifact_path_invalid")

    serialized = f"{checked.model_dump_json(indent=2)}\n".encode()
    if target.exists():
        _require_matching_existing_artifact(target, checked)
        return target

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".qualification-",
        suffix=".tmp",
        dir=directory,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            _require_matching_existing_artifact(target, checked)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise ValueError("qualification_artifact_write_failed") from None
    return target


def load_qualification_artifact(path: str | Path) -> QualificationArtifact:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return QualificationArtifact.model_validate(payload)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("qualification_artifact_invalid") from None


def _require_matching_existing_artifact(
    path: Path,
    expected: QualificationArtifact,
) -> None:
    try:
        if load_qualification_artifact(path) != expected:
            raise ValueError("qualification_artifact_conflict")
    except ValueError as error:
        if str(error) == "qualification_artifact_conflict":
            raise
        raise ValueError("qualification_artifact_conflict") from None


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _aware_now(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification_evidence_time_invalid")
    return value.astimezone(UTC)
