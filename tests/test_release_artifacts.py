from __future__ import annotations

import io
import json
import os
import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from mercury_tools.release.artifacts import (
    ReleaseScannerAttestation,
    build_release_artifacts,
)
from mercury_tools.release.scanner import ReleaseGateError

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.1"
FIXTURE_TIMESTAMP = "2026-07-14T00:00:00+00:00"


def _run(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _archive_head(destination: Path) -> None:
    payload = subprocess.run(
        ["git", "archive", "--format=tar", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                assert source is not None
                target.write_bytes(source.read())
            else:
                raise AssertionError(f"unexpected fixture archive member: {member.name}")


def make_release_tree(tmp_path: Path) -> Path:
    root = tmp_path / "candidate"
    root.mkdir()
    _archive_head(root)

    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text(encoding="utf-8").replace(
            'version = "0.2.0"',
            'version = "0.2.1"',
            1,
        ),
        encoding="utf-8",
    )
    mcp_path = root / "plugins/mercury-finance/.mcp.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["mercury-finance"]["args"][1] = (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@v0.2.1"
    )
    mcp_path.write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    plugin_path = root / "plugins/mercury-finance/.codex-plugin/plugin.json"
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))
    plugin["version"] = str(plugin["version"]).replace("0.2.0", "0.2.1", 1)
    plugin_path.write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")

    _run(["git", "init", "--initial-branch", "main"], cwd=root)
    _run(["git", "config", "user.name", "Release Fixture"], cwd=root)
    _run(["git", "config", "user.email", "release-fixture@example.test"], cwd=root)
    _run(["git", "add", "-A"], cwd=root)
    fixture_env = {
        **os.environ,
        "GIT_AUTHOR_DATE": FIXTURE_TIMESTAMP,
        "GIT_COMMITTER_DATE": FIXTURE_TIMESTAMP,
    }
    _run(["git", "commit", "-m", "release fixture"], cwd=root, env=fixture_env)

    (root / ".env").write_text("local-secret-state\n", encoding="utf-8")
    local_state = root / ".mercury"
    local_state.mkdir()
    (local_state / "audit-ledger.jsonl").write_text("local-audit-state\n", encoding="utf-8")
    assert _run(["git", "status", "--porcelain"], cwd=root) == ""
    return root


def passing_scanner(_root: Path, _target: Path) -> ReleaseScannerAttestation:
    return ReleaseScannerAttestation(passed=True)


def blocked_scanner(_root: Path, _target: Path) -> ReleaseScannerAttestation:
    return ReleaseScannerAttestation(passed=False)


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [item.filename for item in archive.infolist()]
    with tarfile.open(path, mode="r:gz") as archive:
        return [item.name for item in archive.getmembers()]


def test_release_artifacts_are_reproducible_and_bound_to_candidate(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    first = build_release_artifacts(
        root,
        version=VERSION,
        output=first_output,
        scanner_gate=passing_scanner,
    )
    second = build_release_artifacts(
        root,
        version=VERSION,
        output=second_output,
        scanner_gate=passing_scanner,
    )

    assert first.version == VERSION
    assert first.commit_sha == _run(["git", "rev-parse", "HEAD"], cwd=root)
    assert {item.kind for item in first.artifacts} == {"wheel", "sdist", "plugin", "source"}
    assert all(len(item.sha256) == 64 for item in first.artifacts)
    assert first.as_dict() == second.as_dict()
    assert {path.name: path.read_bytes() for path in first_output.iterdir()} == {
        path.name: path.read_bytes() for path in second_output.iterdir()
    }
    assert {path.name for path in first_output.iterdir()} == {
        *(item.file_name for item in first.artifacts),
        "SHA256SUMS.json",
    }
    assert _run(["git", "status", "--porcelain"], cwd=root) == ""

    for artifact in first.artifacts:
        path = first_output / artifact.file_name
        names = _archive_names(path)
        assert names == sorted(names)
        assert all(not name.startswith("/") and ".." not in Path(name).parts for name in names)

    source = next(item for item in first.artifacts if item.kind == "source")
    with tarfile.open(first_output / source.file_name, mode="r:gz") as archive:
        members = archive.getmembers()
    assert all(member.uid == 0 and member.gid == 0 for member in members)
    assert all(member.mtime == first.build_epoch for member in members)
    source_names = _archive_names(first_output / source.file_name)
    assert not any(Path(name).name == ".env" for name in source_names)
    assert not any(".mercury" in Path(name).parts for name in source_names)


def test_current_v020_source_fails_closed_for_v021_request(tmp_path: Path) -> None:
    with pytest.raises(ReleaseGateError, match="^release_version_mismatch$"):
        build_release_artifacts(
            ROOT,
            version=VERSION,
            output=tmp_path / "dist",
            scanner_gate=passing_scanner,
        )


def test_artifact_builder_does_not_publish_when_scanner_gate_blocks(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "blocked"

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_blocked$"):
        build_release_artifacts(
            root,
            version=VERSION,
            output=output,
            scanner_gate=blocked_scanner,
        )

    assert not output.exists()
