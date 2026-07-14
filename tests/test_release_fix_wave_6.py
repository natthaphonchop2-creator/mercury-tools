from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_SCANNER_VERSION,
    GhApiHostedClient,
    HostedInspection,
    HostedObjectBoundary,
    HostedReceipt,
    scan_hosted_surface,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy
from mercury_tools.release.scanner import CommandResult, SubprocessCommandRunner

_MARKER = b"ghp_W6a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5"
_WIKI_SURFACE = "github_packages_pages_wiki"
_BOUNDARY_BLOCKER = f"hosted_archive_boundary_invalid:{_WIKI_SURFACE}"


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=dict(PINNED_SCANNER_VERSIONS))
    return policy.model_copy(update=updates)


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _zip_bytes(data: bytes = b"safe") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_zip_info("payload.bin"), data)
    return buffer.getvalue()


def _tar_header(name: str, size: int, tar_format: int) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = size
    return info.tobuf(format=tar_format)


def _short_tar_magic(magic: bytes) -> bytes:
    candidate = bytearray(263)
    candidate[257:263] = magic
    return bytes(candidate)


def _v7_header() -> bytes:
    candidate = bytearray(_tar_header("safe.bin", 0, tarfile.USTAR_FORMAT))
    candidate[257:265] = b"\0" * 8
    candidate[148:156] = b" " * 8
    checksum = sum(candidate)
    candidate[148:156] = f"{checksum:06o}\0 ".encode("ascii")
    payload = bytes(candidate)
    assert scanner_module._tar_checksum_is_valid(payload)
    return payload


_ARCHIVE_CANDIDATES = (
    ("zip-local", b"PK\x03\x04", "zip"),
    ("zip-central", b"PK\x01\x02", "zip"),
    ("zip-eocd", b"PK\x05\x06", "zip"),
    ("zip-descriptor", b"PK\x07\x08", "zip"),
    ("zip64-eocd", b"PK\x06\x06", "zip"),
    ("zip64-locator", b"PK\x06\x07", "zip"),
    ("zip-digital-signature", b"PK\x05\x05", "zip"),
    ("zip-archive-extra", b"PK\x08\x06", "zip"),
    ("gzip", b"\x1f\x8b", "gzip"),
    ("bzip2", b"BZh", "bz2"),
    ("xz", b"\xfd7zXZ\x00", "xz"),
    ("7z", b"7z\xbc\xaf'\x1c", "opaque"),
    ("rar", b"Rar!\x1a\x07", "opaque"),
    ("lz4", b"\x04\x22\x4d\x18", "opaque"),
    ("compress", b"\x1f\x9d", "opaque"),
    ("zstd", b"\x28\xb5\x2f\xfd", "opaque"),
    ("ustar-short", _short_tar_magic(b"ustar\0"), "tar"),
    ("gnu-tar-short", _short_tar_magic(b"ustar "), "tar"),
    ("v7-checksum", _v7_header(), "tar"),
)


class _InspectionClient:
    def __init__(self, inspection: HostedInspection) -> None:
        self._inspection = inspection

    def inspect(
        self,
        _surface: str,
        _policy: SecretScanPolicy,
    ) -> HostedInspection:
        return self._inspection


def _scan_marketplace(payload: bytes):
    receipt = HostedReceipt(
        name="marketplace_snapshot_download",
        chunks=(payload,),
        object_boundaries=(HostedObjectBoundary(1, len(payload)),),
        complete=True,
        page_count=1,
        record_count=1,
        request_count=1,
        status_codes=(200,),
    )
    return scan_hosted_surface(
        "marketplace_snapshot",
        _InspectionClient(HostedInspection((receipt,), HOSTED_SCANNER_VERSION)),
        _policy(),
    )


def test_hosted_pk_central_candidate_with_compressed_marker_blocks() -> None:
    payload = b"PK\x01\x02" + gzip.compress(_MARKER, mtime=0)
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert "hosted_archive_read_failed:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_short_checksum_valid_ustar_member_without_padding_or_eof_blocks() -> None:
    compressed = gzip.compress(_MARKER, mtime=0)
    header = _tar_header("hidden.gz", len(compressed), tarfile.USTAR_FORMAT)
    payload = header + compressed
    assert len(payload) < 1024
    assert scanner_module._tar_checksum_is_valid(header)
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert "hosted_archive_read_failed:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


@pytest.mark.parametrize(
    ("_name", "candidate", "expected_format"),
    _ARCHIVE_CANDIDATES,
    ids=[name for name, _candidate, _format in _ARCHIVE_CANDIDATES],
)
def test_every_offset_zero_archive_candidate_uses_the_shared_parser(
    monkeypatch: pytest.MonkeyPatch,
    _name: str,
    candidate: bytes,
    expected_format: str,
) -> None:
    predicate = getattr(scanner_module, "_is_archive_candidate", None)
    assert predicate is not None
    assert predicate(candidate) is True
    assert scanner_module._detect_archive_format(candidate) == expected_format
    parsed: list[bytes] = []

    def capture(
        data: bytes,
        _surface: str,
        _policy: SecretScanPolicy,
        _budget: object,
        *,
        expected_formats: tuple[str, ...] | None = None,
    ) -> tuple[list[object], list[str]]:
        assert expected_formats is None
        parsed.append(data)
        return [], ["candidate_parser_invoked"]

    monkeypatch.setattr(hosted_module, "_scan_complete_hosted_archive", capture)

    findings, blockers = hosted_module._scan_hosted_archive(
        candidate,
        "marketplace_snapshot",
        _policy(),
        hosted_module._HostedArchiveBudget(1024 * 1024),
    )

    assert findings == []
    assert blockers == ["candidate_parser_invoked"]
    assert parsed == [candidate]


def _empty_archive_receipt(name: str) -> HostedReceipt:
    return HostedReceipt(
        name=name,
        object_boundaries=(),
        complete=True,
        page_count=1,
        record_count=0,
        request_count=0,
        parent_record_count=0,
        exit_codes=(0,),
    )


def _default_wiki_query(*, present: bool = True) -> HostedReceipt:
    return HostedReceipt(
        name="github_wiki_query",
        chunks=(b"wiki ref inventory",),
        complete=True,
        page_count=1,
        record_count=1 if present else 0,
        request_count=1,
        exit_codes=(0,),
    )


def _wiki_inspection(
    download: HostedReceipt,
    *,
    wiki_query: HostedReceipt | None = None,
) -> HostedInspection:
    return HostedInspection(
        receipts=(
            HostedReceipt(
                name="github_packages_query",
                chunks=(b"[]",),
                complete=True,
                page_count=1,
                record_count=0,
                request_count=1,
                exit_codes=(0,),
            ),
            HostedReceipt(
                name="github_package_versions_query",
                chunks=(b"[]",),
                complete=True,
                page_count=1,
                record_count=0,
                request_count=0,
                parent_record_count=0,
                exit_codes=(0,),
            ),
            _empty_archive_receipt("github_package_versions_content"),
            HostedReceipt(
                name="github_pages_query",
                chunks=(b"pages absent",),
                complete=True,
                page_count=1,
                record_count=0,
                request_count=1,
                exit_codes=(0,),
            ),
            _empty_archive_receipt("github_pages_download"),
            wiki_query or _default_wiki_query(),
            download,
        ),
        scanner_version=HOSTED_SCANNER_VERSION,
    )


def _scan_wiki(
    download: HostedReceipt,
    *,
    wiki_query: HostedReceipt | None = None,
):
    return scan_hosted_surface(
        _WIKI_SURFACE,
        _InspectionClient(_wiki_inspection(download, wiki_query=wiki_query)),
        _policy(),
    )


def test_split_zip_across_wiki_transport_chunks_blocks() -> None:
    payload = _zip_bytes(_MARKER)
    chunks = (payload[:1], payload[1:])
    assert _MARKER not in payload
    download = HostedReceipt(
        name="github_wiki_download",
        chunks=chunks,
        object_boundaries=(
            HostedObjectBoundary(1, len(chunks[0])),
            HostedObjectBoundary(1, len(chunks[1])),
        ),
        complete=True,
        page_count=1,
        record_count=1,
        request_count=1,
        parent_record_count=1,
        exit_codes=(0,),
    )

    result = _scan_wiki(download)

    assert _BOUNDARY_BLOCKER in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def _typed_boundary(
    chunks: tuple[bytes, ...],
    *,
    object_type: str,
    object_id: str,
    digest: str | None = None,
) -> HostedObjectBoundary:
    data = b"".join(chunks)
    return HostedObjectBoundary(
        chunk_count=len(chunks),
        byte_count=len(data),
        object_type=object_type,
        object_id=object_id,
        content_sha256=digest or hashlib.sha256(data).hexdigest(),
    )


def _inventory_manifest(
    payload_entries: list[dict[str, object]],
    *,
    reachable_object_count: int,
    reachable_blob_count: int,
) -> bytes:
    return json.dumps(
        {
            "schema_version": 1,
            "remote_ref_count": 1,
            "remote_ref_digest": hashlib.sha256(b"safe refs").hexdigest(),
            "reachable_object_count": reachable_object_count,
            "reachable_blob_count": reachable_blob_count,
            "payload_object_count": len(payload_entries),
            "payload_objects": payload_entries,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _strict_wiki_declaration() -> tuple[
    tuple[bytes, ...], tuple[HostedObjectBoundary, ...]
]:
    command = b"safe clone command output"
    blob = b"safe reachable wiki payload"
    blob_oid = hashlib.sha1(  # noqa: S324 - Git object identity is SHA-1 by format.
        f"blob {len(blob)}\0".encode("ascii") + blob,
        usedforsecurity=False,
    ).hexdigest()
    blob_entry = {
        "object_type": "wiki_reachable_blob",
        "object_id": f"git/blob/{blob_oid}",
        "byte_count": len(blob),
        "content_sha256": hashlib.sha256(blob).hexdigest(),
    }
    inventory = _inventory_manifest(
        [blob_entry],
        reachable_object_count=3,
        reachable_blob_count=1,
    )
    chunks = (command, inventory, blob)
    boundaries = (
        _typed_boundary(
            (command,),
            object_type="wiki_command_output",
            object_id="wiki/command/clone",
        ),
        _typed_boundary(
            (inventory,),
            object_type="wiki_mirror_inventory",
            object_id="wiki/mirror-inventory/v1",
        ),
        _typed_boundary(
            (blob,),
            object_type="wiki_reachable_blob",
            object_id=f"git/blob/{blob_oid}",
        ),
    )
    return chunks, boundaries


def _declared_wiki_download(
    chunks: tuple[bytes, ...],
    boundaries: tuple[HostedObjectBoundary, ...],
    *,
    expected_object_count: int,
    complete: bool = True,
) -> HostedReceipt:
    return HostedReceipt(
        name="github_wiki_download",
        chunks=chunks,
        object_boundaries=boundaries,
        expected_object_count=expected_object_count,
        complete=complete,
        page_count=1,
        record_count=1,
        request_count=1,
        parent_record_count=1,
        exit_codes=(0,),
    )


def test_exact_typed_wiki_logical_objects_are_accepted() -> None:
    chunks, boundaries = _strict_wiki_declaration()

    result = _scan_wiki(
        _declared_wiki_download(
            chunks,
            boundaries,
            expected_object_count=len(boundaries),
        )
    )

    assert _BOUNDARY_BLOCKER not in result.blockers


@pytest.mark.parametrize(
    "failure",
    ["count", "duplicate-identity", "digest", "missing-identity", "inventory"],
)
def test_false_wiki_logical_object_declarations_block(failure: str) -> None:
    chunks, boundaries = _strict_wiki_declaration()
    expected_count = len(boundaries)
    material = list(boundaries)
    if failure == "count":
        expected_count -= 1
    elif failure == "duplicate-identity":
        material[2] = replace(material[2], object_id=material[0].object_id)
    elif failure == "digest":
        material[2] = replace(material[2], content_sha256="0" * 64)
    elif failure == "missing-identity":
        material[2] = replace(material[2], object_id=None)
    else:
        material[2] = replace(material[2], object_id=f"git/blob/{'f' * 40}")

    result = _scan_wiki(
        _declared_wiki_download(
            chunks,
            tuple(material),
            expected_object_count=expected_count,
        )
    )

    assert _BOUNDARY_BLOCKER in result.blockers


def test_typed_wiki_boundaries_cannot_split_an_archive_candidate() -> None:
    payload = _zip_bytes(_MARKER)
    first, second = payload[:1], payload[1:]
    inventory = _inventory_manifest(
        [],
        reachable_object_count=1,
        reachable_blob_count=0,
    )
    chunks = (first, second, inventory)
    boundaries = (
        _typed_boundary(
            (first,),
            object_type="wiki_command_output",
            object_id="wiki/command/clone",
        ),
        _typed_boundary(
            (second,),
            object_type="wiki_command_output",
            object_id="wiki/command/refs-initial",
        ),
        _typed_boundary(
            (inventory,),
            object_type="wiki_mirror_inventory",
            object_id="wiki/mirror-inventory/v1",
        ),
    )

    result = _scan_wiki(
        _declared_wiki_download(
            chunks,
            boundaries,
            expected_object_count=len(boundaries),
        )
    )

    assert _BOUNDARY_BLOCKER in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_zero_wiki_object_count_requires_a_proven_empty_result() -> None:
    empty = HostedReceipt(
        name="github_wiki_download",
        object_boundaries=(),
        expected_object_count=0,
        complete=True,
        page_count=1,
        record_count=0,
        request_count=0,
        parent_record_count=0,
        exit_codes=(0,),
    )
    proven_empty = _scan_wiki(empty, wiki_query=_default_wiki_query(present=False))
    unproven_empty = _scan_wiki(
        replace(empty, complete=False),
        wiki_query=_default_wiki_query(present=False),
    )

    assert _BOUNDARY_BLOCKER not in proven_empty.blockers
    assert _BOUNDARY_BLOCKER in unproven_empty.blockers


@pytest.mark.parametrize(
    "receipt_name",
    sorted(hosted_module._ARCHIVE_CAPABLE_RECEIPTS),
)
def test_every_archive_capable_receipt_binds_exact_nonzero_object_count(
    receipt_name: str,
) -> None:
    if receipt_name == "github_wiki_download":
        chunks, boundaries = _strict_wiki_declaration()
        expected_count = len(boundaries)
    else:
        chunks = (f"safe {receipt_name}".encode("ascii"),)
        boundaries = hosted_module._object_boundaries_for_chunks(receipt_name, chunks)
        assert boundaries is not None
        expected_count = 1
    receipt = HostedReceipt(
        name=receipt_name,
        chunks=chunks,
        object_boundaries=boundaries,
        expected_object_count=(
            expected_count if receipt_name == "github_wiki_download" else None
        ),
        complete=True,
        page_count=1,
        record_count=1,
        request_count=1,
        parent_record_count=(
            1 if receipt_name in hosted_module._PARENT_COUNT_RECEIPTS else None
        ),
        exit_codes=(0,),
    )

    assert receipt.expected_object_count == len(boundaries) > 0
    identities = [boundary.object_id for boundary in boundaries]
    assert all(isinstance(identity, str) and identity for identity in identities)
    assert len(identities) == len(set(identities))
    cursor = 0
    for boundary in boundaries:
        end = cursor + boundary.chunk_count
        data = b"".join(chunks[cursor:end])
        assert boundary.byte_count == len(data)
        assert boundary.content_sha256 == hashlib.sha256(data).hexdigest()
        cursor = end
    assert cursor == len(chunks)


@pytest.mark.parametrize(
    "receipt_name",
    sorted(
        hosted_module._ARCHIVE_CAPABLE_RECEIPTS
        - hosted_module._PAGE_BOUND_ARCHIVE_RECEIPTS
    ),
)
def test_proven_empty_archive_receipts_declare_zero_objects(
    receipt_name: str,
) -> None:
    receipt = HostedReceipt(
        name=receipt_name,
        object_boundaries=(),
        complete=True,
        page_count=1,
        record_count=0,
        request_count=0,
        parent_record_count=(
            0 if receipt_name in hosted_module._PARENT_COUNT_RECEIPTS else None
        ),
        exit_codes=(0,),
    )

    assert receipt.expected_object_count == 0


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(
        ("git", *args),
        cwd=cwd,
        check=True,
        capture_output=True,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "Release Test",
            "GIT_AUTHOR_EMAIL": "release-test@example.invalid",
            "GIT_COMMITTER_NAME": "Release Test",
            "GIT_COMMITTER_EMAIL": "release-test@example.invalid",
        },
    )


class _LocalWikiRunner:
    def __init__(self, remote: Path) -> None:
        self._remote = remote
        self._delegate = SubprocessCommandRunner()

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        rewritten = tuple(
            str(self._remote) if value.endswith(".wiki.git") else value
            for value in argv
        )
        result = self._delegate.run(
            rewritten,
            cwd=cwd,
            input_bytes=input_bytes,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
        )
        if rewritten[:3] == ("git", "clone", "--mirror") and result.exit_code == 0:
            return CommandResult(exit_code=0, stdout=b"", stderr=b"")
        return result


def _make_wiki_remote(tmp_path: Path) -> Path:
    source = tmp_path / "wiki-source"
    remote = tmp_path / "wiki-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "Home.md").write_text("safe wiki payload\n", encoding="utf-8")
    _git("add", "Home.md", cwd=source)
    _git("commit", "-m", "wiki fixture", cwd=source)
    _git("tag", "-a", "wiki-proof", "-m", "safe tag payload", cwd=source)
    _git("clone", "--bare", str(source), str(remote))
    return remote


def test_production_wiki_producer_declares_exact_logical_objects(
    tmp_path: Path,
) -> None:
    remote = _make_wiki_remote(tmp_path)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=_LocalWikiRunner(remote),
        repo="owner/repository",
    )

    query, download = client._wiki_receipts(_policy())

    assert query.complete is True
    assert download.complete is True
    assert download.object_boundaries is not None
    assert download.expected_object_count == len(download.object_boundaries) > 0
    identities = [boundary.object_id for boundary in download.object_boundaries]
    assert all(isinstance(identity, str) and identity for identity in identities)
    assert len(identities) == len(set(identities))
    assert hosted_module._WIKI_COMMAND_OBJECT_IDS.issubset(identities)
    object_types = {boundary.object_type for boundary in download.object_boundaries}
    assert {
        "wiki_command_output",
        "wiki_mirror_inventory",
        "wiki_reachable_blob",
        "wiki_reachable_tag",
    }.issubset(object_types)

    cursor = 0
    for boundary in download.object_boundaries:
        end = cursor + boundary.chunk_count
        data = b"".join(tuple(download.chunks)[cursor:end])
        assert boundary.byte_count == len(data)
        assert boundary.content_sha256 == hashlib.sha256(data).hexdigest()
        cursor = end
    assert cursor == len(tuple(download.chunks))

    result = _scan_wiki(download, wiki_query=query)

    assert result.blockers == ()
