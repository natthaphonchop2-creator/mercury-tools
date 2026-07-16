#!/usr/bin/env python3
"""Independently derive and verify the v0.2.1 release asset set."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import unicodedata
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NamedTuple

MANIFEST_NAME = "SHA256SUMS.json"
EXPECTED_SCHEMA_VERSION = 4
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_TRUSTED_SOURCE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 10_000
MAX_JSON_STRING = 16 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
MAX_PATH_BYTES = 1024
MAX_PATH_DEPTH = 32
MIN_SOURCE_DATE_EPOCH = 315_532_800
MAX_SOURCE_DATE_EPOCH = 4_102_444_800
_STORED_DEFLATE_BLOCK_BYTES = 65_535
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
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


class ValidationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class DuplicateKey(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise argparse.ArgumentError(None, message)


class SourceEntry(NamedTuple):
    name: str
    mode: int
    data: bytes


def _error(code: str) -> ValidationError:
    return ValidationError(code)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _check_json_shape(value: Any, *, depth: int = 0, nodes: list[int] | None = None) -> None:
    if nodes is None:
        nodes = [0]
    nodes[0] += 1
    if nodes[0] > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
        raise _error("manifest_invalid")
    if isinstance(value, str):
        if len(value) > MAX_JSON_STRING:
            raise _error("manifest_invalid")
    elif isinstance(value, dict):
        for key, child in value.items():
            if len(key) > MAX_JSON_STRING:
                raise _error("manifest_invalid")
            _check_json_shape(child, depth=depth + 1, nodes=nodes)
    elif isinstance(value, list):
        for child in value:
            _check_json_shape(child, depth=depth + 1, nodes=nodes)


def _check_name(name: Any) -> str:
    if not isinstance(name, str) or _SAFE_NAME.fullmatch(name) is None:
        raise _error("manifest_invalid")
    if "/" in name or "\\" in name or name in {".", ".."}:
        raise _error("manifest_invalid")
    return name


def _check_canonical_names(names: Iterable[str], *, code: str) -> tuple[str, ...]:
    canonical_names: list[str] = []
    collision_keys: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not name or "\0" in name or "\\" in name:
            raise _error(code)
        try:
            encoded_length = len(name.encode("utf-8", errors="strict"))
        except UnicodeError as exc:
            raise _error(code) from exc
        normalized = unicodedata.normalize("NFC", name)
        path = PurePosixPath(name)
        parts = name.split("/")
        if (
            encoded_length > MAX_PATH_BYTES
            or len(parts) > MAX_PATH_DEPTH
            or normalized != name
            or path.is_absolute()
            or path.as_posix() != name
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise _error(code)
        collision_key = normalized.casefold()
        if collision_key in collision_keys:
            raise _error(code)
        collision_keys.add(collision_key)
        canonical_names.append(name)
    return tuple(canonical_names)


def _check_regular_metadata(path: Path, entry_stat: os.stat_result) -> None:
    mode = entry_stat.st_mode
    if stat.S_ISLNK(mode):
        raise _error("symlink")
    if not stat.S_ISREG(mode):
        raise _error("special_file")
    if entry_stat.st_nlink != 1:
        raise _error("hardlink")
    if mode & 0o022:
        raise _error("permissions")
    if entry_stat.st_size < 0 or entry_stat.st_size > MAX_FILE_BYTES:
        raise _error("size_overflow")


def _check_directory(path: Path, *, code: str = "root_invalid") -> os.stat_result:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise _error(code) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise _error("symlink")
    if not stat.S_ISDIR(metadata.st_mode):
        raise _error(code)
    if metadata.st_mode & 0o022:
        raise _error("permissions")
    return metadata


def _open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return flags


def _read_bytes(path: Path, entry_stat: os.stat_result, limit: int) -> bytes:
    _check_regular_metadata(path, entry_stat)
    if entry_stat.st_size > limit:
        raise _error("size_overflow")
    try:
        fd = os.open(path, _open_flags())
    except OSError as exc:
        raise _error("io_error") from exc
    try:
        opened_stat = os.fstat(fd)
        _check_regular_metadata(path, opened_stat)
        if opened_stat.st_size > limit:
            raise _error("size_overflow")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise _error("size_overflow")
            chunks.append(chunk)
        if total != opened_stat.st_size:
            raise _error("io_error")
        return b"".join(chunks)
    except ValidationError:
        raise
    except OSError as exc:
        raise _error("io_error") from exc
    finally:
        os.close(fd)


def _hash_file(path: Path, expected_size: int, expected_sha256: str) -> int:
    try:
        fd = os.open(path, _open_flags())
    except OSError as exc:
        raise _error("io_error") from exc
    try:
        entry_stat = os.fstat(fd)
        _check_regular_metadata(path, entry_stat)
        if entry_stat.st_size != expected_size:
            raise _error("digest_mismatch")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_FILE_BYTES:
                raise _error("size_overflow")
            digest.update(chunk)
        if total != expected_size or digest.hexdigest() != expected_sha256:
            raise _error("digest_mismatch")
        return total
    except ValidationError:
        raise
    except OSError as exc:
        raise _error("io_error") from exc
    finally:
        os.close(fd)


def _read_manifest(path: Path, entry_stat: os.stat_result) -> dict[str, Any]:
    try:
        payload = _read_bytes(path, entry_stat, MAX_MANIFEST_BYTES)
        value = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except ValidationError:
        raise
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise _error("manifest_invalid") from exc
    if not isinstance(value, dict):
        raise _error("manifest_invalid")
    _check_json_shape(value)
    return value


def _require_int(value: Any) -> int:
    if type(value) is not int:
        raise _error("manifest_invalid")
    return value


def _expected_name(kind: str, version: str, name: str) -> bool:
    if kind == "wheel":
        return bool(re.fullmatch(rf"mercury_tools-{re.escape(version)}-[A-Za-z0-9_.-]+\.whl", name))
    expected = {
        "sdist": f"mercury_tools-{version}.tar.gz",
        "plugin": f"mercury-finance-plugin-{version}.zip",
        "source": f"mercury-tools-{version}-source.tar.gz",
    }
    return expected.get(kind) == name


def _validate_manifest(
    manifest: dict[str, Any], reviewed_sha: str, version: str
) -> tuple[list[dict[str, Any]], int]:
    if set(manifest) != {
        "artifacts",
        "builder_provenance",
        "commit_sha",
        "schema_version",
        "source_date_epoch",
        "version",
    }:
        raise _error("manifest_invalid")
    if manifest["schema_version"] != EXPECTED_SCHEMA_VERSION:
        raise _error("manifest_invalid")
    if manifest["version"] != version or manifest["commit_sha"] != reviewed_sha:
        raise _error("manifest_mismatch")
    epoch = _require_int(manifest["source_date_epoch"])
    if not MIN_SOURCE_DATE_EPOCH <= epoch <= MAX_SOURCE_DATE_EPOCH:
        raise _error("manifest_invalid")
    if not isinstance(manifest["builder_provenance"], dict):
        raise _error("manifest_invalid")
    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 4:
        raise _error("manifest_invalid")

    valid: list[dict[str, Any]] = []
    names: set[str] = set()
    kinds: set[str] = set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {
            "build_epoch",
            "commit_sha",
            "file_name",
            "kind",
            "sha256",
            "size",
            "version",
        }:
            raise _error("manifest_invalid")
        name = _check_name(item["file_name"])
        kind = item["kind"]
        if not isinstance(kind, str) or kind not in {"wheel", "sdist", "plugin", "source"}:
            raise _error("manifest_invalid")
        if not _expected_name(kind, version, name):
            raise _error("manifest_invalid")
        if name in names or kind in kinds:
            raise _error("manifest_invalid")
        names.add(name)
        kinds.add(kind)
        if item["commit_sha"] != reviewed_sha or item["version"] != version:
            raise _error("manifest_mismatch")
        if _require_int(item["build_epoch"]) != epoch:
            raise _error("manifest_invalid")
        size = _require_int(item["size"])
        if size < 0 or size > MAX_FILE_BYTES:
            raise _error("size_overflow")
        if not isinstance(item["sha256"], str) or _SHA256.fullmatch(item["sha256"]) is None:
            raise _error("manifest_invalid")
        valid.append(item)
    if kinds != {"wheel", "sdist", "plugin", "source"}:
        raise _error("manifest_invalid")
    return valid, epoch


def _candidate_inventory(
    root: Path, reviewed_sha: str, version: str
) -> tuple[list[dict[str, Any]], int, int]:
    _check_directory(root)
    try:
        entries = sorted(os.scandir(root), key=lambda entry: entry.name)
    except OSError as exc:
        raise _error("io_error") from exc
    stats: dict[str, os.stat_result] = {}
    for entry in entries:
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as exc:
            raise _error("io_error") from exc
        _check_regular_metadata(Path(entry.path), entry_stat)
        stats[entry.name] = entry_stat
    if len(entries) != 5 or MANIFEST_NAME not in stats:
        raise _error("inventory_invalid")
    manifest = _read_manifest(root / MANIFEST_NAME, stats[MANIFEST_NAME])
    artifacts, epoch = _validate_manifest(manifest, reviewed_sha, version)
    expected_names = {MANIFEST_NAME, *(item["file_name"] for item in artifacts)}
    if len(expected_names) != 5 or set(stats) != expected_names:
        raise _error("inventory_invalid")
    total = stats[MANIFEST_NAME].st_size
    if total > MAX_TOTAL_BYTES:
        raise _error("size_overflow")
    for item in artifacts:
        total += _hash_file(root / item["file_name"], item["size"], item["sha256"])
        if total > MAX_TOTAL_BYTES:
            raise _error("size_overflow")
    return artifacts, epoch, total


def verify_assets(root: Path, reviewed_sha: str, version: str) -> tuple[int, int]:
    _artifacts, _epoch, total = _candidate_inventory(root, reviewed_sha, version)
    return 5, total


def _git_environment() -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }


def _run_git(repository: Path, arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repository),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=120,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("source_invalid") from exc
    if result.returncode != 0 or len(result.stdout) > MAX_JSON_STRING:
        raise _error("source_invalid")
    return result.stdout


def _is_excluded_public_path(name: str) -> bool:
    lowered_parts = tuple(part.casefold() for part in PurePosixPath(name).parts)
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in lowered_parts):
        return True
    if any(part == ".env" or part.startswith(".env.") for part in lowered_parts):
        return True
    return bool(lowered_parts and lowered_parts[-1] in _EXCLUDED_STATE_FILES)


def _read_stream(stream: BinaryIO, expected_size: int, *, code: str) -> bytes:
    if expected_size < 0 or expected_size > MAX_FILE_BYTES:
        raise _error("size_overflow")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, expected_size - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > expected_size or total > MAX_FILE_BYTES:
            raise _error(code)
        chunks.append(chunk)
    if total != expected_size:
        raise _error(code)
    return b"".join(chunks)


def _trusted_git_entries(
    repository: Path, reviewed_sha: str, version: str
) -> tuple[tuple[SourceEntry, ...], int]:
    _check_directory(repository, code="source_invalid")
    head = _run_git(repository, ["rev-parse", "--verify", "HEAD"]).decode(
        "ascii", errors="ignore"
    ).strip()
    if head != reviewed_sha:
        raise _error("source_mismatch")
    object_type = _run_git(repository, ["cat-file", "-t", reviewed_sha]).decode(
        "ascii", errors="ignore"
    ).strip()
    if object_type != "commit":
        raise _error("source_invalid")
    epoch_text = _run_git(repository, ["show", "-s", "--format=%ct", reviewed_sha]).decode(
        "ascii", errors="ignore"
    ).strip()
    if not epoch_text.isdecimal():
        raise _error("source_invalid")
    epoch = int(epoch_text)
    if not MIN_SOURCE_DATE_EPOCH <= epoch <= MAX_SOURCE_DATE_EPOCH:
        raise _error("source_invalid")

    with tempfile.TemporaryDirectory(prefix="mercury-trusted-git-") as temporary:
        archive_path = Path(temporary) / "reviewed.tar"
        _run_git(
            repository,
            ["archive", "--format=tar", f"--output={archive_path}", reviewed_sha],
        )
        try:
            archive_stat = os.lstat(archive_path)
        except OSError as exc:
            raise _error("source_invalid") from exc
        _check_regular_metadata(archive_path, archive_stat)
        if archive_stat.st_size > MAX_TRUSTED_SOURCE_BYTES:
            raise _error("size_overflow")
        try:
            with tarfile.open(archive_path, mode="r:") as archive:
                members = archive.getmembers()
                if len(members) > MAX_ARCHIVE_MEMBERS:
                    raise _error("count_overflow")
                member_names = [
                    member.name.rstrip("/") if member.isdir() else member.name
                    for member in members
                ]
                _check_canonical_names(member_names, code="source_invalid")
                entries: list[SourceEntry] = []
                total = 0
                for member in members:
                    if member.isdir():
                        continue
                    if not member.isfile():
                        raise _error("special_file")
                    total += member.size
                    if total > MAX_TRUSTED_SOURCE_BYTES:
                        raise _error("size_overflow")
                    stream = archive.extractfile(member)
                    if stream is None:
                        raise _error("source_invalid")
                    data = _read_stream(stream, member.size, code="source_invalid")
                    if _is_excluded_public_path(member.name):
                        continue
                    entries.append(
                        SourceEntry(
                            name=member.name,
                            mode=0o755 if member.mode & 0o111 else 0o644,
                            data=data,
                        )
                    )
        except ValidationError:
            raise
        except (OSError, tarfile.TarError) as exc:
            raise _error("source_invalid") from exc

    ordered = tuple(sorted(entries, key=lambda entry: entry.name))
    _check_canonical_names((entry.name for entry in ordered), code="source_invalid")
    _require_source_version(ordered, version)
    if not ordered:
        raise _error("source_invalid")
    return ordered, epoch


def _require_source_version(entries: Iterable[SourceEntry], version: str) -> None:
    try:
        pyproject = next(entry for entry in entries if entry.name == "pyproject.toml")
        payload = tomllib.loads(pyproject.data.decode("utf-8"))
        project = payload["project"]
        actual = project["version"] if isinstance(project, dict) else None
    except (StopIteration, UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise _error("source_invalid") from exc
    if actual != version:
        raise _error("manifest_mismatch")


def materialize_trusted_source(
    repository: Path,
    destination: Path,
    reviewed_sha: str,
    version: str,
) -> tuple[int, int, int]:
    entries, epoch = _trusted_git_entries(repository, reviewed_sha, version)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise _error("root_invalid") from exc
    else:
        raise _error("root_invalid")
    try:
        destination.mkdir(mode=0o700)
        for entry in entries:
            path = destination.joinpath(*entry.name.split("/"))
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(entry.data)
            os.chmod(path, entry.mode)
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return len(entries), sum(len(entry.data) for entry in entries), epoch


def _read_source_tree(root: Path) -> tuple[SourceEntry, ...]:
    _check_directory(root, code="source_invalid")
    entries: list[SourceEntry] = []
    total = 0

    def visit(directory: Path, relative: PurePosixPath | None = None) -> None:
        nonlocal total
        try:
            children = sorted(os.scandir(directory), key=lambda child: child.name)
        except OSError as exc:
            raise _error("source_invalid") from exc
        for child in children:
            path = Path(child.path)
            name = child.name if relative is None else f"{relative.as_posix()}/{child.name}"
            _check_canonical_names((name,), code="source_invalid")
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise _error("source_invalid") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise _error("symlink")
            if stat.S_ISDIR(metadata.st_mode):
                if metadata.st_mode & 0o022:
                    raise _error("permissions")
                visit(path, PurePosixPath(name))
                continue
            _check_regular_metadata(path, metadata)
            if _is_excluded_public_path(name):
                raise _error("source_mismatch")
            data = _read_bytes(path, metadata, MAX_FILE_BYTES)
            total += len(data)
            if total > MAX_TRUSTED_SOURCE_BYTES or len(entries) >= MAX_ARCHIVE_MEMBERS:
                raise _error("size_overflow")
            entries.append(
                SourceEntry(
                    name=name,
                    mode=0o755 if metadata.st_mode & 0o111 else 0o644,
                    data=data,
                )
            )

    visit(root)
    ordered = tuple(sorted(entries, key=lambda entry: entry.name))
    _check_canonical_names((entry.name for entry in ordered), code="source_invalid")
    return ordered


def _verified_source_entries(
    repository: Path,
    canonical_source: Path,
    reviewed_sha: str,
    version: str,
) -> tuple[tuple[SourceEntry, ...], int]:
    trusted, epoch = _trusted_git_entries(repository, reviewed_sha, version)
    materialized = _read_source_tree(canonical_source)
    if trusted != materialized:
        raise _error("source_mismatch")
    return trusted, epoch


def _archive_file_entries(root: Path) -> tuple[dict[str, os.stat_result], int]:
    _check_directory(root)
    try:
        children = sorted(os.scandir(root), key=lambda child: child.name)
    except OSError as exc:
        raise _error("io_error") from exc
    stats: dict[str, os.stat_result] = {}
    total = 0
    for child in children:
        try:
            metadata = child.stat(follow_symlinks=False)
        except OSError as exc:
            raise _error("io_error") from exc
        _check_regular_metadata(Path(child.path), metadata)
        total += metadata.st_size
        if total > MAX_TOTAL_BYTES:
            raise _error("size_overflow")
        stats[child.name] = metadata
    return stats, total


def _zip_entries(path: Path, metadata: os.stat_result) -> tuple[SourceEntry, ...]:
    _check_regular_metadata(path, metadata)
    try:
        with zipfile.ZipFile(path) as archive:
            members = [member for member in archive.infolist() if not member.is_dir()]
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise _error("count_overflow")
            _check_canonical_names((member.filename for member in members), code="archive_invalid")
            entries: list[SourceEntry] = []
            total = 0
            for member in members:
                unix_mode = (member.external_attr >> 16) & 0xFFFF
                file_type = stat.S_IFMT(unix_mode)
                if file_type == stat.S_IFLNK:
                    raise _error("symlink")
                if file_type not in {0, stat.S_IFREG}:
                    raise _error("special_file")
                if member.flag_bits & 0x1:
                    raise _error("archive_invalid")
                total += member.file_size
                if total > MAX_TRUSTED_SOURCE_BYTES:
                    raise _error("size_overflow")
                with archive.open(member, mode="r") as stream:
                    data = _read_stream(stream, member.file_size, code="archive_invalid")
                entries.append(SourceEntry(member.filename, 0o644, data))
    except ValidationError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise _error("archive_invalid") from exc
    return tuple(sorted(entries, key=lambda entry: entry.name))


def _tar_entries(path: Path, metadata: os.stat_result) -> tuple[SourceEntry, ...]:
    _check_regular_metadata(path, metadata)
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            all_members = archive.getmembers()
            if len(all_members) > MAX_ARCHIVE_MEMBERS:
                raise _error("count_overflow")
            names = [
                member.name.rstrip("/") if member.isdir() else member.name
                for member in all_members
            ]
            _check_canonical_names(names, code="archive_invalid")
            entries: list[SourceEntry] = []
            total = 0
            for member in all_members:
                if member.isdir():
                    continue
                if not member.isfile():
                    raise _error("special_file")
                total += member.size
                if total > MAX_TRUSTED_SOURCE_BYTES:
                    raise _error("size_overflow")
                stream = archive.extractfile(member)
                if stream is None:
                    raise _error("archive_invalid")
                data = _read_stream(stream, member.size, code="archive_invalid")
                entries.append(SourceEntry(member.name, 0o644, data))
    except ValidationError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise _error("archive_invalid") from exc
    return tuple(sorted(entries, key=lambda entry: entry.name))


def _ordered_entries(entries: Iterable[SourceEntry]) -> tuple[SourceEntry, ...]:
    ordered = tuple(sorted(entries, key=lambda entry: entry.name))
    _check_canonical_names((entry.name for entry in ordered), code="archive_invalid")
    return ordered


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


def _write_zip_archive(entries: Iterable[SourceEntry], destination: Path, epoch: int) -> None:
    try:
        with zipfile.ZipFile(
            destination,
            mode="x",
            compression=zipfile.ZIP_STORED,
            strict_timestamps=True,
        ) as archive:
            for entry in _ordered_entries(entries):
                info = zipfile.ZipInfo(entry.name, date_time=_zip_datetime(epoch))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_STORED
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                info.flag_bits |= 0x800
                archive.writestr(info, entry.data, compress_type=zipfile.ZIP_STORED)
        os.chmod(destination, 0o600)
    except ValidationError:
        raise
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise _error("archive_invalid") from exc


class _DeterministicGzipWriter:
    def __init__(self, raw: BinaryIO, epoch: int) -> None:
        if epoch < 0 or epoch > 0xFFFFFFFF:
            raise _error("source_invalid")
        self._raw = raw
        self._pending = bytearray()
        self._crc = 0xFFFFFFFF
        self._uncompressed_size = 0
        self._closed = False
        self._raw.write(b"\x1f\x8b\x08\x00" + struct.pack("<I", epoch) + b"\x00\xff")

    def __enter__(self) -> _DeterministicGzipWriter:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def write(self, data: bytes | bytearray | memoryview) -> int:
        if self._closed:
            raise ValueError("closed deterministic gzip writer")
        value = bytes(data)
        self._crc = _crc32_update(self._crc, value)
        self._uncompressed_size += len(value)
        self._pending.extend(value)
        while len(self._pending) > _STORED_DEFLATE_BLOCK_BYTES:
            block = bytes(self._pending[:_STORED_DEFLATE_BLOCK_BYTES])
            del self._pending[:_STORED_DEFLATE_BLOCK_BYTES]
            _write_stored_deflate_block(self._raw, block, final=False)
        return len(value)

    def tell(self) -> int:
        return self._uncompressed_size

    def flush(self) -> None:
        self._raw.flush()

    def close(self) -> None:
        if self._closed:
            return
        _write_stored_deflate_block(self._raw, bytes(self._pending), final=True)
        self._raw.write(
            struct.pack(
                "<II",
                self._crc ^ 0xFFFFFFFF,
                self._uncompressed_size & 0xFFFFFFFF,
            )
        )
        self._raw.flush()
        self._closed = True


def _write_stored_deflate_block(raw: BinaryIO, data: bytes, *, final: bool) -> None:
    if len(data) > _STORED_DEFLATE_BLOCK_BYTES:
        raise ValueError("stored deflate block exceeds format limit")
    raw.write(bytes((1 if final else 0,)))
    raw.write(struct.pack("<HH", len(data), (~len(data)) & 0xFFFF))
    raw.write(data)


def _crc32_update(crc: int, data: bytes) -> int:
    value = crc
    for byte in data:
        value = _CRC32_TABLE[(value ^ byte) & 0xFF] ^ (value >> 8)
    return value


def _build_crc32_table() -> tuple[int, ...]:
    values: list[int] = []
    for index in range(256):
        value = index
        for _ in range(8):
            value = (value >> 1) ^ (0xEDB88320 if value & 1 else 0)
        values.append(value)
    return tuple(values)


_CRC32_TABLE = _build_crc32_table()


def _write_tar_gz_archive(entries: Iterable[SourceEntry], destination: Path, epoch: int) -> None:
    try:
        with (
            destination.open("xb") as raw,
            _DeterministicGzipWriter(raw, epoch) as compressed,
            tarfile.open(fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT) as archive,
        ):
            for entry in _ordered_entries(entries):
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
        os.chmod(destination, 0o600)
    except ValidationError:
        raise
    except (OSError, ValueError, tarfile.TarError) as exc:
        raise _error("archive_invalid") from exc


def reproduce_release_assets(
    canonical_source: Path,
    reproduced_distributions: Path,
    output: Path,
    epoch: int,
    version: str,
) -> tuple[int, int]:
    if not MIN_SOURCE_DATE_EPOCH <= epoch <= MAX_SOURCE_DATE_EPOCH:
        raise _error("source_invalid")
    source_entries = _read_source_tree(canonical_source)
    _require_source_version(source_entries, version)
    distribution_stats, _distribution_total = _archive_file_entries(reproduced_distributions)
    wheel_names = [
        name
        for name in distribution_stats
        if re.fullmatch(rf"mercury_tools-{re.escape(version)}-[A-Za-z0-9_.-]+\.whl", name)
    ]
    sdist_name = f"mercury_tools-{version}.tar.gz"
    if (
        len(distribution_stats) != 2
        or len(wheel_names) != 1
        or sdist_name not in distribution_stats
    ):
        raise _error("inventory_invalid")
    wheel_name = wheel_names[0]
    wheel_entries = _zip_entries(
        reproduced_distributions / wheel_name,
        distribution_stats[wheel_name],
    )
    sdist_entries = _tar_entries(
        reproduced_distributions / sdist_name,
        distribution_stats[sdist_name],
    )
    plugin_prefix = "plugins/mercury-finance/"
    plugin_entries = tuple(
        SourceEntry(
            name=f"mercury-finance/{entry.name.removeprefix(plugin_prefix)}",
            mode=entry.mode,
            data=entry.data,
        )
        for entry in source_entries
        if entry.name.startswith(plugin_prefix)
    )
    if not plugin_entries:
        raise _error("source_invalid")
    source_prefix = f"mercury-tools-{version}"
    archive_source_entries = tuple(
        SourceEntry(
            name=f"{source_prefix}/{entry.name}",
            mode=entry.mode,
            data=entry.data,
        )
        for entry in source_entries
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.lstat()
    except FileNotFoundError:
        output.mkdir(mode=0o700)
    except OSError as exc:
        raise _error("root_invalid") from exc
    else:
        _check_directory(output)
        try:
            if any(os.scandir(output)):
                raise _error("root_invalid")
        except OSError as exc:
            raise _error("root_invalid") from exc
    try:
        _write_zip_archive(wheel_entries, output / wheel_name, epoch)
        _write_tar_gz_archive(sdist_entries, output / sdist_name, epoch)
        _write_zip_archive(
            plugin_entries,
            output / f"mercury-finance-plugin-{version}.zip",
            epoch,
        )
        _write_tar_gz_archive(
            archive_source_entries,
            output / f"mercury-tools-{version}-source.tar.gz",
            epoch,
        )
        stats, total = _archive_file_entries(output)
        if len(stats) != 4:
            raise _error("inventory_invalid")
        return 4, total
    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise


def _compare_files(candidate: Path, trusted: Path, expected_sha256: str) -> None:
    try:
        candidate_fd = os.open(candidate, _open_flags())
        trusted_fd = os.open(trusted, _open_flags())
    except OSError as exc:
        raise _error("io_error") from exc
    try:
        candidate_stat = os.fstat(candidate_fd)
        trusted_stat = os.fstat(trusted_fd)
        _check_regular_metadata(candidate, candidate_stat)
        _check_regular_metadata(trusted, trusted_stat)
        if candidate_stat.st_size != trusted_stat.st_size:
            raise _error("reproduction_mismatch")
        trusted_digest = hashlib.sha256()
        while True:
            candidate_chunk = os.read(candidate_fd, 1024 * 1024)
            trusted_chunk = os.read(trusted_fd, 1024 * 1024)
            if candidate_chunk != trusted_chunk:
                raise _error("reproduction_mismatch")
            if not trusted_chunk:
                break
            trusted_digest.update(trusted_chunk)
        if trusted_digest.hexdigest() != expected_sha256:
            raise _error("reproduction_mismatch")
    except ValidationError:
        raise
    except OSError as exc:
        raise _error("io_error") from exc
    finally:
        os.close(candidate_fd)
        os.close(trusted_fd)


def verify_reproduction(
    artifact_root: Path,
    source_repository: Path,
    canonical_source: Path,
    reproduced_distributions: Path,
    reviewed_sha: str,
    version: str,
) -> tuple[int, int]:
    artifacts, manifest_epoch, total = _candidate_inventory(
        artifact_root, reviewed_sha, version
    )
    _entries, git_epoch = _verified_source_entries(
        source_repository,
        canonical_source,
        reviewed_sha,
        version,
    )
    if manifest_epoch != git_epoch:
        raise _error("manifest_mismatch")
    with tempfile.TemporaryDirectory(prefix="mercury-trusted-assets-") as temporary:
        trusted_root = Path(temporary) / "artifacts"
        reproduce_release_assets(
            canonical_source,
            reproduced_distributions,
            trusted_root,
            git_epoch,
            version,
        )
        trusted_stats, _trusted_total = _archive_file_entries(trusted_root)
        expected_names = {item["file_name"] for item in artifacts}
        if set(trusted_stats) != expected_names:
            raise _error("reproduction_mismatch")
        for item in artifacts:
            trusted_path = trusted_root / item["file_name"]
            trusted_sha256 = hashlib.sha256(
                _read_bytes(trusted_path, trusted_stats[item["file_name"]], MAX_FILE_BYTES)
            ).hexdigest()
            if trusted_sha256 != item["sha256"]:
                raise _error("reproduction_mismatch")
            _compare_files(artifact_root / item["file_name"], trusted_path, trusted_sha256)
    return 5, total


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    values = list(sys.argv[1:] if argv is None else argv)
    if len(values) == 7 and values[0] == "verify" and not any(
        value.startswith("--") for value in values[1:]
    ):
        return argparse.Namespace(
            command="verify",
            artifact_root=values[1],
            source_repository=values[2],
            canonical_source=values[3],
            reproduced_distributions=values[4],
            reviewed_sha=values[5],
            version=values[6],
        )
    parser = _ArgumentParser(add_help=False, exit_on_error=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare-source", add_help=False, exit_on_error=False)
    prepare.add_argument("--source-repository", required=True)
    prepare.add_argument("--canonical-source", required=True)
    prepare.add_argument("--reviewed-sha", required=True)
    prepare.add_argument("--version", required=True)
    verify = subparsers.add_parser("verify", add_help=False, exit_on_error=False)
    verify.add_argument("--artifact-root", required=True)
    verify.add_argument("--source-repository", required=True)
    verify.add_argument("--canonical-source", required=True)
    verify.add_argument("--reproduced-distributions", required=True)
    verify.add_argument("--reviewed-sha", required=True)
    verify.add_argument("--version", required=True)
    try:
        return parser.parse_args(values)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise _error("arguments") from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        if _COMMIT.fullmatch(args.reviewed_sha) is None:
            raise _error("commit_invalid")
        if _VERSION.fullmatch(args.version) is None:
            raise _error("version_invalid")
        if args.command == "prepare-source":
            files, total, epoch = materialize_trusted_source(
                Path(args.source_repository),
                Path(args.canonical_source),
                args.reviewed_sha,
                args.version,
            )
            _emit(
                {
                    "status": "ok",
                    "files": files,
                    "bytes": total,
                    "source_date_epoch": epoch,
                }
            )
            return 0
        files, total = verify_reproduction(
            Path(args.artifact_root),
            Path(args.source_repository),
            Path(args.canonical_source),
            Path(args.reproduced_distributions),
            args.reviewed_sha,
            args.version,
        )
    except ValidationError as exc:
        _emit({"status": "error", "code": exc.code, "files": 0, "bytes": 0})
        return 1
    except Exception:
        _emit({"status": "error", "code": "io_error", "files": 0, "bytes": 0})
        return 1
    _emit({"status": "ok", "files": files, "bytes": total})
    return 0


if __name__ == "__main__":
    sys.exit(main())
