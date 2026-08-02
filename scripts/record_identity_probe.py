#!/usr/bin/env python3
"""Record closed AWS Wave 0 identity probe evidence and make a safe decision."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mercury_tools.aws.identity import (
    HostIdentityProbe,
    HostName,
    IdentityDecision,
    ProbeResult,
    RegistrationMode,
    decide_identity,
    load_identity_host_contract,
    record_host_probe,
)

_DEFAULT_CONTRACT = Path("infra/aws/wave0/identity-host-contract.yaml")
_DEFAULT_PROBE_DIR = Path(".artifacts/aws/wave0/identity")
_DEFAULT_DECISION = Path("infra/aws/wave0/identity-decision.yaml")


class _CliInputError(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise _CliInputError from None


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record")
    record.add_argument("--host", choices=[item.value for item in HostName], required=True)
    record.add_argument(
        "--mode", choices=[item.value for item in RegistrationMode], required=True
    )
    record.add_argument("--result", choices=[item.value for item in ProbeResult], required=True)
    record.add_argument("--issuer-origin", required=True)
    record.add_argument("--evidence-file", type=Path, required=True)
    record.add_argument("--contract", type=Path, default=_DEFAULT_CONTRACT)
    record.add_argument("--output-dir", type=Path, default=_DEFAULT_PROBE_DIR)

    decide = commands.add_parser("decide")
    decide.add_argument("--probe-dir", type=Path, default=_DEFAULT_PROBE_DIR)
    decide.add_argument("--output", type=Path, default=_DEFAULT_DECISION)
    return parser


def _load_probes(probe_dir: Path) -> tuple[HostIdentityProbe, ...]:
    try:
        if probe_dir.is_symlink() or not probe_dir.is_dir():
            raise OSError
        probes: list[HostIdentityProbe] = []
        for path in sorted(probe_dir.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise OSError
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
            probes.append(HostIdentityProbe.model_validate(raw))
        return tuple(probes)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError):
        raise ValueError("identity_probe_directory_invalid") from None


def _write_decision(path: Path, decision: IdentityDecision) -> None:
    try:
        if path.exists() or path.is_symlink():
            raise OSError
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise OSError
        payload = yaml.safe_dump(
            decision.model_dump(mode="json"), sort_keys=False, allow_unicode=False
        )
        with path.open("x", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError:
        raise ValueError("identity_decision_write_failed") from None


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        if args.command == "record":
            contract = load_identity_host_contract(args.contract)
            probe = HostIdentityProbe(
                host=args.host,
                registration_mode=args.mode,
                result=args.result,
                issuer_origin=args.issuer_origin,
                pkce_method="S256",
                checked_at=datetime.now(UTC),
                evidence_sha256="0" * 64,
            )
            output = record_host_probe(contract, probe, args.evidence_file, args.output_dir)
            print(f"identity_probe={output.name}")
            return 0

        decision = decide_identity(_load_probes(args.probe_dir))
        _write_decision(args.output, decision)
        print(f"identity_mode={decision.mode.value}")
        return 0
    except _CliInputError:
        print("identity_input_invalid")
        return 3
    except (OSError, ValueError, ValidationError) as exc:
        code = str(exc)
        print(code if code.startswith("identity_") else "identity_input_invalid")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
