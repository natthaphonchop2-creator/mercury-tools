from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mercury_tools.aws.commands import CommandResult, run_command
from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.models import CheckResult, CheckState, GateStatus
from mercury_tools.aws.readiness import (
    SERVICE_COMMANDS,
    aggregate_gate,
    build_readiness_report,
    check_aws_accounts,
    check_local_toolchain,
    check_region_services,
    fingerprint_account_id,
    write_readiness_report,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "infra/aws/wave0/environment.yaml"


class FakeRunner:
    def __init__(self, results: Mapping[tuple[str, ...], CommandResult]) -> None:
        self.results = dict(results)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, argv: tuple[str, ...], timeout_seconds: int) -> CommandResult:
        assert timeout_seconds > 0
        self.calls.append(argv)
        return self.results.get(argv, CommandResult(127, "", "command unavailable"))

    @classmethod
    def for_tool_versions(
        cls,
        *,
        aws: str,
        node: str,
        python: str,
        agentcore: str,
        cdk: str,
    ) -> FakeRunner:
        return cls(
            {
                ("aws", "--version"): CommandResult(0, aws, ""),
                ("node", "--version"): CommandResult(0, node, ""),
                ("uv", "run", "python", "--version"): CommandResult(0, python, ""),
                ("npx", "--no-install", "agentcore", "--version"): CommandResult(
                    0, agentcore, ""
                ),
                ("npx", "--no-install", "cdk", "--version"): CommandResult(0, cdk, ""),
            }
        )

    @classmethod
    def with_failed_command(cls, failed: tuple[str, ...]) -> FakeRunner:
        runner = cls.for_tool_versions(
            aws="aws-cli/2.36.14",
            node="v22.22.2",
            python="Python 3.11.15",
            agentcore="0.25.0",
            cdk="2.1134.0",
        )
        runner.results[failed] = CommandResult(127, "", "command unavailable")
        return runner

    @classmethod
    def for_sts_accounts(cls, **profiles: str) -> FakeRunner:
        results = {}
        for profile, account_id in profiles.items():
            command = (
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                profile.replace("_", "-"),
                "--region",
                "ap-southeast-1",
                "--output",
                "json",
                "--no-cli-pager",
            )
            results[command] = CommandResult(0, json.dumps({"Account": account_id}), "")
        return cls(results)

    @classmethod
    def for_services(cls, *, failed: str | None = None) -> FakeRunner:
        config = load_wave0_config(CONFIG_PATH)
        results = {}
        for account in config.accounts:
            for name, suffix in SERVICE_COMMANDS.items():
                command = (
                    "aws",
                    *suffix,
                    "--profile",
                    account.profile,
                    "--region",
                    config.primary_region,
                    "--output",
                    "json",
                    "--no-cli-pager",
                )
                payload = (
                    {"OrderableDBInstanceOptions": [{"Engine": "aurora-postgresql"}]}
                    if name == "aurora_postgresql"
                    else {}
                )
                results[command] = CommandResult(0, json.dumps(payload), "")
                if name == failed:
                    results[command] = CommandResult(254, "", "AccessDeniedException")
        return cls(results)


def test_runner_rejects_shell_and_unknown_programs() -> None:
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("sh", "-c", "env"))
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("curl", "https://example.com"))


def test_runner_uses_bounded_environment_and_redacts_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        captured.update(kwargs)
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout="A" * 5_000 + " secret_access_key=unsafe-value",
            stderr="Bearer unsafe-bearer-value",
        )

    monkeypatch.setenv("UNSAFE_EXTRA", "must-not-be-forwarded")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(("aws", "--version"))

    assert captured["shell"] is False
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert "UNSAFE_EXTRA" not in captured["env"]
    assert len(result.stdout) == 4_096
    assert "unsafe" not in result.stderr


def test_runner_maps_timeout_and_missing_executable_to_stable_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(("aws", "--version"), 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert run_command(("aws", "--version")).returncode == 124

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("raw local path must not escape")

    monkeypatch.setattr(subprocess, "run", missing)
    result = run_command(("aws", "--version"))
    assert result == CommandResult(127, "", "wave0_command_missing")


def test_pinned_local_toolchain_passes() -> None:
    runner = FakeRunner.for_tool_versions(
        aws="aws-cli/2.36.14",
        node="v22.22.2",
        python="Python 3.11.15",
        agentcore="0.25.0",
        cdk="2.1134.0",
    )
    assert all(item.state == "pass" for item in check_local_toolchain(runner))


def test_missing_agentcore_blocks_tooling() -> None:
    runner = FakeRunner.with_failed_command(("npx", "--no-install", "agentcore", "--version"))
    assert aggregate_gate(check_local_toolchain(runner)) == "blocked_tooling"


def test_account_ids_are_fingerprinted() -> None:
    result = fingerprint_account_id("123456789012")
    assert len(result) == 12
    assert result != "123456789012"


def test_same_account_for_two_profiles_is_blocked() -> None:
    config = load_wave0_config(CONFIG_PATH)
    runner = FakeRunner.for_sts_accounts(
        mercury_nonprod="123456789012",
        mercury_prod="123456789012",
    )
    checks = check_aws_accounts(config, runner)
    assert any(item.code == "aws_accounts_not_isolated" for item in checks)
    assert aggregate_gate(checks) == "blocked_identity_compatibility"


def test_account_probe_parses_only_strict_account_shape() -> None:
    config = load_wave0_config(CONFIG_PATH)
    runner = FakeRunner.for_sts_accounts(
        mercury_nonprod="123456789012",
        mercury_prod="210987654321",
    )
    command = next(command for command in runner.results if "mercury-nonprod" in command)
    runner.results[command] = CommandResult(0, '{"Account": 123456789012}', "")

    checks = check_aws_accounts(config, runner)
    assert any(item.code == "aws_account_response_invalid" for item in checks)
    assert aggregate_gate(checks) == "blocked_account_access"


def test_failed_required_service_blocks_region() -> None:
    config = load_wave0_config(CONFIG_PATH)
    checks = check_region_services(
        config,
        FakeRunner.for_services(failed="agentcore_gateway"),
    )
    assert aggregate_gate(checks) == "blocked_region_service"


def test_invalid_json_and_empty_aurora_options_block_region() -> None:
    config = load_wave0_config(CONFIG_PATH)
    runner = FakeRunner.for_services()
    for command in tuple(runner.results):
        if command[1] == "bedrock-agent":
            runner.results[command] = CommandResult(0, "not-json", "")
        if command[1] == "rds":
            runner.results[command] = CommandResult(
                0, json.dumps({"OrderableDBInstanceOptions": []}), ""
            )

    checks = check_region_services(config, runner)
    codes = {item.code for item in checks}
    assert "aws_service_response_invalid" in codes
    assert "aurora_serverless_unavailable" in codes
    assert aggregate_gate(checks) == "blocked_region_service"


def test_throttled_service_retries_three_times_then_blocks() -> None:
    config = load_wave0_config(CONFIG_PATH)
    runner = FakeRunner.for_services()
    command = next(command for command in runner.results if command[1] == "logs")
    runner.results[command] = CommandResult(254, "", "ThrottlingException")

    checks = check_region_services(config, runner)
    assert runner.calls.count(command) == 3
    assert any(item.code == "aws_service_throttled" for item in checks)


def build_report_fixture():
    config = load_wave0_config(CONFIG_PATH)
    local_checks = check_local_toolchain(
        FakeRunner.for_tool_versions(
            aws="aws-cli/2.36.14",
            node="v22.22.2",
            python="Python 3.11.15",
            agentcore="0.25.0",
            cdk="2.1134.0",
        )
    )
    account_checks = check_aws_accounts(
        config,
        FakeRunner.for_sts_accounts(
            mercury_nonprod="123456789012",
            mercury_prod="210987654321",
        ),
    )
    service_checks = check_region_services(config, FakeRunner.for_services())
    return build_readiness_report(
        config,
        (*local_checks, *account_checks, *service_checks),
        checked_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def test_report_contains_no_raw_account_or_secret() -> None:
    report = build_report_fixture()
    payload = report.model_dump_json()
    assert report.schema_version == "mercury.aws.wave0.report.v1"
    assert report.gate_status is GateStatus.READY
    assert "123456789012" not in payload
    assert "AKIA" not in payload
    assert "secret_access_key" not in payload.lower()


def test_report_writer_is_atomic_private_and_bounded(tmp_path: Path) -> None:
    output = tmp_path / ".artifacts/aws/wave0/readiness.json"
    write_readiness_report(build_report_fixture(), output, repository_root=tmp_path)
    assert output.stat().st_mode & 0o777 == 0o600
    assert json.loads(output.read_text(encoding="utf-8"))["gate_status"] == "ready"

    with pytest.raises(ValueError, match="wave0_output_path_invalid"):
        write_readiness_report(
            build_report_fixture(),
            tmp_path / "readiness.json",
            repository_root=tmp_path,
        )


def test_gate_precedence_is_stable() -> None:
    config = load_wave0_config(CONFIG_PATH)
    checks = [
        *check_region_services(config, FakeRunner.for_services(failed="s3")),
        *check_aws_accounts(config, FakeRunner({})),
        *check_local_toolchain(
            FakeRunner.with_failed_command(("npx", "--no-install", "cdk", "--version"))
        ),
    ]
    assert aggregate_gate(checks) is GateStatus.BLOCKED_TOOLING


def test_unknown_blocked_check_fails_closed() -> None:
    check = CheckResult(
        name="identity_contract",
        state=CheckState.BLOCKED,
        code="identity_contract_unknown",
        summary="Identity compatibility could not be established.",
        details={},
    )
    assert aggregate_gate([check]) is GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
