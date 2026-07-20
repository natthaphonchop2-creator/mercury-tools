from __future__ import annotations

import hashlib
import inspect
import io
import json
import os
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from test_release_artifacts import (
    VERSION,
    _commit_release_tree,
    _run,
    install_task13_runner,
    make_release_tree,
    make_v022_release_tree,
    passing_task13_report,
)

from mercury_tools import cli
from mercury_tools.release import artifacts as release_artifacts
from mercury_tools.release import verify as release_verify
from mercury_tools.release.artifacts import (
    _zip_datetime,
    build_release_artifacts,
)
from mercury_tools.release.scanner import ReleaseGateError
from mercury_tools.release.verify import (
    build_public_staging,
    verify_release,
    verify_release_tree,
)


def _verify(root: Path, artifacts: Path) -> None:
    verify_release(
        root=root,
        version=VERSION,
        artifacts=artifacts,
    )


def _refresh_manifest_entry(artifacts: Path, file_name: str) -> None:
    path = artifacts / file_name
    manifest_path = artifacts / "SHA256SUMS.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in manifest["artifacts"]:
        if item["file_name"] == file_name:
            item["size"] = path.stat().st_size
            item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            break
    else:
        raise AssertionError(f"missing artifact manifest entry: {file_name}")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _archive_members(path: Path) -> list[tuple[str, bytes]]:
    if path.suffix in {".whl", ".zip"}:
        with zipfile.ZipFile(path) as archive:
            return [
                (member.filename, archive.read(member))
                for member in archive.infolist()
                if not member.is_dir()
            ]
    with tarfile.open(path, mode="r:gz") as archive:
        result: list[tuple[str, bytes]] = []
        for member in archive.getmembers():
            if member.isdir():
                continue
            assert member.isfile()
            stream = archive.extractfile(member)
            assert stream is not None
            result.append((member.name, stream.read()))
        return result


def _write_normalized_zip(path: Path, members: list[tuple[str, bytes]], epoch: int) -> None:
    with zipfile.ZipFile(
        path,
        mode="w",
        compression=zipfile.ZIP_STORED,
        strict_timestamps=True,
    ) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=_zip_datetime(epoch))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, data, compress_type=zipfile.ZIP_STORED)


def _write_normalized_tar(path: Path, members: list[tuple[str, bytes]], epoch: int) -> None:
    with (
        path.open("wb") as raw,
        release_artifacts._DeterministicGzipWriter(raw, epoch) as compressed,
        tarfile.open(
            fileobj=compressed,
            mode="w",
            format=tarfile.GNU_FORMAT,
        ) as archive,
    ):
        for name, data in members:
            metadata = tarfile.TarInfo(name)
            metadata.size = len(data)
            metadata.mode = 0o644
            metadata.uid = 0
            metadata.gid = 0
            metadata.uname = ""
            metadata.gname = ""
            metadata.mtime = epoch
            metadata.type = tarfile.REGTYPE
            archive.addfile(metadata, io.BytesIO(data))


def _rewrite_archive_with_unrelated_payload(path: Path, epoch: int) -> None:
    members = _archive_members(path)
    assert members
    members[0] = (members[0][0], b"candidate-unrelated-payload\n")
    if path.suffix in {".whl", ".zip"}:
        _write_normalized_zip(path, members, epoch)
    else:
        _write_normalized_tar(path, members, epoch)


def test_release_verifier_accepts_version_consistent_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    calls = install_task13_runner(monkeypatch)
    built = build_release_artifacts(
        root,
        version=VERSION,
        output=artifacts,
    )

    verification = verify_release(
        root=root,
        version=VERSION,
        artifacts=artifacts,
    )

    assert verification.passed is True
    assert verification.version == VERSION
    assert verification.commit_sha == built.commit_sha
    assert len(verification.artifact_manifest_sha256) == 64
    assert len(calls) == 2
    assert all(snapshot != root for _candidate, snapshot, _target in calls)
    assert calls[1][2].name == "expected-artifacts"


def test_release_tree_rejects_noncanonical_hosted_plugin_endpoint(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    path = root / "plugins/mercury-finance/.mcp.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mcpServers"]["mercury-finance"]["url"] = "https://example.invalid/mcp"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _commit_release_tree(root, "mutate hosted plugin endpoint")

    with pytest.raises(ReleaseGateError, match="^mcp_server_contract_invalid$"):
        verify_release_tree(root, version=VERSION)


def test_release_tree_does_not_import_local_mcp_from_mutable_worktree(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    local_server = root / "src/mercury_tools/mcp/local_server.py"
    local_server.write_text("raise RuntimeError('mutable worktree import')\n", encoding="utf-8")

    candidate = verify_release_tree(root, version=VERSION)

    assert candidate.commit_sha == _run(["git", "rev-parse", "HEAD"], cwd=root)


def test_verifier_rejects_missing_duplicate_extra_and_digest_mismatched_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    manifest = build_release_artifacts(
        root,
        version=VERSION,
        output=artifacts,
    )
    wheel_name = next(item.file_name for item in manifest.artifacts if item.kind == "wheel")
    wheel = artifacts / wheel_name
    original = wheel.read_bytes()

    wheel.write_bytes(original + b"tampered")
    with pytest.raises(ReleaseGateError, match="^artifact_digest_mismatch$"):
        _verify(root, artifacts)
    wheel.write_bytes(original)

    extra = artifacts / "surplus.txt"
    extra.write_text("unexpected", encoding="utf-8")
    with pytest.raises(ReleaseGateError, match="^artifact_set_invalid$"):
        _verify(root, artifacts)
    extra.unlink()

    duplicate = artifacts / "duplicate-wheel.whl"
    shutil.copyfile(wheel, duplicate)
    with pytest.raises(ReleaseGateError, match="^artifact_set_invalid$"):
        _verify(root, artifacts)
    duplicate.unlink()

    source_name = next(item.file_name for item in manifest.artifacts if item.kind == "source")
    source = artifacts / source_name
    source.unlink()
    with pytest.raises(ReleaseGateError, match="^artifact_set_invalid$"):
        _verify(root, artifacts)


@pytest.mark.parametrize("kind", ("wheel", "sdist", "plugin", "source"))
def test_verifier_rejects_jointly_rewritten_manifest_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    manifest = build_release_artifacts(root, version=VERSION, output=artifacts)
    artifact = next(item for item in manifest.artifacts if item.kind == kind)

    _rewrite_archive_with_unrelated_payload(artifacts / artifact.file_name, manifest.build_epoch)
    _refresh_manifest_entry(artifacts, artifact.file_name)

    with pytest.raises(ReleaseGateError, match="^artifact_candidate_mismatch$"):
        _verify(root, artifacts)


def test_verifier_rechecks_submitted_artifacts_after_task13_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    manifest = build_release_artifacts(root, version=VERSION, output=artifacts)
    artifact = next(item for item in manifest.artifacts if item.kind == "source")

    def mutate_submitted_artifacts(_candidate: object, _snapshot: Path, _target: Path) -> object:
        _rewrite_archive_with_unrelated_payload(
            artifacts / artifact.file_name,
            manifest.build_epoch,
        )
        _refresh_manifest_entry(artifacts, artifact.file_name)
        return passing_task13_report()

    monkeypatch.setattr(
        release_artifacts,
        "_run_task13_artifact_gate",
        mutate_submitted_artifacts,
    )

    with pytest.raises(ReleaseGateError, match="^artifact_candidate_mismatch$"):
        _verify(root, artifacts)


@pytest.mark.parametrize(
    "member_suffixes",
    (
        ("./README.md",),
        ("a/./b", "a/b"),
    ),
)
def test_verifier_rejects_noncanonical_source_archive_members(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    member_suffixes: tuple[str, ...],
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    install_task13_runner(monkeypatch)
    manifest = build_release_artifacts(root, version=VERSION, output=artifacts)
    source = next(item for item in manifest.artifacts if item.kind == "source")
    prefix = f"mercury-tools-{VERSION}"
    members = [(f"{prefix}/{suffix}", b"benign\n") for suffix in member_suffixes]
    _write_normalized_tar(artifacts / source.file_name, sorted(members), manifest.build_epoch)
    _refresh_manifest_entry(artifacts, source.file_name)

    with pytest.raises(ReleaseGateError, match="^artifact_archive_metadata_invalid$"):
        _verify(root, artifacts)


def test_public_release_apis_do_not_accept_scanner_callback_override() -> None:
    for function in (build_release_artifacts, verify_release, build_public_staging):
        assert "scanner_gate" not in inspect.signature(function).parameters


@pytest.mark.parametrize(
    "namespace_violation",
    (
        "foreign_owner",
        "group_writable",
        "world_writable",
        "effective_uid_unavailable",
    ),
)
@pytest.mark.parametrize("api", ("artifact_builder", "public_staging"))
def test_public_release_apis_reject_namespace_before_any_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    namespace_violation: str,
    api: str,
) -> None:
    root = make_release_tree(tmp_path)
    output_parent = tmp_path / "output-parent"
    output_parent.mkdir()
    if namespace_violation == "group_writable":
        output_parent.chmod(0o720)
    elif namespace_violation == "world_writable":
        output_parent.chmod(0o702)
    output = output_parent / "release"
    entered: list[str] = []
    prepared: list[tuple[object, int]] = []
    original_prepare = release_artifacts._prepare_output_destination
    original_fstat = release_artifacts.os.fstat

    def unexpected(name: str):
        def guard(*_args: object, **_kwargs: object) -> object:
            entered.append(name)
            pytest.fail(f"{name} entered before namespace validation")

        return guard

    def prepare(path: Path) -> object:
        destination = original_prepare(path)
        parent_fd = destination.require_parent_fd()
        prepared.append((destination, parent_fd))
        if namespace_violation == "foreign_owner":

            def foreign_owner_fstat(fd: int) -> os.stat_result:
                metadata = original_fstat(fd)
                if fd != parent_fd:
                    return metadata
                return os.stat_result(
                    (
                        metadata.st_mode,
                        metadata.st_ino,
                        metadata.st_dev,
                        metadata.st_nlink,
                        metadata.st_uid + 1,
                        metadata.st_gid,
                        metadata.st_size,
                        metadata.st_atime,
                        metadata.st_mtime,
                        metadata.st_ctime,
                    )
                )

            monkeypatch.setattr(release_artifacts.os, "fstat", foreign_owner_fstat)
        return destination

    monkeypatch.setattr(release_artifacts, "_prepare_output_destination", prepare)
    monkeypatch.setattr(release_verify, "_prepare_output_destination", prepare)
    if namespace_violation == "effective_uid_unavailable":
        monkeypatch.delattr(release_artifacts.os, "geteuid", raising=False)

    for module in (release_artifacts, release_verify):
        monkeypatch.setattr(module, "load_release_candidate", unexpected("load_candidate"))
        monkeypatch.setattr(
            module,
            "materialize_release_candidate",
            unexpected("materialize_candidate"),
        )
        monkeypatch.setattr(module, "_build_artifact_set", unexpected("build_artifacts"))
        monkeypatch.setattr(
            module,
            "require_task13_scanner_gate",
            unexpected("scanner_gate"),
        )

    monkeypatch.setattr(
        release_artifacts,
        "_create_private_staging",
        unexpected("create_private_staging"),
    )
    monkeypatch.setattr(
        release_artifacts,
        "_copy_verified_tree",
        unexpected("copy_verified_tree"),
    )
    monkeypatch.setattr(
        release_verify,
        "_write_candidate_tree",
        unexpected("write_candidate_tree"),
    )
    monkeypatch.setattr(
        release_verify,
        "_initialize_history_free_repository",
        unexpected("initialize_staging_repository"),
    )
    monkeypatch.setattr(
        release_verify,
        "_require_staging_scanner_gate",
        unexpected("staging_scanner_gate"),
    )
    monkeypatch.setattr(
        release_verify,
        "_publish_owned_directory",
        unexpected("publish_staging"),
    )
    monkeypatch.setattr(
        release_artifacts.tempfile,
        "TemporaryDirectory",
        unexpected("temporary_directory"),
    )

    with pytest.raises(ReleaseGateError, match="^release_output_invalid$"):
        if api == "artifact_builder":
            build_release_artifacts(root, version=VERSION, output=output)
        else:
            build_public_staging(root=root, version=VERSION, output=output)

    assert entered == []
    assert len(prepared) == 1
    destination, parent_fd = prepared[0]
    assert destination.parent_fd is None
    with pytest.raises(OSError):
        original_fstat(parent_fd)
    assert not output.exists()


def test_cli_release_verify_keeps_previous_v022_tree_blocked(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = make_v022_release_tree(tmp_path)
    exit_code = cli.main(
        [
            "release",
            "verify",
            "--version",
            VERSION,
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--repo-root",
            str(root),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload == {"status": "error", "error": "release_version_mismatch"}


def _write_junit(path: Path, cases: tuple[str, ...]) -> None:
    path.write_text(
        "<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>",
        encoding="utf-8",
    )


def test_required_cross_filesystem_release_tests_pass_junit_audit(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    _write_junit(
        junit,
        (
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_publish_copies_verified_tree_to_distinct_destination_device"/>',
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_release_artifacts_publish_to_distinct_destination_device"/>',
        ),
    )

    release_verify.verify_required_release_test_skips(junit)


def test_cli_release_verify_test_skips_accepts_known_device_passing_junit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    junit = tmp_path / "pytest.xml"
    _write_junit(
        junit,
        (
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_publish_copies_verified_tree_to_distinct_destination_device"/>',
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_release_artifacts_publish_to_distinct_destination_device"/>',
        ),
    )

    exit_code = cli.main(["release", "verify-test-skips", "--junit", str(junit)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload == {"junit": str(junit), "known_device": True, "status": "ok"}


def test_release_test_skip_audit_allows_only_the_capability_reason_without_known_device(
    tmp_path: Path,
) -> None:
    junit = tmp_path / "pytest.xml"
    _write_junit(
        junit,
        (
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_publish_copies_verified_tree_to_distinct_destination_device">'
            '<skipped message="no_writable_second_device"/></testcase>',
            '<testcase classname="tests.test_release_artifacts" '
            'name="test_release_artifacts_publish_to_distinct_destination_device">'
            '<skipped message="no_writable_second_device"/></testcase>',
        ),
    )

    release_verify.verify_required_release_test_skips(junit, known_device=False)


@pytest.mark.parametrize(
    ("cases", "error"),
    (
        (
            (
                '<testcase classname="tests.test_release_artifacts" '
                'name="test_publish_copies_verified_tree_to_distinct_destination_device">'
                '<skipped message="no_writable_second_device"/></testcase>',
                '<testcase classname="tests.test_release_artifacts" '
                'name="test_release_artifacts_publish_to_distinct_destination_device"/>',
            ),
            "release_test_skip_audit_failed",
        ),
        (
            (
                '<testcase classname="tests.test_release_artifacts" '
                'name="test_publish_copies_verified_tree_to_distinct_destination_device"/>',
            ),
            "release_test_skip_audit_failed",
        ),
        (
            (
                '<testcase classname="tests.test_release_artifacts" '
                'name="test_publish_copies_verified_tree_to_distinct_destination_device">'
                '<failure message="copy failed"/></testcase>',
                '<testcase classname="tests.test_release_artifacts" '
                'name="test_release_artifacts_publish_to_distinct_destination_device"/>',
            ),
            "release_test_skip_audit_failed",
        ),
    ),
)
def test_required_cross_filesystem_release_tests_reject_nonpassing_junit_results(
    tmp_path: Path,
    cases: tuple[str, ...],
    error: str,
) -> None:
    junit = tmp_path / "pytest.xml"
    _write_junit(junit, cases)

    with pytest.raises(ReleaseGateError, match=f"^{error}$"):
        release_verify.verify_required_release_test_skips(junit)


def test_required_cross_filesystem_release_tests_reject_malformed_junit(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text("<testsuite><testcase>", encoding="utf-8")

    with pytest.raises(ReleaseGateError, match="^release_test_skip_audit_invalid$"):
        release_verify.verify_required_release_test_skips(junit)
