"""Bounded, redacted subprocess execution for AWS Wave 0 readiness checks."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePath

from mercury_tools.safety.redaction import redact_text

_ALLOWED_PROGRAMS = frozenset({"aws", "node", "npx", "uv", "gh"})
_ALLOWED_ENVIRONMENT = (
    "PATH",
    "HOME",
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "AWS_CONFIG_FILE",
    "AWS_SHARED_CREDENTIALS_FILE",
)
_MAX_OUTPUT_CHARS = 4_096
_MAX_WORKFLOW_SOURCE_CHARS = 8_192
_AWS_ACCESS_KEY_ASSIGNMENT_RE = re.compile(
    r"(?i)\bAWS_ACCESS_KEY_ID\s*[:=]\s*[^\s,;]+"
)
_AWS_SECRET_KEY_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:AWS_)?SECRET_ACCESS_KEY\s*[:=]\s*[^\s,;]+"
)
_AWS_ACCESS_KEY_RE = re.compile(r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])")
_GH_REPOSITORY_PATH = r"repos/natthaphonchop2-creator/mercury-tools"
_GH_RUN_ENDPOINT_RE = re.compile(rf"^{_GH_REPOSITORY_PATH}/actions/runs/[1-9]\d*$")
_GH_WORKFLOW_ENDPOINT_RE = re.compile(
    rf"^{_GH_REPOSITORY_PATH}/actions/workflows/[1-9]\d*$"
)
_GH_JOBS_ENDPOINT_RE = re.compile(
    rf"^{_GH_REPOSITORY_PATH}/actions/runs/[1-9]\d*/jobs\?per_page=100$"
)
_GH_ARTIFACTS_ENDPOINT_RE = re.compile(
    rf"^{_GH_REPOSITORY_PATH}/actions/runs/[1-9]\d*/artifacts\?per_page=100$"
)
_GH_SOURCE_ENDPOINT_RE = re.compile(
    rf"^{_GH_REPOSITORY_PATH}/contents/\.github/workflows/"
    r"aws-wave0-oidc-smoke\.yml\?ref=[a-f0-9]{40}$"
)
_GH_RUN_JQ = (
    "{id,html_url,event,status,conclusion,head_sha,run_attempt,workflow_id,path,"
    "repository_full_name:.repository.full_name}"
)
_GH_WORKFLOW_JQ = "{id,path}"
_GH_JOBS_JQ = (
    "{total_count,jobs:[.jobs[]|"
    "{id,name,status,conclusion,run_id,run_attempt,head_sha}]}"
)
_GH_ARTIFACTS_JQ = (
    "{total_count,artifacts:[.artifacts[]|"
    "{id,name,size_in_bytes,expired,expires_at,"
    "workflow_run:{id:.workflow_run.id,head_sha:.workflow_run.head_sha}}]}"
)
_GH_DOWNLOAD_DIRECTORY_RE = re.compile(
    r"^oidc-download-(?P<run_id>[1-9]\d*)-[a-f0-9]{16}$"
)
_LOCAL_VERSION_COMMANDS = frozenset(
    {
        ("node", "--version"),
        ("uv", "run", "python", "--version"),
        ("npx", "--no-install", "agentcore", "--version"),
        ("npx", "--no-install", "cdk", "--version"),
    }
)
_AWS_PROFILES = ("mercury-nonprod", "mercury-prod")
_AWS_TAILS = (
    ("--profile", "{profile}", "--region", "ap-southeast-1", "--output", "json", "--no-cli-pager"),
)
_AWS_READ_PREFIXES = (
    ("sts", "get-caller-identity"),
    ("bedrock-agentcore-control", "list-agent-runtimes", "--max-results", "1"),
    ("bedrock-agentcore-control", "list-gateways", "--max-results", "1"),
    ("bedrock-agentcore-control", "list-workload-identities", "--max-results", "1"),
    ("bedrock-agent", "list-knowledge-bases", "--max-results", "1"),
    (
        "rds",
        "describe-orderable-db-instance-options",
        "--engine",
        "aurora-postgresql",
        "--db-instance-class",
        "db.serverless",
        "--max-records",
        "1",
    ),
    ("s3api", "list-buckets"),
    ("kms", "list-aliases", "--limit", "1"),
    ("ecr", "describe-repositories", "--max-results", "1"),
    ("logs", "describe-log-groups", "--limit", "1"),
    (
        "service-quotas",
        "list-service-quotas",
        "--service-code",
        "bedrock-agentcore",
        "--max-results",
        "100",
    ),
)
_AWS_READ_COMMANDS = frozenset(
    ("aws", *prefix, *(item.format(profile=profile) for item in tail))
    for profile in _AWS_PROFILES
    for prefix in _AWS_READ_PREFIXES
    for tail in _AWS_TAILS
)
_AWS_COGNITO_ABSENCE_COMMAND = (
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


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[tuple[str, ...], int], CommandResult]


def run_command(argv: tuple[str, ...], timeout_seconds: int = 20) -> CommandResult:
    """Run one allowlisted executable without a shell or inherited secret variables."""

    if not _command_allowed(argv) or timeout_seconds <= 0:
        raise ValueError("wave0_command_not_allowed")

    environment = {key: os.environ[key] for key in _ALLOWED_ENVIRONMENT if key in os.environ}
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", "wave0_command_timeout")
    except OSError:
        return CommandResult(127, "", "wave0_command_missing")

    output_limit = (
        _MAX_WORKFLOW_SOURCE_CHARS
        if len(argv) == 5
        and argv[:2] == ("gh", "api")
        and argv[3:] == ("--jq", ".content")
        and _GH_SOURCE_ENDPOINT_RE.fullmatch(argv[2]) is not None
        else _MAX_OUTPUT_CHARS
    )
    return CommandResult(
        completed.returncode,
        _redact_command_output(completed.stdout, output_limit),
        _redact_command_output(completed.stderr, output_limit),
    )


def _command_allowed(argv: tuple[str, ...]) -> bool:
    if (
        not argv
        or argv[0] not in _ALLOWED_PROGRAMS
        or any(not isinstance(argument, str) or not argument for argument in argv)
    ):
        return False
    if argv == ("aws", "--version"):
        return True
    if argv[0] == "aws":
        return argv in _AWS_READ_COMMANDS or argv == _AWS_COGNITO_ABSENCE_COMMAND
    if argv[0] != "gh":
        return argv in _LOCAL_VERSION_COMMANDS
    if argv[:3] == ("gh", "run", "download"):
        return _gh_download_allowed(argv)
    if len(argv) != 5 or argv[:2] != ("gh", "api"):
        return False
    endpoint = argv[2]
    if argv[3:] == ("--jq", _GH_RUN_JQ):
        return _GH_RUN_ENDPOINT_RE.fullmatch(endpoint) is not None
    if argv[3:] == ("--jq", _GH_WORKFLOW_JQ):
        return _GH_WORKFLOW_ENDPOINT_RE.fullmatch(endpoint) is not None
    if argv[3:] == ("--jq", _GH_JOBS_JQ):
        return _GH_JOBS_ENDPOINT_RE.fullmatch(endpoint) is not None
    if argv[3:] == ("--jq", _GH_ARTIFACTS_JQ):
        return _GH_ARTIFACTS_ENDPOINT_RE.fullmatch(endpoint) is not None
    return (
        argv[3:] == ("--jq", ".content")
        and _GH_SOURCE_ENDPOINT_RE.fullmatch(endpoint) is not None
    )


def _gh_download_allowed(argv: tuple[str, ...]) -> bool:
    if (
        len(argv) != 10
        or not argv[3].isdigit()
        or argv[3].startswith("0")
        or argv[4:6] != ("--repo", "natthaphonchop2-creator/mercury-tools")
        or argv[6] != "--name"
        or argv[8] != "--dir"
    ):
        return False
    destination = PurePath(argv[9])
    if not destination.is_absolute() or ".." in destination.parts:
        return False
    if len(destination.parts) < 6 or destination.parts[-5:-2] != (
        ".artifacts",
        "aws",
        "wave0",
    ):
        return False
    environment = destination.parts[-1]
    directory_match = _GH_DOWNLOAD_DIRECTORY_RE.fullmatch(destination.parts[-2])
    return bool(
        environment in {"nonprod", "production"}
        and argv[7] == f"mercury-wave0-oidc-account-proof-{environment}"
        and directory_match is not None
        and directory_match.group("run_id") == argv[3]
    )


def _redact_command_output(value: str, max_chars: int = _MAX_OUTPUT_CHARS) -> str:
    redacted = _AWS_ACCESS_KEY_ASSIGNMENT_RE.sub(
        "[REDACTED_AWS_ACCESS_KEY_ASSIGNMENT]", value
    )
    redacted = _AWS_SECRET_KEY_ASSIGNMENT_RE.sub(
        "[REDACTED_AWS_SECRET_KEY_ASSIGNMENT]", redacted
    )
    redacted = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY_ID]", redacted)
    return redact_text(redacted)[:max_chars]
