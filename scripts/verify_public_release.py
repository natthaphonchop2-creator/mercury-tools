#!/usr/bin/env python3
"""Verify an immutable Mercury release through anonymous public interfaces."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mercury_tools.release.artifacts import (
    MANIFEST_FILE_NAME,
    ReleaseGateError,
    load_release_artifact_manifest,
)
from scripts.smoke_tagged_marketplace import (
    TaggedMarketplaceError,
    build_tagged_smoke_plan,
    run_tagged_smoke,
)

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_PATTERN = re.compile(r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_COMMAND_TIMEOUT_SECONDS = 600
_MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
_MAX_METADATA_BYTES = 4 * 1024 * 1024
_MAX_ASSET_BYTES = 512 * 1024 * 1024
_ANONYMOUS_ENVIRONMENT_KEYS = frozenset(
    {
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
    }
)
_QUICKSTART_MARKERS = (
    "codex plugin marketplace add",
    "codex plugin add mercury-finance@mercury-tools",
    "connector-credential-setup-th",
    "run_erp_read",
    "preview_erp_write",
    "Cross-MCP",
    "codex plugin remove mercury-finance@mercury-tools",
)


class PublicReleaseError(RuntimeError):
    """A bounded anonymous public-release check failed."""


@dataclass(frozen=True)
class PublicReleasePlan:
    repo: str
    tag: str
    release: str
    expected_tools: int
    workspace: Path
    clone_url: str
    environment: dict[str, str]


def build_public_release_plan(
    *,
    repo: str,
    tag: str,
    release: str,
    expected_tools: int,
    workspace: Path,
) -> PublicReleasePlan:
    """Build an anonymous verification plan for one exact release ref."""

    if tag != release:
        raise PublicReleaseError("release_ref_mismatch")
    if _REPOSITORY_PATTERN.fullmatch(repo) is None:
        raise PublicReleaseError("repository_invalid")
    if _TAG_PATTERN.fullmatch(tag) is None:
        raise PublicReleaseError("tag_invalid")
    if type(expected_tools) is not int or expected_tools <= 0:
        raise PublicReleaseError("expected_tools_invalid")
    environment = {
        "CODEX_HOME": str(workspace / "codex-home"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return PublicReleasePlan(
        repo=repo,
        tag=tag,
        release=release,
        expected_tools=expected_tools,
        workspace=workspace,
        clone_url=f"https://github.com/{repo}.git",
        environment=environment,
    )


def _anonymous_environment(plan: PublicReleasePlan) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _ANONYMOUS_ENVIRONMENT_KEYS
    }
    environment.update(plan.environment)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_HTTP_TIMEOUT"] = "120"
    return environment


def _run_git(
    args: Sequence[str],
    *,
    environment: Mapping[str, str],
    cwd: Path | None = None,
    phase: str,
) -> bytes:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            env=dict(environment),
            check=False,
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PublicReleaseError(f"{phase}_unavailable") from exc
    if len(result.stdout) + len(result.stderr) > _MAX_COMMAND_OUTPUT_BYTES:
        raise PublicReleaseError(f"{phase}_output_too_large")
    if result.returncode != 0:
        raise PublicReleaseError(f"{phase}_failed")
    return result.stdout.strip()


def _prepare_workspace(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if any(path.iterdir()):
            raise PublicReleaseError("workspace_not_empty")
        path.chmod(0o700)
    except PublicReleaseError:
        raise
    except OSError as exc:
        raise PublicReleaseError("workspace_invalid") from exc


def _clone_and_verify_tag(
    plan: PublicReleasePlan,
    environment: Mapping[str, str],
) -> tuple[Path, str]:
    clone = plan.workspace / "repository"
    _run_git(
        (
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            "--branch",
            plan.tag,
            "--single-branch",
            plan.clone_url,
            str(clone),
        ),
        environment=environment,
        phase="anonymous_clone",
    )
    object_type = _run_git(
        ("cat-file", "-t", f"refs/tags/{plan.tag}"),
        environment=environment,
        cwd=clone,
        phase="tag_type",
    )
    if object_type != b"tag":
        raise PublicReleaseError("annotated_tag_required")
    commit = _run_git(
        ("rev-parse", f"{plan.tag}^{{commit}}"),
        environment=environment,
        cwd=clone,
        phase="tag_commit",
    ).decode("ascii", errors="strict")
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise PublicReleaseError("tag_commit_invalid")
    _run_git(
        ("checkout", "--detach", commit),
        environment=environment,
        cwd=clone,
        phase="tag_checkout",
    )
    return clone, commit


def _request_bytes(url: str, *, limit: int, phase: str) -> bytes:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise PublicReleaseError(f"{phase}_url_invalid")
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mercury-release-verifier"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme != "https":
                raise PublicReleaseError(f"{phase}_redirect_invalid")
            chunks: list[bytes] = []
            size = 0
            while chunk := response.read(64 * 1024):
                size += len(chunk)
                if size > limit:
                    raise PublicReleaseError(f"{phase}_too_large")
                chunks.append(chunk)
    except PublicReleaseError:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise PublicReleaseError(f"{phase}_request_failed") from exc
    return b"".join(chunks)


def _release_metadata(plan: PublicReleasePlan) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{plan.repo}/releases/tags/{plan.release}"
    try:
        payload = json.loads(
            _request_bytes(url, limit=_MAX_METADATA_BYTES, phase="release_metadata")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PublicReleaseError("release_metadata_invalid") from exc
    if not isinstance(payload, dict) or payload.get("tag_name") != plan.release:
        raise PublicReleaseError("release_metadata_invalid")
    return payload


def _asset_urls(metadata: Mapping[str, Any]) -> dict[str, str]:
    assets = metadata.get("assets")
    if not isinstance(assets, list):
        raise PublicReleaseError("release_assets_invalid")
    result: dict[str, str] = {}
    for item in assets:
        if not isinstance(item, dict):
            raise PublicReleaseError("release_assets_invalid")
        name = item.get("name")
        url = item.get("browser_download_url")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not isinstance(url, str)
            or name in result
        ):
            raise PublicReleaseError("release_assets_invalid")
        result[name] = url
    return result


def _download_and_verify_assets(
    plan: PublicReleasePlan,
    metadata: Mapping[str, Any],
    commit: str,
) -> None:
    urls = _asset_urls(metadata)
    manifest_url = urls.get(MANIFEST_FILE_NAME)
    if manifest_url is None:
        raise PublicReleaseError("release_manifest_missing")
    asset_root = plan.workspace / "release-assets"
    asset_root.mkdir(mode=0o700)
    manifest_path = asset_root / MANIFEST_FILE_NAME
    manifest_path.write_bytes(
        _request_bytes(manifest_url, limit=_MAX_METADATA_BYTES, phase="release_manifest")
    )
    try:
        manifest = load_release_artifact_manifest(manifest_path)
    except (OSError, ReleaseGateError) as exc:
        raise PublicReleaseError("release_manifest_invalid") from exc
    version = plan.tag.removeprefix("v")
    if manifest.version != version or manifest.commit_sha != commit:
        raise PublicReleaseError("release_manifest_identity_mismatch")
    expected_names = {MANIFEST_FILE_NAME, *(item.file_name for item in manifest.artifacts)}
    if set(urls) != expected_names:
        raise PublicReleaseError("release_asset_set_mismatch")
    if {item.kind for item in manifest.artifacts} != {"wheel", "sdist", "plugin", "source"}:
        raise PublicReleaseError("release_artifact_kind_mismatch")

    for artifact in manifest.artifacts:
        data = _request_bytes(
            urls[artifact.file_name],
            limit=_MAX_ASSET_BYTES,
            phase="release_asset",
        )
        if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise PublicReleaseError("release_asset_digest_mismatch")


def _verify_quickstart(clone: Path) -> None:
    try:
        text = (clone / "docs" / "JUDGE_QUICKSTART.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise PublicReleaseError("judge_quickstart_missing") from exc
    if any(marker not in text for marker in _QUICKSTART_MARKERS):
        raise PublicReleaseError("judge_quickstart_incomplete")


def verify_public_release(plan: PublicReleasePlan) -> None:
    """Run the anonymous clone, asset, marketplace, MCP, and docs checks."""

    _prepare_workspace(plan.workspace)
    environment = _anonymous_environment(plan)
    clone, commit = _clone_and_verify_tag(plan, environment)
    _download_and_verify_assets(plan, _release_metadata(plan), commit)
    _verify_quickstart(clone)
    tagged_plan = build_tagged_smoke_plan(
        repo=plan.repo,
        tag=plan.tag,
        expected_tools=plan.expected_tools,
        codex_home=Path(plan.environment["CODEX_HOME"]),
    )
    try:
        run_tagged_smoke(tagged_plan, base_environment=environment)
    except TaggedMarketplaceError as exc:
        raise PublicReleaseError("anonymous_marketplace_smoke_failed") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--expected-tools", required=True, type=int)
    parser.add_argument("--workspace", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.workspace is not None:
        plan = build_public_release_plan(
            repo=args.repo,
            tag=args.tag,
            release=args.release,
            expected_tools=args.expected_tools,
            workspace=args.workspace,
        )
        verify_public_release(plan)
    else:
        with tempfile.TemporaryDirectory(prefix="mercury-public-release-") as temporary:
            plan = build_public_release_plan(
                repo=args.repo,
                tag=args.tag,
                release=args.release,
                expected_tools=args.expected_tools,
                workspace=Path(temporary),
            )
            verify_public_release(plan)
    print("public release verification passed (anonymous exact-tag surface)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (PublicReleaseError, UnicodeError) as error:
        print(f"public release verification failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
