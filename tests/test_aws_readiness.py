from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mercury_tools.aws.commands import CommandResult, run_command
from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.identity import HostName, IdentityDecision, IdentityMode
from mercury_tools.aws.models import CheckResult, CheckState, GateStatus
from mercury_tools.aws.readiness import (
    SERVICE_COMMANDS,
    OidcRunEvidence,
    aggregate_gate,
    build_readiness_report,
    check_aws_accounts,
    check_local_toolchain,
    check_region_services,
    finalize_wave0_gate,
    fingerprint_account_id,
    write_readiness_report,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "infra/aws/wave0/environment.yaml"
SCRIPT_PATH = ROOT / "scripts/check_aws_readiness.py"


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
            stdout=(
                "prefix AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF "
                "secret_access_key=unsafe-value "
                + "A" * 5_000
            ),
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
    assert len(result.stdout) <= 4_096
    assert "AKIA1234567890ABCDEF" not in result.stdout
    assert "AWS_ACCESS_KEY_ID" not in result.stdout
    assert "unsafe-value" not in result.stdout
    assert "unsafe" not in result.stderr


@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"])
def test_runner_redacts_bare_aws_access_key_identifiers(
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> None:
    access_key = f"{prefix}1234567890ABCDEF"

    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout=f"key={access_key}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(("aws", "--version"))

    assert access_key not in result.stdout
    assert "[REDACTED_AWS_ACCESS_KEY_ID]" in result.stdout


@pytest.mark.parametrize("prefix", ["AKIA", "ASIA"])
def test_runner_redacts_bare_aws_key_crossing_output_boundary(
    monkeypatch: pytest.MonkeyPatch,
    prefix: str,
) -> None:
    access_key = f"{prefix}1234567890ABCDEF"

    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        output = "X" * 4_089 + " " + access_key
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(("aws", "--version"))

    assert len(result.stdout) <= 4_096
    for fragment_length in range(4, len(access_key) + 1):
        assert access_key[:fragment_length] not in result.stdout


@pytest.mark.parametrize(
    ("assignment_name", "credential"),
    [
        ("AWS_ACCESS_KEY_ID", "AKIA1234567890ABCDEF"),
        ("secret_access_key", "boundary-secret-value-123456"),
    ],
)
def test_runner_redacts_assignment_value_crossing_output_boundary(
    monkeypatch: pytest.MonkeyPatch,
    assignment_name: str,
    credential: str,
) -> None:
    assignment = f"{assignment_name}={credential}"

    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        value_offset = 4_090
        output = "X" * (value_offset - len(assignment_name) - 2) + " " + assignment
        return subprocess.CompletedProcess(argv, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(("aws", "--version"))

    assert len(result.stdout) <= 4_096
    assert assignment_name not in result.stdout
    for fragment_length in range(4, len(credential) + 1):
        assert credential[:fragment_length] not in result.stdout


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


def build_complete_checks_fixture() -> tuple[CheckResult, ...]:
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
    return (*local_checks, *account_checks, *service_checks)


def build_report_fixture():
    return build_readiness_report(
        load_wave0_config(CONFIG_PATH),
        build_complete_checks_fixture(),
        checked_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def valid_identity() -> IdentityDecision:
    return IdentityDecision(
        mode=IdentityMode.COGNITO_PRE_REGISTERED,
        issuer_kind="cognito",
        issuer_origin="cognito",
        required_hosts=(HostName.CODEX, HostName.CHATGPT, HostName.CLAUDE),
    )


def oidc_run(environment: str, run_id: int) -> OidcRunEvidence:
    run_url = (
        "https://github.com/natthaphonchop2-creator/mercury-tools/"
        f"actions/runs/{run_id}"
    )
    return OidcRunEvidence(
        environment=environment,
        run_url=run_url,
        evidence_sha256=hashlib.sha256(run_url.encode("utf-8")).hexdigest(),
    )


def valid_oidc() -> tuple[OidcRunEvidence, ...]:
    return (oidc_run("nonprod", 1001), oidc_run("production", 1002))


def replace_check(report, name: str, **updates: object):
    checks = tuple(
        item.model_copy(update=updates) if item.name == name else item
        for item in report.checks
    )
    return report.model_copy(update={"checks": checks})


def test_report_contains_no_raw_account_or_secret() -> None:
    report = build_report_fixture()
    payload = report.model_dump_json()
    assert report.schema_version == "mercury.aws.wave0.report.v1"
    assert report.gate_status is GateStatus.READY
    assert "123456789012" not in payload
    assert "AKIA" not in payload
    assert "secret_access_key" not in payload.lower()


@pytest.mark.parametrize(
    ("mutation", "expected_gate"),
    [
        ("missing", GateStatus.BLOCKED_REGION_SERVICE),
        ("duplicate", GateStatus.BLOCKED_TOOLING),
        ("unknown", GateStatus.BLOCKED_IDENTITY_COMPATIBILITY),
    ],
)
def test_report_fails_closed_for_invalid_evidence_inventory(
    mutation: str,
    expected_gate: GateStatus,
) -> None:
    config = load_wave0_config(CONFIG_PATH)
    checks = list(build_complete_checks_fixture())
    if mutation == "missing":
        checks.pop()
    elif mutation == "duplicate":
        checks.append(checks[0])
    else:
        checks.append(
            CheckResult(
                name="unknown_probe",
                state=CheckState.PASS,
                code="unknown_probe_passed",
                summary="Unknown probe passed.",
                details={},
            )
        )

    report = build_readiness_report(config, checks)

    assert report.gate_status is expected_gate
    assert sum(item.code == "wave0_evidence_inventory_invalid" for item in report.checks) == 1


def test_aggregate_gate_rejects_incomplete_passing_evidence() -> None:
    checks = list(build_complete_checks_fixture())
    checks.pop()
    assert aggregate_gate(checks) is not GateStatus.READY


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


@pytest.mark.parametrize("symlink_component", [".artifacts", "aws", "wave0"])
def test_report_writer_rejects_symlinked_artifact_components(
    tmp_path: Path,
    symlink_component: str,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    artifacts = tmp_path / ".artifacts"
    aws = artifacts / "aws"
    wave0 = aws / "wave0"
    component_paths = {".artifacts": artifacts, "aws": aws, "wave0": wave0}
    target = component_paths[symlink_component]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="wave0_output_path_invalid"):
        write_readiness_report(
            build_report_fixture(),
            wave0 / "readiness.json",
            repository_root=tmp_path,
        )

    assert list(outside.iterdir()) == []


def test_report_writer_rejects_symlinked_output_file(tmp_path: Path) -> None:
    target = tmp_path / "outside.json"
    target.write_text("unchanged", encoding="utf-8")
    output = tmp_path / ".artifacts/aws/wave0/readiness.json"
    output.parent.mkdir(parents=True)
    output.symlink_to(target)

    with pytest.raises(ValueError, match="wave0_output_path_invalid"):
        write_readiness_report(build_report_fixture(), output, repository_root=tmp_path)

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_report_writer_cleans_temporary_file_when_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / ".artifacts/aws/wave0/readiness.json"

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("replacement failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        write_readiness_report(build_report_fixture(), output, repository_root=tmp_path)

    assert list(output.parent.glob(".readiness-*")) == []
    assert not output.exists()


def test_report_writer_cleanup_failure_does_not_mask_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / ".artifacts/aws/wave0/readiness.json"

    class ReplacementFailure(OSError):
        pass

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise ReplacementFailure("replacement failed")

    def fail_cleanup(*args: object, **kwargs: object) -> None:
        raise PermissionError("cleanup failed")

    with monkeypatch.context() as context:
        context.setattr(os, "replace", fail_replace)
        context.setattr(os, "unlink", fail_cleanup)
        with pytest.raises(ReplacementFailure, match="replacement failed"):
            write_readiness_report(build_report_fixture(), output, repository_root=tmp_path)

    leftovers = list(output.parent.glob(".readiness-*"))
    assert len(leftovers) == 1
    assert leftovers[0].stat().st_mode & 0o777 == 0o600
    leftovers[0].unlink()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--unknown=AKIA1234567890ABCDEF",),
        ("--output",),
    ],
)
def test_cli_rejects_invalid_arguments_without_echoing_them(arguments: tuple[str, ...]) -> None:
    result = subprocess.run(
        (sys.executable, str(SCRIPT_PATH), *arguments),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    assert result.stdout == "wave0_readiness_invalid_input\n"
    assert result.stderr == ""
    assert "AKIA1234567890ABCDEF" not in f"{result.stdout}{result.stderr}"


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


def test_oidc_run_evidence_is_closed_frozen_and_bound_to_exact_run_url() -> None:
    evidence = oidc_run("nonprod", 1001)
    assert evidence.environment == "nonprod"

    with pytest.raises(ValidationError):
        evidence.environment = "production"
    with pytest.raises(ValidationError):
        OidcRunEvidence(
            environment="nonprod",
            run_url=(
                "https://github.com/natthaphonchop2-creator/mercury-tools/"
                "actions/runs/1001"
            ),
            evidence_sha256="a" * 64,
            run_attempt=1,
        )
    with pytest.raises(ValidationError, match="wave0_oidc_run_url_invalid"):
        OidcRunEvidence(
            environment="nonprod",
            run_url="https://example.com/actions/runs/1001",
            evidence_sha256="a" * 64,
        )
    with pytest.raises(ValidationError, match="wave0_oidc_evidence_hash_invalid"):
        OidcRunEvidence(
            environment="nonprod",
            run_url=(
                "https://github.com/natthaphonchop2-creator/mercury-tools/"
                "actions/runs/1001"
            ),
            evidence_sha256="a" * 64,
        )


def test_gate_requires_distinct_ready_accounts() -> None:
    report = build_report_fixture()
    report = replace_check(
        report,
        "production_account",
        details={"account_fingerprint": "a" * 12},
    )
    report = replace_check(
        report,
        "nonprod_account",
        details={"account_fingerprint": "a" * 12},
    )

    assert finalize_wave0_gate(report, valid_identity(), valid_oidc()) == (
        "blocked_account_access"
    )


def test_gate_requires_every_service_and_quota_probe() -> None:
    report = build_report_fixture()
    report = report.model_copy(
        update={
            "checks": tuple(
                item for item in report.checks if item.name != "production_agentcore_quotas"
            )
        },
    )

    assert finalize_wave0_gate(report, valid_identity(), valid_oidc()) == (
        "blocked_region_service"
    )


def test_gate_requires_identity_and_both_oidc_jobs() -> None:
    report = build_report_fixture()
    assert finalize_wave0_gate(report, None, valid_oidc()) == (
        "blocked_identity_compatibility"
    )
    assert finalize_wave0_gate(report, valid_identity(), valid_oidc()[:1]) == (
        "blocked_account_access"
    )


def test_gate_rejects_duplicate_oidc_environment_url_or_hash() -> None:
    report = build_report_fixture()
    first = oidc_run("nonprod", 1001)
    same_environment = (first, oidc_run("nonprod", 1002))
    duplicate_url = (first, oidc_run("production", 1001))

    assert finalize_wave0_gate(report, valid_identity(), same_environment) == (
        "blocked_account_access"
    )
    assert finalize_wave0_gate(report, valid_identity(), duplicate_url) == (
        "blocked_account_access"
    )


def test_gate_revalidates_tool_versions_codes_and_report_gate() -> None:
    report = build_report_fixture()
    wrong_version = replace_check(
        report,
        "agentcore_cli",
        details={"version": "0.26.0"},
    )
    wrong_code = replace_check(report, "aws_cdk", code="tool_version_assumed")
    forged_gate = report.model_copy(update={"gate_status": GateStatus.BLOCKED_TOOLING})

    assert finalize_wave0_gate(wrong_version, valid_identity(), valid_oidc()) == (
        "blocked_tooling"
    )
    assert finalize_wave0_gate(wrong_code, valid_identity(), valid_oidc()) == (
        "blocked_tooling"
    )
    assert finalize_wave0_gate(forged_gate, valid_identity(), valid_oidc()) == (
        "blocked_tooling"
    )


def test_gate_revalidates_region_aliases_account_codes_and_service_codes() -> None:
    report = build_report_fixture()
    wrong_alias = report.accounts[1].model_copy(update={"alias": "mercury-nonprod"})
    wrong_accounts = report.model_copy(
        update={"accounts": (report.accounts[0], wrong_alias)}
    )
    wrong_account_code = replace_check(
        report, "nonprod_account", code="aws_account_assumed"
    )
    wrong_service_code = replace_check(
        report, "nonprod_s3", code="aws_service_assumed"
    )
    wrong_region = report.model_copy(update={"primary_region": "us-east-1"})

    assert finalize_wave0_gate(wrong_accounts, valid_identity(), valid_oidc()) == (
        "blocked_account_access"
    )
    assert finalize_wave0_gate(wrong_account_code, valid_identity(), valid_oidc()) == (
        "blocked_account_access"
    )
    assert finalize_wave0_gate(wrong_service_code, valid_identity(), valid_oidc()) == (
        "blocked_region_service"
    )
    assert finalize_wave0_gate(wrong_region, valid_identity(), valid_oidc()) == (
        "blocked_region_service"
    )


def test_gate_revalidates_credential_safe_report_fields() -> None:
    unsafe_report = replace_check(
        build_report_fixture(),
        "nonprod_s3",
        summary="Bearer unsafe-value",
    )

    assert finalize_wave0_gate(unsafe_report, valid_identity(), valid_oidc()) == (
        "blocked_identity_compatibility"
    )


def test_gate_is_ready_only_with_complete_proof() -> None:
    assert finalize_wave0_gate(
        build_report_fixture(), valid_identity(), valid_oidc()
    ) == "ready"


def test_cli_hashes_identity_and_oidc_evidence_without_storing_urls(
    tmp_path: Path,
) -> None:
    decision_path = tmp_path / "identity-decision.yaml"
    decision_path.write_text(
        json.dumps(valid_identity().model_dump(mode="json")), encoding="utf-8"
    )
    urls = tuple(str(item.run_url) for item in valid_oidc())
    output = ROOT / ".artifacts/aws/wave0/task5-cli-test.json"
    output.unlink(missing_ok=True)

    try:
        result = subprocess.run(
            (
                sys.executable,
                str(SCRIPT_PATH),
                "--skip-live",
                "--identity-decision",
                str(decision_path),
                "--oidc-run-url",
                urls[0],
                "--oidc-run-url",
                urls[1],
                "--output",
                str(output),
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 2
        serialized = output.read_text(encoding="utf-8")
        assert "gate_status=blocked_account_access" in result.stdout
        assert hashlib.sha256(decision_path.read_bytes()).hexdigest() in serialized
        for url in urls:
            assert url not in serialized
            assert hashlib.sha256(url.encode("utf-8")).hexdigest() in serialized
    finally:
        output.unlink(missing_ok=True)
