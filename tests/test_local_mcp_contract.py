from __future__ import annotations

import os
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.shared.memory import create_connected_server_and_client_session

from mercury_tools.catalog.cache import CatalogCache
from mercury_tools.catalog.local_store import LocalCatalogStore
from mercury_tools.cloud.client import CatalogFetchResult, CloudBrainClient
from mercury_tools.drivers.models import ConnectorResult
from mercury_tools.execution.executor import ExecutionPolicyError
from mercury_tools.execution.store import RequestStateError
from mercury_tools.local.audit import AuditLedger
from mercury_tools.local.credentials import CredentialStore
from mercury_tools.local.repository import ensure_repository_state
from mercury_tools.mcp import local_server
from mercury_tools.mcp.local_runtime import LocalMercuryRuntime
from mercury_tools.mcp.local_server import audit_resource, execute_erp_write, local_mcp

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
EXPECTED_RESOURCE_URIS = {
    "mercury://wiki/index",
    "mercury://wiki/doc/{document_id}",
    "mercury://skills/{skill_id}",
    "mercury://connectors",
    "mercury://audit/{event_id}",
}
EXPECTED_PROMPTS = {
    "company_health_check_th",
    "vat_summary_th",
    "invoice_review_th",
    "management_report_th",
    "connector_setup_guide_th",
}
READ_ONLY_TOOLS = {
    "search_knowledge",
    "retrieve_context_pack",
    "get_document",
    "connector_status",
    "run_accounting_skill",
    "list_workspace_flows",
    "search_erp_actions",
    "get_erp_action_schema",
    "run_erp_read",
    "get_erp_request_status",
    "list_connector_drivers",
    "credential_status",
}
NON_DESTRUCTIVE_WRITE_TOOLS = {
    "run_mercury_flow",
    "save_workspace_flow",
    "run_workspace_flow",
    "preview_erp_write",
    "confirm_erp_write",
    "import_erp_spec",
}


async def _roots_callback(root: Path) -> types.ListRootsResult:
    return types.ListRootsResult(roots=[types.Root(uri=root.as_uri(), name="repository")])


@pytest.mark.asyncio
async def test_local_mcp_has_exact_server_contract_and_hidden_context_schema() -> None:
    assert local_mcp.name == "Mercury Finance"

    async with create_connected_server_and_client_session(local_mcp) as session:
        tools = (await session.list_tools()).tools
        resources = (await session.list_resources()).resources
        templates = (await session.list_resource_templates()).resourceTemplates
        prompts = (await session.list_prompts()).prompts

    by_name = {tool.name: tool for tool in tools}
    assert set(by_name) == EXPECTED_TOOLS
    assert {str(resource.uri) for resource in resources} | {
        template.uriTemplate for template in templates
    } == EXPECTED_RESOURCE_URIS
    assert {prompt.name for prompt in prompts} == EXPECTED_PROMPTS
    assert all(not prompt.arguments for prompt in prompts)
    assert all("ctx" not in tool.inputSchema.get("properties", {}) for tool in tools)
    assert all("context" not in tool.inputSchema.get("properties", {}) for tool in tools)

    for name in READ_ONLY_TOOLS:
        assert by_name[name].annotations.readOnlyHint is True
        assert by_name[name].annotations.destructiveHint is False
    for name in NON_DESTRUCTIVE_WRITE_TOOLS:
        assert by_name[name].annotations.readOnlyHint is False
        assert by_name[name].annotations.destructiveHint is False
    assert by_name["execute_erp_write"].annotations.readOnlyHint is False
    assert by_name["execute_erp_write"].annotations.destructiveHint is True
    assert {
        "preview_flowaccount_journal",
        "create_flowaccount_journal_draft",
        "approve_flowaccount_journal",
    }.isdisjoint(by_name)


@pytest.mark.asyncio
async def test_real_stdio_initialize_and_tools_list(tmp_path: Path) -> None:
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mercury_tools.cli", "mcp", "serve-local"],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    async with (
        stdio_client(parameters) as (read_stream, write_stream),
        ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=10),
            list_roots_callback=lambda _context: _roots_callback(tmp_path),
        ) as session,
    ):
        initialized = await session.initialize()
        listed = await session.list_tools()

    assert initialized.serverInfo.name == "Mercury Finance"
    assert {tool.name for tool in listed.tools} == EXPECTED_TOOLS


@pytest.mark.asyncio
async def test_local_flow_preview_uses_executor_policy_not_hosted_capability_gate(
    tmp_path: Path,
) -> None:
    preview_calls: list[tuple[str, dict[str, object], str]] = []

    class FakeRuntime:
        repository = SimpleNamespace(root=tmp_path)

        async def refresh_catalog(self) -> None:
            return None

        def connector_summaries(self):
            return []

        async def get_document(self, _document_id: str):
            return None

        async def run_accounting_skill(self, *_args, **_kwargs):
            return {"status": "ok"}

        async def run_read(self, *_args):
            return {"status": "ok"}

        async def preview_write(
            self,
            action_id: str,
            inputs: dict[str, object],
            environment: str,
        ) -> dict[str, str]:
            preview_calls.append((action_id, inputs, environment))
            return {"request_id": "req_local_preview", "payload_hash": "a" * 64}

    result = await local_server._run_local_flow(
        FakeRuntime(),
        flow_yaml="""
name: Local Preview
---
- erpWritePreview:
    capability: documents.invoice.create
    actionId: erp.invoice.create
    inputs:
      body:
        reference: PREVIEW-001
    saveAs: preview
""",
        flow_path=None,
        env=None,
        dry_run=False,
    )

    assert result["status"] == "confirmation_required"
    assert preview_calls == [
        ("erp.invoice.create", {"body": {"reference": "PREVIEW-001"}}, "production")
    ]


@pytest.mark.asyncio
async def test_local_flow_taint_blocks_cloud_adapter_without_erp_payload_leak(
    tmp_path: Path,
) -> None:
    cloud_calls: list[str] = []

    class FakeRuntime:
        repository = SimpleNamespace(root=tmp_path)

        async def refresh_catalog(self) -> None:
            return None

        def connector_summaries(self):
            return []

        async def get_document(self, _document_id: str):
            cloud_calls.append("document")
            return None

        async def run_accounting_skill(self, *_args, **_kwargs):
            cloud_calls.append("skill")
            return {"status": "ok"}

        async def run_read(self, *_args):
            return {
                "status": "ok",
                "result": {"reference": "erp-private-invoice-2026"},
            }

        async def preview_write(self, *_args):
            return {"request_id": "req_unused", "payload_hash": "a" * 64}

        async def search_knowledge(self, query: str, **_kwargs):
            cloud_calls.append(query)
            return ()

    result = await local_server._run_local_flow(
        FakeRuntime(),
        flow_yaml="""
name: ERP Taint Local Flow
---
- erpRead:
    actionId: erp.invoice.list
    saveAs: erp
- searchKnowledge:
    query: "${erp.result.reference}"
""",
        flow_path=None,
        env=None,
        dry_run=False,
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "erp_to_cloud_taint"
    assert cloud_calls == []
    assert "erp-private-invoice-2026" not in str(result)


def _public_read_action(action_factory, **overrides):
    values = {
        "method": "GET",
        "path_template": "/company",
        "operation_id": "getCompany",
        "capability": "company.info.read",
        "risk_tier": 0,
        "required_confirmations": 0,
        "side_effects": (),
        "input_schema": {
            "path": {},
            "query": {},
            "headers": {},
            "body": {},
            "files": {},
        },
        "examples": (),
        "idempotency": {},
        "success_rules": {},
        "error_rules": {},
        "response_redaction": (),
    }
    values.update(overrides)
    return action_factory(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["offline", "not_modified", "server_error"])
async def test_runtime_refresh_uses_global_cache_and_merges_local_overlay_in_memory(
    repository_context,
    catalog_source,
    action_factory,
    failure: str,
) -> None:
    global_action = _public_read_action(action_factory)
    local_action = _public_read_action(
        action_factory,
        connector_id="peak",
        path_template="/contacts",
        operation_id="listContacts",
        capability="contacts.read",
    )
    cache = CatalogCache(repository_context)
    cache.replace_global([global_action], etag='"catalog-v1"')
    LocalCatalogStore(repository_context).write_import(catalog_source, [local_action])
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if failure == "offline":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(304 if failure == "not_modified" else 503)

    runtime = LocalMercuryRuntime.for_repository(repository_context)
    await runtime.cloud.aclose()
    runtime.cloud = CloudBrainClient(
        cache=cache,
        base_url="https://cloud.example.test",
        transport=httpx.MockTransport(handler),
    )
    try:
        await runtime.refresh_catalog()
    finally:
        await runtime.aclose()

    assert [action.action_id for action in runtime.catalog.list()] == [
        global_action.action_id,
        local_action.action_id,
    ]
    assert cache.list_global() == [global_action]
    assert len(seen) == 1
    assert dict(seen[0].url.params) == {}


@pytest.mark.asyncio
async def test_runtime_search_keeps_ambiguous_candidates_without_auto_selection(
    repository_context,
    action_factory,
) -> None:
    first = action_factory(operation_id="first", aliases_en=("record payment",))
    second = action_factory(operation_id="second", aliases_en=("record payment",))

    class FakeCloud:
        async def list_actions(self):
            return CatalogFetchResult(actions=(first, second), source="cache")

        async def search_knowledge(self, *args, **kwargs):
            return ()

        async def aclose(self):
            return None

    runtime = LocalMercuryRuntime.for_repository(repository_context)
    await runtime.cloud.aclose()
    runtime.cloud = FakeCloud()
    try:
        result = await runtime.search_actions("record payment")
    finally:
        await runtime.aclose()

    assert result.ambiguous is True
    assert len(result.matches) == 2


@pytest.mark.asyncio
async def test_runtime_erp_read_enforces_effective_tier_zero_before_executor(
    repository_context,
    action_factory,
) -> None:
    action = action_factory()
    runtime = LocalMercuryRuntime.for_repository(repository_context)
    runtime.catalog.replace([action])
    calls = 0

    async def forbidden(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("executor must not run")

    runtime.executor.run_read = forbidden
    try:
        with pytest.raises(
            ExecutionPolicyError,
            match="erp_read_requires_effective_tier_zero",
        ):
            await runtime.run_read(action.action_id, {}, "production")
    finally:
        await runtime.aclose()

    assert calls == 0


@pytest.mark.asyncio
async def test_preview_hash_confirmation_and_request_status_use_existing_store(
    repository_context,
    catalog_action,
) -> None:
    runtime = LocalMercuryRuntime.for_repository(repository_context)
    runtime.catalog.replace([catalog_action])
    try:
        preview = await runtime.preview_write(catalog_action.action_id, {}, "production")
        with pytest.raises(RequestStateError, match="payload_hash_mismatch"):
            runtime.executor.confirm_write(preview["request_id"], "0" * 64)
        confirmed = runtime.executor.confirm_write(
            preview["request_id"],
            preview["payload_hash"],
        )
        status = runtime.executor.get_request_status(preview["request_id"])
    finally:
        await runtime.aclose()

    assert preview["status"] == "confirmation_required"
    assert confirmed.state.value == "ready_to_execute"
    assert status["state"] == "ready_to_execute"
    assert "request_inputs" not in status


@pytest.mark.asyncio
async def test_execute_tool_refreshes_catalog_before_executor_action_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeExecutor:
        async def execute_write(self, request_id: str) -> ConnectorResult:
            events.append(f"execute:{request_id}")
            return ConnectorResult(
                status="succeeded",
                http_status=200,
                data={"id": "record-1"},
                summary="ok",
                dispatched=True,
            )

    class FakeRuntime:
        executor = FakeExecutor()

        async def refresh_catalog(self) -> None:
            events.append("refresh")

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(
        local_server.LocalMercuryRuntime,
        "for_repository",
        classmethod(lambda cls, context: FakeRuntime()),
    )
    result = await execute_erp_write(
        request_id="req_test",
        ctx=_direct_context(tmp_path),
    )

    assert result["status"] == "succeeded"
    assert events == ["refresh", "execute:req_test", "close"]


@pytest.mark.asyncio
async def test_audit_resource_returns_one_sanitized_event_only(tmp_path: Path) -> None:
    context = ensure_repository_state(tmp_path)
    event_id = AuditLedger(context.audit_dir / "audit.jsonl").record(
        {
            "event": "completed",
            "authorization": "Bearer top-secret",
            "repository_path": str(tmp_path),
            "summary": {"status": "succeeded"},
        }
    )

    payload = await audit_resource(event_id=event_id, ctx=_direct_context(tmp_path))

    decoded = __import__("json").loads(payload)
    assert decoded["status"] == "ok"
    assert decoded["event"]["event_id"] == event_id
    assert "top-secret" not in payload
    assert str(tmp_path) not in payload
    assert "audit.jsonl" not in payload


@pytest.mark.asyncio
async def test_local_import_refreshes_overlay_without_writing_global_cache(
    repository_context,
) -> None:
    spec_path = repository_context.root / "openapi.json"
    spec_path.write_text(
        __import__("json").dumps(
            {
                "openapi": "3.0.0",
                "info": {"title": "Local Books", "version": "1.0.0"},
                "servers": [{"url": "https://api.example.test"}],
                "paths": {
                    "/company": {
                        "get": {
                            "operationId": "getCompany",
                            "responses": {"200": {"description": "OK"}},
                        }
                    }
                },
            }
        )
    )
    runtime = LocalMercuryRuntime.for_repository(repository_context)
    try:
        result = await runtime.import_catalog_spec(
            connector_id="local-books",
            source_path=spec_path,
            source_url=None,
        )
    finally:
        await runtime.aclose()

    assert len(result.actions) == 1
    assert runtime.catalog.require(result.actions[0].action_id) == result.actions[0]
    assert runtime.cache.list_global() == []


@pytest.mark.asyncio
async def test_run_accounting_skill_never_sends_inputs_to_cloud(
    repository_context,
) -> None:
    cloud_calls: list[tuple[str, object]] = []

    class FakeCloud:
        async def get_skill(self, skill_id: str):
            cloud_calls.append(("skill", skill_id))
            return {
                "skill_id": skill_id,
                "title": "VAT Summary",
                "summary": "Reviewed VAT workflow",
            }

        async def search_knowledge(self, query: str, **kwargs):
            cloud_calls.append(("search", query))
            return ()

        async def aclose(self):
            return None

    runtime = LocalMercuryRuntime.for_repository(repository_context)
    await runtime.cloud.aclose()
    runtime.cloud = FakeCloud()
    try:
        result = await runtime.run_accounting_skill(
            "vat-summary-th",
            inputs={"erp_payload": "private-invoice-value"},
            evidence_mode=True,
        )
    finally:
        await runtime.aclose()

    assert result["llm_called"] is False
    assert result["tool_plan"][0] == "connector_status"
    assert all("private-invoice-value" not in str(value) for _, value in cloud_calls)


@pytest.mark.asyncio
async def test_connector_status_reports_field_presence_without_values(
    repository_context,
    action_factory,
) -> None:
    action = _public_read_action(action_factory)
    runtime = LocalMercuryRuntime.for_repository(repository_context)
    runtime.catalog.replace([action])
    driver = runtime.drivers.get("flowaccount")
    fields = driver.credential_fields("production")
    CredentialStore(repository_context).save(
        "flowaccount",
        "production",
        {
            "client_id": "private-client-id",
            "client_secret": "private-client-secret",
        },
        fields,
    )
    try:
        rows = runtime.connector_summaries(
            connector="flowaccount",
            environment="production",
        )
    finally:
        await runtime.aclose()

    assert rows[0]["configured"] is True
    assert rows[0]["present_fields"] == ["client_id", "client_secret"]
    assert rows[0]["requires_safe_probe"] is True
    assert "private-client" not in str(rows)


@pytest.mark.asyncio
async def test_repo_bound_tools_construct_and_close_a_fresh_runtime_per_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[int] = []
    closed: list[int] = []

    class FakeRuntime:
        def __init__(self, identifier: int) -> None:
            self.identifier = identifier

        def credential_summary(self, connector: str, environment: str):
            return {
                "connector_id": connector,
                "environment": environment,
                "runtime_id": self.identifier,
            }

        async def aclose(self) -> None:
            closed.append(self.identifier)

    def build(cls, context):
        identifier = len(created) + 1
        created.append(identifier)
        return FakeRuntime(identifier)

    monkeypatch.setattr(
        local_server.LocalMercuryRuntime,
        "for_repository",
        classmethod(build),
    )
    first = await local_server.credential_status(
        connector="flowaccount",
        environment="production",
        ctx=_direct_context(tmp_path),
    )
    second = await local_server.credential_status(
        connector="flowaccount",
        environment="production",
        ctx=_direct_context(tmp_path),
    )

    assert [first["runtime_id"], second["runtime_id"]] == [1, 2]
    assert created == closed == [1, 2]


@pytest.mark.asyncio
async def test_connector_status_refreshes_catalog_before_combining_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeRuntime:
        async def refresh_catalog(self) -> None:
            events.append("refresh")

        def connector_summaries(self, **kwargs):
            events.append("summaries")
            return [{"connector_id": "flowaccount", "capabilities": ["company.info.read"]}]

        async def aclose(self) -> None:
            events.append("close")

    monkeypatch.setattr(
        local_server.LocalMercuryRuntime,
        "for_repository",
        classmethod(lambda cls, context: FakeRuntime()),
    )
    result = await local_server.connector_status(ctx=_direct_context(tmp_path))

    assert result["connectors"][0]["capabilities"] == ["company.info.read"]
    assert events == ["refresh", "summaries", "close"]


def _direct_context(root: Path):
    class Session:
        async def list_roots(self):
            return SimpleNamespace(roots=[SimpleNamespace(uri=root.as_uri())])

    return SimpleNamespace(session=Session())
