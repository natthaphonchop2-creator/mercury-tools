"""Bounded, redacted subprocess execution for AWS Wave 0 readiness checks."""

from __future__ import annotations

import os
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


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[tuple[str, ...], int], CommandResult]


def run_command(argv: tuple[str, ...], timeout_seconds: int = 20) -> CommandResult:
    """Run one allowlisted executable without a shell or inherited secret variables."""

    if (
        not argv
        or argv[0] not in _ALLOWED_PROGRAMS
        or any(not isinstance(argument, str) or not argument for argument in argv)
        or timeout_seconds <= 0
    ):
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
        redact_text(completed.stdout[:_MAX_OUTPUT_CHARS]),
        redact_text(completed.stderr[:_MAX_OUTPUT_CHARS]),
    )
