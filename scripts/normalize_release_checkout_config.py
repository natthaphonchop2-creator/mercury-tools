#!/usr/bin/env python3
"""Remove the exact local Git config entry injected by actions/checkout."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_GIT = Path("/usr/bin/git")
_TIMEOUT_SECONDS = 10
_ERROR = "release_checkout_config_invalid\n"


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
    except (OSError, subprocess.TimeoutExpired, ValueError):
        sys.stderr.write(_ERROR)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
