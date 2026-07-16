#!/usr/bin/env python3
"""Validate the exact attempt-bound Mercury release-ready handoff."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

_TOP_KEYS = {
    "caller",
    "original_release_control",
    "relayed_attestation",
    "release_artifacts",
    "reviewed_commit_sha",
    "schema_version",
    "staging_identity",
    "version",
}
_CALLER_KEYS = {"run_attempt", "run_id"}
_ORIGINAL_CONTROL_KEYS = {
    "artifact_digest",
    "artifact_id",
    "payload_sha256",
    "producer_commit_sha",
    "repository",
    "repository_id",
    "run_attempt",
    "run_id",
    "staging_ref",
    "staging_repository",
    "workflow_path",
}
_ARTIFACT_KEYS = {"artifact_digest", "artifact_id"}
_RELAYED_ATTESTATION_KEYS = {
    "artifact_digest",
    "artifact_id",
    "payload_sha256",
}
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_WORKFLOW_PATH = ".github/workflows/attest-v0.2.1.yml"
_MAX_HANDOFF_BYTES = 128 * 1024


class HandoffError(RuntimeError):
    """A constant-code release-ready handoff failure."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise HandoffError("handoff_duplicate_key")
        payload[key] = value
    return payload


def _mapping(value: object, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise HandoffError("handoff_schema_invalid")
    return value


def _artifact(value: object) -> Mapping[str, object]:
    artifact = _mapping(value, _ARTIFACT_KEYS)
    artifact_id = artifact.get("artifact_id")
    digest = artifact.get("artifact_digest")
    if (
        not isinstance(artifact_id, int)
        or isinstance(artifact_id, bool)
        or artifact_id <= 0
        or not isinstance(digest, str)
        or _DIGEST_PATTERN.fullmatch(digest) is None
    ):
        raise HandoffError("handoff_artifact_invalid")
    return artifact


def validate_handoff(
    payload: Mapping[str, object],
    *,
    expected_reviewed_sha: str,
    expected_caller_run_id: int,
    expected_caller_run_attempt: int,
    expected_control_run_id: int,
    expected_control_run_attempt: int,
    expected_control_repository: str,
    expected_control_repository_id: int,
    expected_control_producer_sha: str,
) -> dict[str, object]:
    _mapping(payload, _TOP_KEYS)
    if (
        payload.get("schema_version") != 2
        or payload.get("version") != "0.2.1"
        or payload.get("reviewed_commit_sha") != expected_reviewed_sha
        or _SHA_PATTERN.fullmatch(expected_reviewed_sha) is None
    ):
        raise HandoffError("handoff_identity_invalid")
    caller = _mapping(payload.get("caller"), _CALLER_KEYS)
    if caller != {
        "run_id": expected_caller_run_id,
        "run_attempt": expected_caller_run_attempt,
    }:
        raise HandoffError("handoff_caller_invalid")
    control = _mapping(
        payload.get("original_release_control"),
        _ORIGINAL_CONTROL_KEYS,
    )
    if (
        control.get("run_id") != expected_control_run_id
        or control.get("run_attempt") != expected_control_run_attempt
        or control.get("repository") != expected_control_repository
        or control.get("repository_id") != expected_control_repository_id
        or control.get("producer_commit_sha") != expected_control_producer_sha
        or control.get("workflow_path") != _WORKFLOW_PATH
        or not isinstance(control.get("staging_repository"), str)
        or _REPOSITORY_PATTERN.fullmatch(control["staging_repository"]) is None
        or not isinstance(control.get("staging_ref"), str)
        or re.fullmatch(
            r"v0\.2\.1-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*",
            control["staging_ref"],
        )
        is None
        or _REPOSITORY_PATTERN.fullmatch(expected_control_repository) is None
        or expected_control_repository_id <= 0
        or _SHA_PATTERN.fullmatch(expected_control_producer_sha) is None
    ):
        raise HandoffError("handoff_control_invalid")
    original_artifact = _artifact(
        {
            "artifact_id": control.get("artifact_id"),
            "artifact_digest": control.get("artifact_digest"),
        }
    )
    source_payload_sha256 = control.get("payload_sha256")
    if (
        not isinstance(source_payload_sha256, str)
        or _DIGEST_PATTERN.fullmatch(source_payload_sha256) is None
    ):
        raise HandoffError("handoff_control_invalid")
    relay = _mapping(payload.get("relayed_attestation"), _RELAYED_ATTESTATION_KEYS)
    relayed_artifact = _artifact(
        {
            "artifact_id": relay.get("artifact_id"),
            "artifact_digest": relay.get("artifact_digest"),
        }
    )
    relayed_payload_sha256 = relay.get("payload_sha256")
    if (
        not isinstance(relayed_payload_sha256, str)
        or _DIGEST_PATTERN.fullmatch(relayed_payload_sha256) is None
        or relayed_payload_sha256 != source_payload_sha256
    ):
        raise HandoffError("handoff_relay_invalid")
    release = _artifact(payload.get("release_artifacts"))
    staging = _artifact(payload.get("staging_identity"))
    return {
        "reviewed_commit_sha": expected_reviewed_sha,
        "caller": dict(caller),
        "original_release_control": {
            "repository": expected_control_repository,
            "repository_id": expected_control_repository_id,
            "producer_commit_sha": expected_control_producer_sha,
            "workflow_path": _WORKFLOW_PATH,
            "staging_repository": control["staging_repository"],
            "staging_ref": control["staging_ref"],
            "run_id": expected_control_run_id,
            "run_attempt": expected_control_run_attempt,
            **dict(original_artifact),
            "payload_sha256": source_payload_sha256,
        },
        "relayed_attestation": {
            **dict(relayed_artifact),
            "payload_sha256": relayed_payload_sha256,
        },
        "release_artifacts": dict(release),
        "staging_identity": dict(staging),
    }


def _load(path: Path) -> Mapping[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > _MAX_HANDOFF_BYTES:
            raise HandoffError("handoff_input_invalid")
        payload = json.loads(encoded, object_pairs_hook=_pairs)
    except HandoffError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HandoffError("handoff_input_invalid") from exc
    if not isinstance(payload, Mapping):
        raise HandoffError("handoff_input_invalid")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--caller-run-id", type=int, required=True)
    parser.add_argument("--caller-run-attempt", type=int, required=True)
    parser.add_argument("--control-run-id", type=int, required=True)
    parser.add_argument("--control-run-attempt", type=int, required=True)
    parser.add_argument("--control-repository", required=True)
    parser.add_argument("--control-repository-id", type=int, required=True)
    parser.add_argument("--control-producer-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.output.exists():
            raise HandoffError("handoff_output_exists")
        result = validate_handoff(
            _load(args.handoff),
            expected_reviewed_sha=args.reviewed_sha,
            expected_caller_run_id=args.caller_run_id,
            expected_caller_run_attempt=args.caller_run_attempt,
            expected_control_run_id=args.control_run_id,
            expected_control_run_attempt=args.control_run_attempt,
            expected_control_repository=args.control_repository,
            expected_control_repository_id=args.control_repository_id,
            expected_control_producer_sha=args.control_producer_sha,
        )
        args.output.write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, HandoffError) as exc:
        print(f"release-ready handoff failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
