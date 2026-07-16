"""Fail-closed public release scanner orchestration."""

from __future__ import annotations

import bz2
import contextlib
import hashlib
import io
import json
import lzma
import math
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import tarfile
import tempfile
import time
import unicodedata
import zipfile
import zlib
from collections import Counter
from collections.abc import Iterable, Mapping
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
_TRUSTED_HOSTED_ATTESTATION_SURFACES = frozenset(
    surface
    for surface in REQUIRED_PUBLIC_SURFACES
    if surface != "wheel_sdist_plugin_source_archives"
)
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
    git_command_runner: CommandRunner | None = None,
    require_trusted_git_runner: bool = False,
    hosted_clients: Mapping[str, object] | None = None,
    hosted_attestations: Mapping[str, SurfaceAttestation] | None = None,
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

    trusted_attestations: dict[str, SurfaceAttestation] = {}
    try:
        for surface, attestation in dict(hosted_attestations or {}).items():
            validated = SurfaceAttestation.model_validate(attestation.model_dump())
            if (
                surface not in _TRUSTED_HOSTED_ATTESTATION_SURFACES
                or validated.surface != surface
                or validated.status is not GateStatus.PASSED
            ):
                raise ValueError
            trusted_attestations[surface] = validated
    except Exception as exc:
        raise ReleaseGateError("trusted_hosted_attestation_invalid") from exc

    runner = command_runner or SubprocessCommandRunner()
    if (
        require_trusted_git_runner
        and git_command_runner is None
        and "git_all_refs" not in trusted_attestations
    ):
        raise ReleaseGateError("release_git_runner_required")
    git_runner = git_command_runner or runner
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

    git_result: GitRepositoryScanResult | None = None
    if "git_all_refs" not in trusted_attestations:
        git_result = scan_git_repository(
            request.repo_url or f"https://github.com/{request.repo}.git",
            request.policy,
            scanner_binaries=scanner_binaries,
            command_runner=runner,
            git_command_runner=git_runner,
            require_trusted_git_runner=require_trusted_git_runner,
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
        if surface in trusted_attestations:
            continue
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
    report_started_at = min(
        (started_at, *(item.started_at for item in trusted_attestations.values()))
    )
    report_completed_at = max(
        (completed_at, *(item.completed_at for item in trusted_attestations.values()))
    )
    verified_versions = tuple(
        attestation.version
        for attestation in scanner_versions
        if attestation.version is not None
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
        "wheel_sdist_plugin_source_archives": artifact_attestation,
        **trusted_attestations,
    }
    if git_result is not None:
        attestation_map["git_all_refs"] = _surface_attestation(
            "git_all_refs",
            findings=git_result.findings,
            blockers=git_result.blockers,
            evidence_hashes=git_result.evidence_hashes,
            exit_codes=git_result.exit_codes,
            scanner_versions=(*verified_versions, HOSTED_SCANNER_VERSION),
            allowlist=request.allowlist,
            at=completed_at,
        )
    if "github_pull_request_refs" not in trusted_attestations:
        pull_result = hosted_results["github_pull_request_refs"]
        git_findings = git_result.findings if git_result is not None else ()
        git_blockers = git_result.blockers if git_result is not None else ()
        git_hashes = git_result.evidence_hashes if git_result is not None else ()
        git_exit_codes = git_result.exit_codes if git_result is not None else ()
        attestation_map["github_pull_request_refs"] = _surface_attestation(
            "github_pull_request_refs",
            findings=(*git_findings, *pull_result.findings),
            blockers=(*git_blockers, *pull_result.blockers),
            evidence_hashes=(*git_hashes, *pull_result.evidence_hashes),
            exit_codes=(*git_exit_codes, *pull_result.exit_codes),
            scanner_versions=(*verified_versions, HOSTED_SCANNER_VERSION),
            allowlist=request.allowlist,
            at=completed_at,
        )
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
        started_at=report_started_at,
        completed_at=report_completed_at,
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


@dataclass(frozen=True)
class _TraversedEntry:
    path: Path
    name: str
    parent_descriptor: int
    metadata: os.stat_result


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
    entries, manifest = _directory_manifest(descriptor)
    after_enumeration = os.fstat(descriptor)
    if _manifest_identity(after_enumeration) != _manifest_identity(before):
        raise OSError("directory_changed")

    for name, metadata in entries:
        if skip_git and name == ".git":
            continue
        child = directory / name
        traversed = _TraversedEntry(
            path=child,
            name=name,
            parent_descriptor=descriptor,
            metadata=metadata,
        )
        yield traversed
        if not stat.S_ISDIR(metadata.st_mode):
            continue
        child_descriptor = os.open(name, flags, dir_fd=descriptor)
        try:
            opened = os.fstat(child_descriptor)
            if _manifest_identity(opened) != _manifest_identity(metadata):
                raise OSError("directory_changed")
            yield from _walk_directory(
                child_descriptor,
                child,
                skip_git=skip_git,
                flags=flags,
            )
            _verify_traversed_entry(traversed)
        finally:
            os.close(child_descriptor)

    _entries_after, manifest_after = _directory_manifest(descriptor)
    if manifest_after != manifest:
        raise OSError("directory_manifest_changed")
    after_walk = os.fstat(descriptor)
    if _manifest_identity(after_walk) != _manifest_identity(before):
        raise OSError("directory_changed")


def _directory_manifest(
    descriptor: int,
) -> tuple[list[tuple[str, os.stat_result]], tuple[tuple[str, tuple[int, ...]], ...]]:
    with os.scandir(descriptor) as iterator:
        names = sorted(entry.name for entry in iterator)
    entries: list[tuple[str, os.stat_result]] = []
    for name in names:
        if not name or "/" in name or name in {".", ".."}:
            raise OSError("directory_entry_invalid")
        metadata = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        entries.append((name, metadata))
    manifest = tuple((name, _manifest_identity(metadata)) for name, metadata in entries)
    return entries, manifest


def _manifest_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _verify_directory_identity(path: Path, expected: os.stat_result) -> None:
    current = path.lstat()
    if (
        not stat.S_ISDIR(current.st_mode)
        or _manifest_identity(current) != _manifest_identity(expected)
    ):
        raise OSError("directory_changed")


def _read_regular_file(
    entry: _TraversedEntry,
    *,
    max_bytes: int,
) -> bytes:
    metadata = entry.metadata
    if metadata.st_size > max_bytes:
        raise _BudgetExceeded
    descriptor = os.open(
        entry.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=entry.parent_descriptor,
    )
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or _manifest_identity(opened) != _manifest_identity(metadata)
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
        if _manifest_identity(closed_over) != _manifest_identity(opened):
            raise OSError("file_changed")
        _verify_traversed_entry(entry)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _verify_traversed_entry(entry: _TraversedEntry) -> None:
    current = os.stat(
        entry.name,
        dir_fd=entry.parent_descriptor,
        follow_symlinks=False,
    )
    if _manifest_identity(current) != _manifest_identity(entry.metadata):
        raise OSError("directory_entry_changed")


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
            entry = next(paths)
        except StopIteration:
            break
        except _TraversalError:
            blockers.append("filesystem_traversal_failed")
            break
        entry_count += 1
        if entry_count > _policy.max_filesystem_entries:
            blockers.append("filesystem_entry_limit")
            break
        path = entry.path
        relative = path.relative_to(root)
        relative_name = relative.as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        metadata = entry.metadata
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
                entry,
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
            entry = next(paths)
        except StopIteration:
            break
        except _TraversalError:
            blockers.append("artifact_traversal_failed")
            break
        entry_count += 1
        if entry_count > policy.max_artifact_entries:
            blockers.append("artifact_entry_limit")
            break
        path = entry.path
        relative_name = path.relative_to(root).as_posix()
        if _is_forbidden_path(PurePosixPath(relative_name)):
            findings.append(_path_finding("forbidden_path", relative_name))
        metadata = entry.metadata
        if stat.S_ISLNK(metadata.st_mode):
            blockers.append("artifact_symlink")
            continue
        if not stat.S_ISREG(metadata.st_mode):
            continue
        kind = _artifact_kind(path)
        if kind is not None:
            discovered[path] = kind
        try:
            artifact_bytes.reserve(metadata.st_size)
        except _BudgetExceeded:
            blockers.append("artifact_aggregate_too_large")
            continue
        if metadata.st_size > policy.max_archive_bytes:
            blockers.append(
                f"artifact_too_large:{kind.value}"
                if kind is not None
                else "artifact_sidecar_too_large"
            )
            continue
        try:
            data = _read_regular_file(
                entry,
                max_bytes=policy.max_archive_bytes,
            )
            evidence_hashes.append(hashlib.sha256(data).hexdigest())
        except (_BudgetExceeded, OSError):
            blockers.append(
                f"artifact_read_failed:{kind.value}"
                if kind is not None
                else "artifact_sidecar_read_failed"
            )
            continue
        archive_format = _detect_archive_format(data)
        if kind is None and archive_format is None:
            if _archive_like_name(path.name):
                blockers.append("artifact_opaque_sidecar")
                findings.extend(_scan_bytes(data, relative_name, policy))
            elif metadata.st_size > policy.max_file_bytes:
                blockers.append("artifact_sidecar_too_large")
            else:
                findings.extend(_scan_bytes(data, relative_name, policy))
        elif archive_format == "opaque":
            findings.extend(_scan_bytes(data, relative_name, policy))
            blockers.append(
                "artifact_opaque_sidecar"
                if kind is None
                else f"artifact_read_failed:{kind.value}"
            )
        else:
            archive_findings, archive_blockers = _scan_archive(
                _ArtifactSnapshot(name=path.name, data=data),
                kind,
                policy,
                archive_bytes,
            )
            findings.extend(archive_findings)
            blockers.extend(archive_blockers)
        try:
            _verify_traversed_entry(entry)
        except OSError:
            blockers.append(
                f"artifact_read_failed:{kind.value}"
                if kind is not None
                else "artifact_sidecar_read_failed"
            )
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
    git_command_runner: CommandRunner | None = None,
    require_trusted_git_runner: bool = False,
) -> GitRepositoryScanResult:
    runner = command_runner or SubprocessCommandRunner()
    if require_trusted_git_runner and git_command_runner is None:
        raise ReleaseGateError("release_git_runner_required")
    git_runner = git_command_runner or runner
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
            git_runner,
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
            git_runner,
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
            git_runner,
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
        checkout_ref, local_refs = ref_sets
        checkout_result = _run_and_record(
            git_runner,
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
            git_runner,
            clone,
            policy,
            ref_oids=local_refs.values(),
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blockers=blockers,
        )
        if inventory is not None:
            object_count = inventory.object_count
            blob_count = inventory.blob_count
            findings.extend(inventory.findings)

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


_GIT_PUBLIC_OBJECT_TYPE_ORDER = ("blob", "tag", "commit", "tree")
_GIT_PUBLIC_OBJECT_TYPE_INDEX = {
    object_type: index
    for index, object_type in enumerate(_GIT_PUBLIC_OBJECT_TYPE_ORDER)
}
_GIT_TREE_MODES = {
    "40000": "tree",
    "100644": "blob",
    "100755": "blob",
    "120000": "blob",
    "160000": "commit",
}


@dataclass(frozen=True, repr=False)
class _GitPublicObject:
    object_type: str
    object_id: str
    data: bytes = field(repr=False)
    validated_high_entropy_spans: frozenset[tuple[int, int]] = field(
        default=frozenset(),
        repr=False,
    )


@dataclass(frozen=True, repr=False)
class _GitReachableInventory:
    findings: tuple[SecretFinding, ...]
    public_objects: tuple[_GitPublicObject, ...] = field(repr=False)
    object_count: int
    blob_count: int
    tag_count: int
    commit_count: int
    tree_count: int

    @property
    def public_payloads(self) -> tuple[bytes, ...]:
        return tuple(item.data for item in self.public_objects)


@dataclass(frozen=True, repr=False)
class _GitObject:
    object_type: str
    size: int
    data: bytes = field(repr=False)


@dataclass(frozen=True, repr=False)
class _GitTreeEntry:
    mode: str
    object_type: str
    object_id: str
    name: bytes = field(repr=False)
    object_id_span: tuple[int, int] = field(repr=False)


@dataclass(frozen=True, repr=False)
class _CanonicalGitObject:
    public_object: _GitPublicObject = field(repr=False)
    references: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    tree_entries: tuple[_GitTreeEntry, ...] = field(default=(), repr=False)


@dataclass(frozen=True, repr=False)
class _CanonicalGitCorpus:
    objects: tuple[_CanonicalGitObject, ...] = field(repr=False)
    tree_entry_count: int

    @property
    def public_objects(self) -> tuple[_GitPublicObject, ...]:
        return tuple(item.public_object for item in self.objects)

    @property
    def by_oid(self) -> dict[str, _CanonicalGitObject]:
        return {item.public_object.object_id: item for item in self.objects}


@dataclass(frozen=True, repr=False)
class _GitHeaderRecord:
    key: bytes = field(repr=False)
    value: bytes = field(repr=False)
    value_span: tuple[int, int]
    continued: bool = False


def _git_object_format_for_oids(oids: Iterable[str]) -> str | None:
    lengths = {len(oid) for oid in oids}
    if lengths == {40}:
        return "sha1"
    if lengths == {64}:
        return "sha256"
    return None


def _canonical_git_object_oid(
    data: bytes,
    object_type: str,
    object_format: str,
) -> str:
    if object_type not in _GIT_PUBLIC_OBJECT_TYPE_INDEX:
        raise ValueError("git_object_type_invalid")
    framed = f"{object_type} {len(data)}\0".encode("ascii") + data
    if object_format == "sha1":
        return hashlib.sha1(  # noqa: S324 - canonical Git SHA-1 identity.
            framed,
            usedforsecurity=False,
        ).hexdigest()
    if object_format == "sha256":
        return hashlib.sha256(framed).hexdigest()
    raise ValueError("git_object_format_invalid")


def _parse_git_headers(data: bytes, malformed_code: str) -> tuple[_GitHeaderRecord, ...]:
    separator = data.find(b"\n\n")
    if separator <= 0:
        raise ReleaseGateError(malformed_code)
    header = data[:separator]
    if b"\0" in header or b"\r" in header:
        raise ReleaseGateError(malformed_code)
    records: list[_GitHeaderRecord] = []
    cursor = 0
    while cursor < len(header):
        line_end = header.find(b"\n", cursor)
        if line_end < 0:
            line_end = len(header)
        line = header[cursor:line_end]
        if not line:
            raise ReleaseGateError(malformed_code)
        if line.startswith(b" "):
            if not records:
                raise ReleaseGateError(malformed_code)
            previous = records[-1]
            records[-1] = _GitHeaderRecord(
                previous.key,
                previous.value,
                previous.value_span,
                continued=True,
            )
        else:
            key, space, value = line.partition(b" ")
            if (
                space != b" "
                or not value
                or re.fullmatch(rb"[a-z][a-z0-9-]*", key) is None
            ):
                raise ReleaseGateError(malformed_code)
            value_start = cursor + len(key) + 1
            records.append(
                _GitHeaderRecord(
                    key,
                    value,
                    (value_start, line_end),
                )
            )
        cursor = line_end + 1
    return tuple(records)


def _validated_git_oid_header(
    record: _GitHeaderRecord,
    *,
    oid_length: int,
    malformed_code: str,
) -> tuple[str, tuple[int, int]]:
    if record.continued or re.fullmatch(rb"[0-9a-f]+", record.value) is None:
        raise ReleaseGateError(malformed_code)
    if len(record.value) != oid_length:
        raise ReleaseGateError(malformed_code)
    return record.value.decode("ascii"), record.value_span


def _parse_canonical_git_commit(
    data: bytes,
    object_format: str,
) -> tuple[tuple[tuple[str, str], ...], frozenset[tuple[int, int]]]:
    records = _parse_git_headers(data, "git_commit_inventory_malformed")
    oid_length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if oid_length == 0 or not records or records[0].key != b"tree":
        raise ReleaseGateError("git_commit_inventory_malformed")
    tree_oid, tree_span = _validated_git_oid_header(
        records[0],
        oid_length=oid_length,
        malformed_code="git_commit_inventory_malformed",
    )
    references: list[tuple[str, str]] = [(tree_oid, "tree")]
    spans = {tree_span}
    cursor = 1
    parent_oids: set[str] = set()
    while cursor < len(records) and records[cursor].key == b"parent":
        parent_oid, parent_span = _validated_git_oid_header(
            records[cursor],
            oid_length=oid_length,
            malformed_code="git_commit_inventory_malformed",
        )
        if parent_oid in parent_oids:
            raise ReleaseGateError("git_commit_inventory_malformed")
        parent_oids.add(parent_oid)
        references.append((parent_oid, "commit"))
        spans.add(parent_span)
        cursor += 1
    if (
        cursor + 1 >= len(records)
        or records[cursor].key != b"author"
        or records[cursor].continued
        or records[cursor + 1].key != b"committer"
        or records[cursor + 1].continued
    ):
        raise ReleaseGateError("git_commit_inventory_malformed")
    cursor += 2
    if any(
        record.key in {b"tree", b"parent", b"author", b"committer"}
        for record in records[cursor:]
    ):
        raise ReleaseGateError("git_commit_inventory_malformed")
    return tuple(references), frozenset(spans)


def _parse_canonical_git_tag(
    data: bytes,
    object_format: str,
) -> tuple[tuple[tuple[str, str], ...], frozenset[tuple[int, int]]]:
    records = _parse_git_headers(data, "git_tag_inventory_malformed")
    oid_length = 40 if object_format == "sha1" else 64 if object_format == "sha256" else 0
    if (
        oid_length == 0
        or len(records) < 4
        or tuple(record.key for record in records[:4])
        != (b"object", b"type", b"tag", b"tagger")
        or any(record.continued for record in records[:4])
        or records[1].value not in {b"blob", b"commit", b"tag", b"tree"}
        or any(
            record.key in {b"object", b"type", b"tag", b"tagger"}
            for record in records[4:]
        )
    ):
        raise ReleaseGateError("git_tag_inventory_malformed")
    target_oid, target_span = _validated_git_oid_header(
        records[0],
        oid_length=oid_length,
        malformed_code="git_tag_inventory_malformed",
    )
    return (
        ((target_oid, records[1].value.decode("ascii")),),
        frozenset({target_span}),
    )


def _parse_canonical_git_tree(
    data: bytes,
    object_format: str,
) -> tuple[_GitTreeEntry, ...]:
    oid_size = 20 if object_format == "sha1" else 32 if object_format == "sha256" else 0
    if oid_size == 0:
        raise ReleaseGateError("git_tree_inventory_malformed")
    entries: list[_GitTreeEntry] = []
    names: set[bytes] = set()
    previous_sort_key: bytes | None = None
    cursor = 0
    while cursor < len(data):
        mode_end = data.find(b" ", cursor)
        if mode_end < 0:
            raise ReleaseGateError("git_tree_inventory_malformed")
        mode_bytes = data[cursor:mode_end]
        try:
            mode = mode_bytes.decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise ReleaseGateError("git_tree_inventory_malformed") from exc
        name_end = data.find(b"\0", mode_end + 1)
        oid_start = name_end + 1
        oid_end = oid_start + oid_size
        if (
            name_end < 0
            or oid_end > len(data)
            or mode not in _GIT_TREE_MODES
        ):
            raise ReleaseGateError("git_tree_inventory_malformed")
        name = data[mode_end + 1 : name_end]
        if (
            not name
            or name in {b".", b".."}
            or b"/" in name
            or b"\\" in name
            or name in names
        ):
            raise ReleaseGateError("git_tree_inventory_malformed")
        if mode == "120000":
            raise ReleaseGateError("git_tree_symlink")
        if mode == "160000":
            raise ReleaseGateError("git_tree_gitlink")
        object_type = _GIT_TREE_MODES[mode]
        object_id = data[oid_start:oid_end].hex()
        if set(data[oid_start:oid_end]) == {0}:
            raise ReleaseGateError("git_tree_inventory_malformed")
        sort_key = name + (b"/" if object_type == "tree" else b"\0")
        if previous_sort_key is not None and sort_key <= previous_sort_key:
            raise ReleaseGateError("git_tree_inventory_malformed")
        previous_sort_key = sort_key
        names.add(name)
        entries.append(
            _GitTreeEntry(
                mode,
                object_type,
                object_id,
                name,
                (oid_start, oid_end),
            )
        )
        cursor = oid_end
    return tuple(entries)


def _validate_canonical_git_object_corpus(
    public_objects: Iterable[_GitPublicObject],
    *,
    object_format: str,
    root_oids: Iterable[str],
    max_objects: int,
    max_commits: int,
    max_tree_entries: int,
    require_canonical_order: bool = False,
) -> _CanonicalGitCorpus:
    supplied = tuple(public_objects)
    if not supplied or len(supplied) > max_objects:
        raise ReleaseGateError("git_object_inventory_incomplete")
    canonical_order = tuple(
        sorted(
            supplied,
            key=lambda item: _GIT_PUBLIC_OBJECT_TYPE_INDEX.get(
                item.object_type,
                len(_GIT_PUBLIC_OBJECT_TYPE_INDEX),
            ),
        )
    )
    if require_canonical_order and supplied != canonical_order:
        raise ReleaseGateError("git_object_inventory_malformed")
    if len({item.object_id for item in supplied}) != len(supplied):
        raise ReleaseGateError("git_object_inventory_malformed")
    oid_format = _git_object_format_for_oids(item.object_id for item in supplied)
    roots = tuple(dict.fromkeys(root_oids))
    if (
        oid_format != object_format
        or not roots
        or _git_object_format_for_oids(roots) != object_format
    ):
        raise ReleaseGateError("git_object_inventory_malformed")

    parsed: list[_CanonicalGitObject] = []
    commit_count = 0
    tree_entry_count = 0
    for supplied_object in canonical_order:
        if supplied_object.object_type not in _GIT_PUBLIC_OBJECT_TYPE_INDEX:
            raise ReleaseGateError("git_object_inventory_malformed")
        try:
            canonical_oid = _canonical_git_object_oid(
                supplied_object.data,
                supplied_object.object_type,
                object_format,
            )
        except ValueError as exc:
            raise ReleaseGateError("git_object_inventory_malformed") from exc
        if canonical_oid != supplied_object.object_id:
            raise ReleaseGateError("git_object_identity_mismatch")
        references: tuple[tuple[str, str], ...] = ()
        tree_entries: tuple[_GitTreeEntry, ...] = ()
        allowed_spans: frozenset[tuple[int, int]] = frozenset()
        if supplied_object.object_type == "commit":
            commit_count += 1
            if commit_count > max_commits:
                raise ReleaseGateError("git_commit_limit")
            references, allowed_spans = _parse_canonical_git_commit(
                supplied_object.data,
                object_format,
            )
        elif supplied_object.object_type == "tag":
            references, allowed_spans = _parse_canonical_git_tag(
                supplied_object.data,
                object_format,
            )
        elif supplied_object.object_type == "tree":
            tree_entries = _parse_canonical_git_tree(
                supplied_object.data,
                object_format,
            )
            allowed_spans = frozenset(
                entry.object_id_span for entry in tree_entries
            )
            tree_entry_count += len(tree_entries)
            if tree_entry_count > max_tree_entries:
                raise ReleaseGateError("git_tree_entry_limit")
            references = tuple(
                (entry.object_id, entry.object_type) for entry in tree_entries
            )
        parsed.append(
            _CanonicalGitObject(
                _GitPublicObject(
                    supplied_object.object_type,
                    supplied_object.object_id,
                    supplied_object.data,
                    allowed_spans,
                ),
                references,
                tree_entries,
            )
        )

    by_oid = {item.public_object.object_id: item for item in parsed}
    for item in parsed:
        for target_oid, expected_type in item.references:
            target = by_oid.get(target_oid)
            if target is None or target.public_object.object_type != expected_type:
                raise ReleaseGateError("git_object_inventory_incomplete")

    states: dict[str, int] = {}
    for root_oid in roots:
        stack: list[tuple[str, bool]] = [(root_oid, False)]
        while stack:
            oid, expanded = stack.pop()
            state = states.get(oid, 0)
            if expanded:
                states[oid] = 2
                continue
            if state == 2:
                continue
            if state == 1:
                raise ReleaseGateError("git_object_inventory_malformed")
            item = by_oid.get(oid)
            if item is None:
                raise ReleaseGateError("git_object_inventory_incomplete")
            states[oid] = 1
            stack.append((oid, True))
            for target_oid, _target_type in reversed(item.references):
                target_state = states.get(target_oid, 0)
                if target_state == 1:
                    raise ReleaseGateError("git_object_inventory_malformed")
                if target_state != 2:
                    stack.append((target_oid, False))
    visited = {oid for oid, state in states.items() if state == 2}
    if visited != set(by_oid):
        raise ReleaseGateError("git_object_inventory_incomplete")
    return _CanonicalGitCorpus(tuple(parsed), tree_entry_count)


def _scan_reachable_blobs(
    runner: CommandRunner,
    clone: Path,
    policy: SecretScanPolicy,
    *,
    ref_oids: Iterable[str],
    evidence_hashes: list[str],
    exit_codes: list[int],
    blockers: list[str],
) -> _GitReachableInventory | None:
    roots = tuple(sorted(set(ref_oids)))
    if not roots or any(
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None for oid in roots
    ):
        blockers.append("git_object_inventory_malformed")
        return None

    graph_result = _run_and_record(
        runner,
        (
            "git",
            "rev-list",
            "--objects",
            "-z",
            "--stdin",
        ),
        cwd=clone,
        input_bytes=("\n".join(roots) + "\n").encode("ascii"),
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_object_graph_inventory",
        blockers=blockers,
    )
    if graph_result is None or graph_result.exit_code != 0:
        return None

    graph_paths: dict[str, set[bytes]] = {}
    graph_oids: list[str] = []
    try:
        last_oid: str | None = None
        for raw_record in graph_result.stdout.split(b"\0"):
            if not raw_record:
                continue
            if raw_record.startswith(b"path="):
                if last_oid is None or graph_paths[last_oid]:
                    raise ValueError
                path = raw_record.removeprefix(b"path=")
                parts = path.split(b"/")
                if (
                    not path
                    or path.startswith(b"/")
                    or b"\\" in path
                    or any(part in {b"", b".", b".."} for part in parts)
                ):
                    raise ValueError
                graph_paths[last_oid].add(path)
                continue
            oid = raw_record.decode("ascii", errors="strict")
            if (
                re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) is None
                or oid in graph_paths
            ):
                raise ValueError
            graph_paths[oid] = set()
            graph_oids.append(oid)
            last_oid = oid
    except (UnicodeError, ValueError):
        blockers.append("git_object_inventory_malformed")
        return None
    if not graph_oids or not set(roots).issubset(graph_paths):
        blockers.append("git_object_inventory_incomplete")
        return None
    if len(graph_oids) > policy.max_git_commits + policy.max_git_tree_entries:
        blockers.append("git_object_limit")
        return None
    object_format = _git_object_format_for_oids(graph_oids)
    if object_format is None or _git_object_format_for_oids(roots) != object_format:
        blockers.append("git_object_inventory_malformed")
        return None

    type_result = _run_and_record(
        runner,
        (
            "git",
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
        ),
        cwd=clone,
        input_bytes=("\n".join(graph_oids) + "\n").encode("ascii"),
        evidence_hashes=evidence_hashes,
        exit_codes=exit_codes,
        blocker_scope="git_object_type_inventory",
        blockers=blockers,
    )
    if type_result is None or type_result.exit_code != 0:
        return None
    object_metadata: dict[str, tuple[str, int]] = {}
    try:
        for line in type_result.stdout.splitlines():
            parts = line.decode("ascii", errors="strict").split()
            if (
                len(parts) != 3
                or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", parts[0]) is None
                or parts[1] not in {"blob", "commit", "tag", "tree"}
                or not parts[2].isdigit()
                or parts[0] in object_metadata
            ):
                raise ValueError
            object_metadata[parts[0]] = parts[1], int(parts[2])
    except (UnicodeError, ValueError):
        blockers.append("git_object_inventory_malformed")
        return None
    if set(object_metadata) != set(graph_oids):
        blockers.append("git_object_inventory_incomplete")
        return None

    objects: dict[str, _GitObject] = {}
    blob_budget = _ByteBudget(policy.max_git_blob_bytes)
    metadata_budget = _ByteBudget(policy.max_git_blob_bytes)
    for oid in graph_oids:
        object_type, size = object_metadata[oid]
        if size > policy.max_file_bytes:
            blockers.append(
                "git_blob_too_large" if object_type == "blob" else "git_object_too_large"
            )
            return None
        try:
            (blob_budget if object_type == "blob" else metadata_budget).reserve(size)
        except _BudgetExceeded:
            blockers.append(
                "git_blob_aggregate_too_large"
                if object_type == "blob"
                else "git_object_aggregate_too_large"
            )
            return None
        object_result = _run_and_record(
            runner,
            ("git", "cat-file", object_type, oid),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope=f"git_{object_type}_read",
            blockers=blockers,
        )
        if object_result is None or object_result.exit_code != 0:
            return None
        if len(object_result.stdout) != size:
            blockers.append(f"git_{object_type}_size_mismatch")
            return None
        objects[oid] = _GitObject(object_type, size, object_result.stdout)

    supplied_objects = tuple(
        _GitPublicObject(
            objects[oid].object_type,
            oid,
            objects[oid].data,
        )
        for oid in graph_oids
    )
    try:
        corpus = _validate_canonical_git_object_corpus(
            supplied_objects,
            object_format=object_format,
            root_oids=roots,
            max_objects=policy.max_git_commits + policy.max_git_tree_entries,
            max_commits=policy.max_git_commits,
            max_tree_entries=policy.max_git_tree_entries,
        )
    except ReleaseGateError as exc:
        blockers.append(str(exc))
        return None

    corpus_by_oid = corpus.by_oid
    for item in corpus.objects:
        if item.public_object.object_type != "tree":
            continue
        oid = item.public_object.object_id
        tree_result = _run_and_record(
            runner,
            ("git", "ls-tree", "-z", oid),
            cwd=clone,
            evidence_hashes=evidence_hashes,
            exit_codes=exit_codes,
            blocker_scope="git_tree_inventory",
            blockers=blockers,
        )
        if tree_result is None or tree_result.exit_code != 0:
            return None
        try:
            if tree_result.stdout and not tree_result.stdout.endswith(b"\0"):
                raise ValueError
            records = (
                tree_result.stdout[:-1].split(b"\0")
                if tree_result.stdout
                else []
            )
            parsed_entries: list[tuple[str, str, str, bytes]] = []
            for record in records:
                raw_metadata, tab, raw_name = record.partition(b"\t")
                metadata = raw_metadata.decode("ascii", errors="strict").split()
                if (
                    tab != b"\t"
                    or len(metadata) != 3
                    or not raw_name
                    or re.fullmatch(r"[0-7]{6}", metadata[0]) is None
                    or metadata[1] not in _GIT_PUBLIC_OBJECT_TYPE_INDEX
                    or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", metadata[2])
                    is None
                ):
                    raise ValueError
                mode, declared_type, target_oid = metadata
                parsed_entries.append((mode, declared_type, target_oid, raw_name))
        except (UnicodeError, ValueError):
            blockers.append("git_tree_inventory_malformed")
            return None
        expected_entries = tuple(
            (
                entry.mode.zfill(6),
                entry.object_type,
                entry.object_id,
                entry.name,
            )
            for entry in item.tree_entries
        )
        if tuple(parsed_entries) != expected_entries:
            blockers.append("git_tree_inventory_malformed")
            return None

    findings: list[SecretFinding] = []
    oid_paths: dict[str, set[bytes]] = {oid: set() for oid in graph_oids}
    tree_paths: set[tuple[str, bytes]] = set()
    visited_trees: set[str] = set()
    visited_states: set[tuple[str, bytes]] = set()
    path_count = 0

    def display_path(path: bytes) -> str:
        try:
            return path.decode("utf-8", errors="strict")
        except UnicodeError:
            return f"git/path/{hashlib.sha256(path).hexdigest()}"

    def forbidden_git_path(path: bytes) -> bool:
        lowered_parts = tuple(part.lower() for part in path.split(b"/"))
        if any(
            part == b".mercury" or part == b".env" or part.startswith(b".env.")
            for part in lowered_parts
        ):
            return True
        filename = lowered_parts[-1] if lowered_parts else b""
        stem = filename.split(b".", 1)[0].replace(b"_", b"-")
        return stem.decode("ascii", errors="ignore") in _FORBIDDEN_FILE_STEMS

    def walk_tree(tree_oid: str, prefix: bytes) -> bool:
        nonlocal path_count
        stack: list[tuple[str, bytes, frozenset[str]]] = [
            (tree_oid, prefix, frozenset())
        ]
        while stack:
            current_oid, current_prefix, active = stack.pop()
            if current_oid in active:
                blockers.append("git_tree_inventory_malformed")
                return False
            state = (current_oid, current_prefix)
            if state in visited_states:
                continue
            visited_states.add(state)
            visited_trees.add(current_oid)
            tree = corpus_by_oid.get(current_oid)
            if tree is None or tree.public_object.object_type != "tree":
                blockers.append("git_object_inventory_incomplete")
                return False
            next_active = active | {current_oid}
            children: list[tuple[str, bytes, frozenset[str]]] = []
            for entry in tree.tree_entries:
                path_count += 1
                if path_count > policy.max_git_tree_entries:
                    blockers.append("git_tree_entry_limit")
                    return False
                path = (
                    current_prefix
                    + (b"/" if current_prefix else b"")
                    + entry.name
                )
                if len(path) > policy.max_file_bytes:
                    blockers.append("git_tree_path_too_large")
                    return False
                tree_paths.add((current_oid, path))
                oid_paths[entry.object_id].add(path)
                if forbidden_git_path(path):
                    findings.append(
                        _path_finding("forbidden_path", display_path(path))
                    )
                if entry.object_type == "tree":
                    children.append((entry.object_id, path, next_active))
            stack.extend(reversed(children))
        return True

    root_trees = {
        item.references[0][0]
        for item in corpus.objects
        if item.public_object.object_type == "commit"
    }
    for root in roots:
        target = corpus_by_oid[root]
        while target.public_object.object_type == "tag":
            target = corpus_by_oid[target.references[0][0]]
        if target.public_object.object_type == "tree":
            root_trees.add(target.public_object.object_id)
    for tree_oid in sorted(root_trees):
        if not walk_tree(tree_oid, b""):
            return None
    expected_trees = {
        item.public_object.object_id
        for item in corpus.objects
        if item.public_object.object_type == "tree"
    }
    if visited_trees != expected_trees:
        blockers.append("git_object_inventory_incomplete")
        return None

    for oid, paths in graph_paths.items():
        oid_paths[oid].update(paths)
    for item in corpus.objects:
        public_object = item.public_object
        object_path = f"git/{public_object.object_type}/{public_object.object_id}"
        if public_object.object_type == "blob":
            paths = oid_paths[public_object.object_id]
            if paths:
                for path in sorted(paths):
                    findings.extend(
                        _scan_bytes(public_object.data, display_path(path), policy)
                    )
            else:
                findings.extend(_scan_bytes(public_object.data, object_path, policy))
        else:
            findings.extend(
                _scan_bytes(
                    public_object.data,
                    object_path,
                    policy,
                    validated_high_entropy_spans=(
                        public_object.validated_high_entropy_spans
                    ),
                )
            )
        if public_object.object_type == "tree":
            for entry in item.tree_entries:
                prefix = f"{entry.mode} {entry.object_type} ".encode("ascii")
                metadata = (
                    prefix
                    + entry.object_id.encode("ascii")
                    + b"\t"
                    + entry.name
                )
                findings.extend(
                    _scan_bytes(
                        metadata,
                        object_path,
                        policy,
                        validated_high_entropy_spans=frozenset(
                            {(len(prefix), len(prefix) + len(entry.object_id))}
                        ),
                    )
                )
    for tree_oid, path in sorted(tree_paths):
        findings.extend(
            _scan_bytes(path, f"git/tree-path/{tree_oid}", policy)
        )

    counts = Counter(
        item.public_object.object_type for item in corpus.objects
    )
    return _GitReachableInventory(
        findings=_deduplicate_findings(findings),
        public_objects=corpus.public_objects,
        object_count=len(corpus.objects),
        blob_count=counts["blob"],
        tag_count=counts["tag"],
        commit_count=counts["commit"],
        tree_count=counts["tree"],
    )


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
            "--concurrency=1",
            "--no-update",
            "--no-verification",
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
        _gitleaks_scanner_finding(
            item,
            _safe_scanner_file(item.get("File"), "scanner/gitleaks"),
        )
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
            _trufflehog_scanner_finding(
                item,
                _safe_scanner_file(file_value, "scanner/trufflehog"),
            )
        )
    return findings, False


def verify_trufflehog_report(
    output: bytes,
    allowlist: SecretScanAllowlist,
    *,
    at: datetime,
) -> int:
    """Fail unless every TruffleHog finding has an exact reviewed fingerprint."""
    findings, malformed = _parse_trufflehog_findings(output)
    if malformed:
        raise ReleaseGateError("raw_evidence_handling_failed:trufflehog")
    unresolved = apply_allowlist(tuple(findings), allowlist, at=at)
    if unresolved:
        raise ReleaseGateError("scanner_findings_unresolved:trufflehog")
    return len(findings)


def _safe_scanner_file(value: object, fallback: str) -> str:
    if not isinstance(value, str) or not value:
        return fallback
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or ".." in candidate.parts or "\\" in value:
        return fallback
    return candidate.as_posix()


def _gitleaks_scanner_finding(
    item: dict[str, object],
    relative_path: str,
) -> SecretFinding:
    canonical = {
        "scanner": "gitleaks",
        "rule_id": _scanner_scalar(item.get("RuleID")),
        "commit": _scanner_scalar(item.get("Commit")),
        "start_line": _scanner_integer(item.get("StartLine")),
        "secret_sha256": _scanner_value_digest(item.get("Secret")),
    }
    return _scanner_finding(canonical, relative_path)


def _trufflehog_scanner_finding(
    item: dict[str, object],
    relative_path: str,
) -> SecretFinding:
    source_metadata = item.get("SourceMetadata")
    data = source_metadata.get("Data") if isinstance(source_metadata, dict) else None
    git_data = data.get("Git") if isinstance(data, dict) else None
    canonical = {
        "scanner": "trufflehog",
        "detector": _scanner_scalar(item.get("DetectorName")),
        "decoder": _scanner_scalar(item.get("DecoderName")),
        "verified": item.get("Verified") if isinstance(item.get("Verified"), bool) else None,
        "line": _scanner_integer(git_data.get("line")) if isinstance(git_data, dict) else None,
        "raw_sha256": _scanner_value_digest(item.get("Raw")),
        "raw_v2_sha256": _scanner_value_digest(item.get("RawV2")),
    }
    return _scanner_finding(canonical, relative_path)


def _scanner_scalar(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _scanner_integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _scanner_value_digest(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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


def _archive_like_name(name: str) -> bool:
    return name.casefold().endswith(
        (
            ".7z",
            ".bz2",
            ".gz",
            ".lz4",
            ".lzma",
            ".rar",
            ".tar",
            ".tar.bz2",
            ".tar.gz",
            ".tar.xz",
            ".tgz",
            ".txz",
            ".whl",
            ".xz",
            ".zip",
            ".zst",
        )
    )


def _expected_archive_formats(name: str) -> tuple[str, ...]:
    lowered = name.casefold()
    if lowered.endswith((".tar.gz", ".tgz")):
        return "gzip", "tar"
    if lowered.endswith((".tar.bz2", ".tbz2")):
        return "bz2", "tar"
    if lowered.endswith((".tar.xz", ".txz")):
        return "xz", "tar"
    if lowered.endswith((".whl", ".zip")):
        return ("zip",)
    if lowered.endswith(".tar"):
        return ("tar",)
    if lowered.endswith(".gz"):
        return ("gzip",)
    if lowered.endswith(".bz2"):
        return ("bz2",)
    if lowered.endswith(".xz"):
        return ("xz",)
    return ()


@dataclass(frozen=True, repr=False)
class _ArtifactSnapshot:
    name: str
    data: bytes = field(repr=False)


_ZIP_RECORD_SIGNATURES = (
    b"PK\x03\x04",
    b"PK\x01\x02",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"PK\x06\x06",
    b"PK\x06\x07",
    b"PK\x05\x05",
    b"PK\x08\x06",
)
_SUPPORTED_COMPRESSION_SIGNATURES = (
    (b"\x1f\x8b", "gzip"),
    (b"BZh", "bz2"),
    (b"\xfd7zXZ\x00", "xz"),
)


@dataclass(frozen=True)
class _OpaqueArchiveMagicFamily:
    name: str
    exact_signatures: tuple[bytes, ...]
    little_endian_ranges: tuple[tuple[int, int], ...] = ()


_SHARED_ZSTD_LZ4_SKIPPABLE_RANGE = (0x184D2A50, 0x184D2A5F)
_OPAQUE_ARCHIVE_MAGIC_FAMILIES = (
    _OpaqueArchiveMagicFamily("7z", (b"7z\xbc\xaf'\x1c",)),
    _OpaqueArchiveMagicFamily(
        "rar",
        (
            b"Rar!\x1a\x07",
            b"Rar!\x1a\x07\x00",
            b"Rar!\x1a\x07\x01\x00",
        ),
    ),
    _OpaqueArchiveMagicFamily(
        "lz4",
        (b"\x04\x22\x4d\x18", b"\x02\x21\x4c\x18"),
        (_SHARED_ZSTD_LZ4_SKIPPABLE_RANGE,),
    ),
    _OpaqueArchiveMagicFamily(
        "compress",
        (b"\x1f\x9d", b"\x1f\x1e", b"\x1f\xa0"),
    ),
    _OpaqueArchiveMagicFamily(
        "zstandard",
        (b"\x28\xb5\x2f\xfd",),
        (_SHARED_ZSTD_LZ4_SKIPPABLE_RANGE,),
    ),
)


def _opaque_archive_signatures() -> tuple[bytes, ...]:
    signatures = {
        signature
        for family in _OPAQUE_ARCHIVE_MAGIC_FAMILIES
        for signature in family.exact_signatures
    }
    signatures.update(
        value.to_bytes(4, "little")
        for family in _OPAQUE_ARCHIVE_MAGIC_FAMILIES
        for lower, upper in family.little_endian_ranges
        for value in range(lower, upper + 1)
    )
    return tuple(sorted(signatures, key=lambda value: (-len(value), value)))


_KNOWN_OPAQUE_ARCHIVE_SIGNATURES = _opaque_archive_signatures()
_TAR_MAGIC_SIGNATURES = (b"ustar\x00", b"ustar ")
_ARCHIVE_OFFSET_ZERO_SIGNATURES = (
    *((signature, "zip") for signature in _ZIP_RECORD_SIGNATURES),
    *_SUPPORTED_COMPRESSION_SIGNATURES,
    *((signature, "opaque") for signature in _KNOWN_OPAQUE_ARCHIVE_SIGNATURES),
)
_TAR_CHECKSUM_FIELD_PATTERNS = (
    re.compile(rb"[ 0-7]{6}\x00 "),
    re.compile(rb"[ 0-7]{6} \x00"),
    re.compile(rb"[ 0-7]{7}\x00"),
)
_MAX_ARCHIVE_CANDIDATE_SPAN = 1024


@dataclass(frozen=True)
class _ArchiveCandidate:
    archive_format: str
    end: int


def _classify_archive_candidate_at(data: bytes, offset: int) -> _ArchiveCandidate | None:
    if offset < 0 or offset > len(data):
        return None
    for signature, archive_format in _ARCHIVE_OFFSET_ZERO_SIGNATURES:
        if data.startswith(signature, offset):
            return _ArchiveCandidate(archive_format, offset + len(signature))
    magic_start = offset + 257
    for magic in _TAR_MAGIC_SIGNATURES:
        if data.startswith(magic, magic_start):
            return _ArchiveCandidate("tar", magic_start + len(magic))
    if offset + 512 <= len(data) and _tar_checksum_is_valid(
        data[offset : offset + 512]
    ):
        return _ArchiveCandidate("tar", offset + 512)
    if (
        offset == 0
        and len(data) >= _MAX_ARCHIVE_CANDIDATE_SPAN
        and len(data) % 512 == 0
        and not any(data)
    ):
        return _ArchiveCandidate("tar", _MAX_ARCHIVE_CANDIDATE_SPAN)
    return None


def _archive_candidate_format_at(data: bytes, offset: int) -> str | None:
    candidate = _classify_archive_candidate_at(data, offset)
    return candidate.archive_format if candidate is not None else None


def _archive_candidate_ranges(data: bytes) -> tuple[tuple[int, int], ...]:
    ranges: set[tuple[int, int]] = set()
    for signature, _archive_format in _ARCHIVE_OFFSET_ZERO_SIGNATURES:
        offset = data.find(signature)
        while offset >= 0:
            candidate = _classify_archive_candidate_at(data, offset)
            if candidate is not None:
                ranges.add((offset, candidate.end))
            offset = data.find(signature, offset + 1)
    for magic in _TAR_MAGIC_SIGNATURES:
        magic_offset = data.find(magic)
        while magic_offset >= 0:
            header_offset = magic_offset - 257
            if header_offset >= 0:
                candidate = _classify_archive_candidate_at(data, header_offset)
                if candidate is not None:
                    ranges.add((header_offset, candidate.end))
            magic_offset = data.find(magic, magic_offset + 1)
    for pattern in _TAR_CHECKSUM_FIELD_PATTERNS:
        for match in pattern.finditer(data):
            header_offset = match.start() - 148
            candidate = _classify_archive_candidate_at(data, header_offset)
            if candidate is not None:
                ranges.add((header_offset, candidate.end))
    candidate = _classify_archive_candidate_at(data, 0)
    if candidate is not None and candidate.archive_format == "tar" and not any(data):
        ranges.add((0, candidate.end))
    return tuple(sorted(ranges))


def _is_archive_candidate(data: bytes, *, search: bool = False) -> bool:
    if not isinstance(data, bytes):
        return False
    if not search:
        return _archive_candidate_format_at(data, 0) is not None
    return bool(_archive_candidate_ranges(data))


def _archive_candidate_crosses_boundary(left: bytes, right: bytes) -> bool:
    return _archive_candidate_crosses_object_boundaries((left, right))


def _archive_candidate_crosses_object_boundaries(objects: Iterable[bytes]) -> bool:
    tail = b""
    seen_object = False
    for data in objects:
        if not isinstance(data, bytes):
            return False
        if seen_object and tail and data:
            head = data[:_MAX_ARCHIVE_CANDIDATE_SPAN]
            split = len(tail)
            if any(
                start < split < end
                for start, end in _archive_candidate_ranges(tail + head)
            ):
                return True
        if len(data) >= _MAX_ARCHIVE_CANDIDATE_SPAN:
            tail = data[-(_MAX_ARCHIVE_CANDIDATE_SPAN - 1) :]
        else:
            tail = (tail + data)[-(_MAX_ARCHIVE_CANDIDATE_SPAN - 1) :]
        seen_object = True
    return False


def _detect_archive_format(data: bytes) -> str | None:
    candidate_format = _archive_candidate_format_at(data, 0)
    if candidate_format is not None:
        return candidate_format
    if _has_bounded_zip_structure(data):
        return "zip"
    if _has_prefixed_zip_local_header(data):
        return "opaque"
    return None


def _has_bounded_zip_structure(data: bytes) -> bool:
    if len(data) < 22 or b"PK\x05\x06" not in data:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            _zip_metadata_corpus(data, archive, entries)
    except (OSError, RuntimeError, EOFError, struct.error, zipfile.BadZipFile):
        return False
    return True


def _has_prefixed_zip_local_header(data: bytes) -> bool:
    offset = data.find(b"PK\x03\x04", 1)
    while offset >= 0:
        if offset + 30 <= len(data):
            name_length, extra_length = struct.unpack(
                "<HH",
                data[offset + 26 : offset + 30],
            )
            if offset + 30 + name_length + extra_length <= len(data):
                return True
        offset = data.find(b"PK\x03\x04", offset + 4)
    return False


def _tar_header_is_valid(data: bytes) -> bool:
    if len(data) < 512:
        return False
    return _tar_checksum_is_valid(data[:512])


def _tar_checksum_is_valid(header: bytes) -> bool:
    if len(header) != 512:
        return False
    raw_checksum = header[148:156].strip(b" \0")
    try:
        expected = int(raw_checksum, 8)
    except ValueError:
        return False
    unsigned = sum(header[:148]) + (8 * ord(" ")) + sum(header[156:])
    signed = (
        sum(value if value < 128 else value - 256 for value in header[:148])
        + (8 * ord(" "))
        + sum(value if value < 128 else value - 256 for value in header[156:])
    )
    return expected in {unsigned, signed}


def _parse_tar_number(field: bytes) -> int:
    if not field:
        raise ValueError("empty_tar_number")
    if field[0] in {0x80, 0xFF}:
        value = int.from_bytes(field[1:], "big")
        if field[0] == 0xFF:
            value -= 256 ** (len(field) - 1)
        return value
    try:
        return int(field.decode("ascii", errors="strict").strip(" \0") or "0", 8)
    except (UnicodeError, ValueError) as error:
        raise ValueError("invalid_tar_number") from error


@dataclass(frozen=True)
class _RawTarEntry:
    offset: int
    type_flag: bytes
    size: int


def _parse_tar_layout(data: bytes) -> tuple[tuple[_RawTarEntry, ...], bool]:
    entries: list[_RawTarEntry] = []
    offset = 0
    zero_block = b"\0" * 512
    while offset + 512 <= len(data):
        header = data[offset : offset + 512]
        if header == zero_block:
            if data[offset + 512 : offset + 1024] != zero_block:
                raise ValueError("tar_eof_incomplete")
            trailing = data[offset + 1024 :]
            unparsed = len(trailing) % 512 != 0 or any(trailing)
            return tuple(entries), unparsed
        if not _tar_checksum_is_valid(header):
            raise ValueError("tar_checksum_invalid")
        size = _parse_tar_number(header[124:136])
        if size < 0:
            raise ValueError("tar_size_invalid")
        data_end = offset + 512 + size
        padded_end = offset + 512 + (((size + 511) // 512) * 512)
        if data_end > len(data) or padded_end > len(data):
            raise ValueError("tar_entry_truncated")
        entries.append(
            _RawTarEntry(
                offset=offset,
                type_flag=header[156:157],
                size=size,
            )
        )
        offset = padded_end
    raise ValueError("tar_eof_missing")


def _artifact_scope(kind: ArtifactKind | None) -> str:
    return kind.value if kind is not None else "sidecar"


def _scan_archive(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind | None,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
) -> tuple[list[SecretFinding], list[str]]:
    return _scan_archive_at_depth(
        artifact,
        kind,
        policy,
        archive_budget,
        depth=0,
        expected_formats=_expected_archive_formats(artifact.name),
    )


def _scan_archive_at_depth(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind | None,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
    *,
    depth: int,
    expected_formats: tuple[str, ...] | None = None,
) -> tuple[list[SecretFinding], list[str]]:
    scope = _artifact_scope(kind)
    if depth > 8:
        return [], [f"artifact_archive_depth:{scope}"]
    if expected_formats is None:
        expected_formats = _expected_archive_formats(artifact.name)
    archive_format = _detect_archive_format(artifact.data)
    if expected_formats and archive_format != expected_formats[0]:
        return list(
            _scan_bytes(artifact.data, f"{artifact.name}!metadata", policy)
        ), [f"artifact_read_failed:{scope}"]
    if archive_format == "opaque":
        return list(
            _scan_bytes(artifact.data, f"{artifact.name}!opaque", policy)
        ), [f"artifact_opaque_member:{scope}"]
    try:
        if archive_format == "zip":
            return _scan_zip(
                artifact,
                kind,
                policy,
                archive_budget,
                depth=depth,
            )
        if archive_format == "tar":
            return _scan_tar(
                artifact,
                kind,
                policy,
                archive_budget,
                depth=depth,
            )
        if archive_format in {"gzip", "bz2", "xz"}:
            remaining = archive_budget.limit - archive_budget.used
            if remaining <= 0:
                return [], [f"artifact_uncompressed_limit:{scope}"]
            expanded, trailing, metadata = _decompress_bounded(
                artifact.data,
                archive_format,
                remaining,
            )
            findings = list(
                _scan_bytes(
                    metadata,
                    f"{artifact.name}!metadata",
                    policy,
                )
            )
            findings.extend(
                _scan_bytes(
                    trailing,
                    f"{artifact.name}!trailing",
                    policy,
                )
            )
            blockers = [f"artifact_unparsed_data:{scope}"] if trailing else []
            inner_format = _detect_archive_format(expanded)
            remaining_expected = expected_formats[1:] if expected_formats else ()
            if remaining_expected and inner_format != remaining_expected[0]:
                blockers.append(f"artifact_read_failed:{scope}")
                findings.extend(
                    _scan_bytes(expanded, f"{artifact.name}!compressed", policy)
                )
                return findings, blockers
            if inner_format is not None:
                if inner_format == "opaque":
                    blockers.append(f"artifact_opaque_member:{scope}")
                    findings.extend(
                        _scan_bytes(expanded, f"{artifact.name}!compressed", policy)
                    )
                    return findings, blockers
                nested_findings, nested_blockers = _scan_archive_at_depth(
                    _ArtifactSnapshot(name=artifact.name, data=expanded),
                    kind,
                    policy,
                    archive_budget,
                    depth=depth + 1,
                    expected_formats=remaining_expected,
                )
                findings.extend(nested_findings)
                blockers.extend(nested_blockers)
                return findings, blockers
            if len(expanded) > policy.max_archive_member_bytes:
                blockers.append(f"artifact_member_too_large:{scope}")
                return findings, blockers
            archive_budget.reserve(len(expanded))
            findings.extend(
                _scan_bytes(expanded, f"{artifact.name}!compressed", policy)
            )
            return findings, blockers
    except _BudgetExceeded:
        return [], [f"artifact_uncompressed_limit:{scope}"]
    except (
        EOFError,
        OSError,
        RuntimeError,
        tarfile.TarError,
        zipfile.BadZipFile,
        lzma.LZMAError,
    ):
        return [], [f"artifact_read_failed:{scope}"]
    return [], [f"artifact_read_failed:{scope}"]


def _decompress_bounded(
    data: bytes,
    compression: str,
    limit: int,
) -> tuple[bytes, bytes, bytes]:
    magic = {
        "gzip": b"\x1f\x8b",
        "bz2": b"BZh",
        "xz": b"\xfd7zXZ\x00",
    }[compression]
    pending = data
    output: list[bytes] = []
    metadata: list[bytes] = []
    total = 0
    while pending.startswith(magic):
        remaining = limit - total
        if remaining < 0:
            raise _BudgetExceeded
        if compression == "gzip":
            header_end = _gzip_header_end(pending)
            decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
            chunk = decompressor.decompress(pending, remaining + 1)
            eof = decompressor.eof
            unused = decompressor.unused_data
        elif compression == "bz2":
            bz2_decompressor = bz2.BZ2Decompressor()
            chunk = bz2_decompressor.decompress(pending, max_length=remaining + 1)
            eof = bz2_decompressor.eof
            unused = bz2_decompressor.unused_data
        else:
            xz_decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
            chunk = xz_decompressor.decompress(pending, max_length=remaining + 1)
            eof = xz_decompressor.eof
            unused = xz_decompressor.unused_data
        total += len(chunk)
        if total > limit:
            raise _BudgetExceeded
        if not eof:
            if total >= limit:
                raise _BudgetExceeded
            raise EOFError
        if compression == "gzip":
            member_end = len(pending) - len(unused)
            if member_end < header_end + 8:
                raise EOFError
            metadata.extend((pending[:header_end], pending[member_end - 8 : member_end]))
        output.append(chunk)
        pending = unused
        if compression in {"gzip", "xz"} and pending.startswith(b"\0"):
            padding_length = len(pending) - len(pending.lstrip(b"\0"))
            if compression == "gzip" or padding_length % 4 == 0:
                metadata.append(pending[:padding_length])
                pending = pending[padding_length:]
    return b"".join(output), pending, b"".join(metadata)


def _gzip_header_end(data: bytes) -> int:
    if (
        len(data) < 10
        or data[:3] != b"\x1f\x8b\x08"
        or data[3] & 0xE0
    ):
        raise EOFError
    flags = data[3]
    cursor = 10
    if flags & 0x04:
        if cursor + 2 > len(data):
            raise EOFError
        extra_length = struct.unpack("<H", data[cursor : cursor + 2])[0]
        cursor += 2 + extra_length
        if cursor > len(data):
            raise EOFError
    for flag in (0x08, 0x10):
        if not flags & flag:
            continue
        terminator = data.find(b"\0", cursor)
        if terminator < 0:
            raise EOFError
        cursor = terminator + 1
    if flags & 0x02:
        cursor += 2
    if cursor > len(data):
        raise EOFError
    return cursor


def _scan_zip(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind | None,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
    *,
    depth: int = 0,
) -> tuple[list[SecretFinding], list[str]]:
    scope = _artifact_scope(kind)
    findings: list[SecretFinding] = []
    blockers: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(artifact.data)) as archive:
            entries = archive.infolist()
            if len(entries) > policy.max_archive_entries:
                return [], [f"artifact_entry_limit:{scope}"]
            metadata, unparsed = _zip_metadata_corpus(artifact.data, archive, entries)
            findings.extend(
                _scan_bytes(metadata, f"{artifact.name}!metadata", policy)
            )
            if unparsed:
                blockers.append(f"artifact_unparsed_data:{scope}")
            canonical_names: set[str] = set()
            for entry in entries:
                logical_path = f"{artifact.name}!{entry.filename}"
                canonical = _canonical_archive_member(entry.filename)
                if canonical is None or not _zip_entry_is_safe_type(entry):
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    blockers.append(f"artifact_unsafe_member:{scope}")
                    continue
                if canonical in canonical_names:
                    blockers.append(f"artifact_duplicate_member:{scope}")
                canonical_names.add(canonical)
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.filename)
                if canonical is None or not _zip_entry_is_safe_type(entry) or entry.is_dir():
                    continue
                if entry.file_size > policy.max_archive_member_bytes:
                    blockers.append(f"artifact_member_too_large:{scope}")
                    continue
                try:
                    archive_budget.reserve(entry.file_size)
                except _BudgetExceeded:
                    blockers.append(f"artifact_uncompressed_limit:{scope}")
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
                nested_format = _detect_archive_format(data)
                if nested_format is not None:
                    nested_findings, nested_blockers = _scan_archive_at_depth(
                        _ArtifactSnapshot(name=logical_path, data=data),
                        kind,
                        policy,
                        archive_budget,
                        depth=depth + 1,
                    )
                    findings.extend(nested_findings)
                    blockers.extend(nested_blockers)
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile):
        blockers.append(f"artifact_read_failed:{scope}")
    return findings, blockers


def _zip_metadata_corpus(
    data: bytes,
    archive: zipfile.ZipFile,
    entries: list[zipfile.ZipInfo],
) -> tuple[bytes, bool]:
    start_dir = archive.start_dir
    if not isinstance(start_dir, int) or start_dir < 0 or start_dir > len(data):
        raise zipfile.BadZipFile
    layouts: list[tuple[int, int, int, bool]] = []
    for entry in sorted(entries, key=lambda item: item.header_offset):
        offset = entry.header_offset
        if (
            offset < 0
            or offset + 30 > len(data)
            or data[offset : offset + 4] != b"PK\x03\x04"
        ):
            raise zipfile.BadZipFile
        name_length, extra_length = struct.unpack("<HH", data[offset + 26 : offset + 30])
        payload_start = offset + 30 + name_length + extra_length
        payload_end = payload_start + entry.compress_size
        if payload_start > payload_end or payload_end > start_dir:
            raise zipfile.BadZipFile
        layouts.append((offset, payload_start, payload_end, bool(entry.flag_bits & 0x08)))
    eocd_offset, eocd_end = _zip_eocd_bounds(data)
    prefix = data[: layouts[0][0]] if layouts else data[:start_dir]
    unparsed = bool(
        (not layouts and (start_dir != 0 or eocd_offset != 0))
        or _zip_prefix_contains_archive(prefix)
    )
    for index, (_offset, _payload_start, payload_end, has_descriptor) in enumerate(layouts):
        next_offset = layouts[index + 1][0] if index + 1 < len(layouts) else start_dir
        if payload_end > next_offset:
            raise zipfile.BadZipFile
        gap = data[payload_end:next_offset]
        if not gap:
            continue
        descriptor_length = len(gap)
        descriptor_valid = has_descriptor and descriptor_length in {12, 16, 20, 24}
        if descriptor_valid and descriptor_length in {16, 24}:
            descriptor_valid = gap.startswith(b"PK\x07\x08")
        if not descriptor_valid:
            unparsed = True
    if eocd_end < len(data):
        unparsed = True
    intervals = sorted((start, end) for _offset, start, end, _flag in layouts)
    metadata_chunks: list[bytes] = []
    cursor = 0
    for start, end in intervals:
        if start < cursor:
            raise zipfile.BadZipFile
        metadata_chunks.append(data[cursor:start])
        cursor = end
    metadata_chunks.append(data[cursor:])
    return b"".join(metadata_chunks), unparsed


def _zip_prefix_contains_archive(prefix: bytes) -> bool:
    return _is_archive_candidate(prefix, search=True)


def _zip_eocd_bounds(data: bytes) -> tuple[int, int]:
    search_start = max(0, len(data) - (65535 + 22 + 65535))
    offset = data.rfind(b"PK\x05\x06", search_start)
    while offset >= search_start:
        if offset + 22 <= len(data):
            comment_length = struct.unpack("<H", data[offset + 20 : offset + 22])[0]
            end = offset + 22 + comment_length
            if end <= len(data):
                return offset, end
        offset = data.rfind(b"PK\x05\x06", search_start, offset)
    raise zipfile.BadZipFile


def _scan_tar(
    artifact: _ArtifactSnapshot,
    kind: ArtifactKind | None,
    policy: SecretScanPolicy,
    archive_budget: _ByteBudget,
    *,
    depth: int = 0,
) -> tuple[list[SecretFinding], list[str]]:
    scope = _artifact_scope(kind)
    findings: list[SecretFinding] = list(
        _scan_bytes(artifact.data, f"{artifact.name}!metadata", policy)
    )
    blockers: list[str] = []
    try:
        raw_entries, unparsed = _parse_tar_layout(artifact.data)
    except ValueError:
        return findings, [f"artifact_read_failed:{scope}"]
    if len(raw_entries) > policy.max_archive_entries:
        return findings, [f"artifact_entry_limit:{scope}"]
    if unparsed:
        blockers.append(f"artifact_unparsed_data:{scope}")
    if any(
        entry.type_flag not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}
        for entry in raw_entries
    ):
        blockers.append(f"artifact_unsafe_member:{scope}")
        return findings, blockers
    try:
        with tarfile.open(fileobj=io.BytesIO(artifact.data), mode="r:") as archive:
            entries = archive.getmembers()
            if len(entries) != len(raw_entries):
                return findings, [f"artifact_read_failed:{scope}"]
            if any(
                entry.offset != raw.offset
                or entry.offset_data != raw.offset + 512
                or entry.type != raw.type_flag
                or entry.size != raw.size
                for entry, raw in zip(entries, raw_entries, strict=True)
            ):
                return findings, [f"artifact_read_failed:{scope}"]
            canonical_names: set[str] = set()
            for entry in entries:
                logical_path = f"{artifact.name}!{entry.name}"
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not _tar_entry_is_safe_type(entry):
                    findings.append(_path_finding("archive_unsafe", logical_path))
                    blockers.append(f"artifact_unsafe_member:{scope}")
                    continue
                if canonical in canonical_names:
                    blockers.append(f"artifact_duplicate_member:{scope}")
                canonical_names.add(canonical)
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not _tar_entry_is_safe_type(entry) or entry.isdir():
                    continue
                if entry.size > policy.max_archive_member_bytes:
                    blockers.append(f"artifact_member_too_large:{scope}")
                    continue
                try:
                    archive_budget.reserve(entry.size)
                except _BudgetExceeded:
                    blockers.append(f"artifact_uncompressed_limit:{scope}")
            if blockers:
                return findings, blockers
            for entry in entries:
                canonical = _canonical_archive_member(entry.name)
                if canonical is None or not _tar_entry_is_safe_type(entry):
                    continue
                if not entry.isfile():
                    continue
                logical_path = f"{artifact.name}!{entry.name}"
                if _is_forbidden_path(PurePosixPath(canonical)):
                    findings.append(_path_finding("forbidden_path", logical_path))
                stream = archive.extractfile(entry)
                if stream is None:
                    blockers.append(f"artifact_read_failed:{scope}")
                    continue
                with stream:
                    data = _read_stream_exact(stream, entry.size)
                findings.extend(_scan_bytes(data, logical_path, policy))
                nested_format = _detect_archive_format(data)
                if nested_format is not None:
                    nested_findings, nested_blockers = _scan_archive_at_depth(
                        _ArtifactSnapshot(name=logical_path, data=data),
                        kind,
                        policy,
                        archive_budget,
                        depth=depth + 1,
                    )
                    findings.extend(nested_findings)
                    blockers.extend(nested_blockers)
    except (OSError, EOFError, tarfile.TarError):
        blockers.append(f"artifact_read_failed:{scope}")
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
        return False
    member_type = (entry.external_attr >> 16) & 0o170000
    if entry.is_dir():
        return member_type == stat.S_IFDIR
    return member_type == stat.S_IFREG


def _tar_entry_is_safe_type(entry: tarfile.TarInfo) -> bool:
    if entry.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE}:
        return False
    if entry.type == tarfile.DIRTYPE:
        return entry.isdir()
    sparse = getattr(entry, "sparse", None)
    return entry.isfile() and not sparse and not any(
        key.startswith("GNU.sparse.") for key in entry.pax_headers
    )


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
    *,
    validated_high_entropy_spans: frozenset[tuple[int, int]] = frozenset(),
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
        if (
            _is_high_entropy(candidate)
            and (match.start(), match.end()) not in validated_high_entropy_spans
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
