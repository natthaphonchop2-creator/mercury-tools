#!/usr/bin/env python3
"""Run local and live AWS Wave 0 readiness checks."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.identity import IdentityDecision
from mercury_tools.aws.models import (
    CheckResult,
    CheckState,
    EnvironmentName,
    GateStatus,
    ReadinessReport,
)
from mercury_tools.aws.readiness import (
    OidcRunEvidence,
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
    parser.add_argument("--oidc-run-url", action="append", default=[])
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


def _load_identity_decision(path: Path) -> tuple[IdentityDecision | None, str | None]:
    if not path.exists():
        return None, None
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 65_536:
            raise OSError
        payload = path.read_bytes()
        raw: Any = yaml.safe_load(payload.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("wave0_identity_decision_invalid")
        decision = IdentityDecision.model_validate(raw)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError):
        raise ValueError("wave0_identity_decision_invalid") from None
    return decision, hashlib.sha256(payload).hexdigest()


def _build_oidc_evidence(run_urls: list[str]) -> tuple[OidcRunEvidence, ...]:
    if len(run_urls) > len(EnvironmentName):
        raise ValueError("wave0_oidc_evidence_invalid")
    environments = (EnvironmentName.NONPROD, EnvironmentName.PRODUCTION)
    return tuple(
        OidcRunEvidence(
            environment=environments[index],
            run_url=run_url,
            evidence_sha256=hashlib.sha256(run_url.encode("utf-8")).hexdigest(),
        )
        for index, run_url in enumerate(run_urls)
    )


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


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        config = load_wave0_config(args.config)
        local_checks = check_local_toolchain()
        live_checks = (
            _skipped_live_checks()
            if args.skip_live
            else (*check_aws_accounts(config), *check_region_services(config))
        )
        report = build_readiness_report(config, (*local_checks, *live_checks))
        identity_decision, identity_evidence_sha256 = _load_identity_decision(
            args.identity_decision
        )
        oidc_evidence = _build_oidc_evidence(args.oidc_run_url)
        gate_status = finalize_wave0_gate(report, identity_decision, oidc_evidence)
        report = _machine_report(
            report,
            gate_status,
            identity_evidence_sha256,
            oidc_evidence,
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
