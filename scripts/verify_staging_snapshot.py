#!/usr/bin/env python3
"""Verify that a public staging tag contains the reviewed release tree."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from mercury_tools.release.artifacts import (
    CandidateEntry,
    is_excluded_public_path,
    source_tree_digest,
    validate_canonical_archive_member_names,
)

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$"
)
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_MAX_COMMAND_OUTPUT_BYTES = 64 * 1024 * 1024
_MAX_IDENTITY_BYTES = 64 * 1024
_COMMAND_TIMEOUT_SECONDS = 600
_IDENTITY_KEYS = {
    "candidate_tree_digest",
    "commit_sha",
    "path",
    "staged_tree_digest",
    "version",
}


class StagingSnapshotError(RuntimeError):
    """A bounded public staging snapshot check failed."""


def _environment(base: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if base is None else base)
    for key in (
        "CODEX_HOME",
        "GH_ENTERPRISE_TOKEN",
        "GH_TOKEN",
        "GITHUB_ENTERPRISE_TOKEN",
        "GITHUB_TOKEN",
        "GIT_ASKPASS",
        "GIT_SSH",
        "GIT_SSH_COMMAND",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
    ):
        environment.pop(key, None)
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _run_git(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=_environment(environment),
            check=False,
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StagingSnapshotError("git_unavailable") from exc
    if len(result.stdout) + len(result.stderr) > _MAX_COMMAND_OUTPUT_BYTES:
        raise StagingSnapshotError("git_output_too_large")
    if result.returncode != 0:
        raise StagingSnapshotError("git_command_failed")
    return result.stdout


def _load_identity(path: Path, *, version: str) -> dict[str, str]:
    try:
        if path.stat().st_size > _MAX_IDENTITY_BYTES:
            raise StagingSnapshotError("identity_too_large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except StagingSnapshotError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StagingSnapshotError("identity_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"status", "staging"}:
        raise StagingSnapshotError("identity_invalid")
    staging = payload["staging"]
    if payload["status"] != "ok" or not isinstance(staging, dict):
        raise StagingSnapshotError("identity_invalid")
    if set(staging) != _IDENTITY_KEYS:
        raise StagingSnapshotError("identity_invalid")
    if staging["version"] != version:
        raise StagingSnapshotError("identity_version_mismatch")
    if not isinstance(staging["commit_sha"], str) or not _SHA_PATTERN.fullmatch(
        staging["commit_sha"]
    ):
        raise StagingSnapshotError("identity_commit_invalid")
    for key in ("candidate_tree_digest", "staged_tree_digest"):
        if not isinstance(staging[key], str) or not _DIGEST_PATTERN.fullmatch(staging[key]):
            raise StagingSnapshotError("identity_digest_invalid")
    return {
        "candidate_tree_digest": staging["candidate_tree_digest"],
        "commit_sha": staging["commit_sha"],
        "staged_tree_digest": staging["staged_tree_digest"],
        "version": staging["version"],
    }


def _archive_tree_digest(archive_bytes: bytes) -> str:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            members = archive.getmembers()
            files = tuple(member for member in members if not member.isdir())
            if any(not member.isfile() for member in files):
                raise StagingSnapshotError("staging_archive_member_invalid")
            validate_canonical_archive_member_names(member.name for member in files)
            entries: list[CandidateEntry] = []
            for member in files:
                if is_excluded_public_path(member.name):
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise StagingSnapshotError("staging_archive_member_invalid")
                entries.append(
                    CandidateEntry(
                        name=member.name,
                        mode=0o755 if member.mode & 0o111 else 0o644,
                        data=stream.read(),
                    )
                )
    except StagingSnapshotError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise StagingSnapshotError("staging_archive_invalid") from exc
    return source_tree_digest(entries)


def verify_staging_snapshot(
    *,
    repo: str,
    tag: str,
    identity: Path,
    clone_url: str | None = None,
) -> dict[str, str]:
    """Verify a single-commit annotated staging tag against its candidate digest."""

    if _REPOSITORY_PATTERN.fullmatch(repo) is None:
        raise StagingSnapshotError("repository_invalid")
    if _TAG_PATTERN.fullmatch(tag) is None:
        raise StagingSnapshotError("tag_invalid")
    expected = _load_identity(identity, version=tag.split("-", 1)[0].removeprefix("v"))
    with tempfile.TemporaryDirectory(prefix="mercury-staging-verify-") as temporary:
        clone = Path(temporary) / "repository"
        _run_git(
            (
                "clone",
                "--filter=blob:none",
                "--no-checkout",
                "--branch",
                tag,
                "--single-branch",
                clone_url or f"https://github.com/{repo}.git",
                str(clone),
            )
        )
        tag_ref = f"refs/tags/{tag}"
        if _run_git(("cat-file", "-t", tag_ref), cwd=clone).strip() != b"tag":
            raise StagingSnapshotError("annotated_tag_required")
        staging_commit = _run_git(("rev-parse", f"{tag_ref}^{{commit}}"), cwd=clone).strip()
        if not _SHA_PATTERN.fullmatch(staging_commit.decode("ascii", errors="ignore")):
            raise StagingSnapshotError("staging_commit_invalid")
        if _run_git(("rev-list", "--all", "--count"), cwd=clone).strip() != b"1":
            raise StagingSnapshotError("staging_history_invalid")
        commits = _run_git(("rev-list", "--all"), cwd=clone).split()
        if expected["commit_sha"].encode("ascii") in commits:
            raise StagingSnapshotError("source_history_present")
        digest = _archive_tree_digest(
            _run_git(("archive", "--format=tar", staging_commit.decode("ascii")), cwd=clone)
        )
    if digest != expected["candidate_tree_digest"] or digest != expected["staged_tree_digest"]:
        raise StagingSnapshotError("staging_tree_digest_mismatch")
    return {
        "candidate_commit_sha": expected["commit_sha"],
        "staging_commit_sha": staging_commit.decode("ascii"),
        "tree_digest": digest,
        "version": expected["version"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = verify_staging_snapshot(
            repo=args.repo,
            tag=args.tag,
            identity=args.identity,
        )
    except StagingSnapshotError as error:
        print(f"staging snapshot verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok", "staging": report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
