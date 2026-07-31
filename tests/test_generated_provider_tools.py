from __future__ import annotations

import asyncio
import gc
import threading
import time
import weakref
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.mcp.server import StrictInputFastMCP

WORKSPACE_ID = UUID("12345678-1234-5678-9234-567812345678")
CONNECTION_ID = UUID("87654321-4321-8765-4321-876543218765")
NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)
_NOTIFICATION_OUTER_GUARD_SECONDS = 0.5


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
        public_output_field_paths=("/document_number",),
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
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id=kwargs["capability_id"],
            capability_version=kwargs["capability_version"],
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
        success = tool.outputSchema["$defs"]["Success0"]
        assert success["properties"]["capability_version"]["const"]
        assert success["properties"]["data"]["type"] == "object"
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
            "capability_version": qualifications[2].capability_version_sha256,
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
async def test_generated_input_preserves_root_one_of_and_local_references() -> None:
    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="PRIVATE_RAW_PROVIDER_TOOL",
        normalized_capability="documents.invoice.get",
        input_schema={
            "$defs": {
                "ById": {
                    "type": "object",
                    "properties": {
                        "mode": {"const": "by_id"},
                        "invoice_id": {"type": "string"},
                    },
                    "required": ["mode", "invoice_id"],
                    "additionalProperties": False,
                },
                "ByNumber": {
                    "type": "object",
                    "properties": {
                        "mode": {"const": "by_number"},
                        "document_number": {"type": "string"},
                    },
                    "required": ["mode", "document_number"],
                    "additionalProperties": False,
                },
            },
            "oneOf": [{"$ref": "#/$defs/ById"}, {"$ref": "#/$defs/ByNumber"}],
        },
        output_schema={
            "type": "object",
            "properties": {"document_number": {"type": "string"}},
            "required": ["document_number"],
            "additionalProperties": False,
        },
        public_output_field_paths=("/document_number",),
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    qualification = definition.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": "b" * 64,
            "evidence_revision_sha256": "c" * 64,
            "qualification_evidence_uri": (
                "catalog://global/flowaccount/qualifications/"
                f"{definition.capability_version_sha256}-{'c' * 64}.json"
            ),
            "evidence_evaluated_at": NOW,
            "evidence_expires_at": NOW + timedelta(days=1),
        }
    )
    received: list[dict[str, object]] = []

    async def execute(_context, **kwargs):
        received.append(kwargs["inputs"].model_dump(mode="json"))
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id="documents.invoice.get",
            capability_version=kwargs["capability_version"],
            data={"document_number": "INV-001"},
        )

    server = StrictInputFastMCP("Root oneOf generated provider input")
    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    assert await publisher.publish((qualification,)) is True
    tool = (await server.list_tools())[0]
    rendered = str(tool.inputSchema)
    assert "ById" in rendered
    assert "ByNumber" in rendered
    assert "oneOf" in rendered

    server.get_context = lambda: SimpleNamespace()
    _content, structured = await server.call_tool(
        tool.name,
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": qualification.capability_version_sha256,
            "mode": "by_id",
            "invoice_id": "invoice-1",
        },
    )

    assert structured["status"] == "ok"
    assert received == [{"mode": "by_id", "invoice_id": "invoice-1"}]


@pytest.mark.asyncio
async def test_generated_input_preserves_root_if_then_else_runtime_parity() -> None:
    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    branch_properties = {
        "mode": {"enum": ["paged", "cursor"]},
        "page_size": {"type": "integer", "minimum": 1},
        "cursor": {"type": "string", "minLength": 1},
    }
    input_schema = {
        "type": "object",
        "properties": branch_properties,
        "required": ["mode"],
        "additionalProperties": False,
        "if": {
            "type": "object",
            "properties": {
                **branch_properties,
                "mode": {"const": "paged"},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        "then": {
            "type": "object",
            "properties": branch_properties,
            "required": ["mode", "page_size"],
            "additionalProperties": False,
        },
        "else": {
            "type": "object",
            "properties": branch_properties,
            "required": ["mode", "cursor"],
            "additionalProperties": False,
        },
    }
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="PRIVATE_RAW_PROVIDER_TOOL",
        normalized_capability="documents.invoice.list",
        input_schema=input_schema,
        output_schema={
            "type": "object",
            "properties": {"document_number": {"type": "string"}},
            "required": ["document_number"],
            "additionalProperties": False,
        },
        public_output_field_paths=("/document_number",),
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    qualification = definition.model_copy(
        update={
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": "b" * 64,
            "evidence_revision_sha256": "c" * 64,
            "qualification_evidence_uri": (
                "catalog://global/flowaccount/qualifications/"
                f"{definition.capability_version_sha256}-{'c' * 64}.json"
            ),
            "evidence_evaluated_at": NOW,
            "evidence_expires_at": NOW + timedelta(days=1),
        }
    )
    received: list[dict[str, object]] = []

    async def execute(_context, **kwargs):
        received.append(kwargs["inputs"].model_dump(mode="json"))
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id="documents.invoice.list",
            capability_version=kwargs["capability_version"],
            data={"document_number": "INV-001"},
        )

    server = StrictInputFastMCP("Root conditional generated provider input")
    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((qualification,))
    tool = (await server.list_tools())[0]
    rendered = str(tool.inputSchema)
    assert "'if':" in rendered
    assert "'then':" in rendered
    assert "'else':" in rendered
    server.get_context = lambda: SimpleNamespace()

    _content, valid = await server.call_tool(
        tool.name,
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": qualification.capability_version_sha256,
            "mode": "paged",
            "page_size": 25,
        },
    )
    _content, invalid = await server.call_tool(
        tool.name,
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": qualification.capability_version_sha256,
            "mode": "paged",
            "cursor": "not-valid-for-paged",
        },
    )

    assert valid["status"] == "ok"
    assert invalid["error"]["code"] == "validation_failed"
    assert received == [{"mode": "paged", "page_size": 25}]


@pytest.mark.asyncio
async def test_runtime_schema_drift_removes_affected_wrapper_and_notifies_search() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    drifted = invoice_get.model_copy(
        update={
            "qualification_state": QualificationState.DISABLED,
            "disable_reason": "schema_changed",
        }
    )
    server = StrictInputFastMCP("Generated provider tools")
    notifications: list[str] = []

    async def execute(_context, **kwargs):
        if kwargs["capability_id"] == "documents.invoice.get":
            raise ProviderSchemaChanged(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        raise AssertionError("only the drifted wrapper is called")

    async def persist_schema_change(qualification, _context):
        return (
            qualification.model_copy(
                update={
                    "qualification_state": QualificationState.DISABLED,
                    "disable_reason": "schema_changed",
                }
            ),
            invoice_list,
        )

    context = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: notifications.append("tools/list_changed")
        )
    )
    publisher = GeneratedProviderToolPublisher(
        server,
        execute=execute,
        persist_schema_change=persist_schema_change,
    )
    await publisher.publish((invoice_get, invoice_list), context=context)
    notifications.clear()
    server.get_context = lambda: context

    _content, structured = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": invoice_get.capability_version_sha256,
            "page_size": 25,
        },
    )

    assert structured["error"]["code"] == "capability_version_changed"
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}
    assert notifications == ["tools/list_changed"]
    assert await publisher.publish((drifted, invoice_list), context=context) is False
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


@pytest.mark.asyncio
async def test_generated_wrapper_groups_exact_versions_and_projects_only_public_schema() -> None:
    """One Mercury tool selects a reviewed version without exposing provider fields."""

    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    def versioned(maximum: int) -> ProviderMCPQualification:
        definition = ProviderMCPQualification.discovered(
            provider="flowaccount",
            environment="sandbox",
            provider_tool_name="PRIVATE_RAW_PROVIDER_TOOL",
            normalized_capability="documents.invoice.get",
            input_schema={
                "type": "object",
                "properties": {"page_size": {"type": "integer", "maximum": maximum}},
                "required": ["page_size"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "tax_amount": {"type": "number"},
                    "contactEmail": {"type": "string"},
                    "taxId": {"type": "string"},
                    "apiKey": {"type": "string"},
                    "nested": {
                        "type": "object",
                        "properties": {
                            "document_id": {"type": "string"},
                            "sessionToken": {"type": "string"},
                        },
                        "required": ["document_id", "sessionToken"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "invoice_id",
                    "tax_amount",
                    "contactEmail",
                    "taxId",
                    "apiKey",
                    "nested",
                ],
                "additionalProperties": False,
            },
            public_output_field_paths=(
                "/invoice_id",
                "/nested/document_id",
                "/tax_amount",
            ),
            response_shape_hash="a" * 64,
            required_permissions=("documents.read",),
        )
        return definition.model_copy(
            update={
                "qualification_state": QualificationState.ENABLED,
                "company_sha256": "b" * 64,
                "evidence_revision_sha256": "c" * 64,
                "qualification_evidence_uri": (
                    "catalog://global/flowaccount/qualifications/"
                    f"{definition.capability_version_sha256}-{'c' * 64}.json"
                ),
                "evidence_evaluated_at": NOW,
                "evidence_expires_at": NOW + timedelta(days=1),
            }
        )

    first = versioned(50)
    second = versioned(75)
    server = StrictInputFastMCP("Grouped generated provider tools")
    calls: list[dict[str, object]] = []

    async def execute(_context, **kwargs):
        calls.append(kwargs)
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id="documents.invoice.get",
            capability_version=kwargs["capability_version"],
            data={"invoice_id": "INV-1", "tax_amount": 7.0, "nested": {"document_id": "DOC-1"}},
        )

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    duplicate_first = first.model_copy(update={"id": UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")})
    assert await publisher.publish((first, duplicate_first, second)) is True
    tool = (await server.list_tools())[0]
    assert len(tool.inputSchema["oneOf"]) == 2
    rendered = str(tool.outputSchema)
    assert "contactEmail" not in rendered
    assert "taxId" not in rendered
    assert "apiKey" not in rendered
    assert "sessionToken" not in rendered
    assert "invoice_id" in rendered
    assert "tax_amount" in rendered

    server.get_context = lambda: SimpleNamespace()
    _content, structured = await server.call_tool(
        tool.name,
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": second.capability_version_sha256,
            "page_size": 25,
        },
    )

    assert structured["capability_version"] == second.capability_version_sha256
    assert structured["data"] == {
        "invoice_id": "INV-1",
        "tax_amount": 7.0,
        "nested": {"document_id": "DOC-1"},
    }
    assert calls[0]["capability_version"] == second.capability_version_sha256


@pytest.mark.asyncio
async def test_runtime_schema_drift_persists_then_refreshes_the_exact_version() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Persisted generated provider drift")
    transitions: list[str] = []

    async def execute(_context, **_kwargs):
        raise ProviderSchemaChanged(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
        )

    async def persist_schema_change(qualification, _context):
        transitions.append(qualification.capability_version_sha256)
        return (
            qualification.model_copy(
                update={
                    "qualification_state": QualificationState.DISABLED,
                    "disable_reason": "schema_changed",
                }
            ),
            invoice_list,
        )

    publisher = GeneratedProviderToolPublisher(
        server,
        execute=execute,
        persist_schema_change=persist_schema_change,
    )
    await publisher.publish((invoice_get, invoice_list))
    server.get_context = lambda: SimpleNamespace()

    _content, structured = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": invoice_get.capability_version_sha256,
            "page_size": 25,
        },
    )

    assert structured["error"]["code"] == "capability_version_changed"
    assert transitions == [invoice_get.capability_version_sha256]
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}


@pytest.mark.asyncio
async def test_generated_publication_serializes_refreshes_and_notifies_each_refresh_session() -> (
    None
):
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Serialized generated provider publication")

    async def execute(_context, **_kwargs):
        raise AssertionError("publication test must not dispatch")

    first_notifications: list[str] = []
    second_notifications: list[str] = []
    first = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: first_notifications.append("tools/list_changed")
        )
    )
    second = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: second_notifications.append("tools/list_changed")
        )
    )
    publisher = GeneratedProviderToolPublisher(server, execute=execute)

    assert await publisher.publish((invoice_get,), context=first) is True
    await asyncio.gather(
        publisher.publish((invoice_get, invoice_list), context=second),
        publisher.publish((invoice_get,), context=second),
    )

    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_get"}
    assert first_notifications == ["tools/list_changed"] * 3
    assert second_notifications == ["tools/list_changed"] * 2


@pytest.mark.asyncio
async def test_stable_core_only_session_receives_background_tool_list_changed() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    server = StrictInputFastMCP("Stable session generated refresh")

    async def stable_core_tool() -> str:
        return "ok"

    server.add_tool(
        stable_core_tool,
        name="stable_core_tool",
        meta={"mercury/surface": "v1"},
    )

    async def execute(_context, **_kwargs):
        raise AssertionError("session registration test must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    server._mercury_v1_generated_provider_tools = publisher
    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    await publisher.publish((invoice_get,))

    notifications: list[str] = []
    server.get_context = lambda: SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: notifications.append("tools/list_changed")
        )
    )
    await server.list_tools()

    assert await publisher.publish((invoice_get, invoice_list)) is True
    assert notifications == ["tools/list_changed"]


@pytest.mark.asyncio
async def test_older_delayed_catalog_load_cannot_replace_newer_publication() -> None:
    from mercury_tools.mcp.v1_tools import refresh_generated_provider_tools

    older = (_qualification("flowaccount", "documents.invoice.get"),)
    newer = (*older, _qualification("flowaccount", "documents.invoice.list"))
    first_load_started = threading.Event()
    release_first_load = threading.Event()

    class DelayedCatalog:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = threading.Lock()

        def list_provider_mcp_qualifications(self):
            with self.lock:
                self.calls += 1
                call = self.calls
            if call == 1:
                first_load_started.set()
                assert release_first_load.wait(timeout=2)
                return older
            return newer

    runtime = SimpleNamespace(qualification_catalog=DelayedCatalog())
    server = StrictInputFastMCP("Monotonic generated publication")
    first = asyncio.create_task(
        refresh_generated_provider_tools(
            server,
            runtime_factory=lambda: runtime,
            close_runtime=False,
        )
    )
    assert await asyncio.to_thread(first_load_started.wait, 1)
    second = asyncio.create_task(
        refresh_generated_provider_tools(
            server,
            runtime_factory=lambda: runtime,
            close_runtime=False,
        )
    )
    await asyncio.sleep(0.05)
    release_first_load.set()
    await asyncio.gather(first, second)

    assert {tool.name for tool in await server.list_tools()} == {
        "mercury_flowaccount_invoice_get",
        "mercury_flowaccount_invoice_list",
    }


@pytest.mark.asyncio
async def test_generated_publication_rolls_back_registration_failure_and_cancellation() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Rollback generated provider publication")

    async def execute(_context, **_kwargs):
        raise AssertionError("publication test must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    original_add_tool = server.add_tool

    def fail_invoice_list(fn, *args, **kwargs):
        if kwargs.get("name") == "mercury_flowaccount_invoice_list":
            raise RuntimeError("registration_failed")
        return original_add_tool(fn, *args, **kwargs)

    server.add_tool = fail_invoice_list
    with pytest.raises(RuntimeError, match="^registration_failed$"):
        await publisher.publish((invoice_get, invoice_list))
    server.add_tool = original_add_tool
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_get"}

    await publisher._refresh_lock.acquire()
    pending = asyncio.create_task(publisher.publish((invoice_get, invoice_list)))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    publisher._refresh_lock.release()
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_get"}


@pytest.mark.asyncio
async def test_many_immutable_versions_keep_wire_model_cache_bounded_and_clearable() -> None:
    import mercury_tools.mcp.generated_tools as generated_tools

    generated_tools._clear_wire_model_cache()
    server = StrictInputFastMCP("Bounded generated wire models")

    async def execute(_context, **_kwargs):
        raise AssertionError("cache lifecycle test must not dispatch")

    publisher = generated_tools.GeneratedProviderToolPublisher(server, execute=execute)
    retained_model = None
    for maximum in range(generated_tools._MAX_WIRE_MODEL_CACHE_SIZE + 12):
        definition = ProviderMCPQualification.discovered(
            provider="flowaccount",
            environment="sandbox",
            provider_tool_name="PRIVATE_RAW_PROVIDER_TOOL",
            normalized_capability="documents.invoice.get",
            input_schema={
                "type": "object",
                "properties": {
                    "page_size": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": maximum + 1,
                    }
                },
                "required": ["page_size"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"document_number": {"type": "string"}},
                "required": ["document_number"],
                "additionalProperties": False,
            },
            public_output_field_paths=("/document_number",),
            response_shape_hash="a" * 64,
            required_permissions=("documents.read",),
        )
        qualification = definition.model_copy(
            update={
                "qualification_state": QualificationState.ENABLED,
                "company_sha256": "b" * 64,
                "evidence_revision_sha256": "c" * 64,
                "qualification_evidence_uri": (
                    "catalog://global/flowaccount/qualifications/"
                    f"{definition.capability_version_sha256}-{'c' * 64}.json"
                ),
                "evidence_evaluated_at": NOW,
                "evidence_expires_at": NOW + timedelta(days=1),
            }
        )
        await publisher.publish((qualification,))
        if retained_model is None:
            retained_model = generated_tools.catalog_wire_model(
                qualification.input_schema,
                kind="input",
            )

    assert len(generated_tools._WIRE_MODEL_CACHE) <= (generated_tools._MAX_WIRE_MODEL_CACHE_SIZE)
    assert retained_model is not None
    assert retained_model.model_validate({"page_size": 1}).model_dump(mode="json") == {
        "page_size": 1
    }

    publisher.clear()
    assert generated_tools._WIRE_MODEL_CACHE == {}


def test_public_projection_prunes_nested_sensitive_fields_across_refs_and_applicators() -> None:
    from mercury_tools.mcp.generated_tools import project_provider_read_data, public_output_schema

    schema = {
        "$defs": {
            "Line": {
                "type": "object",
                "properties": {
                    "document_id": {"type": "string"},
                    "vat_amount": {"type": "number"},
                    "contact_email": {"type": "string"},
                    "sessionToken": {"type": "string"},
                },
                "required": ["document_id", "vat_amount", "contact_email", "sessionToken"],
                "additionalProperties": False,
            },
            "Record": {
                "type": "object",
                "properties": {
                    "kind": {"const": "invoice"},
                    "invoice_id": {"type": "string"},
                    "invoiceAlias": {"type": "string"},
                    "tax_amount": {"type": "number"},
                    "contactEmail": {"type": "string"},
                    "เลขที่เอกสาร": {"type": "string"},
                    "nested": {
                        "type": "object",
                        "properties": {
                            "document_id": {"type": "string"},
                            "customerName": {"type": "string"},
                            "taxId": {"type": "string"},
                            "phone_number": {"type": "string"},
                        },
                        "required": [
                            "document_id",
                            "customerName",
                            "taxId",
                            "phone_number",
                        ],
                        "additionalProperties": False,
                    },
                    "lines": {"type": "array", "items": {"$ref": "#/$defs/Line"}},
                },
                "required": [
                    "kind",
                    "invoice_id",
                    "invoiceAlias",
                    "tax_amount",
                    "contactEmail",
                    "เลขที่เอกสาร",
                    "nested",
                    "lines",
                ],
                "additionalProperties": False,
            },
        },
        "allOf": [{"$ref": "#/$defs/Record"}],
        "if": {"$ref": "#/$defs/Record"},
        "then": {"$ref": "#/$defs/Record"},
    }
    raw = {
        "kind": "invoice",
        "invoice_id": "INV-1",
        "invoiceAlias": "ALIAS-1",
        "tax_amount": 7.0,
        "contactEmail": "person@example.com",
        "เลขที่เอกสาร": "เอกสาร-1",
        "nested": {
            "document_id": "DOC-1",
            "customerName": "Private Customer",
            "taxId": "1234567890123",
            "phone_number": "+66000000000",
        },
        "lines": [
            {
                "document_id": "LINE-1",
                "vat_amount": 0.49,
                "contact_email": "line@example.com",
                "sessionToken": "private-session",
            }
        ],
    }
    public_fields = (
        "/invoice_id",
        "/kind",
        "/lines/*/document_id",
        "/lines/*/vat_amount",
        "/nested/document_id",
        "/tax_amount",
    )

    public_schema = public_output_schema(
        schema,
        public_output_field_paths=public_fields,
    )
    projected = project_provider_read_data(
        raw,
        output_schema=schema,
        public_output_field_paths=public_fields,
    )

    assert projected == {
        "kind": "invoice",
        "invoice_id": "INV-1",
        "tax_amount": 7.0,
        "nested": {"document_id": "DOC-1"},
        "lines": [{"document_id": "LINE-1", "vat_amount": 0.49}],
    }
    rendered = str(public_schema)
    for forbidden in (
        "contactEmail",
        "contact_email",
        "customerName",
        "invoiceAlias",
        "taxId",
        "phone_number",
        "sessionToken",
        "เลขที่เอกสาร",
    ):
        assert forbidden not in rendered


def test_public_projection_supports_root_one_of_local_refs_and_rejects_unknown_semantics() -> None:
    from mercury_tools.mcp.generated_tools import project_provider_read_data, public_output_schema

    schema = {
        "$defs": {
            "Invoice": {
                "type": "object",
                "properties": {
                    "kind": {"const": "invoice"},
                    "invoice_id": {"type": "string"},
                    "email": {"type": "string"},
                },
                "required": ["kind", "invoice_id", "email"],
                "additionalProperties": False,
            },
            "Document": {
                "type": "object",
                "properties": {
                    "kind": {"const": "document"},
                    "document_id": {"type": "string"},
                    "tax_identifier": {"type": "string"},
                },
                "required": ["kind", "document_id", "tax_identifier"],
                "additionalProperties": False,
            },
        },
        "oneOf": [{"$ref": "#/$defs/Invoice"}, {"$ref": "#/$defs/Document"}],
    }

    assert project_provider_read_data(
        {"kind": "invoice", "invoice_id": "INV-2", "email": "person@example.com"},
        output_schema=schema,
        public_output_field_paths=("/invoice_id", "/kind"),
    ) == {"kind": "invoice", "invoice_id": "INV-2"}
    assert "oneOf" not in public_output_schema(
        schema,
        public_output_field_paths=("/invoice_id", "/kind"),
    )

    unsupported = {
        "type": "object",
        "properties": {"invoice_id": {"type": "string"}},
        "required": ["invoice_id"],
        "additionalProperties": False,
        "dependentRequired": {"invoice_id": ["invoice_id"]},
    }
    with pytest.raises(ValueError, match="^generated_schema_invalid$"):
        public_output_schema(
            unsupported,
            public_output_field_paths=("/invoice_id",),
        )


def test_public_projection_preserves_hidden_optional_conditional_false_path() -> None:
    from mercury_tools.mcp.generated_tools import project_provider_read_data

    schema = {
        "type": "object",
        "properties": {
            "status": {"enum": ["draft", "approved"]},
            "contact_email": {"type": "string"},
            "document_id": {"type": "string"},
        },
        "required": ["status"],
        "additionalProperties": False,
        "if": {
            "type": "object",
            "properties": {"contact_email": {"type": "string"}},
            "required": ["contact_email"],
            "additionalProperties": False,
        },
        "then": {
            "type": "object",
            "properties": {
                "status": {"const": "approved"},
                "document_id": {"type": "string"},
            },
            "required": ["status", "document_id"],
            "additionalProperties": False,
        },
    }

    assert project_provider_read_data(
        {"status": "draft"},
        output_schema=schema,
        public_output_field_paths=("/document_id", "/status"),
    ) == {"status": "draft"}


def test_public_projection_rejects_conditional_only_open_object_contract() -> None:
    from mercury_tools.mcp.generated_tools import public_output_schema

    conditional_only = {
        "if": {
            "type": "object",
            "properties": {"contactEmail": {"type": "string"}},
            "required": ["contactEmail"],
            "additionalProperties": False,
        },
        "then": {
            "type": "object",
            "properties": {"invoice_id": {"type": "string"}},
            "required": ["invoice_id"],
            "additionalProperties": False,
        },
    }

    with pytest.raises(ValueError, match="^generated_schema_invalid$"):
        public_output_schema(
            conditional_only,
            public_output_field_paths=("/invoice_id",),
        )


@pytest.mark.asyncio
async def test_schema_drift_persistence_failure_quarantines_then_lifecycle_reconciles() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Quarantined generated provider drift")
    dispatches = 0
    transitions = 0
    alerts: list[dict[str, object]] = []
    persisted = False

    async def execute(_context, **_kwargs):
        nonlocal dispatches
        dispatches += 1
        raise ProviderSchemaChanged(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.DISPATCHED,
        )

    async def persist_schema_change(qualification, _context):
        nonlocal transitions
        transitions += 1
        if not persisted:
            raise RuntimeError("authority unavailable")
        return (
            qualification.model_copy(
                update={
                    "qualification_state": QualificationState.DISABLED,
                    "disable_reason": "schema_changed",
                }
            ),
            invoice_list,
        )

    async def alert(event):
        assert "PRIVATE_RAW_PROVIDER_TOOL" not in str(event)
        alerts.append(event)

    publisher = GeneratedProviderToolPublisher(
        server,
        execute=execute,
        persist_schema_change=persist_schema_change,
        schema_drift_alert=alert,
    )
    await publisher.publish((invoice_get, invoice_list))
    server.get_context = lambda: SimpleNamespace()
    arguments = {
        "workspace_id": str(WORKSPACE_ID),
        "connection_id": str(CONNECTION_ID),
        "capability_version": invoice_get.capability_version_sha256,
        "page_size": 25,
    }

    _content, first = await server.call_tool("mercury_flowaccount_invoice_get", arguments)
    _content, repeated = await server.call_tool("mercury_flowaccount_invoice_get", arguments)
    assert first["error"]["code"] == "capability_unavailable"
    assert repeated["error"]["code"] == "capability_unavailable"
    assert dispatches == 1
    assert transitions == 1
    assert [event["input"]["capability_version"] for event in alerts] == [
        invoice_get.capability_version_sha256
    ]
    assert alerts[0]["output_summary"]["dispatch_certainty"] == "dispatched"
    assert "mercury_flowaccount_invoice_get" in {tool.name for tool in await server.list_tools()}

    assert await publisher.reconcile((invoice_get, invoice_list)) is False
    assert transitions == 2
    assert len(alerts) == 2
    assert alerts[1]["output_summary"]["dispatch_certainty"] == "dispatched"

    persisted = True
    assert await publisher.reconcile((invoice_get, invoice_list)) is True
    assert transitions == 3
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}

    restarted = StrictInputFastMCP("Restart sees persisted generated provider drift")
    restarted_publisher = GeneratedProviderToolPublisher(restarted, execute=execute)
    assert (
        await restarted_publisher.publish(
            (
                invoice_get.model_copy(
                    update={
                        "qualification_state": QualificationState.DISABLED,
                        "disable_reason": "schema_changed",
                    }
                ),
                invoice_list,
            )
        )
        is True
    )
    assert {tool.name for tool in await restarted.list_tools()} == {
        "mercury_flowaccount_invoice_list"
    }


@pytest.mark.asyncio
async def test_generated_request_sessions_receive_background_refresh_notification() -> None:
    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Generated request session notifications")

    async def execute(_context, **kwargs):
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id=kwargs["capability_id"],
            capability_version=kwargs["capability_version"],
            data={"document_number": "INV-1"},
        )

    first_notifications: list[str] = []
    second_notifications: list[str] = []
    first = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: first_notifications.append("tools/list_changed")
        )
    )
    second = SimpleNamespace(
        session=SimpleNamespace(
            send_tool_list_changed=lambda: second_notifications.append("tools/list_changed")
        )
    )
    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    arguments = {
        "workspace_id": str(WORKSPACE_ID),
        "connection_id": str(CONNECTION_ID),
        "capability_version": invoice_get.capability_version_sha256,
        "page_size": 25,
    }
    server.get_context = lambda: first
    await server.call_tool("mercury_flowaccount_invoice_get", arguments)
    server.get_context = lambda: second
    await server.call_tool("mercury_flowaccount_invoice_get", arguments)

    assert await publisher.publish((invoice_get, invoice_list)) is True
    assert first_notifications == ["tools/list_changed"]
    assert second_notifications == ["tools/list_changed"]


@pytest.mark.asyncio
async def test_lifecycle_reconcile_keeps_quarantine_until_publication_commits() -> None:
    from mercury_tools.execution.hosted.read_service import ProviderReadEnvelope
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Quarantine stays active through publication")
    dispatches = 0
    persistence_available = False
    persisted = asyncio.Event()

    async def execute(_context, **kwargs):
        nonlocal dispatches
        dispatches += 1
        if dispatches == 1:
            raise ProviderSchemaChanged(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return ProviderReadEnvelope(
            workspace_id=kwargs["workspace_id"],
            connection_id=kwargs["connection_id"],
            provider="flowaccount",
            company_display_name="Example Company",
            environment="sandbox",
            capability_id=kwargs["capability_id"],
            capability_version=kwargs["capability_version"],
            data={"document_number": "INV-1"},
        )

    async def persist_schema_change(qualification, _context):
        if not persistence_available:
            raise RuntimeError("catalog unavailable")
        persisted.set()
        return (
            qualification.model_copy(
                update={
                    "qualification_state": QualificationState.DISABLED,
                    "disable_reason": "schema_changed",
                }
            ),
            invoice_list,
        )

    publisher = GeneratedProviderToolPublisher(
        server,
        execute=execute,
        persist_schema_change=persist_schema_change,
    )
    await publisher.publish((invoice_get, invoice_list))
    server.get_context = lambda: SimpleNamespace()
    arguments = {
        "workspace_id": str(WORKSPACE_ID),
        "connection_id": str(CONNECTION_ID),
        "capability_version": invoice_get.capability_version_sha256,
        "page_size": 25,
    }
    _content, drift = await server.call_tool("mercury_flowaccount_invoice_get", arguments)
    assert drift["error"]["code"] == "capability_unavailable"
    assert dispatches == 1

    persistence_available = True
    await publisher._refresh_lock.acquire()
    reconcile = asyncio.create_task(publisher.reconcile((invoice_get, invoice_list)))
    await persisted.wait()
    _content, blocked = await server.call_tool("mercury_flowaccount_invoice_get", arguments)
    assert blocked["error"]["code"] == "capability_unavailable"
    assert dispatches == 1

    publisher._refresh_lock.release()
    assert await reconcile is True
    assert {tool.name for tool in await server.list_tools()} == {"mercury_flowaccount_invoice_list"}
    assert not publisher._quarantined_versions


@pytest.mark.asyncio
async def test_schema_drift_persistence_alert_retains_dispatch_certainty() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
    from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
    from mercury_tools.providers.models import ProviderId

    qualification = _qualification("flowaccount", "documents.invoice.get")
    server = StrictInputFastMCP("Schema drift alert certainty")
    alerts: list[dict[str, object]] = []

    async def execute(_context, **_kwargs):
        raise ProviderSchemaChanged(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.DISPATCHED,
        )

    async def persist_schema_change(_qualification, _context):
        raise RuntimeError("catalog unavailable")

    publisher = GeneratedProviderToolPublisher(
        server,
        execute=execute,
        persist_schema_change=persist_schema_change,
        schema_drift_alert=alerts.append,
    )
    await publisher.publish((qualification,))
    server.get_context = lambda: SimpleNamespace()
    _content, result = await server.call_tool(
        "mercury_flowaccount_invoice_get",
        {
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "capability_version": qualification.capability_version_sha256,
            "page_size": 25,
        },
    )

    assert result["error"]["code"] == "capability_unavailable"
    assert len(alerts) == 1
    assert alerts[0]["output_summary"]["dispatch_certainty"] == "dispatched"


@pytest.mark.asyncio
async def test_generated_refresh_session_retention_is_bounded_and_clear_releases_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(generated_tools, "_MAX_REFRESH_SESSIONS", 2, raising=False)

    class NonWeakSession:
        __slots__ = ("notifications",)

        def __init__(self) -> None:
            self.notifications = 0

        def send_tool_list_changed(self) -> None:
            self.notifications += 1

    server = StrictInputFastMCP("Bounded generated refresh sessions")

    async def execute(_context, **_kwargs):
        raise AssertionError("session retention must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    sessions = [NonWeakSession() for _ in range(3)]
    for session in sessions:
        publisher._remember_session(SimpleNamespace(session=session))

    assert len(publisher._refresh_sessions) == 2
    assert id(sessions[0]) not in publisher._refresh_sessions
    publisher.clear()
    assert not publisher._refresh_sessions


@pytest.mark.asyncio
async def test_generated_refresh_notification_has_total_deadline_and_prunes_slow_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(
        generated_tools,
        "_REFRESH_NOTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.02,
        raising=False,
    )

    class SlowSession:
        async def send_tool_list_changed(self) -> None:
            await asyncio.Event().wait()

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Bounded generated refresh notification")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    sessions = [SlowSession() for _ in range(3)]
    for session in sessions:
        publisher._remember_session(SimpleNamespace(session=session))

    started = time.monotonic()
    assert await asyncio.wait_for(publisher.publish((invoice_get, invoice_list)), timeout=0.1)
    assert time.monotonic() - started < 0.1
    assert not publisher._refresh_sessions


@pytest.mark.asyncio
async def test_cancelled_committed_refresh_retries_pending_notification_without_change() -> None:
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    class BlockingSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.notifications = 0

        async def send_tool_list_changed(self) -> None:
            self.notifications += 1
            self.started.set()
            await self.release.wait()

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Retry generated refresh notification")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    session = BlockingSession()
    publisher._remember_session(SimpleNamespace(session=session))
    refresh = asyncio.create_task(publisher.publish((invoice_get, invoice_list)))
    await session.started.wait()
    refresh.cancel()
    with pytest.raises(asyncio.CancelledError):
        await refresh

    session.release.set()
    assert await publisher.publish((invoice_get, invoice_list)) is False
    assert session.notifications == 2


@pytest.mark.asyncio
async def test_detached_notification_owner_survives_clear_and_releases_terminal_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(
        generated_tools,
        "_REFRESH_NOTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )

    class ResistantSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def send_tool_list_changed(self) -> None:
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            self.finished.set()
            raise RuntimeError("late notification failure")

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Cancellation-resistant generated refresh")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    session = ResistantSession()
    session_ref = weakref.ref(session)
    publisher._remember_session(SimpleNamespace(session=session))
    loop = asyncio.get_running_loop()
    exception_contexts: list[dict[str, object]] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: exception_contexts.append(context))
    refresh = asyncio.create_task(publisher.publish((invoice_get, invoice_list)))
    detached_task_ref: weakref.ReferenceType[asyncio.Task[None]] | None = None
    try:
        await session.started.wait()
        assert (
            await asyncio.wait_for(
                refresh,
                timeout=_NOTIFICATION_OUTER_GUARD_SECONDS,
            )
            is True
        )
        assert len(publisher._detached_notification_tasks) == 1
        detached_task_ref = next(iter(publisher._detached_notification_tasks.values())).task_ref

        publisher.clear()
        assert not publisher._refresh_sessions
        assert not publisher._detached_notification_tasks

        publisher._remember_session(SimpleNamespace(session=ResistantSession()))
        assert not publisher._refresh_sessions

        publisher_ref = weakref.ref(publisher)
        await asyncio.sleep(0)
        del publisher
        del session
        gc.collect()
        assert publisher_ref() is None
        assert detached_task_ref() is not None
        assert session_ref() is not None
    finally:
        retained_session = session_ref()
        if retained_session is not None:
            retained_session.release.set()
        if not refresh.done():
            await refresh
        if (
            retained_session is not None
            and detached_task_ref is not None
            and detached_task_ref() is not None
        ):
            await asyncio.wait_for(retained_session.finished.wait(), timeout=0.1)
        del retained_session
        for _ in range(3):
            await asyncio.sleep(0)
            gc.collect()
        loop.set_exception_handler(previous_handler)

    assert detached_task_ref is not None
    assert detached_task_ref() is None
    assert session_ref() is None
    assert not [
        context
        for context in exception_contexts
        if any(
            marker in str(context.get("message", "")).lower()
            for marker in ("destroyed but it is pending", "never retrieved")
        )
    ]


@pytest.mark.asyncio
async def test_cancellation_resistant_notifications_never_exceed_session_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(generated_tools, "_MAX_REFRESH_SESSIONS", 2, raising=False)
    monkeypatch.setattr(
        generated_tools,
        "_REFRESH_NOTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )

    class ResistantSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def send_tool_list_changed(self) -> None:
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            self.finished.set()
            raise RuntimeError("bounded late notification failure")

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("Bounded resistant generated refresh")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    sessions = [ResistantSession() for _ in range(6)]
    started_count = 0
    retained_task_count = 0
    try:
        for index, session in enumerate(sessions):
            publisher._remember_session(SimpleNamespace(session=session))
            qualifications = (invoice_get, invoice_list) if index % 2 == 0 else (invoice_get,)
            assert await asyncio.wait_for(
                publisher.publish(qualifications),
                timeout=_NOTIFICATION_OUTER_GUARD_SECONDS,
            )
        started_count = sum(session.started.is_set() for session in sessions)
        retained_task_count = len(publisher._detached_notification_tasks)
    finally:
        publisher.clear()
        for session in sessions:
            session.release.set()
        await asyncio.gather(
            *(
                asyncio.wait_for(session.finished.wait(), timeout=0.1)
                for session in sessions
                if session.started.is_set()
            )
        )
        await asyncio.sleep(0)

    assert started_count <= 2
    assert retained_task_count <= 2


@pytest.mark.asyncio
async def test_cancellation_resistant_notifications_share_capacity_across_publishers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(generated_tools, "_MAX_REFRESH_SESSIONS", 2, raising=False)
    monkeypatch.setattr(
        generated_tools,
        "_REFRESH_NOTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )

    class ResistantSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = asyncio.Event()

        async def send_tool_list_changed(self) -> None:
            self.started.set()
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    continue
            self.finished.set()

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publishers = [
        GeneratedProviderToolPublisher(
            StrictInputFastMCP(f"Globally bounded generated refresh {index}"),
            execute=execute,
        )
        for index in range(3)
    ]
    for publisher in publishers:
        await publisher.publish((invoice_get,))
    sessions = [ResistantSession() for _ in publishers]
    started_count = 0
    try:
        for publisher, session in zip(publishers, sessions, strict=True):
            publisher._remember_session(SimpleNamespace(session=session))
            assert await asyncio.wait_for(
                publisher.publish((invoice_get, invoice_list)),
                timeout=_NOTIFICATION_OUTER_GUARD_SECONDS,
            )
        started_count = sum(session.started.is_set() for session in sessions)
    finally:
        for publisher in publishers:
            publisher.clear()
        for session in sessions:
            session.release.set()
        await asyncio.gather(
            *(
                asyncio.wait_for(session.finished.wait(), timeout=0.1)
                for session in sessions
                if session.started.is_set()
            )
        )
        await asyncio.sleep(0)

    assert started_count == 2


@pytest.mark.asyncio
async def test_pending_refresh_does_not_duplicate_a_still_detached_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mercury_tools.mcp.generated_tools as generated_tools
    from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher

    monkeypatch.setattr(
        generated_tools,
        "_REFRESH_NOTIFICATION_TOTAL_TIMEOUT_SECONDS",
        0.01,
        raising=False,
    )

    class ResistantSession:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.idle = asyncio.Event()
            self.notifications = 0
            self.active = 0
            self.max_active = 0

        async def send_tool_list_changed(self) -> None:
            self.notifications += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            try:
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        continue
            finally:
                self.active -= 1
                if self.active == 0:
                    self.idle.set()

    invoice_get = _qualification("flowaccount", "documents.invoice.get")
    invoice_list = _qualification("flowaccount", "documents.invoice.list")
    server = StrictInputFastMCP("No duplicate detached generated refresh")

    async def execute(_context, **_kwargs):
        raise AssertionError("refresh notification must not dispatch")

    publisher = GeneratedProviderToolPublisher(server, execute=execute)
    await publisher.publish((invoice_get,))
    session = ResistantSession()
    publisher._remember_session(SimpleNamespace(session=session))
    refresh = asyncio.create_task(publisher.publish((invoice_get, invoice_list)))
    try:
        await session.started.wait()
        refresh.cancel()
        with pytest.raises(asyncio.CancelledError):
            await refresh

        assert (
            await asyncio.wait_for(
                publisher.publish((invoice_get, invoice_list)),
                timeout=0.08,
            )
            is False
        )
        notifications_before_release = session.notifications
        max_active_before_release = session.max_active

        session.release.set()
        await asyncio.wait_for(session.idle.wait(), timeout=0.1)
        await asyncio.sleep(0)
        assert await publisher.publish((invoice_get, invoice_list)) is False
    finally:
        session.release.set()
        if not refresh.done():
            await refresh
        await asyncio.sleep(0)

    assert notifications_before_release == 1
    assert max_active_before_release == 1
    assert session.notifications == 1
