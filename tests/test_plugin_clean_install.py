"""Wheel-only smoke coverage for the local Mercury MCP release."""

from __future__ import annotations

import os
import subprocess
from datetime import timedelta
from email.parser import Parser
from pathlib import Path
from zipfile import ZipFile

import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_LOCAL_TOOLS = {
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


def _build_wheel() -> Path:
    result = subprocess.run(
        ["uv", "build"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    wheels = sorted((ROOT / "dist").glob("mercury_tools-0.2.0-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _clean_uvx_environment(cache_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_CACHE_DIR"] = str(cache_dir)
    return environment


def test_wheel_keeps_openai_as_an_optional_extra_only() -> None:
    wheel = _build_wheel()

    with ZipFile(wheel) as archive:
        metadata_path = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        metadata = Parser().parsestr(archive.read(metadata_path).decode("utf-8"))

    requires_dist = metadata.get_all("Requires-Dist", [])
    assert "openai==2.44.0; extra == \"openai\"" in requires_dist
    assert "openai==2.44.0" not in requires_dist
    assert "openai" in metadata.get_all("Provides-Extra", [])


@pytest.mark.asyncio
async def test_clean_wheel_uvx_cli_and_stdio_expose_all_local_tools(tmp_path: Path) -> None:
    wheel = _build_wheel()
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    environment = _clean_uvx_environment(tmp_path / "uv-cache")
    assert "PYTHONPATH" not in environment

    help_result = subprocess.run(
        ["uvx", "--no-cache", "--from", str(wheel), "mercury", "--help"],
        cwd=empty_cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "usage: mercury-tools" in help_result.stdout
    assert "mcp" in help_result.stdout

    parameters = StdioServerParameters(
        command="uvx",
        args=["--no-cache", "--from", str(wheel), "mercury", "mcp", "serve-local"],
        cwd=empty_cwd,
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

    assert initialized.serverInfo.name == "Mercury Finance"
    assert {tool.name for tool in listed.tools} == EXPECTED_LOCAL_TOOLS
    assert len(listed.tools) == 19
