from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.mcp.server import StrictInputFastMCP

WORKSPACE_ID = UUID("12345678-1234-5678-9234-567812345678")
CONNECTION_ID = UUID("87654321-4321-8765-4321-876543218765")
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def _qualification(provider: str, capability_id: str) -> ProviderMCPQualification:
    definition = ProviderMCPQualification.discovered(
        provider=provider,
        environment="sandbox" if provider == "flowaccount" else "uat",
        provider_tool_name="PRIVATE_RAW_PROVIDER_TOOL",
        normalized_capability=capability_id,
        input_schema={
            "type": "object",
            "properties": {"page_size": {"type": "integer", "minimum": 1, "maximum": 50}},
            "required": ["page_size"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"document_number": {"type": "string", "minLength": 1}},
            "required": ["document_number"],
            "additionalProperties": False,
        },
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    return definition.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": "b" * 64,
            "evidence_revision_sha256": "c" * 64,
            "qualification_evidence_uri": (
                "catalog://global/"
                f"{provider}/qualifications/{definition.capability_version_sha256}-{'c' * 64}.json"
            ),
            "evidence_evaluated_at": NOW,
            "evidence_expires_at": NOW + timedelta(days=1),
        }
    )


def _assert_closed(schema: object) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert schema.get("additionalProperties") is False, schema
        for value in schema.values():
            _assert_closed(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_closed(value)


@pytest.mark.asyncio
async def test_generated_read_wrappers_use_stable_mercury_names_and_closed_contracts() -> None:
    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    qualifications = tuple(
        _qualification(provider, capability)
        for provider in ("flowaccount", "peak")
        for capability in (
            "provider_profile.get",
            "documents.invoice.list",
            "documents.invoice.get",
            "documents.invoice.create",
        )
    )
    server = StrictInputFastMCP("Generated provider tools")
    calls: list[dict[str, object]] = []

    async def execute(_context, **kwargs):
        calls.append(kwargs)
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider=kwargs["qualification"].provider,
            company_display_name="Example Company",
            environment=kwargs["qualification"].environment,
            capability_id=kwargs["qualification"].normalized_capability,
            capability_version=kwargs["qualification"].capability_version_sha256,
            data={"document_number": "INV-001"},
        )

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    assert await publisher.publish(qualifications) is True
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == {
        "mercury_flowaccount_provider_profile_get",
        "mercury_flowaccount_invoice_list",
        "mercury_flowaccount_invoice_get",
        "mercury_peak_provider_profile_get",
        "mercury_peak_invoice_list",
        "mercury_peak_invoice_get",
    }
    for tool in tools.values():
        _assert_closed(tool.inputSchema)
        assert tool.outputSchema is not None
        _assert_closed(tool.outputSchema)
        success = tool.outputSchema["$defs"]["Success"]
        data_reference = success["properties"]["data"]["$ref"]
        assert data_reference.startswith("#/$defs/MercuryData")
        assert data_reference.rsplit("/", 1)[-1] in tool.outputSchema["$defs"]
        serialized = str({"input": tool.inputSchema, "output": tool.outputSchema})
        assert "PRIVATE_RAW_PROVIDER_TOOL" not in serialized
        assert "provider_tool_name" not in serialized
        assert "access_token" not in serialized
        assert "inputs" not in tool.inputSchema.get("properties", {})

    context = SimpleNamespace()
    server.get_context = lambda: context
    content, structured = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "page_size": 25,
        },
    )
    assert structured["capability_id"] == "documents.invoice.get"
    assert structured["capability_version"] == qualifications[2].capability_version_sha256
    assert all("PRIVATE_RAW_PROVIDER_TOOL" not in item.text for item in content)
    assert calls[0]["inputs"].model_dump(mode="json") == {"page_size": 25}


@pytest.mark.asyncio
async def test_schema_drift_unpublishes_only_affected_wrapper_and_notifies_tools_list_changed() -> (
    None
):
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    initial = (
        _qualification("flowaccount", "documents.invoice.get"),
        _qualification("flowaccount", "documents.invoice.list"),
    )
    server = StrictInputFastMCP("Generated provider tools")

    async def execute(_context, **_kwargs):
        pytest.fail("execution is not part of this publication test")

    notifications: list[str] = []
    context = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: notifications.append("tools/list_changed")
        )
    )
    publisher = GeneratedProviderToolPublisher(server, execute=execute)

    assert await publisher.publish(initial, context=context) is True
    drifted = initial[0].model_copy(
        update={
            "qualification_state": QualificationState.SUPERSEDED,
            "disable_reason": "schema_changed",
        }
    )
    assert await publisher.publish((drifted, initial[1]), context=context) is True

    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}
    assert notifications == ["tools/list_changed", "tools/list_changed"]


@pytest.mark.asyncio
async def test_generated_wrapper_rejects_unknown_fields_before_execution() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    server = StrictInputFastMCP("Generated provider tools")
    executed = False

    async def execute(_context, **_kwargs):
        nonlocal executed
        executed = True
        raise AssertionError("unknown input must not execute")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((_qualification("flowaccount", "documents.invoice.get"),))
    server.get_context = lambda: SimpleNamespace()

    _content, structured = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "page_size": 25,
            "unexpected": True,
        },
    )
    assert structured["status"] == "error"
    assert structured["error"]["code"] == "validation_failed"
    assert executed is False


@pytest.mark.asyncio
async def test_runtime_schema_drift_removes_affected_wrapper_and_notifies_search() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Generated provider tools")
    notifications: list[str] = []

    async def execute(_context, **kwargs):
        if kwargs["qualification"].normalized_capability == "documents.invoice.get":
            raise ProviderSchemaChanged(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        raise AssertionError("only the drifted wrapper is called")

    context = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: notifications.append("tools/list_changed")
        )
    )
    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get, invoice_list), context=context)
    notifications.clear()
    server.get_context = lambda: context

    _content, structured = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "page_size": 25,
        },
    )

    assert structured["error"]["code"] == "capability_version_changed"
    await asyncio.sleep(0)
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}
    assert notifications == ["tools/list_changed"]
    assert await publisher.publish((invoice_get, invoice_list), context=context) is False
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}


@pytest.mark.asyncio
async def test_v1_refresh_publishes_catalog_generated_reads_without_changing_the_stable_core() -> (
    None
):
    from mercury_tools.mcp.v1_tools import refresh_generated_provider_tools

    qualification = _qualification("flowaccount", "documents.invoice.get")

    class Catalog:
        def list_provider_mcp_qualifications(self):
            return [qualification]

    class Runtime:
        qualification_catalog = Catalog()

        async def aclose(self) -> None:
            return None

    server = StrictInputFastMCP("V1 generated refresh")
    assert (
        await refresh_generated_provider_tools(
            server,
            runtime_factory=lambda: Runtime(),
        )
        is True
    )
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_get"}

    class EmptyCatalog:
        def list_provider_mcp_qualifications(self):
            return []

    class EmptyRuntime:
        qualification_catalog = EmptyCatalog()

        async def aclose(self) -> None:
            return None

    assert (
        await refresh_generated_provider_tools(
            server,
            runtime_factory=lambda: EmptyRuntime(),
        )
        is True
    )
    assert await server.list_tools() == []
