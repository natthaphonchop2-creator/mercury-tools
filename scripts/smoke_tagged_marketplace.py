#!/usr/bin/env python3
"""Smoke-test the hosted Mercury marketplace plugin from an immutable Git tag."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mercury_tools.mcp.contracts import HOSTED_MCP_URL, HOSTED_TOOL_NAMES

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$"
)
_COMMAND_TIMEOUT_SECONDS = 600
_MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
EXPECTED_HOSTED_MCP_URL = HOSTED_MCP_URL
EXPECTED_HOSTED_TOOLS = HOSTED_TOOL_NAMES
_EXPECTED_INSTALLED_HOSTED_TRANSPORT = {
    "type": "streamable_http",
    "url": EXPECTED_HOSTED_MCP_URL,
    "bearer_token_env_var": None,
    "http_headers": None,
    "env_http_headers": None,
}


class TaggedMarketplaceError(RuntimeError):
    """A bounded tagged-marketplace release check failed."""


@dataclass(frozen=True)
class TaggedSmokePlan:
    repo: str
    tag: str
    expected_hosted_tools: int
    hosted_mcp_url: str
    codex_home: Path
    environment: dict[str, str]
    commands: tuple[tuple[str, ...], ...]


def build_tagged_smoke_plan(
    *,
    repo: str,
    tag: str,
    expected_hosted_tools: int,
    codex_home: Path,
) -> TaggedSmokePlan:
    """Build an immutable, isolated hosted-plugin smoke plan."""

    if not isinstance(repo, str) or _REPOSITORY_PATTERN.fullmatch(repo) is None:
        raise TaggedMarketplaceError("repository_invalid")
    if not isinstance(tag, str) or _TAG_PATTERN.fullmatch(tag) is None:
        raise TaggedMarketplaceError("tag_invalid")
    if type(expected_hosted_tools) is not int or expected_hosted_tools <= 0:
        raise TaggedMarketplaceError("expected_hosted_tools_invalid")
    if expected_hosted_tools != len(EXPECTED_HOSTED_TOOLS):
        raise TaggedMarketplaceError("hosted_mcp_tool_count_mismatch")

    environment = {
        "CODEX_HOME": str(codex_home),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    commands = (
        (
            "codex",
            "plugin",
            "marketplace",
            "add",
            repo,
            "--ref",
            tag,
            "--sparse",
            ".agents/plugins",
            "--sparse",
            "plugins/mercury-finance",
        ),
        ("codex", "plugin", "add", "mercury-finance@mercury-tools"),
        ("codex", "mcp", "list", "--json"),
    )
    return TaggedSmokePlan(
        repo=repo,
        tag=tag,
        expected_hosted_tools=expected_hosted_tools,
        hosted_mcp_url=EXPECTED_HOSTED_MCP_URL,
        codex_home=codex_home,
        environment=environment,
        commands=commands,
    )


def _prepare_codex_home(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
    except FileExistsError:
        try:
            if any(path.iterdir()):
                raise TaggedMarketplaceError("codex_home_not_empty") from None
        except OSError as exc:
            raise TaggedMarketplaceError("codex_home_invalid") from exc
        path.chmod(0o700)
    except OSError as exc:
        raise TaggedMarketplaceError("codex_home_invalid") from exc


def _runtime_environment(
    plan: TaggedSmokePlan,
    base_environment: Mapping[str, str] | None,
) -> dict[str, str]:
    environment = dict(os.environ if base_environment is None else base_environment)
    environment.update(plan.environment)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_HTTP_TIMEOUT"] = "120"
    return environment


def _run_command(
    command: Sequence[str],
    *,
    environment: Mapping[str, str],
    phase: str,
) -> bytes:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            env=dict(environment),
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise TaggedMarketplaceError(f"{phase}_unavailable") from exc
    output_size = len(result.stdout) + len(result.stderr)
    if output_size > _MAX_COMMAND_OUTPUT_BYTES:
        raise TaggedMarketplaceError(f"{phase}_output_too_large")
    if result.returncode != 0:
        raise TaggedMarketplaceError(f"{phase}_failed")
    return result.stdout


def _verify_mcp_listing(output: bytes) -> None:
    try:
        payload = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaggedMarketplaceError("mcp_list_invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise TaggedMarketplaceError("mcp_list_invalid")

    entry = payload[0]
    actual = {
        "name": entry.get("name"),
        "enabled": entry.get("enabled"),
        "disabled_reason": entry.get("disabled_reason"),
        "transport": entry.get("transport"),
    }
    expected = {
        "name": "mercury-finance",
        "enabled": True,
        "disabled_reason": None,
        "transport": _EXPECTED_INSTALLED_HOSTED_TRANSPORT,
    }
    if actual != expected:
        raise TaggedMarketplaceError("mcp_server_surface_mismatch")


async def _verify_tagged_hosted_endpoint(
    *,
    endpoint: str,
    expected_tools: frozenset[str],
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
    streamable_client_factory: Callable[..., Any] = streamable_http_client,
    session_factory: Callable[..., Any] = ClientSession,
) -> None:
    """Verify the immutable hosted MCP surface with a bounded HTTP client."""

    if endpoint != EXPECTED_HOSTED_MCP_URL:
        raise TaggedMarketplaceError("hosted_mcp_endpoint_mismatch")
    if expected_tools != EXPECTED_HOSTED_TOOLS:
        raise TaggedMarketplaceError("hosted_mcp_tool_surface_mismatch")
    try:
        async with (
            http_client_factory(timeout=30.0, follow_redirects=False) as client,
            streamable_client_factory(endpoint, http_client=client) as streams,
        ):
            read_stream, write_stream, _session_id = streams
            async with session_factory(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
    except Exception as exc:
        raise TaggedMarketplaceError("hosted_mcp_start_failed") from exc

    try:
        server_name = initialized.serverInfo.name
        tools = listed.tools
        names = {tool.name for tool in tools}
    except (AttributeError, TypeError) as exc:
        raise TaggedMarketplaceError("hosted_mcp_protocol_invalid") from exc
    if server_name != "Mercury Tools":
        raise TaggedMarketplaceError("hosted_mcp_server_name_mismatch")
    if len(tools) != len(expected_tools) or names != expected_tools:
        raise TaggedMarketplaceError("hosted_mcp_tool_surface_mismatch")


def run_tagged_smoke(
    plan: TaggedSmokePlan,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> None:
    """Execute a tagged hosted-plugin smoke plan without exposing raw output."""

    if plan.expected_hosted_tools != len(EXPECTED_HOSTED_TOOLS):
        raise TaggedMarketplaceError("hosted_mcp_tool_count_mismatch")
    _prepare_codex_home(plan.codex_home)
    environment = _runtime_environment(plan, base_environment)
    phases = ("marketplace_add", "plugin_add", "mcp_list")
    listing = b""
    for command, phase in zip(plan.commands, phases, strict=True):
        listing = _run_command(command, environment=environment, phase=phase)
    _verify_mcp_listing(listing)
    try:
        asyncio.run(
            asyncio.wait_for(
                _verify_tagged_hosted_endpoint(
                    endpoint=plan.hosted_mcp_url,
                    expected_tools=EXPECTED_HOSTED_TOOLS,
                ),
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        )
    except TimeoutError as exc:
        raise TaggedMarketplaceError("hosted_mcp_timeout") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--expected-hosted-tools", required=True, type=int)
    parser.add_argument("--codex-home", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.codex_home is not None:
        plan = build_tagged_smoke_plan(
            repo=args.repo,
            tag=args.tag,
            expected_hosted_tools=args.expected_hosted_tools,
            codex_home=args.codex_home,
        )
        run_tagged_smoke(plan)
    else:
        with tempfile.TemporaryDirectory(prefix="mercury-codex-home-") as temporary:
            codex_home = Path(temporary) / "home"
            plan = build_tagged_smoke_plan(
                repo=args.repo,
                tag=args.tag,
                expected_hosted_tools=args.expected_hosted_tools,
                codex_home=codex_home,
            )
            run_tagged_smoke(plan)
    print("tagged marketplace smoke passed (one hosted HTTP MCP, 24 hosted tools)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TaggedMarketplaceError as error:
        print(f"tagged marketplace smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
