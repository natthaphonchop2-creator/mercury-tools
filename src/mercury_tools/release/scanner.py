"""Fail-closed public release scanner orchestration."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import tarfile
import tempfile
import time
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, TextIO

from pydantic import ValidationError

from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
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
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult: ...


class SubprocessCommandRunner:
    def __init__(
        self,
        *,
        environment: Mapping[str, str] | None = None,
        max_output_bytes: int = 256 * 1024 * 1024,
        timeout_seconds: float = 300.0,
    ) -> None:
        self._environment = dict(environment or {})
        self._max_output_bytes = max_output_bytes
        self._timeout_seconds = timeout_seconds

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        output_limit = self._max_output_bytes if max_output_bytes is None else max_output_bytes
        command_timeout = self._timeout_seconds if timeout_seconds is None else timeout_seconds
        if output_limit <= 0 or command_timeout <= 0:
            return CommandResult(exit_code=127, stdout=b"", stderr=b"")
        process: subprocess.Popen[bytes] | None = None
        selector = selectors.DefaultSelector()
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, **self._environment},
                start_new_session=True,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stdout = bytearray()
            stderr = bytearray()
            total_output = 0
            for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, label)

            pending_input = memoryview(input_bytes or b"")
            input_offset = 0
            if process.stdin is not None:
                os.set_blocking(process.stdin.fileno(), False)
                if pending_input:
                    selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
                else:
                    process.stdin.close()

            deadline = time.monotonic() + command_timeout
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill_process(process)
                    return CommandResult(exit_code=124, stdout=b"", stderr=b"")
                events = selector.select(min(remaining, 0.1))
                for key, _mask in events:
                    stream = key.fileobj
                    if key.data == "stdin":
                        try:
                            written = os.write(
                                stream.fileno(),  # type: ignore[union-attr]
                                pending_input[input_offset : input_offset + 64 * 1024],
                            )
                        except BlockingIOError:
                            continue
                        except BrokenPipeError:
                            selector.unregister(stream)
                            stream.close()  # type: ignore[union-attr]
                            continue
                        input_offset += written
                        if input_offset >= len(pending_input):
                            with contextlib.suppress(Exception):
                                selector.unregister(stream)
                            stream.close()  # type: ignore[union-attr]
                        continue
                    try:
                        chunk = os.read(stream.fileno(), 64 * 1024)  # type: ignore[union-attr]
                    except BlockingIOError:
                        continue
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()  # type: ignore[union-attr]
                        continue
                    destination = stdout if key.data == "stdout" else stderr
                    total_output += len(chunk)
                    if (
                        len(destination) + len(chunk) > output_limit
                        or total_output > output_limit
                    ):
                        _kill_process(process)
                        return CommandResult(exit_code=125, stdout=b"", stderr=b"")
                    destination.extend(chunk)

            remaining = max(0.001, deadline - time.monotonic())
            try:
                exit_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _kill_process(process)
                return CommandResult(exit_code=124, stdout=b"", stderr=b"")
            return CommandResult(
                exit_code=exit_code,
                stdout=bytes(stdout),
                stderr=bytes(stderr),
            )
        except (OSError, subprocess.SubprocessError, ValueError):
            if process is not None:
                _kill_process(process)
            return CommandResult(exit_code=127, stdout=b"", stderr=b"")
        finally:
            selector.close()
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        with contextlib.suppress(OSError):
                            stream.close()


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (AttributeError, OSError):
            with contextlib.suppress(OSError):
                process.kill()
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        process.wait(timeout=1)


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


MAX_DURABLE_REPORT_BYTES = 1024 * 1024


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError("report_parent_invalid")
    return descriptor


def _fsync_directory(path: Path) -> None:
    descriptor = _open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _report_destination(path: Path) -> Path:
    if path.name in {"", ".", ".."}:
        raise OSError("report_output_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    parent = path.parent.resolve(strict=True)
    descriptor = _open_directory(parent)
    os.close(descriptor)
    return parent / path.name


def invalidate_report_output(path: Path) -> None:
    destination = _report_destination(path)
    try:
        metadata = destination.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(metadata.st_mode):
        raise OSError("report_output_invalid")
    os.unlink(destination)
    _fsync_directory(destination.parent)


def write_secret_scan_report(
    path: Path,
    payload: Mapping[str, object],
    *,
    max_bytes: int = MAX_DURABLE_REPORT_BYTES,
) -> None:
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(encoded) > max_bytes:
        raise OSError("report_too_large")

    destination = _report_destination(path)
    temporary: Path | None = None
    descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        for _attempt in range(128):
            candidate = destination.with_name(
                f".{destination.name}.tmp-{secrets.token_hex(16)}"
            )
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                continue
            temporary = candidate
            break
        if descriptor is None or temporary is None:
            raise OSError("report_temp_unavailable")
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("report_temp_invalid")
        created_identity = (metadata.st_dev, metadata.st_ino)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            written = stream.write(encoded)
            if written != len(encoded):
                raise OSError("report_write_incomplete")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        temporary = None
        _fsync_directory(destination.parent)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)
        if created_identity is not None:
            try:
                current = destination.lstat()
                if (current.st_dev, current.st_ino) == created_identity:
                    os.unlink(destination)
                    _fsync_directory(destination.parent)
            except OSError:
                pass
        raise


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


def _constant_evidence_hash(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("ascii")).hexdigest()


def _blocked_scanner_attestations(
    blockers: tuple[str, ...],
) -> tuple[ScannerVersionAttestation, ...]:
    unavailable_markers = ("unavailable", "inaccessible", "client_missing", "missing", "disabled")
    status = (
        GateStatus.UNAVAILABLE
        if any(marker in code for code in blockers for marker in unavailable_markers)
        else GateStatus.BLOCKED
    )
    return tuple(
        ScannerVersionAttestation(
            scanner=name,
            version=None,
            status=status,
            evidence_sha256=_constant_evidence_hash("scanner", name, "unavailable"),
            exit_code=127,
            blocker_codes=blockers,
        )
        for name in PINNED_SCANNER_VERSIONS
    )


def build_blocked_report(blocker: str, *, at: datetime | None = None) -> SecretScanReport:
    timestamp = at or datetime.now(UTC)
    blockers = (blocker,)
    return SecretScanReport(
        status=GateStatus.BLOCKED,
        started_at=timestamp,
        completed_at=timestamp,
        scanner_versions=_blocked_scanner_attestations(blockers),
        surfaces=_blocked_surfaces(timestamp, blockers),
        blockers=blockers,
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
    attestations: list[ScannerVersionAttestation] = []
    for scanner_name in request.policy.scanner_versions:
        try:
            binaries[scanner_name] = require_scanner(scanner_name)
        except ReleaseGateError as exc:
            blocker = str(exc)
            blockers.append(blocker)
            attestations.append(
                ScannerVersionAttestation(
                    scanner=scanner_name,
                    version=None,
                    status=GateStatus.UNAVAILABLE,
                    evidence_sha256=_constant_evidence_hash(
                        "scanner", scanner_name, "missing"
                    ),
                    exit_code=127,
                    blocker_codes=(blocker,),
                )
            )
    for scanner_name, expected_version in request.policy.scanner_versions.items():
        if scanner_name not in binaries:
            continue
        scanner_blockers: list[str] = []
        try:
            result = command_runner.run(
                (str(binaries[scanner_name]), *_SCANNER_VERSION_COMMANDS[scanner_name])
            )
        except Exception:
            blocker = f"scanner_command_failed:{scanner_name}:version"
            blockers.append(blocker)
            attestations.append(
                ScannerVersionAttestation(
                    scanner=scanner_name,
                    version=None,
                    status=GateStatus.BLOCKED,
                    evidence_sha256=_constant_evidence_hash(
                        "scanner", scanner_name, "command_failed"
                    ),
                    exit_code=127,
                    blocker_codes=(blocker,),
                )
            )
            continue
        try:
            evidence_sha256 = _raw_evidence_hash(result, scanner_name)
        except ReleaseGateError as exc:
            blocker = str(exc)
            blockers.append(blocker)
            attestations.append(
                ScannerVersionAttestation(
                    scanner=scanner_name,
                    version=None,
                    status=GateStatus.BLOCKED,
                    evidence_sha256=_constant_evidence_hash(
                        "scanner", scanner_name, "evidence_failed"
                    ),
                    exit_code=result.exit_code,
                    blocker_codes=(blocker,),
                )
            )
            continue
        matches = _VERSION_IN_OUTPUT.findall(result.stdout + b"\n" + result.stderr)
        version = matches[0].decode("ascii") if len(matches) == 1 else None
        status = GateStatus.PASSED
        if result.exit_code != 0:
            scanner_blockers.append(f"scanner_command_failed:{scanner_name}")
            status = GateStatus.BLOCKED
        elif version is None:
            scanner_blockers.append(f"scanner_version_unverifiable:{scanner_name}")
            status = GateStatus.BLOCKED
        elif version != expected_version:
            scanner_blockers.append(f"scanner_version_unpinned:{scanner_name}")
            status = GateStatus.BLOCKED
        blockers.extend(scanner_blockers)
        attestations.append(
            ScannerVersionAttestation(
                scanner=scanner_name,
                version=version,
                status=status,
                evidence_sha256=evidence_sha256,
                exit_code=result.exit_code,
                blocker_codes=tuple(scanner_blockers),
            )
        )
    ordered = {
        attestation.scanner: attestation
        for attestation in attestations
    }
    return (
        tuple(ordered[name] for name in request.policy.scanner_versions),
        tuple(sorted(set(blockers))),
        binaries,
    )


def _blocked_surfaces(
    at: datetime,
    blocker_codes: tuple[str, ...],
) -> tuple[SurfaceAttestation, ...]:
    unavailable_markers = ("unavailable", "inaccessible", "client_missing", "missing", "disabled")
    status = (
        GateStatus.UNAVAILABLE
        if any(marker in code for code in blocker_codes for marker in unavailable_markers)
        else GateStatus.BLOCKED
    )
    return tuple(
        SurfaceAttestation(
            surface=surface,
            status=status,
            started_at=at,
            completed_at=at,
            finding_count=0,
            blocker_codes=blocker_codes,
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
            surfaces=_blocked_surfaces(completed_at, tuple(blockers)),
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
    pull_result = hosted_results["github_pull_request_refs"]
    pull_attestation = _surface_attestation(
        "github_pull_request_refs",
        findings=(*git_result.findings, *pull_result.findings),
        blockers=(*git_result.blockers, *pull_result.blockers),
        evidence_hashes=(*git_result.evidence_hashes, *pull_result.evidence_hashes),
        exit_codes=(*git_result.exit_codes, *pull_result.exit_codes),
        scanner_versions=(*verified_versions, HOSTED_SCANNER_VERSION),
        allowlist=request.allowlist,
        at=completed_at,
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
        if surface == "github_pull_request_refs":
            continue
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


class _BudgetExceeded(RuntimeError):
    pass


class _TraversalError(RuntimeError):
    pass


@dataclass
class _ByteBudget:
    limit: int
    used: int = 0

    def reserve(self, size: int) -> None:
        if size < 0 or size > self.limit - self.used:
            raise _BudgetExceeded
        self.used += size


def _walk_paths(root: Path, *, skip_git: bool = False):
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(root, flags)
    except OSError as error:
        raise _TraversalError from error
    try:
        opened = os.fstat(descriptor)
        _verify_directory_identity(root, opened)
        yield from _walk_directory(
            descriptor,
            root,
            skip_git=skip_git,
            flags=flags,
        )
        _verify_directory_identity(root, opened)
    except (OSError, ValueError) as error:
        raise _TraversalError from error
    finally:
        os.close(descriptor)


def _walk_directory(
    descriptor: int,
    directory: Path,
    *,
    skip_git: bool,
    flags: int,
):
    before = os.fstat(descriptor)
    if not stat.S_ISDIR(before.st_mode):
        raise OSError("directory_changed")
    _verify_directory_identity(directory, before)
    with os.scandir(descriptor) as iterator:
        entries = sorted(iterator, key=lambda entry: entry.name)
    after_enumeration = os.fstat(descriptor)
    if _stat_identity(after_enumeration) != _stat_identity(before):
        raise OSError("directory_changed")
    _verify_directory_identity(directory, before)

    for entry in entries:
        if skip_git and entry.name == ".git":
            continue
        if not entry.name or "/" in entry.name or entry.name in {".", ".."}:
            raise OSError("directory_entry_invalid")
        child = directory / entry.name
        metadata = entry.stat(follow_symlinks=False)
        current = child.lstat()
        if _stat_identity(current) != _stat_identity(metadata):
            raise OSError("directory_entry_changed")
        yield child
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        child_descriptor = os.open(entry.name, flags, dir_fd=descriptor)
        try:
            opened = os.fstat(child_descriptor)
            if _stat_identity(opened) != _stat_identity(metadata):
                raise OSError("directory_changed")
            _verify_directory_identity(child, opened)
            yield from _walk_directory(
                child_descriptor,
                child,
                skip_git=skip_git,
                flags=flags,
            )
            _verify_directory_identity(child, opened)
        finally:
            os.close(child_descriptor)

    after_walk = os.fstat(descriptor)
    if _stat_identity(after_walk) != _stat_identity(before):
        raise OSError("directory_changed")
    _verify_directory_identity(directory, before)


def _stat_identity(metadata: os.stat_result) -> tuple[int, int, int]:
    return metadata.st_dev, metadata.st_ino, metadata.st_mode


def _verify_directory_identity(path: Path, expected: os.stat_result) -> None:
    current = path.lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or _stat_identity(current) != _stat_identity(expected)
    ):
        raise OSError("directory_changed")


def _read_regular_file(
    path: Path,
    metadata: os.stat_result,
    *,
    max_bytes: int,
) -> bytes:
    if metadata.st_size > max_bytes:
        raise _BudgetExceeded
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _stat_identity(opened) != _stat_identity(metadata)
            or opened.st_size != metadata.st_size
        ):
            raise OSError("file_changed")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise OSError("file_truncated")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise _BudgetExceeded
        closed_over = os.fstat(descriptor)
        if (
            _stat_identity(closed_over) != _stat_identity(opened)
            or closed_over.st_size != opened.st_size
        ):
            raise OSError("file_changed")
        _verify_regular_file_identity(path, metadata)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_regular_file_identity(path: Path, expected: os.stat_result) -> None:
    current = path.lstat()
    if (
        not stat.S_ISREG(current.st_mode)
        or _stat_identity(current) != _stat_identity(expected)
        or current.st_size != expected.st_size
    ):
        raise OSError("file_changed")


def scan_filesystem(root: Path, _policy: SecretScanPolicy) -> FilesystemScanResult:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        root_metadata = root.lstat()
    except OSError:
        return FilesystemScanResult(blockers=("filesystem_unavailable",))
    if stat.S_ISLNK(root_metadata.st_mode):
        return FilesystemScanResult(blockers=("filesystem_symlink",))
    if not stat.S_ISDIR(root_metadata.st_mode):
        return FilesystemScanResult(blockers=("filesystem_unavailable",))
    entry_count = 0
    byte_budget = _ByteBudget(_policy.max_filesystem_bytes)
    paths = _walk_paths(root, skip_git=True)
    while True:
        try:
            path = next(paths)
        except StopIteration:
            break
        except _TraversalError:
            blockers.append("filesystem_traversal_failed")
            break
        entry_count += 1
        if entry_count > _policy.max_filesystem_entries:
            blockers.append("filesystem_entry_limit")
            break
        relative = path.relative_to(root)
        relative_name = relative.as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        try:
            metadata = path.lstat()
        except OSError:
            blockers.append("filesystem_read_failed")
            continue
        if stat.S_ISLNK(metadata.st_mode):
            blockers.append("filesystem_symlink")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        try:
            if metadata.st_size > _policy.max_file_bytes:
                blockers.append("filesystem_file_too_large")
                continue
            byte_budget.reserve(metadata.st_size)
            data = _read_regular_file(
                path,
                metadata,
                max_bytes=_policy.max_file_bytes,
            )
        except _BudgetExceeded:
            blockers.append("filesystem_aggregate_too_large")
            continue
        except OSError:
            blockers.append("filesystem_read_failed")
            continue
        findings.extend(_scan_bytes(data, relative_name, _policy))
    paths.close()
    return FilesystemScanResult(
        findings=_deduplicate_findings(findings),
        blockers=tuple(sorted(set(blockers))),
    )


def scan_artifacts(root: Path, policy: SecretScanPolicy) -> ArtifactScanResult:
    missing_kinds = tuple(
        f"artifact_kind_missing:{kind.value}" for kind in ArtifactKind
    )
    try:
        root_metadata = root.lstat()
    except OSError:
        return ArtifactScanResult(
            blockers=(
                "artifact_surface_unavailable",
                *missing_kinds,
            )
        )
    if stat.S_ISLNK(root_metadata.st_mode):
        return ArtifactScanResult(blockers=("artifact_symlink", *missing_kinds))
    if not stat.S_ISDIR(root_metadata.st_mode):
        return ArtifactScanResult(
            blockers=("artifact_surface_unavailable", *missing_kinds)
        )

    discovered: dict[Path, ArtifactKind] = {}
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    evidence_hashes: list[str] = []
    entry_count = 0
    artifact_bytes = _ByteBudget(policy.max_artifact_total_bytes)
    archive_bytes = _ByteBudget(policy.max_archive_uncompressed_bytes)
    paths = _walk_paths(root)
    while True:
        try:
            path = next(paths)
        except StopIteration:
            break
        except _TraversalError:
            blockers.append("artifact_traversal_failed")
            break
        entry_count += 1
        if entry_count > policy.max_artifact_entries:
            blockers.append("artifact_entry_limit")
            break
        relative_name = path.relative_to(root).as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        try:
            metadata = path.lstat()
        except OSError:
            blockers.append("artifact_read_failed")
            continue
        if stat.S_ISLNK(metadata.st_mode):
            blockers.append("artifact_symlink")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        kind = _artifact_kind(path)
        try:
            artifact_bytes.reserve(metadata.st_size)
        except _BudgetExceeded:
            blockers.append("artifact_aggregate_too_large")
            if kind is not None:
                discovered[path] = kind
            continue
        if kind is None:
            try:
                if metadata.st_size > policy.max_file_bytes:
                    blockers.append("artifact_sidecar_too_large")
                    continue
                data = _read_regular_file(
                    path,
                    metadata,
                    max_bytes=policy.max_file_bytes,
                )
                evidence_hashes.append(hashlib.sha256(data).hexdigest())
                findings.extend(_scan_bytes(data, relative_name, policy))
            except (_BudgetExceeded, OSError):
                blockers.append("artifact_sidecar_read_failed")
            continue
        discovered[path] = kind
        if metadata.st_size > policy.max_archive_bytes:
            blockers.append(f"artifact_too_large:{kind.value}")
            continue
        try:
            data = _read_regular_file(
                path,
                metadata,
                max_bytes=policy.max_archive_bytes,
            )
            evidence_hashes.append(hashlib.sha256(data).hexdigest())
        except (_BudgetExceeded, OSError):
            blockers.append(f"artifact_read_failed:{kind.value}")
            continue
        archive_findings, archive_blockers = _scan_archive(
            _ArtifactSnapshot(name=path.name, data=data),
            kind,
            policy,
            archive_bytes,
        )
        findings.extend(archive_findings)
        blockers.extend(archive_blockers)
        try:
            _verify_regular_file_identity(path, metadata)
        except OSError:
            blockers.append(f"artifact_read_failed:{kind.value}")
    paths.close()

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
        checkout_ref, _local_refs = ref_sets
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
) -> tuple[str, dict[str, str]] | None:
    commands = {
        "heads": ("git", "ls-remote", "--heads", "origin"),
        "tags": ("git", "ls-remote", "--tags", "origin"),
        "pull_requests": ("git", "ls-remote", "origin", "refs/pull/*/head"),
        "default": ("git", "ls-remote", "--symref", "origin", "HEAD"),
        "local": (
            "git",
            "for-each-ref",
            "--format=%(refname)%09%(objectname)%09%(*objectname)",
            "refs/remotes/origin",
            "refs/tags",
            "refs/remotes/pull",
        ),
        "local_default": (
            "git",
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
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
            if scope in {"default", "local_default"}:
                blockers.append("git_default_branch_unverifiable")
            return None
        outputs[scope] = result.stdout

    try:
        remote_heads = _parse_ls_remote(outputs["heads"])
        remote_tags = _parse_ls_remote(outputs["tags"])
        remote_pulls = _parse_ls_remote(outputs["pull_requests"])
        remote_default, remote_default_oid = _parse_remote_default(outputs["default"])
        local_refs = _parse_local_refs(outputs["local"])
        local_default_lines = outputs["local_default"].decode(
            "utf-8",
            errors="strict",
        ).splitlines()
        if len(local_default_lines) != 1:
            raise ReleaseGateError("git_default_branch_unverifiable")
        local_default = local_default_lines[0]
    except ReleaseGateError as exc:
        blockers.append(
            "git_default_branch_unverifiable"
            if str(exc) == "git_default_branch_unverifiable"
            else "git_ref_inventory_malformed"
        )
        return None
    except UnicodeError:
        blockers.append("git_ref_inventory_malformed")
        return None
    if not remote_heads:
        blockers.append("git_refs_incomplete:heads")
    if not _valid_ref_namespace(remote_heads, r"refs/heads/.+", allow_peeled=False):
        blockers.append("git_ref_inventory_malformed")
        return None
    if not _valid_ref_namespace(remote_tags, r"refs/tags/.+", allow_peeled=True):
        blockers.append("git_ref_inventory_malformed")
        return None
    if not _valid_ref_namespace(
        remote_pulls,
        r"refs/pull/[1-9][0-9]*/head",
        allow_peeled=False,
    ):
        blockers.append("git_ref_inventory_malformed")
        return None
    expected: dict[str, dict[str, str]] = {
        "heads": {
            f"refs/remotes/origin/{ref.removeprefix('refs/heads/')}": oid
            for ref, oid in remote_heads.items()
        },
        "tags": dict(remote_tags),
        "pull_requests": {
            f"refs/remotes/pull/{ref.removeprefix('refs/pull/')}": oid
            for ref, oid in remote_pulls.items()
        },
    }
    local_scopes = {
        "heads": {
            ref: oid
            for ref, oid in local_refs.items()
            if ref.startswith("refs/remotes/origin/")
            and ref != "refs/remotes/origin/HEAD"
        },
        "tags": {
            ref: oid for ref, oid in local_refs.items() if ref.startswith("refs/tags/")
        },
        "pull_requests": {
            ref: oid
            for ref, oid in local_refs.items()
            if ref.startswith("refs/remotes/pull/")
        },
    }
    for scope, expected_refs in expected.items():
        actual_refs = local_scopes[scope]
        if set(actual_refs) != set(expected_refs):
            blockers.append(f"git_refs_incomplete:{scope}")
        elif actual_refs != expected_refs:
            blockers.append(f"git_ref_object_mismatch:{scope}")
    expected_default = (
        f"refs/remotes/origin/{remote_default.removeprefix('refs/heads/')}"
    )
    if (
        remote_default not in remote_heads
        or expected_default not in local_refs
        or local_default != expected_default
        or remote_default_oid != remote_heads.get(remote_default)
        or remote_default_oid != local_refs.get(expected_default)
    ):
        blockers.append("git_default_branch_unverifiable")
    if blockers:
        return None
    return expected_default, local_refs


def _parse_ls_remote(output: bytes) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split(b"\t", 1)
        if (
            len(parts) != 2
            or re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]) is None
        ):
            raise ReleaseGateError("git_ref_inventory_malformed")
        ref = parts[1].decode("utf-8", errors="strict")
        if (
            not ref.startswith("refs/")
            or any(character in ref for character in "\r\n\0")
            or ref in refs
        ):
            raise ReleaseGateError("git_ref_inventory_malformed")
        refs[ref] = parts[0].decode("ascii")
    return refs


def _parse_remote_default(output: bytes) -> tuple[str, str]:
    symbolic: list[str] = []
    head_oids: list[str] = []
    for line in output.splitlines():
        if line.startswith(b"ref: "):
            prefix, separator, suffix = line.partition(b"\t")
            if separator != b"\t" or suffix != b"HEAD":
                raise ReleaseGateError("git_default_branch_unverifiable")
            target = prefix.removeprefix(b"ref: ").decode("utf-8", errors="strict")
            if not target.startswith("refs/heads/"):
                raise ReleaseGateError("git_default_branch_unverifiable")
            symbolic.append(target)
            continue
        parts = line.split(b"\t", 1)
        if (
            len(parts) == 2
            and parts[1] == b"HEAD"
            and re.fullmatch(rb"[0-9a-f]{40}|[0-9a-f]{64}", parts[0])
        ):
            head_oids.append(parts[0].decode("ascii"))
            continue
        raise ReleaseGateError("git_default_branch_unverifiable")
    if len(symbolic) != 1 or len(head_oids) != 1:
        raise ReleaseGateError("git_default_branch_unverifiable")
    return symbolic[0], head_oids[0]


def _parse_local_refs(output: bytes) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split(b"\t")
        if len(parts) != 3:
            raise ReleaseGateError("git_ref_inventory_malformed")
        ref = parts[0].decode("utf-8", errors="strict")
        oid = parts[1].decode("ascii", errors="strict")
        peeled = parts[2].decode("ascii", errors="strict")
        if (
            not ref.startswith("refs/")
            or any(character in ref for character in "\r\n\0")
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None
            or ref in refs
        ):
            raise ReleaseGateError("git_ref_inventory_malformed")
        refs[ref] = oid
        if peeled:
            peeled_ref = f"{ref}^{{}}"
            if (
                not ref.startswith("refs/tags/")
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", peeled) is None
                or peeled_ref in refs
            ):
                raise ReleaseGateError("git_ref_inventory_malformed")
            refs[peeled_ref] = peeled
    return refs


def _valid_ref_namespace(
    refs: Mapping[str, str],
    pattern: str,
    *,
    allow_peeled: bool,
) -> bool:
    direct_refs = {ref for ref in refs if not ref.endswith("^{}")}
    for ref in refs:
        candidate = ref.removesuffix("^{}")
        if re.fullmatch(pattern, candidate) is None:
            return False
        if ref.endswith("^{}") and (
            not allow_peeled or candidate not in direct_refs
        ):
            return False
    return True


def _scan_reachable_blobs(
    runner: CommandRunner,
    clone: Path,
    policy: SecretScanPolicy,
    *,
    evidence_hashes: list[str],
    exit_codes: list[int],
    blockers: list[str],
) -> tuple[list[SecretFinding], int, int] | None:
    commit_result = _run_and_record(
        runner,
        (
            "git",
            "rev-list",
            f"--max-count={policy.max_git_commits + 1}",
            "--all",
        ),
        cwd=clone,
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_commit_inventory",
        blockers=blockers,
    )
    if commit_result is None or commit_result.exit_code != 0:
        return None
    try:
        commits = tuple(
            line.decode("ascii", errors="strict")
            for line in commit_result.stdout.splitlines()
            if line
        )
    except UnicodeError:
        blockers.append("git_commit_inventory_malformed")
        return None
    if not commits or any(
        not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", commit)
        for commit in commits
    ):
        blockers.append("git_commit_inventory_malformed")
        return None
    if len(commits) > policy.max_git_commits:
        blockers.append("git_commit_limit")
        return None

    findings: list[SecretFinding] = []
    oid_paths: dict[str, set[str]] = {}
    reachable_oids = set(commits)
    tree_entry_count = 0
    for commit in commits:
        tree_result = _run_and_record(
            runner,
            ("git", "ls-tree", "-rzt", "--full-tree", commit),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_tree_inventory",
            blockers=blockers,
        )
        if tree_result is None or tree_result.exit_code != 0:
            return None
        try:
            records = [record for record in tree_result.stdout.split(b"\0") if record]
            for record in records:
                tree_entry_count += 1
                if tree_entry_count > policy.max_git_tree_entries:
                    blockers.append("git_tree_entry_limit")
                    return None
                metadata_raw, separator, path_raw = record.partition(b"\t")
                metadata = metadata_raw.decode("ascii", errors="strict").split()
                path = path_raw.decode("utf-8", errors="strict")
                if separator != b"\t" or len(metadata) != 3:
                    raise ValueError
                mode, object_type, oid = metadata
                candidate_path = PurePosixPath(path)
                if (
                    not path
                    or candidate_path.is_absolute()
                    or "\\" in path
                    or ".." in candidate_path.parts
                    or not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid)
                ):
                    raise ValueError
                reachable_oids.add(oid)
                if _is_forbidden_path(candidate_path):
                    findings.append(_path_finding("forbidden_path", path))
                if mode == "120000" and object_type == "blob":
                    blockers.append("git_tree_symlink")
                    continue
                if mode == "160000" and object_type == "commit":
                    blockers.append("git_tree_gitlink")
                    continue
                if object_type == "tree" and mode == "040000":
                    continue
                if object_type != "blob" or mode not in {"100644", "100755"}:
                    blockers.append("git_tree_mode_unsafe")
                    continue
                oid_paths.setdefault(oid, set()).add(path)
        except (UnicodeError, ValueError):
            blockers.append("git_tree_inventory_malformed")
            return None

    blobs = tuple(sorted(oid_paths))
    if not blobs:
        return findings, len(reachable_oids), 0
    type_result = _run_and_record(
        runner,
        (
            "git",
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ),
        cwd=clone,
        input_bytes=("\n".join(blobs) + "\n").encode("ascii"),
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_object_type_inventory",
        blockers=blockers,
    )
    if type_result is None or type_result.exit_code != 0:
        return None
    blob_sizes: dict[str, int] = {}
    try:
        for line in type_result.stdout.splitlines():
            parts = line.decode("ascii", errors="strict").split()
            if (
                len(parts) != 3
                or parts[1] != "blob"
                or not parts[2].isdigit()
            ):
                raise ValueError
            blob_sizes[parts[0]] = int(parts[2])
    except (UnicodeError, ValueError):
        blockers.append("git_object_inventory_malformed")
        return None
    if set(blob_sizes) != set(blobs):
        blockers.append("git_object_inventory_incomplete")
        return None

    blob_budget = _ByteBudget(policy.max_git_blob_bytes)
    for oid in blobs:
        size = blob_sizes[oid]
        if size > policy.max_file_bytes:
            blockers.append("git_blob_too_large")
            continue
        try:
            blob_budget.reserve(size)
        except _BudgetExceeded:
            blockers.append("git_blob_aggregate_too_large")
            continue
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
        if len(blob_result.stdout) != size:
            blockers.append("git_blob_size_mismatch")
            continue
        for path in oid_paths[oid]:
            findings.extend(_scan_bytes(blob_result.stdout, path, policy))
    return findings, len(reachable_oids), len(blobs)


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
            "--exit-code=0",
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
            if result.exit_code != 0:
                blockers.append("scanner_command_failed:gitleaks:history")
            if result.exit_code != 0 and not result.stdout:
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


@dataclass(frozen=True, repr=False)
class _ArtifactSnapshot:
    name: str
    data: bytes = field(repr=False)


def _scan_archive(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
) -> tuple[list[SecretFinding], list[str]]:
    try:
        if zipfile.is_zipfile(io.BytesIO(artifact.data)):
            return _scan_zip(artifact, kind, policy, archive_budget)
        with tarfile.open(fileobj=io.BytesIO(artifact.data), mode="r:*"):
            pass
        return _scan_tar(artifact, kind, policy, archive_budget)
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        pass
    return [], [f"artifact_read_failed:{kind.value}"]


def _scan_zip(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
) -> tuple[list[SecretFinding], list[str]]:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
            entries = archive.infolist()
            if len(entries) > policy.max_archive_entries:
                return [], [f"artifact_entry_limit:{kind.value}"]
            canonical_names: set[str] = set()
            for entry in entries:
                logical_path = f"{artifact.name}!{entry.filename}"
                canonical = _canonical_archive_member(entry.filename)
                if canonical is None or not _zip_entry_is_safe_type(entry):
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    blockers.append(f"artifact_unsafe_member:{kind.value}")
                    continue
                if canonical in canonical_names:
                    blockers.append(f"artifact_duplicate_member:{kind.value}")
                canonical_names.add(canonical)
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.filename)
                if canonical is None or not _zip_entry_is_safe_type(entry) or entry.is_dir():
                    continue
                if entry.file_size > policy.max_archive_member_bytes:
                    blockers.append(f"artifact_member_too_large:{kind.value}")
                    continue
                try:
                    archive_budget.reserve(entry.file_size)
                except _BudgetExceeded:
                    blockers.append(f"artifact_uncompressed_limit:{kind.value}")
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.filename)
                if canonical is None or not _zip_entry_is_safe_type(entry):
                    continue
                if entry.is_dir():
                    continue
                logical_path = f"{artifact.name}!{entry.filename}"
                member_path = PurePosixPath(canonical)
                if _is_forbidden_path(member_path):
                    findings.append(_path_finding("forbidden_path", logical_path))
                with archive.open(entry, "r") as stream:
                    data = _read_stream_exact(stream, entry.file_size)
                findings.extend(_scan_bytes(data, logical_path, policy))
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile):
        blockers.append(f"artifact_read_failed:{kind.value}")
    return findings, blockers


def _scan_tar(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
) -> tuple[list[SecretFinding], list[str]]:
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(artifact.data), mode="r:*") as archive:
            entries = archive.getmembers()
            if len(entries) > policy.max_archive_entries:
                return [], [f"artifact_entry_limit:{kind.value}"]
            canonical_names: set[str] = set()
            for entry in entries:
                logical_path = f"{artifact.name}!{entry.name}"
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not (entry.isfile() or entry.isdir()):
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    blockers.append(f"artifact_unsafe_member:{kind.value}")
                    continue
                if canonical in canonical_names:
                    blockers.append(f"artifact_duplicate_member:{kind.value}")
                canonical_names.add(canonical)
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not (entry.isfile() or entry.isdir()) or not entry.isfile():
                    continue
                if entry.size > policy.max_archive_member_bytes:
                    blockers.append(f"artifact_member_too_large:{kind.value}")
                    continue
                try:
                    archive_budget.reserve(entry.size)
                except _BudgetExceeded:
                    blockers.append(f"artifact_uncompressed_limit:{kind.value}")
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not (entry.isfile() or entry.isdir()):
                    continue
                if not entry.isfile():
                    continue
                logical_path = f"{artifact.name}!{entry.name}"
                if _is_forbidden_path(PurePosixPath(canonical)):
                    findings.append(_path_finding("forbidden_path", logical_path))
                stream = archive.extractfile(entry)
                if stream is None:
                    blockers.append(f"artifact_read_failed:{kind.value}")
                    continue
                with stream:
                    data = _read_stream_exact(stream, entry.size)
                findings.extend(_scan_bytes(data, logical_path, policy))
    except (OSError, EOFError, tarfile.TarError):
        blockers.append(f"artifact_read_failed:{kind.value}")
    return findings, blockers


def _read_stream_exact(stream, expected_size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        chunk = stream.read(min(1024 * 1024, remaining))
        if not isinstance(chunk, bytes) or not chunk:
            raise EOFError
        chunks.append(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise EOFError
    return b"".join(chunks)


def _canonical_archive_member(name: str) -> str | None:
    normalized = unicodedata.normalize("NFC", name)
    if _unsafe_archive_member(normalized):
        return None
    canonical = PurePosixPath(normalized).as_posix().rstrip("/").casefold()
    if not canonical or canonical == ".":
        return None
    return canonical


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


def _zip_entry_is_safe_type(entry: zipfile.ZipInfo) -> bool:
    if entry.create_system != 3:
        return True
    member_type = (entry.external_attr >> 16) & 0o170000
    if entry.is_dir():
        return member_type in {0, stat.S_IFDIR}
    return member_type in {0, stat.S_IFREG}


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
        if _is_high_entropy(candidate) and not _is_declared_digest_context(
            data,
            match.start(),
            match.end(),
            candidate,
        ):
            findings.append(_content_finding("high_entropy", candidate, relative_path))
    fingerprints = set(policy.known_secret_digests)
    if fingerprints:
        candidates = _known_fingerprint_candidates(data)
        for candidate in candidates:
            if hashlib.sha256(candidate).hexdigest() in fingerprints:
                findings.append(_content_finding("known_credential", candidate, relative_path))
    return _deduplicate_findings(findings)


def _is_high_entropy(candidate: bytes) -> bool:
    if len(candidate) < 32 or re.fullmatch(
        rb"(?:md5|sha1|sha224|sha256|sha384|sha512)=[A-Za-z0-9_-]+",
        candidate,
        flags=re.IGNORECASE,
    ):
        return False
    counts = Counter(candidate)
    entropy = -sum(
        (count / len(candidate)) * math.log2(count / len(candidate))
        for count in counts.values()
    )
    if re.fullmatch(rb"[0-9a-fA-F]+", candidate):
        return len(candidate) >= 40 and len(counts) >= 8 and entropy >= 3.5
    character_classes = sum(
        bool(re.search(pattern, candidate))
        for pattern in (rb"[a-z]", rb"[A-Z]", rb"[0-9]", rb"[+/=_-]")
    )
    if character_classes < 3:
        return False
    return entropy >= 4.0


def _is_declared_digest_context(
    data: bytes,
    start: int,
    end: int,
    candidate: bytes,
) -> bool:
    if not re.fullmatch(rb"[0-9a-fA-F]+", candidate) or len(candidate) not in {
        32,
        40,
        56,
        64,
        96,
        128,
    }:
        return False
    line_start = data.rfind(b"\n", 0, start) + 1
    line_end = data.find(b"\n", end)
    if line_end == -1:
        line_end = len(data)
    line = data[line_start:line_end]
    pattern = re.compile(
        rb"(?i)(?:^|[,{])\s*[\"']?"
        rb"(?:md5|sha1|sha224|sha256|sha384|sha512|evidence_sha256|digest)"
        rb"[\"']?\s*[:=]\s*[\"']?"
        rb"(?:(?:md5|sha1|sha224|sha256|sha384|sha512)[:=])?"
        + re.escape(candidate)
        + rb"[\"']?(?=\s*(?:[,}]|$))"
    )
    return pattern.search(line) is not None


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


def _hash_file(
    path: Path,
    *,
    max_bytes: int | None = None,
    expected_metadata: os.stat_result | None = None,
) -> str:
    digest = hashlib.sha256()
    total = 0
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError("artifact_not_regular")
        if expected_metadata is not None and (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
        ) != (
            expected_metadata.st_dev,
            expected_metadata.st_ino,
            expected_metadata.st_size,
        ):
            raise OSError("artifact_changed")
        if max_bytes is not None and metadata.st_size > max_bytes:
            raise _BudgetExceeded
        while chunk := os.read(descriptor, 1024 * 1024):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise _BudgetExceeded
            digest.update(chunk)
    finally:
        os.close(descriptor)
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
