from __future__ import annotations

import gzip
import hashlib
import inspect
import io
import json
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest
from test_release_artifacts import (
    ROOT,
    VERSION,
    _run,
    install_task13_runner,
    make_release_tree,
    passing_task13_report,
)

from mercury_tools import cli
from mercury_tools.release import artifacts as release_artifacts
from mercury_tools.release.artifacts import _zip_datetime, build_release_artifacts
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
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=_zip_datetime(epoch))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)


def _write_normalized_tar(path: Path, members: list[tuple[str, bytes]], epoch: int) -> None:
    with (
        path.open("wb") as raw,
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


def test_release_tree_rejects_moving_plugin_ref(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    path = root / "plugins/mercury-finance/.mcp.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mcpServers"]["mercury-finance"]["args"][1] = (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@main"
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _run(["git", "add", str(path.relative_to(root))], cwd=root)
    _run(["git", "commit", "-m", "moving plugin ref"], cwd=root)

    with pytest.raises(ReleaseGateError, match="^plugin_ref_not_immutable$"):
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


def test_cli_release_verify_keeps_current_v020_tree_blocked(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = cli.main(
        [
            "release",
            "verify",
            "--version",
            VERSION,
            "--artifacts",
            str(tmp_path / "artifacts"),
            "--repo-root",
            str(ROOT),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload == {"status": "error", "error": "release_version_mismatch"}
