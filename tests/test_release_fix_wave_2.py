from __future__ import annotations

import io
import json
import os
import stat
import sys
import tarfile
import time
import zipfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from mcp.types import Tool as McpTool

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_RECEIPT_INVENTORY,
    GhApiHostedClient,
    HostedHttpResponse,
    HostedInspection,
    HostedReceipt,
    MarketplaceHostedClient,
    PublicMcpHostedClient,
    SupabaseHostedClient,
    scan_hosted_surface,
)
from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
    ArtifactKind,
    SecretScanPolicy,
)
from mercury_tools.release.scanner import CommandResult, SubprocessCommandRunner


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS)
    return policy.model_copy(update=updates)


class CallbackCommandRunner:
    def __init__(
        self,
        handler: Callable[[tuple[str, ...], Path | None], CommandResult],
    ) -> None:
        self._handler = handler
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        del input_bytes
        self.calls.append(
            {
                "argv": argv,
                "cwd": cwd,
                "max_output_bytes": max_output_bytes,
                "timeout_seconds": timeout_seconds,
            }
        )
        return self._handler(argv, cwd)


class CallbackHttpTransport:
    def __init__(
        self,
        handler: Callable[[dict[str, object]], HostedHttpResponse],
    ) -> None:
        self._handler = handler
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: object | None = None,
        max_bytes: int,
    ) -> HostedHttpResponse:
        call = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "json_body": json_body,
            "max_bytes": max_bytes,
        }
        self.calls.append(call)
        return self._handler(call)


def _gh_json(argv: tuple[str, ...], payload: object) -> CommandResult:
    if "--slurp" in argv:
        payload = [payload]
    return CommandResult(0, json.dumps(payload).encode(), b"")


def _gh_route(argv: tuple[str, ...]) -> str:
    return next(
        argument
        for argument in argv
        if argument.startswith(("repos/", "users/"))
    )


def _public_tool_records() -> list[dict[str, object]]:
    from mercury_tools.mcp.server import mcp

    records: list[dict[str, object]] = []
    for tool in mcp._tool_manager.list_tools():
        record = {
            "name": tool.name,
            "title": tool.title,
            "description": tool.description,
            "inputSchema": tool.parameters,
            "outputSchema": tool.output_schema,
            "annotations": tool.annotations,
            "icons": tool.icons,
            "_meta": tool.meta,
        }
        records.append(
            McpTool.model_validate(record).model_dump(
                by_alias=True,
                exclude_none=True,
            )
        )
    return records


def _valid_initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "test-server", "version": "1.0.0"},
    }


def _mcp_transport(
    tools: list[dict[str, object]],
    *,
    initialize_body: bytes | None = None,
    response_id: int | None = None,
    session_id: str | None = None,
) -> CallbackHttpTransport:
    def handler(call: dict[str, object]) -> HostedHttpResponse:
        if call["method"] == "DELETE":
            return HostedHttpResponse(204, b"", {})
        body = call["json_body"]
        assert isinstance(body, dict)
        method = body["method"]
        if method == "initialize":
            payload = {
                "jsonrpc": "2.0",
                "id": response_id if response_id is not None else body["id"],
                "result": _valid_initialize_result(),
            }
            return HostedHttpResponse(
                200,
                initialize_body or json.dumps(payload).encode(),
                {"mcp-session-id": session_id} if session_id else {},
            )
        if method == "notifications/initialized":
            return HostedHttpResponse(202, b"", {})
        assert method == "tools/list"
        payload = {
            "jsonrpc": "2.0",
            "id": response_id if response_id is not None else body["id"],
            "result": {"tools": tools},
        }
        return HostedHttpResponse(200, json.dumps(payload).encode(), {})

    return CallbackHttpTransport(handler)


def _write_zip(path: Path, member: str = "safe.txt", data: bytes = b"safe") -> None:
    with zipfile.ZipFile(path, "w") as archive:
        info = zipfile.ZipInfo(member)
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        archive.writestr(info, data)


def _write_artifact_set(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _write_zip(root / "mercury_tools-0.0.0-py3-none-any.whl")
    with tarfile.open(root / "mercury-tools-0.0.0.tar.gz", "w:gz") as archive:
        data = b"safe"
        info = tarfile.TarInfo("safe.txt")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    _write_zip(root / "mercury-finance-plugin.zip")
    _write_zip(root / "mercury-tools-source.zip")


@pytest.mark.parametrize(
    "surface",
    [
        "github_actions_logs_artifacts_caches",
        "github_packages_pages_wiki",
    ],
)
def test_github_inventory_requires_cache_and_package_content_receipts(surface: str) -> None:
    expected = HOSTED_RECEIPT_INVENTORY[surface]

    required = {
        "github_actions_logs_artifacts_caches": "github_actions_caches_content",
        "github_packages_pages_wiki": "github_package_versions_content",
    }[surface]
    assert required in expected


def test_child_receipts_cannot_omit_parent_count_reconciliation() -> None:
    surface = "github_releases_and_assets"
    inspection = HostedInspection(
        receipts=tuple(
            HostedReceipt(
                name=name,
                complete=True,
                page_count=1,
                record_count=0,
                exit_codes=(0,),
            )
            for name in HOSTED_RECEIPT_INVENTORY[surface]
        ),
        scanner_version=hosted_module.HOSTED_SCANNER_VERSION,
    )

    class Client:
        def inspect(
            self,
            _surface: str,
            _policy: SecretScanPolicy,
        ) -> HostedInspection:
            return inspection

    result = scan_hosted_surface(surface, Client(), _policy())

    assert f"hosted_receipt_reconciliation_failed:{surface}" in result.blockers


@pytest.mark.parametrize("malformed_parent", [True, False])
def test_github_release_and_asset_records_never_filter_into_empty_success(
    malformed_parent: bool,
) -> None:
    repo = "example/mercury-tools"

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if route.startswith(f"repos/{repo}/releases?"):
            records = [{"name": "missing-id"}] if malformed_parent else [{"id": 11}]
            return _gh_json(argv, records)
        if "releases/11/assets" in route:
            return _gh_json(argv, [{"id": 22}, {"name": "missing-id"}])
        if "releases/assets/22" in route:
            return CommandResult(0, b"safe", b"")
        raise AssertionError("unexpected command")

    runner = CallbackCommandRunner(handler)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo=repo,
    )

    result = scan_hosted_surface("github_releases_and_assets", client, _policy())

    assert "hosted_receipt_incomplete:github_releases_and_assets" in result.blockers


@pytest.mark.parametrize("malformed_kind", ["run", "artifact"])
def test_github_actions_parent_child_records_are_exact(malformed_kind: str) -> None:
    repo = "example/mercury-tools"

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if "/actions/runs?" in route:
            records = [{"id": 1}]
            if malformed_kind == "run":
                records.append({"status": "completed"})
            return _gh_json(
                argv,
                {"total_count": len(records), "workflow_runs": records},
            )
        if "/actions/runs/1/logs" in route:
            return CommandResult(0, b"safe log", b"")
        if "/actions/artifacts?" in route:
            records = [{"id": 2}]
            if malformed_kind == "artifact":
                records.append({"name": "missing-id"})
            return _gh_json(
                argv,
                {"total_count": len(records), "artifacts": records},
            )
        if "/actions/artifacts/2/zip" in route:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                info = zipfile.ZipInfo("safe.txt")
                info.create_system = 3
                info.external_attr = (stat.S_IFREG | 0o644) << 16
                archive.writestr(info, b"safe")
            return CommandResult(0, buffer.getvalue(), b"")
        if "/actions/caches?" in route:
            return _gh_json(argv, {"total_count": 0, "actions_caches": []})
        raise AssertionError("unexpected command")

    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(handler),
        repo=repo,
    )

    result = scan_hosted_surface(
        "github_actions_logs_artifacts_caches",
        client,
        _policy(),
    )

    assert "hosted_receipt_incomplete:github_actions_logs_artifacts_caches" in result.blockers


def test_existing_github_cache_blocks_when_content_cannot_be_read() -> None:
    repo = "example/mercury-tools"

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if "/actions/runs?" in route:
            return _gh_json(argv, {"total_count": 0, "workflow_runs": []})
        if "/actions/artifacts?" in route:
            return _gh_json(argv, {"total_count": 0, "artifacts": []})
        if "/actions/caches?" in route:
            return _gh_json(
                argv,
                {"total_count": 1, "actions_caches": [{"id": 9, "key": "safe"}]},
            )
        raise AssertionError("unexpected command")

    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(handler),
        repo=repo,
    )

    result = scan_hosted_surface(
        "github_actions_logs_artifacts_caches",
        client,
        _policy(),
    )

    assert "hosted_receipt_incomplete:github_actions_logs_artifacts_caches" in result.blockers


def test_github_actions_rejects_partial_records_against_exact_total() -> None:
    repo = "example/mercury-tools"

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if "/actions/runs?" in route:
            return _gh_json(
                argv,
                {"total_count": 2, "workflow_runs": [{"id": 1}]},
            )
        if "/actions/runs/1/logs" in route:
            return CommandResult(0, b"safe", b"")
        if "/actions/artifacts?" in route:
            return _gh_json(argv, {"total_count": 0, "artifacts": []})
        if "/actions/caches?" in route:
            return _gh_json(argv, {"total_count": 0, "actions_caches": []})
        raise AssertionError("unexpected command")

    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(handler),
        repo=repo,
    )

    result = scan_hosted_surface(
        "github_actions_logs_artifacts_caches",
        client,
        _policy(),
    )

    assert "hosted_receipt_incomplete:github_actions_logs_artifacts_caches" in result.blockers


def test_existing_github_package_blocks_without_version_content_proof() -> None:
    repo = "example/mercury-tools"

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if route.startswith("users/example/packages?"):
            records = (
                [{"name": "mercury", "package_type": "npm"}]
                if "package_type=npm" in route
                else []
            )
            return _gh_json(argv, records)
        if "/packages/npm/mercury/versions" in route:
            return _gh_json(argv, [{"id": 71, "name": "0.2.1"}])
        if route == f"repos/{repo}" or route.startswith(f"repos/{repo}?"):
            return _gh_json(argv, {"has_pages": False, "has_wiki": False})
        raise AssertionError("unexpected command")

    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(handler),
        repo=repo,
    )

    result = scan_hosted_surface("github_packages_pages_wiki", client, _policy())

    assert "hosted_receipt_incomplete:github_packages_pages_wiki" in result.blockers


def test_existing_github_pages_blocks_without_complete_deployment_inventory() -> None:
    repo = "example/mercury-tools"

    def command_handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        route = _gh_route(argv)
        if route.startswith("users/example/packages?"):
            return _gh_json(argv, [])
        if route == f"repos/{repo}" or route.startswith(f"repos/{repo}?"):
            return _gh_json(argv, {"has_pages": True, "has_wiki": False})
        if route.startswith(f"repos/{repo}/pages"):
            return _gh_json(argv, {"html_url": "https://pages.example/"})
        raise AssertionError("unexpected command")

    transport = CallbackHttpTransport(
        lambda _call: HostedHttpResponse(200, b"safe root page", {})
    )
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(command_handler),
        repo=repo,
        http_transport=transport,
    )

    result = scan_hosted_surface("github_packages_pages_wiki", client, _policy())

    assert "hosted_receipt_incomplete:github_packages_pages_wiki" in result.blockers


def test_github_wiki_rejects_malformed_ref_inventory() -> None:
    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        if argv[:2] == ("git", "ls-remote"):
            return CommandResult(0, b"malformed ref inventory\n", b"")
        if argv[:2] == ("git", "clone"):
            return CommandResult(0, b"", b"")
        if argv[:2] == ("git", "log"):
            return CommandResult(0, b"safe", b"")
        raise AssertionError("unexpected command")

    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=CallbackCommandRunner(handler),
        repo="example/mercury-tools",
    )

    query, download = client._wiki_receipts(_policy())

    assert query.complete is False
    assert download.complete is False


def test_marketplace_rejects_malformed_or_duplicate_items() -> None:
    transport = CallbackHttpTransport(
        lambda _call: HostedHttpResponse(200, b'[{"name":"mercury-finance"},{}]', {})
    )
    client = MarketplaceHostedClient(
        snapshot_url="https://marketplace.example/snapshot",
        transport=transport,
    )

    result = scan_hosted_surface("marketplace_snapshot", client, _policy())

    assert "hosted_receipt_incomplete:marketplace_snapshot" in result.blockers


def test_github_queries_are_explicit_bounded_pages_without_paginate_or_slurp() -> None:
    repo = "example/mercury-tools"
    records = [
        {"ref": "refs/pull/1/head", "object": {"sha": "a" * 40}},
        {"ref": "refs/pull/2/head", "object": {"sha": "b" * 40}},
    ]

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        if "--paginate" in argv:
            return CommandResult(0, json.dumps([[records[0]], [records[1]], []]).encode(), b"")
        route = _gh_route(argv)
        page = int(dict(item.split("=", 1) for item in route.split("?", 1)[1].split("&"))["page"])
        payload = [records[page - 1]] if page <= len(records) else []
        return _gh_json(argv, payload)

    runner = CallbackCommandRunner(handler)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo=repo,
    )

    result = scan_hosted_surface(
        "github_pull_request_refs",
        client,
        _policy(max_hosted_page_records=1, max_hosted_records=10),
    )

    assert result.blockers == ()
    assert all(
        "--paginate" not in call["argv"] and "--slurp" not in call["argv"]  # type: ignore[operator]
        for call in runner.calls
    )
    assert all(call["max_output_bytes"] is not None for call in runner.calls)
    assert [
        dict(
            item.split("=", 1)
            for item in _gh_route(call["argv"]).split("?", 1)[1].split("&")  # type: ignore[arg-type]
        )["page"]
        for call in runner.calls
    ] == ["1", "2", "3"]


@pytest.mark.parametrize("failure", ["missing", "short", "changing"])
def test_supabase_requires_stable_exact_content_range_until_total(failure: str) -> None:
    knowledge_calls = 0

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        nonlocal knowledge_calls
        url = str(call["url"])
        if "/rest/v1/knowledge" in url:
            knowledge_calls += 1
            if failure == "missing":
                return HostedHttpResponse(200, b"[]", {})
            if failure == "short":
                return HostedHttpResponse(200, b"[{}]", {"Content-Range": "0-0/2"})
            if knowledge_calls == 1:
                return HostedHttpResponse(206, b"[{},{}]", {"Content-Range": "0-1/3"})
            return HostedHttpResponse(206, b"[{}]", {"Content-Range": "2-2/4"})
        if "/storage/v1/object/list/public" in url:
            return HostedHttpResponse(200, b"[]", {"Content-Range": "*/0"})
        raise AssertionError("unexpected request")

    client = SupabaseHostedClient(
        base_url="https://project.supabase.example",
        service_key="operator-token",
        knowledge_tables=("knowledge",),
        storage_buckets=("public",),
        transport=CallbackHttpTransport(handler),
    )

    result = scan_hosted_surface(
        "supabase_knowledge_and_storage",
        client,
        _policy(max_hosted_page_records=2, max_hosted_records=10),
    )

    assert "hosted_receipt_incomplete:supabase_knowledge_and_storage" in result.blockers
    if failure == "short":
        assert knowledge_calls == 1


def test_supabase_downloads_each_exactly_counted_storage_object_once() -> None:
    objects = [{"name": "one.txt"}, {"name": "two.txt"}, {"name": "three.txt"}]

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        url = str(call["url"])
        if "/rest/v1/knowledge" in url:
            return HostedHttpResponse(200, b"[]", {"Content-Range": "*/0"})
        if "/storage/v1/object/list/public" in url:
            body = call["json_body"]
            assert isinstance(body, dict)
            offset = int(body["offset"])
            limit = int(body["limit"])
            page = objects[offset : offset + limit]
            end = offset + len(page) - 1
            content_range = f"{offset}-{end}/{len(objects)}"
            return HostedHttpResponse(
                200,
                json.dumps(page).encode(),
                {"Content-Range": content_range},
            )
        if "/storage/v1/object/authenticated/public/" in url:
            return HostedHttpResponse(200, b"safe", {})
        raise AssertionError("unexpected request")

    transport = CallbackHttpTransport(handler)
    client = SupabaseHostedClient(
        base_url="https://project.supabase.example",
        service_key="operator-token",
        knowledge_tables=("knowledge",),
        storage_buckets=("public",),
        transport=transport,
    )

    result = scan_hosted_surface(
        "supabase_knowledge_and_storage",
        client,
        _policy(max_hosted_page_records=2, max_hosted_records=10),
    )

    assert result.blockers == ()
    downloads = [
        call
        for call in transport.calls
        if "/storage/v1/object/authenticated/public/" in str(call["url"])
    ]
    assert len(downloads) == len(objects)
    assert len({call["url"] for call in downloads}) == len(objects)


def test_supabase_duplicate_storage_objects_block_before_download() -> None:
    def handler(call: dict[str, object]) -> HostedHttpResponse:
        url = str(call["url"])
        if "/rest/v1/knowledge" in url:
            return HostedHttpResponse(200, b"[]", {"Content-Range": "*/0"})
        if "/storage/v1/object/list/public" in url:
            return HostedHttpResponse(
                200,
                b'[{"name":"same.txt"},{"name":"same.txt"}]',
                {"Content-Range": "0-1/2"},
            )
        if "/storage/v1/object/authenticated/public/" in url:
            return HostedHttpResponse(200, b"safe", {})
        raise AssertionError("unexpected request")

    transport = CallbackHttpTransport(handler)
    client = SupabaseHostedClient(
        base_url="https://project.supabase.example",
        service_key="operator-token",
        knowledge_tables=("knowledge",),
        storage_buckets=("public",),
        transport=transport,
    )

    result = scan_hosted_surface("supabase_knowledge_and_storage", client, _policy())

    assert "hosted_receipt_incomplete:supabase_knowledge_and_storage" in result.blockers
    assert not any(
        "/storage/v1/object/authenticated/public/" in str(call["url"])
        for call in transport.calls
    )


def test_public_mcp_exact_inventory_is_compiled_from_20_public_tools() -> None:
    tools = _public_tool_records()
    assert len(tools) == len({record["name"] for record in tools}) == 20
    transport = _mcp_transport(tools)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    result = scan_hosted_surface("public_mcp_responses", client, _policy())

    assert result.blockers == ()


def test_public_and_local_mcp_inventories_remain_separate_and_exact() -> None:
    from mercury_tools.mcp.local_server import local_mcp

    public_names = [record["name"] for record in _public_tool_records()]
    local_names = [tool.name for tool in local_mcp._tool_manager.list_tools()]

    assert len(public_names) == len(set(public_names)) == 20
    assert len(local_names) == len(set(local_names)) == 19
    assert set(public_names) != set(local_names)


def test_public_mcp_rejects_malformed_trailing_sse_event() -> None:
    valid = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2025-11-25"},
        }
    )
    body = f"event: message\ndata: {valid}\n\ndata: {{malformed\n\n".encode()

    with pytest.raises(ValueError, match="invalid_mcp_response"):
        hosted_module._decode_mcp_json(body)


@pytest.mark.parametrize(
    "tools",
    [
        [{} for _ in range(19)],
        [{"name": "duplicate"} for _ in range(19)],
    ],
)
def test_public_mcp_rejects_malformed_or_duplicate_tool_records(
    tools: list[dict[str, object]],
) -> None:
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=_mcp_transport(tools),
    )

    result = scan_hosted_surface("public_mcp_responses", client, _policy())

    assert "hosted_receipt_incomplete:public_mcp_responses" in result.blockers


def test_public_mcp_correlates_jsonrpc_ids_and_closes_session() -> None:
    session_id = "private-session-id"
    transport = _mcp_transport(
        _public_tool_records(),
        response_id=999,
        session_id=session_id,
    )
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    result = scan_hosted_surface("public_mcp_responses", client, _policy())

    assert "hosted_receipt_incomplete:public_mcp_responses" in result.blockers
    assert transport.calls[-1]["method"] == "DELETE"
    assert transport.calls[-1]["headers"] == {  # type: ignore[comparison-overlap]
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        "Mcp-Session-Id": session_id,
    }
    assert session_id not in result.model_dump_json()


def _ref_inventory_runner(
    *,
    remote_head: str = "a" * 40,
    local_head: str = "a" * 40,
    default_head: str = "a" * 40,
    duplicate_head: bool = False,
    remote_tag_peeled: str | None = None,
    local_tag_peeled: str | None = None,
) -> CallbackCommandRunner:
    tag_object = "c" * 40

    def handler(argv: tuple[str, ...], _cwd: Path | None) -> CommandResult:
        if argv[1:3] == ("ls-remote", "--heads"):
            line = f"{remote_head}\trefs/heads/main\n"
            if duplicate_head:
                line += f"{remote_head}\trefs/heads/main\n"
            return CommandResult(0, line.encode(), b"")
        if argv[1:3] == ("ls-remote", "--tags"):
            lines = f"{tag_object}\trefs/tags/v1\n"
            if "--refs" not in argv and remote_tag_peeled is not None:
                lines += f"{remote_tag_peeled}\trefs/tags/v1^{{}}\n"
            return CommandResult(0, lines.encode(), b"")
        if argv[1:3] == ("ls-remote", "origin"):
            return CommandResult(0, b"", b"")
        if argv[1:4] == ("ls-remote", "--symref", "origin"):
            return CommandResult(
                0,
                (
                    "ref: refs/heads/main\tHEAD\n"
                    f"{default_head}\tHEAD\n"
                ).encode(),
                b"",
            )
        if argv[1] == "for-each-ref":
            if "%(objectname)" not in argv[2]:
                return CommandResult(
                    0,
                    b"refs/remotes/origin/main\nrefs/tags/v1\n",
                    b"",
                )
            lines = (
                f"refs/remotes/origin/main\t{local_head}\t\n"
                f"refs/tags/v1\t{tag_object}\t{local_tag_peeled or ''}\n"
            )
            return CommandResult(0, lines.encode(), b"")
        if argv[1:3] == ("symbolic-ref", "refs/remotes/origin/HEAD"):
            return CommandResult(0, b"refs/remotes/origin/main\n", b"")
        raise AssertionError(f"unexpected command: {argv!r}")

    return CallbackCommandRunner(handler)


@pytest.mark.parametrize(
    "runner",
    [
        _ref_inventory_runner(default_head="b" * 40),
        _ref_inventory_runner(local_head="b" * 40),
        _ref_inventory_runner(duplicate_head=True),
        _ref_inventory_runner(
            remote_tag_peeled="d" * 40,
            local_tag_peeled="e" * 40,
        ),
    ],
)
def test_git_ref_inventory_requires_exact_unique_objects(
    runner: CallbackCommandRunner,
    tmp_path: Path,
) -> None:
    blockers: list[str] = []

    result = scanner_module._inventory_refs(
        runner,
        tmp_path,
        evidence_hashes=[],
        exit_codes=[],
        blockers=blockers,
    )

    assert result is None or blockers


@pytest.mark.parametrize(
    ("scan", "expected"),
    [
        ("filesystem", "filesystem_traversal_failed"),
        ("artifacts", "artifact_traversal_failed"),
    ],
)
def test_scandir_errors_are_unsuppressible_blockers(
    scan: str,
    expected: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    if scan == "artifacts":
        _write_artifact_set(root)
    else:
        root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    original_scandir = os.scandir
    calls = 0

    def failing_scandir(path: Any):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError("private detail")
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", failing_scandir)

    result = (
        scanner_module.scan_artifacts(root, _policy())
        if scan == "artifacts"
        else scanner_module.scan_filesystem(root, _policy())
    )

    assert expected in result.blockers
    assert "private detail" not in result.model_dump_json()


def test_directory_replacement_symlink_race_blocks_traversal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    (nested / "safe.txt").write_text("safe", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    (replacement / "safe.txt").write_text("safe", encoding="utf-8")
    original_scandir = os.scandir
    calls = 0
    replaced = False

    class RaceIterator:
        def __init__(self, iterator: Any) -> None:
            self._iterator = iterator

        def __enter__(self) -> RaceIterator:
            self._iterator.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._iterator.__exit__(*args)

        def __iter__(self) -> RaceIterator:
            return self

        def __next__(self) -> os.DirEntry[str]:
            nonlocal replaced
            try:
                return next(self._iterator)
            except StopIteration:
                if not replaced:
                    replaced = True
                    nested.rename(root / "nested-original")
                    nested.symlink_to(replacement, target_is_directory=True)
                raise

    def racing_scandir(path: Any):
        nonlocal calls
        calls += 1
        iterator = original_scandir(path)
        return RaceIterator(iterator) if calls == 2 else iterator

    monkeypatch.setattr(os, "scandir", racing_scandir)

    result = scanner_module.scan_filesystem(root, _policy())

    assert "filesystem_traversal_failed" in result.blockers


def test_artifact_hash_and_archive_scan_use_one_immutable_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.zip"
    replacement = tmp_path / "replacement.zip"
    _write_zip(replacement)
    original_scan = scanner_module._scan_archive
    replaced = False

    def race_scan(
        artifact: object,
        kind: ArtifactKind,
        policy: SecretScanPolicy,
        archive_budget: object,
    ) -> tuple[list[object], list[str]]:
        nonlocal replaced
        if kind is ArtifactKind.SOURCE and not replaced:
            replaced = True
            source.unlink()
            source.symlink_to(replacement)
        return original_scan(artifact, kind, policy, archive_budget)  # type: ignore[arg-type]

    monkeypatch.setattr(scanner_module, "_scan_archive", race_scan)

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_read_failed:source" in result.blockers


@pytest.mark.parametrize(
    "member_type",
    [
        tarfile.FIFOTYPE,
        tarfile.CHRTYPE,
        tarfile.BLKTYPE,
        tarfile.SYMTYPE,
        tarfile.LNKTYPE,
        b"s",
    ],
)
def test_tar_rejects_every_non_file_non_directory_member(
    member_type: bytes,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.tar.gz"
    (root / "mercury-tools-source.zip").unlink()
    with tarfile.open(source, "w:gz") as archive:
        info = tarfile.TarInfo("special")
        info.type = member_type
        info.linkname = "safe.txt"
        archive.addfile(info)

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unsafe_member:source" in result.blockers


@pytest.mark.parametrize(
    "member_mode",
    [
        stat.S_IFIFO,
        stat.S_IFCHR,
        stat.S_IFBLK,
        stat.S_IFSOCK,
        stat.S_IFLNK,
        0o150000,
    ],
)
def test_zip_rejects_every_special_unix_member_type(
    member_mode: int,
    tmp_path: Path,
) -> None:
    root = tmp_path / "dist"
    _write_artifact_set(root)
    source = root / "mercury-tools-source.zip"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("special")
        info.create_system = 3
        info.external_attr = (member_mode | 0o600) << 16
        archive.writestr(info, b"")

    result = scanner_module.scan_artifacts(root, _policy())

    assert "artifact_unsafe_member:source" in result.blockers


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_subprocess_output_limit_kills_child_during_execution(
    stream_name: str,
    tmp_path: Path,
) -> None:
    pid_file = tmp_path / "child.pid"
    script = (
        "import os,pathlib,sys,time;"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        f"sys.{stream_name}.buffer.write(b'x' * 1048576);sys.{stream_name}.flush();"
        "time.sleep(1.5)"
    )
    runner = SubprocessCommandRunner(max_output_bytes=1024)

    started = time.monotonic()
    result = runner.run((sys.executable, "-c", script))
    elapsed = time.monotonic() - started

    assert result == CommandResult(125, b"", b"")
    assert elapsed < 1.0
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_subprocess_timeout_kills_and_reaps_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "timeout-child.pid"
    script = (
        "import os,pathlib,time;"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()));"
        "time.sleep(5)"
    )
    runner = SubprocessCommandRunner(
        max_output_bytes=1024,
        timeout_seconds=0.1,
    )

    started = time.monotonic()
    result = runner.run((sys.executable, "-c", script))
    elapsed = time.monotonic() - started

    assert result == CommandResult(124, b"", b"")
    assert elapsed < 1.0
    pid = int(pid_file.read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
