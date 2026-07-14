from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

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
from mercury_tools.release.scanner import (
    CommandResult,
    ReleaseGateError,
    SubprocessCommandRunner,
    scan_git_repository,
)

_WIKI_SURFACE = "github_packages_pages_wiki"
_MESSAGE_MARKER = b"ghp_MessageMarker1234567890AbCdEfGhIj"
_PATH_MARKER = b"ghp_PathMarker1234567890AbCdEfGhIjKl"
_HEADER_MARKER = b"ghp_HeaderMarker1234567890AbCdEfGhIj"
_SIGNATURE_MARKER = b"ghp_SignatureMarker1234567890AbCdEfGh"
_RAW_DIGEST = b"0123456789abcdef" * 4
_CATEGORY_ORDER = {
    "wiki_reachable_blob": 0,
    "wiki_reachable_tag": 1,
    "wiki_reachable_commit": 2,
    "wiki_reachable_tree": 3,
}


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=dict(PINNED_SCANNER_VERSIONS))
    return policy.model_copy(update=updates)


def _git(
    *args: str,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> bytes:
    completed = subprocess.run(
        ("git", *args),
        cwd=cwd,
        input=input_bytes,
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
    return completed.stdout.strip()


def _make_source(
    tmp_path: Path,
    *,
    name: str,
    filename: bytes = b"Home.md",
    content: bytes = b"safe wiki payload\n",
    message: bytes = b"safe commit message\n",
    optional_header: bytes | None = None,
    signature_marker: bytes | None = None,
    annotated_tag: bool = False,
    object_format: str = "sha1",
) -> Path:
    source = tmp_path / name
    source.mkdir()
    init_args = (
        ("init", "--object-format=sha256", "-b", "main")
        if object_format == "sha256"
        else ("init", "-b", "main")
    )
    _git(*init_args, cwd=source)
    (source / os.fsdecode(filename)).write_bytes(content)
    _git("add", "--", os.fsdecode(filename), cwd=source)
    tree_oid = _git("write-tree", cwd=source)
    headers = [
        b"tree " + tree_oid,
        b"author Release Test <release-test@example.invalid> 1700000000 +0000",
        b"committer Release Test <release-test@example.invalid> 1700000000 +0000",
    ]
    if optional_header is not None:
        headers.append(b"x-release-proof " + optional_header)
    if signature_marker is not None:
        headers.append(
            b"gpgsig -----BEGIN TEST SIGNATURE-----\n "
            + signature_marker
            + b"\n -----END TEST SIGNATURE-----"
        )
    commit_payload = b"\n".join(headers) + b"\n\n" + message
    commit_oid = _git(
        "hash-object",
        "-t",
        "commit",
        "-w",
        "--stdin",
        cwd=source,
        input_bytes=commit_payload,
    )
    _git("update-ref", "refs/heads/main", commit_oid.decode("ascii"), cwd=source)
    if annotated_tag:
        _git("tag", "-a", "wiki-proof", "-m", "safe tag payload", cwd=source)
    return source


def _publish_remote(source: Path, remote: Path, *, pull_ref: bool = False) -> Path:
    _git("clone", "--bare", str(source), str(remote))
    if pull_ref:
        head = _git("rev-parse", "HEAD", cwd=source).decode("ascii")
        _git("--git-dir", str(remote), "update-ref", "refs/pull/1/head", head)
    return remote


class _DelegatingRunner:
    def __init__(self, *, wiki_remote: Path | None = None) -> None:
        self._wiki_remote = wiki_remote
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
        executable = Path(argv[0]).name
        if executable == "gitleaks":
            return CommandResult(0, b"[]", b"")
        if executable == "trufflehog":
            return CommandResult(0, b"", b"")
        rewritten = tuple(
            str(self._wiki_remote)
            if self._wiki_remote is not None and value.endswith(".wiki.git")
            else value
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
            return CommandResult(0, b"", b"")
        return result


class _InspectionClient:
    def __init__(self, inspection: HostedInspection) -> None:
        self._inspection = inspection

    def inspect(
        self,
        _surface: str,
        _policy: SecretScanPolicy,
    ) -> HostedInspection:
        return self._inspection


def _wiki_receipts(remote: Path) -> tuple[HostedReceipt, HostedReceipt]:
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=_DelegatingRunner(wiki_remote=remote),
        repo="owner/repository",
    )
    return client._wiki_receipts(_policy())


def _scan_wiki(query: HostedReceipt, download: HostedReceipt):
    empty = lambda name: HostedReceipt(  # noqa: E731 - compact receipt fixture.
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
        empty("github_package_versions_content"),
        HostedReceipt(
            name="github_pages_query",
            chunks=(b"[]",),
            complete=True,
            page_count=1,
            record_count=0,
            request_count=1,
            exit_codes=(0,),
        ),
        empty("github_pages_download"),
        query,
        download,
    )
    return scan_hosted_surface(
        _WIKI_SURFACE,
        _InspectionClient(HostedInspection(receipts, HOSTED_SCANNER_VERSION)),
        _policy(),
    )


def _materialize(
    receipt: HostedReceipt,
) -> tuple[tuple[HostedObjectBoundary, bytes], ...]:
    chunks = tuple(receipt.chunks)
    material: list[tuple[HostedObjectBoundary, bytes]] = []
    cursor = 0
    for boundary in receipt.object_boundaries or ():
        end = cursor + boundary.chunk_count
        material.append((boundary, b"".join(chunks[cursor:end])))
        cursor = end
    assert cursor == len(chunks)
    return tuple(material)


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


def _manifest_entry(boundary: HostedObjectBoundary) -> dict[str, object]:
    return {
        "object_type": boundary.object_type,
        "object_id": boundary.object_id,
        "byte_count": boundary.byte_count,
        "content_sha256": boundary.content_sha256,
    }


def _rebuild_wiki_download(
    download: HostedReceipt,
    material: list[tuple[HostedObjectBoundary, bytes]],
    manifest: dict[str, object],
) -> HostedReceipt:
    manifest_data = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    manifest_index = next(
        index
        for index, (boundary, _data) in enumerate(material)
        if boundary.object_type == "wiki_mirror_inventory"
    )
    material[manifest_index] = (
        _typed_boundary(
            manifest_data,
            object_type="wiki_mirror_inventory",
            object_id="wiki/mirror-inventory/v1",
        ),
        manifest_data,
    )
    boundaries = tuple(
        _typed_boundary(
            data,
            object_type=boundary.object_type or "invalid",
            object_id=boundary.object_id or "invalid",
        )
        for boundary, data in material
    )
    return replace(
        download,
        chunks=tuple(data for _boundary, data in material),
        object_boundaries=boundaries,
        expected_object_count=len(boundaries),
    )


def _expected_finding_digest(candidate: bytes) -> str:
    return hashlib.sha256(b"provider_token\0" + candidate).hexdigest()


def test_repo_all_history_scans_commit_tree_optional_header_and_signature(
    tmp_path: Path,
) -> None:
    source = _make_source(
        tmp_path,
        name="marker-source",
        filename=_PATH_MARKER,
        message=_MESSAGE_MARKER + b"\n",
        optional_header=_HEADER_MARKER,
        signature_marker=_SIGNATURE_MARKER,
    )
    remote = _publish_remote(source, tmp_path / "marker-remote.git", pull_ref=True)

    result = scan_git_repository(
        str(remote),
        _policy(),
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=_DelegatingRunner(),
    )

    finding_digests = {finding.evidence_sha256 for finding in result.findings}
    assert result.blockers == ()
    assert {
        _expected_finding_digest(_MESSAGE_MARKER),
        _expected_finding_digest(_PATH_MARKER),
        _expected_finding_digest(_HEADER_MARKER),
        _expected_finding_digest(_SIGNATURE_MARKER),
    }.issubset(finding_digests)
    serialized = result.model_dump_json()
    assert all(marker.decode("ascii") not in serialized for marker in (
        _MESSAGE_MARKER,
        _PATH_MARKER,
        _HEADER_MARKER,
        _SIGNATURE_MARKER,
    ))


def test_wiki_scan_covers_commit_tree_optional_header_and_signature(
    tmp_path: Path,
) -> None:
    source = _make_source(
        tmp_path,
        name="marker-wiki-source",
        filename=_PATH_MARKER,
        message=_MESSAGE_MARKER + b"\n",
        optional_header=_HEADER_MARKER,
        signature_marker=_SIGNATURE_MARKER,
    )
    remote = _publish_remote(source, tmp_path / "marker-wiki.git")

    query, download = _wiki_receipts(remote)
    result = _scan_wiki(query, download)

    finding_digests = {finding.evidence_sha256 for finding in result.findings}
    assert query.complete is True
    assert download.complete is True
    assert result.blockers == ()
    assert {
        _expected_finding_digest(_MESSAGE_MARKER),
        _expected_finding_digest(_PATH_MARKER),
        _expected_finding_digest(_HEADER_MARKER),
        _expected_finding_digest(_SIGNATURE_MARKER),
    }.issubset(finding_digests)
    serialized = result.model_dump_json()
    assert all(
        marker.decode("ascii") not in serialized
        for marker in (
            _MESSAGE_MARKER,
            _PATH_MARKER,
            _HEADER_MARKER,
            _SIGNATURE_MARKER,
        )
    )


def test_canonical_git_object_helpers_parse_raw_tree_records_exactly() -> None:
    oid = bytes.fromhex("12" * 20)
    entry = b"100644 safe-name\0" + oid
    verifier = getattr(scanner_module, "_canonical_git_object_oid", None)
    parser = getattr(scanner_module, "_parse_canonical_git_tree", None)

    assert callable(verifier)
    assert callable(parser)
    parsed = parser(entry, "sha1")
    assert [(item.mode, item.object_type, item.object_id, item.name) for item in parsed] == [
        ("100644", "blob", oid.hex(), b"safe-name")
    ]
    raw_name_entry = b"100644 \xffraw-name\0" + oid
    assert parser(raw_name_entry, "sha1")[0].name == b"\xffraw-name"
    assert verifier(b"payload", "blob", "sha1") == hashlib.sha1(  # noqa: S324
        b"blob 7\0payload",
        usedforsecurity=False,
    ).hexdigest()

    with pytest.raises(ReleaseGateError, match="^git_tree_inventory_malformed$"):
        parser(entry + entry, "sha1")
    with pytest.raises(ReleaseGateError, match="^git_tree_inventory_malformed$"):
        parser(entry + b"unaccounted", "sha1")
    with pytest.raises(ReleaseGateError, match="^git_tree_inventory_malformed$"):
        parser(b"100600 unsafe\0" + oid, "sha1")


def test_canonical_object_graph_validation_is_iterative_within_policy() -> None:
    validator = scanner_module._validate_canonical_git_object_corpus
    public_object = scanner_module._GitPublicObject
    blob = b"safe deep graph root"
    target_oid = scanner_module._canonical_git_object_oid(blob, "blob", "sha1")
    objects = [public_object("blob", target_oid, blob)]
    target_type = "blob"
    for index in range(1_100):
        tag = (
            f"object {target_oid}\n"
            f"type {target_type}\n"
            f"tag proof-{index}\n"
            "tagger Release Test <release@example.invalid> 0 +0000\n"
            "\n"
            "safe nested tag\n"
        ).encode("ascii")
        target_oid = scanner_module._canonical_git_object_oid(tag, "tag", "sha1")
        objects.append(public_object("tag", target_oid, tag))
        target_type = "tag"

    corpus = validator(
        objects,
        object_format="sha1",
        root_oids=(target_oid,),
        max_objects=2_000,
        max_commits=1,
        max_tree_entries=1,
        require_canonical_order=True,
    )

    assert len(corpus.objects) == 1_101


def test_wiki_inventory_binds_every_reachable_git_object_category_once(
    tmp_path: Path,
) -> None:
    source = _make_source(
        tmp_path,
        name="complete-wiki-source",
        annotated_tag=True,
    )
    remote = _publish_remote(source, tmp_path / "complete-wiki.git")

    query, download = _wiki_receipts(remote)
    material = _materialize(download)
    manifest_data = next(
        data
        for boundary, data in material
        if boundary.object_type == "wiki_mirror_inventory"
    )
    manifest = json.loads(manifest_data)
    payload_boundaries = tuple(
        boundary
        for boundary, _data in material
        if isinstance(boundary.object_type, str)
        and boundary.object_type.startswith("wiki_reachable_")
    )
    payload_types = tuple(boundary.object_type for boundary in payload_boundaries)

    assert query.complete is True
    assert download.complete is True
    assert set(payload_types) == set(_CATEGORY_ORDER)
    assert payload_types == tuple(
        boundary.object_type
        for boundary in sorted(
            payload_boundaries,
            key=lambda item: _CATEGORY_ORDER[item.object_type],
        )
    )
    assert len({boundary.object_id for boundary in payload_boundaries}) == len(
        payload_boundaries
    )
    assert manifest["reachable_object_count"] == len(payload_boundaries)
    assert manifest["payload_object_count"] == len(payload_boundaries)
    assert manifest["reachable_blob_count"] == payload_types.count(
        "wiki_reachable_blob"
    )
    assert manifest["reachable_tag_count"] == payload_types.count(
        "wiki_reachable_tag"
    )
    assert manifest["reachable_commit_count"] == payload_types.count(
        "wiki_reachable_commit"
    )
    assert manifest["reachable_tree_count"] == payload_types.count(
        "wiki_reachable_tree"
    )
    assert _scan_wiki(query, download).blockers == ()


@pytest.mark.parametrize(
    "failure",
    ("omit-commit", "reorder-categories", "extra-unreachable-object"),
)
def test_wiki_consumer_rejects_self_consistent_object_graph_tampering(
    tmp_path: Path,
    failure: str,
) -> None:
    source = _make_source(
        tmp_path,
        name=f"tamper-{failure}-source",
        annotated_tag=True,
    )
    remote = _publish_remote(source, tmp_path / f"tamper-{failure}.git")
    query, download = _wiki_receipts(remote)
    material = list(_materialize(download))
    manifest_data = next(
        data
        for boundary, data in material
        if boundary.object_type == "wiki_mirror_inventory"
    )
    manifest = json.loads(manifest_data)
    payload_entries = manifest["payload_objects"]
    assert isinstance(payload_entries, list)

    if failure == "omit-commit":
        material = [
            item
            for item in material
            if item[0].object_type != "wiki_reachable_commit"
        ]
        payload_entries[:] = [
            entry
            for entry in payload_entries
            if entry["object_type"] != "wiki_reachable_commit"
        ]
        manifest["reachable_commit_count"] = 0
        manifest["reachable_object_count"] -= 1
        manifest["payload_object_count"] -= 1
    elif failure == "reorder-categories":
        commit_index = next(
            index
            for index, (boundary, _data) in enumerate(material)
            if boundary.object_type == "wiki_reachable_commit"
        )
        tree_index = next(
            index
            for index, (boundary, _data) in enumerate(material)
            if boundary.object_type == "wiki_reachable_tree"
        )
        material[commit_index], material[tree_index] = (
            material[tree_index],
            material[commit_index],
        )
        commit_entry_index = next(
            index
            for index, entry in enumerate(payload_entries)
            if entry["object_type"] == "wiki_reachable_commit"
        )
        tree_entry_index = next(
            index
            for index, entry in enumerate(payload_entries)
            if entry["object_type"] == "wiki_reachable_tree"
        )
        payload_entries[commit_entry_index], payload_entries[tree_entry_index] = (
            payload_entries[tree_entry_index],
            payload_entries[commit_entry_index],
        )
    else:
        extra = b"safe but unreachable wiki object"
        extra_oid = scanner_module._canonical_git_object_oid(extra, "blob", "sha1")
        extra_boundary = _typed_boundary(
            extra,
            object_type="wiki_reachable_blob",
            object_id=f"git/blob/{extra_oid}",
        )
        commit_index = next(
            index
            for index, (boundary, _data) in enumerate(material)
            if boundary.object_type == "wiki_reachable_commit"
        )
        material.insert(commit_index, (extra_boundary, extra))
        commit_entry_index = next(
            index
            for index, entry in enumerate(payload_entries)
            if entry["object_type"] == "wiki_reachable_commit"
        )
        payload_entries.insert(commit_entry_index, _manifest_entry(extra_boundary))
        manifest["reachable_blob_count"] += 1
        manifest["reachable_object_count"] += 1
        manifest["payload_object_count"] += 1

    tampered = _rebuild_wiki_download(download, material, manifest)
    result = _scan_wiki(query, tampered)
    serialized = result.model_dump_json()

    assert f"hosted_archive_boundary_invalid:{_WIKI_SURFACE}" in result.blockers
    assert all(
        not boundary.object_id or boundary.object_id not in serialized
        for boundary in tampered.object_boundaries or ()
    )


@pytest.mark.parametrize("digest_key", ("sha256", "digest"))
def test_raw_marketplace_digest_like_key_is_not_an_entropy_exemption(
    digest_key: str,
) -> None:
    payload = json.dumps({digest_key: _RAW_DIGEST.decode("ascii")}).encode("ascii")
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

    result = scan_hosted_surface(
        "marketplace_snapshot",
        _InspectionClient(HostedInspection((receipt,), HOSTED_SCANNER_VERSION)),
        _policy(),
    )

    assert any(finding.rule == "high_entropy" for finding in result.findings)
    assert _RAW_DIGEST.decode("ascii") not in result.model_dump_json()


def test_raw_wiki_blob_digest_like_key_is_not_an_entropy_exemption(
    tmp_path: Path,
) -> None:
    source = _make_source(
        tmp_path,
        name="digest-wiki-source",
        content=json.dumps({"sha256": _RAW_DIGEST.decode("ascii")}).encode("ascii"),
    )
    remote = _publish_remote(source, tmp_path / "digest-wiki.git")

    query, download = _wiki_receipts(remote)
    result = _scan_wiki(query, download)

    assert query.complete is True
    assert download.complete is True
    assert result.blockers == ()
    assert any(finding.rule == "high_entropy" for finding in result.findings)
    assert _RAW_DIGEST.decode("ascii") not in result.model_dump_json()


def test_validated_internal_wiki_manifest_and_clean_git_corpus_remain_clean(
    tmp_path: Path,
) -> None:
    source = _make_source(
        tmp_path,
        name="clean-wiki-source",
        annotated_tag=True,
    )
    remote = _publish_remote(source, tmp_path / "clean-wiki.git")

    query, download = _wiki_receipts(remote)
    result = _scan_wiki(query, download)

    assert query.complete is True
    assert download.complete is True
    assert result.blockers == ()
    assert result.findings == ()


@pytest.mark.parametrize("object_format", ("sha1", "sha256"))
def test_canonical_clean_repository_is_positive_for_each_git_object_format(
    tmp_path: Path,
    object_format: str,
) -> None:
    source = _make_source(
        tmp_path,
        name=f"clean-{object_format}-source",
        annotated_tag=True,
        object_format=object_format,
    )
    remote = _publish_remote(
        source,
        tmp_path / f"clean-{object_format}.git",
        pull_ref=True,
    )

    result = scan_git_repository(
        str(remote),
        _policy(),
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=_DelegatingRunner(),
    )

    assert result.blockers == ()
    assert result.findings == ()
    assert result.object_count == 4
    assert result.blob_count == 1
