#!/usr/bin/env python3
"""Verify pytest JUnit skips against exact, approved release waivers."""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from pathlib import Path

_WAIVER_FIELDS = {
    "test_id",
    "rationale",
    "owner_role",
    "expires_at",
    "release_approved",
}
_MAX_JUNIT_BYTES = 32 * 1024 * 1024
_MAX_TESTCASES = 100_000
_MAX_WAIVERS = 10_000
_TEST_ID_RE = re.compile(r"^[A-Za-z0-9_./:\[\],=+ -]{1,500}$")
_OWNER_RE = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_NON_WAIVABLE_MARKERS = (
    "test_release_secret",
    "test_security",
    "test_redaction",
    "migration",
    "test_mcp",
    "_mcp_",
    "mcp_contract",
    "test_release",
    "test_plugin_clean_install",
    "test_flowaccount_sandbox_qualification",
)


class SkipVerificationError(RuntimeError):
    """Raised when the release JUnit skip contract is not exact."""


def _safe_test_id(value: object) -> str:
    if not isinstance(value, str) or _TEST_ID_RE.fullmatch(value) is None:
        raise SkipVerificationError("junit_test_id_invalid")
    return value


def _case_test_id(case: ET.Element) -> str:
    properties = case.find("properties")
    if properties is not None:
        explicit = [
            item.get("value")
            for item in properties.findall("property")
            if item.get("name") == "test_id"
        ]
        if len(explicit) == 1:
            return _safe_test_id(explicit[0])
        if explicit:
            raise SkipVerificationError("junit_test_id_invalid")

    classname = case.get("classname")
    name = case.get("name")
    if not classname or not name:
        raise SkipVerificationError("junit_test_id_missing")
    path = classname.replace(".", "/") + ".py"
    return _safe_test_id(f"{path}::{name}")


def _load_skipped_test_ids(path: Path) -> tuple[str, ...]:
    try:
        if path.stat().st_size > _MAX_JUNIT_BYTES:
            raise SkipVerificationError("junit_size_exceeded")
        root = ET.parse(path).getroot()
    except SkipVerificationError:
        raise
    except (OSError, ET.ParseError) as exc:
        raise SkipVerificationError("junit_invalid") from exc

    skipped: list[str] = []
    for count, case in enumerate(root.iter("testcase"), start=1):
        if count > _MAX_TESTCASES:
            raise SkipVerificationError("junit_testcase_limit_exceeded")
        if case.find("skipped") is not None:
            skipped.append(_case_test_id(case))
    if len(skipped) != len(set(skipped)):
        raise SkipVerificationError("junit_duplicate_skip")
    return tuple(sorted(skipped))


def _load_waivers(path: Path, *, today: date) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SkipVerificationError("waiver_json_invalid") from exc
    if not isinstance(payload, list) or len(payload) > _MAX_WAIVERS:
        raise SkipVerificationError("waiver_schema_invalid")

    waivers: dict[str, dict[str, object]] = {}
    errors: list[str] = []
    for row in payload:
        if not isinstance(row, dict) or set(row) != _WAIVER_FIELDS:
            errors.append("waiver_schema_invalid")
            continue
        try:
            test_id = _safe_test_id(row["test_id"])
        except SkipVerificationError:
            errors.append("waiver_schema_invalid")
            continue
        rationale = row["rationale"]
        owner_role = row["owner_role"]
        expires_at = row["expires_at"]
        if (
            not isinstance(rationale, str)
            or not 20 <= len(rationale.strip()) <= 1_000
            or not isinstance(owner_role, str)
            or _OWNER_RE.fullmatch(owner_role) is None
            or not isinstance(expires_at, str)
        ):
            errors.append(f"waiver_schema_invalid:{test_id}")
            continue
        try:
            expiry = date.fromisoformat(expires_at)
        except ValueError:
            errors.append(f"waiver_schema_invalid:{test_id}")
            continue
        if expiry.isoformat() != expires_at:
            errors.append(f"waiver_schema_invalid:{test_id}")
        if row["release_approved"] is not True:
            errors.append(f"waiver_unapproved:{test_id}")
        if expiry < today:
            errors.append(f"waiver_expired:{test_id}")
        if test_id in waivers:
            errors.append(f"waiver_duplicate:{test_id}")
        waivers[test_id] = row
    if errors:
        raise SkipVerificationError(";".join(sorted(set(errors))))
    return waivers


def _is_non_waivable(test_id: str) -> bool:
    lowered = test_id.casefold()
    return any(marker in lowered for marker in _NON_WAIVABLE_MARKERS)


def verify_test_skips(
    junit: Path,
    waivers: Path,
    *,
    today: date | None = None,
) -> tuple[str, ...]:
    """Return exact skipped test IDs, or raise when the release gate is not valid."""

    effective_today = today or datetime.now(UTC).date()
    skipped = _load_skipped_test_ids(junit)
    approved = _load_waivers(waivers, today=effective_today)
    skipped_set = set(skipped)
    waiver_set = set(approved)

    errors: list[str] = []
    for test_id in skipped:
        if _is_non_waivable(test_id):
            errors.append(f"non_waivable_skip:{test_id}")
        elif test_id not in approved:
            errors.append(f"unknown_skip:{test_id}")
    if skipped_set != waiver_set:
        errors.append("waiver_set_mismatch")
        errors.extend(f"unused_waiver:{test_id}" for test_id in sorted(waiver_set - skipped_set))
    if errors:
        raise SkipVerificationError(";".join(sorted(set(errors))))
    return skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--waivers", type=Path, required=True)
    args = parser.parse_args()
    try:
        skipped = verify_test_skips(args.junit, args.waivers)
    except SkipVerificationError as exc:
        print(f"test skip verification failed: {exc}", file=sys.stderr)
        return 1
    if skipped:
        print("test skip verification passed: " + ", ".join(skipped))
    else:
        print("test skip verification passed: no skips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
