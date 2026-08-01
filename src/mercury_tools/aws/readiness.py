"""Secret-safe local and live readiness probes for AWS Wave 0."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mercury_tools.aws.commands import CommandResult, CommandRunner, run_command
from mercury_tools.aws.models import (
    CheckResult,
    CheckState,
    GateStatus,
    ReadinessReport,
    Wave0Config,
)

TOOL_COMMANDS = {
    "aws_cli": ("aws", "--version"),
    "node": ("node", "--version"),
    "python": ("uv", "run", "python", "--version"),
    "agentcore_cli": ("npx", "--no-install", "agentcore", "--version"),
    "aws_cdk": ("npx", "--no-install", "cdk", "--version"),
}

SERVICE_COMMANDS = {
    "agentcore_runtime": (
        "bedrock-agentcore-control",
        "list-agent-runtimes",
        "--max-results",
        "1",
    ),
    "agentcore_gateway": (
        "bedrock-agentcore-control",
        "list-gateways",
        "--max-results",
        "1",
    ),
    "agentcore_identity": (
        "bedrock-agentcore-control",
        "list-workload-identities",
        "--max-results",
        "1",
    ),
    "bedrock_knowledge_bases": (
        "bedrock-agent",
        "list-knowledge-bases",
        "--max-results",
        "1",
    ),
    "aurora_postgresql": (
        "rds",
        "describe-orderable-db-instance-options",
        "--engine",
        "aurora-postgresql",
        "--db-instance-class",
        "db.serverless",
        "--max-records",
        "1",
    ),
    "s3": ("s3api", "list-buckets"),
    "kms": ("kms", "list-aliases", "--limit", "1"),
    "ecr": ("ecr", "describe-repositories", "--max-results", "1"),
    "cloudwatch_logs": ("logs", "describe-log-groups", "--limit", "1"),
    "agentcore_quotas": (
        "service-quotas",
        "list-service-quotas",
        "--service-code",
        "bedrock-agentcore",
        "--max-results",
        "100",
    ),
}

_VERSION_RE = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_ACCOUNT_ID_RE = re.compile(r"^\d{12}$")
_THROTTLING_MARKERS = (
    "throttl",
    "rate exceeded",
    "too many requests",
    "requestlimitexceeded",
)
_TOOL_NAMES = frozenset(TOOL_COMMANDS)
_ACCOUNT_CHECK_NAMES = frozenset({"nonprod_account", "production_account"})
_ISOLATION_CHECK_NAMES = frozenset({"aws_account_isolation"})
_SERVICE_CHECK_NAMES = frozenset(
    f"{environment}_{service}"
    for environment in ("nonprod", "production")
    for service in SERVICE_COMMANDS
)
_EXPECTED_CHECK_NAMES = (
    _TOOL_NAMES | _ACCOUNT_CHECK_NAMES | _ISOLATION_CHECK_NAMES | _SERVICE_CHECK_NAMES
)
_ACCOUNT_ACCESS_CODES = frozenset(
    {
        "aws_account_access_blocked",
        "aws_account_response_invalid",
        "aws_live_checks_skipped",
    }
)
_IDENTITY_CODES = frozenset(
    {"aws_accounts_not_isolated", "wave0_evidence_inventory_invalid"}
)
_INVENTORY_INVALID_CODE = "wave0_evidence_inventory_invalid"
_REGION_CODES = frozenset(
    {
        "aws_service_probe_failed",
        "aws_service_response_invalid",
        "aws_service_throttled",
        "aurora_serverless_unavailable",
    }
)


def _check(
    name: str,
    state: CheckState,
    code: str,
    summary: str,
    **details: str | bool | int | float,
) -> CheckResult:
    return CheckResult(name=name, state=state, code=code, summary=summary, details=details)


def _version_tuple(output: str) -> tuple[int, int, int] | None:
    match = _VERSION_RE.search(output)
    if match is None:
        return None
    return tuple(int(item) for item in match.groups())  # type: ignore[return-value]


def _tool_version_allowed(name: str, version: tuple[int, int, int]) -> bool:
    if name == "aws_cli":
        return version >= (2, 36, 14)
    if name == "node":
        return version[0] >= 20
    if name == "python":
        return (3, 11, 0) <= version < (3, 14, 0)
    if name == "agentcore_cli":
        return version == (0, 25, 0)
    if name == "aws_cdk":
        return version == (2, 1134, 0)
    return False


def check_local_toolchain(runner: CommandRunner = run_command) -> tuple[CheckResult, ...]:
    """Verify the exact Wave 0 local command set and version constraints."""

    checks: list[CheckResult] = []
    for name, command in TOOL_COMMANDS.items():
        result = runner(command, 20)
        output = f"{result.stdout}\n{result.stderr}"
        version = _version_tuple(output)
        if result.returncode == 0 and version is not None and _tool_version_allowed(name, version):
            checks.append(
                _check(
                    name,
                    CheckState.PASS,
                    "tool_version_verified",
                    "Required local tool version verified.",
                    version=".".join(str(item) for item in version),
                )
            )
        else:
            checks.append(
                _check(
                    name,
                    CheckState.BLOCKED,
                    "tool_version_unsupported",
                    "Required local tool is missing or outside the supported version range.",
                    returncode=result.returncode,
                )
            )
    return tuple(checks)


def fingerprint_account_id(account_id: str) -> str:
    """Return the publishable fingerprint for one validated AWS account ID."""

    if _ACCOUNT_ID_RE.fullmatch(account_id) is None:
        raise ValueError("wave0_account_id_invalid")
    return hashlib.sha256(account_id.encode("ascii")).hexdigest()[:12]


def check_aws_accounts(
    config: Wave0Config,
    runner: CommandRunner = run_command,
) -> tuple[CheckResult, ...]:
    """Verify both AWS profiles and prove that their account IDs are isolated."""

    checks: list[CheckResult] = []
    fingerprints: list[str] = []
    for account in config.accounts:
        command = (
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
        result = runner(command, 20)
        if result.returncode != 0:
            checks.append(
                _check(
                    f"{account.environment.value}_account",
                    CheckState.BLOCKED,
                    "aws_account_access_blocked",
                    "AWS account identity could not be verified.",
                    returncode=result.returncode,
                )
            )
            continue

        account_id = _parse_account_id(result)
        if account_id is None:
            checks.append(
                _check(
                    f"{account.environment.value}_account",
                    CheckState.BLOCKED,
                    "aws_account_response_invalid",
                    "AWS account identity response did not match the required shape.",
                )
            )
            continue

        fingerprint = fingerprint_account_id(account_id)
        fingerprints.append(fingerprint)
        checks.append(
            _check(
                f"{account.environment.value}_account",
                CheckState.PASS,
                "aws_account_verified",
                "AWS account identity verified.",
                account_fingerprint=fingerprint,
            )
        )

    if len(fingerprints) == len(config.accounts):
        isolated = len(set(fingerprints)) == len(fingerprints)
        checks.append(
            _check(
                "aws_account_isolation",
                CheckState.PASS if isolated else CheckState.BLOCKED,
                "aws_accounts_isolated" if isolated else "aws_accounts_not_isolated",
                (
                    "AWS account fingerprints are isolated."
                    if isolated
                    else "AWS account fingerprints are not isolated."
                ),
            )
        )
    return tuple(checks)


def _parse_account_id(result: CommandResult) -> str | None:
    try:
        payload: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        return None
    if not isinstance(payload, dict):
        return None
    account_id = payload.get("Account")
    if not isinstance(account_id, str) or _ACCOUNT_ID_RE.fullmatch(account_id) is None:
        return None
    return account_id


def check_region_services(
    config: Wave0Config,
    runner: CommandRunner = run_command,
) -> tuple[CheckResult, ...]:
    """Probe every required service for both profiles in the frozen Region."""

    checks: list[CheckResult] = []
    for account in config.accounts:
        for probe in config.required_service_probes:
            name = probe.value
            command = (
                "aws",
                *SERVICE_COMMANDS[name],
                "--profile",
                account.profile,
                "--region",
                config.primary_region,
                "--output",
                "json",
                "--no-cli-pager",
            )
            result, attempts = _run_service_probe(command, runner)
            check_name = f"{account.environment.value}_{name}"
            if result.returncode != 0:
                throttled = _is_throttled(result)
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aws_service_throttled" if throttled else "aws_service_probe_failed",
                        "Required AWS service probe did not complete successfully.",
                        attempts=attempts,
                        returncode=result.returncode,
                    )
                )
                continue

            payload = _parse_service_payload(result.stdout)
            if payload is None:
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aws_service_response_invalid",
                        "Required AWS service returned an invalid response shape.",
                    )
                )
                continue
            if name == "aurora_postgresql" and not _aurora_serverless_available(payload):
                checks.append(
                    _check(
                        check_name,
                        CheckState.BLOCKED,
                        "aurora_serverless_unavailable",
                        "Aurora PostgreSQL Serverless options were not returned.",
                    )
                )
                continue
            checks.append(
                _check(
                    check_name,
                    CheckState.PASS,
                    "aws_service_available",
                    "Required AWS service probe passed.",
                )
            )
    return tuple(checks)


def _run_service_probe(
    command: tuple[str, ...],
    runner: CommandRunner,
) -> tuple[CommandResult, int]:
    result = runner(command, 20)
    attempts = 1
    while attempts < 3 and _is_throttled(result):
        result = runner(command, 20)
        attempts += 1
    return result, attempts


def _is_throttled(result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in output for marker in _THROTTLING_MARKERS)


def _parse_service_payload(output: str) -> dict[str, Any] | None:
    try:
        payload: Any = json.loads(output)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _aurora_serverless_available(payload: dict[str, Any]) -> bool:
    options = payload.get("OrderableDBInstanceOptions")
    return isinstance(options, list) and len(options) > 0


def aggregate_gate(checks: tuple[CheckResult, ...] | list[CheckResult]) -> GateStatus:
    """Aggregate checks using the frozen fail-closed gate precedence."""

    counts = Counter(item.name for item in checks)
    blocked = [item for item in checks if item.state is not CheckState.PASS]
    substantive_blocked = [item for item in blocked if item.code != _INVENTORY_INVALID_CODE]
    if any(item.name in _TOOL_NAMES for item in substantive_blocked):
        return GateStatus.BLOCKED_TOOLING
    if any(item.code in _ACCOUNT_ACCESS_CODES for item in substantive_blocked):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if any(item.code in _REGION_CODES for item in substantive_blocked):
        return GateStatus.BLOCKED_REGION_SERVICE
    if any(item.code in _IDENTITY_CODES for item in substantive_blocked) or substantive_blocked:
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY

    if _inventory_group_invalid(counts, _TOOL_NAMES):
        return GateStatus.BLOCKED_TOOLING
    if _inventory_group_invalid(counts, _ACCOUNT_CHECK_NAMES):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if _inventory_group_invalid(counts, _SERVICE_CHECK_NAMES):
        return GateStatus.BLOCKED_REGION_SERVICE
    if _inventory_group_invalid(counts, _ISOLATION_CHECK_NAMES) or any(
        name not in _EXPECTED_CHECK_NAMES for name in counts
    ):
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
    return GateStatus.READY


def _inventory_group_invalid(counts: Counter[str], expected: frozenset[str]) -> bool:
    return any(counts[name] != 1 for name in expected)


def _expected_check_names(config: Wave0Config) -> frozenset[str]:
    account_names = {f"{account.environment.value}_account" for account in config.accounts}
    service_names = {
        f"{account.environment.value}_{probe.value}"
        for account in config.accounts
        for probe in config.required_service_probes
    }
    return frozenset((*TOOL_COMMANDS, *account_names, "aws_account_isolation", *service_names))


def _inventory_is_exact(config: Wave0Config, checks: tuple[CheckResult, ...]) -> bool:
    expected = _expected_check_names(config)
    counts = Counter(item.name for item in checks)
    return set(counts) == set(expected) and all(counts[name] == 1 for name in expected)


def build_readiness_report(
    config: Wave0Config,
    checks: tuple[CheckResult, ...] | list[CheckResult],
    *,
    checked_at: datetime | None = None,
) -> ReadinessReport:
    """Build the frozen report model from already sanitized checks."""

    check_tuple = tuple(checks)
    if not _inventory_is_exact(config, check_tuple):
        check_tuple += (
            _check(
                "wave0_evidence_inventory",
                CheckState.BLOCKED,
                _INVENTORY_INVALID_CODE,
                "AWS readiness evidence inventory is incomplete or invalid.",
            ),
        )
    return ReadinessReport(
        schema_version="mercury.aws.wave0.report.v1",
        primary_region=config.primary_region,
        github_repository=config.github_repository,
        checked_at=checked_at or datetime.now(UTC),
        accounts=config.accounts,
        checks=check_tuple,
        gate_status=aggregate_gate(check_tuple),
    )


def write_readiness_report(
    report: ReadinessReport,
    output: Path,
    *,
    repository_root: Path | None = None,
) -> None:
    """Atomically write private evidence below the fixed Wave 0 artifact directory."""

    root = (repository_root or Path.cwd()).resolve()
    allowed_root = root / ".artifacts/aws/wave0"
    candidate = output if output.is_absolute() else root / output
    normalized_output = Path(os.path.abspath(candidate))
    if normalized_output.parent != allowed_root or normalized_output.name in {"", ".", ".."}:
        raise ValueError("wave0_output_path_invalid")

    payload = report.model_dump_json(indent=2).encode("utf-8") + b"\n"
    directory_fd = _open_artifact_directory(root)
    temporary_name: str | None = None
    try:
        _reject_symlinked_output(directory_fd, normalized_output.name)
        descriptor, temporary_name = _create_temporary_file(directory_fd)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(
            temporary_name,
            normalized_output.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        temporary_name = None
        os.fsync(directory_fd)
    except BaseException:
        if temporary_name is not None:
            with suppress(OSError):
                os.unlink(temporary_name, dir_fd=directory_fd)
        raise
    finally:
        os.close(directory_fd)


def _open_artifact_directory(root: Path) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        directory_fd = os.open(root, flags)
    except OSError:
        raise ValueError("wave0_output_path_invalid") from None
    try:
        for component in (".artifacts", "aws", "wave0"):
            with suppress(FileExistsError):
                os.mkdir(component, mode=0o700, dir_fd=directory_fd)
            next_fd = os.open(component, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return directory_fd
    except OSError:
        os.close(directory_fd)
        raise ValueError("wave0_output_path_invalid") from None


def _reject_symlinked_output(directory_fd: int, output_name: str) -> None:
    try:
        mode = os.stat(output_name, dir_fd=directory_fd, follow_symlinks=False).st_mode
    except FileNotFoundError:
        return
    except OSError:
        raise ValueError("wave0_output_path_invalid") from None
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise ValueError("wave0_output_path_invalid")


def _create_temporary_file(directory_fd: int) -> tuple[int, str]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    for _ in range(10):
        name = f".readiness-{secrets.token_hex(8)}"
        try:
            return os.open(name, flags, 0o600, dir_fd=directory_fd), name
        except FileExistsError:
            continue
    raise OSError("wave0_temporary_file_unavailable")
