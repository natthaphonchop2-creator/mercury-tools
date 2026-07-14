from __future__ import annotations

import bz2
import gzip
import io
import lzma
import stat
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_SCANNER_VERSION,
    HostedInspection,
    HostedObjectBoundary,
    HostedReceipt,
    scan_hosted_surface,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy

_MARKER = b"ghp_W5a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5"
_SAFE_PREFIX = b"#!/bin/sh\nexit 0\n"


def _policy(**updates: object) -> SecretScanPolicy:
    return SecretScanPolicy(
        scanner_versions=dict(PINNED_SCANNER_VERSIONS),
        **updates,
    )


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    return info


def _zip_bytes(
    data: bytes = b"safe",
    *,
    name: str = "safe.txt",
    prefix: bytes = b"",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_zip_info(name), data)
    return prefix + buffer.getvalue()


def _tar_candidate() -> bytes:
    compressed_marker = gzip.compress(_MARKER, mtime=0)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("nested/marker.gz")
        info.size = len(compressed_marker)
        archive.addfile(info, io.BytesIO(compressed_marker))
    return buffer.getvalue()


def _v7_tar_candidate() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        data = b"safe v7 tar payload"
        info = tarfile.TarInfo("safe.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    candidate = bytearray(buffer.getvalue())
    candidate[257:265] = b"\0" * 8
    candidate[148:156] = b" " * 8
    checksum = sum(candidate[:512])
    candidate[148:156] = f"{checksum:06o}\0 ".encode("ascii")
    payload = bytes(candidate)
    assert scanner_module._tar_header_is_valid(payload)
    return payload


def _opaque_candidate(signature: bytes) -> bytes:
    return signature + gzip.compress(_MARKER, mtime=0)


def _xz_candidate() -> bytes:
    corpus = (b"A" * 4096) + _MARKER + (b"B" * 4096)
    return lzma.compress(corpus, format=lzma.FORMAT_XZ, preset=9)


_PREFIX_CANDIDATES: tuple[tuple[str, Callable[[], bytes]], ...] = (
    ("gzip", lambda: gzip.compress(_MARKER, mtime=0)),
    ("bzip2", lambda: bz2.compress(_MARKER)),
    ("xz", _xz_candidate),
    ("zip", lambda: _zip_bytes(_MARKER)),
    ("tar", _tar_candidate),
    ("v7-tar", _v7_tar_candidate),
    ("zip-local-signature", lambda: _opaque_candidate(b"PK\x03\x04")),
    ("zip-central-signature", lambda: _opaque_candidate(b"PK\x01\x02")),
    ("zip-eocd-signature", lambda: _opaque_candidate(b"PK\x05\x06")),
    ("zip-descriptor-signature", lambda: _opaque_candidate(b"PK\x07\x08")),
    ("zip64-eocd-signature", lambda: _opaque_candidate(b"PK\x06\x06")),
    ("zip64-locator-signature", lambda: _opaque_candidate(b"PK\x06\x07")),
    ("7z", lambda: _opaque_candidate(b"7z\xbc\xaf'\x1c")),
    ("rar", lambda: _opaque_candidate(b"Rar!\x1a\x07")),
    ("lz4", lambda: _opaque_candidate(b"\x04\x22\x4d\x18")),
    ("compress", lambda: _opaque_candidate(b"\x1f\x9d")),
    ("zstd", lambda: _opaque_candidate(b"\x28\xb5\x2f\xfd")),
)


def _write_zip(path: Path, data: bytes = b"safe") -> None:
    path.write_bytes(_zip_bytes(data))


def _write_artifact_set(root: Path) -> None:
    root.mkdir()
    _write_zip(root / "mercury_tools-0.2.1-py3-none-any.whl")
    with tarfile.open(root / "mercury_tools-0.2.1.tar.gz", "w:gz") as archive:
        data = b"safe"
        info = tarfile.TarInfo("safe.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    _write_zip(root / "mercury-finance-plugin.zip")
    _write_zip(root / "mercury-tools-source.zip")


@pytest.mark.parametrize(
    ("_candidate_name", "candidate_factory"),
    _PREFIX_CANDIDATES,
    ids=[name for name, _factory in _PREFIX_CANDIDATES],
)
def test_archive_candidate_anywhere_in_accepted_zip_prefix_fails_closed(
    tmp_path: Path,
    _candidate_name: str,
    candidate_factory: Callable[[], bytes],
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    candidate = candidate_factory()
    payload = _SAFE_PREFIX + candidate + _zip_bytes()
    assert _MARKER not in candidate
    assert _MARKER not in payload
    (root / "mercury-tools-source.zip").write_bytes(payload)

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "provider_token" for finding in result.findings) or (
        "artifact_unparsed_data:source" in result.blockers
        or "artifact_read_failed:source" in result.blockers
    )


def _compressed_extended_tar(tar_format: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz", format=tar_format) as archive:
        data = b"safe"
        if tar_format == tarfile.PAX_FORMAT:
            info = tarfile.TarInfo("safe.txt")
            info.pax_headers = {"comment": _MARKER.decode("ascii")}
        else:
            info = tarfile.TarInfo(
                f"nested/{_MARKER.decode('ascii')}/" + ("long-name-" * 12)
            )
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    assert _MARKER not in payload
    return payload


class _SplitHostedClient:
    def __init__(
        self,
        payload: bytes,
        split_at: int,
        object_boundaries: tuple[HostedObjectBoundary, ...] | None,
    ) -> None:
        self._chunks = (payload[:split_at], payload[split_at:])
        self._object_boundaries = object_boundaries

    def inspect(
        self,
        _surface: str,
        _policy: SecretScanPolicy,
    ) -> HostedInspection:
        return HostedInspection(
            receipts=(
                HostedReceipt(
                    name="marketplace_snapshot_download",
                    chunks=self._chunks,
                    object_boundaries=self._object_boundaries,
                    complete=True,
                    page_count=1,
                    record_count=1,
                    status_codes=(200,),
                ),
            ),
            scanner_version=HOSTED_SCANNER_VERSION,
        )


def _scan_split_marketplace(
    payload: bytes,
    split_at: int = 1,
    object_boundaries: tuple[HostedObjectBoundary, ...] | None = None,
):
    return scan_hosted_surface(
        "marketplace_snapshot",
        _SplitHostedClient(payload, split_at, object_boundaries),
        _policy(),
    )


@pytest.mark.parametrize(
    "tar_format",
    [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT],
    ids=["pax", "gnu"],
)
def test_split_compressed_extended_tar_without_object_boundary_blocks(
    tar_format: int,
) -> None:
    payload = _compressed_extended_tar(tar_format)

    result = _scan_split_marketplace(payload)

    assert "hosted_archive_boundary_invalid:marketplace_snapshot" in result.blockers


def test_split_prefixed_nested_zip_without_object_boundary_blocks() -> None:
    nested = _zip_bytes(_MARKER, prefix=_SAFE_PREFIX)
    payload = _zip_bytes(nested, name="nested/archive.payload")
    assert _MARKER not in nested
    assert _MARKER not in payload

    result = _scan_split_marketplace(payload)

    assert "hosted_archive_boundary_invalid:marketplace_snapshot" in result.blockers


@pytest.mark.parametrize(
    "tar_format",
    [tarfile.PAX_FORMAT, tarfile.GNU_FORMAT],
    ids=["pax", "gnu"],
)
def test_split_compressed_extended_tar_uses_one_complete_object_boundary(
    tar_format: int,
) -> None:
    payload = _compressed_extended_tar(tar_format)
    boundary = HostedObjectBoundary(chunk_count=2, byte_count=len(payload))

    result = _scan_split_marketplace(payload, object_boundaries=(boundary,))

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "hosted_archive_unsafe:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_split_prefixed_nested_zip_uses_one_complete_object_boundary() -> None:
    nested = _zip_bytes(_MARKER, prefix=_SAFE_PREFIX)
    payload = _zip_bytes(nested, name="nested/archive.payload")
    boundary = HostedObjectBoundary(chunk_count=2, byte_count=len(payload))
    assert _MARKER not in nested
    assert _MARKER not in payload

    result = _scan_split_marketplace(payload, object_boundaries=(boundary,))

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert result.blockers == ()


def test_complete_split_archive_invokes_shared_parser_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _zip_bytes()
    boundary = HostedObjectBoundary(chunk_count=2, byte_count=len(payload))
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
        return [], []

    monkeypatch.setattr(hosted_module, "_scan_complete_hosted_archive", capture)

    result = _scan_split_marketplace(payload, object_boundaries=(boundary,))

    assert result.blockers == ()
    assert result.findings == ()
    assert parsed == [payload]


def test_declared_complete_archive_object_with_empty_transport_tail_is_clean() -> None:
    payload = _zip_bytes()
    boundary = HostedObjectBoundary(chunk_count=2, byte_count=len(payload))

    result = _scan_split_marketplace(
        payload,
        split_at=len(payload),
        object_boundaries=(boundary,),
    )

    assert result.blockers == ()
    assert result.findings == ()


@pytest.mark.parametrize(
    "failure",
    ["extra", "missing", "byte-mismatch", "empty", "malformed"],
)
def test_invalid_complete_object_boundaries_block(failure: str) -> None:
    payload = _zip_bytes()
    first_size = 1
    if failure == "extra":
        boundaries: tuple[HostedObjectBoundary, ...] = (
            HostedObjectBoundary(chunk_count=1, byte_count=first_size),
        )
    elif failure == "missing":
        boundaries = (HostedObjectBoundary(chunk_count=3, byte_count=len(payload)),)
    elif failure == "byte-mismatch":
        boundaries = (HostedObjectBoundary(chunk_count=2, byte_count=len(payload) - 1),)
    elif failure == "empty":
        boundaries = ()
    else:
        boundaries = (HostedObjectBoundary(chunk_count=True, byte_count=len(payload)),)

    result = _scan_split_marketplace(payload, object_boundaries=boundaries)

    assert "hosted_archive_boundary_invalid:marketplace_snapshot" in result.blockers


def test_one_request_cannot_declare_split_archive_as_two_complete_objects() -> None:
    payload = _compressed_extended_tar(tarfile.PAX_FORMAT)
    boundaries = (
        HostedObjectBoundary(chunk_count=1, byte_count=1),
        HostedObjectBoundary(chunk_count=1, byte_count=len(payload) - 1),
    )

    result = _scan_split_marketplace(payload, object_boundaries=boundaries)

    assert "hosted_archive_boundary_invalid:marketplace_snapshot" in result.blockers
