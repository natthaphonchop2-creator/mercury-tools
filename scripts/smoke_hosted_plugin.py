#!/usr/bin/env python3
"""Smoke-test the hosted MCP and an isolated Codex plugin installation."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mercury_tools.mcp.contracts import HOSTED_MCP_URL, HOSTED_TOOL_NAMES


class SmokeError(RuntimeError):
    """A hosted MCP or plugin installation check failed."""


def _run(command: list[str], *, environment: dict[str, str]) -> bytes:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            env=environment,
            timeout=300,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SmokeError(f"command unavailable: {command[0]}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace")[-800:]
        raise SmokeError(f"command failed: {' '.join(command)}\n{detail}")
    return result.stdout


def _smoke_plugin_install(*, source: str, ref: str | None) -> None:
    with tempfile.TemporaryDirectory(prefix="mercury-plugin-smoke-") as temporary:
        codex_home = Path(temporary) / "codex-home"
        codex_home.mkdir(mode=0o700)
        environment = dict(os.environ)
        environment.update(
            {
                "CODEX_HOME": str(codex_home),
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        command = ["codex", "plugin", "marketplace", "add", source]
        if ref:
            command.extend(["--ref", ref])
        _run(command, environment=environment)
        _run(
            ["codex", "plugin", "add", "mercury-finance@mercury-tools"],
            environment=environment,
        )
        listing = json.loads(
            _run(["codex", "mcp", "list", "--json"], environment=environment)
        )
        matches = [item for item in listing if item.get("name") == "mercury-finance"]
        if len(matches) != 1:
            raise SmokeError("isolated install did not register exactly one Mercury MCP")
        entry = matches[0]
        transport = entry.get("transport") or {}
        if not entry.get("enabled") or transport.get("url") != HOSTED_MCP_URL:
            raise SmokeError("installed Mercury MCP does not match the hosted endpoint")


async def _smoke_remote() -> None:
    try:
        async with (
            httpx.AsyncClient(timeout=45, follow_redirects=False) as client,
            streamable_http_client(HOSTED_MCP_URL, http_client=client) as streams,
        ):
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                connector_result = await session.call_tool("list_connectors", arguments={})
    except Exception as exc:
        raise SmokeError("hosted MCP connection failed") from exc

    if initialized.serverInfo.name != "Mercury Tools":
        raise SmokeError("unexpected hosted MCP server name")
    names = {tool.name for tool in listed.tools}
    if names != HOSTED_TOOL_NAMES:
        missing = sorted(HOSTED_TOOL_NAMES - names)
        extra = sorted(names - HOSTED_TOOL_NAMES)
        raise SmokeError(f"hosted tool mismatch; missing={missing}, extra={extra}")
    if connector_result.isError or not connector_result.content:
        raise SmokeError("safe list_connectors call failed")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-only", action="store_true")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    return parser


def main() -> int:
    args = _parser().parse_args()
    asyncio.run(_smoke_remote())
    if not args.remote_only:
        if bool(args.repo_root) == bool(args.repo):
            raise SmokeError("choose exactly one of --repo-root or --repo")
        source = str(args.repo_root.resolve()) if args.repo_root else args.repo
        _smoke_plugin_install(source=source, ref=args.ref)
    print(f"Mercury smoke passed: one hosted MCP, {len(HOSTED_TOOL_NAMES)} tools")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmokeError, json.JSONDecodeError) as exc:
        print(f"Mercury smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
