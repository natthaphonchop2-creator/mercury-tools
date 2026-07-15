"""Wheel-only smoke coverage for the local Mercury MCP release."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
import tomllib
from datetime import timedelta
from email.parser import Parser
from pathlib import Path
from typing import Any
from zipfile import ZipFile

import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.local.repository import ensure_repository_state
from mercury_tools.mcp.local_runtime import _checked_in_semantic_contracts
from mercury_tools.qualification.semantics import load_actions, load_semantic_contracts

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
    started = time.monotonic()
    result = subprocess.run(
        ["uv", "build"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    _print_phase_timing("build-wheel", started)
    assert result.returncode == 0, result.stdout + result.stderr
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = project["project"]["version"]
    wheels = sorted((ROOT / "dist").glob(f"mercury_tools-{version}-*.whl"))
    assert len(wheels) == 1
    return wheels[0]


def _print_phase_timing(phase: str, started: float) -> None:
    elapsed = min(max(time.monotonic() - started, 0.0), 600.0)
    print(f"clean-install phase={phase} seconds={elapsed:.3f}")


def _clean_uvx_environment(cache_dir: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment.pop("VIRTUAL_ENV", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["UV_CACHE_DIR"] = str(cache_dir)
    environment["UV_HTTP_TIMEOUT"] = "120"
    return environment


async def _roots_callback(root: Path) -> types.ListRootsResult:
    return types.ListRootsResult(roots=[types.Root(uri=root.as_uri(), name="repository")])


async def _call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    result = await session.call_tool(name, arguments)
    assert not result.isError, result
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    text = next(
        (
            item.text
            for item in result.content
            if isinstance(item, types.TextContent)
        ),
        None,
    )
    assert text is not None
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


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


def test_wheel_contains_exact_254_contract_semantic_sidecar_mirror() -> None:
    wheel = _build_wheel()
    identities: set[tuple[str, str]] = set()

    with ZipFile(wheel) as archive:
        for connector_id in ("flowaccount", "peak"):
            authoritative = ROOT / "catalog" / "global" / connector_id / "semantic-contracts.json"
            member = (
                "mercury_tools/catalog/global/"
                f"{connector_id}/semantic-contracts.json"
            )
            mirrored = archive.read(member)
            assert mirrored == authoritative.read_bytes()
            payload = json.loads(mirrored)
            for row in payload["contracts"]:
                identity = (row["action_id"], row["version_id"])
                assert identity not in identities
                identities.add(identity)

    assert len(identities) == 254


def test_source_runtime_semantics_equal_all_254_authoritative_contracts() -> None:
    authoritative = {}
    for connector_id in ("flowaccount", "peak"):
        actions = load_actions(ROOT / f"catalog/global/{connector_id}/actions.json")
        authoritative.update(
            load_semantic_contracts(
                ROOT / f"catalog/global/{connector_id}/semantic-contracts.json",
                actions,
            )
        )

    packaged = dict(_checked_in_semantic_contracts())

    assert len(authoritative) == len(packaged) == 254
    assert packaged == authoritative


@pytest.mark.asyncio
async def test_clean_wheel_uvx_cli_and_stdio_expose_all_local_tools(tmp_path: Path) -> None:
    wheel = _build_wheel()
    empty_cwd = tmp_path / "empty-cwd"
    empty_cwd.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    context = ensure_repository_state(repository)
    actions = load_actions(ROOT / "catalog/global/flowaccount/actions.json")
    contracts = load_semantic_contracts(
        ROOT / "catalog/global/flowaccount/semantic-contracts.json",
        actions,
    )
    action = next(
        candidate
        for candidate in actions
        if contracts[(candidate.action_id, candidate.version_id)].accounting_uses
    )
    semantic = contracts[(action.action_id, action.version_id)]
    CatalogCache(context).replace_global([action], etag='"clean-wheel-fixture"')
    configured_cache = os.environ.get("MERCURY_CLEAN_INSTALL_UV_CACHE_DIR", "").strip()
    clean_cache = Path(configured_cache) if configured_cache else tmp_path / "uv-cache"
    if not configured_cache:
        assert not clean_cache.exists()
    environment = _clean_uvx_environment(clean_cache)
    environment["MERCURY_CLOUD_BASE_URL"] = "http://127.0.0.1:9"
    assert "PYTHONPATH" not in environment

    started = time.monotonic()
    help_result = subprocess.run(
        ["uvx", "--from", str(wheel), "mercury", "--help"],
        cwd=empty_cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=600,
    )
    _print_phase_timing("uvx-help", started)
    assert help_result.returncode == 0, help_result.stdout + help_result.stderr
    assert "usage: mercury-tools" in help_result.stdout
    assert "mcp" in help_result.stdout

    parameters = StdioServerParameters(
        command="uvx",
        args=["--from", str(wheel), "mercury", "mcp", "serve-local"],
        cwd=empty_cwd,
        env=environment,
    )
    started = time.monotonic()
    async with asyncio.timeout(600):
        async with (
            stdio_client(parameters) as (read_stream, write_stream),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=120),
                list_roots_callback=lambda _context: _roots_callback(repository),
            ) as session,
        ):
            initialized = await session.initialize()
            listed = await session.list_tools()
            search = await _call_tool(
                session,
                "search_erp_actions",
                {
                    "query": action.action_id,
                    "connector": action.connector_id,
                    "environment": "sandbox",
                },
            )
            schema = await _call_tool(
                session,
                "get_erp_action_schema",
                {
                    "action_id": action.action_id,
                    "version": action.version_id,
                    "environment": "sandbox",
                },
            )
    _print_phase_timing("stdio-mcp", started)

    assert initialized.serverInfo.name == "Mercury Finance"
    assert {tool.name for tool in listed.tools} == EXPECTED_LOCAL_TOOLS
    assert len(listed.tools) == 19
    assert search["candidates"][0]["accounting_uses"] == list(semantic.accounting_uses)
    assert schema["status"] == "ok"
    assert schema["action"]["semantic_contract"] == semantic.model_dump(mode="json")
    assert schema["action"]["selected_evidence"] is None
    assert schema["action"]["validation"]["blocking_conditions"] == [
        "validation_unavailable"
    ]
