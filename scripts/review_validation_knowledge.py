#!/usr/bin/env python3
"""Review complete connector validation reports into one deterministic artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from mercury_tools.qualification.models import QualificationReport
from mercury_tools.qualification.publisher import (
    REVIEWER_ROLES,
    ReviewedValidationReport,
    load_catalog_definitions,
    review_validation_report,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG_ROOT = _REPOSITORY_ROOT / "catalog" / "global"
_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


def review_inputs(
    input_paths: Sequence[Path],
    *,
    reviewer_role: str,
    catalog_root: Path,
) -> ReviewedValidationReport:
    if (
        isinstance(input_paths, (str, bytes, bytearray))
        or not isinstance(input_paths, Sequence)
        or not input_paths
    ):
        raise ValueError("validation_review_inputs_invalid")
    if reviewer_role not in REVIEWER_ROLES:
        raise ValueError("validation_reviewer_role_invalid")

    catalog = load_catalog_definitions(catalog_root)
    reviewed_by_connector: dict[str, ReviewedValidationReport] = {}
    for input_path in sorted(input_paths, key=lambda path: path.as_posix()):
        try:
            payload = json.loads(input_path.read_text(encoding="utf-8"))
            report = QualificationReport.model_validate(payload)
        except (AttributeError, OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("validation_report_invalid") from None
        if report.connector_id in reviewed_by_connector:
            raise ValueError("validation_review_inputs_duplicate")
        reviewed_by_connector[report.connector_id] = review_validation_report(
            report,
            reviewer_role=reviewer_role,
            catalog=catalog,
        )

    records = tuple(
        sorted(
            (
                record
                for reviewed in reviewed_by_connector.values()
                for record in reviewed.records
            ),
            key=lambda record: (
                record.connector_id,
                record.action_id,
                record.version_id,
            ),
        )
    )
    return ReviewedValidationReport(records=records, reviewer_role=reviewer_role)


def write_reviewed_report(report: ReviewedValidationReport, output_path: Path) -> None:
    try:
        validated = ReviewedValidationReport.model_validate(report)
        serialized = json.dumps(
            validated.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(f"{serialized}\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):
        raise ValueError("validation_review_output_failed") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Review endpoint validation knowledge.")
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--reviewer-role", choices=sorted(REVIEWER_ROLES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=_DEFAULT_CATALOG_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        reviewed = review_inputs(
            args.input,
            reviewer_role=args.reviewer_role,
            catalog_root=args.catalog_root,
        )
        write_reviewed_report(reviewed, args.output)
    except (RuntimeError, ValueError) as error:
        code = str(error) if _SAFE_ERROR.fullmatch(str(error)) else "validation_review_failed"
        print(code, file=sys.stderr)
        return 1
    print(f"reviewed_records={len(reviewed.records)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
