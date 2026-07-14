from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import stat
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_RECEIPT_INVENTORY,
    HOSTED_SCANNER_VERSION,
    GhApiHostedClient,
    HostedInspection,
    HostedObjectBoundary,
    HostedReceipt,
    scan_hosted_surface,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy
from mercury_tools.release.scanner import CommandResult, SubprocessCommandRunner

_MARKER = b"password=" + b"A" * 1024
_WIKI_SURFACE = "github_packages_pages_wiki"
_WIKI_BOUNDARY_BLOCKER = f"hosted_archive_boundary_invalid:{_WIKI_SURFACE}"
_WIKI_COMMAND_IDS = (
    "wiki/command/clone",
    "wiki/command/refs-initial",
    "wiki/command/head-initial",
    "wiki/command/refs-final",
    "wiki/command/head-final",
)
_SHARED_SKIPPABLE_MAGIC_MIN = 0x184D2A50
_SHARED_SKIPPABLE_MAGIC_MAX = 0x184D2A5F
_ZSTD_COMPRESSED_MARKER = base64.b64decode(
    b"KLUv/QRYjQAAUHBhc3N3b3JkPUEBAPwrgATn+NjMlz4="
)
_LZ4_COMPRESSED_MARKER = base64.b64decode(
    b"BCJNGGRApxcAAACvcGFzc3dvcmQ9QQEA////6lBBQUFBQQAAAAAvheEt"
)
_OPAQUE_FRAME_CASES = (
    ("zstd-skippable-min", _SHARED_SKIPPABLE_MAGIC_MIN.to_bytes(4, "little")),
    ("zstd-skippable-max", _SHARED_SKIPPABLE_MAGIC_MAX.to_bytes(4, "little")),
    ("lz4-modern", b"\x04\x22\x4d\x18"),
    ("lz4-legacy", b"\x02\x21\x4c\x18"),
    ("lz4-skippable-min", _SHARED_SKIPPABLE_MAGIC_MIN.to_bytes(4, "little")),
    ("lz4-skippable-max", _SHARED_SKIPPABLE_MAGIC_MAX.to_bytes(4, "little")),
    ("rar-family-prefix", b"Rar!\x1a\x07"),
    ("rar-v4", b"Rar!\x1a\x07\x00"),
    ("rar-v5", b"Rar!\x1a\x07\x01\x00"),
    ("7z", b"7z\xbc\xaf'\x1c"),
    ("compress", b"\x1f\x9d"),
    ("pack-compress-family", b"\x1f\x1e"),
    ("lzh-compress-family", b"\x1f\xa0"),
    ("zstd-standard", b"\x28\xb5\x2f\xfd"),
)


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=dict(PINNED_SCANNER_VERSIONS))
    return policy.model_copy(update=updates)


class _InspectionClient:
    def __init__(self, inspection: HostedInspection) -> None:
        self._inspection = inspection

    def inspect(
        self,
        _surface: str,
        _policy: SecretScanPolicy,
    ) -> HostedInspection:
        return self._inspection


def _typed_boundary(
    data: bytes,
    *,
    object_type: str,
    object_id: str,
) -> HostedObjectBoundary:
    return HostedObjectBoundary(
        chunk_count=1,
        byte_count=len(data),
        object_type=object_type,
        object_id=object_id,
        content_sha256=hashlib.sha256(data).hexdigest(),
    )


def _git_oid(object_type: str, data: bytes, object_format: str = "sha1") -> str:
    framed = f"{object_type} {len(data)}\0".encode("ascii") + data
    if object_format == "sha1":
        return hashlib.sha1(  # noqa: S324 - canonical Git SHA-1 object identity.
            framed,
            usedforsecurity=False,
        ).hexdigest()
    return hashlib.sha256(framed).hexdigest()


def _manifest_entry(boundary: HostedObjectBoundary) -> dict[str, object]:
    return {
        "object_type": boundary.object_type,
        "object_id": boundary.object_id,
        "byte_count": boundary.byte_count,
        "content_sha256": boundary.content_sha256,
    }


def _wiki_declaration(
    payloads: tuple[tuple[str, bytes], ...] = (("blob", b"safe wiki payload"),),
    *,
    object_format: str = "sha1",
) -> tuple[HostedReceipt, HostedReceipt]:
    payload_boundaries: list[HostedObjectBoundary] = []
    for object_type, data in payloads:
        oid = _git_oid(object_type, data, object_format)
        payload_boundaries.append(
            _typed_boundary(
                data,
                object_type=f"wiki_reachable_{object_type}",
                object_id=f"git/{object_type}/{oid}",
            )
        )

    head_oid = payload_boundaries[0].object_id.rsplit("/", 1)[-1]
    local_refs = f"refs/heads/main\t{head_oid}\t\n".encode("ascii")
    head = f"{head_oid}\n".encode("ascii")
    command_data = (
        b"safe clone output",
        local_refs,
        head,
        local_refs,
        head,
    )
    command_boundaries = tuple(
        _typed_boundary(
            data,
            object_type="wiki_command_output",
            object_id=object_id,
        )
        for object_id, data in zip(_WIKI_COMMAND_IDS, command_data, strict=True)
    )
    remote_refs = {"HEAD": head_oid, "refs/heads/main": head_oid}
    remote_ref_payload = json.dumps(
        sorted(remote_refs.items()),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    manifest = json.dumps(
        {
            "schema_version": 1,
            "object_format": object_format,
            "remote_ref_count": len(remote_refs),
            "remote_ref_digest": hashlib.sha256(remote_ref_payload).hexdigest(),
            "reachable_object_count": len(payload_boundaries),
            "reachable_blob_count": sum(
                boundary.object_type == "wiki_reachable_blob"
                for boundary in payload_boundaries
            ),
            "reachable_tag_count": sum(
                boundary.object_type == "wiki_reachable_tag"
                for boundary in payload_boundaries
            ),
            "command_object_count": len(command_boundaries),
            "command_objects": [
                _manifest_entry(boundary) for boundary in command_boundaries
            ],
            "payload_object_count": len(payload_boundaries),
            "payload_objects": [
                _manifest_entry(boundary) for boundary in payload_boundaries
            ],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    inventory_boundary = _typed_boundary(
        manifest,
        object_type="wiki_mirror_inventory",
        object_id="wiki/mirror-inventory/v1",
    )
    boundaries = (*command_boundaries, inventory_boundary, *payload_boundaries)
    chunks = (*command_data, manifest, *(data for _object_type, data in payloads))
    query_data = b"".join(
        f"{oid}\t{ref}\n".encode("ascii") for ref, oid in remote_refs.items()
    )
    query = HostedReceipt(
        name="github_wiki_query",
        chunks=(query_data,),
        complete=True,
        page_count=1,
        record_count=1,
        request_count=1,
        exit_codes=(0,),
    )
    download = HostedReceipt(
        name="github_wiki_download",
        chunks=chunks,
        object_boundaries=boundaries,
        expected_object_count=len(boundaries),
        complete=True,
        page_count=1,
        record_count=1,
        request_count=1,
        parent_record_count=1,
        exit_codes=(0,),
    )
    return query, download


def _empty_archive_receipt(name: str) -> HostedReceipt:
    return HostedReceipt(
        name=name,
        object_boundaries=(),
        expected_object_count=0,
        complete=True,
        page_count=1,
        record_count=0,
        request_count=0,
        parent_record_count=0,
        exit_codes=(0,),
    )


def _wiki_inspection(
    query: HostedReceipt,
    download: HostedReceipt,
) -> HostedInspection:
    receipts = (
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
            chunks=(b"{}",),
            complete=True,
            page_count=1,
            record_count=0,
            request_count=1,
            exit_codes=(0,),
        ),
        _empty_archive_receipt("github_pages_download"),
        query,
        download,
    )
    return HostedInspection(receipts, HOSTED_SCANNER_VERSION)


def _scan_wiki(query: HostedReceipt, download: HostedReceipt):
    return scan_hosted_surface(
        _WIKI_SURFACE,
        _InspectionClient(_wiki_inspection(query, download)),
        _policy(),
    )


def _replace_wiki_manifest(
    download: HostedReceipt,
    mutate: Callable[[dict[str, object]], None],
) -> HostedReceipt:
    boundaries = list(download.object_boundaries or ())
    chunks = list(download.chunks)
    inventory_index = next(
        index
        for index, boundary in enumerate(boundaries)
        if boundary.object_type == "wiki_mirror_inventory"
    )
    manifest = json.loads(chunks[inventory_index])
    mutate(manifest)
    encoded = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    chunks[inventory_index] = encoded
    boundaries[inventory_index] = _typed_boundary(
        encoded,
        object_type="wiki_mirror_inventory",
        object_id="wiki/mirror-inventory/v1",
    )
    return replace(download, chunks=tuple(chunks), object_boundaries=tuple(boundaries))


def _zip_bytes(data: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo("payload.bin")
        info.create_system = 3
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, data)
    return buffer.getvalue()


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


@pytest.mark.parametrize(
    ("name", "magic"),
    _OPAQUE_FRAME_CASES,
)
def test_every_claimed_opaque_frame_family_is_classified(
    name: str,
    magic: bytes,
) -> None:
    del name
    assert scanner_module._is_archive_candidate(magic) is True
    assert scanner_module._detect_archive_format(magic) == "opaque"


@pytest.mark.parametrize(
    "magic_value",
    range(_SHARED_SKIPPABLE_MAGIC_MIN, _SHARED_SKIPPABLE_MAGIC_MAX + 1),
)
def test_full_shared_zstd_lz4_skippable_range_is_classified(
    magic_value: int,
) -> None:
    magic = magic_value.to_bytes(4, "little")
    assert scanner_module._is_archive_candidate(magic) is True
    assert scanner_module._detect_archive_format(magic) == "opaque"


def test_opaque_signature_matrix_is_the_single_complete_classifier_source() -> None:
    expected = {magic for _name, magic in _OPAQUE_FRAME_CASES}
    expected.update(
        value.to_bytes(4, "little")
        for value in range(
            _SHARED_SKIPPABLE_MAGIC_MIN,
            _SHARED_SKIPPABLE_MAGIC_MAX + 1,
        )
    )

    assert set(scanner_module._KNOWN_OPAQUE_ARCHIVE_SIGNATURES) == expected


@pytest.mark.parametrize(("name", "magic"), _OPAQUE_FRAME_CASES)
def test_every_claimed_opaque_frame_family_fails_closed(
    name: str,
    magic: bytes,
) -> None:
    del name

    result = _scan_marketplace(magic + b"safe opaque payload")

    assert "hosted_archive_opaque_member:marketplace_snapshot" in result.blockers


@pytest.mark.parametrize(
    "outside_magic",
    (
        (_SHARED_SKIPPABLE_MAGIC_MIN - 1).to_bytes(4, "little"),
        (_SHARED_SKIPPABLE_MAGIC_MAX + 1).to_bytes(4, "little"),
    ),
    ids=("below-skippable-range", "above-skippable-range"),
)
def test_shared_zstd_lz4_skippable_range_has_exact_boundaries(
    outside_magic: bytes,
) -> None:
    assert scanner_module._is_archive_candidate(outside_magic) is False
    assert scanner_module._detect_archive_format(outside_magic) is None


@pytest.mark.parametrize(
    ("name", "compressed"),
    (
        ("zstd", _ZSTD_COMPRESSED_MARKER),
        ("lz4", _LZ4_COMPRESSED_MARKER),
    ),
)
def test_skippable_frame_followed_by_compressed_marker_blocks(
    name: str,
    compressed: bytes,
) -> None:
    del name
    payload = (
        _SHARED_SKIPPABLE_MAGIC_MIN.to_bytes(4, "little")
        + (0).to_bytes(4, "little")
        + compressed
    )
    assert _MARKER not in payload

    result = _scan_marketplace(payload)

    assert "hosted_archive_opaque_member:marketplace_snapshot" in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_exact_canonical_wiki_declaration_is_clean() -> None:
    query, download = _wiki_declaration()

    result = _scan_wiki(query, download)

    assert result.blockers == ()
    assert result.findings == ()


@pytest.mark.parametrize(
    ("field", "value"),
    (("reachable_blob_count", 0), ("reachable_tag_count", 1)),
)
def test_wiki_payload_identity_category_counts_are_exact(
    field: str,
    value: int,
) -> None:
    query, download = _wiki_declaration()
    download = _replace_wiki_manifest(
        download,
        lambda manifest: manifest.__setitem__(field, value),
    )

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


@pytest.mark.parametrize(
    "parts",
    (
        (1, 2),
        (2, 3),
        (1, 2, 3),
        (1, 2, 3, 4),
    ),
    ids=("p-k-remainder", "pk-byte-remainder", "three-junctions", "four-junctions"),
)
def test_zip_candidate_across_any_number_of_wiki_objects_blocks(
    parts: tuple[int, ...],
) -> None:
    archive = _zip_bytes(_MARKER)
    offsets = (0, *parts, len(archive))
    payloads = tuple(
        ("blob", archive[offsets[index] : offsets[index + 1]])
        for index in range(len(offsets) - 1)
    )
    assert all(
        scanner_module._detect_archive_format(data) is None
        for _object_type, data in payloads
    )
    query, download = _wiki_declaration(payloads)

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers
    assert _MARKER.decode("ascii") not in result.model_dump_json()


def test_tar_header_spanning_many_wiki_objects_blocks() -> None:
    info = tarfile.TarInfo("safe.bin")
    header = info.tobuf(format=tarfile.USTAR_FORMAT)
    payloads = tuple(
        ("blob", header[offset : offset + 32])
        for offset in range(0, len(header), 32)
    )
    assert len(payloads) == 16
    assert all(
        scanner_module._detect_archive_format(data) is None
        for _object_type, data in payloads
    )
    query, download = _wiki_declaration(payloads)

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


@pytest.mark.parametrize(
    "objects",
    (
        (b"P", b"K", b"\x03", b"\x04safe"),
        tuple(
            tarfile.TarInfo("safe.bin").tobuf(format=tarfile.USTAR_FORMAT)[
                offset : offset + 32
            ]
            for offset in range(0, 512, 32)
        ),
    ),
    ids=("zip-four-objects", "tar-sixteen-objects"),
)
def test_archive_candidate_rolling_window_spans_all_logical_objects(
    objects: tuple[bytes, ...],
) -> None:
    detector = getattr(
        scanner_module,
        "_archive_candidate_crosses_object_boundaries",
        None,
    )
    assert detector is not None
    assert detector(objects) is True


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
@pytest.mark.parametrize("object_type", ("blob", "tag"))
def test_canonical_git_hash_verifier_uses_type_length_and_object_format(
    object_format: str,
    object_type: str,
) -> None:
    data = f"safe {object_type} bytes".encode("ascii")
    verifier = getattr(hosted_module, "_canonical_git_object_oid", None)
    assert verifier is not None
    assert verifier(data, object_type, object_format) == _git_oid(
        object_type,
        data,
        object_format,
    )


@pytest.mark.parametrize("failure", ("missing", "duplicate", "extra"))
def test_wiki_requires_exact_command_identity_cardinality(failure: str) -> None:
    query, download = _wiki_declaration()
    boundaries = list(download.object_boundaries or ())
    chunks = list(download.chunks)
    if failure == "missing":
        del boundaries[0]
        del chunks[0]
        download = replace(
            download,
            chunks=tuple(chunks),
            object_boundaries=tuple(boundaries),
            expected_object_count=len(boundaries),
        )
        download = _replace_wiki_manifest(
            download,
            lambda manifest: (
                manifest["command_objects"].pop(0),
                manifest.__setitem__("command_object_count", 4),
            ),
        )
    elif failure == "duplicate":
        boundaries[1] = replace(boundaries[1], object_id=boundaries[0].object_id)
        download = replace(download, object_boundaries=tuple(boundaries))
        download = _replace_wiki_manifest(
            download,
            lambda manifest: manifest["command_objects"][1].__setitem__(
                "object_id", _WIKI_COMMAND_IDS[0]
            ),
        )
    else:
        extra = b"safe extra command"
        boundaries.insert(
            5,
            _typed_boundary(
                extra,
                object_type="wiki_command_output",
                object_id="wiki/command/extra",
            ),
        )
        chunks.insert(5, extra)
        download = replace(
            download,
            chunks=tuple(chunks),
            object_boundaries=tuple(boundaries),
            expected_object_count=len(boundaries),
        )
        download = _replace_wiki_manifest(
            download,
            lambda manifest: (
                manifest["command_objects"].append(_manifest_entry(boundaries[5])),
                manifest.__setitem__("command_object_count", 6),
            ),
        )

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


def test_wiki_logical_object_categories_require_canonical_order() -> None:
    query, download = _wiki_declaration()
    boundaries = list(download.object_boundaries or ())
    chunks = list(download.chunks)
    inventory_index = next(
        index
        for index, boundary in enumerate(boundaries)
        if boundary.object_type == "wiki_mirror_inventory"
    )
    payload_index = next(
        index
        for index, boundary in enumerate(boundaries)
        if boundary.object_type == "wiki_reachable_blob"
    )
    payload_boundary = boundaries.pop(payload_index)
    payload_chunk = chunks.pop(payload_index)
    boundaries.insert(inventory_index, payload_boundary)
    chunks.insert(inventory_index, payload_chunk)
    download = replace(
        download,
        chunks=tuple(chunks),
        object_boundaries=tuple(boundaries),
    )

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


@pytest.mark.parametrize("inventory", ("command_objects", "payload_objects"))
@pytest.mark.parametrize("failure", ("omission", "duplicate", "extra"))
def test_wiki_manifest_inventory_has_no_omissions_duplicates_or_extras(
    inventory: str,
    failure: str,
) -> None:
    query, download = _wiki_declaration()
    count_field = (
        "command_object_count"
        if inventory == "command_objects"
        else "payload_object_count"
    )

    def mutate(manifest: dict[str, object]) -> None:
        entries = manifest[inventory]
        assert isinstance(entries, list)
        if failure == "omission":
            entries.pop()
        elif failure == "duplicate":
            entries.append(dict(entries[0]))
        else:
            extra = dict(entries[0])
            extra["object_id"] = (
                "wiki/command/extra"
                if inventory == "command_objects"
                else f"git/blob/{'f' * 40}"
            )
            entries.append(extra)
        manifest[count_field] = len(entries)

    download = _replace_wiki_manifest(download, mutate)

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


@pytest.mark.parametrize("object_type", ("blob", "tag"))
def test_wiki_object_identity_must_match_canonical_git_framing(
    object_type: str,
) -> None:
    blob = b"safe canonical blob"
    if object_type == "blob":
        data = blob
    else:
        target = _git_oid("blob", blob)
        data = (
            f"object {target}\ntype blob\ntag proof\n"
            "tagger Release Test <release@example.invalid> 0 +0000\n\n"
            "safe annotated tag\n"
        ).encode("ascii")
    query, download = _wiki_declaration(((object_type, data),))
    boundaries = list(download.object_boundaries or ())
    payload_index = next(
        index
        for index, boundary in enumerate(boundaries)
        if boundary.object_type == f"wiki_reachable_{object_type}"
    )
    forged_oid = "f" * 40
    boundaries[payload_index] = replace(
        boundaries[payload_index],
        object_id=f"git/{object_type}/{forged_oid}",
    )
    download = replace(download, object_boundaries=tuple(boundaries))
    download = _replace_wiki_manifest(
        download,
        lambda manifest: manifest["payload_objects"][0].__setitem__(
            "object_id", f"git/{object_type}/{forged_oid}"
        ),
    )

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers


@pytest.mark.parametrize("failure", ("uppercase", "forged", "unknown-field"))
def test_noncanonical_or_unreconciled_wiki_digest_metadata_blocks_and_scans(
    failure: str,
) -> None:
    query, download = _wiki_declaration()
    unknown_value = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"

    def mutate(manifest: dict[str, object]) -> None:
        if failure == "uppercase":
            manifest["remote_ref_digest"] = str(manifest["remote_ref_digest"]).upper()
        elif failure == "forged":
            manifest["remote_ref_digest"] = "f" * 64
        else:
            manifest["unknown_digest_metadata"] = unknown_value

    download = _replace_wiki_manifest(download, mutate)

    result = _scan_wiki(query, download)

    assert _WIKI_BOUNDARY_BLOCKER in result.blockers
    if failure == "unknown-field":
        assert any(finding.rule == "high_entropy" for finding in result.findings)
        assert unknown_value not in result.model_dump_json()


def test_validated_wiki_digest_exemption_does_not_apply_to_raw_blob_bytes() -> None:
    command_digest = hashlib.sha256(b"safe clone output").hexdigest().encode("ascii")
    raw_blob = b"untrusted raw payload " + command_digest
    query, download = _wiki_declaration((("blob", raw_blob),))

    result = _scan_wiki(query, download)

    assert result.blockers == ()
    assert any(finding.rule == "high_entropy" for finding in result.findings)
    assert command_digest.decode("ascii") not in result.model_dump_json()


_EXPECTED_RECEIPT_PARENT_GRAPH = {
    "github_release_assets_query": "github_releases_query",
    "github_release_assets_download": "github_release_assets_query",
    "github_actions_logs_download": "github_actions_runs_query",
    "github_actions_artifacts_download": "github_actions_artifacts_query",
    "github_actions_caches_content": "github_actions_caches_query",
    "github_package_versions_query": "github_packages_query",
    "github_package_versions_content": "github_package_versions_query",
    "github_pages_download": "github_pages_query",
    "github_wiki_download": "github_wiki_query",
    "supabase_storage_download": "supabase_storage_query",
}
_EXPECTED_ARCHIVE_CARDINALITY = {
    "github_release_assets_download": "parent_records",
    "github_actions_logs_download": "parent_records",
    "github_actions_artifacts_download": "parent_records",
    "github_actions_caches_content": "parent_records",
    "github_package_versions_content": "parent_records",
    "github_pages_download": "parent_records",
    "github_wiki_download": "wiki_logical_objects",
    "marketplace_snapshot_download": "receipt_pages",
    "supabase_storage_download": "parent_records",
}


def test_receipt_parent_and_archive_cardinality_matrices_are_exhaustive() -> None:
    assert dict(hosted_module._RECEIPT_PARENT_GRAPH) == _EXPECTED_RECEIPT_PARENT_GRAPH
    assert (
        dict(hosted_module._ARCHIVE_RECEIPT_CARDINALITY)
        == _EXPECTED_ARCHIVE_CARDINALITY
    )
    assert set(hosted_module._ARCHIVE_RECEIPT_CARDINALITY) == set(
        hosted_module._ARCHIVE_CAPABLE_RECEIPTS
    )
    for surface, inventory in HOSTED_RECEIPT_INVENTORY.items():
        del surface
        positions = {name: index for index, name in enumerate(inventory)}
        for child, parent in _EXPECTED_RECEIPT_PARENT_GRAPH.items():
            if child in positions:
                assert parent in positions
                assert positions[parent] < positions[child]


def _surface_for_receipt(receipt_name: str) -> str:
    return next(
        surface
        for surface, inventory in HOSTED_RECEIPT_INVENTORY.items()
        if receipt_name in inventory
    )


def _self_asserted_zero_child_inspection(
    child_name: str,
    parent_name: str,
) -> tuple[str, HostedInspection]:
    surface = _surface_for_receipt(child_name)
    graph = _EXPECTED_RECEIPT_PARENT_GRAPH
    inventory = HOSTED_RECEIPT_INVENTORY[surface]
    receipts: dict[str, HostedReceipt] = {}
    for name in inventory:
        is_archive = name in _EXPECTED_ARCHIVE_CARDINALITY
        is_child = name in graph
        receipts[name] = HostedReceipt(
            name=name,
            chunks=() if is_archive else (b"safe empty query proof",),
            object_boundaries=() if is_archive else None,
            expected_object_count=0 if is_archive else None,
            complete=True,
            page_count=1,
            record_count=0,
            request_count=0 if is_child else 1,
            parent_record_count=0 if is_child else None,
            exit_codes=(0,),
        )

    current = parent_name
    while True:
        receipt = receipts[current]
        parent = graph.get(current)
        receipts[current] = replace(
            receipt,
            chunks=(b"safe nonempty parent proof",),
            record_count=1,
            request_count=1,
            parent_record_count=1 if parent is not None else None,
        )
        if parent is None:
            break
        current = parent

    child = receipts[child_name]
    receipts[child_name] = replace(
        child,
        chunks=(),
        object_boundaries=(),
        expected_object_count=0,
        complete=True,
        record_count=0,
        request_count=0,
        parent_record_count=0,
    )
    return surface, HostedInspection(
        tuple(receipts[name] for name in inventory),
        HOSTED_SCANNER_VERSION,
    )


@pytest.mark.parametrize(
    ("child_name", "parent_name"),
    tuple(
        (child, parent)
        for child, parent in _EXPECTED_RECEIPT_PARENT_GRAPH.items()
        if child in _EXPECTED_ARCHIVE_CARDINALITY
    ),
    ids=lambda value: value,
)
def test_archive_child_cannot_self_assert_zero_against_nonempty_parent(
    child_name: str,
    parent_name: str,
) -> None:
    surface, inspection = _self_asserted_zero_child_inspection(child_name, parent_name)

    result = scan_hosted_surface(
        surface,
        _InspectionClient(inspection),
        _policy(),
    )

    assert f"hosted_receipt_reconciliation_failed:{surface}" in result.blockers
    assert f"hosted_archive_boundary_invalid:{surface}" in result.blockers


def test_actions_nonempty_runs_parent_with_zero_logs_child_blocks() -> None:
    surface, inspection = _self_asserted_zero_child_inspection(
        "github_actions_logs_download",
        "github_actions_runs_query",
    )
    runs, logs = inspection.receipts[:2]
    assert runs.complete is True and runs.record_count == 1
    assert logs.complete is True and logs.record_count == 0
    assert logs.request_count == logs.parent_record_count == 0

    result = scan_hosted_surface(
        surface,
        _InspectionClient(inspection),
        _policy(),
    )

    assert f"hosted_receipt_reconciliation_failed:{surface}" in result.blockers
    assert f"hosted_archive_boundary_invalid:{surface}" in result.blockers


def test_archive_child_requires_the_actual_parent_to_be_completed() -> None:
    surface, inspection = _self_asserted_zero_child_inspection(
        "github_actions_logs_download",
        "github_actions_runs_query",
    )
    receipts = list(inspection.receipts)
    parent_index = next(
        index
        for index, receipt in enumerate(receipts)
        if receipt.name == "github_actions_runs_query"
    )
    child_index = next(
        index
        for index, receipt in enumerate(receipts)
        if receipt.name == "github_actions_logs_download"
    )
    data = b"safe completed-child proof"
    receipts[parent_index] = replace(receipts[parent_index], complete=False)
    receipts[child_index] = replace(
        receipts[child_index],
        chunks=(data,),
        object_boundaries=(HostedObjectBoundary(1, len(data)),),
        expected_object_count=1,
        record_count=1,
        request_count=1,
        parent_record_count=1,
    )

    result = scan_hosted_surface(
        surface,
        _InspectionClient(
            HostedInspection(tuple(receipts), HOSTED_SCANNER_VERSION)
        ),
        _policy(),
    )

    assert f"hosted_receipt_reconciliation_failed:{surface}" in result.blockers
    assert f"hosted_archive_boundary_invalid:{surface}" in result.blockers


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


def test_production_wiki_blob_and_annotated_tag_attestation_is_clean(
    tmp_path: Path,
) -> None:
    remote = _make_wiki_remote(tmp_path)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=_LocalWikiRunner(remote),
        repo="owner/repository",
    )

    query, download = client._wiki_receipts(_policy())
    result = _scan_wiki(query, download)

    assert query.complete is True
    assert download.complete is True
    assert result.blockers == ()
    assert result.findings == ()
    assert _MARKER.decode("ascii") not in result.model_dump_json()
