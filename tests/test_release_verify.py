from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_release_artifacts import ROOT, VERSION, make_release_tree, passing_scanner

from mercury_tools import cli
from mercury_tools.release.artifacts import build_release_artifacts
from mercury_tools.release.scanner import ReleaseGateError
from mercury_tools.release.verify import verify_release, verify_release_tree


def _verify(root: Path, artifacts: Path) -> None:
    verify_release(
        root=root,
        version=VERSION,
        artifacts=artifacts,
        scanner_gate=passing_scanner,
    )


def test_release_verifier_accepts_version_consistent_candidate(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    built = build_release_artifacts(
        root,
        version=VERSION,
        output=artifacts,
        scanner_gate=passing_scanner,
    )

    verification = verify_release(
        root=root,
        version=VERSION,
        artifacts=artifacts,
        scanner_gate=passing_scanner,
    )

    assert verification.passed is True
    assert verification.version == VERSION
    assert verification.commit_sha == built.commit_sha
    assert len(verification.artifact_manifest_sha256) == 64


def test_release_tree_rejects_moving_plugin_ref(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    path = root / "plugins/mercury-finance/.mcp.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["mcpServers"]["mercury-finance"]["args"][1] = (
        "git+https://github.com/natthaphonchop2-creator/mercury-tools.git@main"
    )
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ReleaseGateError, match="^plugin_ref_not_immutable$"):
        verify_release_tree(root, version=VERSION)


def test_verifier_rejects_missing_duplicate_extra_and_digest_mismatched_artifacts(
    tmp_path: Path,
) -> None:
    root = make_release_tree(tmp_path)
    artifacts = tmp_path / "artifacts"
    manifest = build_release_artifacts(
        root,
        version=VERSION,
        output=artifacts,
        scanner_gate=passing_scanner,
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
