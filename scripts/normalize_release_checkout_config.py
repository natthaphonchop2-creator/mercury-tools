#!/usr/bin/env python3
"""Remove the exact local Git config entry injected by actions/checkout."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

_GIT = Path("/usr/bin/git")
_TIMEOUT_SECONDS = 10
_ERROR = "release_checkout_config_invalid\n"
_CHECKOUT_WORKTREE_CONFIG = (
    b"[core]\n"
    b"\tsparseCheckout = false\n"
    b"\tsparseCheckoutCone = false\n"
    b"[index]\n"
    b"\tsparse = false\n"
)


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        (str(_GIT), *arguments),
        cwd=root,
        env={
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        },
        check=False,
        capture_output=True,
        timeout=_TIMEOUT_SECONDS,
    )


def _is_clean(result: subprocess.CompletedProcess[bytes], *, returncode: int) -> bool:
    return result.returncode == returncode and result.stderr == b""


def _checkout_worktree_config(root: Path) -> Path | None:
    git_dir = _git(root, "rev-parse", "--absolute-git-dir")
    expected_git_dir = root / ".git"
    if (
        not _is_clean(git_dir, returncode=0)
        or git_dir.stdout != os.fsencode(expected_git_dir) + b"\n"
    ):
        raise ValueError

    path = expected_git_dir / "config.worktree"
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode) or path.read_bytes() != _CHECKOUT_WORKTREE_CONFIG:
        raise ValueError
    return path


def main() -> int:
    root = Path.cwd().resolve()
    try:
        top_level = _git(root, "rev-parse", "--show-toplevel")
        if (
            not _is_clean(top_level, returncode=0)
            or top_level.stdout != os.fsencode(root) + b"\n"
        ):
            raise ValueError

        existing = _git(root, "config", "--null", "--local", "--get-all", "gc.auto")
        if (
            not _is_clean(existing, returncode=0)
            or existing.stdout != b"0\0"
        ):
            raise ValueError

        worktree_config = _checkout_worktree_config(root)

        removed = _git(
            root,
            "config",
            "--local",
            "--fixed-value",
            "--unset-all",
            "gc.auto",
            "0",
        )
        if not _is_clean(removed, returncode=0) or removed.stdout:
            raise ValueError

        remaining = _git(root, "config", "--null", "--local", "--get-all", "gc.auto")
        if not _is_clean(remaining, returncode=1) or remaining.stdout:
            raise ValueError

        if worktree_config is not None:
            worktree_config.unlink()
            if worktree_config.exists() or worktree_config.is_symlink():
                raise ValueError
    except (OSError, subprocess.TimeoutExpired, ValueError):
        sys.stderr.write(_ERROR)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
