#!/usr/bin/env python3
"""Verify an original release-control attestation before publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from assemble_trusted_attestation import (  # noqa: PLC2701
    AttestationError,
    assemble_attestation,
)

_TOP_KEYS = {
    "completed_at",
    "flowaccount",
    "preflight",
    "producer",
    "public_surface_manifest_sha256",
    "render",
    "reviewed_commit_sha",
    "reviewed_repository",
    "schema_version",
    "secret_scan_allowlist_sha256",
    "staging",
    "supabase",
    "surfaces",
}
_PRODUCER_KEYS = {
    "commit_sha",
    "repository",
    "workflow_run_attempt",
    "workflow_run_id",
}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_MAX_BYTES = 512 * 1024
_MAX_AGE = timedelta(hours=24)
_MAX_FUTURE_SKEW = timedelta(minutes=5)


class VerificationError(RuntimeError):
    """A constant-code trusted-attestation verification failure."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("attestation_duplicate_key")
        result[key] = value
    return result


def _load_json(path: Path, *, max_bytes: int) -> Mapping[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > max_bytes:
            raise VerificationError("attestation_input_invalid")
        value = json.loads(encoded, object_pairs_hook=_pairs)
    except VerificationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VerificationError("attestation_input_invalid") from exc
    if not isinstance(value, Mapping):
        raise VerificationError("attestation_input_invalid")
    return value


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise VerificationError("attestation_time_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError("attestation_time_invalid") from exc
    if parsed.tzinfo is None:
        raise VerificationError("attestation_time_invalid")
    return parsed.astimezone(UTC)


def verify_original_attestation(
    *,
    attestation_path: Path,
    policy_path: Path,
    expected_payload_sha256: str,
    expected_reviewed_sha: str,
    expected_producer_repository: str,
    expected_producer_sha: str,
    expected_producer_run_id: int,
    expected_producer_run_attempt: int,
    expected_staging_ref: str,
    now: datetime | None = None,
) -> dict[str, object]:
    if (
        _SHA256_PATTERN.fullmatch(expected_payload_sha256) is None
        or _SHA_PATTERN.fullmatch(expected_reviewed_sha) is None
        or _SHA_PATTERN.fullmatch(expected_producer_sha) is None
        or expected_producer_run_id <= 0
        or expected_producer_run_attempt <= 0
    ):
        raise VerificationError("attestation_expectation_invalid")
    try:
        encoded = attestation_path.read_bytes()
    except OSError as exc:
        raise VerificationError("attestation_input_invalid") from exc
    if hashlib.sha256(encoded).hexdigest() != expected_payload_sha256:
        raise VerificationError("attestation_payload_digest_mismatch")

    payload = _load_json(attestation_path, max_bytes=_MAX_BYTES)
    policy = _load_json(policy_path, max_bytes=_MAX_BYTES)
    if set(payload) != _TOP_KEYS or payload.get("schema_version") != 2:
        raise VerificationError("attestation_schema_invalid")
    producer = payload.get("producer")
    if not isinstance(producer, Mapping) or set(producer) != _PRODUCER_KEYS:
        raise VerificationError("attestation_producer_invalid")
    if producer != {
        "repository": expected_producer_repository,
        "commit_sha": expected_producer_sha,
        "workflow_run_id": expected_producer_run_id,
        "workflow_run_attempt": expected_producer_run_attempt,
    }:
        raise VerificationError("attestation_producer_mismatch")
    if payload.get("reviewed_commit_sha") != expected_reviewed_sha:
        raise VerificationError("attestation_reviewed_commit_mismatch")

    completed_at = _parse_timestamp(payload.get("completed_at"))
    current = (now or datetime.now(UTC)).astimezone(UTC)
    if completed_at > current + _MAX_FUTURE_SKEW or current - completed_at > _MAX_AGE:
        raise VerificationError("attestation_stale")

    evidence = {
        "schema_version": 1,
        "reviewed_repository": payload.get("reviewed_repository"),
        "reviewed_commit_sha": payload.get("reviewed_commit_sha"),
        "public_surface_manifest_sha256": payload.get("public_surface_manifest_sha256"),
        "secret_scan_allowlist_sha256": payload.get("secret_scan_allowlist_sha256"),
        "flowaccount": payload.get("flowaccount"),
        "staging": payload.get("staging"),
        "render": payload.get("render"),
        "supabase": payload.get("supabase"),
        "surfaces": payload.get("surfaces"),
        "completed_at": payload.get("completed_at"),
    }
    try:
        rebuilt = assemble_attestation(
            evidence=evidence,
            preflight=payload.get("preflight"),
            policy=policy,
            producer_repository=expected_producer_repository,
            producer_sha=expected_producer_sha,
            producer_run_id=expected_producer_run_id,
            producer_run_attempt=expected_producer_run_attempt,
            staging_ref=expected_staging_ref,
            manifest_sha256=payload.get("public_surface_manifest_sha256"),
            allowlist_sha256=payload.get("secret_scan_allowlist_sha256"),
        )
    except (AssertionError, AttestationError, TypeError) as exc:
        raise VerificationError("attestation_contract_invalid") from exc
    if rebuilt != dict(payload):
        raise VerificationError("attestation_contract_invalid")
    return {
        "status": "ok",
        "payload_sha256": expected_payload_sha256,
        "reviewed_commit_sha": expected_reviewed_sha,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--attestation", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--payload-sha256", required=True)
    parser.add_argument("--reviewed-sha", required=True)
    parser.add_argument("--producer-repository", required=True)
    parser.add_argument("--producer-sha", required=True)
    parser.add_argument("--producer-run-id", type=int, required=True)
    parser.add_argument("--producer-run-attempt", type=int, required=True)
    parser.add_argument("--staging-ref", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_original_attestation(
            attestation_path=args.attestation,
            policy_path=args.policy,
            expected_payload_sha256=args.payload_sha256,
            expected_reviewed_sha=args.reviewed_sha,
            expected_producer_repository=args.producer_repository,
            expected_producer_sha=args.producer_sha,
            expected_producer_run_id=args.producer_run_id,
            expected_producer_run_attempt=args.producer_run_attempt,
            expected_staging_ref=args.staging_ref,
        )
    except VerificationError as exc:
        print(f"original attestation verification failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
