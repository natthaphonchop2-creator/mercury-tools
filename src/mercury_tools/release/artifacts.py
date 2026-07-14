"""Deterministic, candidate-bound Mercury release artifact construction."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from mercury_tools.release.models import SecretScanPolicy, SecretScanRequest
from mercury_tools.release.scanner import (
    ReleaseGateError,
    SubprocessCommandRunner,
    load_public_surface_manifest,
    load_secret_scan_allowlist,
    scan_public_release,
)

MANIFEST_FILE_NAME = "SHA256SUMS.json"
_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
_MAX_COMMAND_OUTPUT = 256 * 1024 * 1024
_BUILD_TIMEOUT_SECONDS = 600.0
_GIT_TIMEOUT_SECONDS = 60.0
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".mercury",
        ".superpowers",
        "__pycache__",
        "build",
        "dist",
        "release-evidence",
    }
)
_EXCLUDED_STATE_FILES = frozenset(
    {
        "audit-ledger.jsonl",
        "credential-store.json",
        "credentials-store.json",
        "downloaded-provider-payload.json",
        "provider-payload.json",
        "provider-response.json",
        "raw-provider-payload.json",
        "raw-provider-response.json",
        "validation-raw-traffic.json",
        "validation-traffic.json",
    }
)


@dataclass(frozen=True)
class ReleaseScannerAttestation:
    """A narrow adapter for an explicit successful Task 13 gate result."""

    passed: bool


@dataclass(frozen=True)
class ReleaseArtifact:
    file_name: str
    kind: str
    size: int
    sha256: str
    version: str
    commit_sha: str
    build_epoch: int

    def as_dict(self) -> dict[str, object]:
        return {
            "build_epoch": self.build_epoch,
            "commit_sha": self.commit_sha,
            "file_name": self.file_name,
            "kind": self.kind,
            "sha256": self.sha256,
            "size": self.size,
            "version": self.version,
        }


@dataclass(frozen=True)
class ReleaseArtifactManifest:
    version: str
    commit_sha: str
    build_epoch: int
    artifacts: tuple[ReleaseArtifact, ...]

    @property
    def source_date_epoch(self) -> int:
        return self.build_epoch

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "commit_sha": self.commit_sha,
            "schema_version": 1,
            "source_date_epoch": self.build_epoch,
            "version": self.version,
        }


@dataclass(frozen=True)
class CandidateEntry:
    name: str
    mode: int
    data: bytes


@dataclass(frozen=True)
class ReleaseCandidate:
    root: Path
    version: str
    commit_sha: str
    build_epoch: int
    entries: tuple[CandidateEntry, ...]


ScannerGate = Callable[[Path, Path], object]


def build_release_artifacts(
    root: Path,
    *,
    version: str,
    output: Path,
    scanner_gate: ScannerGate | None = None,
) -> ReleaseArtifactManifest:
    """Build exactly four reproducible artifacts from the clean candidate commit."""

    candidate = load_release_candidate(root, version=version, require_clean=True)
    output = output.expanduser()
    _require_output_absent(output)
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc

    try:
        with tempfile.TemporaryDirectory(
            prefix=".mercury-release-",
            dir=output.parent,
        ) as temporary:
            temporary_root = Path(temporary)
            source_root = temporary_root / "source"
            staged_artifacts = temporary_root / "artifacts"
            source_root.mkdir()
            staged_artifacts.mkdir()
            _write_candidate_tree(candidate.entries, source_root)
            _build_distributions(candidate, source_root, staged_artifacts)
            _build_plugin_archive(candidate, staged_artifacts)
            _build_source_archive(candidate, staged_artifacts)
            manifest = _write_manifest(candidate, staged_artifacts)
            require_task13_scanner_gate(
                candidate.root,
                staged_artifacts,
                scanner_gate=scanner_gate,
            )
            os.replace(staged_artifacts, output)
            return manifest
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        raise ReleaseGateError("release_artifact_write_failed") from exc


def load_release_candidate(
    root: Path,
    *,
    version: str,
    require_clean: bool,
) -> ReleaseCandidate:
    """Read the reviewed Git commit without importing worktree-only content."""

    root = _resolve_root(root)
    _require_version_matches(root, version)
    if require_clean:
        require_clean_worktree(root)
    commit_sha = git_head(root)
    build_epoch = git_commit_epoch(root, commit_sha)
    entries = _candidate_entries(root, commit_sha)
    if not entries:
        raise ReleaseGateError("release_candidate_empty")
    return ReleaseCandidate(
        root=root,
        version=version,
        commit_sha=commit_sha,
        build_epoch=build_epoch,
        entries=entries,
    )


def require_clean_worktree(root: Path) -> None:
    result = _run_command(
        ("git", "status", "--porcelain=v1", "--untracked-files=all"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise ReleaseGateError("release_repository_invalid")
    if result.stdout.strip():
        raise ReleaseGateError("release_worktree_not_clean")


def git_head(root: Path) -> str:
    result = _run_command(
        ("git", "rev-parse", "--verify", "HEAD"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    value = result.stdout.decode("ascii", errors="ignore").strip()
    if result.exit_code != 0 or not _COMMIT_PATTERN.fullmatch(value):
        raise ReleaseGateError("release_candidate_invalid")
    return value


def git_commit_epoch(root: Path, commit_sha: str) -> int:
    result = _run_command(
        ("git", "show", "-s", "--format=%ct", commit_sha),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    value = result.stdout.decode("ascii", errors="ignore").strip()
    if result.exit_code != 0 or not value.isdecimal():
        raise ReleaseGateError("release_epoch_invalid")
    epoch = int(value)
    if epoch < 315532800:
        raise ReleaseGateError("release_epoch_invalid")
    return epoch


def load_release_artifact_manifest(path: Path) -> ReleaseArtifactManifest:
    try:
        payload = _strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise ReleaseGateError("release_manifest_invalid") from exc
    if not isinstance(payload, dict):
        raise ReleaseGateError("release_manifest_invalid")
    if set(payload) != {
        "artifacts",
        "commit_sha",
        "schema_version",
        "source_date_epoch",
        "version",
    }:
        raise ReleaseGateError("release_manifest_invalid")
    version = payload["version"]
    commit_sha = payload["commit_sha"]
    epoch = payload["source_date_epoch"]
    raw_artifacts = payload["artifacts"]
    if (
        payload["schema_version"] != 1
        or not isinstance(version, str)
        or not _VERSION_PATTERN.fullmatch(version)
        or not isinstance(commit_sha, str)
        or not _COMMIT_PATTERN.fullmatch(commit_sha)
        or type(epoch) is not int
        or epoch < 315532800
        or not isinstance(raw_artifacts, list)
    ):
        raise ReleaseGateError("release_manifest_invalid")

    artifacts: list[ReleaseArtifact] = []
    for item in raw_artifacts:
        if not isinstance(item, dict) or set(item) != {
            "build_epoch",
            "commit_sha",
            "file_name",
            "kind",
            "sha256",
            "size",
            "version",
        }:
            raise ReleaseGateError("release_manifest_invalid")
        artifact = ReleaseArtifact(
            file_name=item["file_name"],
            kind=item["kind"],
            size=item["size"],
            sha256=item["sha256"],
            version=item["version"],
            commit_sha=item["commit_sha"],
            build_epoch=item["build_epoch"],
        )
        if not _valid_artifact(artifact):
            raise ReleaseGateError("release_manifest_invalid")
        artifacts.append(artifact)
    if len(artifacts) != 4:
        raise ReleaseGateError("release_manifest_invalid")
    if [artifact.file_name for artifact in artifacts] != sorted(
        artifact.file_name for artifact in artifacts
    ):
        raise ReleaseGateError("release_manifest_invalid")
    return ReleaseArtifactManifest(
        version=version,
        commit_sha=commit_sha,
        build_epoch=epoch,
        artifacts=tuple(artifacts),
    )


def require_task13_scanner_gate(
    root: Path,
    target: Path,
    *,
    scanner_gate: ScannerGate | None,
) -> None:
    try:
        result = (
            scanner_gate(root, target)
            if scanner_gate is not None
            else _run_task13_artifact_gate(root, target)
        )
        passed = result.passed
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    if passed is not True:
        raise ReleaseGateError("release_scanner_gate_blocked")


def source_tree_digest(entries: Iterable[CandidateEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.name):
        digest.update(f"{entry.mode:o} {entry.name}\0".encode())
        digest.update(hashlib.sha256(entry.data).digest())
    return digest.hexdigest()


def is_excluded_public_path(name: str) -> bool:
    path = PurePosixPath(name)
    lowered_parts = tuple(part.casefold() for part in path.parts)
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in lowered_parts):
        return True
    if any(part == ".env" or part.startswith(".env.") for part in lowered_parts):
        return True
    return bool(lowered_parts and lowered_parts[-1] in _EXCLUDED_STATE_FILES)


def _resolve_root(root: Path) -> Path:
    try:
        resolved = root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ReleaseGateError("release_repository_invalid") from exc
    if not resolved.is_dir():
        raise ReleaseGateError("release_repository_invalid")
    return resolved


def _require_version_matches(root: Path, version: str) -> None:
    if not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseGateError("release_version_invalid")
    try:
        payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        project = payload["project"]
        package_version = project["version"] if isinstance(project, dict) else None
    except (OSError, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReleaseGateError("release_version_unavailable") from exc
    if package_version != version:
        raise ReleaseGateError("release_version_mismatch")


def _candidate_entries(root: Path, commit_sha: str) -> tuple[CandidateEntry, ...]:
    result = _run_command(
        ("git", "archive", "--format=tar", commit_sha),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise ReleaseGateError("release_git_archive_failed")
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:") as archive:
            entries: list[CandidateEntry] = []
            seen: set[str] = set()
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile() or not _safe_archive_name(member.name):
                    raise ReleaseGateError("release_archive_member_invalid")
                canonical_name = member.name.casefold()
                if canonical_name in seen:
                    raise ReleaseGateError("release_archive_member_invalid")
                seen.add(canonical_name)
                if is_excluded_public_path(member.name):
                    continue
                source = archive.extractfile(member)
                if source is None:
                    raise ReleaseGateError("release_archive_member_invalid")
                entries.append(
                    CandidateEntry(
                        name=member.name,
                        mode=0o755 if member.mode & 0o111 else 0o644,
                        data=source.read(),
                    )
                )
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseGateError("release_git_archive_failed") from exc
    ordered = tuple(sorted(entries, key=lambda item: item.name))
    if len({item.name for item in ordered}) != len(ordered):
        raise ReleaseGateError("release_archive_member_invalid")
    return ordered


def _write_candidate_tree(entries: Iterable[CandidateEntry], destination: Path) -> None:
    for entry in sorted(entries, key=lambda item: item.name):
        if not _safe_archive_name(entry.name):
            raise ReleaseGateError("release_archive_member_invalid")
        path = destination / entry.name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entry.data)
        os.chmod(path, entry.mode)


def _build_distributions(
    candidate: ReleaseCandidate,
    source_root: Path,
    staged_artifacts: Path,
) -> None:
    raw_output = staged_artifacts / "raw"
    raw_output.mkdir()
    runner = SubprocessCommandRunner(
        environment={"PYTHONHASHSEED": "0", "SOURCE_DATE_EPOCH": str(candidate.build_epoch)},
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=_BUILD_TIMEOUT_SECONDS,
    )
    result = runner.run(
        ("uv", "build", "--wheel", "--sdist", "--out-dir", str(raw_output)),
        cwd=source_root,
    )
    if result.exit_code != 0:
        raise ReleaseGateError("release_build_failed")
    wheels = sorted(raw_output.glob("*.whl"))
    sdists = sorted(raw_output.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ReleaseGateError("release_build_output_invalid")
    wheel = wheels[0]
    sdist = sdists[0]
    if not wheel.name.startswith(f"mercury_tools-{candidate.version}-"):
        raise ReleaseGateError("release_build_output_invalid")
    if sdist.name != f"mercury_tools-{candidate.version}.tar.gz":
        raise ReleaseGateError("release_build_output_invalid")
    _normalize_zip_archive(wheel, staged_artifacts / wheel.name, candidate.build_epoch)
    _normalize_tar_gz_archive(sdist, staged_artifacts / sdist.name, candidate.build_epoch)
    shutil.rmtree(raw_output)


def _build_plugin_archive(candidate: ReleaseCandidate, staged_artifacts: Path) -> None:
    prefix = "plugins/mercury-finance/"
    entries = tuple(
        CandidateEntry(
            name=f"mercury-finance/{entry.name.removeprefix(prefix)}",
            mode=entry.mode,
            data=entry.data,
        )
        for entry in candidate.entries
        if entry.name.startswith(prefix)
    )
    if not entries:
        raise ReleaseGateError("release_plugin_source_missing")
    _write_zip_archive(
        entries,
        staged_artifacts / f"mercury-finance-plugin-{candidate.version}.zip",
        candidate.build_epoch,
    )


def _build_source_archive(candidate: ReleaseCandidate, staged_artifacts: Path) -> None:
    prefix = f"mercury-tools-{candidate.version}"
    entries = tuple(
        CandidateEntry(
            name=f"{prefix}/{entry.name}",
            mode=entry.mode,
            data=entry.data,
        )
        for entry in candidate.entries
    )
    _write_tar_gz_archive(
        entries,
        staged_artifacts / f"mercury-tools-{candidate.version}-source.tar.gz",
        candidate.build_epoch,
    )


def _normalize_zip_archive(source: Path, destination: Path, epoch: int) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            entries: list[CandidateEntry] = []
            for member in archive.infolist():
                if member.is_dir():
                    continue
                if not _safe_archive_name(member.filename):
                    raise ReleaseGateError("release_archive_member_invalid")
                entries.append(
                    CandidateEntry(
                        name=member.filename,
                        mode=0o755 if (member.external_attr >> 16) & 0o111 else 0o644,
                        data=archive.read(member),
                    )
                )
    except ReleaseGateError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise ReleaseGateError("release_build_output_invalid") from exc
    _write_zip_archive(entries, destination, epoch)


def _normalize_tar_gz_archive(source: Path, destination: Path, epoch: int) -> None:
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            entries: list[CandidateEntry] = []
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile() or not _safe_archive_name(member.name):
                    raise ReleaseGateError("release_archive_member_invalid")
                stream = archive.extractfile(member)
                if stream is None:
                    raise ReleaseGateError("release_build_output_invalid")
                entries.append(
                    CandidateEntry(
                        name=member.name,
                        mode=0o755 if member.mode & 0o111 else 0o644,
                        data=stream.read(),
                    )
                )
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseGateError("release_build_output_invalid") from exc
    _write_tar_gz_archive(entries, destination, epoch)


def _write_zip_archive(entries: Iterable[CandidateEntry], destination: Path, epoch: int) -> None:
    ordered = _ordered_entries(entries)
    with zipfile.ZipFile(
        destination,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for entry in ordered:
            info = zipfile.ZipInfo(entry.name, date_time=_zip_datetime(epoch))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, entry.data, compress_type=zipfile.ZIP_DEFLATED)


def _write_tar_gz_archive(entries: Iterable[CandidateEntry], destination: Path, epoch: int) -> None:
    ordered = _ordered_entries(entries)
    with destination.open("wb") as raw, gzip.GzipFile(
        filename="",
        mode="wb",
        fileobj=raw,
        mtime=epoch,
        compresslevel=9,
    ) as compressed, tarfile.open(
        fileobj=compressed,
        mode="w",
        format=tarfile.GNU_FORMAT,
    ) as archive:
        for entry in ordered:
            metadata = tarfile.TarInfo(entry.name)
            metadata.size = len(entry.data)
            metadata.mode = 0o644
            metadata.uid = 0
            metadata.gid = 0
            metadata.uname = ""
            metadata.gname = ""
            metadata.mtime = epoch
            metadata.type = tarfile.REGTYPE
            archive.addfile(metadata, io.BytesIO(entry.data))


def _write_manifest(candidate: ReleaseCandidate, output: Path) -> ReleaseArtifactManifest:
    artifact_paths = sorted(
        (path for path in output.iterdir() if path.is_file()),
        key=lambda path: path.name,
    )
    if len(artifact_paths) != 4:
        raise ReleaseGateError("release_build_output_invalid")
    artifacts: list[ReleaseArtifact] = []
    for path in artifact_paths:
        kind = _artifact_kind(path.name)
        if kind is None:
            raise ReleaseGateError("release_build_output_invalid")
        artifacts.append(
            ReleaseArtifact(
                file_name=path.name,
                kind=kind,
                size=path.stat().st_size,
                sha256=_sha256_file(path),
                version=candidate.version,
                commit_sha=candidate.commit_sha,
                build_epoch=candidate.build_epoch,
            )
        )
    if {artifact.kind for artifact in artifacts} != {"wheel", "sdist", "plugin", "source"}:
        raise ReleaseGateError("release_build_output_invalid")
    manifest = ReleaseArtifactManifest(
        version=candidate.version,
        commit_sha=candidate.commit_sha,
        build_epoch=candidate.build_epoch,
        artifacts=tuple(sorted(artifacts, key=lambda artifact: artifact.file_name)),
    )
    try:
        encoded = json.dumps(
            manifest.as_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        (output / MANIFEST_FILE_NAME).write_bytes(encoded)
    except OSError as exc:
        raise ReleaseGateError("release_manifest_write_failed") from exc
    return manifest


def _run_task13_artifact_gate(root: Path, artifacts: Path) -> ReleaseScannerAttestation:
    repo = _repository_name_from_origin(root)
    try:
        from mercury_tools.release.hosted import HostedAdapterConfig, build_hosted_clients

        manifest = load_public_surface_manifest(
            root / "docs/release/public-surface-manifest.json"
        )
        allowlist = load_secret_scan_allowlist(root / "docs/release/secret-scan-allowlist.json")
        request = SecretScanRequest(
            repo=repo,
            artifacts=artifacts,
            all_history=True,
            hosted=True,
            manifest=manifest,
            allowlist=allowlist,
            policy=SecretScanPolicy(scanner_versions=manifest.scanner_versions),
        )
        gh_path = shutil.which("gh")
        hosted_clients = build_hosted_clients(
            HostedAdapterConfig(
                repo=repo,
                gh_executable=Path(gh_path) if gh_path else None,
                github_token=_environment_secret("GH_TOKEN"),
                marketplace_url=os.environ.get("MERCURY_MARKETPLACE_SNAPSHOT_URL") or None,
                render_api_url=os.environ.get("MERCURY_RENDER_API_URL") or None,
                render_service_id=os.environ.get("MERCURY_RENDER_SERVICE_ID") or None,
                render_token=_environment_secret("RENDER_API_KEY"),
                supabase_url=os.environ.get("SUPABASE_URL") or None,
                supabase_key=_environment_secret("SUPABASE_SERVICE_ROLE_KEY"),
                supabase_knowledge_tables=_environment_values(
                    "MERCURY_RELEASE_KNOWLEDGE_TABLES"
                ),
                supabase_storage_buckets=_environment_values(
                    "MERCURY_RELEASE_STORAGE_BUCKETS"
                ),
                public_mcp_url=os.environ.get("MERCURY_PUBLIC_MCP_URL") or None,
                public_mcp_token=_environment_secret("MERCURY_PUBLIC_MCP_TOKEN"),
            )
        )
        report = scan_public_release(request, hosted_clients=hosted_clients)
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    return ReleaseScannerAttestation(passed=report.passed)


def _environment_secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _environment_values(name: str) -> tuple[str, ...]:
    values = (item.strip() for item in os.environ.get(name, "").split(","))
    return tuple(dict.fromkeys(item for item in values if item))


def _repository_name_from_origin(root: Path) -> str:
    result = _run_command(
        ("git", "remote", "get-url", "origin"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    value = result.stdout.decode("utf-8", errors="ignore").strip()
    if result.exit_code != 0:
        raise ReleaseGateError("release_scanner_context_unavailable")
    ssh_match = re.fullmatch(
        r"git@github\.com:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:\.git)?",
        value,
    )
    if ssh_match is not None:
        return f"{ssh_match.group(1)}/{ssh_match.group(2)}"
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.username:
        raise ReleaseGateError("release_scanner_context_unavailable")
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path):
        raise ReleaseGateError("release_scanner_context_unavailable")
    return path


def _run_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    timeout_seconds: float,
):
    runner = SubprocessCommandRunner(
        max_output_bytes=_MAX_COMMAND_OUTPUT,
        timeout_seconds=timeout_seconds,
    )
    return runner.run(argv, cwd=cwd)


def _require_output_absent(output: Path) -> None:
    try:
        output.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    raise ReleaseGateError("release_output_invalid")


def _ordered_entries(entries: Iterable[CandidateEntry]) -> tuple[CandidateEntry, ...]:
    ordered = tuple(sorted(entries, key=lambda entry: entry.name))
    names = [entry.name for entry in ordered]
    canonical_names = [name.casefold() for name in names]
    if (
        len(names) != len(set(canonical_names))
        or any(not _safe_archive_name(name) for name in names)
    ):
        raise ReleaseGateError("release_archive_member_invalid")
    return ordered


def _safe_archive_name(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(name)
        and name == path.as_posix()
        and path.as_posix() != "."
        and not path.is_absolute()
        and "\\" not in name
        and ".." not in path.parts
    )


def _zip_datetime(epoch: int) -> tuple[int, int, int, int, int, int]:
    timestamp = datetime.fromtimestamp(epoch, tz=UTC)
    return (
        timestamp.year,
        timestamp.month,
        timestamp.day,
        timestamp.hour,
        timestamp.minute,
        timestamp.second - (timestamp.second % 2),
    )


def _artifact_kind(name: str) -> str | None:
    if name.endswith(".whl"):
        return "wheel"
    if name.endswith(".tar.gz") and "-source" in name:
        return "source"
    if name.endswith(".zip") and "plugin" in name:
        return "plugin"
    if name.endswith(".tar.gz"):
        return "sdist"
    return None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseGateError("release_artifact_read_failed") from exc
    return digest.hexdigest()


def _strict_json_loads(value: str) -> object:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in pairs:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = item
        return result

    return json.loads(value, object_pairs_hook=unique_object)


def _valid_artifact(artifact: ReleaseArtifact) -> bool:
    return (
        isinstance(artifact.file_name, str)
        and "/" not in artifact.file_name
        and "\\" not in artifact.file_name
        and artifact.kind in {"wheel", "sdist", "plugin", "source"}
        and type(artifact.size) is int
        and artifact.size > 0
        and isinstance(artifact.sha256, str)
        and _SHA256_PATTERN.fullmatch(artifact.sha256) is not None
        and isinstance(artifact.version, str)
        and _VERSION_PATTERN.fullmatch(artifact.version) is not None
        and isinstance(artifact.commit_sha, str)
        and _COMMIT_PATTERN.fullmatch(artifact.commit_sha) is not None
        and type(artifact.build_epoch) is int
        and artifact.build_epoch >= 315532800
    )
