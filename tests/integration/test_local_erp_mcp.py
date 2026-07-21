"""End-to-end coverage for the repository-local ERP MCP surface.

The acceptance path uses only in-process fake Cloud and ERP transports. Live
credential validation remains opt-in and invokes each connector's safe GET
probe only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.shared.memory import create_connected_server_and_client_session

import mercury_tools.mcp.local_runtime as local_runtime
from mercury_tools.cloud.client import CatalogFetchResult
from mercury_tools.drivers.models import CredentialField
from mercury_tools.execution.executor import ERPExecutor
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import configure_connector, ensure_repository_state
from mercury_tools.mcp.local_server import local_mcp
from mercury_tools.safety.network import ResolvedTarget


class _FakeCloud:
    """Offline catalog client: imported repository actions remain local."""

    def __init__(self, **_: Any) -> None:
        pass

    async def list_actions(self) -> CatalogFetchResult:
        return CatalogFetchResult(actions=(), source="fake-cloud")

    async def search_knowledge(self, *_: Any, **__: Any) -> tuple[dict[str, Any], ...]:
        return ()

    async def get_document(self, _: str) -> None:
        return None

    async def get_skill(self, _: str) -> None:
        return None

    async def aclose(self) -> None:
        return None


class _FakePeer:
    def get_extra_info(self, name: str) -> tuple[str, int] | None:
        return ("203.0.113.9", 443) if name == "server_addr" else None


class _FakeNetworkPolicy:
    """Keep fake ERP requests inside the in-memory transport."""

    @staticmethod
    def _target(url: str) -> ResolvedTarget:
        return ResolvedTarget(
            url=url,
            hostname="fake-erp.example.test",
            port=443,
            addresses=("203.0.113.9",),
        )

    def validate_base_url(self, url: str, **_: Any) -> ResolvedTarget:
        return self._target(url)

    def validate_request_url(self, url: str, **_: Any) -> ResolvedTarget:
        return self._target(url)


def _write_fake_spec(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"title": "Fake ERP", "version": "1.0.0"},
                "paths": {
                    "/company": {
                        "get": {
                            "operationId": "companyInfo",
                            "responses": {"200": {"description": "OK"}},
                        }
                    },
                    "/invoices": {
                        "post": {
                            "operationId": "createInvoice",
                            "requestBody": {
                                "required": True,
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "reference": {"type": "string"},
                                                "amount": {"type": "number"},
                                            },
                                            "required": ["reference", "amount"],
                                        }
                                    }
                                },
                            },
                            "responses": {"201": {"description": "Created"}},
                        }
                    },
                },
            }
        )
    )


def _configure_fake_repository(repo: Path) -> Path:
    context = ensure_repository_state(repo)
    configure_connector(
        context,
        connector_id="fake-erp",
        environment="production",
        driver_id="bearer",
        base_url="https://fake-erp.example.test/v1",
        auth_settings={},
    )
    CredentialStore(context).save(
        "fake-erp",
        "production",
        {"token": "acceptance-secret"},
        (CredentialField("token", secret=True, label="Bearer token"),),
    )
    spec_path = repo / "fake-erp-openapi.json"
    _write_fake_spec(spec_path)
    return spec_path


async def _roots_callback(_: Any, repo: Path) -> types.ListRootsResult:
    return types.ListRootsResult(roots=[types.Root(uri=repo.as_uri(), name="repository")])


async def _call_tool(
    client: ClientSession,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    result = await client.call_tool(name, dict(arguments))
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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_local_mcp_acceptance_uses_only_fake_cloud_and_fake_erp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repository"
    repo.mkdir()
    spec_path = _configure_fake_repository(repo)
    dispatched: list[tuple[str, str]] = []

    def fake_erp(request: httpx.Request) -> httpx.Response:
        dispatched.append((request.method, request.url.path))
        assert request.headers["Authorization"] == "Bearer acceptance-secret"
        if request.method == "GET":
            return httpx.Response(
                200,
                request=request,
                json={"company": "Fake Books", "token": "acceptance-secret"},
                extensions={"network_stream": _FakePeer()},
            )
        assert request.method == "POST"
        assert json.loads(request.content) == {"amount": 100, "reference": "DEMO-001"}
        return httpx.Response(
            201,
            request=request,
            json={"id": "invoice-1", "token": "acceptance-secret"},
            extensions={"network_stream": _FakePeer()},
        )

    def fake_executor(**kwargs: Any) -> ERPExecutor:
        return ERPExecutor(
            **kwargs,
            network=_FakeNetworkPolicy(),
            client_factory=lambda **client_kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(fake_erp), **client_kwargs
            ),
        )

    monkeypatch.setattr(local_runtime, "CloudBrainClient", _FakeCloud)
    monkeypatch.setattr(local_runtime, "ERPExecutor", fake_executor)

    async with create_connected_server_and_client_session(
        local_mcp,
        list_roots_callback=lambda context: _roots_callback(context, repo),
    ) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert {
            "import_erp_spec",
            "run_erp_read",
            "prepare_erp_mutation",
            "execute_sensitive_erp_action",
        } <= tools
        assert {
            "preview_erp_write",
            "confirm_erp_write",
            "execute_erp_write",
        }.isdisjoint(tools)

        imported = await _call_tool(
            client,
            "import_erp_spec",
            {
                "connector_id": "fake-erp",
                "source_path": str(spec_path),
                "repo_root": str(repo),
            },
        )
        assert imported["status"] == "imported"
        actions = {action["method"]: action["action_id"] for action in imported["actions"]}

        read = await _call_tool(
            client,
            "run_erp_read",
            {
                "action_id": actions["GET"],
                "inputs": {"json_object": "{}"},
                "repo_root": str(repo),
            },
        )
        assert read["status"] == "succeeded"

        prepared = await _call_tool(
            client,
            "prepare_erp_mutation",
            {
                "action_id": actions["POST"],
                "inputs": {
                    "json_object": '{"body":{"reference":"DEMO-001","amount":100}}'
                },
                "repo_root": str(repo),
            },
        )
        assert prepared["status"] == "prepared"
        assert prepared["mutation_class"] == "sensitive"
        assert prepared["next_tool"] == "execute_sensitive_erp_action"

        executed = await _call_tool(
            client,
            "execute_sensitive_erp_action",
            {
                "request_id": prepared["request_id"],
                "payload_hash": prepared["payload_hash"],
                "repo_root": str(repo),
            },
        )
        assert executed["status"] == "succeeded"

    audit_text = (repo / ".mercury" / "audit" / "audit.jsonl").read_text()
    assert dispatched == [("GET", "/v1/company"), ("POST", "/v1/invoices")]
    assert "acceptance-secret" not in audit_text
    assert "Fake Books" not in audit_text


def _run_live_credential_probe(connector: str, environment: str) -> None:
    repo_root = Path(os.environ.get("MERCURY_LIVE_REPO_ROOT", Path.cwd())).resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mercury_tools.cli",
            "credentials",
            "test",
            connector,
            "--env",
            environment,
            "--repo-root",
            str(repo_root),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "connected"
    assert payload["probe_action"].startswith("GET ")


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("MERCURY_LIVE_FLOWACCOUNT") != "1",
    reason="set MERCURY_LIVE_FLOWACCOUNT=1 to run the safe FlowAccount GET probe",
)
def test_live_flowaccount_credential_probe_is_read_only() -> None:
    _run_live_credential_probe("flowaccount", "production")


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("MERCURY_LIVE_PEAK") != "1",
    reason="set MERCURY_LIVE_PEAK=1 to run the safe PEAK GET probe",
)
def test_live_peak_credential_probe_is_read_only() -> None:
    _run_live_credential_probe("peak", "production")
