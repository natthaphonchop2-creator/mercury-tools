"""Bounded, redacted subprocess execution for AWS Wave 0 readiness checks."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from mercury_tools.safety.redaction import redact_text

_ALLOWED_PROGRAMS = frozenset({"aws", "node", "npm", "npx", "uv", "gh"})
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
_GH_SOURCE_ENDPOINT_RE = re.compile(
    rf"^{_GH_REPOSITORY_PATH}/contents/\.github/workflows/"
    r"aws-wave0-oidc-smoke\.yml\?ref=[a-f0-9]{40}$"
)
_GH_RUN_JQ = (
    "{id,html_url,event,status,conclusion,head_sha,workflow_id,path,"
    "repository_full_name:.repository.full_name}"
)
_GH_WORKFLOW_JQ = "{id,path}"
_GH_JOBS_JQ = "{total_count,jobs:[.jobs[]|{id,name,status,conclusion}]}"


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

    return CommandResult(
        completed.returncode,
        _redact_command_output(completed.stdout),
        _redact_command_output(completed.stderr),
    )


def _command_allowed(argv: tuple[str, ...]) -> bool:
    if (
        not argv
        or argv[0] not in _ALLOWED_PROGRAMS
        or any(not isinstance(argument, str) or not argument for argument in argv)
    ):
        return False
    if argv[0] != "gh":
        return True
    if len(argv) != 5 or argv[:2] != ("gh", "api"):
        return False
    endpoint = argv[2]
    if argv[3:] == ("--jq", _GH_RUN_JQ):
        return _GH_RUN_ENDPOINT_RE.fullmatch(endpoint) is not None
    if argv[3:] == ("--jq", _GH_WORKFLOW_JQ):
        return _GH_WORKFLOW_ENDPOINT_RE.fullmatch(endpoint) is not None
    if argv[3:] == ("--jq", _GH_JOBS_JQ):
        return _GH_JOBS_ENDPOINT_RE.fullmatch(endpoint) is not None
    return (
        argv[3:] == ("--jq", ".content")
        and _GH_SOURCE_ENDPOINT_RE.fullmatch(endpoint) is not None
    )


def _redact_command_output(value: str) -> str:
    redacted = _AWS_ACCESS_KEY_ASSIGNMENT_RE.sub(
        "[REDACTED_AWS_ACCESS_KEY_ASSIGNMENT]", value
    )
    redacted = _AWS_SECRET_KEY_ASSIGNMENT_RE.sub(
        "[REDACTED_AWS_SECRET_KEY_ASSIGNMENT]", redacted
    )
    redacted = _AWS_ACCESS_KEY_RE.sub("[REDACTED_AWS_ACCESS_KEY_ID]", redacted)
    return redact_text(redacted)[:_MAX_OUTPUT_CHARS]
