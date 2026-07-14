from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_release_artifacts import (
    incomplete_task13_report,
    install_task13_runner,
    make_release_tree,
)

from mercury_tools.release.scanner import ReleaseGateError, build_blocked_report
from mercury_tools.release.verify import build_public_staging


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_public_staging_is_history_free_and_matches_reviewed_archive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"
    source_sha = _git(root, "rev-parse", "HEAD")
    calls = install_task13_runner(monkeypatch)

    staging = build_public_staging(
        root=root,
        version="0.2.1",
        output=output,
    )

    assert staging.commit_sha == source_sha
    assert staging.candidate_tree_digest == staging.staged_tree_digest
    assert _git(output, "rev-list", "--all", "--count") == "1"
    assert source_sha not in _git(output, "rev-list", "--all")
    assert not (output / ".env").exists()
    assert not (output / ".mercury").exists()
    assert not (output / ".superpowers").exists()
    assert not (output / "release-evidence").exists()
    assert len(calls) == 1
    assert calls[0][1] != root
    assert calls[0][2].name == "expected-artifacts"


def test_public_staging_ignores_caller_git_template_and_global_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"
    template = tmp_path / "poison-template"
    hooks = template / "hooks"
    hooks.mkdir(parents=True)
    marker = tmp_path / "template-hook-ran"
    hook = hooks / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\nprintf '%s' hook > '{marker}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    global_config = tmp_path / "poison.gitconfig"
    global_config.write_text(
        f"[init]\n\ttemplateDir = {template}\n[core]\n\thooksPath = {hooks}\n",
        encoding="utf-8",
    )
    install_task13_runner(monkeypatch)
    monkeypatch.setenv("GIT_TEMPLATE_DIR", str(template))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("HOME", str(tmp_path / "poison-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "poison-xdg"))

    build_public_staging(root=root, version="0.2.1", output=output)

    assert not marker.exists()
    assert (output / ".git").is_dir()


def test_public_staging_rejects_untracked_candidate_content(tmp_path: Path) -> None:
    root = make_release_tree(tmp_path)
    (root / "untracked-local-file.txt").write_text("must not stage", encoding="utf-8")

    with pytest.raises(ReleaseGateError, match="^release_worktree_not_clean$"):
        build_public_staging(
            root=root,
            version="0.2.1",
            output=tmp_path / "public-staging",
        )


def test_public_staging_does_not_publish_when_task13_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"
    install_task13_runner(monkeypatch, build_blocked_report("scanner_missing"))

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_blocked$"):
        build_public_staging(
            root=root,
            version="0.2.1",
            output=output,
        )

    assert not output.exists()


def test_public_staging_rejects_incomplete_task13_report_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_release_tree(tmp_path)
    output = tmp_path / "public-staging"
    install_task13_runner(monkeypatch, incomplete_task13_report())

    with pytest.raises(ReleaseGateError, match="^release_scanner_gate_unavailable$"):
        build_public_staging(
            root=root,
            version="0.2.1",
            output=output,
        )

    assert not output.exists()
