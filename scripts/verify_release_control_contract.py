#!/usr/bin/env python3
"""Verify PublicTreeV1 parity with an independently implemented control repo."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from mercury_tools.release.public_tree import PublicTreeError, build_public_tree

MAX_CONTROL_OUTPUT_BYTES = 32 * 1024 * 1024


class ContractError(RuntimeError):
    """A constant-code cross-repository contract failure."""


ControlRunner = Callable[[Path, bytes], Mapping[str, object]]


def verify_contract(
    *,
    mercury_root: Path,
    control_root: Path,
    reviewed_sha: str,
    control_runner: ControlRunner | None = None,
) -> dict[str, object]:
    archive = _git_archive(mercury_root, reviewed_sha)
    try:
        mercury = build_public_tree(archive)
    except PublicTreeError as exc:
        raise ContractError("mercury_public_tree_invalid") from exc
    observed = (control_runner or _run_control_public_tree)(control_root, archive)
    expected_inventory = list(mercury.public_inventory())
    if set(observed) != {"digest", "entries", "schema_version"}:
        raise ContractError("control_public_tree_output_invalid")
    if observed.get("schema_version") != 1:
        raise ContractError("control_public_tree_output_invalid")
    if observed.get("digest") != mercury.digest or observed.get("entries") != expected_inventory:
        raise ContractError("public_tree_contract_mismatch")
    return {
        "digest": mercury.digest,
        "entry_count": len(mercury.entries),
        "reviewed_sha": reviewed_sha,
        "status": "passed",
    }


def _git_archive(root: Path, reviewed_sha: str) -> bytes:
    if len(reviewed_sha) not in {40, 64} or any(
        character not in "0123456789abcdef" for character in reviewed_sha
    ):
        raise ContractError("reviewed_sha_invalid")
    completed = subprocess.run(
        ["git", "archive", "--format=tar", reviewed_sha],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0 or not completed.stdout:
        raise ContractError("reviewed_archive_unavailable")
    return completed.stdout


def _run_control_public_tree(control_root: Path, archive: bytes) -> Mapping[str, object]:
    source = control_root / "src"
    module = source / "mercury_release_control/public_tree.py"
    if not module.is_file() or module.is_symlink():
        raise ContractError("control_public_tree_unavailable")
    with tempfile.TemporaryDirectory(prefix="mercury-control-contract-") as temporary:
        archive_path = Path(temporary) / "candidate.tar"
        archive_path.write_bytes(archive)
        os.chmod(archive_path, 0o600)
        environment = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONNOUSERSITE": "1",
            "PYTHONPATH": str(source.resolve(strict=True)),
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "mercury_release_control.public_tree",
                "--archive",
                str(archive_path),
            ],
            cwd=control_root,
            env=environment,
            check=False,
            capture_output=True,
        )
    if completed.returncode != 0 or len(completed.stdout) > MAX_CONTROL_OUTPUT_BYTES:
        raise ContractError("control_public_tree_failed")
    try:
        payload = json.loads(completed.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError("control_public_tree_output_invalid") from exc
    if not isinstance(payload, Mapping):
        raise ContractError("control_public_tree_output_invalid")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mercury-root", type=Path, default=Path.cwd())
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--reviewed-sha", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_contract(
            mercury_root=args.mercury_root,
            control_root=args.control_root,
            reviewed_sha=args.reviewed_sha,
        )
    except ContractError as exc:
        print(json.dumps({"error": str(exc), "status": "error"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
