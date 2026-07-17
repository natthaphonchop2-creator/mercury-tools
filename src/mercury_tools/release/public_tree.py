"""Canonical, bounded public source tree construction for Mercury releases."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import tarfile
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MEMBER_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_PATH_BYTES = 4096
MAX_PATH_DEPTH = 64

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
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class PublicTreeError(RuntimeError):
    """A constant-code canonical public-tree failure."""


@dataclass(frozen=True, slots=True)
class PublicTreeEntry:
    path: str
    mode: int
    sha256: str
    content: bytes = field(repr=False)

    def public_identity(self) -> dict[str, str | int]:
        return {"mode": self.mode, "path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class PublicTreeSnapshot:
    entries: tuple[PublicTreeEntry, ...]
    digest: str

    def public_inventory(self) -> tuple[dict[str, str | int], ...]:
        return tuple(entry.public_identity() for entry in self.entries)

    def as_dict(self) -> dict[str, object]:
        return {
            "digest": self.digest,
            "entries": list(self.public_inventory()),
            "schema_version": 1,
        }


def public_tree_digest(entries: tuple[PublicTreeEntry, ...]) -> str:
    """Compute the PublicTreeV1 digest for an exact canonical entry inventory."""

    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    collision_keys: set[str] = set()
    for entry in ordered:
        _canonical_path(entry.path)
        collision_key = entry.path.casefold()
        if (
            collision_key in collision_keys
            or entry.mode not in {0o644, 0o755}
            or entry.sha256 != hashlib.sha256(entry.content).hexdigest()
        ):
            raise PublicTreeError("public_tree_snapshot_invalid")
        collision_keys.add(collision_key)
    records = b"".join(
        f"{entry.mode:o} {entry.path}\0{entry.sha256}".encode()
        for entry in ordered
    )
    return hashlib.sha256(records).hexdigest()


def is_excluded_public_path(name: str) -> bool:
    """Return whether a canonical source path is excluded from public output."""

    path = PurePosixPath(name)
    lowered_parts = tuple(part.casefold() for part in path.parts)
    if any(part in _EXCLUDED_DIRECTORY_NAMES for part in lowered_parts):
        return True
    if any(part == ".env" or part.startswith(".env.") for part in lowered_parts):
        return True
    return bool(lowered_parts and lowered_parts[-1] in _EXCLUDED_STATE_FILES)


def build_public_tree(archive_bytes: bytes) -> PublicTreeSnapshot:
    """Parse exact ``git archive`` bytes into the PublicTreeV1 representation."""

    if (
        not isinstance(archive_bytes, bytes)
        or not archive_bytes
        or len(archive_bytes) > MAX_ARCHIVE_BYTES
    ):
        raise PublicTreeError("public_tree_archive_too_large")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            git_comment = _git_pax_comment(archive.pax_headers)
            members = archive.getmembers()
            if len(members) > MAX_ARCHIVE_MEMBERS:
                raise PublicTreeError("public_tree_archive_too_large")
            entries: list[PublicTreeEntry] = []
            collision_keys: set[str] = set()
            total_bytes = 0
            for member in members:
                name = _canonical_member_name(member, git_comment=git_comment)
                collision_key = name.casefold()
                if collision_key in collision_keys:
                    raise PublicTreeError("public_tree_archive_invalid")
                collision_keys.add(collision_key)
                if member.isdir():
                    continue
                if not member.isfile():
                    raise PublicTreeError("public_tree_archive_invalid")
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    raise PublicTreeError("public_tree_archive_too_large")
                total_bytes += member.size
                if total_bytes > MAX_TOTAL_BYTES:
                    raise PublicTreeError("public_tree_archive_too_large")
                if is_excluded_public_path(name):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise PublicTreeError("public_tree_archive_invalid")
                content = stream.read(MAX_MEMBER_BYTES + 1)
                if len(content) != member.size or len(content) > MAX_MEMBER_BYTES:
                    raise PublicTreeError("public_tree_archive_invalid")
                entries.append(
                    PublicTreeEntry(
                        path=name,
                        mode=0o755 if member.mode & 0o111 else 0o644,
                        sha256=hashlib.sha256(content).hexdigest(),
                        content=content,
                    )
                )
    except PublicTreeError:
        raise
    except (OSError, tarfile.TarError, UnicodeError, ValueError) as exc:
        raise PublicTreeError("public_tree_archive_invalid") from exc

    ordered = tuple(sorted(entries, key=lambda entry: entry.path))
    return PublicTreeSnapshot(entries=ordered, digest=public_tree_digest(ordered))


def write_public_tree(snapshot: PublicTreeSnapshot, destination: Path) -> None:
    """Write a canonical tree through a same-parent temporary directory."""

    if not isinstance(snapshot, PublicTreeSnapshot):
        raise PublicTreeError("public_tree_snapshot_invalid")
    destination = destination.expanduser()
    parent = destination.parent.resolve(strict=True)
    if destination.exists() or destination.is_symlink():
        raise PublicTreeError("public_tree_destination_exists")
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.public-tree-", dir=parent))
    published = False
    try:
        for entry in snapshot.entries:
            target = temporary.joinpath(*entry.path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(entry.content)
            os.chmod(target, entry.mode)
        if destination.exists() or destination.is_symlink():
            raise PublicTreeError("public_tree_destination_exists")
        temporary.rename(destination)
        published = True
    except PublicTreeError:
        raise
    except OSError as exc:
        raise PublicTreeError("public_tree_write_failed") from exc
    finally:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)


def _canonical_member_name(member: tarfile.TarInfo, *, git_comment: str | None) -> str:
    expected_pax_headers = {"comment": git_comment} if git_comment is not None else {}
    if member.pax_headers != expected_pax_headers:
        raise PublicTreeError("public_tree_archive_invalid")
    name = member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name
    return _canonical_path(name)


def _git_pax_comment(headers: dict[str, str]) -> str | None:
    if not headers:
        return None
    comment = headers.get("comment")
    if set(headers) != {"comment"} or not isinstance(comment, str):
        raise PublicTreeError("public_tree_archive_invalid")
    if _COMMIT_PATTERN.fullmatch(comment) is None:
        raise PublicTreeError("public_tree_archive_invalid")
    return comment


def _canonical_path(name: str) -> str:
    if not name or "\0" in name or "\\" in name:
        raise PublicTreeError("public_tree_archive_invalid")
    normalized = unicodedata.normalize("NFC", name)
    path = PurePosixPath(name)
    parts = name.split("/")
    if (
        normalized != name
        or path.is_absolute()
        or path.as_posix() != name
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) > MAX_PATH_DEPTH
        or len(name.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise PublicTreeError("public_tree_archive_invalid")
    return name
