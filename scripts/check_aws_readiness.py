#!/usr/bin/env python3
"""Run local and live AWS Wave 0 readiness checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.models import CheckResult, CheckState, GateStatus
from mercury_tools.aws.readiness import (
    build_readiness_report,
    check_aws_accounts,
    check_local_toolchain,
    check_region_services,
    write_readiness_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("infra/aws/wave0/environment.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".artifacts/aws/wave0/readiness.json"),
    )
    parser.add_argument("--skip-live", action="store_true")
    return parser


def _skipped_live_checks() -> tuple[CheckResult, ...]:
    return (
        CheckResult(
            name="aws_live_checks",
            state=CheckState.BLOCKED,
            code="aws_live_checks_skipped",
            summary="Live AWS checks were explicitly skipped.",
            details={},
        ),
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = load_wave0_config(args.config)
        local_checks = check_local_toolchain()
        live_checks = (
            _skipped_live_checks()
            if args.skip_live
            else (*check_aws_accounts(config), *check_region_services(config))
        )
        report = build_readiness_report(config, (*local_checks, *live_checks))
        write_readiness_report(report, args.output)
    except (OSError, ValueError, ValidationError):
        print("wave0_readiness_invalid_input")
        return 3

    print(f"gate_status={report.gate_status.value}")
    print(f"report={args.output}")
    return 0 if report.gate_status is GateStatus.READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
