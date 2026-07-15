from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.verify_staging_snapshot import (  # noqa: E402
    StagingSnapshotError,
    _archive_tree_digest,
    verify_staging_snapshot,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _staging_fixture(tmp_path: Path) -> tuple[Path, Path]:
    repository = tmp_path / "staging"
    repository.mkdir()
    _git(repository, "init", "--initial-branch=main")
    _git(repository, "config", "user.name", "Mercury Test")
    _git(repository, "config", "user.email", "test@example.invalid")
    (repository / "README.md").write_text("Mercury staging\n", encoding="utf-8")
    (repository / "run.sh").write_text("#!/bin/sh\nprintf ok\n", encoding="utf-8")
    (repository / "run.sh").chmod(0o755)
    _git(repository, "add", "--all")
    _git(repository, "commit", "-m", "staging snapshot")
    _git(repository, "tag", "-a", "v0.2.1-rc.1", "-m", "Mercury staging")
    commit = _git(repository, "rev-parse", "HEAD")
    archive = subprocess.run(
        ["git", "archive", "--format=tar", commit],
        cwd=repository,
        check=True,
        capture_output=True,
    ).stdout
    digest = _archive_tree_digest(archive)
    identity = tmp_path / "identity.json"
    identity.write_text(
        json.dumps(
            {
                "status": "ok",
                "staging": {
                    "candidate_tree_digest": digest,
                    "commit_sha": "a" * 40,
                    "path": "/private/release-output/public-staging",
                    "staged_tree_digest": digest,
                    "version": "0.2.1",
                },
            }
        ),
        encoding="utf-8",
    )
    return repository, identity


def test_staging_snapshot_binds_annotated_single_commit_to_identity(tmp_path: Path) -> None:
    repository, identity = _staging_fixture(tmp_path)

    report = verify_staging_snapshot(
        repo="owner/repository",
        tag="v0.2.1-rc.1",
        identity=identity,
        clone_url=repository.as_uri(),
    )

    assert report["candidate_commit_sha"] == "a" * 40
    assert report["staging_commit_sha"] != "a" * 40
    assert len(report["tree_digest"]) == 64


def test_staging_snapshot_rejects_digest_mismatch(tmp_path: Path) -> None:
    repository, identity = _staging_fixture(tmp_path)
    payload = json.loads(identity.read_text(encoding="utf-8"))
    payload["staging"]["candidate_tree_digest"] = "0" * 64
    identity.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(StagingSnapshotError, match="^staging_tree_digest_mismatch$"):
        verify_staging_snapshot(
            repo="owner/repository",
            tag="v0.2.1-rc.1",
            identity=identity,
            clone_url=repository.as_uri(),
        )
