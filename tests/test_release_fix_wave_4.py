from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_SCANNER_VERSION,
    HostedInspection,
    HostedObjectBoundary,
    HostedReceipt,
    scan_hosted_surface,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy

_MARKER = b"ghp_W4a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5"
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
    comment: bytes = b"",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_zip_info(name), data)
        archive.comment = comment
    return prefix + buffer.getvalue()


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


class _HostedClient:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def inspect(
        self,
        _surface: str,
        _policy: SecretScanPolicy,
    ) -> HostedInspection:
        return HostedInspection(
            receipts=(
                HostedReceipt(
                    name="marketplace_snapshot_download",
                    chunks=(self._payload,),
                    object_boundaries=(HostedObjectBoundary(1, len(self._payload)),),
                    complete=True,
                    page_count=1,
                    record_count=1,
                    status_codes=(200,),
                ),
            ),
            scanner_version=HOSTED_SCANNER_VERSION,
        )


def _scan_marketplace(payload: bytes):
    return scan_hosted_surface(
        "marketplace_snapshot",
        _HostedClient(payload),
        _policy(),
    )


def test_required_source_expands_prefixed_deflated_nested_zip(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    nested = _zip_bytes(_MARKER, prefix=_SAFE_PREFIX)
    payload = _zip_bytes(nested, name="nested/history.bin")
    assert _MARKER not in nested
    assert _MARKER not in payload
    (root / "mercury-tools-source.zip").write_bytes(payload)

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "provider_token" for finding in result.findings) or (
        "artifact_read_failed:source" in result.blockers
        or "artifact_unparsed_data:source" in result.blockers
    )


def test_safe_prefixed_required_zip_is_an_explicit_positive_case(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    (root / "mercury-tools-source.zip").write_bytes(
        _zip_bytes(prefix=_SAFE_PREFIX)
    )

    result = scanner_module.scan_artifacts(root, _policy())

    assert result.blockers == ()
    assert result.findings == ()


def test_prefixed_zip_prefix_bytes_are_scanned_as_public_corpus(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    (root / "mercury-tools-source.zip").write_bytes(
        _zip_bytes(prefix=_SAFE_PREFIX + _MARKER)
    )

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "artifact_read_failed:source" not in result.blockers
    assert "artifact_unparsed_data:source" not in result.blockers


def test_prefixed_zip_rejects_an_embedded_archive_in_its_prefix(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    hidden_archive = _zip_bytes(_MARKER)
    visible_archive = _zip_bytes()
    payload = _SAFE_PREFIX + hidden_archive + visible_archive
    assert _MARKER not in payload
    (root / "mercury-tools-source.zip").write_bytes(payload)

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unparsed_data:source" in result.blockers


def test_nested_malformed_prefixed_zip_is_blocked_as_archive_like(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    malformed = _SAFE_PREFIX + _zip_bytes(_MARKER)[:-22]
    payload = _zip_bytes(malformed, name="nested/malformed.payload")
    assert _MARKER not in malformed
    assert _MARKER not in payload
    (root / "mercury-tools-source.zip").write_bytes(payload)

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_opaque_member:source" in result.blockers


def test_hosted_compressed_pax_metadata_is_scanned_and_rejected() -> None:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w:gz",
        format=tarfile.PAX_FORMAT,
    ) as archive:
        data = b"safe"
        info = tarfile.TarInfo("safe.txt")
        info.pax_headers = {"comment": _MARKER.decode("ascii")}
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "hosted_archive_unsafe:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_hosted_compressed_gnu_metadata_is_scanned_and_rejected() -> None:
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer,
        mode="w:gz",
        format=tarfile.GNU_FORMAT,
    ) as archive:
        data = b"safe"
        info = tarfile.TarInfo(
            f"nested/{_MARKER.decode('ascii')}/" + ("long-name-" * 12)
        )
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    payload = buffer.getvalue()
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "hosted_archive_unsafe:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_hosted_zip_metadata_and_trailing_bytes_use_complete_layout() -> None:
    payload = _zip_bytes(comment=_MARKER) + b"unexplained-trailing-bytes"

    result = _scan_marketplace(payload)

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "hosted_archive_unparsed_data:marketplace_snapshot" in result.blockers


def test_hosted_nested_prefixed_zip_is_expanded_by_content() -> None:
    nested = _zip_bytes(_MARKER, prefix=_SAFE_PREFIX)
    payload = _zip_bytes(nested, name="nested/archive.payload")
    assert _MARKER not in nested
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert not result.blockers


def test_hosted_opaque_archive_like_content_blocks() -> None:
    payload = b"7z\xbc\xaf'\x1c" + b"opaque archive bytes"

    result = _scan_marketplace(payload)

    assert "hosted_archive_opaque_member:marketplace_snapshot" in result.blockers
