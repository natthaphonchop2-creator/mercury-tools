from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mercury_tools.release import artifacts as release_artifacts
from mercury_tools.release.artifacts import (
    ReleaseCandidate,
    build_release_artifacts,
    validate_canonical_archive_member_names,
)
from mercury_tools.release.models import (
    EXPECTED_SURFACE_SCANNER_VERSIONS,
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    GateStatus,
    ScannerVersionAttestation,
    SecretScanReport,
    SurfaceAttestation,
)
from mercury_tools.release.scanner import ReleaseGateError, build_blocked_report

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


def passing_task13_report() -> SecretScanReport:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    scanners = tuple(
        ScannerVersionAttestation(
            scanner=name,
            version=version,
            status=GateStatus.PASSED,
            evidence_sha256=hashlib.sha256(name.encode()).hexdigest(),
            exit_code=0,
        )
        for name, version in PINNED_SCANNER_VERSIONS.items()
    )
    surfaces = tuple(
        SurfaceAttestation(
            surface=surface,
            status=GateStatus.PASSED,
            scanner_versions=EXPECTED_SURFACE_SCANNER_VERSIONS[surface],
            started_at=timestamp,
            completed_at=timestamp,
            finding_count=0,
            evidence_hashes=(hashlib.sha256(surface.encode()).hexdigest(),),
            exit_codes=(0,),
        )
        for surface in REQUIRED_PUBLIC_SURFACES
    )
    return SecretScanReport(
        status=GateStatus.PASSED,
        started_at=timestamp,
        completed_at=timestamp,
        scanner_versions=scanners,
        surfaces=surfaces,
    )


def install_task13_runner(
    monkeypatch: pytest.MonkeyPatch,
    report: object | None = None,
) -> list[tuple[object, Path, Path]]:
    calls: list[tuple[object, Path, Path]] = []

    def run(candidate: object, snapshot: Path, target: Path) -> object:
        calls.append((candidate, snapshot, target))
        return passing_task13_report() if report is None else report

    monkeypatch.setattr(release_artifacts, "_run_task13_artifact_gate", run)
    return calls


def incomplete_task13_report() -> SecretScanReport:
    timestamp = datetime(2026, 7, 14, tzinfo=UTC)
    return SecretScanReport.model_construct(
        status=GateStatus.PASSED,
        started_at=timestamp,
        completed_at=timestamp,
        scanner_versions=(),
        surfaces=(),
        blockers=(),
        finding_codes=(),
    )


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl" or path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            return [item.filename for item in archive.infolist()]
    with tarfile.open(path, mode="r:gz") as archive:
        return [item.name for item in archive.getmembers()]


@pytest.mark.parametrize(
    "names",
    (
        ("a/./b", "a/b"),
        ("/absolute",),
        ("a\\b",),
        ("a//b",),
        ("a/../b",),
        ("README.md", "readme.md"),
        ("caf\u00e9.txt", "cafe\u0301.txt"),
    ),
)
def test_canonical_archive_member_validator_rejects_noncanonical_paths(
    names: tuple[str, ...],
) -> None:
    with pytest.raises(ReleaseGateError, match="^release_archive_member_invalid$"):
        validate_canonical_archive_member_names(names)


def test_release_artifacts_are_reproducible_and_bound_to_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"
    calls = install_task13_runner(monkeypatch)

    first = build_release_artifacts(
        root,
        version=VERSION,
        output=first_output,
    )
    second = build_release_artifacts(
        root,
        version=VERSION,
        output=second_output,
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
    assert len(calls) == 2
    assert all(snapshot != root for _candidate, snapshot, _target in calls)

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
        )


def test_artifact_builder_does_not_publish_when_task13_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "blocked"
    install_task13_runner(monkeypatch, build_blocked_report("scanner_missing"))

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_blocked$"):
        build_release_artifacts(
            root,
            version=VERSION,
            output=output,
        )

    assert not output.exists()


def test_artifact_builder_rejects_incomplete_task13_report_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "incomplete"
    install_task13_runner(monkeypatch, incomplete_task13_report())

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_unavailable$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()


def test_artifact_builder_rechecks_candidate_identity_before_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "mutated"

    def mutate_candidate(
        candidate: ReleaseCandidate,
        _snapshot: Path,
        _target: Path,
    ) -> SecretScanReport:
        readme = candidate.root / "README.md"
        readme.write_text(readme.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
        return passing_task13_report()

    monkeypatch.setattr(release_artifacts, "_run_task13_artifact_gate", mutate_candidate)

    with pytest.raises(ReleaseGateError, match="^release_candidate_changed$"):
        build_release_artifacts(root, version=VERSION, output=output)

    assert not output.exists()
