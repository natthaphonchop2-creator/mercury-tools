from __future__ import annotations

import asyncio
import time
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
                    "tax_amount": {"type": "number"},
                    "contactEmail": {"type": "string"},
                    "nested": {
                        "type": "object",
                        "properties": {
                            "document_id": {"type": "string"},
                            "taxId": {"type": "string"},
                            "phone_number": {"type": "string"},
                        },
                        "required": ["document_id", "taxId", "phone_number"],
                        "additionalProperties": False,
                    },
                    "lines": {"type": "array", "items": {"$ref": "#/$defs/Line"}},
                },
                "required": [
                    "kind",
                    "invoice_id",
                    "tax_amount",
                    "contactEmail",
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
        "tax_amount": 7.0,
        "contactEmail": "person@example.com",
        "nested": {
            "document_id": "DOC-1",
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

    public_schema = public_output_schema(schema)
    projected = project_provider_read_data(raw, output_schema=schema)

    assert projected == {
        "kind": "invoice",
        "invoice_id": "INV-1",
        "tax_amount": 7.0,
        "nested": {"document_id": "DOC-1"},
        "lines": [{"document_id": "LINE-1", "vat_amount": 0.49}],
    }
    rendered = str(public_schema)
    for forbidden in ("contactEmail", "contact_email", "taxId", "phone_number", "sessionToken"):
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
    ) == {"kind": "invoice", "invoice_id": "INV-2"}
    assert "oneOf" not in public_output_schema(schema)

    unsupported = {
        "type": "object",
        "properties": {"invoice_id": {"type": "string"}},
        "required": ["invoice_id"],
        "additionalProperties": False,
        "dependentRequired": {"invoice_id": ["invoice_id"]},
    }
    with pytest.raises(ValueError, match="^generated_schema_invalid$"):
        public_output_schema(unsupported)


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

    assert project_provider_read_data({"status": "draft"}, output_schema=schema) == {
        "status": "draft"
    }


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
        public_output_schema(conditional_only)


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
