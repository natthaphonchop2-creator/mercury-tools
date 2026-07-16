from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.1"
SOURCE_DATE_EPOCH = 1_750_000_000


def _load_script(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


candidate_validator = _load_script(
    "validate_candidate_output",
    ROOT / "scripts" / "validate_candidate_output.py",
)
asset_validator = _load_script(
    "verify_release_assets",
    ROOT / "release-control" / "scaffold" / "scripts" / "verify_release_assets.py",
)


def _json_output(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return json.loads(captured.out)


def _run_candidate(root: Path, capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    exit_code = candidate_validator.main([str(root), *args])
    payload = _json_output(capsys)
    assert set(payload) <= {"status", "code", "files", "bytes"}
    assert exit_code == (0 if payload["status"] == "ok" else 1)
    return payload


@dataclass(frozen=True)
class ReleaseFixture:
    artifacts: Path
    repository: Path
    canonical_source: Path
    reproduced_distributions: Path
    commit_sha: str


def _run(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stderr == ""
    return result.stdout.strip()


def _run_assets(
    fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> dict[str, Any]:
    exit_code = asset_validator.main(
        [
            "verify",
            str(fixture.artifacts),
            str(fixture.repository),
            str(fixture.canonical_source),
            str(fixture.reproduced_distributions),
            fixture.commit_sha,
            VERSION,
            *args,
        ]
    )
    payload = _json_output(capsys)
    assert set(payload) <= {"status", "code", "files", "bytes"}
    assert exit_code == (0 if payload["status"] == "ok" else 1)
    return payload


def _write_manifest(
    root: Path,
    commit_sha: str,
    *,
    manifest: dict[str, Any] | None = None,
) -> None:
    if manifest is None:
        manifest = {
            "artifacts": [],
            "builder_provenance": {"builder": "release-test"},
            "commit_sha": commit_sha,
            "schema_version": 4,
            "source_date_epoch": SOURCE_DATE_EPOCH,
            "version": VERSION,
        }
        for path in sorted(root.iterdir(), key=lambda item: item.name):
            if path.name == "SHA256SUMS.json":
                continue
            payload = path.read_bytes()
            kind = {
                "mercury_tools-0.2.1-py3-none-any.whl": "wheel",
                "mercury_tools-0.2.1.tar.gz": "sdist",
                "mercury-finance-plugin-0.2.1.zip": "plugin",
                "mercury-tools-0.2.1-source.tar.gz": "source",
            }[path.name]
            manifest["artifacts"].append(
                {
                    "build_epoch": SOURCE_DATE_EPOCH,
                    "commit_sha": commit_sha,
                    "file_name": path.name,
                    "kind": kind,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                    "version": VERSION,
                }
            )
        manifest["artifacts"].sort(key=lambda item: item["file_name"])
    (root / "SHA256SUMS.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _write_raw_distributions(root: Path) -> None:
    root.mkdir(mode=0o700)
    wheel = root / "mercury_tools-0.2.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mercury_tools/__init__.py", '__version__ = "0.2.1"\n')
        archive.writestr(
            "mercury_tools-0.2.1.dist-info/METADATA",
            "Metadata-Version: 2.4\nName: mercury-tools\nVersion: 0.2.1\n",
        )
    wheel.chmod(0o600)

    sdist = root / "mercury_tools-0.2.1.tar.gz"
    with tarfile.open(sdist, mode="w:gz") as archive:
        files = {
            "mercury_tools-0.2.1/PKG-INFO": (
                b"Metadata-Version: 2.4\nName: mercury-tools\nVersion: 0.2.1\n"
            ),
            "mercury_tools-0.2.1/src/mercury_tools/__init__.py": (
                b'__version__ = "0.2.1"\n'
            ),
        }
        for name, payload in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = 0o644
            member.mtime = SOURCE_DATE_EPOCH + 123
            archive.addfile(member, io.BytesIO(payload))
    sdist.chmod(0o600)


def _make_source_repository(root: Path) -> tuple[Path, str]:
    repository = root / "source-repository"
    repository.mkdir()
    files = {
        "README.md": b"# Mercury test source\n",
        "pyproject.toml": (
            b"[build-system]\n"
            b'requires = ["setuptools==80.9.0", "wheel==0.45.1"]\n'
            b'build-backend = "setuptools.build_meta"\n\n'
            b"[project]\n"
            b'name = "mercury-tools"\n'
            b'version = "0.2.1"\n'
        ),
        "src/mercury_tools/__init__.py": b'__version__ = "0.2.1"\n',
        "plugins/mercury-finance/.codex-plugin/plugin.json": (
            b'{"name":"mercury-finance","version":"0.2.1"}\n'
        ),
        "plugins/mercury-finance/skills/test/SKILL.md": b"# Test skill\n",
        ".env.example": b"MUST_NOT_BE_PUBLISHED=1\n",
    }
    for name, payload in files.items():
        path = repository / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    _run(["git", "init", "--quiet"], cwd=repository)
    _run(["git", "config", "user.name", "Release Test"], cwd=repository)
    _run(["git", "config", "user.email", "release@example.invalid"], cwd=repository)
    _run(["git", "add", "--all"], cwd=repository)
    environment = {
        **os.environ,
        "GIT_AUTHOR_DATE": f"@{SOURCE_DATE_EPOCH} +0000",
        "GIT_COMMITTER_DATE": f"@{SOURCE_DATE_EPOCH} +0000",
    }
    _run(
        ["git", "commit", "--quiet", "-m", "reviewed source"],
        cwd=repository,
        environment=environment,
    )
    return repository, _run(["git", "rev-parse", "HEAD"], cwd=repository)


def _make_release_fixture(tmp_path: Path) -> ReleaseFixture:
    repository, commit_sha = _make_source_repository(tmp_path)
    canonical_source = tmp_path / "canonical-source"
    files, total, epoch = asset_validator.materialize_trusted_source(
        repository,
        canonical_source,
        commit_sha,
        VERSION,
    )
    assert files == 5
    assert total > 0
    assert epoch == SOURCE_DATE_EPOCH
    assert not (canonical_source / ".env.example").exists()

    reproduced_distributions = tmp_path / "reproduced-distributions"
    _write_raw_distributions(reproduced_distributions)
    expected = tmp_path / "expected-assets"
    expected.mkdir(mode=0o700)
    asset_validator.reproduce_release_assets(
        canonical_source,
        reproduced_distributions,
        expected,
        SOURCE_DATE_EPOCH,
        VERSION,
    )

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(mode=0o700)
    for path in expected.iterdir():
        destination = artifacts / path.name
        shutil.copyfile(path, destination)
        destination.chmod(0o600)
    _write_manifest(artifacts, commit_sha)
    (artifacts / "SHA256SUMS.json").chmod(0o600)
    return ReleaseFixture(
        artifacts=artifacts,
        repository=repository,
        canonical_source=canonical_source,
        reproduced_distributions=reproduced_distributions,
        commit_sha=commit_sha,
    )


@pytest.fixture
def release_fixture(tmp_path: Path) -> ReleaseFixture:
    return _make_release_fixture(tmp_path)


def test_candidate_validator_accepts_bounded_regular_tree(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested").chmod(0o700)
    (tmp_path / "nested" / "output.bin").write_bytes(b"abc")
    (tmp_path / "nested" / "output.bin").chmod(0o600)
    tmp_path.chmod(0o700)

    assert _run_candidate(tmp_path, capsys) == {"status": "ok", "files": 1, "bytes": 3}


@pytest.mark.parametrize("bad_root", ["missing", "file"])
def test_candidate_validator_rejects_absent_or_non_directory_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], bad_root: str
) -> None:
    root = tmp_path / bad_root
    if bad_root == "file":
        root.write_bytes(b"x")

    payload = _run_candidate(root, capsys)

    assert payload["status"] == "error"
    assert payload["code"] == "root_invalid"


def test_candidate_validator_rejects_symlinks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "target").write_bytes(b"x")
    try:
        (root / "link").symlink_to(root / "target")
    except OSError:
        pytest.skip("symlinks are unavailable")

    assert _run_candidate(root, capsys)["code"] == "symlink"


def test_candidate_validator_rejects_symlink_root(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    try:
        root = tmp_path / "root"
        root.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")

    assert _run_candidate(root, capsys)["code"] == "symlink"


def test_candidate_validator_rejects_hardlinks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "one"
    source.write_bytes(b"x")
    try:
        os.link(source, root / "two")
    except OSError:
        pytest.skip("hardlinks are unavailable")

    assert _run_candidate(root, capsys)["code"] == "hardlink"


def test_candidate_validator_rejects_fifo_when_supported(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO creation is unavailable")
    root = tmp_path / "root"
    root.mkdir()
    try:
        os.mkfifo(root / "pipe")
    except OSError:
        pytest.skip("FIFO creation is unavailable")

    assert _run_candidate(root, capsys)["code"] == "special_file"


@pytest.mark.parametrize(
    ("setup", "code"),
    (
        (lambda root: (root / "bad name").write_bytes(b"x"), "unsafe_name"),
        (lambda root: (root / "file").write_bytes(b"x"), "permissions"),
    ),
)
def test_candidate_validator_rejects_unsafe_names_and_permissions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    setup: Callable[[Path], object],
    code: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0o700)
    setup(root)
    if code == "permissions":
        (root / "file").chmod(0o620)

    assert _run_candidate(root, capsys)["code"] == code


def test_candidate_validator_rejects_duplicate_canonical_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    root.chmod(0o700)
    (root / "A").write_bytes(b"a")
    (root / "a").write_bytes(b"b")
    if len(tuple(root.iterdir())) != 2:
        pytest.skip("filesystem is case-insensitive")

    assert _run_candidate(root, capsys)["code"] == "duplicate_name"


def test_candidate_validator_rejects_depth_file_count_and_byte_overflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    current = root
    for index in range(33):
        current = current / f"d{index}"
        current.mkdir()
    (current / "file").write_bytes(b"x")
    assert _run_candidate(root, capsys)["code"] == "depth_overflow"

    shallow = tmp_path / "shallow"
    shallow.mkdir()
    for index in range(3):
        (shallow / f"f{index}").write_bytes(b"x")
    assert _run_candidate(shallow, capsys, "--max-files", "2")["code"] == "count_overflow"
    assert _run_candidate(shallow, capsys, "--max-bytes", "2")["code"] == "bytes_overflow"


def test_prepare_source_reads_reviewed_git_objects_not_worktree(
    tmp_path: Path,
) -> None:
    repository, commit_sha = _make_source_repository(tmp_path)
    (repository / "README.md").write_text("malicious worktree bytes\n", encoding="utf-8")
    (repository / "untracked-payload.py").write_text("payload = True\n", encoding="utf-8")
    canonical_source = tmp_path / "canonical"

    files, _total, epoch = asset_validator.materialize_trusted_source(
        repository,
        canonical_source,
        commit_sha,
        VERSION,
    )

    assert files == 5
    assert epoch == SOURCE_DATE_EPOCH
    assert (canonical_source / "README.md").read_text(encoding="utf-8") == (
        "# Mercury test source\n"
    )
    assert not (canonical_source / "untracked-payload.py").exists()
    assert not (canonical_source / ".env.example").exists()


def test_release_asset_validator_accepts_exact_independent_reproduction(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert _run_assets(release_fixture, capsys) == {
        "status": "ok",
        "files": 5,
        "bytes": sum(path.stat().st_size for path in release_fixture.artifacts.iterdir()),
    }

    source = release_fixture.artifacts / "mercury-tools-0.2.1-source.tar.gz"
    with tarfile.open(source, mode="r:gz") as archive:
        names = {member.name for member in archive.getmembers() if member.isfile()}
    expected_names = {
        f"mercury-tools-0.2.1/{path.relative_to(release_fixture.canonical_source).as_posix()}"
        for path in release_fixture.canonical_source.rglob("*")
        if path.is_file()
    }
    assert names == expected_names


@pytest.mark.parametrize("tamper", ["extra", "missing"])
def test_release_asset_validator_rejects_extra_or_missing_file(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    tamper: str,
) -> None:
    root = release_fixture.artifacts
    if tamper == "extra":
        (root / "extra.txt").write_bytes(b"x")
    else:
        (root / "mercury_tools-0.2.1.tar.gz").unlink()

    assert _run_assets(release_fixture, capsys)["code"] == "inventory_invalid"


@pytest.mark.parametrize("entry_type", ["symlink", "hardlink", "fifo"])
def test_release_asset_validator_rejects_symlink_hardlink_and_special_file(
    release_fixture: ReleaseFixture,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    entry_type: str,
) -> None:
    root = release_fixture.artifacts
    wheel = root / "mercury_tools-0.2.1-py3-none-any.whl"
    payload = wheel.read_bytes()
    wheel.unlink()
    if entry_type == "symlink":
        target = tmp_path / "target"
        target.write_bytes(payload)
        try:
            wheel.symlink_to(target)
        except OSError:
            pytest.skip("symlinks are unavailable")
        code = "symlink"
    elif entry_type == "hardlink":
        external = tmp_path / "external"
        external.write_bytes(payload)
        try:
            os.link(external, wheel)
        except OSError:
            pytest.skip("hardlinks are unavailable")
        code = "hardlink"
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO creation is unavailable")
        try:
            os.mkfifo(wheel)
        except OSError:
            pytest.skip("FIFO creation is unavailable")
        code = "special_file"

    assert _run_assets(release_fixture, capsys)["code"] == code


@pytest.mark.parametrize("archive_kind", ["wheel_symlink", "sdist_hardlink"])
def test_release_asset_validator_rejects_link_members_in_reproduced_archives(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    archive_kind: str,
) -> None:
    distributions = release_fixture.reproduced_distributions
    if archive_kind == "wheel_symlink":
        wheel = distributions / "mercury_tools-0.2.1-py3-none-any.whl"
        wheel.unlink()
        with zipfile.ZipFile(wheel, mode="w") as archive:
            member = zipfile.ZipInfo("mercury_tools/payload.py")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, "../outside.py")
        expected_code = "symlink"
    else:
        sdist = distributions / "mercury_tools-0.2.1.tar.gz"
        sdist.unlink()
        with tarfile.open(sdist, mode="w:gz") as archive:
            member = tarfile.TarInfo("mercury_tools-0.2.1/payload.py")
            member.type = tarfile.LNKTYPE
            member.linkname = "mercury_tools-0.2.1/PKG-INFO"
            archive.addfile(member)
        expected_code = "special_file"

    assert _run_assets(release_fixture, capsys)["code"] == expected_code


@pytest.mark.parametrize(
    "asset_name",
    (
        "mercury_tools-0.2.1-py3-none-any.whl",
        "mercury_tools-0.2.1.tar.gz",
        "mercury-finance-plugin-0.2.1.zip",
        "mercury-tools-0.2.1-source.tar.gz",
    ),
)
def test_release_asset_validator_rejects_self_consistent_malicious_candidate_bytes(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    asset_name: str,
) -> None:
    path = release_fixture.artifacts / asset_name
    path.write_bytes(path.read_bytes() + b"candidate-controlled-payload")
    path.chmod(0o600)
    _write_manifest(release_fixture.artifacts, release_fixture.commit_sha)
    (release_fixture.artifacts / "SHA256SUMS.json").chmod(0o600)

    assert _run_assets(release_fixture, capsys)["code"] == "reproduction_mismatch"


def test_release_asset_validator_rejects_source_tree_not_bound_to_reviewed_git(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = release_fixture.canonical_source / "README.md"
    source.write_text("self-consistent but unreviewed source\n", encoding="utf-8")

    assert _run_assets(release_fixture, capsys)["code"] == "source_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    (
        ("sha256", "0" * 64, "digest_mismatch"),
        ("size", 999, "digest_mismatch"),
        ("commit_sha", "b" * 40, "manifest_mismatch"),
        ("version", "0.2.2", "manifest_mismatch"),
        ("kind", "unknown", "manifest_invalid"),
        ("file_name", "../unsafe.whl", "manifest_invalid"),
    ),
)
def test_release_asset_validator_rejects_manifest_tampering(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    field: str,
    value: object,
    code: str,
) -> None:
    root = release_fixture.artifacts
    manifest = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    manifest["artifacts"][0][field] = value
    _write_manifest(root, release_fixture.commit_sha, manifest=manifest)

    assert _run_assets(release_fixture, capsys)["code"] == code


@pytest.mark.parametrize("field", ["commit_sha", "version"])
def test_release_asset_validator_rejects_top_level_identity_tampering(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
    field: str,
) -> None:
    root = release_fixture.artifacts
    manifest = json.loads((root / "SHA256SUMS.json").read_text(encoding="utf-8"))
    manifest[field] = "b" * 40 if field == "commit_sha" else "0.2.2"
    _write_manifest(root, release_fixture.commit_sha, manifest=manifest)

    assert _run_assets(release_fixture, capsys)["code"] == "manifest_mismatch"


def test_release_asset_validator_rejects_duplicate_json_key_and_wrong_schema(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = release_fixture.artifacts
    duplicate = (
        '{"artifacts":[],"artifacts":[],"builder_provenance":{},"commit_sha":"'
        + release_fixture.commit_sha
        + '","schema_version":4,"source_date_epoch":1750000000,"version":"0.2.1"}'
    )
    (root / "SHA256SUMS.json").write_text(duplicate + "\n", encoding="utf-8")
    assert _run_assets(release_fixture, capsys)["code"] == "manifest_invalid"

    wrong_schema = json.loads(duplicate, object_pairs_hook=dict)
    wrong_schema["schema_version"] = 3
    (root / "SHA256SUMS.json").write_text(
        json.dumps(wrong_schema, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    assert _run_assets(release_fixture, capsys)["code"] == "manifest_invalid"


def test_release_asset_validator_rejects_wrong_reviewed_commit_and_version(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    common = [
        "verify",
        str(release_fixture.artifacts),
        str(release_fixture.repository),
        str(release_fixture.canonical_source),
        str(release_fixture.reproduced_distributions),
    ]
    assert asset_validator.main([*common, "b" * 40, VERSION]) == 1
    assert _json_output(capsys)["code"] == "manifest_mismatch"
    assert asset_validator.main([*common, release_fixture.commit_sha, "0.2.2"]) == 1
    assert _json_output(capsys)["code"] == "manifest_mismatch"


def test_release_asset_validator_rejects_group_world_writable_root(
    release_fixture: ReleaseFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    release_fixture.artifacts.chmod(stat.S_IRWXU | stat.S_IWGRP)

    assert _run_assets(release_fixture, capsys)["code"] == "permissions"


def test_publisher_fetches_git_source_and_rebuilds_only_after_network_isolation() -> None:
    workflow = (
        ROOT
        / "release-control"
        / "scaffold"
        / ".github"
        / "workflows"
        / "publish-v0.2.1.yml"
    ).read_text(encoding="utf-8")

    assert "Independently fetch exact reviewed Git source" in workflow
    assert "repository: ${{ steps.target.outputs.repository }}" in workflow
    assert "ref: ${{ inputs.reviewed_commit_sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "prepare-source" in workflow
    assert "git ls-tree" not in workflow
    assert "--network none" in workflow
    assert "--read-only" in workflow
    assert "--cap-drop ALL" in workflow
    assert "--security-opt no-new-privileges:true" in workflow
    assert "uv sync" not in workflow

    isolated_step = workflow.split(
        "- name: Reproduce wheel and sdist in networkless isolation",
        maxsplit=1,
    )[1].split("- name:", maxsplit=1)[0]
    assert "secrets." not in isolated_step
    assert "/trusted-dependencies/uv build" in isolated_step
    assert "scripts/build_release_artifacts.py" not in isolated_step
    assert "--reproduced-distributions" in workflow
    assert "--canonical-source" in workflow
    assert "--source-repository" in workflow
