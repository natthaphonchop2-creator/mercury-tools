from __future__ import annotations

import io
import json
import shutil
import stat
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from mercury_tools.release import hosted as hosted_module
from mercury_tools.release import scanner as scanner_module
from mercury_tools.release.hosted import (
    HOSTED_PUBLIC_SURFACES,
    HOSTED_RECEIPT_INVENTORY,
    HOSTED_SCANNER_VERSION,
    GhApiHostedClient,
    HostedAdapterConfig,
    HostedHttpResponse,
    HostedInspection,
    HostedReceipt,
    MarketplaceHostedClient,
    PublicMcpHostedClient,
    RenderHostedClient,
    SupabaseHostedClient,
    build_hosted_clients,
    scan_hosted_surface,
)
from mercury_tools.release.models import (
    PINNED_SCANNER_VERSIONS,
    REQUIRED_PUBLIC_SURFACES,
    ArtifactKind,
    ArtifactScanResult,
    GitRepositoryScanResult,
    HostedSurface,
    PublicSurfaceManifest,
    SecretScanAllowlist,
    SecretScanPolicy,
    SecretScanRequest,
)
from mercury_tools.release.scanner import (
    CommandResult,
    ReleaseGateError,
    build_blocked_report,
    scan_public_release,
)


def _write_regular_zip_member(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.compress_type = archive.compression
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data)


class FakeHostedClient:
    def __init__(self, inspection: HostedInspection | Exception) -> None:
        self.inspection = inspection
        self.calls: list[str] = []

    def inspect(self, surface: str, _policy: SecretScanPolicy) -> HostedInspection:
        self.calls.append(surface)
        if isinstance(self.inspection, Exception):
            raise self.inspection
        return self.inspection


class FakeCommandRunner:
    def __init__(self, responses: Mapping[str, CommandResult]) -> None:
        self.responses = dict(responses)
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        input_bytes: bytes | None = None,
        max_output_bytes: int | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        del cwd, input_bytes, max_output_bytes, timeout_seconds
        self.calls.append(argv)
        executable = Path(argv[0]).name
        if executable in self.responses and argv[1:] in {("version",), ("--version",)}:
            return self.responses[executable]
        for key, response in self.responses.items():
            if any(key in argument for argument in argv):
                return response
        raise AssertionError("unexpected_command")


class FakeHttpTransport:
    def __init__(self, responses: Mapping[str, HostedHttpResponse]) -> None:
        self.responses = dict(responses)
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
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "json_body": json_body,
                "max_bytes": max_bytes,
            }
        )
        for suffix, response in self.responses.items():
            if suffix in url:
                return response
        raise AssertionError("unexpected_http_request")


class CallbackHttpTransport:
    def __init__(self, handler: Any) -> None:
        self.handler = handler
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
        return self.handler(call)


def _request(tmp_path: Path, **updates: object) -> SecretScanRequest:
    request = SecretScanRequest(
        repo="example/mercury-tools",
        artifacts=tmp_path / "dist",
        all_history=True,
        hosted=True,
        manifest=PublicSurfaceManifest(
            required=REQUIRED_PUBLIC_SURFACES,
            scanner_versions=PINNED_SCANNER_VERSIONS,
        ),
        allowlist=SecretScanAllowlist(entries=()),
        policy=SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS),
    )
    return request.model_copy(update=updates)


def _complete_inspection(surface: str, *chunks: bytes) -> HostedInspection:
    expected = HOSTED_RECEIPT_INVENTORY[surface]
    material = chunks or (b"safe hosted fixture",)
    receipts = tuple(
        HostedReceipt(
            name=name,
            chunks=material if index == 0 else (),
            object_boundaries=(
                tuple(
                    hosted_module.HostedObjectBoundary(1, len(chunk))
                    for chunk in (material if index == 0 else ())
                )
                if name in hosted_module._ARCHIVE_CAPABLE_RECEIPTS
                else None
            ),
            complete=True,
            page_count=1,
            record_count=0,
            request_count=0 if name in hosted_module._PARENT_COUNT_RECEIPTS else 1,
            parent_record_count=(
                0 if name in hosted_module._PARENT_COUNT_RECEIPTS else None
            ),
            exit_codes=(0,),
        )
        for index, name in enumerate(expected)
    )
    return HostedInspection(
        receipts=receipts,
        scanner_version=HOSTED_SCANNER_VERSION,
    )


def _hosted_clients() -> dict[str, FakeHostedClient]:
    return {
        surface: FakeHostedClient(_complete_inspection(surface))
        for surface in HOSTED_PUBLIC_SURFACES
    }


def _policy(**updates: object) -> SecretScanPolicy:
    policy = SecretScanPolicy(scanner_versions=PINNED_SCANNER_VERSIONS)
    return policy.model_copy(update=updates)


def _public_tool_records() -> list[dict[str, object]]:
    return [
        dict(record)
        for record in hosted_module._compiled_public_mcp_inventory().values()
    ]


def _valid_initialize_result() -> dict[str, object]:
    return {
        "protocolVersion": "2025-11-25",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "test-server", "version": "1.0.0"},
    }


def test_inaccessible_hosted_surface_blocks_release(tmp_path: Path) -> None:
    request = _request(
        tmp_path,
        hosted_surfaces=(HostedSurface(name="render_logs", accessible=False),),
    )

    report = scan_public_release(request)

    assert report.passed is False
    assert "hosted_surface_inaccessible:render_logs" in report.blockers


def test_hosted_receipt_stream_detects_secret_without_returning_raw_payload() -> None:
    raw_value = "xox" + "b-" + "A1b2C3d4-E5f6G7h8-I9j0K1l2"
    surface = "public_mcp_responses"
    client = FakeHostedClient(_complete_inspection(surface, raw_value.encode()))

    result = scan_hosted_surface(surface, client, _policy())
    serialized = result.model_dump_json()

    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert raw_value not in serialized
    assert len(result.evidence_hashes) == len(HOSTED_RECEIPT_INVENTORY[surface])


def test_caller_complete_flag_without_fixed_receipts_is_not_evidence() -> None:
    surface = "render_build_and_runtime_logs"
    inspection = HostedInspection(receipts=(), scanner_version=HOSTED_SCANNER_VERSION)

    result = scan_hosted_surface(surface, FakeHostedClient(inspection), _policy())

    assert result.blockers == (f"hosted_receipt_inventory_invalid:{surface}",)


def test_zero_records_pass_only_when_every_expected_receipt_proves_complete_empty() -> None:
    surface = "supabase_knowledge_and_storage"
    inspection = HostedInspection(
        receipts=tuple(
            HostedReceipt(
                name=name,
                chunks=(),
                object_boundaries=(
                    () if name in hosted_module._ARCHIVE_CAPABLE_RECEIPTS else None
                ),
                complete=True,
                page_count=1,
                record_count=0,
                request_count=(
                    0 if name in hosted_module._PARENT_COUNT_RECEIPTS else 1
                ),
                parent_record_count=(
                    0 if name in hosted_module._PARENT_COUNT_RECEIPTS else None
                ),
                status_codes=(200,),
            )
            for name in HOSTED_RECEIPT_INVENTORY[surface]
        ),
        scanner_version=HOSTED_SCANNER_VERSION,
    )

    complete = scan_hosted_surface(surface, FakeHostedClient(inspection), _policy())
    missing = scan_hosted_surface(
        surface,
        FakeHostedClient(
            HostedInspection(
                receipts=inspection.receipts[:-1],
                scanner_version=HOSTED_SCANNER_VERSION,
            )
        ),
        _policy(),
    )

    assert complete.blockers == ()
    assert f"hosted_receipt_inventory_invalid:{surface}" in missing.blockers


@pytest.mark.parametrize(
    ("receipt_updates", "policy_updates", "blocker"),
    [
        ({"complete": False}, {}, "hosted_receipt_incomplete"),
        ({"exit_codes": (7,)}, {}, "hosted_command_failed"),
        ({"exit_codes": (), "status_codes": (503,)}, {}, "hosted_status_failed"),
        ({"page_count": 3}, {"max_hosted_pages": 2}, "hosted_page_limit"),
        ({"record_count": 3}, {"max_hosted_records": 2}, "hosted_record_limit"),
        ({"chunks": (b"12345",)}, {"max_hosted_receipt_bytes": 4}, "hosted_byte_limit"),
    ],
)
def test_each_hosted_receipt_reconciles_completion_status_exit_and_budgets(
    receipt_updates: dict[str, object],
    policy_updates: dict[str, object],
    blocker: str,
) -> None:
    surface = "marketplace_snapshot"
    inspection = _complete_inspection(surface)
    receipt = inspection.receipts[0]
    values = {
        field: getattr(receipt, field)
        for field in (
            "name",
            "chunks",
            "object_boundaries",
            "complete",
            "page_count",
            "record_count",
            "exit_codes",
            "status_codes",
        )
    }
    values.update(receipt_updates)
    malformed = HostedInspection(
        receipts=(HostedReceipt(**values),),
        scanner_version=HOSTED_SCANNER_VERSION,
    )

    result = scan_hosted_surface(surface, FakeHostedClient(malformed), _policy(**policy_updates))

    assert f"{blocker}:{surface}" in result.blockers


def test_hosted_record_budget_is_cumulative_across_expected_receipts() -> None:
    surface = "render_build_and_runtime_logs"
    inspection = HostedInspection(
        receipts=tuple(
            HostedReceipt(
                name=name,
                chunks=(b"[]",),
                complete=True,
                page_count=1,
                record_count=2,
                status_codes=(200,),
            )
            for name in HOSTED_RECEIPT_INVENTORY[surface]
        ),
        scanner_version=HOSTED_SCANNER_VERSION,
    )

    result = scan_hosted_surface(
        surface,
        FakeHostedClient(inspection),
        _policy(max_hosted_records=3),
    )

    assert f"hosted_total_record_limit:{surface}" in result.blockers


def test_malformed_hosted_receipt_numbers_fail_closed_without_raising() -> None:
    surface = "marketplace_snapshot"
    inspection = HostedInspection(
        receipts=(
            HostedReceipt(
                name=HOSTED_RECEIPT_INVENTORY[surface][0],
                chunks=(b"[]",),
                complete=True,
                page_count="one",  # type: ignore[arg-type]
                record_count=0,
                status_codes=(200,),
            ),
        ),
        scanner_version=HOSTED_SCANNER_VERSION,
    )

    result = scan_hosted_surface(surface, FakeHostedClient(inspection), _policy())

    assert result.blockers == (f"hosted_receipt_malformed:{surface}",)


def test_hosted_client_exception_and_malformed_result_use_constant_codes() -> None:
    surface = "public_mcp_responses"
    raw_message = "provider payload must not survive"

    failed = scan_hosted_surface(surface, FakeHostedClient(RuntimeError(raw_message)), _policy())

    class MalformedClient:
        def inspect(self, _surface: str, _policy: SecretScanPolicy) -> object:
            return object()

    malformed = scan_hosted_surface(surface, MalformedClient(), _policy())  # type: ignore[arg-type]

    assert failed.blockers == (f"hosted_inspection_failed:{surface}",)
    assert malformed.blockers == (f"hosted_inspection_malformed:{surface}",)
    assert raw_message not in failed.model_dump_json()


def test_http_transport_never_buffers_past_the_caller_byte_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Response:
        status_code = 200
        headers: dict[str, str] = {}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def iter_bytes(self, chunk_size: int | None = None):
            assert chunk_size == 64 * 1024
            yield b"a" * 16

    class Client:
        def __enter__(self) -> Client:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def stream(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    monkeypatch.setattr(hosted_module.httpx, "Client", lambda **_kwargs: Client())

    response = hosted_module.HttpxHostedTransport().request(
        "GET",
        "https://hosted.example/data",
        headers={},
        max_bytes=5,
    )

    assert response.body == b"a" * 5


def test_gh_adapter_uses_compiled_release_asset_inventory_and_downloads_content() -> None:
    raw_value = "gh" + "p_" + "E1f2G3h4I5j6K7l8M9n0P1q2R3s4"
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(
        archive_buffer,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        _write_regular_zip_member(archive, "release/config.txt", raw_value)
    releases_route = "repos/example/mercury-tools/releases?per_page=100"
    assets_route = "repos/example/mercury-tools/releases/11/assets?per_page=100"
    download_route = "repos/example/mercury-tools/releases/assets/22"
    runner = FakeCommandRunner(
        {
            releases_route: CommandResult(
                exit_code=0,
                stdout=json.dumps([{"id": 11}]).encode(),
                stderr=b"",
            ),
            assets_route: CommandResult(
                exit_code=0,
                stdout=json.dumps([{"id": 22}]).encode(),
                stderr=b"",
            ),
            download_route: CommandResult(
                exit_code=0,
                stdout=archive_buffer.getvalue(),
                stderr=b"",
            ),
        }
    )
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo="example/mercury-tools",
    )

    inspection = client.inspect("github_releases_and_assets", _policy())
    result = scan_hosted_surface("github_releases_and_assets", client, _policy())

    assert tuple(receipt.name for receipt in inspection.receipts) == HOSTED_RECEIPT_INVENTORY[
        "github_releases_and_assets"
    ]
    assert all("--paginate" not in call and "--slurp" not in call for call in runner.calls)
    assert any(download_route in call for call in runner.calls)
    assert any(finding.rule == "provider_token" for finding in result.findings)
    assert raw_value not in result.model_dump_json()


def test_gh_adapter_rejects_a_page_over_the_record_budget() -> None:
    route = "repos/example/mercury-tools/git/matching-refs/pull/?per_page=100"
    runner = FakeCommandRunner(
        {route: CommandResult(0, b'[[{"ref":"one"},{"ref":"two"}]]', b"")}
    )
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo="example/mercury-tools",
    )

    result = scan_hosted_surface(
        "github_pull_request_refs",
        client,
        _policy(max_hosted_page_records=1, max_hosted_records=10),
    )

    assert "hosted_receipt_incomplete:github_pull_request_refs" in result.blockers


@pytest.mark.parametrize(
    ("duplicate", "policy_updates", "blocker"),
    [
        (
            True,
            {},
            "hosted_archive_duplicate_member:github_releases_and_assets",
        ),
        (
            False,
            {"max_archive_member_bytes": 8, "max_archive_uncompressed_bytes": 10},
            "hosted_archive_uncompressed_limit:github_releases_and_assets",
        ),
    ],
)
def test_hosted_archive_receipts_preflight_aliases_and_uncompressed_budget(
    duplicate: bool,
    policy_updates: dict[str, object],
    blocker: str,
) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if duplicate:
            _write_regular_zip_member(archive, "same/path.txt", b"safe")
            _write_regular_zip_member(archive, "same//path.txt", b"safe alias")
        else:
            _write_regular_zip_member(archive, "one.txt", b"a" * 6)
            _write_regular_zip_member(archive, "two.txt", b"b" * 6)
    surface = "github_releases_and_assets"
    inspection = HostedInspection(
        receipts=(
            HostedReceipt(
                name="github_releases_query",
                complete=True,
                page_count=1,
                record_count=1,
                exit_codes=(0,),
            ),
            HostedReceipt(
                name="github_release_assets_query",
                complete=True,
                page_count=1,
                record_count=1,
                request_count=1,
                parent_record_count=1,
                exit_codes=(0,),
            ),
            HostedReceipt(
                name="github_release_assets_download",
                chunks=(buffer.getvalue(),),
                object_boundaries=(
                    hosted_module.HostedObjectBoundary(1, len(buffer.getvalue())),
                ),
                complete=True,
                page_count=1,
                record_count=1,
                request_count=1,
                parent_record_count=1,
                exit_codes=(0,),
            ),
        ),
        scanner_version=HOSTED_SCANNER_VERSION,
    )

    result = scan_hosted_surface(
        surface,
        FakeHostedClient(inspection),
        _policy(**policy_updates),
    )

    assert blocker in result.blockers


def test_gh_wiki_download_rejects_clone_without_object_inventory() -> None:
    oid = b"a" * 40
    runner = FakeCommandRunner(
        {
            "ls-remote": CommandResult(
                0,
                oid + b"\tHEAD\n" + oid + b"\trefs/heads/main\n",
                b"",
            ),
            "clone": CommandResult(0, b"", b""),
        }
    )
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo="example/mercury-tools",
    )

    query, download = client._wiki_receipts(_policy())

    assert query.complete is True
    assert download.complete is False
    assert not any(call[:2] == ("git", "log") for call in runner.calls)


def test_gh_adapter_proves_fixed_pr_actions_packages_pages_wiki_empty_receipts() -> None:
    repo = "example/mercury-tools"
    responses = {
        f"repos/{repo}/git/matching-refs/pull/?per_page=100": CommandResult(
            0, b"[]", b""
        ),
        f"repos/{repo}/actions/runs?per_page=100": CommandResult(
            0, b'{"total_count":0,"workflow_runs":[]}', b""
        ),
        f"repos/{repo}/actions/artifacts?per_page=100": CommandResult(
            0, b'{"total_count":0,"artifacts":[]}', b""
        ),
        f"repos/{repo}/actions/caches?per_page=100": CommandResult(
            0, b'{"total_count":0,"actions_caches":[]}', b""
        ),
        f"repos/{repo}": CommandResult(
            0, b'{"has_pages":false,"has_wiki":false}', b""
        ),
    }
    responses.update(
        {
                f"users/example/packages?package_type={package_type}&per_page=100": CommandResult(
                    0, b"[]", b""
            )
            for package_type in ("container", "docker", "maven", "npm", "nuget", "rubygems")
        }
    )
    runner = FakeCommandRunner(responses)
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=runner,
        repo=repo,
    )

    for surface in (
        "github_pull_request_refs",
        "github_actions_logs_artifacts_caches",
        "github_packages_pages_wiki",
    ):
        inspection = client.inspect(surface, _policy())
        result = scan_hosted_surface(surface, client, _policy())
        assert tuple(receipt.name for receipt in inspection.receipts) == (
            HOSTED_RECEIPT_INVENTORY[surface]
        )
        assert result.blockers == ()


def test_render_adapter_proves_build_and_runtime_pagination_without_leaking_token() -> None:
    token = "render-operator-token"
    transport = FakeHttpTransport(
        {
            "type=build": HostedHttpResponse(200, b'{"logs":[]}', {}),
            "type=runtime": HostedHttpResponse(200, b'{"logs":[]}', {}),
        }
    )
    client = RenderHostedClient(
        api_url="https://api.render.example",
        service_id="srv-safe",
        token=token,
        transport=transport,
    )

    result = scan_hosted_surface("render_build_and_runtime_logs", client, _policy())

    assert result.blockers == ()
    assert len(transport.calls) == 2
    assert all(call["headers"] == {"Authorization": f"Bearer {token}"} for call in transport.calls)
    assert token not in result.model_dump_json()


def test_render_adapter_follows_provider_cursor_to_completion() -> None:
    def handler(call: dict[str, object]) -> HostedHttpResponse:
        url = str(call["url"])
        if "type=runtime" in url:
            payload = {"logs": []}
        elif "cursor=next-page" in url:
            payload = {"logs": [{"message": "second"}]}
        else:
            payload = {"logs": [{"message": "first"}], "nextCursor": "next-page"}
        return HostedHttpResponse(200, json.dumps(payload).encode(), {})

    transport = CallbackHttpTransport(handler)
    client = RenderHostedClient(
        api_url="https://api.render.example",
        service_id="srv-safe",
        token="operator-token",
        transport=transport,
    )

    result = scan_hosted_surface("render_build_and_runtime_logs", client, _policy())

    assert result.blockers == ()
    build_calls = [call for call in transport.calls if "type=build" in str(call["url"])]
    assert len(build_calls) == 2


def test_render_adapter_rejects_a_page_over_the_record_budget() -> None:
    transport = FakeHttpTransport(
        {
            "type=build": HostedHttpResponse(200, b'{"logs":[{},{}]}', {}),
            "type=runtime": HostedHttpResponse(200, b'{"logs":[]}', {}),
        }
    )
    client = RenderHostedClient(
        api_url="https://api.render.example",
        service_id="srv-safe",
        token="operator-token",
        transport=transport,
    )

    result = scan_hosted_surface(
        "render_build_and_runtime_logs",
        client,
        _policy(max_hosted_page_records=1, max_hosted_records=10),
    )

    assert "hosted_receipt_incomplete:render_build_and_runtime_logs" in result.blockers


def test_supabase_adapter_queries_knowledge_lists_storage_and_downloads_every_object() -> None:
    token = "supabase-operator-token"
    transport = FakeHttpTransport(
        {
            "/rest/v1/knowledge": HostedHttpResponse(
                200,
                b"[]",
                {"Content-Range": "*/0"},
            ),
            "/storage/v1/object/list/public": HostedHttpResponse(
                200,
                b'[{"name":"safe.txt"}]',
                {"Content-Range": "0-0/1"},
            ),
            "/storage/v1/object/authenticated/public/safe.txt": HostedHttpResponse(
                200,
                b"safe storage content",
                {},
            ),
        }
    )
    client = SupabaseHostedClient(
        base_url="https://project.supabase.example",
        service_key=token,
        knowledge_tables=("knowledge",),
        storage_buckets=("public",),
        transport=transport,
    )

    result = scan_hosted_surface("supabase_knowledge_and_storage", client, _policy())

    assert result.blockers == ()
    assert len(transport.calls) == 3
    assert all(call["headers"]["apikey"] == token for call in transport.calls)  # type: ignore[index]
    assert token not in result.model_dump_json()


def test_supabase_storage_paginates_to_a_proven_short_final_page_before_downloads() -> None:
    objects = [{"name": "one.txt"}, {"name": "two.txt"}, {"name": "three.txt"}]

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        url = str(call["url"])
        if "/rest/v1/knowledge" in url:
            return HostedHttpResponse(200, b"[]", {"Content-Range": "*/0"})
        if "/storage/v1/object/list/public" in url:
            body = call["json_body"]
            assert isinstance(body, dict)
            offset = body["offset"]
            page = objects[offset : offset + body["limit"]]
            end = offset + len(page) - 1
            return HostedHttpResponse(
                200,
                json.dumps(page).encode(),
                {"Content-Range": f"{offset}-{end}/{len(objects)}"},
            )
        if "/storage/v1/object/authenticated/public/" in url:
            return HostedHttpResponse(200, b"safe", {})
        raise AssertionError("unexpected_http_request")

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
    list_calls = [
        call for call in transport.calls if "/storage/v1/object/list/public" in str(call["url"])
    ]
    assert [call["json_body"]["offset"] for call in list_calls] == [0, 2]  # type: ignore[index]
    download_calls = [
        call
        for call in transport.calls
        if "/storage/v1/object/authenticated/public/" in str(call["url"])
    ]
    assert len(download_calls) == 3


def test_supabase_adapter_rejects_a_server_page_over_the_requested_record_budget() -> None:
    transport = FakeHttpTransport(
        {
            "/rest/v1/knowledge": HostedHttpResponse(200, b"[{},{}]", {}),
            "/storage/v1/object/list/public": HostedHttpResponse(200, b"[]", {}),
        }
    )
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
        _policy(max_hosted_page_records=1, max_hosted_records=10),
    )

    assert "hosted_receipt_incomplete:supabase_knowledge_and_storage" in result.blockers


def test_marketplace_and_public_mcp_adapters_scan_fixed_http_receipts() -> None:
    tools = _public_tool_records()
    marketplace_transport = FakeHttpTransport(
        {"snapshot": HostedHttpResponse(200, b'[{"name":"mercury-finance"}]', {})}
    )
    def mcp_handler(call: dict[str, object]) -> HostedHttpResponse:
        body = call["json_body"]
        assert isinstance(body, dict)
        if body["method"] == "notifications/initialized":
            return HostedHttpResponse(202, b"", {})
        result = (
            _valid_initialize_result()
            if body["method"] == "initialize"
            else {"tools": tools}
        )
        return HostedHttpResponse(
            200,
            json.dumps(
                {"jsonrpc": "2.0", "id": body["id"], "result": result}
            ).encode(),
            {},
        )

    mcp_transport = CallbackHttpTransport(mcp_handler)
    marketplace = MarketplaceHostedClient(
        snapshot_url="https://marketplace.example/snapshot",
        transport=marketplace_transport,
    )
    mcp = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token="mcp-operator-token",
        transport=mcp_transport,
    )

    marketplace_result = scan_hosted_surface("marketplace_snapshot", marketplace, _policy())
    mcp_result = scan_hosted_surface("public_mcp_responses", mcp, _policy())

    assert marketplace_result.blockers == ()
    assert mcp_result.blockers == ()
    assert len(mcp_transport.calls) == len(HOSTED_RECEIPT_INVENTORY["public_mcp_responses"])
    assert "mcp-operator-token" not in mcp_result.model_dump_json()


def test_public_mcp_adapter_follows_tools_cursor_until_all_20_tools_are_scanned() -> None:
    tools = _public_tool_records()
    first_tools = tools[:10]
    second_tools = tools[10:]

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        body = call["json_body"]
        assert isinstance(body, dict)
        method = body["method"]
        parameters = body["params"]
        if method == "notifications/initialized":
            return HostedHttpResponse(202, b"", {})
        if method == "initialize":
            result = _valid_initialize_result()
        elif method == "tools/list" and parameters == {}:
            result = {"tools": first_tools, "nextCursor": "next-page"}
        elif method == "tools/list" and parameters == {"cursor": "next-page"}:
            result = {"tools": second_tools}
        else:
            result = {}
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return HostedHttpResponse(200, json.dumps(payload).encode(), {})

    transport = CallbackHttpTransport(handler)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    result = scan_hosted_surface("public_mcp_responses", client, _policy())

    assert result.blockers == ()
    tool_calls = [
        call
        for call in transport.calls
        if isinstance(call["json_body"], dict)
        and call["json_body"]["method"] == "tools/list"  # type: ignore[index]
    ]
    assert len(tool_calls) == 2


def test_public_mcp_adapter_rejects_a_tools_page_over_the_record_budget() -> None:
    tools = _public_tool_records()

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        body = call["json_body"]
        assert isinstance(body, dict)
        if body["method"] == "notifications/initialized":
            return HostedHttpResponse(202, b"", {})
        result = (
            _valid_initialize_result()
            if body["method"] == "initialize"
            else {"tools": tools}
        )
        payload = {"jsonrpc": "2.0", "id": body["id"], "result": result}
        return HostedHttpResponse(200, json.dumps(payload).encode(), {})

    transport = CallbackHttpTransport(handler)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token=None,
        transport=transport,
    )

    result = scan_hosted_surface(
        "public_mcp_responses",
        client,
        _policy(max_hosted_page_records=10, max_hosted_records=20),
    )

    assert "hosted_receipt_incomplete:public_mcp_responses" in result.blockers


def test_public_mcp_adapter_handles_sse_session_handshake_without_persisting_session() -> None:
    tools = _public_tool_records()
    session_id = "private-session-id"

    def handler(call: dict[str, object]) -> HostedHttpResponse:
        if call["method"] == "DELETE":
            headers = call["headers"]
            assert isinstance(headers, dict)
            assert headers["Mcp-Session-Id"] == session_id
            return HostedHttpResponse(204, b"", {})
        body = call["json_body"]
        assert isinstance(body, dict)
        method = body["method"]
        headers = call["headers"]
        assert isinstance(headers, dict)
        if method == "initialize":
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": _valid_initialize_result(),
                }
            )
            return HostedHttpResponse(
                200,
                f"event: message\ndata: {payload}\n\n".encode(),
                {"mcp-session-id": session_id},
            )
        assert headers["Mcp-Session-Id"] == session_id
        if method == "notifications/initialized":
            return HostedHttpResponse(202, b"", {})
        if method == "tools/list":
            return HostedHttpResponse(
                200,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": body["id"],
                        "result": {"tools": tools},
                    }
                ).encode(),
                {},
            )
        raise AssertionError("unexpected_mcp_method")

    transport = CallbackHttpTransport(handler)
    client = PublicMcpHostedClient(
        endpoint="https://public.example/mcp",
        token="operator-token",
        transport=transport,
    )

    result = scan_hosted_surface("public_mcp_responses", client, _policy())

    assert result.blockers == ()
    assert session_id not in result.model_dump_json()
    assert [
        call["json_body"].get("method") if isinstance(call["json_body"], dict) else None
        for call in transport.calls
    ] == [
        "initialize",
        "notifications/initialized",
        "tools/list",
        None,
    ]


def test_adapter_factory_instantiates_every_hosted_surface_from_secret_safe_config() -> None:
    token = "operator-token-that-must-not-render"
    config = HostedAdapterConfig(
        repo="example/mercury-tools",
        gh_executable=Path("/mock/gh"),
        github_token=token,
        marketplace_url="https://marketplace.example/snapshot",
        render_api_url="https://api.render.example",
        render_service_id="srv-safe",
        render_token=token,
        supabase_url="https://project.supabase.example",
        supabase_key=token,
        supabase_knowledge_tables=("knowledge",),
        supabase_storage_buckets=("public",),
        public_mcp_url="https://public.example/mcp",
        public_mcp_token=token,
    )

    clients = build_hosted_clients(
        config,
        command_runner=FakeCommandRunner({}),
        http_transport=FakeHttpTransport({}),
    )

    assert set(clients) == set(HOSTED_PUBLIC_SURFACES)
    assert token not in repr(config)
    assert token not in repr(clients)


def test_adapter_factory_requires_a_trusted_git_runner_in_release_mode() -> None:
    with pytest.raises(ReleaseGateError, match="^release_git_runner_required$"):
        build_hosted_clients(
            HostedAdapterConfig(repo="example/mercury-tools"),
            require_trusted_git_runner=True,
        )


def test_wiki_git_query_uses_the_dedicated_git_runner() -> None:
    command_runner = FakeCommandRunner({})
    git_runner = FakeCommandRunner({"git": CommandResult(127, b"", b"")})
    client = GhApiHostedClient(
        executable=Path("/mock/gh"),
        command_runner=command_runner,
        git_command_runner=git_runner,
        repo="example/mercury-tools",
    )

    query, download = client._wiki_receipts(_policy())

    assert query.complete is False
    assert download.complete is False
    assert command_runner.calls == []
    assert git_runner.calls == [("git", "ls-remote", "https://github.com/example/mercury-tools.wiki.git")]


def test_all_ten_surfaces_are_required_for_a_passing_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        scanner_module,
        "scan_git_repository",
        lambda *_args, **_kwargs: GitRepositoryScanResult(
            evidence_hashes=("a" * 64,),
            exit_codes=(0,),
            object_count=4,
            blob_count=1,
        ),
    )
    monkeypatch.setattr(
        scanner_module,
        "scan_artifacts",
        lambda *_args, **_kwargs: ArtifactScanResult(kinds=tuple(ArtifactKind)),
    )
    runner = FakeCommandRunner(
        {
            "gitleaks": CommandResult(
                exit_code=0,
                stdout=f"gitleaks {PINNED_SCANNER_VERSIONS['gitleaks']}".encode(),
                stderr=b"",
            ),
            "trufflehog": CommandResult(
                exit_code=0,
                stdout=f"trufflehog {PINNED_SCANNER_VERSIONS['trufflehog']}".encode(),
                stderr=b"",
            ),
        }
    )

    report = scan_public_release(
        _request(tmp_path),
        command_runner=runner,
        hosted_clients=_hosted_clients(),
    )

    assert report.passed is True
    assert tuple(surface.surface for surface in report.surfaces) == REQUIRED_PUBLIC_SURFACES
    assert all(surface.status == "passed" for surface in report.surfaces)


def test_missing_required_hosted_client_blocks_full_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/mock/{name}")
    monkeypatch.setattr(
        scanner_module,
        "scan_git_repository",
        lambda *_args, **_kwargs: GitRepositoryScanResult(),
    )
    monkeypatch.setattr(
        scanner_module,
        "scan_artifacts",
        lambda *_args, **_kwargs: ArtifactScanResult(kinds=tuple(ArtifactKind)),
    )
    clients = _hosted_clients()
    clients.pop("public_mcp_responses")
    runner = FakeCommandRunner(
        {
            "gitleaks": CommandResult(
                exit_code=0,
                stdout=f"gitleaks {PINNED_SCANNER_VERSIONS['gitleaks']}".encode(),
                stderr=b"",
            ),
            "trufflehog": CommandResult(
                exit_code=0,
                stdout=f"trufflehog {PINNED_SCANNER_VERSIONS['trufflehog']}".encode(),
                stderr=b"",
            ),
        }
    )

    report = scan_public_release(
        _request(tmp_path),
        command_runner=runner,
        hosted_clients=clients,
    )
    serialized = json.dumps(report.public_dict(), sort_keys=True)

    assert report.passed is False
    assert "hosted_client_missing:public_mcp_responses" in report.blockers
    assert "/mock" not in serialized


def test_cli_wires_concrete_hosted_clients_from_env_names_without_token_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from mercury_tools import cli

    root = Path(__file__).resolve().parents[1]
    raw_token = "operator-token-not-for-output"
    captured: dict[str, Any] = {}

    def fake_build(config: HostedAdapterConfig, **_kwargs: object) -> dict[str, object]:
        captured["config"] = config
        return {surface: object() for surface in HOSTED_PUBLIC_SURFACES}

    def fake_scan(
        _request: SecretScanRequest,
        *,
        hosted_clients: Mapping[str, object],
        **_kwargs: object,
    ) -> object:
        captured["clients"] = hosted_clients
        return build_blocked_report("test_blocker")

    monkeypatch.setattr(hosted_module, "build_hosted_clients", fake_build)
    monkeypatch.setattr(scanner_module, "scan_public_release", fake_scan)
    monkeypatch.setenv("TASK13_GITHUB_TOKEN", raw_token)
    monkeypatch.setenv("TASK13_RENDER_TOKEN", raw_token)
    monkeypatch.setenv("TASK13_SUPABASE_KEY", raw_token)
    monkeypatch.setenv("TASK13_MCP_TOKEN", raw_token)
    monkeypatch.setattr(shutil, "which", lambda _name: "/mock/gh")

    exit_code = cli.main(
        [
            "release",
            "scan-secrets",
            "--all-history",
            "--hosted",
            "--artifacts",
            str(tmp_path / "dist"),
            "--repo",
            "example/mercury-tools",
            "--manifest",
            str(root / "docs/release/public-surface-manifest.json"),
            "--allowlist",
            str(root / "docs/release/secret-scan-allowlist.json"),
            "--github-token-env",
            "TASK13_GITHUB_TOKEN",
            "--marketplace-snapshot-url",
            "https://marketplace.example/snapshot",
            "--render-api-url",
            "https://api.render.example",
            "--render-service-id",
            "srv-safe",
            "--render-token-env",
            "TASK13_RENDER_TOKEN",
            "--supabase-url",
            "https://project.supabase.example",
            "--supabase-key-env",
            "TASK13_SUPABASE_KEY",
            "--supabase-knowledge-table",
            "knowledge",
            "--supabase-storage-bucket",
            "public",
            "--public-mcp-url",
            "https://public.example/mcp",
            "--public-mcp-token-env",
            "TASK13_MCP_TOKEN",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert set(captured["clients"]) == set(HOSTED_PUBLIC_SURFACES)
    assert raw_token not in json.dumps(payload, sort_keys=True)
    assert raw_token not in repr(captured["config"])


def test_missing_scanners_never_invoke_injected_hosted_clients(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    clients = _hosted_clients()

    report = scan_public_release(_request(tmp_path), hosted_clients=clients)

    assert report.passed is False
    assert all(client.calls == [] for client in clients.values())
