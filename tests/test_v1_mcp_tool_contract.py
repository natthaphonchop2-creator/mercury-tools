from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import ValidationError
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


def _same_server_reconfiguration_names() -> tuple[
    set[str],
    set[str],
    set[str],
    dict[str, int],
    dict[str, int],
]:
    environment = os.environ.copy()
    environment["MERCURY_V1_ENABLED"] = "false"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import asyncio, json; from concurrent.futures import ThreadPoolExecutor; "
                "from mercury_tools.mcp.server import mcp; "
                "from mercury_tools.mcp.v1_tools import configure_v1_tools; "
                "names = lambda: sorted(tool.name for tool in asyncio.run(mcp.list_tools())); "
                "legacy = names(); "
                "legacy_registry = {name: id(mcp._tool_manager.get_tool(name)) "
                "for name in legacy}; "
                "executor = ThreadPoolExecutor(max_workers=8); "
                "list(executor.map(lambda _: configure_v1_tools(mcp, enabled=True), range(20))); "
                "executor.shutdown(); "
                "enabled = names(); "
                "executor = ThreadPoolExecutor(max_workers=8); "
                "list(executor.map(lambda _: configure_v1_tools(mcp, enabled=False), range(20))); "
                "executor.shutdown(); "
                "disabled = names(); "
                "disabled_registry = {name: id(mcp._tool_manager.get_tool(name)) "
                "for name in disabled}; "
                "print(json.dumps([legacy, enabled, disabled, legacy_registry, disabled_registry]))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    legacy, enabled, disabled, legacy_registry, disabled_registry = json.loads(result.stdout)
    return (
        set(legacy),
        set(enabled),
        set(disabled),
        legacy_registry,
        disabled_registry,
    )


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
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_errors import MercuryV1ErrorOutput
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 validation")
    configure_v1_tools(server, enabled=True)

    for name, arguments in (
        ("list_accounting_providers", {"unexpected": "value"}),
        ("list_provider_connections", {"workspace_id": "not-a-uuid"}),
        (
            "start_provider_connection",
            {
                "workspace_id": str(WORKSPACE_ID),
                "provider": "flowaccount",
                "environment": "uat",
            },
        ),
    ):
        content, structured = await server.call_tool(name, arguments)

        error = MercuryV1ErrorOutput.model_validate(structured)
        assert error.error.code == "validation_failed"
        assert all("validation error" not in item.text.lower() for item in content)
        assert all("not-a-uuid" not in item.text for item in content)


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
        "disconnect_provider": (False, True, True, True),
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


def test_same_server_v1_reconfiguration_restores_the_exact_legacy_registry() -> None:
    from mercury_tools.mcp.contracts import LEGACY_HOSTED_TOOL_NAMES, V1_HOSTED_TOOL_NAMES

    (
        legacy,
        enabled,
        disabled,
        legacy_registry,
        disabled_registry,
    ) = _same_server_reconfiguration_names()

    assert legacy == LEGACY_HOSTED_TOOL_NAMES
    assert enabled == V1_HOSTED_TOOL_NAMES
    assert disabled == LEGACY_HOSTED_TOOL_NAMES
    assert disabled_registry == legacy_registry


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

        def load_connection(self, **_kwargs: object):
            return _flowaccount_connection()

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


def _authenticated_context() -> SimpleNamespace:
    from mercury_tools.auth.models import MercuryPrincipal

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
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


def _workspace_service_type() -> type[object]:
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

    return WorkspaceService


def _flowaccount_connection():
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderConnection,
        ProviderId,
    )

    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    return ProviderConnection(
        id=CONNECTION_ID,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        workspace_id=WORKSPACE_ID,
        auth_user_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        provider_account_id="company-mismatch",
        account_display_name="Sanitized Account",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.read",),
        readiness=ConnectionReadiness.READY,
        revision=3,
        last_validated_at=now,
        credential_envelope_ids=(UUID("99999999-9999-4999-8999-999999999999"),),
        created_at=now,
        updated_at=now,
    )


def _enabled_flowaccount_qualification():
    from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
    from mercury_tools.qualification.artifacts import build_qualification_artifact
    from mercury_tools.qualification.provider_mcp import transition_qualification

    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="get_invoice",
        normalized_capability="documents.invoice.get",
        input_schema={"type": "object", "properties": {}},
        output_schema={"type": "object", "properties": {}},
        response_shape_hash="a" * 64,
        required_permissions=("documents.read",),
    )
    artifact = build_qualification_artifact(
        definition=definition,
        company_sha256="b" * 64,
        runner_version="test-runner-v1",
        evaluated_at=now,
        input_sha256="c" * 64,
        sanitized_result_identifier="result_test_001",
        checks={"schema_matches": True},
        reviewer="release_reviewer",
        evidence_expires_at=now + timedelta(days=1),
        passed=True,
    )
    schema_validated = transition_qualification(
        definition,
        QualificationState.SCHEMA_VALIDATED,
        now=now,
    )
    nonproduction = transition_qualification(
        schema_validated,
        QualificationState.NONPRODUCTION_QUALIFIED,
        evidence=artifact,
        now=now,
    )
    return transition_qualification(
        nonproduction,
        QualificationState.ENABLED,
        evidence=artifact,
        now=now,
    )


@pytest.mark.asyncio
async def test_v1_handler_failures_use_the_published_error_output_union() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_errors import MercuryV1ErrorOutput
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 handler errors")
    configure_v1_tools(server, enabled=True)

    content, structured = await server.call_tool("list_accounting_providers", {})

    error = MercuryV1ErrorOutput.model_validate(structured)
    assert error.error.code == "mercury_auth_required"
    assert all("error executing tool" not in item.text.lower() for item in content)


@pytest.mark.asyncio
async def test_all_runtime_backed_handlers_preserve_results_when_runtime_close_fails() -> None:
    from mercury_tools.mcp.v1_errors import MercuryV1ToolError
    from mercury_tools.mcp.v1_tools import (
        connector_status,
        disconnect_provider,
        get_capability_schema,
        list_provider_capabilities,
        list_provider_connections,
        start_provider_connection,
    )

    connection = _flowaccount_connection()

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            return (connection.summary(),)

        def load_connection(self, **_kwargs: object):
            return connection

        def disconnect(self, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                deleted_envelope_count=1,
                provider_revocation_required=False,
                revision=4,
            )

    class ProviderOAuthService:
        async def start(self, *_args: object) -> SimpleNamespace:
            return SimpleNamespace(
                authorization_url="https://example.test/authorize",
                expires_at=datetime(2026, 7, 29, 13, tzinfo=UTC),
            )

        async def disconnect(self, *_args: object) -> SimpleNamespace:
            return SimpleNamespace(
                status="disconnected",
                local_credentials_deleted=True,
                remote_revocation_status="revoked",
                provider_revocation_required=False,
                deleted_envelope_count=1,
                revision=4,
            )

    class QualificationCatalog:
        def list_provider_mcp_qualifications(self) -> tuple[object, ...]:
            return ()

    class Runtime:
        connection_store = ConnectionStore()
        provider_oauth_service = ProviderOAuthService()
        qualification_catalog = QualificationCatalog()

        def __init__(self) -> None:
            self.close_count = 0

        async def aclose(self) -> None:
            self.close_count += 1
            raise RuntimeError("raw_close_marker")

    runtime = Runtime()
    context = _authenticated_context()
    workspace_service = _workspace_service_type()
    kwargs = {
        "service_factory": workspace_service,
        "runtime_factory": lambda: runtime,
    }

    assert (
        await start_provider_connection(
            context,
            workspace_id=WORKSPACE_ID,
            provider="flowaccount",
            environment="sandbox",
            **kwargs,
        )
    ).status == "ok"
    assert (
        await list_provider_connections(
            context,
            workspace_id=WORKSPACE_ID,
            **kwargs,
        )
    ).status == "ok"
    assert (
        await connector_status(
            context,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            audit_recorder=lambda _event: None,
            **kwargs,
        )
    ).status == "ok"
    assert (
        await list_provider_capabilities(
            context,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            **kwargs,
        )
    ).status == "ok"
    with pytest.raises(MercuryV1ToolError, match="^capability_unavailable$"):
        await get_capability_schema(
            context,
            workspace_id=WORKSPACE_ID,
            capability_id="documents.invoice.get",
            capability_version="a" * 64,
            **kwargs,
        )
    assert (
        await disconnect_provider(
            context,
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            confirmation="DISCONNECT",
            **kwargs,
        )
    ).status == "ok"
    assert runtime.close_count == 6


@pytest.mark.asyncio
async def test_runtime_backed_handler_propagates_close_cancellation() -> None:
    from mercury_tools.mcp.v1_tools import list_provider_connections

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            return ()

    class Runtime:
        connection_store = ConnectionStore()

        async def aclose(self) -> None:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await list_provider_connections(
            _authenticated_context(),
            workspace_id=WORKSPACE_ID,
            service_factory=_workspace_service_type(),
            runtime_factory=Runtime,
        )


def test_flowaccount_disconnect_public_variants_exclude_contradictory_outcomes() -> None:
    from mercury_tools.mcp.v1_schemas import (
        DisconnectProviderOutput,
        FlowAccountDisconnectedData,
        FlowAccountRevocationRequiredData,
    )

    disconnected = [
        FlowAccountDisconnectedData(
            provider="flowaccount",
            status="disconnected",
            local_credentials_deleted=True,
            remote_revocation_status=remote_revocation_status,
            deleted_envelope_count=1,
            provider_revocation_required=False,
            revision=2,
        )
        for remote_revocation_status in ("revoked", "not_supported", "already_disconnected")
    ]
    revocation_required = [
        FlowAccountRevocationRequiredData(
            provider="flowaccount",
            status="provider_revocation_required",
            local_credentials_deleted=True,
            remote_revocation_status=remote_revocation_status,
            deleted_envelope_count=1,
            provider_revocation_required=True,
            revision=2,
        )
        for remote_revocation_status in ("failed", "already_disconnected")
    ]
    local_only = disconnected[1]
    failed_revoke = revocation_required[0]
    assert local_only.remote_revocation_status == "not_supported"
    assert failed_revoke.remote_revocation_status == "failed"

    with pytest.raises(ValidationError):
        FlowAccountDisconnectedData(
            provider="flowaccount",
            status="disconnected",
            local_credentials_deleted=True,
            remote_revocation_status="failed",
            deleted_envelope_count=1,
            provider_revocation_required=False,
            revision=2,
        )
    with pytest.raises(ValidationError):
        FlowAccountRevocationRequiredData(
            provider="flowaccount",
            status="provider_revocation_required",
            local_credentials_deleted=True,
            remote_revocation_status="already_disconnected",
            deleted_envelope_count=0,
            provider_revocation_required=False,
            revision=2,
        )

    output = DisconnectProviderOutput.model_validate(
        {
            "status": "ok",
            "workspace_id": str(WORKSPACE_ID),
            "connection_id": str(CONNECTION_ID),
            "provider": "flowaccount",
            "environment": "sandbox",
            "data": local_only.model_dump(),
            "next_allowed_actions": ["list_provider_connections"],
        }
    )
    assert isinstance(output.data, type(local_only))


@pytest.mark.asyncio
async def test_published_disconnect_schema_uses_only_valid_flowaccount_variants() -> None:
    from mercury_tools.mcp.server import StrictInputFastMCP
    from mercury_tools.mcp.v1_tools import configure_v1_tools

    server = StrictInputFastMCP("Mercury V1 disconnect variants")
    configure_v1_tools(server, enabled=True)
    tools = {tool.name: tool for tool in await server.list_tools()}
    output_schema = tools["disconnect_provider"].outputSchema
    assert output_schema is not None
    definitions = output_schema["$defs"]
    assert isinstance(definitions, dict)
    disconnected = definitions["FlowAccountDisconnectedData"]
    revocation_required = definitions["FlowAccountRevocationRequiredData"]
    assert disconnected["properties"]["status"]["const"] == "disconnected"
    assert disconnected["properties"]["provider_revocation_required"]["const"] is False
    assert disconnected["properties"]["remote_revocation_status"]["enum"] == [
        "revoked",
        "not_supported",
        "already_disconnected",
    ]
    assert revocation_required["properties"]["status"]["const"] == "provider_revocation_required"
    assert revocation_required["properties"]["provider_revocation_required"]["const"] is True
    assert revocation_required["properties"]["remote_revocation_status"]["enum"] == [
        "failed",
        "already_disconnected",
    ]
    data_schema = definitions["Success"]["properties"]["data"]
    assert data_schema["discriminator"]["propertyName"] == "provider"


@pytest.mark.asyncio
async def test_public_capability_status_uses_the_connection_bound_catalog_resolver() -> None:
    from mercury_tools.mcp.v1_tools import list_provider_capabilities
    from mercury_tools.qualification.provider_mcp import CapabilityResolution

    connection = _flowaccount_connection()
    qualification = _enabled_flowaccount_qualification()

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            return (connection.summary(),)

        def load_connection(self, **_kwargs: object):
            return connection

    class QualificationCatalog:
        def list_provider_mcp_qualifications(self) -> tuple[object, ...]:
            return (qualification,)

    class Resolver:
        def __init__(self) -> None:
            self.calls: list[tuple[object, object]] = []

        async def resolve_for_connection(self, connection_value: object, *, selection: object):
            self.calls.append((connection_value, selection))
            return CapabilityResolution(status="insufficient_evidence")

    resolver = Resolver()
    runtime = SimpleNamespace(
        connection_store=ConnectionStore(),
        qualification_catalog=QualificationCatalog(),
        qualification_resolver=resolver,
    )

    result = await list_provider_capabilities(
        _authenticated_context(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        service_factory=_workspace_service_type(),
        runtime_factory=lambda: runtime,
    )

    assert resolver.calls
    assert result.data[0].availability == "unavailable"
    assert result.data[0].status_detail == "insufficient_evidence"


@pytest.mark.asyncio
async def test_public_capability_schema_refuses_terminal_or_unverified_catalog_rows() -> None:
    from mercury_tools.mcp.v1_errors import MercuryV1ToolError
    from mercury_tools.mcp.v1_tools import get_capability_schema
    from mercury_tools.qualification.provider_mcp import CapabilityResolution

    connection = _flowaccount_connection()
    qualification = _enabled_flowaccount_qualification()

    class ConnectionStore:
        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            return (connection.summary(),)

        def load_connection(self, **_kwargs: object):
            return connection

    class QualificationCatalog:
        def list_provider_mcp_qualifications(self) -> tuple[object, ...]:
            return (qualification,)

    class Resolver:
        async def resolve_for_connection(self, _connection: object, *, selection: object):
            return CapabilityResolution(status="capability_unavailable")

    runtime = SimpleNamespace(
        connection_store=ConnectionStore(),
        qualification_catalog=QualificationCatalog(),
        qualification_resolver=Resolver(),
    )

    with pytest.raises(MercuryV1ToolError, match="^capability_unavailable$"):
        await get_capability_schema(
            _authenticated_context(),
            workspace_id=WORKSPACE_ID,
            capability_id=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            service_factory=_workspace_service_type(),
            runtime_factory=lambda: runtime,
        )


@pytest.mark.asyncio
async def test_disconnect_provider_routes_peak_and_flowaccount_through_provider_services() -> None:
    from mercury_tools.mcp.v1_tools import disconnect_provider
    from mercury_tools.providers.models import (
        AuthorizationMethod,
        ConnectionReadiness,
        ProviderConnection,
        ProviderId,
    )
    from mercury_tools.providers.peak_setup import PeakDisconnectOutcome

    now = datetime(2026, 7, 29, 12, tzinfo=UTC)
    peak_connection = ProviderConnection(
        id=CONNECTION_ID,
        tenant_id=UUID("11111111-1111-1111-1111-111111111111"),
        workspace_id=WORKSPACE_ID,
        auth_user_id=UUID("22222222-2222-2222-2222-222222222222"),
        provider=ProviderId.PEAK,
        environment="uat",
        provider_account_id="merchant-123",
        account_display_name="PEAK Merchant",
        authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS,
        granted_permissions=("documents.read",),
        readiness=ConnectionReadiness.READY,
        revision=3,
        last_validated_at=now,
        credential_envelope_ids=(UUID("99999999-9999-4999-8999-999999999999"),),
        created_at=now,
        updated_at=now,
    )
    flow_connection = _flowaccount_connection()

    class ConnectionStore:
        def __init__(self, connection: object) -> None:
            self.connection = connection

        def list_for_workspace(self, **_kwargs: object) -> tuple[object, ...]:
            return (self.connection.summary(),)

        def load_connection(self, **_kwargs: object):
            return self.connection

        def disconnect(self, **_kwargs: object) -> None:
            raise AssertionError("v1 must use the provider disconnect service")

    class PeakService:
        def __init__(self) -> None:
            self.calls: list[tuple[object, UUID, UUID]] = []

        async def disconnect(self, *args: object) -> PeakDisconnectOutcome:
            self.calls.append(args)  # type: ignore[arg-type]
            return PeakDisconnectOutcome()

    class FlowService:
        def __init__(self) -> None:
            self.calls: list[tuple[object, UUID, UUID]] = []

        async def disconnect(self, *args: object) -> SimpleNamespace:
            self.calls.append(args)  # type: ignore[arg-type]
            return SimpleNamespace(
                status="disconnected",
                local_credentials_deleted=True,
                remote_revocation_status="revoked",
                provider_revocation_required=False,
                deleted_envelope_count=1,
                revision=4,
            )

    peak_service = PeakService()
    peak_runtime = SimpleNamespace(
        connection_store=ConnectionStore(peak_connection),
        peak_setup_service=peak_service,
    )
    peak_result = await disconnect_provider(
        _authenticated_context(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        confirmation="DISCONNECT",
        service_factory=_workspace_service_type(),
        runtime_factory=lambda: peak_runtime,
    )

    assert peak_service.calls
    assert peak_result.data.status == "provider_revocation_required"
    assert peak_result.data.instruction == "Revoke this credential set in PEAK Account."

    flow_service = FlowService()
    flow_runtime = SimpleNamespace(
        connection_store=ConnectionStore(flow_connection),
        provider_oauth_service=flow_service,
    )
    flow_result = await disconnect_provider(
        _authenticated_context(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        confirmation="DISCONNECT",
        service_factory=_workspace_service_type(),
        runtime_factory=lambda: flow_runtime,
    )

    assert flow_service.calls
    assert flow_result.data.remote_revocation_status == "revoked"
    assert flow_result.data.provider_revocation_required is False
