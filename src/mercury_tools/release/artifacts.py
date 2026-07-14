"""Deterministic, candidate-bound Mercury release artifact construction."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import gzip
import hashlib
import io
import json
import os
import re
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import unicodedata
import zipfile
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse

from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    GateStatus,
    SecretScanPolicy,
    SecretScanReport,
    SecretScanRequest,
)
from mercury_tools.release.scanner import (
    CommandResult,
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
_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_BACKEND_MODULE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MAX_COMMAND_OUTPUT = 256 * 1024 * 1024
_BUILD_TIMEOUT_SECONDS = 600.0
_GIT_TIMEOUT_SECONDS = 60.0
_BUILD_TOOLCHAIN_SCHEMA_VERSION = 1
_MAX_PUBLICATION_FILES = 50_000
_MAX_PUBLICATION_DIRECTORIES = 20_000
_MAX_PUBLICATION_BYTES = 2 * 1024 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024
_STAGING_NAME_PREFIX = ".mercury-release-publish-"
_RENAME_NOREPLACE = 1
_RENAME_EXCL = 0x00000004
_LINUX_RENAMEAT2_SYSCALLS = {
    "aarch64": 276,
    "arm64": 276,
    "armv7l": 382,
    "i386": 353,
    "riscv64": 276,
    "s390x": 347,
    "x86_64": 316,
}
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
class _BuildDependency:
    name: str
    version: str
    sha256: str
    file_name: str

    def as_dict(self) -> dict[str, str]:
        return {
            "file": self.file_name,
            "name": self.name,
            "sha256": self.sha256,
            "version": self.version,
        }


@dataclass(frozen=True)
class ReleaseBuilderProvenance:
    policy_sha256: str
    lock_sha256: str
    uv_version: str
    uv_sha256: str
    build_version: str
    build_sha256: str
    constraints_sha256: str
    backend_module: str
    backend_requirements: tuple[_BuildDependency, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": {
                "module": self.backend_module,
                "requirements": [
                    requirement.as_dict() for requirement in self.backend_requirements
                ],
            },
            "build": {
                "constraints_sha256": self.constraints_sha256,
                "sha256": self.build_sha256,
                "version": self.build_version,
            },
            "lock_sha256": self.lock_sha256,
            "policy_sha256": self.policy_sha256,
            "uv": {
                "sha256": self.uv_sha256,
                "version": self.uv_version,
            },
        }


@dataclass(frozen=True)
class _BuildToolchainPolicy:
    uv_path: str
    constraints_path: str
    constraints_sha256: str
    wheelhouse_path: str
    provenance: ReleaseBuilderProvenance


@dataclass(frozen=True)
class ReleaseArtifactManifest:
    version: str
    commit_sha: str
    build_epoch: int
    builder_provenance: ReleaseBuilderProvenance
    artifacts: tuple[ReleaseArtifact, ...]

    @property
    def source_date_epoch(self) -> int:
        return self.build_epoch

    def as_dict(self) -> dict[str, object]:
        return {
            "artifacts": [artifact.as_dict() for artifact in self.artifacts],
            "builder_provenance": self.builder_provenance.as_dict(),
            "commit_sha": self.commit_sha,
            "schema_version": 2,
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
    head_ref: str
    origin_url: str | None
    repository_name: str | None
    version: str
    commit_sha: str
    build_epoch: int
    entries: tuple[CandidateEntry, ...]
    build_toolchain: _BuildToolchainPolicy


@dataclass
class _OutputDestination:
    path: Path
    name: str
    parent_fd: int | None
    parent_device: int
    parent_inode: int

    def require_parent_fd(self) -> int:
        if self.parent_fd is None:
            raise ReleaseGateError("release_output_invalid")
        return self.parent_fd

    def close(self) -> None:
        if self.parent_fd is None:
            return
        with contextlib.suppress(OSError):
            os.close(self.parent_fd)
        self.parent_fd = None


@dataclass(frozen=True)
class _PrivateStaging:
    name: str
    fd: int
    device: int
    inode: int


@dataclass
class _PublicationBounds:
    files: int = 0
    directories: int = 0
    bytes_written: int = 0


def build_release_artifacts(
    root: Path,
    *,
    version: str,
    output: Path,
) -> ReleaseArtifactManifest:
    """Build exactly four reproducible artifacts from the clean candidate commit."""

    destination = _prepare_output_destination(output)
    try:
        candidate = load_release_candidate(root, version=version, require_clean=True)
        with (
            materialize_release_candidate(candidate) as snapshot,
            tempfile.TemporaryDirectory(prefix=".mercury-release-") as temporary,
        ):
            temporary_root = Path(temporary)
            staged_artifacts = temporary_root / "artifacts"
            staged_artifacts.mkdir()
            manifest = _build_artifact_set(candidate, snapshot, staged_artifacts)
            require_task13_scanner_gate(candidate, snapshot, staged_artifacts)
            _ensure_candidate_unchanged(candidate)
            _publish_owned_directory(staged_artifacts, destination)
            return manifest
    except ReleaseGateError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as exc:
        raise ReleaseGateError("release_artifact_write_failed") from exc
    finally:
        destination.close()


def load_release_candidate(
    root: Path,
    *,
    version: str,
    require_clean: bool,
) -> ReleaseCandidate:
    """Read the reviewed Git commit without importing worktree-only content."""

    root = _resolve_root(root)
    commit_sha = git_head(root)
    head_ref = _git_head_ref(root)
    _require_git_commit_object(root, commit_sha)
    origin_url = _origin_url(root)
    build_epoch = git_commit_epoch(root, commit_sha)
    entries = _candidate_entries(root, commit_sha)
    if not entries:
        raise ReleaseGateError("release_candidate_empty")
    _require_version_matches_entries(entries, version)
    build_toolchain = _load_build_toolchain_policy(entries)
    candidate = ReleaseCandidate(
        root=root,
        head_ref=head_ref,
        origin_url=origin_url,
        repository_name=(
            _repository_name_from_origin_url(origin_url) if origin_url is not None else None
        ),
        version=version,
        commit_sha=commit_sha,
        build_epoch=build_epoch,
        entries=entries,
        build_toolchain=build_toolchain,
    )
    if require_clean:
        require_clean_worktree(root)
        _ensure_candidate_unchanged(candidate)
    return candidate


@contextmanager
def materialize_release_candidate(candidate: ReleaseCandidate) -> Iterator[Path]:
    """Materialize the captured candidate once in an owned temporary snapshot."""

    with tempfile.TemporaryDirectory(prefix=".mercury-candidate-") as temporary:
        snapshot = Path(temporary) / "candidate"
        snapshot.mkdir()
        _write_candidate_tree(candidate.entries, snapshot)
        yield snapshot


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


def _git_head_ref(root: Path) -> str:
    result = _run_command(
        ("git", "rev-parse", "--symbolic-full-name", "HEAD"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    value = result.stdout.decode("utf-8", errors="ignore").strip()
    if result.exit_code != 0 or not value or "\n" in value or "\0" in value:
        raise ReleaseGateError("release_candidate_invalid")
    return value


def _require_git_commit_object(root: Path, commit_sha: str) -> None:
    result = _run_command(
        ("git", "cat-file", "-e", f"{commit_sha}^{{commit}}"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    if result.exit_code != 0:
        raise ReleaseGateError("release_candidate_invalid")


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
        "builder_provenance",
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
    raw_provenance = payload["builder_provenance"]
    if (
        payload["schema_version"] != 2
        or not isinstance(version, str)
        or not _VERSION_PATTERN.fullmatch(version)
        or not isinstance(commit_sha, str)
        or not _COMMIT_PATTERN.fullmatch(commit_sha)
        or type(epoch) is not int
        or epoch < 315532800
        or not isinstance(raw_artifacts, list)
    ):
        raise ReleaseGateError("release_manifest_invalid")
    builder_provenance = _parse_builder_provenance(raw_provenance)

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
        builder_provenance=builder_provenance,
        artifacts=tuple(artifacts),
    )


def _parse_builder_provenance(value: object) -> ReleaseBuilderProvenance:
    if not isinstance(value, dict) or set(value) != {
        "policy_sha256",
        "lock_sha256",
        "uv",
        "build",
        "backend",
    }:
        raise ReleaseGateError("release_manifest_invalid")
    policy_sha256 = _manifest_sha256(value["policy_sha256"])
    lock_sha256 = _manifest_sha256(value["lock_sha256"])
    uv = value["uv"]
    build = value["build"]
    backend = value["backend"]
    if not isinstance(uv, dict) or set(uv) != {"version", "sha256"}:
        raise ReleaseGateError("release_manifest_invalid")
    if not isinstance(build, dict) or set(build) != {"version", "sha256", "constraints_sha256"}:
        raise ReleaseGateError("release_manifest_invalid")
    if not isinstance(backend, dict) or set(backend) != {"module", "requirements"}:
        raise ReleaseGateError("release_manifest_invalid")
    uv_version = _manifest_toolchain_version(uv["version"])
    uv_sha256 = _manifest_sha256(uv["sha256"])
    build_version = _manifest_toolchain_version(build["version"])
    build_sha256 = _manifest_sha256(build["sha256"])
    constraints_sha256 = _manifest_sha256(build["constraints_sha256"])
    backend_module = backend["module"]
    raw_requirements = backend["requirements"]
    if (
        not isinstance(backend_module, str)
        or not _BACKEND_MODULE_PATTERN.fullmatch(backend_module)
        or not isinstance(raw_requirements, list)
        or not raw_requirements
    ):
        raise ReleaseGateError("release_manifest_invalid")
    requirements = tuple(_parse_manifest_build_dependency(item) for item in raw_requirements)
    if (
        len({item.name.casefold() for item in requirements}) != len(requirements)
        or len({item.file_name.casefold() for item in requirements}) != len(requirements)
    ):
        raise ReleaseGateError("release_manifest_invalid")
    return ReleaseBuilderProvenance(
        policy_sha256=policy_sha256,
        lock_sha256=lock_sha256,
        uv_version=uv_version,
        uv_sha256=uv_sha256,
        build_version=build_version,
        build_sha256=build_sha256,
        constraints_sha256=constraints_sha256,
        backend_module=backend_module,
        backend_requirements=requirements,
    )


def _manifest_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ReleaseGateError("release_manifest_invalid")
    return value


def _manifest_toolchain_version(value: object) -> str:
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise ReleaseGateError("release_manifest_invalid")
    return value


def _parse_manifest_build_dependency(value: object) -> _BuildDependency:
    if not isinstance(value, dict) or set(value) != {"name", "version", "sha256", "file"}:
        raise ReleaseGateError("release_manifest_invalid")
    name = value["name"]
    if not isinstance(name, str) or not _PACKAGE_NAME_PATTERN.fullmatch(name):
        raise ReleaseGateError("release_manifest_invalid")
    version = _manifest_toolchain_version(value["version"])
    sha256 = _manifest_sha256(value["sha256"])
    file_name = value["file"]
    if not isinstance(file_name, str):
        raise ReleaseGateError("release_manifest_invalid")
    try:
        validate_canonical_archive_member_names((file_name,))
    except ReleaseGateError as exc:
        raise ReleaseGateError("release_manifest_invalid") from exc
    return _BuildDependency(name=name, version=version, sha256=sha256, file_name=file_name)


def require_task13_scanner_gate(
    candidate: ReleaseCandidate,
    snapshot: Path,
    target: Path,
) -> None:
    try:
        report = _run_task13_artifact_gate(candidate, snapshot, target)
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    _require_complete_task13_report(report)


def _require_complete_task13_report(report: object) -> None:
    if type(report) is not SecretScanReport:
        raise ReleaseGateError("release_scanner_gate_unavailable")
    try:
        validated = SecretScanReport.model_validate(report.model_dump())
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    expected_scanners = tuple(
        (name, version, GateStatus.PASSED, 0) for name, version in PINNED_SCANNER_VERSIONS.items()
    )
    actual_scanners = tuple(
        (scanner.scanner, scanner.version, scanner.status, scanner.exit_code)
        for scanner in validated.scanner_versions
    )
    if (
        validated.status is not GateStatus.PASSED
        or actual_scanners != expected_scanners
        or tuple(surface.surface for surface in validated.surfaces) != REQUIRED_PUBLIC_SURFACES
        or any(surface.status is not GateStatus.PASSED for surface in validated.surfaces)
    ):
        raise ReleaseGateError("release_scanner_gate_blocked")


def source_tree_digest(entries: Iterable[CandidateEntry]) -> str:
    digest = hashlib.sha256()
    for entry in _ordered_entries(entries):
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


def _require_version_matches_entries(entries: Iterable[CandidateEntry], version: str) -> None:
    if not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseGateError("release_version_invalid")
    try:
        pyproject = next(entry for entry in entries if entry.name == "pyproject.toml")
        payload = tomllib.loads(pyproject.data.decode("utf-8"))
        project = payload["project"]
        package_version = project["version"] if isinstance(project, dict) else None
    except (StopIteration, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise ReleaseGateError("release_version_unavailable") from exc
    if package_version != version:
        raise ReleaseGateError("release_version_mismatch")


def _load_build_toolchain_policy(
    entries: Iterable[CandidateEntry],
) -> _BuildToolchainPolicy:
    entry_map = {entry.name: entry for entry in entries}
    pyproject_entry = entry_map.get("pyproject.toml")
    if pyproject_entry is None:
        raise ReleaseGateError("release_build_toolchain_invalid")
    try:
        pyproject = tomllib.loads(pyproject_entry.data.decode("utf-8"))
    except (UnicodeError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    if not isinstance(pyproject, dict):
        raise ReleaseGateError("release_build_toolchain_invalid")
    tool = pyproject.get("tool")
    if tool is None:
        raise ReleaseGateError("release_build_toolchain_policy_missing")
    if not isinstance(tool, dict):
        raise ReleaseGateError("release_build_toolchain_invalid")
    mercury = tool.get("mercury")
    if mercury is None:
        raise ReleaseGateError("release_build_toolchain_policy_missing")
    if not isinstance(mercury, dict):
        raise ReleaseGateError("release_build_toolchain_invalid")
    policy = mercury.get("release-build")
    if policy is None:
        raise ReleaseGateError("release_build_toolchain_policy_missing")
    if not isinstance(policy, dict):
        raise ReleaseGateError("release_build_toolchain_invalid")
    try:
        if set(policy) != {"schema_version", "lock_sha256", "uv", "build", "backend"}:
            raise ReleaseGateError("release_build_toolchain_invalid")
        if policy["schema_version"] != _BUILD_TOOLCHAIN_SCHEMA_VERSION or type(
            policy["schema_version"]
        ) is not int:
            raise ReleaseGateError("release_build_toolchain_invalid")
        lock_sha256 = _require_toolchain_sha256(policy["lock_sha256"])
        lock_entry = entry_map.get("uv.lock")
        if lock_entry is None or _sha256_bytes(lock_entry.data) != lock_sha256:
            raise ReleaseGateError("release_build_toolchain_invalid")
        lock_payload = tomllib.loads(lock_entry.data.decode("utf-8"))
        if not isinstance(lock_payload, dict):
            raise ReleaseGateError("release_build_toolchain_invalid")

        uv = policy["uv"]
        build = policy["build"]
        backend = policy["backend"]
        if not isinstance(uv, dict) or set(uv) != {"path", "version", "sha256"}:
            raise ReleaseGateError("release_build_toolchain_invalid")
        if not isinstance(build, dict) or set(build) != {
            "command",
            "version",
            "sha256",
            "constraints",
            "constraints_sha256",
            "wheelhouse",
        }:
            raise ReleaseGateError("release_build_toolchain_invalid")
        if not isinstance(backend, dict) or set(backend) != {"module", "requirements"}:
            raise ReleaseGateError("release_build_toolchain_invalid")

        uv_path = _require_toolchain_relative_path(uv["path"])
        uv_version = _require_toolchain_version(uv["version"])
        uv_sha256 = _require_toolchain_sha256(uv["sha256"])
        uv_entry = entry_map.get(uv_path)
        if (
            uv_entry is None
            or not uv_entry.mode & 0o111
            or _sha256_bytes(uv_entry.data) != uv_sha256
        ):
            raise ReleaseGateError("release_build_toolchain_invalid")

        if (
            build["command"] != "uv build"
            or _require_toolchain_version(build["version"]) != uv_version
            or _require_toolchain_sha256(build["sha256"]) != uv_sha256
        ):
            raise ReleaseGateError("release_build_toolchain_invalid")
        constraints_path = _require_toolchain_relative_path(build["constraints"])
        constraints_sha256 = _require_toolchain_sha256(build["constraints_sha256"])
        constraints_entry = entry_map.get(constraints_path)
        if (
            constraints_entry is None
            or _sha256_bytes(constraints_entry.data) != constraints_sha256
        ):
            raise ReleaseGateError("release_build_toolchain_invalid")
        wheelhouse_path = _require_toolchain_relative_path(build["wheelhouse"])

        backend_module = backend["module"]
        if not isinstance(backend_module, str) or not _BACKEND_MODULE_PATTERN.fullmatch(
            backend_module
        ):
            raise ReleaseGateError("release_build_toolchain_invalid")
        raw_requirements = backend["requirements"]
        if not isinstance(raw_requirements, list) or not raw_requirements:
            raise ReleaseGateError("release_build_toolchain_invalid")
        dependencies = tuple(
            _parse_build_dependency(value, entry_map, wheelhouse_path)
            for value in raw_requirements
        )
        if len({dependency.name.casefold() for dependency in dependencies}) != len(dependencies):
            raise ReleaseGateError("release_build_toolchain_invalid")
        _require_exact_build_system(pyproject, backend_module, dependencies)
        _require_locked_backend_inputs(lock_payload, dependencies)
        _require_exact_build_constraints(constraints_entry.data, dependencies)
    except ReleaseGateError:
        raise
    except (KeyError, TypeError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    provenance = ReleaseBuilderProvenance(
        policy_sha256=_sha256_bytes(pyproject_entry.data),
        lock_sha256=lock_sha256,
        uv_version=uv_version,
        uv_sha256=uv_sha256,
        build_version=uv_version,
        build_sha256=uv_sha256,
        constraints_sha256=constraints_sha256,
        backend_module=backend_module,
        backend_requirements=dependencies,
    )
    return _BuildToolchainPolicy(
        uv_path=uv_path,
        constraints_path=constraints_path,
        constraints_sha256=constraints_sha256,
        wheelhouse_path=wheelhouse_path,
        provenance=provenance,
    )


def _require_toolchain_sha256(value: object) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise ReleaseGateError("release_build_toolchain_invalid")
    return value


def _require_toolchain_version(value: object) -> str:
    if not isinstance(value, str) or not _VERSION_PATTERN.fullmatch(value):
        raise ReleaseGateError("release_build_toolchain_invalid")
    return value


def _require_toolchain_relative_path(value: object) -> str:
    if not isinstance(value, str):
        raise ReleaseGateError("release_build_toolchain_invalid")
    try:
        validate_canonical_archive_member_names((value,))
    except ReleaseGateError as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    return value


def _parse_build_dependency(
    value: object,
    entry_map: dict[str, CandidateEntry],
    wheelhouse_path: str,
) -> _BuildDependency:
    if not isinstance(value, dict) or set(value) != {"name", "version", "sha256", "file"}:
        raise ReleaseGateError("release_build_toolchain_invalid")
    name = value["name"]
    if not isinstance(name, str) or not _PACKAGE_NAME_PATTERN.fullmatch(name):
        raise ReleaseGateError("release_build_toolchain_invalid")
    version = _require_toolchain_version(value["version"])
    sha256 = _require_toolchain_sha256(value["sha256"])
    file_name = _require_toolchain_relative_path(value["file"])
    if not file_name.startswith(f"{wheelhouse_path}/"):
        raise ReleaseGateError("release_build_toolchain_invalid")
    entry = entry_map.get(file_name)
    if entry is None or _sha256_bytes(entry.data) != sha256:
        raise ReleaseGateError("release_build_toolchain_invalid")
    return _BuildDependency(
        name=name,
        version=version,
        sha256=sha256,
        file_name=file_name,
    )


def _require_exact_build_system(
    pyproject: dict[str, object],
    backend_module: str,
    dependencies: tuple[_BuildDependency, ...],
) -> None:
    build_system = pyproject.get("build-system")
    if not isinstance(build_system, dict) or set(build_system) != {"requires", "build-backend"}:
        raise ReleaseGateError("release_build_toolchain_invalid")
    requires = build_system["requires"]
    if (
        not isinstance(requires, list)
        or any(not isinstance(requirement, str) for requirement in requires)
        or tuple(requires)
        != tuple(f"{dependency.name}=={dependency.version}" for dependency in dependencies)
        or build_system["build-backend"] != backend_module
    ):
        raise ReleaseGateError("release_build_toolchain_invalid")


def _require_locked_backend_inputs(
    lock_payload: dict[str, object],
    dependencies: tuple[_BuildDependency, ...],
) -> None:
    packages = lock_payload.get("package")
    if not isinstance(packages, list):
        raise ReleaseGateError("release_build_toolchain_invalid")
    for dependency in dependencies:
        matches = [
            package
            for package in packages
            if isinstance(package, dict)
            and package.get("name") == dependency.name
            and package.get("version") == dependency.version
        ]
        if len(matches) != 1 or dependency.sha256 not in _lock_distribution_hashes(matches[0]):
            raise ReleaseGateError("release_build_toolchain_invalid")


def _lock_distribution_hashes(package: dict[str, object]) -> set[str]:
    hashes: set[str] = set()
    wheels = package.get("wheels", [])
    if not isinstance(wheels, list):
        raise ReleaseGateError("release_build_toolchain_invalid")
    for wheel in wheels:
        if isinstance(wheel, dict) and isinstance(wheel.get("hash"), str):
            hashes.add(str(wheel["hash"]).removeprefix("sha256:"))
    sdist = package.get("sdist")
    if isinstance(sdist, dict) and isinstance(sdist.get("hash"), str):
        hashes.add(str(sdist["hash"]).removeprefix("sha256:"))
    return hashes


def _require_exact_build_constraints(
    data: bytes,
    dependencies: tuple[_BuildDependency, ...],
) -> None:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    expected = [
        f"{dependency.name}=={dependency.version} --hash=sha256:{dependency.sha256}"
        for dependency in dependencies
    ]
    if lines != expected:
        raise ReleaseGateError("release_build_toolchain_invalid")


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
            members = tuple(member for member in archive.getmembers() if not member.isdir())
            if any(not member.isfile() for member in members):
                raise ReleaseGateError("release_archive_member_invalid")
            validate_canonical_archive_member_names(member.name for member in members)
            entries: list[CandidateEntry] = []
            for member in members:
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
    try:
        destination = destination.resolve(strict=True)
        metadata = destination.lstat()
    except OSError as exc:
        raise ReleaseGateError("release_archive_member_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseGateError("release_archive_member_invalid")
    for entry in _ordered_entries(entries):
        path = destination.joinpath(*entry.name.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            resolved_parent = path.parent.resolve(strict=True)
            resolved_parent.relative_to(destination)
            path.lstat()
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as exc:
            raise ReleaseGateError("release_archive_member_invalid") from exc
        else:
            raise ReleaseGateError("release_archive_member_invalid")
        try:
            with path.open("xb") as stream:
                stream.write(entry.data)
            os.chmod(path, entry.mode)
        except OSError as exc:
            raise ReleaseGateError("release_archive_member_invalid") from exc


def _build_artifact_set(
    candidate: ReleaseCandidate,
    source_root: Path,
    output: Path,
) -> ReleaseArtifactManifest:
    _build_distributions(candidate, source_root, output)
    _build_plugin_archive(candidate, output)
    _build_source_archive(candidate, output)
    return _write_manifest(candidate, output)


def _build_distributions(
    candidate: ReleaseCandidate,
    source_root: Path,
    staged_artifacts: Path,
) -> None:
    raw_output = staged_artifacts / "raw"
    build_workspace = staged_artifacts / ".build-toolchain"
    raw_output.mkdir()
    build_workspace.mkdir()
    try:
        uv, constraints, wheelhouse = _verify_materialized_build_toolchain(candidate, source_root)
        environment = _isolated_build_environment(candidate, build_workspace)
        _require_exact_uv_version(uv, candidate, source_root, environment)
        lock_result = _run_isolated_build_command(
            (
                str(uv),
                "lock",
                "--check",
                "--offline",
                "--no-config",
                "--no-index",
                "--no-sources",
                "--no-python-downloads",
                "--no-progress",
                "--color",
                "never",
            ),
            cwd=source_root,
            environment=environment,
        )
        if lock_result.exit_code != 0:
            raise ReleaseGateError("release_build_toolchain_invalid")
        result = _run_isolated_build_command(
            (
                str(uv),
                "build",
                "--wheel",
                "--sdist",
                "--out-dir",
                str(raw_output),
                "--offline",
                "--no-config",
                "--no-index",
                "--no-sources",
                "--no-python-downloads",
                "--require-hashes",
                "--build-constraints",
                str(constraints),
                "--find-links",
                str(wheelhouse),
                "--no-progress",
                "--color",
                "never",
            ),
            cwd=source_root,
            environment=environment,
        )
        if result.exit_code != 0:
            raise ReleaseGateError("release_build_failed")
        _verify_materialized_build_toolchain(candidate, source_root)
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
    finally:
        shutil.rmtree(raw_output, ignore_errors=True)
        shutil.rmtree(build_workspace, ignore_errors=True)


def _verify_materialized_build_toolchain(
    candidate: ReleaseCandidate,
    source_root: Path,
) -> tuple[Path, Path, Path]:
    policy = candidate.build_toolchain
    provenance = policy.provenance
    pyproject = _materialized_candidate_file(source_root, "pyproject.toml")
    lock = _materialized_candidate_file(source_root, "uv.lock")
    uv = _materialized_candidate_file(source_root, policy.uv_path)
    constraints = _materialized_candidate_file(source_root, policy.constraints_path)
    wheelhouse = source_root.joinpath(*policy.wheelhouse_path.split("/"))
    try:
        wheelhouse_metadata = wheelhouse.lstat()
    except OSError as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    if stat.S_ISLNK(wheelhouse_metadata.st_mode) or not stat.S_ISDIR(wheelhouse_metadata.st_mode):
        raise ReleaseGateError("release_build_toolchain_invalid")
    if (
        _sha256_file(pyproject) != provenance.policy_sha256
        or _sha256_file(lock) != provenance.lock_sha256
        or _sha256_file(uv) != provenance.uv_sha256
        or _sha256_file(constraints) != policy.constraints_sha256
        or not uv.stat().st_mode & 0o111
    ):
        raise ReleaseGateError("release_build_toolchain_invalid")
    for dependency in provenance.backend_requirements:
        path = _materialized_candidate_file(source_root, dependency.file_name)
        if path.parent != wheelhouse or _sha256_file(path) != dependency.sha256:
            raise ReleaseGateError("release_build_toolchain_invalid")
    return uv, constraints, wheelhouse


def _materialized_candidate_file(source_root: Path, name: str) -> Path:
    path = source_root.joinpath(*name.split("/"))
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ReleaseGateError("release_build_toolchain_invalid")
    return path


def _isolated_build_environment(
    candidate: ReleaseCandidate,
    workspace: Path,
) -> dict[str, str]:
    home = workspace / "home"
    cache = workspace / "uv-cache"
    temporary = workspace / "tmp"
    try:
        for path in (home, cache, temporary):
            path.mkdir(mode=0o700)
    except OSError as exc:
        raise ReleaseGateError("release_build_toolchain_invalid") from exc
    return {
        "HOME": str(home),
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
        "PYTHONHASHSEED": "0",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": "",
        "SOURCE_DATE_EPOCH": str(candidate.build_epoch),
        "TMPDIR": str(temporary),
        "UV_CACHE_DIR": str(cache),
        "UV_FROZEN": "1",
        "UV_NO_CONFIG": "1",
        "UV_NO_INDEX": "1",
        "UV_NO_PROGRESS": "1",
        "UV_OFFLINE": "1",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_REQUIRE_HASHES": "1",
    }


def _require_exact_uv_version(
    uv: Path,
    candidate: ReleaseCandidate,
    source_root: Path,
    environment: dict[str, str],
) -> None:
    result = _run_isolated_build_command(
        (str(uv), "--version"),
        cwd=source_root,
        environment=environment,
    )
    value = result.stdout.decode("utf-8", errors="ignore").strip()
    expected = f"uv {candidate.build_toolchain.provenance.uv_version}"
    if result.exit_code != 0 or not (value == expected or value.startswith(f"{expected} ")):
        raise ReleaseGateError("release_build_toolchain_invalid")


def _run_isolated_build_command(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str],
) -> CommandResult:
    try:
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            start_new_session=True,
        )
    except OSError as exc:
        return CommandResult(exit_code=127, stdout=b"", stderr=str(exc).encode("utf-8"))
    assert process.stdout is not None
    assert process.stderr is not None
    selector = selectors.DefaultSelector()
    output = bytearray()
    error = bytearray()
    total_output = 0
    deadline = time.monotonic() + _BUILD_TIMEOUT_SECONDS
    try:
        for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, label)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_isolated_build_process(process)
                return CommandResult(exit_code=124, stdout=b"", stderr=b"")
            for key, _mask in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                total_output += len(chunk)
                if total_output > _MAX_COMMAND_OUTPUT:
                    _terminate_isolated_build_process(process)
                    return CommandResult(exit_code=125, stdout=b"", stderr=b"")
                if key.data == "stdout":
                    output.extend(chunk)
                else:
                    error.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_isolated_build_process(process)
            return CommandResult(exit_code=124, stdout=b"", stderr=b"")
        try:
            exit_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _terminate_isolated_build_process(process)
            return CommandResult(exit_code=124, stdout=b"", stderr=b"")
        return CommandResult(exit_code=exit_code, stdout=bytes(output), stderr=bytes(error))
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            with contextlib.suppress(OSError):
                stream.close()


def _terminate_isolated_build_process(process: subprocess.Popen[bytes]) -> None:
    with contextlib.suppress(ProcessLookupError, OSError):
        os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(OSError):
        process.kill()
    with contextlib.suppress(OSError):
        process.wait(timeout=5.0)


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
    validate_canonical_archive_member_names(entry.name for entry in entries)
    _write_zip_archive(entries, destination, epoch)


def _normalize_tar_gz_archive(source: Path, destination: Path, epoch: int) -> None:
    try:
        with tarfile.open(source, mode="r:gz") as archive:
            entries: list[CandidateEntry] = []
            for member in archive.getmembers():
                if member.isdir():
                    continue
                if not member.isfile():
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
    validate_canonical_archive_member_names(entry.name for entry in entries)
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
    with (
        destination.open("wb") as raw,
        gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw,
            mtime=epoch,
            compresslevel=9,
        ) as compressed,
        tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.GNU_FORMAT,
        ) as archive,
    ):
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
        builder_provenance=candidate.build_toolchain.provenance,
        artifacts=tuple(sorted(artifacts, key=lambda artifact: artifact.file_name)),
    )
    try:
        encoded = (
            json.dumps(
                manifest.as_dict(),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
        (output / MANIFEST_FILE_NAME).write_bytes(encoded)
    except OSError as exc:
        raise ReleaseGateError("release_manifest_write_failed") from exc
    return manifest


def _run_task13_artifact_gate(
    candidate: ReleaseCandidate,
    snapshot: Path,
    artifacts: Path,
) -> SecretScanReport:
    repo = candidate.repository_name
    if repo is None:
        raise ReleaseGateError("release_scanner_context_unavailable")
    try:
        from mercury_tools.release.hosted import HostedAdapterConfig, build_hosted_clients

        manifest = load_public_surface_manifest(
            snapshot / "docs/release/public-surface-manifest.json"
        )
        allowlist = load_secret_scan_allowlist(snapshot / "docs/release/secret-scan-allowlist.json")
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
                supabase_knowledge_tables=_environment_values("MERCURY_RELEASE_KNOWLEDGE_TABLES"),
                supabase_storage_buckets=_environment_values("MERCURY_RELEASE_STORAGE_BUCKETS"),
                public_mcp_url=os.environ.get("MERCURY_PUBLIC_MCP_URL") or None,
                public_mcp_token=_environment_secret("MERCURY_PUBLIC_MCP_TOKEN"),
            )
        )
        report = scan_public_release(request, hosted_clients=hosted_clients)
    except ReleaseGateError:
        raise
    except Exception as exc:
        raise ReleaseGateError("release_scanner_gate_unavailable") from exc
    return report


def _environment_secret(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _environment_values(name: str) -> tuple[str, ...]:
    values = (item.strip() for item in os.environ.get(name, "").split(","))
    return tuple(dict.fromkeys(item for item in values if item))


def _origin_url(root: Path) -> str | None:
    result = _run_command(
        ("git", "remote", "get-url", "origin"),
        cwd=root,
        timeout_seconds=_GIT_TIMEOUT_SECONDS,
    )
    value = result.stdout.decode("utf-8", errors="ignore").strip()
    if result.exit_code != 0:
        return None
    return value or None


def _repository_name_from_origin_url(value: str) -> str:
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


def _ensure_candidate_unchanged(candidate: ReleaseCandidate) -> None:
    try:
        require_clean_worktree(candidate.root)
        if git_head(candidate.root) != candidate.commit_sha:
            raise ReleaseGateError("release_candidate_changed")
        if _git_head_ref(candidate.root) != candidate.head_ref:
            raise ReleaseGateError("release_candidate_changed")
        _require_git_commit_object(candidate.root, candidate.commit_sha)
        if _origin_url(candidate.root) != candidate.origin_url:
            raise ReleaseGateError("release_candidate_changed")
    except ReleaseGateError as exc:
        if str(exc) == "release_candidate_changed":
            raise
        raise ReleaseGateError("release_candidate_changed") from exc


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


def _prepare_output_destination(output: Path) -> _OutputDestination:
    requested = output.expanduser()
    if requested.name in {"", ".", ".."}:
        raise ReleaseGateError("release_output_invalid")
    parent_fd: int | None = None
    try:
        requested.parent.mkdir(parents=True, exist_ok=True)
        parent_fd = _open_directory_path_no_follow(requested.parent)
        metadata = os.fstat(parent_fd)
    except OSError as exc:
        if parent_fd is not None:
            os.close(parent_fd)
        raise ReleaseGateError("release_output_invalid") from exc
    try:
        if not stat.S_ISDIR(metadata.st_mode):
            raise ReleaseGateError("release_output_invalid")
        destination = requested.parent / requested.name
        _require_output_absent(destination)
        _require_child_absent(parent_fd, requested.name)
        return _OutputDestination(
            path=destination,
            name=requested.name,
            parent_fd=parent_fd,
            parent_device=metadata.st_dev,
            parent_inode=metadata.st_ino,
        )
    except ReleaseGateError:
        os.close(parent_fd)
        raise
    except OSError as exc:
        os.close(parent_fd)
        raise ReleaseGateError("release_output_invalid") from exc


def _publish_owned_directory(source: Path, destination: _OutputDestination) -> None:
    try:
        parent_fd = _require_destination_parent_fd(destination)
        _require_child_absent(parent_fd, destination.name)
        staging = _create_private_staging(parent_fd)
    except ReleaseGateError:
        raise
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    try:
        _copy_verified_tree(source, staging.fd)
        os.fsync(staging.fd)
        os.fsync(parent_fd)
        # This pathname precheck is advisory only; the descriptor-relative rename below is final.
        _require_output_absent(destination.path)
        _rename_directory_exclusive(parent_fd, staging.name, destination.name)
        os.fsync(parent_fd)
    except ReleaseGateError:
        _safe_remove_private_staging(parent_fd, staging)
        raise
    except OSError as exc:
        _safe_remove_private_staging(parent_fd, staging)
        raise ReleaseGateError("release_output_invalid") from exc
    finally:
        with contextlib.suppress(OSError):
            os.close(staging.fd)


def _directory_open_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not directory or not no_follow:
        raise ReleaseGateError("release_output_invalid")
    return os.O_RDONLY | directory | no_follow | getattr(os, "O_CLOEXEC", 0)


def _open_directory_path_no_follow(path: Path) -> int:
    return os.open(os.fspath(path), _directory_open_flags())


def _open_directory_at_no_follow(parent_fd: int, name: str) -> int:
    return os.open(name, _directory_open_flags(), dir_fd=parent_fd)


def _require_destination_parent_fd(destination: _OutputDestination) -> int:
    parent_fd = destination.require_parent_fd()
    try:
        metadata = os.fstat(parent_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_dev != destination.parent_device
        or metadata.st_ino != destination.parent_inode
    ):
        raise ReleaseGateError("release_output_invalid")
    return parent_fd


def _require_child_absent(parent_fd: int, name: str) -> None:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    raise ReleaseGateError("release_output_invalid")


def _create_private_staging(parent_fd: int) -> _PrivateStaging:
    for _attempt in range(128):
        name = f"{_STAGING_NAME_PREFIX}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            continue
        fd = _open_directory_at_no_follow(parent_fd, name)
        try:
            metadata = os.fstat(fd)
        except OSError:
            os.close(fd)
            raise
        if not stat.S_ISDIR(metadata.st_mode):
            os.close(fd)
            raise OSError(errno.ENOTDIR, "private staging is not a directory")
        return _PrivateStaging(
            name=name,
            fd=fd,
            device=metadata.st_dev,
            inode=metadata.st_ino,
        )
    raise OSError(errno.EEXIST, "unable to reserve private staging directory")


def _copy_verified_tree(source: Path, destination_fd: int) -> None:
    source_fd, source_metadata = _open_verified_source_directory(source)
    try:
        bounds = _PublicationBounds()
        _copy_directory_contents(source_fd, destination_fd, bounds)
        os.fchmod(destination_fd, _safe_publication_mode(source_metadata))
        os.fsync(destination_fd)
    finally:
        os.close(source_fd)


def _open_verified_source_directory(source: Path) -> tuple[int, os.stat_result]:
    try:
        expected = source.lstat()
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
        raise ReleaseGateError("release_output_invalid")
    try:
        source_fd = _open_directory_path_no_follow(source)
        actual = os.fstat(source_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    if not _same_directory(expected, actual):
        os.close(source_fd)
        raise ReleaseGateError("release_output_invalid")
    return source_fd, actual


def _copy_directory_contents(
    source_fd: int,
    destination_fd: int,
    bounds: _PublicationBounds,
) -> None:
    try:
        names = sorted(os.listdir(source_fd))
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    for name in names:
        _require_safe_directory_entry_name(name)
        try:
            metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        except OSError as exc:
            raise ReleaseGateError("release_output_invalid") from exc
        if stat.S_ISDIR(metadata.st_mode):
            _copy_directory_entry(source_fd, destination_fd, name, metadata, bounds)
        elif stat.S_ISREG(metadata.st_mode):
            _copy_regular_entry(source_fd, destination_fd, name, metadata, bounds)
        else:
            raise ReleaseGateError("release_output_invalid")
    try:
        os.fsync(destination_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc


def _require_safe_directory_entry_name(name: str) -> None:
    if not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name:
        raise ReleaseGateError("release_output_invalid")


def _copy_directory_entry(
    source_fd: int,
    destination_fd: int,
    name: str,
    metadata: os.stat_result,
    bounds: _PublicationBounds,
) -> None:
    bounds.directories += 1
    if bounds.directories > _MAX_PUBLICATION_DIRECTORIES:
        raise ReleaseGateError("release_output_invalid")
    try:
        child_source_fd = _open_directory_at_no_follow(source_fd, name)
        child_source_metadata = os.fstat(child_source_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    try:
        if not _same_directory(metadata, child_source_metadata):
            raise ReleaseGateError("release_output_invalid")
        os.mkdir(name, mode=0o700, dir_fd=destination_fd)
        child_destination_fd: int | None = _open_directory_at_no_follow(destination_fd, name)
        try:
            _copy_directory_contents(child_source_fd, child_destination_fd, bounds)
            os.fchmod(child_destination_fd, _safe_publication_mode(child_source_metadata))
            os.fsync(child_destination_fd)
        finally:
            if child_destination_fd is not None:
                os.close(child_destination_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    finally:
        os.close(child_source_fd)


def _copy_regular_entry(
    source_fd: int,
    destination_fd: int,
    name: str,
    metadata: os.stat_result,
    bounds: _PublicationBounds,
) -> None:
    bounds.files += 1
    if bounds.files > _MAX_PUBLICATION_FILES:
        raise ReleaseGateError("release_output_invalid")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        input_fd = os.open(name, flags, dir_fd=source_fd)
        input_metadata = os.fstat(input_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    try:
        if not _same_regular_file(metadata, input_metadata):
            raise ReleaseGateError("release_output_invalid")
        output_fd: int | None = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=destination_fd,
        )
        try:
            copied = 0
            while chunk := os.read(input_fd, _COPY_CHUNK_BYTES):
                copied += len(chunk)
                if bounds.bytes_written + copied > _MAX_PUBLICATION_BYTES:
                    raise ReleaseGateError("release_output_invalid")
                _write_all(output_fd, chunk)
            if copied != input_metadata.st_size:
                raise ReleaseGateError("release_output_invalid")
            bounds.bytes_written += copied
            os.fchmod(output_fd, _safe_publication_mode(input_metadata))
            os.fsync(output_fd)
        finally:
            if output_fd is not None:
                os.close(output_fd)
    except OSError as exc:
        raise ReleaseGateError("release_output_invalid") from exc
    finally:
        os.close(input_fd)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "unable to write publication staging")
        view = view[written:]


def _safe_publication_mode(metadata: os.stat_result) -> int:
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & 0o7000:
        raise ReleaseGateError("release_output_invalid")
    return mode & 0o777


def _same_directory(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(left.st_mode)
        and stat.S_ISDIR(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
    )


def _same_regular_file(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        stat.S_ISREG(left.st_mode)
        and stat.S_ISREG(right.st_mode)
        and left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_size == right.st_size
    )


def _rename_directory_exclusive(parent_fd: int, source_name: str, destination_name: str) -> None:
    source = os.fsencode(source_name)
    destination = os.fsencode(destination_name)
    libc = ctypes.CDLL(None, use_errno=True)
    ctypes.set_errno(0)
    if sys.platform == "darwin":
        rename = getattr(libc, "renameatx_np", None)
        if rename is None:
            raise OSError(errno.ENOSYS, "renameatx_np unavailable")
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        result = rename(parent_fd, source, parent_fd, destination, _RENAME_EXCL)
    elif sys.platform.startswith("linux"):
        rename = getattr(libc, "renameat2", None)
        if rename is not None:
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(parent_fd, source, parent_fd, destination, _RENAME_NOREPLACE)
        else:
            syscall_number = _LINUX_RENAMEAT2_SYSCALLS.get(os.uname().machine)
            syscall = getattr(libc, "syscall", None)
            if syscall_number is None or syscall is None:
                raise OSError(errno.ENOSYS, "renameat2 unavailable")
            syscall.restype = ctypes.c_long
            result = syscall(
                ctypes.c_long(syscall_number),
                ctypes.c_int(parent_fd),
                ctypes.c_char_p(source),
                ctypes.c_int(parent_fd),
                ctypes.c_char_p(destination),
                ctypes.c_uint(_RENAME_NOREPLACE),
            )
    else:
        raise OSError(errno.ENOSYS, "exclusive descriptor rename unavailable")
    if result != 0:
        error = ctypes.get_errno() or errno.EIO
        raise OSError(error, os.strerror(error))


def _safe_remove_private_staging(parent_fd: int, staging: _PrivateStaging) -> None:
    try:
        metadata = os.stat(staging.name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        return
    if not _same_staging(metadata, staging):
        return
    try:
        staging_fd = _open_directory_at_no_follow(parent_fd, staging.name)
        opened = os.fstat(staging_fd)
    except OSError:
        return
    try:
        if not _same_staging(opened, staging):
            return
        _remove_directory_contents(staging_fd)
    except OSError:
        return
    finally:
        with contextlib.suppress(OSError):
            os.close(staging_fd)
    try:
        current = os.stat(staging.name, dir_fd=parent_fd, follow_symlinks=False)
        if _same_staging(current, staging):
            os.rmdir(staging.name, dir_fd=parent_fd)
    except OSError:
        return


def _same_staging(metadata: os.stat_result, staging: _PrivateStaging) -> bool:
    return (
        stat.S_ISDIR(metadata.st_mode)
        and metadata.st_dev == staging.device
        and metadata.st_ino == staging.inode
    )


def _remove_directory_contents(directory_fd: int) -> None:
    for name in os.listdir(directory_fd):
        _require_safe_directory_entry_name(name)
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            child_fd = _open_directory_at_no_follow(directory_fd, name)
            try:
                _remove_directory_contents(child_fd)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)
    os.fsync(directory_fd)


def _ordered_entries(entries: Iterable[CandidateEntry]) -> tuple[CandidateEntry, ...]:
    ordered = tuple(sorted(entries, key=lambda entry: entry.name))
    validate_canonical_archive_member_names(entry.name for entry in ordered)
    return ordered


def validate_canonical_archive_member_names(names: Iterable[str]) -> tuple[str, ...]:
    canonical_names: list[str] = []
    collision_keys: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name or "\0" in name or "\\" in name:
            raise ReleaseGateError("release_archive_member_invalid")
        normalized = unicodedata.normalize("NFC", name)
        path = PurePosixPath(name)
        parts = name.split("/")
        canonical = "/".join(parts)
        if (
            normalized != name
            or path.is_absolute()
            or path.as_posix() != name
            or any(part in {"", ".", ".."} for part in parts)
            or canonical != name
        ):
            raise ReleaseGateError("release_archive_member_invalid")
        collision_key = normalized.casefold()
        if collision_key in collision_keys:
            raise ReleaseGateError("release_archive_member_invalid")
        collision_keys.add(collision_key)
        canonical_names.append(canonical)
    return tuple(canonical_names)


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


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
