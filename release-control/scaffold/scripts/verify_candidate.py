#!/usr/bin/env python3
"""Verify a release-control candidate as data using only trusted base code."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType

MAX_FILE_BYTES = 4 * 1024 * 1024
CRITICAL_ROOTS = (
    ".github/workflows",
    "bin",
    "scripts",
)
CRITICAL_FILES = (
    "release-notes-v0.2.1.md",
    "requirements-inspector.txt",
)
FORBIDDEN_BYTECODE_SUFFIXES = frozenset({".pyc", ".pyo"})


class CandidateVerificationError(RuntimeError):
    """A constant-code failure for an untrusted release-control candidate."""


def _regular_bytes(path: Path) -> bytes:
    try:
        metadata = path.lstat()
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size <= 0
            or metadata.st_size > MAX_FILE_BYTES
        ):
            raise CandidateVerificationError("candidate_control_invalid")
        return path.read_bytes()
    except CandidateVerificationError:
        raise
    except OSError as exc:
        raise CandidateVerificationError("candidate_control_invalid") from exc


def _critical_paths(root: Path) -> tuple[str, ...]:
    paths = list(CRITICAL_FILES)
    try:
        for relative_root in CRITICAL_ROOTS:
            directory = root / relative_root
            metadata = directory.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise CandidateVerificationError("candidate_control_invalid")
            for path in directory.rglob("*"):
                relative = path.relative_to(root)
                if (
                    "__pycache__" in relative.parts
                    or path.suffix in FORBIDDEN_BYTECODE_SUFFIXES
                ):
                    raise CandidateVerificationError("candidate_control_invalid")
                if path.is_file():
                    paths.append(relative.as_posix())
    except CandidateVerificationError:
        raise
    except OSError as exc:
        raise CandidateVerificationError("candidate_control_invalid") from exc
    return tuple(sorted(paths))


def _strict_json(path: Path) -> Mapping[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                raise CandidateVerificationError("candidate_policy_invalid")
            result[key] = value
        return result

    try:
        payload = json.loads(_regular_bytes(path), object_pairs_hook=pairs)
    except CandidateVerificationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateVerificationError("candidate_policy_invalid") from exc
    if not isinstance(payload, Mapping):
        raise CandidateVerificationError("candidate_policy_invalid")
    return payload


def _trusted_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CandidateVerificationError("trusted_verifier_invalid")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(name)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise CandidateVerificationError("trusted_verifier_invalid") from exc
    finally:
        if previous is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = previous
    return module


def verify_candidate(*, trusted_root: Path, candidate_root: Path) -> None:
    trusted_root = trusted_root.resolve()
    candidate_root = candidate_root.resolve()
    if trusted_root == candidate_root:
        raise CandidateVerificationError("candidate_root_invalid")

    trusted_paths = _critical_paths(trusted_root)
    candidate_paths = _critical_paths(candidate_root)
    if candidate_paths != trusted_paths:
        raise CandidateVerificationError("candidate_control_drift")
    for relative_path in trusted_paths:
        if _regular_bytes(trusted_root / relative_path) != _regular_bytes(
            candidate_root / relative_path
        ):
            raise CandidateVerificationError("candidate_control_drift")

    for relative_path in trusted_paths:
        if relative_path.endswith(".py") or relative_path.startswith("bin/"):
            try:
                ast.parse(
                    _regular_bytes(candidate_root / relative_path),
                    filename=relative_path,
                )
            except (SyntaxError, UnicodeError) as exc:
                raise CandidateVerificationError("candidate_python_invalid") from exc

    policy = _strict_json(candidate_root / "policy-v0.2.1.json")
    core = _trusted_module(
        trusted_root / "scripts" / "inspector_core.py",
        "trusted_release_control_inspector_core",
    )
    preflight = _trusted_module(
        trusted_root / "scripts" / "verify_remote_preflight.py",
        "trusted_release_control_preflight",
    )
    try:
        core.validate_policy(policy)
        preflight._validate_policy(policy)
    except Exception as exc:
        raise CandidateVerificationError("candidate_policy_invalid") from exc
    inspector = policy.get("inspector")
    if not isinstance(inspector, Mapping):
        raise CandidateVerificationError("candidate_policy_invalid")
    expected_inspector_sha256 = inspector.get("sha256")
    if (
        not isinstance(expected_inspector_sha256, str)
        or expected_inspector_sha256
        != hashlib.sha256(
            _regular_bytes(candidate_root / "bin" / "mercury-release-control-inspector")
        ).hexdigest()
    ):
        raise CandidateVerificationError("candidate_policy_invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusted-root", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        verify_candidate(
            trusted_root=args.trusted_root,
            candidate_root=args.candidate_root,
        )
    except CandidateVerificationError as exc:
        print(f"release-control candidate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
