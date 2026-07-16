#!/usr/bin/env python3
"""Assemble a strict sanitized attestation from pinned-inspector evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from verify_remote_preflight import (  # noqa: PLC2701
    REQUIRED_FUNCTIONS,
    PreflightError,
    _validate_policy,
    build_supabase_schema_digest,
)

TRUSTED_SURFACES = (
    "git_all_refs",
    "github_pull_request_refs",
    "github_releases_and_assets",
    "github_actions_logs_artifacts_caches",
    "github_packages_pages_wiki",
    "marketplace_snapshot",
    "render_build_and_runtime_logs",
    "supabase_knowledge_and_storage",
    "public_mcp_responses",
)
_HISTORY_VERSIONS = ("1.0.0", "3.88.32", "8.24.3")
_BUILTIN_VERSION = ("1.0.0",)
_EVIDENCE_KEYS = {
    "completed_at",
    "flowaccount",
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
_PREFLIGHT_KEYS = {
    "admin_bypass_disabled",
    "approved_supabase_migration_history_sha256",
    "approved_supabase_project_ref_sha256",
    "approved_supabase_schema_sha256",
    "environment",
    "prevent_self_review",
    "protected_branch_only",
    "repository_visibility",
    "required_configuration_sha256",
    "required_reviewers",
}
_SURFACE_KEYS = {
    "blocker_codes",
    "completed_at",
    "evidence_hashes",
    "exit_codes",
    "finding_codes",
    "finding_count",
    "scanner_versions",
    "started_at",
    "status",
    "surface",
}
_FLOWACCOUNT_KEYS = {
    "report_sha256",
    "required_live_test_passed",
    "terminal_records",
    "total",
}
_STAGING_KEYS = {
    "commit_sha",
    "local_tool_count",
    "ref",
    "repository",
    "tree_sha256",
}
_RENDER_KEYS = {
    "deployment_commit",
    "evidence_sha256",
    "hosted_tool_count",
    "version",
}
_SUPABASE_KEYS = {
    "functions",
    "migration_history_sha256",
    "migration_id",
    "project_ref",
    "project_ref_sha256",
    "schema_sha256",
    "storage_buckets",
    "tables",
}
_FUNCTION_KEYS = {"definition_sha256", "signature"}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REF_PATTERN = re.compile(r"^v0\.2\.1-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*$")
_RFC3339_TIMESTAMP_PATTERN = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"T(?P<hour>[01]\d|2[0-3]):(?P<minute>[0-5]\d):(?P<second>[0-5]\d)"
    r"(?P<fraction>\.\d{1,6})?"
    r"(?P<offset>Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$"
)
_MAX_INPUT_BYTES = 2 * 1024 * 1024


class AttestationError(RuntimeError):
    """A constant-code sanitized-attestation assembly failure."""


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise AttestationError("json_duplicate_key")
        payload[key] = value
    return payload


def _load(path: Path, code: str) -> Mapping[str, object]:
    try:
        encoded = path.read_bytes()
        if not encoded or len(encoded) > _MAX_INPUT_BYTES:
            raise AttestationError(code)
        payload = json.loads(encoded, object_pairs_hook=_pairs)
    except AttestationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(code) from exc
    if not isinstance(payload, Mapping):
        raise AttestationError(code)
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise AttestationError("candidate_input_unavailable") from exc
    return digest.hexdigest()


def _keys(value: Mapping[str, object], expected: set[str], code: str) -> None:
    if set(value) != expected:
        raise AttestationError(code)


def _mapping(value: object, code: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise AttestationError(code)
    return value


def _sha(value: object, code: str, *, commit: bool = False) -> str:
    pattern = _SHA_PATTERN if commit else _SHA256_PATTERN
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise AttestationError(code)
    return value


def _timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        raise AttestationError(code)
    matched = _RFC3339_TIMESTAMP_PATTERN.fullmatch(value)
    if matched is None or matched["offset"] == "-00:00":
        raise AttestationError(code)
    try:
        parsed = datetime.fromisoformat(
            f"{value[:-1]}+00:00" if matched["offset"] == "Z" else value
        )
    except ValueError as exc:
        raise AttestationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AttestationError(code)
    return parsed.astimezone(UTC)


def _serialize_timestamp(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    timestamp = (
        f"{normalized.year:04d}-{normalized.month:02d}-{normalized.day:02d}"
        f"T{normalized.hour:02d}:{normalized.minute:02d}:{normalized.second:02d}"
    )
    fraction = f"{normalized.microsecond:06d}".rstrip("0")
    if fraction:
        timestamp += f".{fraction}"
    return f"{timestamp}Z"


def _validate_preflight(
    preflight: Mapping[str, object],
    policy: Mapping[str, object],
) -> None:
    _keys(preflight, _PREFLIGHT_KEYS, "preflight_invalid")
    if (
        preflight.get("environment") != "production-release"
        or preflight.get("repository_visibility") != "public"
        or not isinstance(preflight.get("required_reviewers"), int)
        or preflight["required_reviewers"] < 1
        or preflight.get("prevent_self_review") is not True
        or preflight.get("admin_bypass_disabled") is not True
        or preflight.get("protected_branch_only") is not True
    ):
        raise AttestationError("preflight_invalid")
    supabase = _mapping(policy["supabase"], "policy_invalid")
    project_ref = supabase.get("project_ref")
    assert isinstance(project_ref, str)
    expected = (
        hashlib.sha256(project_ref.encode("utf-8")).hexdigest(),
        supabase.get("migration_history_sha256"),
        supabase.get("schema_sha256"),
    )
    actual = (
        preflight.get("approved_supabase_project_ref_sha256"),
        preflight.get("approved_supabase_migration_history_sha256"),
        preflight.get("approved_supabase_schema_sha256"),
    )
    if actual != expected:
        raise AttestationError("preflight_supabase_policy_mismatch")
    for key in (
        "required_configuration_sha256",
        "approved_supabase_project_ref_sha256",
        "approved_supabase_migration_history_sha256",
        "approved_supabase_schema_sha256",
    ):
        _sha(preflight.get(key), "preflight_invalid")


def _validate_surfaces(
    value: object,
    completed_at: datetime,
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or len(value) != len(TRUSTED_SURFACES):
        raise AttestationError("surface_inventory_invalid")
    surfaces: list[dict[str, object]] = []
    for expected_surface, raw in zip(TRUSTED_SURFACES, value, strict=True):
        surface = _mapping(raw, "surface_invalid")
        _keys(surface, _SURFACE_KEYS, "surface_invalid")
        expected_versions = (
            _HISTORY_VERSIONS
            if expected_surface in {"git_all_refs", "github_pull_request_refs"}
            else _BUILTIN_VERSION
        )
        if (
            surface.get("surface") != expected_surface
            or surface.get("status") != "passed"
            or tuple(surface.get("scanner_versions", ())) != expected_versions
            or surface.get("finding_count") != 0
            or surface.get("blocker_codes") != []
            or surface.get("finding_codes") != []
            or surface.get("exit_codes") != [0]
        ):
            raise AttestationError("surface_invalid")
        hashes = surface.get("evidence_hashes")
        if not isinstance(hashes, list) or not hashes:
            raise AttestationError("surface_invalid")
        for digest in hashes:
            _sha(digest, "surface_invalid")
        started_at = _timestamp(surface.get("started_at"), "surface_invalid")
        surface_completed_at = _timestamp(
            surface.get("completed_at"),
            "surface_invalid",
        )
        if started_at > surface_completed_at or surface_completed_at > completed_at:
            raise AttestationError("surface_invalid")
        normalized = dict(surface)
        normalized["started_at"] = _serialize_timestamp(started_at)
        normalized["completed_at"] = _serialize_timestamp(surface_completed_at)
        surfaces.append(normalized)
    return tuple(surfaces)


def _validate_supabase(
    observed: Mapping[str, object],
    policy: Mapping[str, object],
) -> None:
    _keys(observed, _SUPABASE_KEYS, "supabase_evidence_invalid")
    approved = _mapping(policy["supabase"], "policy_invalid")
    project_ref = approved.get("project_ref")
    assert isinstance(project_ref, str)
    if observed.get("project_ref") != project_ref:
        raise AttestationError("supabase_project_mismatch")
    if observed.get("project_ref_sha256") != hashlib.sha256(
        project_ref.encode("utf-8")
    ).hexdigest():
        raise AttestationError("supabase_project_mismatch")
    for key in (
        "migration_id",
        "migration_history_sha256",
        "tables",
        "storage_buckets",
        "functions",
        "schema_sha256",
    ):
        if observed.get(key) != approved.get(key):
            raise AttestationError("supabase_approved_state_mismatch")
    functions = observed.get("functions")
    if not isinstance(functions, list) or tuple(
        item.get("signature") if isinstance(item, Mapping) else None
        for item in functions
    ) != REQUIRED_FUNCTIONS:
        raise AttestationError("supabase_function_inventory_invalid")
    for function in functions:
        item = _mapping(function, "supabase_function_inventory_invalid")
        _keys(item, _FUNCTION_KEYS, "supabase_function_inventory_invalid")
        _sha(item.get("definition_sha256"), "supabase_function_digest_invalid")
    if observed.get("schema_sha256") != build_supabase_schema_digest(observed):
        raise AttestationError("supabase_schema_digest_invalid")


def assemble_attestation(
    *,
    evidence: Mapping[str, object],
    preflight: Mapping[str, object],
    policy: Mapping[str, object],
    producer_repository: str,
    producer_sha: str,
    producer_run_id: int,
    producer_run_attempt: int,
    staging_ref: str,
    manifest_sha256: str,
    allowlist_sha256: str,
) -> dict[str, object]:
    _validate_policy(policy)
    _keys(evidence, _EVIDENCE_KEYS, "evidence_schema_invalid")
    _validate_preflight(preflight, policy)
    if (
        evidence.get("schema_version") != 1
        or evidence.get("reviewed_repository") != policy["reviewed_repository"]
        or evidence.get("public_surface_manifest_sha256") != manifest_sha256
        or evidence.get("secret_scan_allowlist_sha256") != allowlist_sha256
    ):
        raise AttestationError("candidate_binding_invalid")
    reviewed_sha = _sha(
        evidence.get("reviewed_commit_sha"),
        "candidate_binding_invalid",
        commit=True,
    )
    _sha(producer_sha, "producer_invalid", commit=True)
    if producer_repository != policy["repository"]:
        raise AttestationError("producer_invalid")
    if producer_run_id <= 0 or producer_run_attempt <= 0:
        raise AttestationError("producer_invalid")
    completed_at = _timestamp(evidence.get("completed_at"), "evidence_time_invalid")

    flowaccount = _mapping(evidence.get("flowaccount"), "flowaccount_invalid")
    _keys(flowaccount, _FLOWACCOUNT_KEYS, "flowaccount_invalid")
    if (
        flowaccount.get("total") != 190
        or flowaccount.get("terminal_records") != 190
        or flowaccount.get("required_live_test_passed") is not True
    ):
        raise AttestationError("flowaccount_invalid")
    _sha(flowaccount.get("report_sha256"), "flowaccount_invalid")

    staging = _mapping(evidence.get("staging"), "staging_invalid")
    _keys(staging, _STAGING_KEYS, "staging_invalid")
    if (
        staging.get("repository") != policy["staging_repository"]
        or staging.get("ref") != staging_ref
        or not isinstance(staging_ref, str)
        or _REF_PATTERN.fullmatch(staging_ref) is None
        or staging.get("local_tool_count") != 19
    ):
        raise AttestationError("staging_invalid")
    _sha(staging.get("commit_sha"), "staging_invalid", commit=True)
    _sha(staging.get("tree_sha256"), "staging_invalid")

    render = _mapping(evidence.get("render"), "render_invalid")
    _keys(render, _RENDER_KEYS, "render_invalid")
    if (
        render.get("deployment_commit") != reviewed_sha
        or render.get("version") != "0.2.1"
        or render.get("hosted_tool_count") != 20
    ):
        raise AttestationError("render_invalid")
    _sha(render.get("evidence_sha256"), "render_invalid")

    supabase = _mapping(evidence.get("supabase"), "supabase_evidence_invalid")
    _validate_supabase(supabase, policy)
    surfaces = _validate_surfaces(evidence.get("surfaces"), completed_at)
    return {
        "schema_version": 2,
        "reviewed_repository": policy["reviewed_repository"],
        "reviewed_commit_sha": reviewed_sha,
        "producer": {
            "repository": producer_repository,
            "commit_sha": producer_sha,
            "workflow_run_id": producer_run_id,
            "workflow_run_attempt": producer_run_attempt,
        },
        "preflight": dict(preflight),
        "public_surface_manifest_sha256": manifest_sha256,
        "secret_scan_allowlist_sha256": allowlist_sha256,
        "flowaccount": dict(flowaccount),
        "staging": dict(staging),
        "render": dict(render),
        "supabase": dict(supabase),
        "surfaces": [dict(surface) for surface in surfaces],
        "completed_at": _serialize_timestamp(completed_at),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--producer-repository", required=True)
    parser.add_argument("--producer-sha", required=True)
    parser.add_argument("--producer-run-id", type=int, required=True)
    parser.add_argument("--producer-run-attempt", type=int, required=True)
    parser.add_argument("--staging-ref", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.output.exists():
            raise AttestationError("attestation_output_exists")
        manifest_sha256 = _sha256(args.manifest)
        allowlist_sha256 = _sha256(args.allowlist)
        attestation = assemble_attestation(
            evidence=_load(args.evidence, "evidence_input_invalid"),
            preflight=_load(args.preflight, "preflight_input_invalid"),
            policy=_load(args.policy, "policy_input_invalid"),
            producer_repository=args.producer_repository,
            producer_sha=args.producer_sha,
            producer_run_id=args.producer_run_id,
            producer_run_attempt=args.producer_run_attempt,
            staging_ref=args.staging_ref,
            manifest_sha256=manifest_sha256,
            allowlist_sha256=allowlist_sha256,
        )
        args.output.write_text(
            json.dumps(attestation, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    except (OSError, AttestationError, PreflightError) as exc:
        print(f"trusted attestation assembly failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": "ok"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
