from __future__ import annotations

import base64
import hashlib
import json
import os
import runpy
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mercury_tools.aws import readiness as aws_readiness
from mercury_tools.aws.commands import CommandResult, run_command
from mercury_tools.aws.config import load_wave0_config
from mercury_tools.aws.identity import (
    HostIdentityProbe,
    HostName,
    IdentityDecision,
    IdentityHostContract,
    IdentityMode,
    IdentityProofReference,
    ProbeResult,
    record_host_probe,
)
from mercury_tools.aws.models import CheckResult, CheckState, EnvironmentName, GateStatus
from mercury_tools.aws.readiness import (
    SERVICE_COMMANDS,
    TOOL_COMMANDS,
    OidcRunEvidence,
    OidcRunReference,
    aggregate_gate,
    build_readiness_report,
    check_aws_accounts,
    check_local_toolchain,
    check_region_services,
    finalize_wave0_gate,
    fingerprint_account_id,
    verify_oidc_runs,
    write_readiness_report,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "infra/aws/wave0/environment.yaml"
SCRIPT_PATH = ROOT / "scripts/check_aws_readiness.py"
WORKFLOW_PATH = ROOT / ".github/workflows/aws-wave0-oidc-smoke.yml"
REPOSITORY = "natthaphonchop2-creator/mercury-tools"
WORKFLOW_FILE = ".github/workflows/aws-wave0-oidc-smoke.yml"
COGNITO_STACK_COMMAND = (
    "aws",
    "cloudformation",
    "describe-stacks",
    "--stack-name",
    "mercury-wave0-identity-spike",
    "--profile",
    "mercury-nonprod",
    "--region",
    "ap-southeast-1",
    "--output",
    "json",
    "--no-cli-pager",
)
readiness_main = runpy.run_path(str(SCRIPT_PATH))["main"]


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


class VerifiedGhRunner(FakeRunner):
    @classmethod
    def for_references(
        cls,
        references: tuple[OidcRunReference, ...],
        *,
        run_updates: Mapping[str, object] | None = None,
        job_updates: Mapping[str, object] | None = None,
        workflow_updates: Mapping[str, object] | None = None,
        source: str | None = None,
        role_account_ids: Mapping[EnvironmentName, str] | None = None,
        proof_account_ids: Mapping[EnvironmentName, str] | None = None,
        proof_updates: Mapping[str, object] | None = None,
        artifact_mutation: str | None = None,
        download_mutation: str | None = None,
    ) -> VerifiedGhRunner:
        results: dict[tuple[str, ...], CommandResult] = {}
        current_role_accounts = role_account_ids or {
            EnvironmentName.NONPROD: "123456789012",
            EnvironmentName.PRODUCTION: "210987654321",
        }
        proved_accounts = proof_account_ids or {
            EnvironmentName.NONPROD: "123456789012",
            EnvironmentName.PRODUCTION: "210987654321",
        }
        downloads: dict[tuple[int, EnvironmentName], dict[str, bytes | tuple[str, str]]] = {}
        for reference in references:
            run_id = int(str(reference.run_url).rstrip("/").rsplit("/", 1)[1])
            head_sha = f"{run_id:040x}"[-40:]
            workflow_id = run_id + 10_000
            run_attempt = 1
            run_payload: dict[str, object] = {
                "id": run_id,
                "html_url": str(reference.run_url),
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "head_sha": head_sha,
                "run_attempt": run_attempt,
                "workflow_id": workflow_id,
                "path": WORKFLOW_FILE,
                "repository_full_name": REPOSITORY,
            }
            run_payload.update(run_updates or {})
            workflow_payload: dict[str, object] = {
                "id": workflow_id,
                "path": WORKFLOW_FILE,
            }
            workflow_payload.update(workflow_updates or {})
            jobs_payload: dict[str, object] = {
                "total_count": 2,
                "jobs": [
                    {
                        "id": run_id * 10 + 1,
                        "name": "OIDC smoke (nonprod)",
                        "status": "completed",
                        "conclusion": "success",
                        "run_id": run_id,
                        "run_attempt": run_attempt,
                        "head_sha": head_sha,
                    },
                    {
                        "id": run_id * 10 + 2,
                        "name": "OIDC smoke (production)",
                        "status": "completed",
                        "conclusion": "success",
                        "run_id": run_id,
                        "run_attempt": run_attempt,
                        "head_sha": head_sha,
                    },
                ],
            }
            jobs_payload.update(job_updates or {})
            artifacts = [
                {
                    "id": run_id * 10 + index,
                    "name": f"mercury-wave0-oidc-account-proof-{environment.value}",
                    "size_in_bytes": 512,
                    "expired": False,
                    "expires_at": "2099-01-01T00:00:00Z",
                    "workflow_run": {"id": run_id, "head_sha": head_sha},
                }
                for index, environment in enumerate(EnvironmentName, start=1)
            ]
            selected_artifact = next(
                artifact
                for artifact in artifacts
                if artifact["name"].endswith(reference.environment.value)
            )
            if artifact_mutation == "missing":
                artifacts.remove(selected_artifact)
            elif artifact_mutation == "duplicate":
                artifacts.append({**selected_artifact, "id": run_id * 10 + 99})
            elif artifact_mutation == "expired":
                selected_artifact["expired"] = True
            elif artifact_mutation == "past_expiry":
                selected_artifact["expires_at"] = "2000-01-01T00:00:00Z"
            elif artifact_mutation == "oversized":
                selected_artifact["size_in_bytes"] = 65_537
            elif artifact_mutation == "wrong_run":
                selected_artifact["workflow_run"] = {
                    "id": run_id + 1,
                    "head_sha": head_sha,
                }

            proof: dict[str, object] = {
                "schema": "mercury.aws.wave0.oidc_account_proof.v1",
                "repository": REPOSITORY,
                "workflow": WORKFLOW_FILE,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "head_sha": head_sha,
                "environment": reference.environment.value,
                "account_fingerprint": fingerprint_account_id(
                    proved_accounts[reference.environment]
                ),
            }
            proof.update(proof_updates or {})
            proof_name = f"oidc-account-proof-{reference.environment.value}.json"
            download_files: dict[str, bytes | tuple[str, str]] = {
                proof_name: json.dumps(proof).encode("utf-8")
            }
            if download_mutation == "missing":
                download_files = {}
            elif download_mutation == "additional":
                download_files["unexpected.json"] = b"{}"
            elif download_mutation == "oversized":
                download_files[proof_name] = b"{" + (b" " * 8_192) + b"}"
            elif download_mutation == "symlink":
                download_files[proof_name] = ("symlink", "/dev/null")
            downloads[(run_id, reference.environment)] = download_files

            endpoints = {
                f"repos/{REPOSITORY}/actions/runs/{run_id}": json.dumps(run_payload),
                f"repos/{REPOSITORY}/actions/workflows/{workflow_id}": json.dumps(
                    workflow_payload
                ),
                f"repos/{REPOSITORY}/actions/runs/{run_id}/jobs?per_page=100": (
                    json.dumps(jobs_payload)
                ),
                f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100": (
                    json.dumps({"total_count": len(artifacts), "artifacts": artifacts})
                ),
                f"repos/{REPOSITORY}/contents/{WORKFLOW_FILE}?ref={head_sha}": (
                    base64.b64encode(
                        (
                            source
                            if source is not None
                            else WORKFLOW_PATH.read_text(encoding="utf-8")
                        ).encode("utf-8")
                    ).decode("ascii")
                ),
            }
            for endpoint, stdout in endpoints.items():
                results[("gh", "api", endpoint)] = CommandResult(0, stdout, "")
            environment_endpoint = (
                f"repos/{REPOSITORY}/environments/{reference.environment.value}/"
                "variables/AWS_WAVE0_ROLE_ARN"
            )
            results[("gh", "api", environment_endpoint)] = CommandResult(
                0,
                json.dumps(
                    {
                        "name": "AWS_WAVE0_ROLE_ARN",
                        "value": (
                            "arn:aws:iam::"
                            f"{current_role_accounts[reference.environment]}:"
                            "role/mercury-wave0-readiness"
                        ),
                    }
                ),
                "",
            )
        results[COGNITO_STACK_COMMAND] = CommandResult(
            255,
            "",
            "Stack with id mercury-wave0-identity-spike does not exist",
        )
        runner = cls(results)
        runner.downloads = downloads
        return runner

    def __call__(self, argv: tuple[str, ...], timeout_seconds: int) -> CommandResult:
        self.calls.append(argv)
        assert timeout_seconds > 0
        assert argv[:2] == ("gh", "api") or argv[:3] == (
            "gh",
            "run",
            "download",
        ) or argv[0] in {
            "aws",
            "node",
            "uv",
            "npx",
        }
        if argv[:2] == ("gh", "api"):
            base_command = argv[:3]
            return self.results.get(base_command, CommandResult(127, "", "not found"))
        if argv[:3] == ("gh", "run", "download"):
            run_id = int(argv[3])
            environment = EnvironmentName(argv[7].rsplit("-", 1)[1])
            destination = Path(argv[9])
            for name, content in self.downloads[(run_id, environment)].items():
                target = destination / name
                if isinstance(content, tuple):
                    os.symlink(content[1], target)
                else:
                    target.write_bytes(content)
            return CommandResult(0, "", "")
        return self.results.get(argv, CommandResult(127, "", "command unavailable"))


def test_runner_rejects_shell_and_unknown_programs() -> None:
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("sh", "-c", "env"))
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(("curl", "https://example.com"))


def test_runner_allows_only_closed_wave0_gh_api_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    for command in (
        ("gh", "auth", "token"),
        ("gh", "api", "user"),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/runs/1001",
            "--jq",
            ".",
        ),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/environments/nonprod/variables/AWS_WAVE0_ROLE_ARN",
            "--jq",
            "{name,value}",
        ),
    ):
        with pytest.raises(ValueError, match="wave0_command_not_allowed"):
            run_command(command)

    allowed_commands = (
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/runs/1001",
            "--jq",
                (
                    "{id,html_url,event,status,conclusion,head_sha,run_attempt,workflow_id,path,"
                    "repository_full_name:.repository.full_name}"
                ),
        ),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/workflows/11001",
            "--jq",
            "{id,path}",
        ),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/runs/1001/jobs?per_page=100",
            "--jq",
            (
                "{total_count,jobs:[.jobs[]|"
                "{id,name,status,conclusion,run_id,run_attempt,head_sha}]}"
            ),
        ),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/contents/{WORKFLOW_FILE}?ref={'1' * 40}",
            "--jq",
            ".content",
        ),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/actions/runs/1001/artifacts?per_page=100",
            "--jq",
            (
                "{total_count,artifacts:[.artifacts[]|"
                "{id,name,size_in_bytes,expired,expires_at,"
                "workflow_run:{id:.workflow_run.id,head_sha:.workflow_run.head_sha}}]}"
            ),
        ),
    )
    assert all(run_command(command).returncode == 0 for command in allowed_commands)


@pytest.mark.parametrize(
    "command",
    (
        ("aws", "s3", "rm", "s3://unsafe", "--recursive"),
        (
            "aws",
            "cloudformation",
            "delete-stack",
            "--stack-name",
            "mercury-wave0-identity-spike",
        ),
        ("npm", "install", "unsafe-package"),
        ("npx", "--yes", "cdk", "deploy"),
        (
            "gh",
            "api",
            f"repos/{REPOSITORY}/environments/nonprod/variables/AWS_WAVE0_ROLE_ARN",
            "--method",
            "PATCH",
        ),
    ),
)
def test_runner_rejects_mutating_aws_npm_npx_and_gh_forms(
    command: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("rejected command reached subprocess")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(command)


@pytest.mark.parametrize(
    "command",
    (
        (
            "gh",
            "run",
            "download",
            "1001",
            "--repo",
            REPOSITORY,
            "--name",
            "wrong-artifact",
            "--dir",
            "/tmp/.artifacts/aws/wave0/oidc-download-1001-0123456789abcdef/nonprod",
        ),
        (
            "gh",
            "run",
            "download",
            "1002",
            "--repo",
            REPOSITORY,
            "--name",
            "mercury-wave0-oidc-account-proof-nonprod",
            "--dir",
            "/tmp/.artifacts/aws/wave0/oidc-download-1001-0123456789abcdef/nonprod",
        ),
        (
            "gh",
            "run",
            "download",
            "1001",
            "--repo",
            REPOSITORY,
            "--name",
            "mercury-wave0-oidc-account-proof-nonprod",
            "--dir",
            "/tmp/arbitrary",
        ),
    ),
)
def test_runner_rejects_arbitrary_gh_download_names_runs_and_destinations(
    command: tuple[str, ...],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("rejected command reached subprocess")

    monkeypatch.setattr(subprocess, "run", must_not_run)
    with pytest.raises(ValueError, match="wave0_command_not_allowed"):
        run_command(command)


def test_runner_allows_exact_local_and_aws_read_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    config = load_wave0_config(CONFIG_PATH)
    commands = list(TOOL_COMMANDS.values())
    commands.append(COGNITO_STACK_COMMAND)
    for account in config.accounts:
        commands.append(
            (
                "aws",
                "sts",
                "get-caller-identity",
                "--profile",
                account.profile,
                "--region",
                config.primary_region,
                "--output",
                "json",
                "--no-cli-pager",
            )
        )
        commands.extend(
            (
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
            for suffix in SERVICE_COMMANDS.values()
        )

    assert all(run_command(command).returncode == 0 for command in commands)


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


def valid_identity_proof(
    tmp_path: Path,
    *,
    checked_at: datetime = datetime(2026, 8, 1, tzinfo=UTC),
) -> tuple[IdentityProofReference, ...]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    contract = IdentityHostContract(
        schema_version="mercury.aws.wave0.identity_host_contract.v1",
        required_hosts=(HostName.CODEX, HostName.CHATGPT, HostName.CLAUDE),
        authorization_flow="authorization_code",
        pkce_method="S256",
        refresh_token_rotation="required",
        audience_resource_binding="required",
    )
    references: list[IdentityProofReference] = []
    for host in contract.required_hosts:
        evidence_path = tmp_path / f"{host.value}-raw.json"
        evidence_path.write_text(
            json.dumps({"host": host.value, "authorized": True}),
            encoding="utf-8",
        )
        probe = HostIdentityProbe(
            host=host,
            registration_mode="pre_registered",
            result=ProbeResult.PASS,
            issuer_origin="cognito",
            pkce_method="S256",
            checked_at=checked_at,
            evidence_sha256="0" * 64,
        )
        probe_path = record_host_probe(
            contract,
            probe,
            evidence_path,
            tmp_path / "identity",
        )
        references.append(
            IdentityProofReference(
                host=host,
                probe_path=probe_path,
                evidence_path=evidence_path,
            )
        )
    return tuple(references)


def oidc_reference(environment: str, run_id: int) -> OidcRunReference:
    run_url = (
        "https://github.com/natthaphonchop2-creator/mercury-tools/"
        f"actions/runs/{run_id}"
    )
    return OidcRunReference(
        environment=environment,
        run_url=run_url,
    )


def valid_oidc_references() -> tuple[OidcRunReference, ...]:
    return (
        oidc_reference("nonprod", 1001),
        oidc_reference("production", 1002),
    )


def expected_account_fingerprints(
    references: tuple[OidcRunReference, ...],
) -> dict[EnvironmentName, str]:
    account_ids = {
        EnvironmentName.NONPROD: "123456789012",
        EnvironmentName.PRODUCTION: "210987654321",
    }
    return {
        reference.environment: fingerprint_account_id(account_ids[reference.environment])
        for reference in references
    }


def valid_oidc() -> tuple[OidcRunEvidence, ...]:
    references = valid_oidc_references()
    return verify_oidc_runs(
        references,
        expected_account_fingerprints(references),
        VerifiedGhRunner.for_references(references),
    )


def fabricated_oidc() -> tuple[OidcRunEvidence, ...]:
    evidence: list[OidcRunEvidence] = []
    workflow_sha256 = hashlib.sha256(WORKFLOW_PATH.read_bytes()).hexdigest()
    for environment, run_id in (("nonprod", 1001), ("production", 1002)):
        run_url = str(oidc_reference(environment, run_id).run_url)
        head_sha = f"{run_id:040x}"
        workflow_id = run_id + 10_000
        job_id = run_id * 10 + (1 if environment == "nonprod" else 2)
        run_attempt = 1
        account_fingerprint = fingerprint_account_id(
            "123456789012" if environment == "nonprod" else "210987654321"
        )
        account_proof_sha256 = hashlib.sha256(b"fabricated-proof").hexdigest()
        canonical = json.dumps(
            {
                "account_fingerprint": account_fingerprint,
                "account_proof_sha256": account_proof_sha256,
                "environment": environment,
                "head_sha": head_sha,
                "job_id": job_id,
                "repository": REPOSITORY,
                "run_id": run_id,
                "run_attempt": run_attempt,
                "run_url_sha256": hashlib.sha256(run_url.encode("utf-8")).hexdigest(),
                "schema_version": "mercury.aws.wave0.oidc_run_evidence.v4",
                "workflow_id": workflow_id,
                "workflow_path": WORKFLOW_FILE,
                "workflow_sha256": workflow_sha256,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        evidence.append(
            OidcRunEvidence(
                environment=environment,
                run_url=run_url,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                workflow_id=workflow_id,
                workflow_sha256=workflow_sha256,
                job_id=job_id,
                account_fingerprint=account_fingerprint,
                account_proof_sha256=account_proof_sha256,
                evidence_sha256=hashlib.sha256(canonical).hexdigest(),
            )
        )
    return tuple(evidence)


def cli_runner(
    references: tuple[OidcRunReference, ...],
    **gh_options: object,
) -> VerifiedGhRunner:
    runner = VerifiedGhRunner.for_references(references, **gh_options)
    tools = FakeRunner.for_tool_versions(
        aws="aws-cli/2.36.14",
        node="v22.22.2",
        python="Python 3.11.15",
        agentcore="0.25.0",
        cdk="2.1134.0",
    )
    runner.results.update(tools.results)
    runner.results.update(
        FakeRunner.for_sts_accounts(
            mercury_nonprod="123456789012",
            mercury_prod="210987654321",
        ).results
    )
    runner.results.update(FakeRunner.for_services().results)
    return runner


def finalize_status(
    report,
    identity_decision: IdentityDecision | None,
    references: tuple[OidcRunReference, ...] | None = None,
) -> GateStatus:
    checked_references = (
        valid_oidc_references() if references is None else references
    )
    runner = VerifiedGhRunner.for_references(checked_references)
    with tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve()) as directory:
        identity_proof = valid_identity_proof(Path(directory))
        return finalize_wave0_gate(
            report,
            identity_decision,
            checked_references,
            runner,
            identity_proof_references=identity_proof,
        ).gate_status


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
        (
            "--oidc-run-url",
            "https://github.com/natthaphonchop2-creator/mercury-tools/actions/runs/1",
        ),
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


def test_oidc_models_are_closed_frozen_and_canonically_bound() -> None:
    reference = oidc_reference("nonprod", 1001)
    evidence = valid_oidc()[0]
    assert evidence.environment == "nonprod"
    assert evidence.run_id == 1001
    assert evidence.run_attempt == 1
    assert len(evidence.account_proof_sha256) == 64
    assert evidence.evidence_sha256 != hashlib.sha256(
        str(evidence.run_url).encode("utf-8")
    ).hexdigest()

    with pytest.raises(ValidationError):
        evidence.environment = "production"
    with pytest.raises(ValidationError):
        OidcRunEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "raw_account_id": "123456789012"}
        )
    with pytest.raises(ValidationError, match="wave0_oidc_run_url_invalid"):
        OidcRunReference(
            environment="nonprod",
            run_url="https://example.com/actions/runs/1001",
        )
    with pytest.raises(ValidationError, match="wave0_oidc_evidence_hash_invalid"):
        OidcRunEvidence.model_validate(
            {**evidence.model_dump(mode="python"), "evidence_sha256": "a" * 64}
        )
    assert reference.run_url == evidence.run_url


def test_oidc_verifier_uses_closed_gh_calls_and_returns_no_raw_payload(
    tmp_path: Path,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references((reference,))

    evidence = verify_oidc_runs(
        (reference,),
        expected_account_fingerprints((reference,)),
        runner,
        repository_root=tmp_path,
    )

    assert len(evidence) == 1
    assert evidence[0].environment == "nonprod"
    assert evidence[0].head_sha == f"{1001:040x}"
    assert evidence[0].workflow_sha256 == hashlib.sha256(
        WORKFLOW_PATH.read_bytes()
    ).hexdigest()
    assert len(runner.calls) == 6
    assert sum(call[:2] == ("gh", "api") for call in runner.calls) == 5
    assert sum(call[:3] == ("gh", "run", "download") for call in runner.calls) == 1
    assert all("--log" not in call for call in runner.calls)
    assert "workflow_dispatch" not in evidence[0].model_dump_json()
    assert str(tmp_path) not in evidence[0].model_dump_json()
    assert not any((tmp_path / ".artifacts/aws/wave0").iterdir())


def test_oidc_verifier_binds_each_run_artifact_to_readiness_account() -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(references)
    expected = {
        EnvironmentName.NONPROD: fingerprint_account_id("123456789012"),
        EnvironmentName.PRODUCTION: fingerprint_account_id("210987654321"),
    }

    evidence = verify_oidc_runs(references, expected, runner)

    assert tuple(item.account_fingerprint for item in evidence) == (
        expected[EnvironmentName.NONPROD],
        expected[EnvironmentName.PRODUCTION],
    )
    serialized = json.dumps([item.model_dump(mode="json") for item in evidence])
    assert "123456789012" not in serialized
    assert "210987654321" not in serialized
    assert "arn:aws:iam" not in serialized
    assert len(runner.calls) == 12


@pytest.mark.parametrize(
    "proof_account_ids",
    (
        {
            EnvironmentName.NONPROD: "123456789012",
            EnvironmentName.PRODUCTION: "123456789012",
        },
        {
            EnvironmentName.NONPROD: "999999999999",
            EnvironmentName.PRODUCTION: "210987654321",
        },
    ),
)
def test_oidc_verifier_rejects_same_or_wrong_account_artifact_proofs(
    proof_account_ids: Mapping[EnvironmentName, str],
) -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(
        references,
        proof_account_ids=proof_account_ids,
    )
    expected = {
        EnvironmentName.NONPROD: fingerprint_account_id("123456789012"),
        EnvironmentName.PRODUCTION: fingerprint_account_id("210987654321"),
    }

    with pytest.raises(ValueError, match="wave0_oidc_account_unverified"):
        verify_oidc_runs(references, expected, runner)


def test_historical_artifact_proof_ignores_changed_current_role_arn() -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,),
        role_account_ids={
            EnvironmentName.NONPROD: "999999999999",
            EnvironmentName.PRODUCTION: "210987654321",
        },
    )

    evidence = verify_oidc_runs(
        (reference,), expected_account_fingerprints((reference,)), runner
    )

    assert evidence[0].account_fingerprint == fingerprint_account_id("123456789012")
    assert not any("/environments/" in " ".join(call) for call in runner.calls)


def test_forged_current_role_arn_cannot_repair_wrong_historical_artifact() -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,),
        role_account_ids={
            EnvironmentName.NONPROD: "123456789012",
            EnvironmentName.PRODUCTION: "210987654321",
        },
        proof_account_ids={
            EnvironmentName.NONPROD: "999999999999",
            EnvironmentName.PRODUCTION: "210987654321",
        },
    )

    with pytest.raises(ValueError, match="wave0_oidc_account_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), runner
        )
    assert not any("/environments/" in " ".join(call) for call in runner.calls)


@pytest.mark.parametrize(
    "artifact_mutation",
    ("missing", "duplicate", "expired", "past_expiry", "oversized", "wrong_run"),
)
def test_oidc_verifier_rejects_invalid_run_artifact_metadata(
    artifact_mutation: str,
    tmp_path: Path,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,), artifact_mutation=artifact_mutation
    )

    with pytest.raises(ValueError, match="wave0_oidc_artifact_unverified"):
        verify_oidc_runs(
            (reference,),
            expected_account_fingerprints((reference,)),
            runner,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "download_mutation",
    ("missing", "additional", "oversized", "symlink"),
)
def test_oidc_verifier_rejects_unsafe_downloaded_artifact_files(
    download_mutation: str,
    tmp_path: Path,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,), download_mutation=download_mutation
    )

    with pytest.raises(ValueError, match="wave0_oidc_artifact_unverified"):
        verify_oidc_runs(
            (reference,),
            expected_account_fingerprints((reference,)),
            runner,
            repository_root=tmp_path,
        )


@pytest.mark.parametrize(
    "proof_updates",
    (
        {"schema": "wrong"},
        {"repository": "attacker/fork"},
        {"workflow": ".github/workflows/other.yml"},
        {"run_id": 1002},
        {"run_attempt": 2},
        {"head_sha": "f" * 40},
        {"environment": "production"},
        {"unexpected": "field"},
    ),
)
def test_oidc_verifier_rejects_non_closed_or_mismatched_account_proof(
    proof_updates: Mapping[str, object],
    tmp_path: Path,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,), proof_updates=proof_updates
    )

    with pytest.raises(ValueError, match="wave0_oidc_artifact_unverified"):
        verify_oidc_runs(
            (reference,),
            expected_account_fingerprints((reference,)),
            runner,
            repository_root=tmp_path,
        )


def test_oidc_verifier_works_through_shell_free_command_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    backend = VerifiedGhRunner.for_references((reference,))

    def fake_run(
        argv: tuple[str, ...], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        result = backend(argv, 20)
        return subprocess.CompletedProcess(
            argv,
            result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert verify_oidc_runs(
        (reference,),
        expected_account_fingerprints((reference,)),
        run_command,
        repository_root=tmp_path,
    )[0].run_id == 1001
    assert len(backend.calls) == 6


@pytest.mark.parametrize(
    "run_updates",
    (
        {"event": "push"},
        {"status": "in_progress"},
        {"conclusion": "failure"},
        {"run_attempt": 0},
        {"path": ".github/workflows/other.yml"},
        {"repository_full_name": "attacker/fork"},
        {"html_url": "https://github.com/attacker/fork/actions/runs/1001"},
    ),
)
def test_oidc_verifier_rejects_untrusted_run_metadata(
    run_updates: Mapping[str, object],
) -> None:
    reference = oidc_reference("nonprod", 1001)
    runner = VerifiedGhRunner.for_references(
        (reference,), run_updates=run_updates
    )

    with pytest.raises(ValueError, match="wave0_oidc_run_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), runner
        )


def test_oidc_verifier_rejects_missing_or_failed_expected_environment_job() -> None:
    reference = oidc_reference("production", 1001)
    failed_jobs = {
        "total_count": 2,
        "jobs": [
            {
                "id": 10011,
                "name": "OIDC smoke (nonprod)",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 10012,
                "name": "OIDC smoke (production)",
                "status": "completed",
                "conclusion": "failure",
            },
        ],
    }
    runner = VerifiedGhRunner.for_references(
        (reference,), job_updates=failed_jobs
    )

    with pytest.raises(ValueError, match="wave0_oidc_job_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), runner
        )


def test_oidc_verifier_rejects_workflow_identity_or_unpinned_source() -> None:
    reference = oidc_reference("nonprod", 1001)
    wrong_identity = VerifiedGhRunner.for_references(
        (reference,), workflow_updates={"path": ".github/workflows/other.yml"}
    )
    changed_source = VerifiedGhRunner.for_references(
        (reference,),
        source=WORKFLOW_PATH.read_text(encoding="utf-8") + "\n# changed\n",
    )

    with pytest.raises(ValueError, match="wave0_oidc_workflow_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), wrong_identity
        )
    with pytest.raises(ValueError, match="wave0_oidc_workflow_source_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), changed_source
        )


@pytest.mark.parametrize(
    ("trusted_source", "untrusted_source"),
    (
        (
            "aws-actions/configure-aws-credentials@"
            "00943011d9042930efac3dcd3a170e4273319bc8",
            "aws-actions/configure-aws-credentials@"
            "1111111111111111111111111111111111111111",
        ),
        (
            "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
            "actions/upload-artifact@1111111111111111111111111111111111111111",
        ),
        (
            '"account_fingerprint": $account_fingerprint',
            '"account_id": $account_fingerprint',
        ),
    ),
)
def test_oidc_verifier_rejects_untrusted_workflow_source_with_matching_digest(
    monkeypatch: pytest.MonkeyPatch,
    trusted_source: str,
    untrusted_source: str,
) -> None:
    reference = oidc_reference("nonprod", 1001)
    changed_source = WORKFLOW_PATH.read_text(encoding="utf-8").replace(
        trusted_source,
        untrusted_source,
    )
    monkeypatch.setattr(
        aws_readiness,
        "_GITHUB_WORKFLOW_SHA256",
        hashlib.sha256(changed_source.encode("utf-8")).hexdigest(),
    )
    runner = VerifiedGhRunner.for_references((reference,), source=changed_source)

    with pytest.raises(ValueError, match="wave0_oidc_workflow_source_unverified"):
        verify_oidc_runs(
            (reference,), expected_account_fingerprints((reference,)), runner
        )


def test_oidc_verifier_with_no_references_makes_no_gh_calls() -> None:
    runner = FakeRunner({})

    assert verify_oidc_runs((), {}, runner) == ()
    assert runner.calls == []


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

    assert finalize_status(report, valid_identity()) == "blocked_account_access"


def test_public_finalizer_rejects_fabricated_oidc_self_attestation() -> None:
    runner = FakeRunner({})
    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        fabricated_oidc(),
        runner,
    )

    assert result.gate_status == "blocked_account_access"
    assert result.oidc_evidence == ()
    assert runner.calls == []


def test_public_finalizer_verifies_references_and_fails_closed_on_mismatch() -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(
        references,
        run_updates={"conclusion": "failure"},
    )

    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        references,
        runner,
    )

    assert result.gate_status == "blocked_account_access"
    assert result.oidc_evidence == ()
    assert runner.calls


def test_public_finalizer_absent_references_stays_blocked_without_gh_calls() -> None:
    runner = FakeRunner({})

    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        (),
        runner,
    )

    assert result.gate_status == "blocked_account_access"
    assert result.oidc_evidence == ()
    assert runner.calls == []


def test_gate_requires_every_service_and_quota_probe() -> None:
    report = build_report_fixture()
    report = report.model_copy(
        update={
            "checks": tuple(
                item for item in report.checks if item.name != "production_agentcore_quotas"
            )
        },
    )

    assert finalize_status(report, valid_identity()) == "blocked_region_service"


def test_gate_requires_identity_and_both_oidc_jobs() -> None:
    report = build_report_fixture()
    assert finalize_status(report, None) == "blocked_identity_compatibility"
    assert finalize_status(
        report,
        valid_identity(),
        valid_oidc_references()[:1],
    ) == "blocked_account_access"


def test_gate_rejects_duplicate_oidc_environment_url_or_hash() -> None:
    report = build_report_fixture()
    first, second = valid_oidc_references()
    same_environment = (
        first,
        second.model_copy(update={"environment": first.environment}),
    )
    duplicate_url = (first, second.model_copy(update={"run_url": first.run_url}))

    assert finalize_status(report, valid_identity(), same_environment) == (
        "blocked_account_access"
    )
    assert finalize_status(report, valid_identity(), duplicate_url) == (
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

    assert finalize_status(wrong_version, valid_identity()) == "blocked_tooling"
    assert finalize_status(wrong_code, valid_identity()) == "blocked_tooling"
    assert finalize_status(forged_gate, valid_identity()) == "blocked_tooling"


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

    assert finalize_status(wrong_accounts, valid_identity()) == "blocked_account_access"
    assert finalize_status(wrong_account_code, valid_identity()) == (
        "blocked_account_access"
    )
    assert finalize_status(wrong_service_code, valid_identity()) == (
        "blocked_region_service"
    )
    assert finalize_status(wrong_region, valid_identity()) == (
        "blocked_region_service"
    )


def test_gate_revalidates_credential_safe_report_fields() -> None:
    unsafe_report = replace_check(
        build_report_fixture(),
        "nonprod_s3",
        summary="Bearer unsafe-value",
    )

    assert finalize_status(unsafe_report, valid_identity()) == (
        "blocked_identity_compatibility"
    )


def test_public_finalizer_rejects_minimal_identity_decision() -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(references)

    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        references,
        runner,
    )

    assert result.gate_status == "blocked_identity_compatibility"
    assert len(result.oidc_evidence) == 2
    assert len(runner.calls) == 12


def test_gate_is_ready_only_after_reverified_identity_and_deleted_cognito_stack(
    tmp_path: Path,
) -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(references)

    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        references,
        runner,
        identity_proof_references=valid_identity_proof(tmp_path),
    )

    assert result.gate_status == "ready"
    assert runner.calls[-1] == COGNITO_STACK_COMMAND


def test_gate_blocks_when_disposable_cognito_stack_still_exists(tmp_path: Path) -> None:
    references = valid_oidc_references()
    runner = VerifiedGhRunner.for_references(references)
    runner.results[COGNITO_STACK_COMMAND] = CommandResult(
        0,
        json.dumps(
            {
                "Stacks": [
                    {
                        "StackName": "mercury-wave0-identity-spike",
                        "StackStatus": "CREATE_COMPLETE",
                    }
                ]
            }
        ),
        "",
    )

    result = finalize_wave0_gate(
        build_report_fixture(),
        valid_identity(),
        references,
        runner,
        identity_proof_references=valid_identity_proof(tmp_path),
    )

    assert result.gate_status == "blocked_identity_compatibility"


def test_account_gate_blocks_before_identity_oidc_or_cloudformation_calls(
    tmp_path: Path,
) -> None:
    report = replace_check(
        build_report_fixture(),
        "nonprod_account",
        state=CheckState.BLOCKED,
        code="aws_account_access_blocked",
        details={"returncode": 254},
    )
    runner = FakeRunner({})

    result = finalize_wave0_gate(
        report,
        valid_identity(),
        valid_oidc_references(),
        runner,
        identity_proof_references=valid_identity_proof(tmp_path),
    )

    assert result.gate_status == "blocked_account_access"
    assert runner.calls == []


def test_cli_verifies_explicit_environment_bindings_and_stores_only_hashes(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decision_path = tmp_path / "identity-decision.yaml"
    decision_path.write_text(
        json.dumps(valid_identity().model_dump(mode="json")), encoding="utf-8"
    )
    references = (
        oidc_reference("nonprod", 1001),
        oidc_reference("production", 1002),
    )
    evidence = verify_oidc_runs(
        references,
        expected_account_fingerprints(references),
        VerifiedGhRunner.for_references(references),
    )
    runner = cli_runner(references)
    finalizer_calls = 0
    proof_references = valid_identity_proof(
        tmp_path / "proof",
        checked_at=datetime.now(UTC),
    )

    def counting_finalizer(
        report,
        identity_decision,
        oidc_references,
        injected_runner,
        *,
        identity_proof_references,
    ):
        nonlocal finalizer_calls
        finalizer_calls += 1
        assert identity_proof_references == proof_references
        return finalize_wave0_gate(
            report,
            identity_decision,
            oidc_references,
            injected_runner,
            identity_proof_references=identity_proof_references,
        )

    monkeypatch.setitem(
        readiness_main.__globals__,
        "finalize_wave0_gate",
        counting_finalizer,
    )
    output = ROOT / ".artifacts/aws/wave0/task5-cli-test.json"
    output.unlink(missing_ok=True)

    try:
        result = readiness_main(
            [
                "--identity-decision",
                str(decision_path),
                "--identity-proof",
                (
                    f"codex={proof_references[0].probe_path},"
                    f"{proof_references[0].evidence_path}"
                ),
                "--identity-proof",
                (
                    f"chatgpt={proof_references[1].probe_path},"
                    f"{proof_references[1].evidence_path}"
                ),
                "--identity-proof",
                (
                    f"claude={proof_references[2].probe_path},"
                    f"{proof_references[2].evidence_path}"
                ),
                "--oidc-run",
                f"nonprod={references[0].run_url}",
                "--oidc-run",
                f"production={references[1].run_url}",
                "--output",
                str(output),
            ],
            runner=runner,
        )

        assert result == 0
        serialized = output.read_text(encoding="utf-8")
        assert "gate_status=ready" in capsys.readouterr().out
        assert hashlib.sha256(decision_path.read_bytes()).hexdigest() in serialized
        for reference, verified in zip(references, evidence, strict=True):
            assert str(reference.run_url) not in serialized
            assert verified.evidence_sha256 in serialized
            assert verified.head_sha not in serialized
            assert verified.workflow_sha256 not in serialized
            assert hashlib.sha256(
                str(reference.run_url).encode("utf-8")
            ).hexdigest() not in serialized
        assert len([call for call in runner.calls if call[:2] == ("gh", "api")]) == 10
        assert COGNITO_STACK_COMMAND in runner.calls
        assert str(tmp_path) not in serialized
        assert "arn:aws:iam" not in serialized
        assert "123456789012" not in serialized
        assert "210987654321" not in serialized
        assert finalizer_calls == 1
    finally:
        output.unlink(missing_ok=True)


@pytest.mark.parametrize(
    "bindings",
    (
        (
            "staging=https://github.com/natthaphonchop2-creator/"
            "mercury-tools/actions/runs/1001",
        ),
        (
            "nonprod=https://github.com/natthaphonchop2-creator/"
            "mercury-tools/actions/runs/1001",
            "nonprod=https://github.com/natthaphonchop2-creator/"
            "mercury-tools/actions/runs/1002",
        ),
        (
            "https://github.com/natthaphonchop2-creator/"
            "mercury-tools/actions/runs/1001",
        ),
    ),
)
def test_cli_rejects_unknown_duplicate_or_implicit_environment_bindings(
    bindings: tuple[str, ...],
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = cli_runner(())
    arguments = ["--skip-live"]
    for binding in bindings:
        arguments.extend(("--oidc-run", binding))

    assert readiness_main(arguments, runner=runner) == 3
    assert capsys.readouterr().out == "wave0_readiness_invalid_input\n"
    assert not any(call[:2] == ("gh", "api") for call in runner.calls)


def test_cli_with_absent_oidc_bindings_makes_no_gh_calls_and_stays_blocked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = cli_runner(())
    output = ROOT / ".artifacts/aws/wave0/task5-cli-absent.json"
    output.unlink(missing_ok=True)

    try:
        assert readiness_main(
            ["--skip-live", "--output", str(output)], runner=runner
        ) == 2
        assert "gate_status=blocked_account_access" in capsys.readouterr().out
        assert not any(call[:2] == ("gh", "api") for call in runner.calls)
    finally:
        output.unlink(missing_ok=True)


def test_cli_failed_gh_proof_stays_blocked_without_persisting_url_hash(
    capsys: pytest.CaptureFixture[str],
) -> None:
    reference = oidc_reference("nonprod", 1001)
    jobs = {
        "total_count": 1,
        "jobs": [
            {
                "id": 10011,
                "name": "OIDC smoke (nonprod)",
                "status": "completed",
                "conclusion": "failure",
            }
        ],
    }
    runner = cli_runner((reference,), job_updates=jobs)
    output = ROOT / ".artifacts/aws/wave0/task5-cli-failed-gh.json"
    output.unlink(missing_ok=True)

    try:
        result = readiness_main(
            [
                "--skip-live",
                "--oidc-run",
                f"nonprod={reference.run_url}",
                "--output",
                str(output),
            ],
            runner=runner,
        )

        assert result == 2
        assert "gate_status=blocked_account_access" in capsys.readouterr().out
        serialized = output.read_text(encoding="utf-8")
        assert str(reference.run_url) not in serialized
        assert hashlib.sha256(
            str(reference.run_url).encode("utf-8")
        ).hexdigest() not in serialized
    finally:
        output.unlink(missing_ok=True)
