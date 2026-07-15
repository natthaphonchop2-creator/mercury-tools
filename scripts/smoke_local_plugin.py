#!/usr/bin/env python3
"""Smoke-test the packaged local Mercury Finance stdio MCP without a Git tag."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import tomllib
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = {
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


def _manifest_server() -> dict[str, Any]:
    manifest_path = ROOT / "plugins" / "mercury-finance" / ".mcp.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {"mercury-finance"}:
        raise RuntimeError("expected_exactly_one_mercury_finance_mcp_server")
    server = servers["mercury-finance"]
    if not isinstance(server, dict):
        raise RuntimeError("mercury_finance_mcp_server_invalid")
    return server


def _build_local_wheel() -> Path:
    result = subprocess.run(
        ["uv", "build", "--wheel"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stdout + result.stderr)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    wheels = sorted((ROOT / "dist").glob(f"mercury_tools-{version}-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("expected_one_project_version_wheel")
    return wheels[0]


def _clean_environment(cache_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_CACHE_DIR"] = str(cache_dir)
    environment["UV_HTTP_TIMEOUT"] = "120"
    return environment


async def _smoke(wheel: Path, environment: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory(prefix="mercury-local-plugin-") as temporary:
        cwd = Path(temporary)
        parameters = StdioServerParameters(
            command="uvx",
            args=["--no-cache", "--from", str(wheel), "mercury", "mcp", "serve-local"],
            cwd=cwd,
            env=environment,
        )
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

    names = {tool.name for tool in listed.tools}
    if initialized.serverInfo.name != "Mercury Finance":
        raise RuntimeError("unexpected_local_mcp_server_name")
    if names != EXPECTED_TOOLS or len(listed.tools) != 19:
        raise RuntimeError("local_mcp_tool_surface_mismatch")


def main() -> int:
    server = _manifest_server()
    if server.get("command") != "uvx":
        raise RuntimeError("local_mcp_launcher_must_use_uvx")

    wheel = _build_local_wheel()
    with tempfile.TemporaryDirectory(prefix="mercury-local-plugin-cache-") as temporary:
        asyncio.run(_smoke(wheel, _clean_environment(Path(temporary))))
    print("local packaged Mercury Finance MCP smoke passed (one server, 19 tools)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"local packaged Mercury Finance MCP smoke failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
