"""Fail-closed public release scanner orchestration."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, TextIO

from pydantic import ValidationError

from mercury_tools.release.models import (
    REQUIRED_PUBLIC_SURFACES,
    ArtifactKind,
    ArtifactScanResult,
    FilesystemScanResult,
    GateStatus,
    GitRepositoryScanResult,
    PublicSurfaceManifest,
    ScannerVersionAttestation,
    SecretFinding,
    SecretScanAllowlist,
    SecretScanPolicy,
    SecretScanReport,
    SecretScanRequest,
    SurfaceAttestation,
)

_VERSION_IN_OUTPUT = re.compile(rb"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])")
_SCANNER_VERSION_COMMANDS = {
    "gitleaks": ("version",),
    "trufflehog": ("--version",),
}
_PROVIDER_TOKEN_PATTERNS = (
    re.compile(rb"AKIA[0-9A-Z]{16}"),
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,255}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,255}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{16,255}"),
    re.compile(rb"(?:sk|pk|rk)_live_[A-Za-z0-9]{16,255}"),
    re.compile(rb"sk-(?:proj-)?[A-Za-z0-9_-]{20,255}"),
    re.compile(rb"AIza[0-9A-Za-z_-]{30,255}"),
    re.compile(rb"ya29\.[0-9A-Za-z_-]{20,255}"),
    re.compile(rb"[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    rb"(?i)(?:api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|"
    rb"password|passwd|pwd)\s*[:=]\s*[\"']?([A-Za-z0-9_./+=:-]{8,512})"
)
_TOKEN_CANDIDATE = re.compile(rb"[A-Za-z0-9_./+=:-]{8,512}")
_HIGH_ENTROPY_CANDIDATE = re.compile(rb"[A-Za-z0-9+/=_-]{32,256}")
_FORBIDDEN_FILE_STEMS = frozenset(
    {
        "credential",
        "credentials",
        "credential-store",
        "credentials-store",
        "audit-ledger",
        "provider-payload",
        "provider-response",
        "raw-provider-payload",
        "raw-provider-response",
        "validation-traffic",
        "validation-raw-traffic",
        "downloaded-provider-payload",
    }
)


class ReleaseGateError(RuntimeError):
    """A constant-code release gate failure."""


@dataclass(frozen=True, repr=False)
class CommandResult:
    exit_code: int
    stdout: bytes = field(repr=False)
    stderr: bytes = field(repr=False)


class CommandRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=input_bytes,
                capture_output=True,
                check=False,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            return CommandResult(exit_code=127, stdout=b"", stderr=b"")
        return CommandResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def require_scanner(name: str) -> Path:
    binary = shutil.which(name)
    if binary is None:
        raise ReleaseGateError(f"scanner_missing:{name}")
    return Path(binary)


def validate_allowlist(allowlist: SecretScanAllowlist, *, at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ReleaseGateError("allowlist_validation_time_invalid")
    if any(entry.expires_at <= at for entry in allowlist.entries):
        raise ReleaseGateError("allowlist_expired")


def load_public_surface_manifest(path: Path) -> PublicSurfaceManifest:
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8"))
        return PublicSurfaceManifest.model_validate(payload)
    except (OSError, UnicodeError, ValueError, TypeError, ValidationError) as exc:
        raise ReleaseGateError("public_surface_manifest_malformed") from exc


def load_secret_scan_allowlist(path: Path) -> SecretScanAllowlist:
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8"))
        return SecretScanAllowlist.model_validate(payload)
    except (OSError, UnicodeError, ValueError, TypeError, ValidationError) as exc:
        raise ReleaseGateError("secret_scan_allowlist_malformed") from exc


def _strict_json_loads(value: str) -> object:
    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=unique_object)


def load_known_secret_digests(
    *,
    paths: tuple[Path, ...],
    interactive: bool,
    repo_root: Path,
    stdin: TextIO | None = None,
) -> tuple[str, ...]:
    values: list[str] = []
    if interactive:
        stream = stdin or __import__("sys").stdin
        if not stream.isatty():
            raise ReleaseGateError("fingerprint_stdin_not_interactive")
        while True:
            line = stream.readline()
            if line == "":
                break
            value = line.strip()
            if not value:
                break
            values.append(value)

    for path in paths:
        if path.is_symlink():
            raise ReleaseGateError("fingerprint_source_symlink")
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise ReleaseGateError("fingerprint_source_unavailable")
        _reject_tracked_fingerprint_source(resolved)
        try:
            values.extend(
                line.strip()
                for line in resolved.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except (OSError, UnicodeError) as exc:
            raise ReleaseGateError("fingerprint_source_unavailable") from exc

    if any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in values):
        raise ReleaseGateError("known_credential_fingerprint_invalid")
    return tuple(dict.fromkeys(values))


def _reject_tracked_fingerprint_source(path: Path) -> None:
    try:
        repository = subprocess.run(
            ("git", "-C", str(path.parent), "rev-parse", "--show-toplevel"),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseGateError("fingerprint_source_unverifiable") from exc
    if repository.returncode != 0:
        return
    try:
        repository_root = Path(repository.stdout.decode("utf-8").strip()).resolve()
        relative = path.relative_to(repository_root)
    except (UnicodeError, ValueError) as exc:
        raise ReleaseGateError("fingerprint_source_unverifiable") from exc
    try:
        tracked = subprocess.run(
            (
                "git",
                "-C",
                str(repository_root),
                "ls-files",
                "--error-unmatch",
                "--",
                relative.as_posix(),
            ),
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseGateError("fingerprint_source_unverifiable") from exc
    if tracked.returncode == 0:
        raise ReleaseGateError("fingerprint_source_tracked")
    if tracked.returncode != 1:
        raise ReleaseGateError("fingerprint_source_unverifiable")


def build_blocked_report(blocker: str, *, at: datetime | None = None) -> SecretScanReport:
    timestamp = at or datetime.now(UTC)
    return SecretScanReport(
        status=GateStatus.BLOCKED,
        started_at=timestamp,
        completed_at=timestamp,
        surfaces=_blocked_surfaces(timestamp, "scan_prerequisite_failed"),
        blockers=(blocker,),
    )


def apply_allowlist(
    findings: tuple[SecretFinding, ...],
    allowlist: SecretScanAllowlist,
    *,
    at: datetime,
) -> tuple[SecretFinding, ...]:
    validate_allowlist(allowlist, at=at)
    allowed = {
        (entry.file, entry.rule, entry.digest)
        for entry in allowlist.entries
    }
    return tuple(
        finding
        for finding in findings
        if (finding.relative_path, finding.rule, finding.evidence_sha256) not in allowed
    )


def _raw_evidence_hash(result: CommandResult, scanner_name: str) -> str:
    if not isinstance(result.stdout, bytes) or not isinstance(result.stderr, bytes):
        raise ReleaseGateError(f"raw_evidence_handling_failed:{scanner_name}")
    return hashlib.sha256(result.stdout + b"\0" + result.stderr).hexdigest()


def _scanner_versions(
    request: SecretScanRequest,
    command_runner: CommandRunner,
) -> tuple[
    tuple[ScannerVersionAttestation, ...],
    tuple[str, ...],
    dict[str, Path],
]:
    binaries: dict[str, Path] = {}
    blockers: list[str] = []
    for scanner_name in request.policy.scanner_versions:
        try:
            binaries[scanner_name] = require_scanner(scanner_name)
        except ReleaseGateError as exc:
            blockers.append(str(exc))
    if blockers:
        return (), tuple(blockers), binaries

    attestations: list[ScannerVersionAttestation] = []
    for scanner_name, expected_version in request.policy.scanner_versions.items():
        try:
            result = command_runner.run(
                (str(binaries[scanner_name]), *_SCANNER_VERSION_COMMANDS[scanner_name])
            )
        except Exception:
            blockers.append(f"scanner_command_failed:{scanner_name}:version")
            continue
        try:
            evidence_sha256 = _raw_evidence_hash(result, scanner_name)
        except ReleaseGateError as exc:
            blockers.append(str(exc))
            continue
        matches = _VERSION_IN_OUTPUT.findall(result.stdout + b"\n" + result.stderr)
        version = matches[0].decode("ascii") if len(matches) == 1 else None
        status = GateStatus.PASSED
        if result.exit_code != 0:
            blockers.append(f"scanner_command_failed:{scanner_name}")
            status = GateStatus.BLOCKED
        elif version is None:
            blockers.append(f"scanner_version_unverifiable:{scanner_name}")
            status = GateStatus.BLOCKED
        elif version != expected_version:
            blockers.append(f"scanner_version_unpinned:{scanner_name}")
            status = GateStatus.BLOCKED
        attestations.append(
            ScannerVersionAttestation(
                scanner=scanner_name,
                version=version,
                status=status,
                evidence_sha256=evidence_sha256,
                exit_code=result.exit_code,
            )
        )
    return tuple(attestations), tuple(blockers), binaries


def _blocked_surfaces(at: datetime, blocker_code: str) -> tuple[SurfaceAttestation, ...]:
    return tuple(
        SurfaceAttestation(
            surface=surface,
            status=GateStatus.BLOCKED,
            started_at=at,
            completed_at=at,
            finding_count=0,
            blocker_codes=(blocker_code,),
        )
        for surface in REQUIRED_PUBLIC_SURFACES
    )


def scan_public_release(
    request: SecretScanRequest,
    *,
    command_runner: CommandRunner | None = None,
    hosted_clients: Mapping[str, object] | None = None,
) -> SecretScanReport:
    started_at = datetime.now(UTC)
    blockers = [
        f"hosted_surface_inaccessible:{surface.name}"
        for surface in request.hosted_surfaces
        if not surface.accessible
    ]
    if not request.all_history:
        blockers.append("history_scan_disabled")
    if not request.hosted:
        blockers.append("hosted_scan_disabled")
    try:
        validate_allowlist(request.allowlist, at=started_at)
    except ReleaseGateError as exc:
        blockers.append(str(exc))

    runner = command_runner or SubprocessCommandRunner()
    scanner_versions, scanner_blockers, scanner_binaries = _scanner_versions(
        request,
        runner,
    )
    blockers.extend(scanner_blockers)
    blockers = sorted(set(blockers))
    completed_at = datetime.now(UTC)
    if blockers:
        return SecretScanReport(
            status=GateStatus.BLOCKED,
            started_at=started_at,
            completed_at=completed_at,
            scanner_versions=scanner_versions,
            surfaces=_blocked_surfaces(completed_at, "scan_prerequisite_failed"),
            blockers=tuple(blockers),
        )

    git_result = scan_git_repository(
        request.repo_url or f"https://github.com/{request.repo}.git",
        request.policy,
        scanner_binaries=scanner_binaries,
        command_runner=runner,
    )
    artifact_result = scan_artifacts(request.artifacts, request.policy)

    from mercury_tools.release.hosted import (
        HOSTED_PUBLIC_SURFACES,
        HOSTED_SCANNER_VERSION,
        scan_hosted_surface,
    )
    from mercury_tools.release.models import HostedSurfaceScanResult

    hosted_results: dict[str, HostedSurfaceScanResult] = {}
    client_map = dict(hosted_clients or {})
    for surface in HOSTED_PUBLIC_SURFACES:
        client = client_map.get(surface)
        if client is None:
            hosted_results[surface] = HostedSurfaceScanResult(
                surface=surface,
                scanner_version=None,
                blockers=(f"hosted_client_missing:{surface}",),
            )
        else:
            hosted_results[surface] = scan_hosted_surface(surface, client, request.policy)

    completed_at = datetime.now(UTC)
    verified_versions = tuple(
        attestation.version
        for attestation in scanner_versions
        if attestation.version is not None
    )
    git_attestation = _surface_attestation(
        "git_all_refs",
        findings=git_result.findings,
        blockers=git_result.blockers,
        evidence_hashes=git_result.evidence_hashes,
        exit_codes=git_result.exit_codes,
        scanner_versions=(*verified_versions, HOSTED_SCANNER_VERSION),
        allowlist=request.allowlist,
        at=completed_at,
    )
    pull_attestation = git_attestation.model_copy(
        update={"surface": "github_pull_request_refs"}
    )
    artifact_attestation = _surface_attestation(
        "wheel_sdist_plugin_source_archives",
        findings=artifact_result.findings,
        blockers=artifact_result.blockers,
        evidence_hashes=artifact_result.evidence_hashes,
        exit_codes=artifact_result.exit_codes,
        scanner_versions=(HOSTED_SCANNER_VERSION,),
        allowlist=request.allowlist,
        at=completed_at,
    )
    attestation_map = {
        "git_all_refs": git_attestation,
        "github_pull_request_refs": pull_attestation,
        "wheel_sdist_plugin_source_archives": artifact_attestation,
    }
    for surface, result in hosted_results.items():
        attestation_map[surface] = _surface_attestation(
            surface,
            findings=result.findings,
            blockers=result.blockers,
            evidence_hashes=result.evidence_hashes,
            exit_codes=result.exit_codes,
            scanner_versions=(result.scanner_version,) if result.scanner_version else (),
            allowlist=request.allowlist,
            at=completed_at,
        )
    surfaces = tuple(attestation_map[surface] for surface in REQUIRED_PUBLIC_SURFACES)
    report_blockers = tuple(
        sorted({code for surface in surfaces for code in surface.blocker_codes})
    )
    report_findings = tuple(
        sorted({code for surface in surfaces for code in surface.finding_codes})
    )
    status = (
        GateStatus.PASSED
        if not report_blockers and not report_findings
        else GateStatus.BLOCKED
    )
    return SecretScanReport(
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        scanner_versions=scanner_versions,
        surfaces=surfaces,
        blockers=report_blockers,
        finding_codes=report_findings,
    )


def _surface_attestation(
    surface: str,
    *,
    findings: tuple[SecretFinding, ...],
    blockers: tuple[str, ...],
    evidence_hashes: tuple[str, ...],
    exit_codes: tuple[int, ...],
    scanner_versions: tuple[str, ...],
    allowlist: SecretScanAllowlist,
    at: datetime,
) -> SurfaceAttestation:
    unresolved = apply_allowlist(findings, allowlist, at=at)
    finding_codes = tuple(
        sorted({f"finding:{finding.rule.value}" for finding in unresolved})
    )
    if blockers:
        unavailable_markers = ("unavailable", "inaccessible", "client_missing", "disabled")
        status = (
            GateStatus.UNAVAILABLE
            if any(marker in code for code in blockers for marker in unavailable_markers)
            else GateStatus.BLOCKED
        )
    elif finding_codes:
        status = GateStatus.BLOCKED
    else:
        status = GateStatus.PASSED
    return SurfaceAttestation(
        surface=surface,
        status=status,
        scanner_versions=tuple(sorted(set(scanner_versions))),
        started_at=at,
        completed_at=at,
        finding_count=len(unresolved),
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_codes=tuple(sorted(set(blockers))),
        finding_codes=finding_codes,
    )


def scan_filesystem(root: Path, _policy: SecretScanPolicy) -> FilesystemScanResult:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    if not root.is_dir():
        return FilesystemScanResult(blockers=("filesystem_unavailable",))
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        relative_name = relative.as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        if path.is_symlink():
            blockers.append("filesystem_symlink")
            continue
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > _policy.max_file_bytes:
                blockers.append("filesystem_file_too_large")
                continue
            data = path.read_bytes()
        except OSError:
            blockers.append("filesystem_read_failed")
            continue
        findings.extend(_scan_bytes(data, relative_name, _policy))
    return FilesystemScanResult(
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
    )


def scan_artifacts(root: Path, policy: SecretScanPolicy) -> ArtifactScanResult:
    if not root.is_dir():
        return ArtifactScanResult(
            blockers=(
                "artifact_surface_unavailable",
                *(f"artifact_kind_missing:{kind.value}" for kind in ArtifactKind),
            )
        )

    discovered: dict[Path, ArtifactKind] = {}
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    evidence_hashes: list[str] = []
    for path in sorted(root.rglob("*")):
        relative_name = path.relative_to(root).as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        if path.is_symlink():
            blockers.append("artifact_symlink")
            continue
        if not path.is_file():
            continue
        kind = _artifact_kind(path)
        if kind is None:
            try:
                sidecar_size = path.stat().st_size
                evidence_hashes.append(_hash_file(path))
                if sidecar_size > policy.max_file_bytes:
                    blockers.append("artifact_sidecar_too_large")
                    continue
                findings.extend(_scan_bytes(path.read_bytes(), relative_name, policy))
            except OSError:
                blockers.append("artifact_sidecar_read_failed")
            continue
        discovered[path] = kind
        try:
            artifact_size = path.stat().st_size
            evidence_hashes.append(_hash_file(path))
        except OSError:
            blockers.append(f"artifact_read_failed:{kind.value}")
            continue
        if artifact_size > policy.max_archive_bytes:
            blockers.append(f"artifact_too_large:{kind.value}")
            continue
        archive_findings, archive_blockers = _scan_archive(path, kind, policy)
        findings.extend(archive_findings)
        blockers.extend(archive_blockers)

    kinds = tuple(kind for kind in ArtifactKind if kind in discovered.values())
    for kind in ArtifactKind:
        if kind not in kinds:
            blockers.append(f"artifact_kind_missing:{kind.value}")
    return ArtifactScanResult(
        kinds=kinds,
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
        evidence_hashes=tuple(evidence_hashes),
    )


def scan_git_repository(
    repo_url: str,
    policy: SecretScanPolicy,
    *,
    scanner_binaries: Mapping[str, Path],
    command_runner: CommandRunner | None = None,
) -> GitRepositoryScanResult:
    runner = command_runner or SubprocessCommandRunner()
    blockers: list[str] = []
    findings: list[SecretFinding] = []
    evidence_hashes: list[str] = []
    exit_codes: list[int] = []
    object_count = 0
    blob_count = 0

    if set(scanner_binaries) != {"gitleaks", "trufflehog"}:
        return GitRepositoryScanResult(blockers=("scanner_set_incomplete",))

    with tempfile.TemporaryDirectory(prefix="mercury-release-scan-") as temporary:
        clone = Path(temporary) / "repository"
        clone_result = _run_and_record(
            runner,
            ("git", "clone", "--no-checkout", "--origin", "origin", repo_url, str(clone)),
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_clone",
            blockers=blockers,
        )
        if clone_result is None or clone_result.exit_code != 0:
            return _git_result(
                findings,
                blockers,
                evidence_hashes,
                exit_codes,
                object_count,
                blob_count,
            )

        fetch_result = _run_and_record(
            runner,
            (
                "git",
                "fetch",
                "--force",
                "--prune",
                "origin",
                "+refs/heads/*:refs/remotes/origin/*",
                "+refs/tags/*:refs/tags/*",
                "+refs/pull/*/head:refs/remotes/pull/*/head",
            ),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_fetch",
            blockers=blockers,
        )
        if fetch_result is None or fetch_result.exit_code != 0:
            return _git_result(
                findings,
                blockers,
                evidence_hashes,
                exit_codes,
                object_count,
                blob_count,
            )

        ref_sets = _inventory_refs(
            runner,
            clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blockers=blockers,
        )
        if ref_sets is None or blockers:
            return _git_result(
                findings,
                blockers,
                evidence_hashes,
                exit_codes,
                object_count,
                blob_count,
            )
        remote_heads, local_refs = ref_sets

        checkout_ref = sorted(
            f"refs/remotes/origin/{ref.removeprefix('refs/heads/')}"
            for ref in remote_heads
        )[0]
        checkout_result = _run_and_record(
            runner,
            ("git", "checkout", "--force", "--detach", checkout_ref),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_checkout",
            blockers=blockers,
        )
        if checkout_result is None or checkout_result.exit_code != 0:
            return _git_result(
                findings,
                blockers,
                evidence_hashes,
                exit_codes,
                object_count,
                blob_count,
            )

        inventory = _scan_reachable_blobs(
            runner,
            clone,
            policy,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blockers=blockers,
        )
        if inventory is not None:
            inventory_findings, object_count, blob_count = inventory
            findings.extend(inventory_findings)

        checkout_scan = scan_filesystem(clone, policy)
        findings.extend(checkout_scan.findings)
        blockers.extend(checkout_scan.blockers)

        scanner_findings, scanner_blockers = _run_history_scanners(
            runner,
            clone,
            scanner_binaries,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
        )
        findings.extend(scanner_findings)
        blockers.extend(scanner_blockers)

        if "refs/remotes/origin/HEAD" in local_refs:
            local_refs.remove("refs/remotes/origin/HEAD")

    return _git_result(
        findings,
        blockers,
        evidence_hashes,
        exit_codes,
        object_count,
        blob_count,
    )


def _inventory_refs(
    runner: CommandRunner,
    clone: Path,
    *,
    evidence_hashes: list[str],
    exit_codes: list[int],
    blockers: list[str],
) -> tuple[set[str], set[str]] | None:
    commands = {
        "heads": ("git", "ls-remote", "--heads", "origin"),
        "tags": ("git", "ls-remote", "--tags", "--refs", "origin"),
        "pull_requests": ("git", "ls-remote", "origin", "refs/pull/*/head"),
        "local": (
            "git",
            "for-each-ref",
            "--format=%(refname)",
            "refs/remotes/origin",
            "refs/tags",
            "refs/remotes/pull",
        ),
    }
    outputs: dict[str, bytes] = {}
    for scope, command in commands.items():
        result = _run_and_record(
            runner,
            command,
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope=f"git_ref_inventory_{scope}",
            blockers=blockers,
        )
        if result is None or result.exit_code != 0:
            return None
        outputs[scope] = result.stdout

    try:
        remote_heads = _parse_ls_remote(outputs["heads"])
        remote_tags = _parse_ls_remote(outputs["tags"])
        remote_pulls = _parse_ls_remote(outputs["pull_requests"])
        local_refs = {
            line.decode("utf-8", errors="strict")
            for line in outputs["local"].splitlines()
            if line
        }
    except (ReleaseGateError, UnicodeError):
        blockers.append("git_ref_inventory_malformed")
        return None
    if not remote_heads:
        blockers.append("git_refs_incomplete:heads")
    expected = {
        "heads": {
            f"refs/remotes/origin/{ref.removeprefix('refs/heads/')}"
            for ref in remote_heads
        },
        "tags": remote_tags,
        "pull_requests": {
            f"refs/remotes/pull/{ref.removeprefix('refs/pull/')}"
            for ref in remote_pulls
        },
    }
    for scope, expected_refs in expected.items():
        if not expected_refs.issubset(local_refs):
            blockers.append(f"git_refs_incomplete:{scope}")
    return remote_heads, local_refs


def _parse_ls_remote(output: bytes) -> set[str]:
    refs: set[str] = set()
    for line in output.splitlines():
        parts = line.split(b"\t", 1)
        if len(parts) != 2:
            raise ReleaseGateError("git_ref_inventory_malformed")
        refs.add(parts[1].decode("utf-8", errors="strict"))
    return refs


def _scan_reachable_blobs(
    runner: CommandRunner,
    clone: Path,
    policy: SecretScanPolicy,
    *,
    evidence_hashes: list[str],
    exit_codes: list[int],
    blockers: list[str],
) -> tuple[list[SecretFinding], int, int] | None:
    rev_list = _run_and_record(
        runner,
        ("git", "rev-list", "--objects", "--all"),
        cwd=clone,
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_object_inventory",
        blockers=blockers,
    )
    if rev_list is None or rev_list.exit_code != 0:
        return None

    oid_paths: dict[str, set[str]] = {}
    for line in rev_list.stdout.splitlines():
        oid_raw, _, path_raw = line.partition(b" ")
        oid = oid_raw.decode("ascii", errors="strict")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid):
            blockers.append("git_object_inventory_malformed")
            return None
        path = path_raw.decode("utf-8", errors="strict") if path_raw else "git-object"
        oid_paths.setdefault(oid, set()).add(path)

    oids = tuple(sorted(oid_paths))
    type_result = _run_and_record(
        runner,
        ("git", "cat-file", "--batch-check=%(objectname) %(objecttype)"),
        cwd=clone,
        input_bytes=("\n".join(oids) + "\n").encode("ascii"),
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_object_type_inventory",
        blockers=blockers,
    )
    if type_result is None or type_result.exit_code != 0:
        return None
    object_types: dict[str, str] = {}
    for line in type_result.stdout.splitlines():
        parts = line.decode("ascii", errors="strict").split()
        if len(parts) != 2:
            blockers.append("git_object_inventory_malformed")
            return None
        object_types[parts[0]] = parts[1]
    if set(object_types) != set(oids):
        blockers.append("git_object_inventory_incomplete")
        return None

    findings: list[SecretFinding] = []
    blobs = [oid for oid in oids if object_types[oid] == "blob"]
    for oid in blobs:
        blob_result = _run_and_record(
            runner,
            ("git", "cat-file", "blob", oid),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_blob_read",
            blockers=blockers,
        )
        if blob_result is None or blob_result.exit_code != 0:
            continue
        if len(blob_result.stdout) > policy.max_file_bytes:
            blockers.append("git_blob_too_large")
            continue
        for path in oid_paths[oid]:
            if _is_forbidden_path(PurePosixPath(path)):
                findings.append(_path_finding("forbidden_path", path))
            findings.extend(_scan_bytes(blob_result.stdout, path, policy))
    return findings, len(object_types), len(blobs)


def _run_history_scanners(
    runner: CommandRunner,
    clone: Path,
    scanner_binaries: Mapping[str, Path],
    *,
    evidence_hashes: list[str],
    exit_codes: list[int],
) -> tuple[list[SecretFinding], list[str]]:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    commands = {
        "gitleaks": (
            str(scanner_binaries["gitleaks"]),
            "git",
            "--no-banner",
            "--redact",
            "--report-format",
            "json",
            "--report-path",
            "-",
            "--log-opts=--all",
            str(clone),
        ),
        "trufflehog": (
            str(scanner_binaries["trufflehog"]),
            "git",
            f"file://{clone}",
            "--json",
            "--no-update",
        ),
    }
    for scanner_name, command in commands.items():
        try:
            result = runner.run(command, cwd=clone)
        except Exception:
            blockers.append(f"scanner_command_failed:{scanner_name}:history")
            continue
        try:
            evidence_hashes.append(_raw_evidence_hash(result, scanner_name))
        except ReleaseGateError as exc:
            blockers.append(str(exc))
            continue
        exit_codes.append(result.exit_code)
        if scanner_name == "gitleaks":
            parsed, parse_blocker = _parse_gitleaks_findings(result.stdout)
            if result.exit_code not in {0, 1}:
                blockers.append("scanner_command_failed:gitleaks:history")
            elif result.exit_code == 1 and not parsed:
                parse_blocker = True
        else:
            parsed, parse_blocker = _parse_trufflehog_findings(result.stdout)
            if result.exit_code != 0:
                blockers.append("scanner_command_failed:trufflehog:history")
        if parse_blocker:
            blockers.append(f"raw_evidence_handling_failed:{scanner_name}")
        else:
            findings.extend(parsed)
    return findings, blockers


def _parse_gitleaks_findings(output: bytes) -> tuple[list[SecretFinding], bool]:
    try:
        payload = json.loads(output or b"[]")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], True
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        return [], True
    findings = [
        _scanner_finding(item, _safe_scanner_file(item.get("File"), "scanner/gitleaks"))
        for item in payload
    ]
    return findings, False


def _parse_trufflehog_findings(output: bytes) -> tuple[list[SecretFinding], bool]:
    findings: list[SecretFinding] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return [], True
        if not isinstance(item, dict):
            return [], True
        git_data = item.get("SourceMetadata", {}).get("Data", {}).get("Git", {})
        file_value = git_data.get("file") if isinstance(git_data, dict) else None
        findings.append(
            _scanner_finding(item, _safe_scanner_file(file_value, "scanner/trufflehog"))
        )
    return findings, False


def _safe_scanner_file(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        return fallback
    return candidate.as_posix()


def _scanner_finding(item: dict[str, object], relative_path: str) -> SecretFinding:
    raw = json.dumps(item, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _content_finding("scanner_finding", raw, relative_path)


def _run_and_record(
    runner: CommandRunner,
    argv: tuple[str, ...],
    *,
    evidence_hashes: list[str],
    exit_codes: list[int],
    blocker_scope: str,
    blockers: list[str],
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> CommandResult | None:
    try:
        result = runner.run(argv, cwd=cwd, input_bytes=input_bytes)
    except Exception:
        blockers.append(f"command_failed:{blocker_scope}")
        return None
    try:
        evidence_hashes.append(_raw_evidence_hash(result, blocker_scope))
    except ReleaseGateError:
        blockers.append(f"raw_evidence_handling_failed:{blocker_scope}")
        return None
    exit_codes.append(result.exit_code)
    if result.exit_code != 0:
        blockers.append(f"command_failed:{blocker_scope}")
    return result


def _git_result(
    findings: list[SecretFinding],
    blockers: list[str],
    evidence_hashes: list[str],
    exit_codes: list[int],
    object_count: int,
    blob_count: int,
) -> GitRepositoryScanResult:
    return GitRepositoryScanResult(
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
        evidence_hashes=tuple(evidence_hashes),
        exit_codes=tuple(exit_codes),
        object_count=object_count,
        blob_count=blob_count,
    )


def _artifact_kind(path: Path) -> ArtifactKind | None:
    name = path.name.casefold()
    if name.endswith(".whl"):
        return ArtifactKind.WHEEL
    if not _is_archive_name(name):
        return None
    if "plugin" in name:
        return ArtifactKind.PLUGIN
    if "source" in name or "repository" in name or "repo-archive" in name:
        return ArtifactKind.SOURCE
    return ArtifactKind.SDIST


def _is_archive_name(name: str) -> bool:
    return name.endswith((".whl", ".zip", ".tar", ".tar.gz", ".tgz"))


def _scan_archive(
    path: Path,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
) -> tuple[list[SecretFinding], list[str]]:
    try:
        if zipfile.is_zipfile(path):
            return _scan_zip(path, kind, policy)
        if tarfile.is_tarfile(path):
            return _scan_tar(path, kind, policy)
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        pass
    return [], [f"artifact_read_failed:{kind.value}"]


def _scan_zip(
    path: Path,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
) -> tuple[list[SecretFinding], list[str]]:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > policy.max_archive_entries:
                return [], [f"artifact_entry_limit:{kind.value}"]
            for entry in entries:
                logical_path = f"{path.name}!{entry.filename}"
                if _unsafe_archive_member(entry.filename) or _zip_entry_is_symlink(entry):
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    continue
                if entry.is_dir():
                    continue
                member_path = PurePosixPath(entry.filename)
                if _is_forbidden_path(member_path):
                    findings.append(_path_finding("forbidden_path", logical_path))
                if entry.file_size > policy.max_file_bytes:
                    blockers.append(f"artifact_member_too_large:{kind.value}")
                    continue
                data = archive.read(entry)
                findings.extend(_scan_bytes(data, logical_path, policy))
    except (OSError, RuntimeError, zipfile.BadZipFile):
        blockers.append(f"artifact_read_failed:{kind.value}")
    return findings, blockers


def _scan_tar(
    path: Path,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
) -> tuple[list[SecretFinding], list[str]]:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        with tarfile.open(path, "r:*") as archive:
            entries = archive.getmembers()
            if len(entries) > policy.max_archive_entries:
                return [], [f"artifact_entry_limit:{kind.value}"]
            for entry in entries:
                logical_path = f"{path.name}!{entry.name}"
                if _unsafe_archive_member(entry.name) or entry.issym() or entry.islnk():
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    continue
                if not entry.isfile():
                    continue
                if _is_forbidden_path(PurePosixPath(entry.name)):
                    findings.append(_path_finding("forbidden_path", logical_path))
                if entry.size > policy.max_file_bytes:
                    blockers.append(f"artifact_member_too_large:{kind.value}")
                    continue
                stream = archive.extractfile(entry)
                if stream is None:
                    blockers.append(f"artifact_read_failed:{kind.value}")
                    continue
                findings.extend(_scan_bytes(stream.read(), logical_path, policy))
    except (OSError, tarfile.TarError):
        blockers.append(f"artifact_read_failed:{kind.value}")
    return findings, blockers


def _unsafe_archive_member(name: str) -> bool:
    candidate = PurePosixPath(name)
    return (
        not name
        or candidate.is_absolute()
        or "\\" in name
        or ".." in candidate.parts
    )


def _zip_entry_is_symlink(entry: zipfile.ZipInfo) -> bool:
    return (entry.external_attr >> 16) & 0o170000 == 0o120000


def _is_forbidden_path(path: PurePosixPath) -> bool:
    lowered_parts = tuple(part.casefold() for part in path.parts)
    if any(
        part == ".mercury" or part == ".env" or part.startswith(".env.")
        for part in lowered_parts
    ):
        return True
    filename = lowered_parts[-1] if lowered_parts else ""
    stem = filename.split(".", 1)[0].replace("_", "-")
    return stem in _FORBIDDEN_FILE_STEMS


def _scan_bytes(
    data: bytes,
    relative_path: str,
    policy: SecretScanPolicy,
) -> tuple[SecretFinding, ...]:
    findings: list[SecretFinding] = []
    for pattern in _PROVIDER_TOKEN_PATTERNS:
        for match in pattern.finditer(data):
            findings.append(_content_finding("provider_token", match.group(0), relative_path))
    for match in _CREDENTIAL_ASSIGNMENT.finditer(data):
        candidate = match.group(1)
        findings.append(_content_finding("credential_assignment", candidate, relative_path))
        if _is_high_entropy(candidate):
            findings.append(_content_finding("high_entropy", candidate, relative_path))
    for match in _HIGH_ENTROPY_CANDIDATE.finditer(data):
        candidate = match.group(0)
        if _is_high_entropy(candidate):
            findings.append(_content_finding("high_entropy", candidate, relative_path))
    fingerprints = set(policy.known_secret_digests)
    if fingerprints:
        candidates = _known_fingerprint_candidates(data)
        for candidate in candidates:
            if hashlib.sha256(candidate).hexdigest() in fingerprints:
                findings.append(_content_finding("known_credential", candidate, relative_path))
    return _deduplicate_findings(findings)


def _is_high_entropy(candidate: bytes) -> bool:
    if (
        len(candidate) < 32
        or re.fullmatch(rb"[0-9a-fA-F]+", candidate)
        or re.fullmatch(
            rb"(?:md5|sha1|sha224|sha256|sha384|sha512)=[A-Za-z0-9_-]+",
            candidate,
            flags=re.IGNORECASE,
        )
    ):
        return False
    character_classes = sum(
        bool(re.search(pattern, candidate))
        for pattern in (rb"[a-z]", rb"[A-Z]", rb"[0-9]", rb"[+/=_-]")
    )
    if character_classes < 3:
        return False
    counts = Counter(candidate)
    entropy = -sum(
        (count / len(candidate)) * math.log2(count / len(candidate))
        for count in counts.values()
    )
    return entropy >= 4.0


def _known_fingerprint_candidates(data: bytes) -> set[bytes]:
    candidates = {match.group(0) for match in _TOKEN_CANDIDATE.finditer(data)}
    stripped = data.strip()
    if stripped:
        candidates.add(stripped)
    for line in data.splitlines():
        stripped_line = line.strip()
        if stripped_line:
            candidates.add(stripped_line)
        for separator in (b"=", b":"):
            if separator in stripped_line:
                value = stripped_line.split(separator, 1)[1].strip().strip(b"\"',")
                if value:
                    candidates.add(value)
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return candidates
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, str):
            candidates.add(value.encode("utf-8"))
        elif isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return candidates


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _content_finding(rule: str, candidate: bytes, relative_path: str) -> SecretFinding:
    digest = hashlib.sha256(rule.encode("ascii") + b"\0" + candidate).hexdigest()
    return SecretFinding(
        rule=rule,
        evidence_sha256=digest,
        relative_path=relative_path,
    )


def _path_finding(rule: str, relative_path: str) -> SecretFinding:
    digest = hashlib.sha256(rule.encode("ascii") + b"\0" + relative_path.encode()).hexdigest()
    return SecretFinding(
        rule=rule,
        evidence_sha256=digest,
        relative_path=relative_path,
    )


def _deduplicate_findings(findings: list[SecretFinding]) -> tuple[SecretFinding, ...]:
    unique: dict[tuple[str, object, str], SecretFinding] = {}
    for finding in findings:
        unique[(finding.relative_path, finding.rule, finding.evidence_sha256)] = finding
    ordered = sorted(unique, key=lambda item: (item[0], str(item[1]), item[2]))
    return tuple(unique[key] for key in ordered)
