from __future__ import annotations

import io
import json
import lzma
import os
import re
import stat
import struct
import subprocess
import tarfile
import zipfile
import zlib
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    GhApiHostedClient,
    HostedHttpResponse,
    PublicMcpHostedClient,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy
from mercury_tools.release.scanner import CommandResult, SubprocessCommandRunner

_BLOB_TOKEN = b"ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"
_TREE_BLOB_TOKEN = b"ghp_R4t5Y6u7I8o9P0a1S2d3F4g5H6j7K8l9"
_TAG_TOKEN = b"ghp_Q1w2E3r4T5y6U7i8O9p0A1s2D3f4G5h6"
_NESTED_TAG_TOKEN = b"ghp_Z9x8C7v6B5n4M3a2S1d0F9g8H7j6K5l4"


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS)
    return policy.model_copy(update=updates)


def _git(
    *args: str,
    cwd: Path | None = None,
    input_bytes: bytes | None = None,
) -> str:
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
    return completed.stdout.decode("utf-8").strip()


class GitScannerRunner:
    def __init__(self, *, truncate_graph: bool = False) -> None:
        self._delegate = SubprocessCommandRunner()
        self._truncate_graph = truncate_graph
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        self.calls.append(argv)
        executable = Path(argv[0]).name
        if executable == "gitleaks":
            return CommandResult(0, b"[]", b"")
        if executable == "trufflehog":
            return CommandResult(0, b"", b"")
        result = self._delegate.run(
            argv,
            cwd=cwd,
            input_bytes=input_bytes,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
        )
        if self._truncate_graph and argv[1:3] == ("rev-list", "--objects"):
            records = result.stdout.split(b"\0")
            result = CommandResult(
                result.exit_code,
                b"\0".join(records[:-3]) + b"\0",
                result.stderr,
            )
        return result


def _make_non_commit_ref_remote(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "README.md").write_text("safe release fixture\n", encoding="utf-8")
    _git("add", "README.md", cwd=source)
    _git("commit", "-m", "initial", cwd=source)
    blob_oid = _git("hash-object", "-w", "--stdin", cwd=source, input_bytes=_BLOB_TOKEN)
    _git("update-ref", "refs/tags/blob-secret", blob_oid, cwd=source)
    tree_blob_oid = _git(
        "hash-object",
        "-w",
        "--stdin",
        cwd=source,
        input_bytes=_TREE_BLOB_TOKEN,
    )
    tree_oid = _git(
        "mktree",
        cwd=source,
        input_bytes=f"100644 blob {tree_blob_oid}\ttree-secret.bin\n".encode(),
    )
    _git("update-ref", "refs/tags/tree-secret", tree_oid, cwd=source)
    _git(
        "tag",
        "-a",
        "annotated-secret",
        blob_oid,
        "-m",
        _TAG_TOKEN.decode("ascii"),
        cwd=source,
    )
    _git(
        "tag",
        "-a",
        "nested-annotated-secret",
        "annotated-secret",
        "-m",
        _NESTED_TAG_TOKEN.decode("ascii"),
        cwd=source,
    )
    _git("clone", "--bare", str(source), str(remote))
    head = _git("rev-parse", "HEAD", cwd=source)
    _git("--git-dir", str(remote), "update-ref", "refs/pull/1/head", head)
    return remote


def _scan_remote(remote: Path, runner: GitScannerRunner):
    return scanner_module.scan_git_repository(
        str(remote),
        _policy(),
        scanner_binaries={
            "gitleaks": Path("/mock/gitleaks"),
            "trufflehog": Path("/mock/trufflehog"),
        },
        command_runner=runner,
    )


def test_git_inventory_scans_noncommit_refs_and_recursive_tag_payloads(
    tmp_path: Path,
) -> None:
    remote = _make_non_commit_ref_remote(tmp_path)
    runner = GitScannerRunner()

    result = _scan_remote(remote, runner)

    provider_findings = [
        finding for finding in result.findings if finding.rule == "provider_token"
    ]
    assert result.blockers == ()
    assert len(provider_findings) >= 4
    assert any(argv[1:3] == ("rev-list", "--objects") for argv in runner.calls)
    assert _BLOB_TOKEN.decode("ascii") not in result.model_dump_json()
    assert _TREE_BLOB_TOKEN.decode("ascii") not in result.model_dump_json()
    assert _TAG_TOKEN.decode("ascii") not in result.model_dump_json()
    assert _NESTED_TAG_TOKEN.decode("ascii") not in result.model_dump_json()


def test_git_inventory_blocks_an_unreconciled_reachable_object(tmp_path: Path) -> None:
    remote = _make_non_commit_ref_remote(tmp_path)

    result = _scan_remote(remote, GitScannerRunner(truncate_graph=True))

    assert "git_object_inventory_incomplete" in result.blockers


class WikiRunner:
    def __init__(self, remote: Path, *, mismatch_local_refs: bool = False) -> None:
        self._remote = remote
        self._mismatch_local_refs = mismatch_local_refs
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
            str(self._remote) if value.endswith(".wiki.git") else value for value in argv
        )
        result = self._delegate.run(
            rewritten,
            cwd=cwd,
            input_bytes=input_bytes,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
        )
        if self._mismatch_local_refs and argv[1] == "for-each-ref" and result.exit_code == 0:
            output = re.sub(rb"\t[0-9a-f]{40}\t", b"\t" + b"f" * 40 + b"\t", result.stdout, count=1)
            return CommandResult(0, output, result.stderr)
        return result


def _make_binary_wiki_remote(tmp_path: Path) -> Path:
    source = tmp_path / "wiki-source"
    remote = tmp_path / "wiki-remote.git"
    source.mkdir()
    _git("init", "-b", "main", cwd=source)
    (source / "Binary.md").write_bytes(b"\0binary\0" + _BLOB_TOKEN + b"\0")
    _git("add", "Binary.md", cwd=source)
    _git("commit", "-m", "binary page", cwd=source)
    _git(
        "tag",
        "-a",
        "wiki-payload",
        "-m",
        _TAG_TOKEN.decode("ascii"),
        cwd=source,
    )
    _git("clone", "--bare", str(source), str(remote))
    return remote


def test_wiki_receipt_contains_every_reachable_binary_blob(tmp_path: Path) -> None:
    remote = _make_binary_wiki_remote(tmp_path)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=WikiRunner(remote),
        repo="owner/repository",
    )

    query, download = client._wiki_receipts(_policy())

    assert query.complete is True
    assert download.complete is True
    assert any(_BLOB_TOKEN in chunk for chunk in download.chunks)
    assert any(_TAG_TOKEN in chunk for chunk in download.chunks)


def test_wiki_receipt_reconciles_exact_remote_and_mirror_ref_maps(
    tmp_path: Path,
) -> None:
    remote = _make_binary_wiki_remote(tmp_path)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=WikiRunner(remote, mismatch_local_refs=True),
        repo="owner/repository",
    )

    _query, download = client._wiki_receipts(_policy())

    assert download.complete is False


def _zip_info(name: str, *, directory: bool = False) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    member_type = stat.S_IFDIR if directory else stat.S_IFREG
    permissions = 0o755 if directory else 0o644
    info.external_attr = (member_type | permissions) << 16
    return info


def _write_zip(path: Path, *, data: bytes = b"safe") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(_zip_info("safe.txt"), data)


def _write_tar(path: Path, *, mode: str = "w:gz") -> None:
    with tarfile.open(path, mode) as archive:
        data = b"safe"
        info = tarfile.TarInfo("safe.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def _write_artifact_set(root: Path) -> None:
    root.mkdir()
    _write_zip(root / "mercury_tools-0.2.1-py3-none-any.whl")
    _write_tar(root / "mercury_tools-0.2.1.tar.gz")
    _write_zip(root / "mercury-finance-plugin.zip")
    _write_zip(root / "mercury-tools-source.zip")


@pytest.mark.parametrize(
    "metadata_location",
    ["archive_comment", "entry_comment", "entry_extra", "trailing_bytes"],
)
def test_zip_public_metadata_and_trailing_bytes_are_scanned(
    metadata_location: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = _zip_info("safe.txt")
        if metadata_location == "entry_comment":
            info.comment = _BLOB_TOKEN
        if metadata_location == "entry_extra":
            info.extra = struct.pack("<HH", 0xCAFE, len(_BLOB_TOKEN)) + _BLOB_TOKEN
        archive.writestr(info, b"safe")
        if metadata_location == "archive_comment":
            archive.comment = _BLOB_TOKEN
    if metadata_location == "trailing_bytes":
        with source.open("ab") as stream:
            stream.write(_BLOB_TOKEN)

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "provider_token" for finding in result.findings)
    if metadata_location == "trailing_bytes":
        assert "artifact_unparsed_data:source" in result.blockers


def test_zip_trailing_empty_eocd_cannot_hide_the_original_archive(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.zip"
    with source.open("ab") as stream:
        stream.write(struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0, 0, 0, 0, 0))

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unparsed_data:source" in result.blockers


def test_tar_xz_sidecar_is_detected_from_bytes_and_fully_scanned(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    sidecar = root / "public-history.tar.xz"
    with tarfile.open(sidecar, "w:xz") as archive:
        data = _BLOB_TOKEN
        info = tarfile.TarInfo("nested/.env")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "forbidden_path" for finding in result.findings)
    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "artifact_opaque_sidecar" not in result.blockers


@pytest.mark.parametrize("mode", ["w:gz", "w:bz2", "w:xz"])
def test_compressed_tar_variants_are_detected_without_archive_suffix(
    mode: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    sidecar = root / f"history-{mode.removeprefix('w:')}.payload"
    with tarfile.open(sidecar, mode) as archive:
        info = tarfile.TarInfo("nested/.env")
        info.size = len(_BLOB_TOKEN)
        archive.addfile(info, io.BytesIO(_BLOB_TOKEN))

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "forbidden_path" for finding in result.findings)
    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert "artifact_opaque_sidecar" not in result.blockers


def test_gzip_optional_header_metadata_is_part_of_the_public_corpus(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    payload = compressor.compress(b"safe") + compressor.flush()
    header = b"\x1f\x8b\x08\x10" + b"\0" * 6 + _BLOB_TOKEN + b"\0"
    trailer = struct.pack("<II", zlib.crc32(b"safe"), len(b"safe"))
    (root / "history.payload").write_bytes(header + payload + trailer)

    result = scanner_module.scan_artifacts(root, _policy())

    assert any(finding.rule == "provider_token" for finding in result.findings)


def test_tar_hidden_pax_extension_header_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    sidecar = root / "pax-history.payload"
    with tarfile.open(sidecar, "w:xz", format=tarfile.PAX_FORMAT) as archive:
        info = tarfile.TarInfo("safe.txt")
        info.pax_headers = {"comment": "public metadata"}
        info.size = len(b"safe")
        archive.addfile(info, io.BytesIO(b"safe"))

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unsafe_member:sidecar" in result.blockers


def test_tar_zero_blocks_inside_regular_payload_are_not_treated_as_eof(
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    sidecar = root / "zero-history.payload"
    with tarfile.open(sidecar, "w:xz") as archive:
        data = b"\0" * 1024 + b"A" * 512
        info = tarfile.TarInfo("safe.bin")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unparsed_data:sidecar" not in result.blockers


def test_required_wheel_rejects_non_zip_bytes(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    wheel = root / "mercury_tools-0.2.1-py3-none-any.whl"
    with tarfile.open(wheel, "w") as archive:
        info = tarfile.TarInfo("safe.txt")
        info.size = len(b"safe")
        archive.addfile(info, io.BytesIO(b"safe"))

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_read_failed:wheel" in result.blockers


def test_tar_compression_suffix_requires_a_tar_payload(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    compressor = zlib.compressobj(level=9, wbits=16 + zlib.MAX_WBITS)
    (root / "mercury_tools-0.2.1.tar.gz").write_bytes(
        compressor.compress(b"safe") + compressor.flush()
    )
    (root / "opaque-history.tar.xz").write_bytes(lzma.compress(b"safe"))

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_read_failed:sdist" in result.blockers
    assert "artifact_read_failed:sidecar" in result.blockers


def test_opaque_archive_like_sidecar_blocks_release(tmp_path: Path) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    (root / "public-history.payload").write_bytes(b"7z\xbc\xaf'\x1c" + b"opaque")

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_opaque_sidecar" in result.blockers


class CallbackHttpTransport:
    def __init__(
        self,
        handler: Callable[[dict[str, object]], HostedHttpResponse],
    ) -> None:
        self._handler = handler
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None = None,
        max_bytes: int,
    ) -> HostedHttpResponse:
        call = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "json_body": json_body,
            "max_bytes": max_bytes,
        }
        self.calls.append(call)
        return self._handler(call)


def _valid_initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "test-server", "version": "1.0.0"},
    }


def _lifecycle_transport(
    initialize_result: dict[str, object],
    *,
    initialized_status: int = 202,
) -> CallbackHttpTransport:
    def handler(call: dict[str, object]) -> HostedHttpResponse:
        body = call["json_body"]
        assert isinstance(body, dict)
        method = body["method"]
        if method == "initialize":
            payload = {
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": initialize_result,
            }
            return HostedHttpResponse(200, json.dumps(payload).encode(), {})
        if method == "notifications/initialized":
            return HostedHttpResponse(initialized_status, b"", {})
        raise AssertionError("tools inventory must not affect lifecycle rejection")

    return CallbackHttpTransport(handler)


@pytest.mark.parametrize(
    "initialize_result",
    [
        {
            **_valid_initialize_result(),
            "protocolVersion": "2099-01-01",
        },
        {
            key: value
            for key, value in _valid_initialize_result().items()
            if key != "capabilities"
        },
        {
            key: value
            for key, value in _valid_initialize_result().items()
            if key != "serverInfo"
        },
        {
            **_valid_initialize_result(),
            "capabilities": {},
        },
    ],
)
def test_mcp_initialize_rejects_unsupported_or_incomplete_contracts(
    initialize_result: dict[str, object],
) -> None:
    transport = _lifecycle_transport(initialize_result)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    inspection = client.inspect("public_mcp_responses", _policy())

    initialize = inspection.receipts[0]
    assert initialize.name == "public_mcp_initialize"
    assert initialize.complete is False
    assert [call["json_body"]["method"] for call in transport.calls] == ["initialize"]  # type: ignore[index]


def test_mcp_initialized_notification_requires_exact_http_202() -> None:
    transport = _lifecycle_transport(_valid_initialize_result(), initialized_status=200)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    inspection = client.inspect("public_mcp_responses", _policy())

    response_stream = inspection.receipts[2]
    assert response_stream.name == "public_mcp_response_stream"
    assert response_stream.complete is False
    assert [call["json_body"]["method"] for call in transport.calls] == [  # type: ignore[index]
        "initialize",
        "notifications/initialized",
    ]


@pytest.mark.parametrize("mutation", ["insert", "remove", "rename"])
def test_directory_manifest_detects_membership_races(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "a-target.txt"
    target.write_text("safe", encoding="utf-8")
    (root / "z-trigger.txt").write_text("safe", encoding="utf-8")
    original_scan = scanner_module._scan_bytes
    mutated = False

    def racing_scan(
        data: bytes,
        relative_path: str,
        policy: SecretScanPolicy,
    ):
        nonlocal mutated
        if relative_path == "z-trigger.txt" and not mutated:
            mutated = True
            if mutation == "insert":
                (root / ".env").write_bytes(_BLOB_TOKEN)
            elif mutation == "remove":
                target.unlink()
            else:
                target.rename(root / "renamed.txt")
        return original_scan(data, relative_path, policy)

    monkeypatch.setattr(scanner_module, "_scan_bytes", racing_scan)

    result = scanner_module.scan_filesystem(root, _policy())

    assert "filesystem_traversal_failed" in result.blockers


@pytest.mark.parametrize("member_type", [tarfile.CONTTYPE, tarfile.GNUTYPE_SPARSE])
def test_local_tar_rejects_contiguous_and_sparse_members(
    member_type: bytes,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.tar.gz"
    (root / "mercury-tools-source.zip").unlink()
    with tarfile.open(source, "w:gz") as archive:
        info = tarfile.TarInfo("special")
        info.type = member_type
        info.size = 0
        archive.addfile(info, io.BytesIO())

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unsafe_member:source" in result.blockers


@pytest.mark.parametrize("member_type", [tarfile.CONTTYPE, tarfile.GNUTYPE_SPARSE])
def test_hosted_tar_rejects_contiguous_and_sparse_members(member_type: bytes) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("special")
        info.type = member_type
        info.size = 0
        archive.addfile(info, io.BytesIO())

    _findings, blockers = hosted_module._scan_hosted_tar(
        buffer.getvalue(),
        "github_releases_and_assets",
        _policy(),
        hosted_module._HostedArchiveBudget(1024 * 1024),
    )

    assert "hosted_archive_unsafe:github_releases_and_assets" in blockers


@pytest.mark.parametrize(
    ("create_system", "member_type", "name", "expected"),
    [
        (3, stat.S_IFREG, "file.txt", True),
        (3, stat.S_IFDIR, "directory/", True),
        (3, 0, "file.txt", False),
        (3, stat.S_IFIFO, "special", False),
        (99, stat.S_IFREG, "file.txt", False),
    ],
)
def test_zip_type_proof_accepts_only_known_regular_files_and_directories(
    create_system: int,
    member_type: int,
    name: str,
    expected: bool,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = create_system
    info.external_attr = (member_type | 0o644) << 16

    assert scanner_module._zip_entry_is_safe_type(info) is expected
