from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_release_artifacts import VERSION, make_release_tree, passing_scanner

from mercury_tools.release.artifacts import ReleaseScannerAttestation
from mercury_tools.release.scanner import ReleaseGateError
from mercury_tools.release.verify import build_public_staging


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_public_staging_is_history_free_and_matches_reviewed_archive(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"
    source_sha = _git(root, "rev-parse", "HEAD")

    staging = build_public_staging(
        root=root,
        version=VERSION,
        output=output,
        scanner_gate=passing_scanner,
    )

    assert staging.commit_sha == source_sha
    assert staging.candidate_tree_digest == staging.staged_tree_digest
    assert _git(output, "rev-list", "--all", "--count") == "1"
    assert source_sha not in _git(output, "rev-list", "--all")
    assert not (output / ".env").exists()
    assert not (output / ".mercury").exists()
    assert not (output / ".superpowers").exists()
    assert not (output / "release-evidence").exists()


def test_public_staging_rejects_untracked_candidate_content(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    (root / "untracked-local-file.txt").write_text("must not stage", encoding="utf-8")

    with pytest.raises(ReleaseGateError, match="^release_worktree_not_clean$"):
        build_public_staging(
            root=root,
            version=VERSION,
            output=tmp_path / "public-staging",
            scanner_gate=passing_scanner,
        )


def test_public_staging_does_not_publish_when_scanner_gate_blocks(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_blocked$"):
        build_public_staging(
            root=root,
            version=VERSION,
            output=output,
            scanner_gate=lambda _root, _target: ReleaseScannerAttestation(passed=False),
        )

    assert not output.exists()
