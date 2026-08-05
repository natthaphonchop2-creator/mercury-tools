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
from urllib.parse import urlsplit, urlunsplit

import httpx
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mercury_tools.mcp.contracts import (
    HOSTED_MCP_URL,
    HOSTED_TOOL_NAMES,
    V1_HOSTED_TOOL_NAMES,
)


class SmokeError(RuntimeError):
    """A hosted MCP or plugin installation check failed."""


def _access_token_from_environment() -> str | None:
    token = os.environ.get("MERCURY_SMOKE_ACCESS_TOKEN", "").strip()
    return token or None


def _validate_health_payload(payload: object) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise SmokeError("hosted health check is not healthy")
    v1_enabled = payload.get("v1_enabled") is True
    if v1_enabled:
        if payload.get("http_auth_required") is not True:
            raise SmokeError("hosted V1 does not require HTTP authentication")
        if payload.get("legacy_http_api") != "disabled":
            raise SmokeError("hosted V1 still exposes the legacy HTTP API")
    return v1_enabled


def _service_url() -> str:
    parsed = urlsplit(HOSTED_MCP_URL)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


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
        listing = json.loads(_run(["codex", "mcp", "list", "--json"], environment=environment))
        matches = [item for item in listing if item.get("name") == "mercury-finance"]
        if len(matches) != 1:
            raise SmokeError("isolated install did not register exactly one Mercury MCP")
        entry = matches[0]
        transport = entry.get("transport") or {}
        if not entry.get("enabled") or transport.get("url") != HOSTED_MCP_URL:
            raise SmokeError("installed Mercury MCP does not match the hosted endpoint")


async def _smoke_mcp(*, access_token: str | None, v1_enabled: bool) -> int:
    headers = {"Authorization": f"Bearer {access_token}"} if access_token else None
    try:
        async with (
            httpx.AsyncClient(
                timeout=45,
                follow_redirects=False,
                headers=headers,
            ) as client,
            streamable_http_client(HOSTED_MCP_URL, http_client=client) as streams,
        ):
            read_stream, write_stream, _ = streams
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                safe_tool = "get_mercury_context" if v1_enabled else "list_connectors"
                safe_result = await session.call_tool(safe_tool, arguments={})
    except Exception as exc:
        raise SmokeError("hosted MCP connection failed") from exc

    if initialized.serverInfo.name != "Mercury Tools":
        raise SmokeError("unexpected hosted MCP server name")
    names = {tool.name for tool in listed.tools}
    expected = V1_HOSTED_TOOL_NAMES if v1_enabled else HOSTED_TOOL_NAMES
    missing = sorted(expected - names)
    if missing:
        raise SmokeError(f"hosted tool mismatch; missing={missing}")
    if not v1_enabled and names != expected:
        extra = sorted(names - expected)
        raise SmokeError(f"hosted tool mismatch; extra={extra}")
    if v1_enabled:
        unexpected = sorted(name for name in names - expected if not name.startswith("mercury_"))
        if unexpected:
            raise SmokeError(f"unexpected generated V1 tools: {unexpected}")
    if safe_result.isError or not safe_result.content:
        raise SmokeError(f"safe {safe_tool} call failed")
    return len(names)


async def _smoke_remote() -> dict[str, object]:
    access_token = _access_token_from_environment()
    try:
        async with httpx.AsyncClient(timeout=45, follow_redirects=False) as client:
            health_response = await client.get(f"{_service_url()}/healthz")
            health_response.raise_for_status()
            v1_enabled = _validate_health_payload(health_response.json())
            if v1_enabled:
                metadata_response = await client.get(
                    f"{_service_url()}/.well-known/oauth-protected-resource/mcp"
                )
                metadata_response.raise_for_status()
                metadata = metadata_response.json()
                if (
                    not isinstance(metadata, dict)
                    or metadata.get("resource") != HOSTED_MCP_URL
                    or not metadata.get("authorization_servers")
                ):
                    raise SmokeError("hosted OAuth protected-resource metadata is invalid")
                unauthenticated = await client.get(HOSTED_MCP_URL)
                if unauthenticated.status_code != 401:
                    raise SmokeError("hosted V1 MCP accepted an unauthenticated request")
    except SmokeError:
        raise
    except Exception as exc:
        raise SmokeError("hosted public readiness probe failed") from exc

    if v1_enabled and access_token is None:
        return {"v1_enabled": True, "authenticated": False, "tool_count": None}

    tool_count = await _smoke_mcp(
        access_token=access_token,
        v1_enabled=v1_enabled,
    )
    return {
        "v1_enabled": v1_enabled,
        "authenticated": access_token is not None,
        "tool_count": tool_count,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-only", action="store_true")
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--repo")
    parser.add_argument("--ref")
    return parser


def main() -> int:
    args = _parser().parse_args()
    remote = asyncio.run(_smoke_remote())
    if not args.remote_only:
        if bool(args.repo_root) == bool(args.repo):
            raise SmokeError("choose exactly one of --repo-root or --repo")
        source = str(args.repo_root.resolve()) if args.repo_root else args.repo
        _smoke_plugin_install(source=source, ref=args.ref)
    if remote["v1_enabled"] and not remote["authenticated"]:
        detail = "V1 public/OAuth boundary ready; authenticated tool smoke skipped"
    else:
        detail = f"{remote['tool_count']} tools"
    print(f"Mercury smoke passed: one hosted MCP, {detail}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmokeError, json.JSONDecodeError) as exc:
        print(f"Mercury smoke failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
