#!/usr/bin/env python3
# ruff: noqa: E501
"""Verify and execute the separately reviewed release-control inspector."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_POLICY_BYTES = 512 * 1024
_MAX_EVIDENCE_BYTES = 2 * 1024 * 1024
_FORWARDED_NAMES = {
    "FLOWACCOUNT_SANDBOX_BASE_URL",
    "FLOWACCOUNT_SANDBOX_CLIENT_ID",
    "FLOWACCOUNT_SANDBOX_CLIENT_SECRET",
    "MERCURY_MARKETPLACE_SNAPSHOT_URL",
    "MERCURY_PUBLIC_MCP_TOKEN",
    "MERCURY_PUBLIC_MCP_URL",
    "MERCURY_STAGING_REPOSITORY_TOKEN",
    "MERCURY_TARGET_REPOSITORY_READ_TOKEN",
    "RENDER_API_TOKEN",
    "RENDER_API_URL",
    "RENDER_SERVICE_ID",
    "INSPECTOR_GIT",
    "INSPECTOR_GITLEAKS",
    "INSPECTOR_TRUFFLEHOG",
    "INSPECTOR_PYTHON",
    "STAGING_REPOSITORY",
    "SUPABASE_DB_URL",
    "SUPABASE_URL",
    "TARGET_REPOSITORY",
}
_PINNED_CLOSURE = {
    # Updated together with the separately reviewed executable.  The policy
    # pins the executable; this closes its only local code and dependency-manifest inputs.
    "scripts/inspector_core.py": "dc8b7667154dcaf812693f7bab1253d9f945fbc0c93d7afa9a5902fd6a9d40a9",
    "requirements-inspector.txt": "abbd849aeab70ad26dfa4ef9a57821243ccfbea0d4d1f4e1dff036972441d858",
}


class InspectorError(RuntimeError):
    """A constant-code pinned-inspector failure."""


def _load_policy(path: Path) -> Mapping[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > _MAX_POLICY_BYTES:
            raise InspectorError("inspector_policy_invalid")
        policy = json.loads(encoded)
    except InspectorError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InspectorError("inspector_policy_invalid") from exc
    if not isinstance(policy, Mapping) or policy.get("bootstrap_state") != "configured":
        raise InspectorError("inspector_policy_invalid")
    return policy


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise InspectorError("inspector_unavailable") from exc
    return digest.hexdigest()


def _verify_regular_digest(path: Path, expected: str, code: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InspectorError(code) from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or _sha256(path) != expected
    ):
        raise InspectorError(code)


def _verified_python(environment: Mapping[str, str]) -> str:
    location = environment.get("INSPECTOR_PYTHON", "")
    path = Path(location)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise InspectorError("inspector_runtime_invalid") from exc
    if (
        not path.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or not metadata.st_mode & stat.S_IXUSR
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise InspectorError("inspector_runtime_invalid")
    return str(path)


def run_pinned_inspector(
    *,
    root: Path,
    policy_path: Path,
    reviewed_sha: str,
    staging_ref: str,
    manifest: Path,
    allowlist: Path,
    output: Path,
) -> None:
    policy = _load_policy(policy_path)
    inspector = policy.get("inspector")
    if not isinstance(inspector, Mapping):
        raise InspectorError("inspector_policy_invalid")
    if inspector.get("interface_version") != 1:
        raise InspectorError("inspector_policy_invalid")
    relative_path = inspector.get("path")
    expected_sha256 = inspector.get("sha256")
    if relative_path != "bin/mercury-release-control-inspector" or not isinstance(
        expected_sha256, str
    ) or _SHA256_PATTERN.fullmatch(expected_sha256) is None:
        raise InspectorError("inspector_policy_invalid")
    if expected_sha256 == "0" * 64:
        raise InspectorError("inspector_not_bootstrapped")

    executable = root / relative_path
    try:
        metadata = executable.lstat()
    except OSError as exc:
        raise InspectorError("inspector_unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
        or not metadata.st_mode & stat.S_IXUSR
    ):
        raise InspectorError("inspector_permissions_invalid")
    if _sha256(executable) != expected_sha256:
        raise InspectorError("inspector_digest_mismatch")
    for relative_closure_path, closure_digest in _PINNED_CLOSURE.items():
        _verify_regular_digest(
            root / relative_closure_path,
            closure_digest,
            "inspector_closure_digest_mismatch",
        )
    if output.exists():
        raise InspectorError("inspector_output_exists")

    environment = {
        name: value
        for name, value in os.environ.items()
        if name in _FORWARDED_NAMES or name in {"HOME", "SSL_CERT_FILE"}
    }
    if not environment.get("SUPABASE_DB_URL", "").strip():
        raise InspectorError("inspector_database_credential_missing")
    try:
        interpreter = _verified_python(environment)
        environment["PATH"] = "/usr/bin:/bin"
        completed = subprocess.run(
            [
                interpreter,
                "-I",
                str(executable),
                "--interface-version",
                "1",
                "--policy",
                str(policy_path),
                "--reviewed-sha",
                reviewed_sha,
                "--staging-ref",
                staging_ref,
                "--manifest",
                str(manifest),
                "--allowlist",
                str(allowlist),
                "--output",
                str(output),
            ],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise InspectorError("inspector_execution_failed") from exc
    if completed.returncode != 0:
        raise InspectorError("inspector_execution_failed")
    try:
        output_metadata = output.lstat()
    except OSError as exc:
        raise InspectorError("inspector_output_missing") from exc
    if (
        stat.S_ISLNK(output_metadata.st_mode)
        or not stat.S_ISREG(output_metadata.st_mode)
        or output_metadata.st_size <= 0
        or output_metadata.st_size > _MAX_EVIDENCE_BYTES
    ):
        raise InspectorError("inspector_output_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--staging-ref", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        run_pinned_inspector(
            root=args.root.resolve(),
            policy_path=args.policy.resolve(),
            reviewed_sha=args.reviewed_sha,
            staging_ref=args.staging_ref,
            manifest=args.manifest.resolve(),
            allowlist=args.allowlist.resolve(),
            output=args.output.resolve(),
        )
    except InspectorError as exc:
        print(f"release-control inspector failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
