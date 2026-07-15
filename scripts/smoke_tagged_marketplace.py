#!/usr/bin/env python3
"""Smoke-test the Codex marketplace plugin from an immutable Git tag."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*)?$"
)
_COMMAND_TIMEOUT_SECONDS = 600
_MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
_EXPECTED_LOCAL_TOOLS = frozenset(
    {
        "search_knowledge",
        "retrieve_context_pack",
        "get_document",
        "connector_status",
        "run_accounting_skill",
        "run_mercury_flow",
        "list_workspace_flows",
        "save_workspace_flow",
        "run_workspace_flow",
        "search_erp_actions",
        "get_erp_action_schema",
        "run_erp_read",
        "preview_erp_write",
        "confirm_erp_write",
        "execute_erp_write",
        "get_erp_request_status",
        "import_erp_spec",
        "list_connector_drivers",
        "credential_status",
    }
)


class TaggedMarketplaceError(RuntimeError):
    """A bounded tagged-marketplace release check failed."""


@dataclass(frozen=True)
class TaggedSmokePlan:
    repo: str
    tag: str
    launcher_source: str
    expected_tools: int
    codex_home: Path
    environment: dict[str, str]
    commands: tuple[tuple[str, ...], ...]


def build_tagged_smoke_plan(
    *,
    repo: str,
    tag: str,
    expected_tools: int,
    codex_home: Path,
    launcher_repo: str | None = None,
    launcher_ref: str | None = None,
) -> TaggedSmokePlan:
    """Build an immutable, isolated marketplace smoke plan."""

    if not isinstance(repo, str) or _REPOSITORY_PATTERN.fullmatch(repo) is None:
        raise TaggedMarketplaceError("repository_invalid")
    if not isinstance(tag, str) or _TAG_PATTERN.fullmatch(tag) is None:
        raise TaggedMarketplaceError("tag_invalid")
    launcher_repo = repo if launcher_repo is None else launcher_repo
    launcher_ref = tag if launcher_ref is None else launcher_ref
    if not isinstance(launcher_repo, str) or _REPOSITORY_PATTERN.fullmatch(launcher_repo) is None:
        raise TaggedMarketplaceError("launcher_repository_invalid")
    if not isinstance(launcher_ref, str) or _TAG_PATTERN.fullmatch(launcher_ref) is None:
        raise TaggedMarketplaceError("launcher_ref_invalid")
    if type(expected_tools) is not int or expected_tools <= 0:
        raise TaggedMarketplaceError("expected_tools_invalid")

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
        launcher_source=f"git+https://github.com/{launcher_repo}.git@{launcher_ref}",
        expected_tools=expected_tools,
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


def _mcp_server_names(payload: Any) -> tuple[str, ...]:
    if isinstance(payload, list):
        names = []
        for item in payload:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise TaggedMarketplaceError("mcp_list_invalid")
            names.append(item["name"])
        return tuple(names)
    if isinstance(payload, dict) and set(payload) == {"servers"}:
        return _mcp_server_names(payload["servers"])
    if isinstance(payload, dict) and set(payload) == {"mcpServers"}:
        servers = payload["mcpServers"]
        if not isinstance(servers, dict) or not all(
            isinstance(name, str) and isinstance(value, dict)
            for name, value in servers.items()
        ):
            raise TaggedMarketplaceError("mcp_list_invalid")
        return tuple(servers)
    raise TaggedMarketplaceError("mcp_list_invalid")


def _verify_mcp_listing(output: bytes) -> None:
    try:
        payload = json.loads(output)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaggedMarketplaceError("mcp_list_invalid") from exc
    if _mcp_server_names(payload) != ("mercury-finance",):
        raise TaggedMarketplaceError("mcp_server_surface_mismatch")


async def _verify_tagged_launcher(
    plan: TaggedSmokePlan,
    environment: Mapping[str, str],
) -> None:
    if plan.expected_tools != len(_EXPECTED_LOCAL_TOOLS):
        raise TaggedMarketplaceError("local_mcp_tool_count_mismatch")
    with tempfile.TemporaryDirectory(prefix="mercury-tagged-mcp-") as temporary:
        parameters = StdioServerParameters(
            command="uvx",
            args=[
                "--from",
                plan.launcher_source,
                "mercury",
                "mcp",
                "serve-local",
            ],
            cwd=Path(temporary),
            env=dict(environment),
        )
        try:
            async with (
                stdio_client(parameters) as (read_stream, write_stream),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=120),
                ) as session,
            ):
                initialized = await session.initialize()
                listed = await session.list_tools()
        except Exception as exc:
            raise TaggedMarketplaceError("tagged_mcp_start_failed") from exc

    names = {tool.name for tool in listed.tools}
    if initialized.serverInfo.name != "Mercury Finance":
        raise TaggedMarketplaceError("local_mcp_server_name_mismatch")
    if len(listed.tools) != plan.expected_tools or names != _EXPECTED_LOCAL_TOOLS:
        raise TaggedMarketplaceError("local_mcp_tool_surface_mismatch")


def run_tagged_smoke(
    plan: TaggedSmokePlan,
    *,
    base_environment: Mapping[str, str] | None = None,
) -> None:
    """Execute a tagged marketplace smoke plan without exposing raw output."""

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
                _verify_tagged_launcher(plan, environment),
                timeout=_COMMAND_TIMEOUT_SECONDS,
            )
        )
    except TimeoutError as exc:
        raise TaggedMarketplaceError("tagged_mcp_timeout") from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--launcher-repo")
    parser.add_argument("--launcher-ref")
    parser.add_argument("--expected-tools", required=True, type=int)
    parser.add_argument("--codex-home", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.codex_home is not None:
        plan = build_tagged_smoke_plan(
            repo=args.repo,
            tag=args.tag,
            expected_tools=args.expected_tools,
            codex_home=args.codex_home,
            launcher_repo=args.launcher_repo,
            launcher_ref=args.launcher_ref,
        )
        run_tagged_smoke(plan)
    else:
        with tempfile.TemporaryDirectory(prefix="mercury-codex-home-") as temporary:
            codex_home = Path(temporary) / "home"
            plan = build_tagged_smoke_plan(
                repo=args.repo,
                tag=args.tag,
                expected_tools=args.expected_tools,
                codex_home=codex_home,
                launcher_repo=args.launcher_repo,
                launcher_ref=args.launcher_ref,
            )
            run_tagged_smoke(plan)
    print("tagged marketplace smoke passed (one local server, 19 tools)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TaggedMarketplaceError as error:
        print(f"tagged marketplace smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
