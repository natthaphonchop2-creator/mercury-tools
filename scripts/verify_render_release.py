#!/usr/bin/env python3
"""Verify an exact Mercury Render deployment without exposing provider values."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

import httpx
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mercury_tools.release.hosted import (
    HostedAdapterConfig,
    build_hosted_clients,
    scan_hosted_surface,
)
from mercury_tools.release.models import PINNED_SCANNER_VERSIONS, SecretScanPolicy

EXPECTED_HOSTED_TOOLS = frozenset(
    {
        "search_knowledge",
        "retrieve_context_pack",
        "retrieve_workspace_context_pack",
        "get_document",
        "create_public_workspace",
        "get_public_workspace",
        "list_connectors",
        "connector_capabilities",
        "start_connector_setup",
        "connector_status",
        "run_accounting_skill",
        "flow_cheat_sheet",
        "check_flow_syntax",
        "inspect_flow_files",
        "run_flow",
        "run_flow_files",
        "run_mercury_flow",
        "list_workspace_flows",
        "run_workspace_flow",
        "save_workspace_flow",
    }
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_SAFE_SERVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_CREDENTIAL_KEYS = (
    "api_key",
    "authorization",
    "client_id",
    "client_secret",
    "credential",
    "password",
    "secret",
    "token",
)
_MAX_JSON_BYTES = 32 * 1024 * 1024
_MAX_SCHEMA_NODES = 100_000


class RenderReleaseError(RuntimeError):
    """Raised when an exact Render release gate is not satisfied."""


@dataclass(frozen=True)
class McpReleaseEvidence:
    server_name: str
    tools: tuple[dict[str, object], ...]
    searches: dict[str, dict[str, object]]
    contexts: dict[str, dict[str, object]]


@dataclass(frozen=True)
class RenderReleaseReport:
    passed: bool
    version: str
    commit: str
    catalog_count: int
    tool_count: int


class RenderProbe(Protocol):
    def health(self) -> object: ...

    def status(self) -> object: ...

    def catalog(self) -> object: ...

    def mcp(self, endpoint: str) -> McpReleaseEvidence: ...

    def scan_logs(self) -> bool: ...


def _valid_https_url(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2_048:
        return False
    parsed = urlparse(value)
    return bool(
        parsed.scheme == "https"
        and parsed.hostname
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
        and not parsed.query
        and not parsed.fragment
    )


def _contains_credential_schema(value: object) -> bool:
    pending = [value]
    for _ in range(_MAX_SCHEMA_NODES):
        if not pending:
            return False
        item = pending.pop()
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).casefold().replace("-", "_")
                if any(marker in normalized for marker in _CREDENTIAL_KEYS):
                    return True
                pending.append(child)
        elif isinstance(item, list):
            pending.extend(item)
    return bool(pending)


def _has_connector_validation_citations(
    payload: object,
    result_field: str,
    connector: str,
) -> bool:
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        return False
    rows = payload.get(result_field)
    return bool(
        isinstance(rows, list)
        and rows
        and all(
            isinstance(row, dict)
            and isinstance(row.get("citation"), dict)
            and row["citation"]
            and isinstance(row.get("metadata"), dict)
            and row["metadata"].get("connector") == connector
            and row["metadata"].get("doc_type") == "endpoint_validation"
            and row["metadata"].get("review_status") == "reviewed"
            for row in rows
        )
    )


def verify_render_release(
    probe: RenderProbe,
    *,
    version: str,
    commit: str,
) -> RenderReleaseReport:
    """Run every Render release check in fail-closed order."""

    if _VERSION_RE.fullmatch(version) is None:
        raise RenderReleaseError("requested_version_invalid")
    if _COMMIT_RE.fullmatch(commit) is None:
        raise RenderReleaseError("requested_commit_invalid")

    try:
        health = probe.health()
    except Exception as exc:
        raise RenderReleaseError("healthz_required") from exc
    if not isinstance(health, dict) or health.get("status") != "ok":
        raise RenderReleaseError("healthz_required")

    try:
        status = probe.status()
    except Exception as exc:
        raise RenderReleaseError("status_unavailable") from exc
    if not isinstance(status, dict) or status.get("status") != "ok":
        raise RenderReleaseError("status_unavailable")
    if status.get("version") != version:
        raise RenderReleaseError("status_version_mismatch")
    if status.get("deployment_commit") != commit:
        raise RenderReleaseError("status_commit_mismatch")
    endpoint = status.get("mcp_endpoint")
    if not _valid_https_url(endpoint):
        raise RenderReleaseError("status_mcp_endpoint_invalid")

    try:
        catalog = probe.catalog()
    except Exception as exc:
        raise RenderReleaseError("catalog_unavailable") from exc
    actions = catalog.get("actions") if isinstance(catalog, dict) else None
    if not isinstance(actions, list) or len(actions) != 254:
        raise RenderReleaseError("catalog_count_mismatch")

    try:
        mcp = probe.mcp(endpoint)
    except Exception as exc:
        raise RenderReleaseError("mcp_unavailable") from exc
    if not isinstance(mcp, McpReleaseEvidence) or mcp.server_name != "Mercury Tools":
        raise RenderReleaseError("mcp_initialize_invalid")
    if len(mcp.tools) != 20:
        raise RenderReleaseError("hosted_tool_surface_mismatch")
    names = [tool.get("name") for tool in mcp.tools if isinstance(tool, dict)]
    if len(names) != 20 or set(names) != EXPECTED_HOSTED_TOOLS:
        raise RenderReleaseError("hosted_tool_surface_mismatch")
    for tool in mcp.tools:
        if _contains_credential_schema(tool.get("inputSchema")):
            raise RenderReleaseError("public_credential_surface")
    for collection_name, collection, result_field in (
        ("searches", mcp.searches, "results"),
        ("contexts", mcp.contexts, "context"),
    ):
        if set(collection) != {"flowaccount", "peak"}:
            raise RenderReleaseError(f"rag_{collection_name}_inventory_invalid")
        for connector in ("flowaccount", "peak"):
            if not _has_connector_validation_citations(
                collection[connector],
                result_field,
                connector,
            ):
                raise RenderReleaseError(
                    f"rag_{collection_name}_{connector}_citation_missing"
                )

    try:
        logs_clean = probe.scan_logs()
    except Exception as exc:
        raise RenderReleaseError("render_log_scan_blocked") from exc
    if logs_clean is not True:
        raise RenderReleaseError("render_log_scan_blocked")

    return RenderReleaseReport(
        passed=True,
        version=version,
        commit=commit,
        catalog_count=len(actions),
        tool_count=len(mcp.tools),
    )


def _result_payload(result: types.CallToolResult) -> dict[str, object]:
    if isinstance(result.structuredContent, dict):
        return result.structuredContent
    text = next(
        (item.text for item in result.content if isinstance(item, types.TextContent)),
        None,
    )
    if text is None or len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise RenderReleaseError("mcp_payload_invalid")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RenderReleaseError("mcp_payload_invalid")
    return payload


class LiveRenderProbe:
    def __init__(
        self,
        *,
        base_url: str,
        mcp_token: str | None,
        render_api_url: str,
        render_service_id: str | None,
        render_token: str | None,
    ) -> None:
        if not _valid_https_url(base_url):
            raise RenderReleaseError("render_url_invalid")
        if not _valid_https_url(render_api_url):
            raise RenderReleaseError("render_api_url_invalid")
        if (
            render_service_id is not None
            and _SAFE_SERVICE_ID_RE.fullmatch(render_service_id) is None
        ):
            raise RenderReleaseError("render_service_id_invalid")
        self._base_url = base_url.rstrip("/")
        self._mcp_token = mcp_token
        self._render_api_url = render_api_url.rstrip("/")
        self._render_service_id = render_service_id
        self._render_token = render_token

    def _get_json(self, path: str) -> object:
        with httpx.Client(timeout=30.0, follow_redirects=False) as client:
            response = client.get(f"{self._base_url}{path}")
        if response.status_code != 200 or len(response.content) > _MAX_JSON_BYTES:
            raise RenderReleaseError("http_probe_failed")
        return response.json()

    def health(self) -> object:
        return self._get_json("/healthz")

    def status(self) -> object:
        return self._get_json("/api/status")

    def catalog(self) -> object:
        return self._get_json("/api/cloud/v1/catalog/actions")

    def mcp(self, endpoint: str) -> McpReleaseEvidence:
        parsed_endpoint = urlparse(endpoint)
        parsed_base = urlparse(self._base_url)
        if (
            not _valid_https_url(endpoint)
            or parsed_endpoint.hostname != parsed_base.hostname
            or parsed_endpoint.port != parsed_base.port
        ):
            raise RenderReleaseError("status_mcp_endpoint_invalid")
        return asyncio.run(self._mcp(endpoint))

    async def _mcp(self, endpoint: str) -> McpReleaseEvidence:
        headers = {}
        if self._mcp_token:
            headers["Authorization"] = f"Bearer {self._mcp_token}"
        async with httpx.AsyncClient(
            headers=headers,
            timeout=30.0,
            follow_redirects=False,
        ) as client, streamable_http_client(endpoint, http_client=client) as streams:
            read_stream, write_stream, _session_id = streams
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                searches: dict[str, dict[str, object]] = {}
                contexts: dict[str, dict[str, object]] = {}
                for connector, label in (
                    ("flowaccount", "FlowAccount"),
                    ("peak", "PEAK"),
                ):
                    search = await session.call_tool(
                        "search_knowledge",
                        {
                            "query": f"{label} invoice accounting validation",
                            "filters": {
                                "connector": connector,
                                "doc_type": "endpoint_validation",
                                "review_status": "reviewed",
                            },
                            "top_k": 5,
                        },
                    )
                    context = await session.call_tool(
                        "retrieve_context_pack",
                        {
                            "query": f"{label} invoice accounting validation",
                            "task": "release verification",
                            "filters": {
                                "connector": connector,
                                "doc_type": "endpoint_validation",
                                "review_status": "reviewed",
                            },
                            "max_chunks": 5,
                        },
                    )
                    searches[connector] = _result_payload(search)
                    contexts[connector] = _result_payload(context)
        return McpReleaseEvidence(
            server_name=initialized.serverInfo.name,
            tools=tuple(
                {"name": tool.name, "inputSchema": tool.inputSchema}
                for tool in listed.tools
            ),
            searches=searches,
            contexts=contexts,
        )

    def scan_logs(self) -> bool:
        if not self._render_service_id or not self._render_token:
            return False
        clients = build_hosted_clients(
            HostedAdapterConfig(
                repo="natthaphonchop2-creator/mercury-tools",
                render_api_url=self._render_api_url,
                render_service_id=self._render_service_id,
                render_token=self._render_token,
            )
        )
        client = clients.get("render_build_and_runtime_logs")
        if client is None:
            return False
        result = scan_hosted_surface(
            "render_build_and_runtime_logs",
            client,
            SecretScanPolicy(scanner_versions=dict(PINNED_SCANNER_VERSIONS)),
        )
        return bool(
            result.scanner_version
            and not result.findings
            and not result.blockers
            and len(result.exit_codes) == 2
            and all(code == 200 for code in result.exit_codes)
        )


def _env_value(name: str) -> str | None:
    value = os.environ.get(name, "")
    return value if value else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--render-api-url", default="https://api.render.com")
    parser.add_argument("--render-service-id-env", default="RENDER_SERVICE_ID")
    parser.add_argument("--render-token-env", default="RENDER_API_TOKEN")
    parser.add_argument("--mcp-token-env", default="MERCURY_TOOLS_HTTP_BEARER_TOKEN")
    args = parser.parse_args()
    try:
        probe = LiveRenderProbe(
            base_url=args.url,
            mcp_token=_env_value(args.mcp_token_env),
            render_api_url=args.render_api_url,
            render_service_id=_env_value(args.render_service_id_env),
            render_token=_env_value(args.render_token_env),
        )
        report = verify_render_release(
            probe,
            version=args.version,
            commit=args.commit,
        )
    except (RenderReleaseError, httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        code = str(exc) if isinstance(exc, RenderReleaseError) else "render_probe_failed"
        print(f"Render release verification failed: {code}", file=sys.stderr)
        return 1
    print(
        "Render release verification passed "
        f"(version={report.version}, commit={report.commit}, "
        f"catalog={report.catalog_count}, tools={report.tool_count})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
