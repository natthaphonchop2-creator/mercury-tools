from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ID = UUID("12345678-1234-5678-9234-567812345678")
CONNECTION_ID = UUID("87654321-4321-8765-4321-876543218765")
SECTION_16_ERROR_CODES = frozenset(
    {
        "mercury_auth_required",
        "mercury_scope_insufficient",
        "workspace_context_required",
        "workspace_access_denied",
        "provider_connection_required",
        "provider_connection_invalid",
        "provider_authorization_expired",
        "provider_setup_expired",
        "provider_setup_replayed",
        "provider_revocation_required",
        "provider_permission_insufficient",
        "provider_company_mismatch",
        "capability_unavailable",
        "capability_unreviewed",
        "capability_version_changed",
        "validation_failed",
        "preview_expired",
        "preview_binding_mismatch",
        "preview_state_changed",
        "confirmation_required",
        "duplicate_batch_item",
        "operation_in_progress",
        "provider_rejected",
        "outcome_unknown",
        "manual_review_required",
        "insufficient_evidence",
        "rate_limited",
    }
)


def _tool_names_for_process(*, v1_enabled: bool) -> set[str]:
    environment = os.environ.copy()
    environment["MERCURY_V1_ENABLED"] = "true" if v1_enabled else "false"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, json; "
                "from mercury_tools.mcp.server import mcp; "
                "print(json.dumps(sorted(tool.name for tool in asyncio.run(mcp.list_tools()))))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return set(json.loads(result.stdout))


def _schema_property(schema: object, property_name: str) -> dict[str, object] | None:
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict) and property_name in properties:
            value = properties[property_name]
            return value if isinstance(value, dict) else None
        for value in schema.values():
            found = _schema_property(value, property_name)
            if found is not None:
                return found
    elif isinstance(schema, list):
        for value in schema:
            found = _schema_property(value, property_name)
            if found is not None:
                return found
    return None


def _assert_closed_schema(schema: object) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            assert "properties" in schema, schema
            assert schema.get("additionalProperties") is False, schema
        assert schema.get("nullable") is not True, schema
        assert schema.get("type") != "null", schema
        for value in schema.values():
            _assert_closed_schema(value)
    elif isinstance(schema, list):
        for value in schema:
            _assert_closed_schema(value)


def _schema_property_names(schema: object) -> set[str]:
    if isinstance(schema, dict):
        names = set(schema.get("properties", {}))
        for value in schema.values():
            names.update(_schema_property_names(value))
        return names
    if isinstance(schema, list):
        return set().union(*(_schema_property_names(value) for value in schema))
    return set()


def _error_codes_from_output_schema(schema: dict[str, object]) -> set[str]:
    definitions = schema.get("$defs")
    assert isinstance(definitions, dict)
    error_details = definitions.get("MercuryV1ErrorDetails")
    assert isinstance(error_details, dict)
    properties = error_details.get("properties")
    assert isinstance(properties, dict)
    code = properties.get("code")
    assert isinstance(code, dict)
    values = code.get("enum")
    assert isinstance(values, list)
    return set(values)


@pytest.mark.asyncio
async def test_v1_tool_contract_is_closed_and_provider_environment_is_discriminated() -> None:
    from mercury_tools.mcp.contracts import V1_HOSTED_TOOL_NAMES
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 contract")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}

    assert set(tools) == V1_HOSTED_TOOL_NAMES
    for tool in tools.values():
        _assert_closed_schema(tool.inputSchema)
        assert tool.outputSchema is not None
        _assert_closed_schema(tool.outputSchema)

    start_schema = tools["start_provider_connection"].inputSchema
    assert "oneOf" in start_schema
    branches = [
        start_schema["$defs"][branch["$ref"].rsplit("/", 1)[-1]] for branch in start_schema["oneOf"]
    ]
    environments_by_provider = {
        branch["properties"]["provider"]["const"]: branch["properties"]["environment"]["enum"]
        for branch in branches
    }
    assert environments_by_provider == {
        "flowaccount": ["sandbox", "production"],
        "peak": ["uat", "production"],
    }
    output_definitions = tools["start_provider_connection"].outputSchema["$defs"]
    for definition_name in (
        "FlowAccountConnectionStartData",
        "PeakConnectionStartData",
    ):
        expires_at = output_definitions[definition_name]["properties"]["expires_at"]
        assert expires_at["format"] == "date-time"

    for name in {
        "start_provider_connection",
        "list_provider_connections",
        "connector_status",
        "list_provider_capabilities",
        "get_capability_schema",
        "disconnect_provider",
    }:
        workspace = _schema_property(tools[name].inputSchema, "workspace_id")
        assert workspace is not None
        assert workspace.get("format") == "uuid"

    for name in {
        "connector_status",
        "list_provider_capabilities",
        "disconnect_provider",
    }:
        connection = _schema_property(tools[name].inputSchema, "connection_id")
        assert connection is not None
        assert connection.get("format") == "uuid"

    version = _schema_property(tools["get_capability_schema"].inputSchema, "capability_version")
    assert version is not None
    assert version.get("pattern") == "^[0-9a-f]{64}$"

    property_names = _schema_property_names(
        {
            name: {
                "input": tool.inputSchema,
                "output": tool.outputSchema,
            }
            for name, tool in tools.items()
        }
    )
    for forbidden in (
        "access_token",
        "api_key",
        "client_secret",
        "connect_key",
        "credentials",
        "password",
        "provider_account_id",
        "raw_provider",
        "refresh_token",
        "user_token",
    ):
        assert forbidden not in property_names


@pytest.mark.asyncio
async def test_v1_tools_publish_the_complete_closed_section_16_error_union() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_errors import V1_ERROR_CODES
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 error contract")
    configure_v1_tools(server, enabled=True)

    assert V1_ERROR_CODES == SECTION_16_ERROR_CODES
    for tool in await server.list_tools():
        assert tool.outputSchema is not None
        output_schema = tool.outputSchema
        assert tool.meta is not None
        assert tool.meta["mercury/error-schema"] == "mercury.v1.error.v1"
        assert "oneOf" in output_schema
        assert output_schema["oneOf"] == [
            {"$ref": "#/$defs/Success"},
            {"$ref": "#/$defs/MercuryV1ErrorOutput"},
        ]
        assert _error_codes_from_output_schema(output_schema) == SECTION_16_ERROR_CODES
        _assert_closed_schema(output_schema)
        definitions = output_schema["$defs"]
        assert isinstance(definitions, dict)
        error_output = definitions["MercuryV1ErrorOutput"]
        assert isinstance(error_output, dict)
        error_properties = error_output["properties"]
        assert isinstance(error_properties, dict)
        assert set(error_properties) == {"status", "error"}
        details = definitions["MercuryV1ErrorDetails"]
        assert isinstance(details, dict)
        details_properties = details["properties"]
        assert isinstance(details_properties, dict)
        assert set(details_properties) == {"code", "guidance"}


@pytest.mark.asyncio
async def test_v1_reconfiguration_keeps_published_output_contracts_stable() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 output stability")
    configure_v1_tools(server, enabled=True)
    first_output_schemas = {
        tool.name: copy.deepcopy(tool.outputSchema) for tool in await server.list_tools()
    }

    configure_v1_tools(server, enabled=True)

    assert {
        tool.name: tool.outputSchema for tool in await server.list_tools()
    } == first_output_schemas


@pytest.mark.asyncio
async def test_v1_tool_contract_rejects_unknown_fields_and_invalid_workspace_uuid() -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 validation")
    configure_v1_tools(server, enabled=True)

    with pytest.raises(ToolError):
        await server.call_tool("list_accounting_providers", {"unexpected": "value"})
    with pytest.raises(ToolError):
        await server.call_tool(
            "list_provider_connections",
            {"workspace_id": "not-a-uuid"},
        )
    with pytest.raises(ToolError):
        await server.call_tool(
            "start_provider_connection",
            {
                "workspace_id": str(WORKSPACE_ID),
                "provider": "flowaccount",
                "environment": "uat",
            },
        )


@pytest.mark.asyncio
async def test_v1_tool_annotations_match_observable_effects() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 annotations")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}

    expected = {
        "get_mercury_context": (False, False, True, False),
        "list_accounting_providers": (True, False, None, False),
        "start_provider_connection": (False, False, False, True),
        "list_provider_connections": (True, False, None, False),
        "connector_status": (False, False, False, False),
        "list_provider_capabilities": (True, False, None, False),
        "get_capability_schema": (True, False, None, False),
        "disconnect_provider": (False, True, True, False),
    }
    for name, expected_annotations in expected.items():
        annotations = tools[name].annotations
        assert annotations is not None
        assert (
            annotations.readOnlyHint,
            annotations.destructiveHint,
            annotations.idempotentHint,
            annotations.openWorldHint,
        ) == expected_annotations


def test_final_module_registry_is_v1_only_when_enabled_and_legacy_unchanged_when_disabled() -> None:
    from mercury_tools.mcp.contracts import LEGACY_HOSTED_TOOL_NAMES, V1_HOSTED_TOOL_NAMES

    assert _tool_names_for_process(v1_enabled=True) == V1_HOSTED_TOOL_NAMES
    assert _tool_names_for_process(v1_enabled=False) == LEGACY_HOSTED_TOOL_NAMES


@pytest.mark.asyncio
async def test_workspace_membership_is_resolved_before_provider_connection_store_access() -> None:
    from mercury_tools.auth.models import MercuryPrincipal
    from mercury_tools.mcp.v1_tools import list_provider_connections
    from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

    membership_checked = False

    class WorkspaceService:
        def require_workspace(self, *_args: object) -> WorkspaceMembership:
            nonlocal membership_checked
            membership_checked = True
            return WorkspaceMembership(
                tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
                tenant_display_name="Mercury",
                workspace_id=WORKSPACE_ID,
                workspace_display_name="Default",
                role=WorkspaceRole.MEMBER,
            )

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            assert membership_checked
            return ()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer test.token.value")],
        }
    )
    request.state.mercury_principal = MercuryPrincipal(
        subject=UUID("22222222-2222-2222-2222-222222222222"),
        client_id="test-client",
        scopes=frozenset({"openid"}),
        token_id="test-token",
    )
    context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    runtime = SimpleNamespace(connection_store=ConnectionStore())

    result = await list_provider_connections(
        context,
        workspace_id=WORKSPACE_ID,
        service_factory=WorkspaceService,
        runtime_factory=lambda: runtime,
    )

    assert result.status == "ok"
    assert result.workspace_id == WORKSPACE_ID
    assert membership_checked


@pytest.mark.asyncio
async def test_connector_status_writes_only_a_sanitized_local_audit_event() -> None:
    from datetime import UTC, datetime

    from mercury_tools.auth.models import MercuryPrincipal
    from mercury_tools.mcp.v1_tools import connector_status
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderConnectionSummary,
        ProviderId,
    )
    from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

    class WorkspaceService:
        def require_workspace(self, *_args: object) -> WorkspaceMembership:
            return WorkspaceMembership(
                tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
                tenant_display_name="Mercury",
                workspace_id=WORKSPACE_ID,
                workspace_display_name="Default",
                role=WorkspaceRole.MEMBER,
            )

    connection = ProviderConnectionSummary(
        connection_id=CONNECTION_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        account_display_name="Sanitized Account",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("accounting.read",),
        readiness=ConnectionReadiness.READY,
        revision=3,
        last_validated_at=datetime.now(UTC),
        provider_revocation_required=False,
    )

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[ProviderConnectionSummary, ...]:
            return (connection,)

    class QualificationCatalog:
        def list_provider_mcp_qualifications(self) -> tuple[object, ...]:
            return ()

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer test.token.value")],
        }
    )
    request.state.mercury_principal = MercuryPrincipal(
        subject=UUID("22222222-2222-2222-2222-222222222222"),
        client_id="test-client",
        scopes=frozenset({"openid"}),
        token_id="test-token",
    )
    context = SimpleNamespace(request_context=SimpleNamespace(request=request))
    runtime = SimpleNamespace(
        connection_store=ConnectionStore(),
        qualification_catalog=QualificationCatalog(),
    )
    audit_events: list[dict[str, object]] = []

    result = await connector_status(
        context,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        service_factory=WorkspaceService,
        runtime_factory=lambda: runtime,
        audit_recorder=audit_events.append,
    )

    assert result.status == "ok"
    assert audit_events == [
        {
            "tool_name": "connector_status",
            "input": {
                "workspace_id_sha256": hashlib.sha256(
                    str(WORKSPACE_ID).encode("utf-8")
                ).hexdigest(),
                "connection_id_sha256": hashlib.sha256(
                    str(CONNECTION_ID).encode("utf-8")
                ).hexdigest(),
            },
            "output_summary": {
                "provider": "flowaccount",
                "environment": "sandbox",
                "readiness": "ready",
                "missing_qualification_count": 4,
            },
            "status": "ok",
            "metadata": {"runtime": "mcp", "surface": "v1"},
        }
    ]
    assert str(WORKSPACE_ID) not in str(audit_events)
    assert str(CONNECTION_ID) not in str(audit_events)
    assert "Sanitized Account" not in str(audit_events)
