#!/usr/bin/env python3
"""Run local and live AWS Wave 0 readiness checks."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import ValidationError

from mercury_tools.aws.commands import CommandRunner, run_command
from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.identity import read_identity_decision
from mercury_tools.aws.models import (
    CheckResult,
    CheckState,
    EnvironmentName,
    GateStatus,
    ReadinessReport,
)
from mercury_tools.aws.readiness import (
    OidcRunEvidence,
    OidcRunReference,
    build_readiness_report,
    check_aws_accounts,
    check_local_toolchain,
    check_region_services,
    finalize_wave0_gate,
    write_readiness_report,
)

_DEFAULT_IDENTITY_DECISION = Path("infra/aws/wave0/identity-decision.yaml")


class _CliInputError(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliInputError from None


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
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
    parser.add_argument(
        "--identity-decision",
        type=Path,
        default=_DEFAULT_IDENTITY_DECISION,
    )
    parser.add_argument(
        "--oidc-run",
        action="append",
        default=[],
        metavar="ENV=URL",
        help=(
            "Verified GitHub run bound explicitly to nonprod or production; "
            "provide once for each environment."
        ),
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


def _parse_oidc_references(values: list[str]) -> tuple[OidcRunReference, ...]:
    if len(values) > len(EnvironmentName):
        raise ValueError("wave0_oidc_bindings_invalid")
    references: list[OidcRunReference] = []
    for value in values:
        environment_value, separator, run_url = value.partition("=")
        if not separator or not environment_value or not run_url:
            raise ValueError("wave0_oidc_bindings_invalid")
        try:
            environment = EnvironmentName(environment_value)
        except ValueError:
            raise ValueError("wave0_oidc_bindings_invalid") from None
        references.append(OidcRunReference(environment=environment, run_url=run_url))
    if len({item.environment for item in references}) != len(references):
        raise ValueError("wave0_oidc_bindings_invalid")
    return tuple(references)


def _machine_report(
    report: ReadinessReport,
    gate_status: GateStatus,
    identity_evidence_sha256: str | None,
    oidc_evidence: tuple[OidcRunEvidence, ...],
) -> ReadinessReport:
    oidc_hashes = {item.environment: item.evidence_sha256 for item in oidc_evidence}
    checks: list[CheckResult] = []
    stored_identity = False
    stored_oidc: set[EnvironmentName] = set()
    for check in report.checks:
        details = dict(check.details)
        if check.name == "aws_account_isolation" and identity_evidence_sha256 is not None:
            details["identity_evidence_sha256"] = identity_evidence_sha256
            stored_identity = True
        for environment, evidence_hash in oidc_hashes.items():
            if check.name == f"{environment.value}_account":
                details["oidc_evidence_sha256"] = evidence_hash
                stored_oidc.add(environment)
        checks.append(check.model_copy(update={"details": details}))

    pending: dict[str, str] = {}
    if identity_evidence_sha256 is not None and not stored_identity:
        pending["identity_evidence_sha256"] = identity_evidence_sha256
    for environment, evidence_hash in oidc_hashes.items():
        if environment not in stored_oidc:
            pending[f"{environment.value}_oidc_evidence_sha256"] = evidence_hash
    if pending:
        anchor = next(
            (
                index
                for index, check in enumerate(checks)
                if check.name in {"wave0_evidence_inventory", "aws_live_checks"}
            ),
            None,
        )
        if anchor is None:
            raise ValueError("wave0_machine_evidence_invalid")
        details = {**checks[anchor].details, **pending}
        checks[anchor] = checks[anchor].model_copy(update={"details": details})

    payload = report.model_dump(mode="python")
    payload["checks"] = tuple(checks)
    payload["gate_status"] = gate_status
    return ReadinessReport.model_validate(payload)


def main(
    argv: list[str] | None = None,
    *,
    runner: CommandRunner = run_command,
) -> int:
    try:
        args = _parser().parse_args(argv)
        config = load_wave0_config(args.config)
        local_checks = check_local_toolchain(runner)
        live_checks = (
            _skipped_live_checks()
            if args.skip_live
            else (
                *check_aws_accounts(config, runner),
                *check_region_services(config, runner),
            )
        )
        report = build_readiness_report(config, (*local_checks, *live_checks))
        loaded_identity = read_identity_decision(args.identity_decision)
        identity_decision, identity_evidence_sha256 = (
            loaded_identity if loaded_identity is not None else (None, None)
        )
        oidc_references = _parse_oidc_references(args.oidc_run)
        finalization = finalize_wave0_gate(
            report,
            identity_decision,
            oidc_references,
            runner,
        )
        report = _machine_report(
            report,
            finalization.gate_status,
            identity_evidence_sha256,
            finalization.oidc_evidence,
        )
        write_readiness_report(report, args.output)
    except (_CliInputError, OSError, ValueError, ValidationError):
        print("wave0_readiness_invalid_input")
        return 3

    print(f"gate_status={report.gate_status.value}")
    print(f"report={args.output}")
    return 0 if report.gate_status is GateStatus.READY else 2


if __name__ == "__main__":
    raise SystemExit(main())
