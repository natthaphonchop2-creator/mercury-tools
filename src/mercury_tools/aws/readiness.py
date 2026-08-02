"""Secret-safe local and live readiness probes for AWS Wave 0."""

from __future__ import annotations

import base64
import binascii
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

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from mercury_tools.aws.commands import CommandResult, CommandRunner, run_command
from mercury_tools.aws.identity import IdentityDecision
from mercury_tools.aws.models import (
    CheckResult,
    CheckState,
    EnvironmentName,
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
_ACCOUNT_FINGERPRINT_RE = re.compile(r"^[a-f0-9]{12}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_GITHUB_REPOSITORY = "natthaphonchop2-creator/mercury-tools"
_GITHUB_RUN_PATH_RE = re.compile(
    r"^/natthaphonchop2-creator/mercury-tools/actions/runs/([1-9]\d*)/?$"
)
_GITHUB_WORKFLOW_PATH = ".github/workflows/aws-wave0-oidc-smoke.yml"
_GITHUB_WORKFLOW_SHA256 = "6a4da75fb63a43b3a9e0bfa480cde3251aea0fa0c6459afa501d40c9bc4b9ded"
_GITHUB_CREDENTIALS_ACTION = (
    "aws-actions/configure-aws-credentials@00943011d9042930efac3dcd3a170e4273319bc8"
)
_GIT_SHA_RE = re.compile(r"^[a-f0-9]{40}$")
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


class _OidcModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class OidcRunReference(_OidcModel):
    """One explicitly environment-bound GitHub Actions run URL."""

    environment: EnvironmentName
    run_url: AnyHttpUrl

    @model_validator(mode="after")
    def validate_run_reference(self) -> OidcRunReference:
        if _run_id_from_url(self.run_url) is None:
            raise ValueError("wave0_oidc_run_url_invalid")
        return self


class OidcRunEvidence(OidcRunReference):
    """Closed evidence produced only after independent GitHub verification."""

    run_id: StrictInt = Field(gt=0)
    head_sha: str = Field(pattern=r"^[a-f0-9]{40}$")
    workflow_id: StrictInt = Field(gt=0)
    workflow_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    job_id: StrictInt = Field(gt=0)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_run_evidence(self) -> OidcRunEvidence:
        if _run_id_from_url(self.run_url) != self.run_id:
            raise ValueError("wave0_oidc_run_url_invalid")
        if self.workflow_sha256 != _GITHUB_WORKFLOW_SHA256:
            raise ValueError("wave0_oidc_workflow_source_unverified")
        expected_hash = _canonical_oidc_evidence_sha256(
            environment=self.environment,
            run_url=self.run_url,
            run_id=self.run_id,
            head_sha=self.head_sha,
            workflow_id=self.workflow_id,
            workflow_sha256=self.workflow_sha256,
            job_id=self.job_id,
        )
        if not secrets.compare_digest(self.evidence_sha256, expected_hash):
            raise ValueError("wave0_oidc_evidence_hash_invalid")
        return self


def _run_id_from_url(run_url: AnyHttpUrl) -> int | None:
    if (
        run_url.scheme != "https"
        or run_url.host != "github.com"
        or run_url.username is not None
        or run_url.password is not None
        or run_url.query is not None
        or run_url.fragment is not None
    ):
        return None
    match = _GITHUB_RUN_PATH_RE.fullmatch(run_url.path)
    return int(match.group(1)) if match is not None else None


def _canonical_oidc_evidence_sha256(
    *,
    environment: EnvironmentName,
    run_url: AnyHttpUrl,
    run_id: int,
    head_sha: str,
    workflow_id: int,
    workflow_sha256: str,
    job_id: int,
) -> str:
    payload = {
        "environment": environment.value,
        "head_sha": head_sha,
        "job_id": job_id,
        "repository": _GITHUB_REPOSITORY,
        "run_id": run_id,
        "run_url_sha256": hashlib.sha256(str(run_url).encode("utf-8")).hexdigest(),
        "schema_version": "mercury.aws.wave0.oidc_run_evidence.v2",
        "workflow_id": workflow_id,
        "workflow_path": _GITHUB_WORKFLOW_PATH,
        "workflow_sha256": workflow_sha256,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def verify_oidc_runs(
    references: tuple[OidcRunReference, ...],
    runner: CommandRunner = run_command,
) -> tuple[OidcRunEvidence, ...]:
    """Verify exact GitHub runs and return only closed canonical evidence."""

    try:
        checked = tuple(
            OidcRunReference.model_validate(item.model_dump(mode="python"))
            for item in references
        )
    except (AttributeError, TypeError, ValidationError):
        raise ValueError("wave0_oidc_bindings_invalid") from None
    if len({item.environment for item in checked}) != len(checked) or len(
        {str(item.run_url) for item in checked}
    ) != len(checked):
        raise ValueError("wave0_oidc_bindings_invalid")
    return tuple(_verify_oidc_run(item, runner) for item in checked)


def _verify_oidc_run(
    reference: OidcRunReference,
    runner: CommandRunner,
) -> OidcRunEvidence:
    run_id = _run_id_from_url(reference.run_url)
    if run_id is None:
        raise ValueError("wave0_oidc_run_unverified")
    run_endpoint = f"repos/{_GITHUB_REPOSITORY}/actions/runs/{run_id}"
    run = _gh_json(
        run_endpoint,
        (
            "{id,html_url,event,status,conclusion,head_sha,workflow_id,path,"
            "repository_full_name:.repository.full_name}"
        ),
        runner,
        "wave0_oidc_run_unverified",
    )
    expected_run_keys = {
        "id",
        "html_url",
        "event",
        "status",
        "conclusion",
        "head_sha",
        "workflow_id",
        "path",
        "repository_full_name",
    }
    if set(run) != expected_run_keys:
        raise ValueError("wave0_oidc_run_unverified")
    head_sha = run["head_sha"]
    workflow_id = run["workflow_id"]
    if (
        type(run["id"]) is not int
        or run["id"] != run_id
        or run["html_url"] != str(reference.run_url)
        or run["event"] != "workflow_dispatch"
        or run["status"] != "completed"
        or run["conclusion"] != "success"
        or type(head_sha) is not str
        or _GIT_SHA_RE.fullmatch(head_sha) is None
        or type(workflow_id) is not int
        or workflow_id <= 0
        or run["path"] != _GITHUB_WORKFLOW_PATH
        or run["repository_full_name"] != _GITHUB_REPOSITORY
    ):
        raise ValueError("wave0_oidc_run_unverified")

    workflow = _gh_json(
        f"repos/{_GITHUB_REPOSITORY}/actions/workflows/{workflow_id}",
        "{id,path}",
        runner,
        "wave0_oidc_workflow_unverified",
    )
    if workflow != {"id": workflow_id, "path": _GITHUB_WORKFLOW_PATH}:
        raise ValueError("wave0_oidc_workflow_unverified")

    jobs = _gh_json(
        f"{run_endpoint}/jobs?per_page=100",
        "{total_count,jobs:[.jobs[]|{id,name,status,conclusion}]}",
        runner,
        "wave0_oidc_job_unverified",
    )
    job_id = _verified_environment_job(jobs, reference.environment)

    source_result = runner(
        (
            "gh",
            "api",
            f"repos/{_GITHUB_REPOSITORY}/contents/{_GITHUB_WORKFLOW_PATH}?ref={head_sha}",
            "--jq",
            ".content",
        ),
        20,
    )
    try:
        workflow_source = base64.b64decode(
            "".join(source_result.stdout.split()).encode("ascii"), validate=True
        ).decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, binascii.Error):
        raise ValueError("wave0_oidc_workflow_source_unverified") from None
    if source_result.returncode != 0 or not _workflow_source_valid(workflow_source):
        raise ValueError("wave0_oidc_workflow_source_unverified")
    workflow_sha256 = hashlib.sha256(workflow_source.encode("utf-8")).hexdigest()
    evidence_sha256 = _canonical_oidc_evidence_sha256(
        environment=reference.environment,
        run_url=reference.run_url,
        run_id=run_id,
        head_sha=head_sha,
        workflow_id=workflow_id,
        workflow_sha256=workflow_sha256,
        job_id=job_id,
    )
    return OidcRunEvidence(
        environment=reference.environment,
        run_url=reference.run_url,
        run_id=run_id,
        head_sha=head_sha,
        workflow_id=workflow_id,
        workflow_sha256=workflow_sha256,
        job_id=job_id,
        evidence_sha256=evidence_sha256,
    )


def _gh_json(
    endpoint: str,
    jq_filter: str,
    runner: CommandRunner,
    error_code: str,
) -> dict[str, Any]:
    result = runner(("gh", "api", endpoint, "--jq", jq_filter), 20)
    if result.returncode != 0:
        raise ValueError(error_code)
    try:
        payload: Any = json.loads(result.stdout)
    except (json.JSONDecodeError, RecursionError, UnicodeError):
        raise ValueError(error_code) from None
    if not isinstance(payload, dict):
        raise ValueError(error_code)
    return payload


def _verified_environment_job(jobs: dict[str, Any], environment: EnvironmentName) -> int:
    if set(jobs) != {"total_count", "jobs"} or type(jobs["total_count"]) is not int:
        raise ValueError("wave0_oidc_job_unverified")
    records = jobs["jobs"]
    if (
        not isinstance(records, list)
        or jobs["total_count"] != len(records)
        or len(records) > 100
    ):
        raise ValueError("wave0_oidc_job_unverified")
    expected_name = f"OIDC smoke ({environment.value})"
    matches = [
        item
        for item in records
        if isinstance(item, dict) and item.get("name") == expected_name
    ]
    if len(matches) != 1:
        raise ValueError("wave0_oidc_job_unverified")
    job = matches[0]
    if (
        set(job) != {"id", "name", "status", "conclusion"}
        or type(job["id"]) is not int
        or job["id"] <= 0
        or job["status"] != "completed"
        or job["conclusion"] != "success"
    ):
        raise ValueError("wave0_oidc_job_unverified")
    return job["id"]


def _workflow_source_valid(source: str) -> bool:
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != _GITHUB_WORKFLOW_SHA256:
        return False
    try:
        workflow: Any = yaml.load(source, Loader=yaml.BaseLoader)
        dispatch = workflow["on"]
        permissions = workflow["permissions"]
        jobs = workflow["jobs"]
        job = jobs["oidc-smoke"]
        assume_role = job["steps"][0]
    except (KeyError, TypeError, yaml.YAMLError):
        return False
    try:
        return bool(
            set(dispatch) == {"workflow_dispatch"}
            and permissions == {"contents": "read", "id-token": "write"}
            and set(jobs) == {"oidc-smoke"}
            and job.get("environment") == "${{ matrix.environment }}"
            and job.get("strategy", {}).get("fail-fast") == "false"
            and job.get("strategy", {}).get("matrix", {}).get("environment")
            == ["nonprod", "production"]
            and assume_role.get("uses") == _GITHUB_CREDENTIALS_ACTION
            and assume_role.get("with")
            == {
                "role-to-assume": "${{ vars.AWS_WAVE0_ROLE_ARN }}",
                "aws-region": "ap-southeast-1",
                "mask-aws-account-id": "true",
            }
        )
    except (AttributeError, TypeError):
        return False


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


def finalize_wave0_gate(
    report: ReadinessReport,
    identity_decision: IdentityDecision | None,
    oidc_evidence: tuple[OidcRunEvidence, ...],
) -> GateStatus:
    """Independently require every exact Wave 0 proof before returning ready."""

    checks = tuple(report.checks)
    counts = Counter(item.name for item in checks)
    by_name = {item.name: item for item in checks}

    if not _final_tooling_valid(counts, by_name):
        return GateStatus.BLOCKED_TOOLING
    if report.gate_status is GateStatus.BLOCKED_TOOLING:
        return GateStatus.BLOCKED_TOOLING

    if not _final_accounts_valid(report, counts, by_name):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if not _final_oidc_valid(oidc_evidence):
        return GateStatus.BLOCKED_ACCOUNT_ACCESS
    if report.gate_status is GateStatus.BLOCKED_ACCOUNT_ACCESS:
        return GateStatus.BLOCKED_ACCOUNT_ACCESS

    if not _final_region_services_valid(report, counts, by_name):
        return GateStatus.BLOCKED_REGION_SERVICE
    if report.gate_status is GateStatus.BLOCKED_REGION_SERVICE:
        return GateStatus.BLOCKED_REGION_SERVICE

    if not _final_report_contract_valid(report, counts):
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
    if not _final_identity_valid(identity_decision):
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
    if report.gate_status is not GateStatus.READY:
        return GateStatus.BLOCKED_IDENTITY_COMPATIBILITY
    return GateStatus.READY


def _final_tooling_valid(
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    if _inventory_group_invalid(counts, _TOOL_NAMES):
        return False
    for name in TOOL_COMMANDS:
        check = by_name[name]
        if check.state is not CheckState.PASS or check.code != "tool_version_verified":
            return False
        if set(check.details) != {"version"}:
            return False
        version_text = check.details["version"]
        if not isinstance(version_text, str) or _VERSION_RE.fullmatch(version_text) is None:
            return False
        version = _version_tuple(version_text)
        if version is None or not _tool_version_allowed(name, version):
            return False
    return True


def _final_accounts_valid(
    report: ReadinessReport,
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    expected_accounts = (
        (EnvironmentName.NONPROD, "mercury-nonprod", EnvironmentName.NONPROD),
        (EnvironmentName.PRODUCTION, "mercury-prod", EnvironmentName.PRODUCTION),
    )
    actual_accounts = tuple(
        (account.environment, account.alias, account.github_environment)
        for account in report.accounts
    )
    if actual_accounts != expected_accounts or any(
        account.profile != account.alias for account in report.accounts
    ):
        return False
    if _inventory_group_invalid(counts, _ACCOUNT_CHECK_NAMES | _ISOLATION_CHECK_NAMES):
        return False

    fingerprints: list[str] = []
    for name in sorted(_ACCOUNT_CHECK_NAMES):
        check = by_name[name]
        if check.state is not CheckState.PASS or check.code != "aws_account_verified":
            return False
        allowed_keys = {"account_fingerprint", "oidc_evidence_sha256"}
        if "account_fingerprint" not in check.details or not set(check.details) <= allowed_keys:
            return False
        fingerprint = check.details["account_fingerprint"]
        if (
            not isinstance(fingerprint, str)
            or _ACCOUNT_FINGERPRINT_RE.fullmatch(fingerprint) is None
        ):
            return False
        oidc_hash = check.details.get("oidc_evidence_sha256")
        if oidc_hash is not None and (
            not isinstance(oidc_hash, str) or _SHA256_RE.fullmatch(oidc_hash) is None
        ):
            return False
        fingerprints.append(fingerprint)

    isolation = by_name["aws_account_isolation"]
    if isolation.state is not CheckState.PASS or isolation.code != "aws_accounts_isolated":
        return False
    if not set(isolation.details) <= {"identity_evidence_sha256"}:
        return False
    identity_hash = isolation.details.get("identity_evidence_sha256")
    if identity_hash is not None and (
        not isinstance(identity_hash, str) or _SHA256_RE.fullmatch(identity_hash) is None
    ):
        return False
    return len(set(fingerprints)) == 2


def _final_oidc_valid(oidc_evidence: tuple[OidcRunEvidence, ...]) -> bool:
    if len(oidc_evidence) != len(EnvironmentName):
        return False
    try:
        checked = tuple(
            OidcRunEvidence.model_validate(item.model_dump(mode="python"))
            for item in oidc_evidence
        )
    except (AttributeError, TypeError, ValidationError):
        return False
    return (
        {item.environment for item in checked} == set(EnvironmentName)
        and len({str(item.run_url) for item in checked}) == len(checked)
        and len({item.evidence_sha256 for item in checked}) == len(checked)
    )


def _final_region_services_valid(
    report: ReadinessReport,
    counts: Counter[str],
    by_name: dict[str, CheckResult],
) -> bool:
    if report.primary_region != "ap-southeast-1":
        return False
    if _inventory_group_invalid(counts, _SERVICE_CHECK_NAMES):
        return False
    return all(
        by_name[name].state is CheckState.PASS
        and by_name[name].code == "aws_service_available"
        and not by_name[name].details
        for name in _SERVICE_CHECK_NAMES
    )


def _final_report_contract_valid(
    report: ReadinessReport,
    counts: Counter[str],
) -> bool:
    try:
        ReadinessReport.model_validate(report.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError):
        return False
    if (
        report.schema_version != "mercury.aws.wave0.report.v1"
        or report.github_repository != "natthaphonchop2-creator/mercury-tools"
        or report.checked_at.tzinfo is None
        or report.checked_at.utcoffset() is None
    ):
        return False
    return set(counts) == set(_EXPECTED_CHECK_NAMES) and all(
        counts[name] == 1 for name in _EXPECTED_CHECK_NAMES
    )


def _final_identity_valid(identity_decision: IdentityDecision | None) -> bool:
    if identity_decision is None:
        return False
    try:
        IdentityDecision.model_validate(identity_decision.model_dump(mode="python"))
    except (AttributeError, TypeError, ValidationError):
        return False
    return True


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
