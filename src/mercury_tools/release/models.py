"""Typed, secret-safe release scanner contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mercury_tools.catalog.identity import FrozenDict

REQUIRED_PUBLIC_SURFACES = (
    "git_all_refs",
    "github_pull_request_refs",
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
    "wheel_sdist_plugin_source_archives",
    "public_mcp_responses",
)

PINNED_SCANNER_VERSIONS = MappingProxyType(
    {
        "gitleaks": "8.24.3",
        "trufflehog": "3.88.32",
    }
)
BUILTIN_SCANNER_VERSION = "1.0.0"
_HISTORY_SURFACE_SCANNER_VERSIONS = tuple(
    sorted((*PINNED_SCANNER_VERSIONS.values(), BUILTIN_SCANNER_VERSION))
)
EXPECTED_SURFACE_SCANNER_VERSIONS = MappingProxyType(
    {
        surface: (
            _HISTORY_SURFACE_SCANNER_VERSIONS
            if surface in {"git_all_refs", "github_pull_request_refs"}
            else (BUILTIN_SCANNER_VERSION,)
        )
        for surface in REQUIRED_PUBLIC_SURFACES
    }
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SAFE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z0-9_.-]+){0,2}$")
_SAFE_SURFACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_UNAVAILABLE_CODE_MARKERS = ("unavailable", "inaccessible", "client_missing", "missing", "disabled")


class StrictReleaseModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        values = {
            field_name: getattr(self, field_name)
            for field_name in type(self).model_fields
        }
        if update:
            values.update(update)
        return type(self).model_validate(values)


def _validate_scanner_pins(value: Mapping[str, str]) -> FrozenDict:
    pins = dict(value)
    if pins != dict(PINNED_SCANNER_VERSIONS):
        raise ValueError("scanner_pins_invalid")
    return FrozenDict({name: pins[name] for name in PINNED_SCANNER_VERSIONS})


class PublicSurfaceManifest(StrictReleaseModel):
    schema_version: Literal[1] = 1
    required: tuple[str, ...]
    scanner_versions: dict[str, str]

    @model_validator(mode="after")
    def validate_exact_manifest(self) -> PublicSurfaceManifest:
        if self.required != REQUIRED_PUBLIC_SURFACES:
            raise ValueError("public_surface_manifest_invalid")
        object.__setattr__(self, "scanner_versions", _validate_scanner_pins(self.scanner_versions))
        return self


class AllowlistClassification(StrEnum):
    NON_SECRET_FIXTURE = "non_secret_fixture"
    DOCUMENTATION_PLACEHOLDER = "documentation_placeholder"


class FindingRule(StrEnum):
    FORBIDDEN_PATH = "forbidden_path"
    KNOWN_CREDENTIAL = "known_credential"
    PROVIDER_TOKEN = "provider_token"
    CREDENTIAL_ASSIGNMENT = "credential_assignment"
    HIGH_ENTROPY = "high_entropy"
    SCANNER_FINDING = "scanner_finding"
    ARCHIVE_UNSAFE = "archive_unsafe"


class ReviewerRole(StrEnum):
    SECURITY_REVIEWER = "security_reviewer"
    RELEASE_REVIEWER = "release_reviewer"


class AllowlistEntry(StrictReleaseModel):
    classification: AllowlistClassification
    file: str
    rule: FindingRule
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewer_role: ReviewerRole
    expires_at: datetime

    @field_validator("file")
    @classmethod
    def validate_exact_file(cls, value: str) -> str:
        candidate = PurePosixPath(value)
        if (
            not value
            or value == "."
            or candidate.is_absolute()
            or candidate.as_posix() != value
            or "\\" in value
            or any(part in {"", ".", ".."} for part in candidate.parts)
            or any(character in value for character in "*?[]{}")
        ):
            raise ValueError("allowlist_file_invalid")
        return candidate.as_posix()

    @field_validator("expires_at")
    @classmethod
    def validate_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("allowlist_expiry_invalid")
        return value


class SecretScanAllowlist(StrictReleaseModel):
    schema_version: Literal[1] = 1
    entries: tuple[AllowlistEntry, ...] = ()

    @model_validator(mode="after")
    def reject_duplicate_entries(self) -> SecretScanAllowlist:
        identities = [(entry.file, entry.rule, entry.digest) for entry in self.entries]
        if len(identities) != len(set(identities)):
            raise ValueError("allowlist_duplicate")
        return self


class SecretScanPolicy(StrictReleaseModel):
    scanner_versions: dict[str, str]
    known_secret_digests: tuple[str, ...] = ()
    max_file_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_filesystem_entries: int = Field(default=100_000, gt=0)
    max_filesystem_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_artifact_entries: int = Field(default=100_000, gt=0)
    max_artifact_total_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_archive_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_archive_entries: int = Field(default=100_000, gt=0)
    max_archive_member_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_archive_uncompressed_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_git_commits: int = Field(default=100_000, gt=0)
    max_git_tree_entries: int = Field(default=1_000_000, gt=0)
    max_git_blob_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)
    max_hosted_pages: int = Field(default=1_000, gt=0)
    max_hosted_page_records: int = Field(default=1_000, gt=0)
    max_hosted_records: int = Field(default=1_000_000, gt=0)
    max_hosted_receipt_bytes: int = Field(default=256 * 1024 * 1024, gt=0)
    max_hosted_total_bytes: int = Field(default=1024 * 1024 * 1024, gt=0)

    @model_validator(mode="after")
    def validate_policy(self) -> SecretScanPolicy:
        object.__setattr__(self, "scanner_versions", _validate_scanner_pins(self.scanner_versions))
        if any(
            not _SHA256_PATTERN.fullmatch(fingerprint)
            for fingerprint in self.known_secret_digests
        ):
            raise ValueError("known_credential_fingerprint_invalid")
        if len(self.known_secret_digests) != len(
            set(self.known_secret_digests)
        ):
            raise ValueError("known_credential_fingerprint_duplicate")
        return self


class HostedSurface(StrictReleaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    accessible: bool


class SecretScanRequest(StrictReleaseModel):
    repo: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    repo_url: str | None = Field(default=None, exclude=True, repr=False)
    artifacts: Path
    all_history: bool
    hosted: bool
    manifest: PublicSurfaceManifest
    allowlist: SecretScanAllowlist
    policy: SecretScanPolicy
    hosted_surfaces: tuple[HostedSurface, ...] = ()

    @field_validator("repo_url")
    @classmethod
    def reject_credentialed_repo_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value or any(character in value for character in "\r\n\0"):
            raise ValueError("repo_url_invalid")
        if "://" not in value:
            return value
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "file"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (parsed.scheme == "https" and not parsed.hostname)
        ):
            raise ValueError("repo_url_invalid")
        return value

    @model_validator(mode="after")
    def validate_consistent_policy(self) -> SecretScanRequest:
        if self.manifest.scanner_versions != self.policy.scanner_versions:
            raise ValueError("scanner_pins_inconsistent")
        return self


class SecretFinding(StrictReleaseModel):
    rule: FindingRule
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    relative_path: str = Field(default="", exclude=True, repr=False)


class FilesystemScanResult(StrictReleaseModel):
    findings: tuple[SecretFinding, ...] = ()
    blockers: tuple[str, ...] = ()


class ArtifactKind(StrEnum):
    WHEEL = "wheel"
    SDIST = "sdist"
    PLUGIN = "plugin"
    SOURCE = "source"


class ArtifactScanResult(StrictReleaseModel):
    kinds: tuple[ArtifactKind, ...] = ()
    findings: tuple[SecretFinding, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()


class HostedSurfaceScanResult(StrictReleaseModel):
    surface: str
    scanner_version: str | None
    findings: tuple[SecretFinding, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()

    @model_validator(mode="after")
    def validate_safe_result(self) -> HostedSurfaceScanResult:
        if not _SAFE_SURFACE_PATTERN.fullmatch(self.surface):
            raise ValueError("surface_name_invalid")
        if self.scanner_version is not None and not _VERSION_PATTERN.fullmatch(
            self.scanner_version
        ):
            raise ValueError("scanner_version_invalid")
        if any(not _SHA256_PATTERN.fullmatch(value) for value in self.evidence_hashes):
            raise ValueError("evidence_hash_invalid")
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in self.blockers):
            raise ValueError("report_code_invalid")
        return self


class GitRepositoryScanResult(StrictReleaseModel):
    findings: tuple[SecretFinding, ...] = ()
    blockers: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    object_count: int = Field(default=0, ge=0)
    blob_count: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_safe_result(self) -> GitRepositoryScanResult:
        if any(not _SHA256_PATTERN.fullmatch(value) for value in self.evidence_hashes):
            raise ValueError("evidence_hash_invalid")
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in self.blockers):
            raise ValueError("report_code_invalid")
        return self


class GateStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class ScannerVersionAttestation(StrictReleaseModel):
    scanner: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str | None = None
    status: GateStatus
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    exit_code: int
    blocker_codes: tuple[str, ...] = ()

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str | None) -> str | None:
        if value is not None and not _VERSION_PATTERN.fullmatch(value):
            raise ValueError("scanner_version_invalid")
        return value

    @model_validator(mode="after")
    def validate_consistency(self) -> ScannerVersionAttestation:
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in self.blocker_codes):
            raise ValueError("report_code_invalid")
        if len(self.blocker_codes) != len(set(self.blocker_codes)):
            raise ValueError("scanner_attestation_inconsistent")
        expected = PINNED_SCANNER_VERSIONS.get(self.scanner)
        if self.status is GateStatus.PASSED and (
            expected is None
            or self.version != expected
            or self.exit_code != 0
            or self.blocker_codes
        ):
            raise ValueError("scanner_attestation_inconsistent")
        if self.status is not GateStatus.PASSED and not self.blocker_codes:
            raise ValueError("scanner_attestation_inconsistent")
        if self.exit_code != 0 and not self.blocker_codes:
            raise ValueError("scanner_attestation_inconsistent")
        if self.status is not GateStatus.PASSED:
            expected_status = (
                GateStatus.UNAVAILABLE
                if any(
                    marker in code
                    for code in self.blocker_codes
                    for marker in _UNAVAILABLE_CODE_MARKERS
                )
                else GateStatus.BLOCKED
            )
            if self.status is not expected_status:
                raise ValueError("scanner_attestation_inconsistent")
        return self


class SurfaceAttestation(StrictReleaseModel):
    surface: str
    status: GateStatus
    scanner_versions: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime
    finding_count: int = Field(ge=0)
    evidence_hashes: tuple[str, ...] = ()
    exit_codes: tuple[int, ...] = ()
    blocker_codes: tuple[str, ...] = ()
    finding_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_safe_attestation(self) -> SurfaceAttestation:
        if not _SAFE_SURFACE_PATTERN.fullmatch(self.surface):
            raise ValueError("surface_name_invalid")
        if any(not _VERSION_PATTERN.fullmatch(version) for version in self.scanner_versions):
            raise ValueError("scanner_version_invalid")
        if any(not _SHA256_PATTERN.fullmatch(value) for value in self.evidence_hashes):
            raise ValueError("evidence_hash_invalid")
        codes = self.blocker_codes + self.finding_codes
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in codes):
            raise ValueError("report_code_invalid")
        if self.completed_at < self.started_at:
            raise ValueError("attestation_time_invalid")
        if len(self.scanner_versions) != len(set(self.scanner_versions)):
            raise ValueError("surface_scanner_versions_invalid")
        expected_versions = EXPECTED_SURFACE_SCANNER_VERSIONS.get(self.surface)
        allowed_versions = set(PINNED_SCANNER_VERSIONS.values()) | {
            BUILTIN_SCANNER_VERSION
        }
        if any(version not in allowed_versions for version in self.scanner_versions):
            raise ValueError("surface_scanner_versions_invalid")
        if len(self.blocker_codes) != len(set(self.blocker_codes)) or len(
            self.finding_codes
        ) != len(set(self.finding_codes)):
            raise ValueError("surface_codes_duplicate")
        if (self.finding_count == 0) != (not self.finding_codes):
            raise ValueError("surface_finding_evidence_inconsistent")
        if any(exit_code != 0 for exit_code in self.exit_codes) and not self.blocker_codes:
            raise ValueError("surface_exit_codes_inconsistent")
        if self.blocker_codes:
            expected_status = (
                GateStatus.UNAVAILABLE
                if any(
                    marker in code
                    for code in self.blocker_codes
                    for marker in _UNAVAILABLE_CODE_MARKERS
                )
                else GateStatus.BLOCKED
            )
        elif self.finding_codes:
            expected_status = GateStatus.BLOCKED
        else:
            expected_status = GateStatus.PASSED
        if self.status is not expected_status:
            raise ValueError("surface_status_inconsistent")
        if self.status is GateStatus.PASSED and (
            self.blocker_codes
            or self.finding_codes
            or self.finding_count
            or any(exit_code != 0 for exit_code in self.exit_codes)
            or expected_versions is None
            or self.scanner_versions != expected_versions
        ):
            raise ValueError("surface_pass_inconsistent")
        return self


class SecretScanReport(StrictReleaseModel):
    status: GateStatus
    started_at: datetime
    completed_at: datetime
    scanner_versions: tuple[ScannerVersionAttestation, ...] = ()
    surfaces: tuple[SurfaceAttestation, ...]
    blockers: tuple[str, ...] = ()
    finding_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_safe_report(self) -> SecretScanReport:
        surface_names = tuple(surface.surface for surface in self.surfaces)
        if surface_names != REQUIRED_PUBLIC_SURFACES:
            raise ValueError("report_surface_corpus_invalid")
        scanner_names = tuple(scanner.scanner for scanner in self.scanner_versions)
        if scanner_names != tuple(PINNED_SCANNER_VERSIONS):
            raise ValueError("report_scanner_corpus_invalid")
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in self.blockers):
            raise ValueError("report_code_invalid")
        if any(not _SAFE_CODE_PATTERN.fullmatch(code) for code in self.finding_codes):
            raise ValueError("report_code_invalid")
        if self.completed_at < self.started_at:
            raise ValueError("report_time_invalid")
        if self.status is GateStatus.PASSED and (self.blockers or self.finding_codes):
            raise ValueError("passing_report_has_failures")
        if self.status is GateStatus.PASSED and any(
            surface.status is not GateStatus.PASSED for surface in self.surfaces
        ):
            raise ValueError("passing_report_has_blocked_surface")
        if self.status is GateStatus.PASSED and any(
            scanner.status is not GateStatus.PASSED for scanner in self.scanner_versions
        ):
            raise ValueError("passing_report_has_blocked_scanner")
        expected_blockers = tuple(
            sorted(
                {
                    code
                    for scanner in self.scanner_versions
                    for code in scanner.blocker_codes
                }
                | {
                    code
                    for surface in self.surfaces
                    for code in surface.blocker_codes
                }
            )
        )
        if self.blockers != expected_blockers:
            raise ValueError("report_blockers_inconsistent")
        expected_findings = tuple(
            sorted(
                {
                    code
                    for surface in self.surfaces
                    for code in surface.finding_codes
                }
            )
        )
        if self.finding_codes != expected_findings:
            raise ValueError("report_findings_inconsistent")
        expected_status = (
            GateStatus.PASSED
            if not expected_blockers
            and not expected_findings
            and all(
                scanner.status is GateStatus.PASSED
                for scanner in self.scanner_versions
            )
            and all(surface.status is GateStatus.PASSED for surface in self.surfaces)
            else GateStatus.BLOCKED
        )
        if self.status is not expected_status:
            raise ValueError("report_status_inconsistent")
        if any(
            surface.started_at < self.started_at
            or surface.completed_at > self.completed_at
            for surface in self.surfaces
        ):
            raise ValueError("report_time_inconsistent")
        return self

    @property
    def passed(self) -> bool:
        return self.status is GateStatus.PASSED

    @property
    def findings(self) -> tuple[str, ...]:
        return self.finding_codes

    def public_dict(self) -> dict[str, object]:
        payload = self.model_dump(mode="json")
        payload["passed"] = self.passed
        payload["surface_count"] = len(self.surfaces)
        payload["finding_count"] = sum(surface.finding_count for surface in self.surfaces)
        return payload
