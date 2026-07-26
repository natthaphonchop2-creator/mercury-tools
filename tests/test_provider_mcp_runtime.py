from __future__ import annotations

import warnings
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from uuid import UUID

import httpx
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

import mercury_tools.providers.streamable_mcp as streamable_module
from mercury_tools.config import Settings
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderDriver,
    ProviderOperationClass,
    ProviderRuntimeError,
    ProviderStatusClass,
    ProviderValidation,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.streamable_mcp import StreamableMCPDriver

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
AUTH_USER_ID = UUID("44444444-4444-4444-8444-444444444444")
CONNECTION_ID = UUID("55555555-5555-4555-8555-555555555555")
OTHER_CONNECTION_ID = UUID("66666666-6666-4666-8666-666666666666")
OPERATION_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


class InvoiceArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    invoice_id: str


def _settings() -> Settings:
    return Settings(
        supabase_url="",
        supabase_service_role_key="",
        openai_api_key="",
        flowaccount_mcp_sandbox_url="https://flowaccount-sandbox.example/mcp",
        flowaccount_mcp_production_url="https://flowaccount.example/mcp",
        peak_mcp_uat_url="https://peak-uat.example/mcp",
        peak_mcp_production_url="https://peak.example/mcp",
    )


def _connection(
    *,
    tenant_id: UUID = TENANT_ID,
    connection_id: UUID = CONNECTION_ID,
    environment: str = "sandbox",
) -> ProviderConnection:
    return ProviderConnection(
        id=connection_id,
        tenant_id=tenant_id,
        workspace_id=WORKSPACE_ID,
        auth_user_id=AUTH_USER_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment=environment,
        provider_account_id=f"account-{connection_id}",
        account_display_name="Mercury Test Company",
        authorization_method=AuthorizationMethod.OAUTH2_PKCE,
        granted_permissions=("documents.create", "documents.read", "profile.read"),
        readiness=ConnectionReadiness.READY,
        revision=1,
        last_validated_at=NOW,
        credential_envelope_ids=(
            UUID("88888888-8888-4888-8888-888888888888"),
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _binding(
    operation_class: ProviderOperationClass = ProviderOperationClass.READ,
    *,
    provider_tool: str = "catalog_qualified_tool",
) -> QualifiedCapabilityBinding:
    return QualifiedCapabilityBinding(
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        normalized_capability="documents.invoice.get",
        provider_tool=provider_tool,
        operation_class=operation_class,
        qualification_hash="a" * 64,
    )


def _normalize_invoice(
    binding: QualifiedCapabilityBinding,
    structured_content: Mapping[str, Any],
) -> object:
    assert binding.normalized_capability == "documents.invoice.get"
    invoice = structured_content["invoice"]
    assert isinstance(invoice, Mapping)
    return {"invoice_id": invoice["id"]}


class FakeMCPHarness:
    def __init__(self) -> None:
        self.events: list[tuple[str, int, object]] = []
        self.clients: list[object] = []
        self.sessions: list[object] = []
        self.protocol_version = "2025-11-25"
        self.tools: object = [
            SimpleNamespace(name="get_provider_profile"),
            SimpleNamespace(name="PRIVATE_RAW_TOOL"),
        ]
        self.call_result: object = SimpleNamespace(
            structuredContent={
                "invoice": {"id": "invoice-123"},
                "session_id": "PRIVATE_SESSION_SENTINEL",
                "headers": {"X-Private": "PRIVATE_HEADER_SENTINEL"},
            },
            isError=False,
        )
        self.initialize_error: Exception | None = None
        self.list_error: Exception | None = None
        self.call_error: Exception | None = None
        self.transport_error: Exception | None = None
        self.initialize_http_status: int | None = None
        self.call_count = 0

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        harness = self

        class FakeAsyncClient:
            def __init__(
                self,
                *,
                headers: Mapping[str, str],
                timeout: httpx.Timeout,
                follow_redirects: bool,
                event_hooks: Mapping[str, list[Callable[[httpx.Response], object]]],
            ) -> None:
                self.headers = dict(headers)
                self.timeout = timeout
                self.follow_redirects = follow_redirects
                self.event_hooks = event_hooks
                self.closed = False
                harness.clients.append(self)

            async def __aenter__(self) -> FakeAsyncClient:
                return self

            async def __aexit__(self, *_args: object) -> None:
                self.closed = True

        @asynccontextmanager
        async def fake_streamable_http_client(
            url: str,
            *,
            http_client: object,
            terminate_on_close: bool,
        ):
            client_index = harness.clients.index(http_client)
            harness.events.append(("transport", client_index, url))
            if harness.transport_error is not None:
                raise harness.transport_error
            yield (
                f"read-{client_index}",
                f"write-{client_index}",
                lambda: "PRIVATE_SESSION_SENTINEL",
            )

        class FakeClientSession:
            def __init__(
                self,
                read_stream: object,
                write_stream: object,
                read_timeout_seconds: object,
            ) -> None:
                self.index = len(harness.sessions)
                self.read_stream = read_stream
                self.write_stream = write_stream
                self.read_timeout_seconds = read_timeout_seconds
                harness.sessions.append(self)

            async def __aenter__(self) -> FakeClientSession:
                return self

            async def __aexit__(self, *_args: object) -> None:
                return None

            async def initialize(self) -> object:
                harness.events.append(("initialize", self.index, None))
                if harness.initialize_http_status is not None:
                    request = httpx.Request(
                        "POST",
                        "https://provider.example/mcp",
                    )
                    response = httpx.Response(
                        harness.initialize_http_status,
                        request=request,
                    )
                    for hook in harness.clients[self.index].event_hooks["response"]:
                        observed = hook(response)
                        if hasattr(observed, "__await__"):
                            await observed
                if harness.initialize_error is not None:
                    raise harness.initialize_error
                return SimpleNamespace(protocolVersion=harness.protocol_version)

            async def list_tools(self) -> object:
                harness.events.append(("list_tools", self.index, None))
                if harness.list_error is not None:
                    raise harness.list_error
                return SimpleNamespace(tools=harness.tools)

            async def call_tool(
                self,
                name: str,
                arguments: dict[str, Any],
                read_timeout_seconds: object,
            ) -> object:
                harness.call_count += 1
                harness.events.append(
                    (
                        "call_tool",
                        self.index,
                        (name, arguments, read_timeout_seconds),
                    )
                )
                if harness.call_error is not None:
                    raise harness.call_error
                return harness.call_result

        monkeypatch.setattr(streamable_module.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(
            streamable_module,
            "streamable_http_client",
            fake_streamable_http_client,
        )
        monkeypatch.setattr(streamable_module, "ClientSession", FakeClientSession)


def _driver(
    *,
    header_factory: Callable[[ProviderConnection], object] | None = None,
    response_normalizer: Callable[
        [QualifiedCapabilityBinding, Mapping[str, Any]], object
    ] = _normalize_invoice,
) -> StreamableMCPDriver:
    manifest = load_provider_manifest(
        Path(__file__).resolve().parents[1]
        / "catalog/global/flowaccount/driver.json"
    )
    return StreamableMCPDriver(
        settings=_settings(),
        manifest=manifest,
        header_factory=header_factory,
        response_normalizer=response_normalizer,
    )


def _assert_sanitized_error(
    error: ProviderRuntimeError,
    *,
    code: str,
    dispatch_certainty: DispatchCertainty,
) -> None:
    rendered = f"{error!s} {error!r} {error.public_dict()}"
    assert error.code == code
    assert error.dispatch_certainty is dispatch_certainty
    assert str(error) == code
    assert "PRIVATE_" not in rendered
    assert "catalog_qualified_tool" not in rendered
    assert "flowaccount.example" not in rendered
    assert "X-Private" not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


def test_hosted_driver_models_and_protocol_are_closed_and_sanitized() -> None:
    assert get_type_hints(ProviderDriver)["provider"] is ProviderId

    result = ProviderCallResult(
        provider=ProviderId.FLOWACCOUNT,
        status_class=ProviderStatusClass.SUCCESS,
        normalized_data={"invoice": {"id": "invoice-123"}},
        dispatch_certainty=DispatchCertainty.DISPATCHED,
    )
    binding = _binding(provider_tool="PRIVATE_RAW_TOOL")

    assert result.model_dump(mode="json") == {
        "provider": "flowaccount",
        "status_class": "success",
        "normalized_data": {"invoice": {"id": "invoice-123"}},
        "dispatch_certainty": "dispatched",
    }
    assert "PRIVATE_RAW_TOOL" not in repr(binding)
    assert "provider_tool" not in binding.model_dump(mode="json")
    with pytest.raises(TypeError):
        result.normalized_data["invoice"]["id"] = "changed"  # type: ignore[index]
    with pytest.raises(ValidationError):
        ProviderDiscovery.model_validate(
            {
                "provider": "flowaccount",
                "status_class": "success",
                "normalized_data": {},
                "dispatch_certainty": "not_applicable",
                "raw_tools": ["PRIVATE_RAW_TOOL"],
            }
        )
    with pytest.raises(ValidationError):
        ProviderValidation.model_validate(
            {
                "provider": "flowaccount",
                "status_class": "success",
                "normalized_data": {},
                "dispatch_certainty": "not_applicable",
                "session_id": "PRIVATE_SESSION_SENTINEL",
            }
        )


def test_immutable_normalized_json_serializes_without_schema_warnings() -> None:
    result = ProviderDiscovery(
        provider=ProviderId.FLOWACCOUNT,
        status_class=ProviderStatusClass.SUCCESS,
        normalized_data={"capabilities": ["provider_profile.get"]},
        dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
    )

    with warnings.catch_warnings(record=True) as caught:
        assert result.model_dump(mode="json")["normalized_data"] == {
            "capabilities": ["provider_profile.get"]
        }

    assert caught == []
    with pytest.raises(TypeError):
        dict.__setitem__(result.normalized_data, "injected", True)
    with pytest.raises(TypeError):
        list.append(result.normalized_data["capabilities"], "injected")


@pytest.mark.parametrize(
    "reserved_key",
    ["sessionId", "session-id", "MCP-Session-Id", "toolName", "tool-name"],
)
def test_normalized_results_reject_reserved_metadata_key_variants(
    reserved_key: str,
) -> None:
    with pytest.raises(ValidationError):
        ProviderCallResult(
            provider=ProviderId.FLOWACCOUNT,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={reserved_key: "PRIVATE_BOUNDARY_SENTINEL"},
            dispatch_certainty=DispatchCertainty.DISPATCHED,
        )


@pytest.mark.asyncio
async def test_runtime_initializes_before_discovery_and_catalog_qualified_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    driver = _driver()

    discovery = await driver.discover(_connection())
    call_result = await driver.call(
        _connection(),
        _binding(provider_tool="catalog_qualified_tool"),
        InvoiceArguments(invoice_id="invoice-123"),
        OPERATION_ID,
    )

    assert discovery.model_dump(mode="json")["normalized_data"] == {
        "capabilities": ["provider_profile.get"],
        "resource_uri_sha256": discovery.normalized_data["resource_uri_sha256"],
    }
    assert call_result.model_dump(mode="json") == {
        "provider": "flowaccount",
        "status_class": "success",
        "normalized_data": {"invoice_id": "invoice-123"},
        "dispatch_certainty": "dispatched",
    }
    assert [event[0] for event in harness.events] == [
        "transport",
        "initialize",
        "list_tools",
        "transport",
        "initialize",
        "call_tool",
    ]
    serialized = f"{discovery.model_dump_json()} {call_result.model_dump_json()}"
    assert "PRIVATE_RAW_TOOL" not in serialized
    assert "PRIVATE_SESSION_SENTINEL" not in serialized
    assert "PRIVATE_HEADER_SENTINEL" not in serialized


@pytest.mark.asyncio
async def test_every_operation_uses_fresh_scoped_client_session_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    async def request_headers(connection: ProviderConnection) -> Mapping[str, str]:
        return {
            "X-Mercury-Test-Scope": (
                f"{connection.tenant_id}:{connection.id}:{connection.environment}"
            )
        }

    driver = _driver(header_factory=request_headers)
    first = _connection()
    second = _connection(
        tenant_id=OTHER_TENANT_ID,
        connection_id=OTHER_CONNECTION_ID,
        environment="production",
    )

    await driver.discover(first)
    await driver.validate_connection(second)
    await driver.call(
        first,
        _binding(ProviderOperationClass.READ),
        InvoiceArguments(invoice_id="invoice-123"),
        OPERATION_ID,
    )
    await driver.call(
        second,
        _binding(ProviderOperationClass.CREATE).model_copy(
            update={"environment": "production"}
        ),
        InvoiceArguments(invoice_id="invoice-456"),
        UUID("99999999-9999-4999-8999-999999999999"),
    )

    assert len({id(client) for client in harness.clients}) == 4
    assert len({id(session) for session in harness.sessions}) == 4
    assert all(client.closed for client in harness.clients)
    assert all(client.follow_redirects is False for client in harness.clients)
    assert [client.timeout.connect for client in harness.clients] == [5, 5, 5, 5]
    assert [client.timeout.read for client in harness.clients] == [30, 30, 30, 60]
    assert [
        session.read_timeout_seconds.total_seconds()
        for session in harness.sessions
    ] == [30, 30, 30, 60]
    assert harness.clients[0].headers != harness.clients[1].headers
    assert harness.clients[0].headers == harness.clients[2].headers
    assert harness.clients[1].headers == harness.clients[3].headers
    assert harness.clients[0].headers is not harness.clients[2].headers


@pytest.mark.asyncio
async def test_predispatch_failures_are_classified_without_raw_boundary_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    connection = _connection()

    async def broken_headers(_connection: ProviderConnection) -> Mapping[str, str]:
        raise RuntimeError("PRIVATE_HEADER_SENTINEL")

    with pytest.raises(ProviderRuntimeError) as auth_error:
        await _driver(header_factory=broken_headers).discover(connection)
    _assert_sanitized_error(
        auth_error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )

    harness.initialize_error = TimeoutError("PRIVATE_TIMEOUT_SENTINEL")
    with pytest.raises(ProviderRuntimeError) as timeout_error:
        await _driver().discover(connection)
    _assert_sanitized_error(
        timeout_error.value,
        code="provider_timeout_pre_dispatch",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )

    harness.initialize_error = None
    harness.protocol_version = "2024-11-05"
    with pytest.raises(ProviderRuntimeError) as schema_error:
        await _driver().discover(connection)
    _assert_sanitized_error(
        schema_error.value,
        code="provider_schema_changed",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )

    harness.protocol_version = "2025-11-25"
    harness.tools = SimpleNamespace(private="PRIVATE_SCHEMA_SENTINEL")
    with pytest.raises(ProviderRuntimeError) as response_error:
        await _driver().discover(connection)
    _assert_sanitized_error(
        response_error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )


@pytest.mark.asyncio
async def test_http_auth_and_unknown_transport_errors_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    request = httpx.Request("POST", "https://flowaccount.example/mcp")
    response = httpx.Response(
        401,
        request=request,
        text="PRIVATE_AUTH_BODY_SENTINEL",
    )
    harness.transport_error = httpx.HTTPStatusError(
        "PRIVATE_AUTH_BODY_SENTINEL",
        request=request,
        response=response,
    )

    with pytest.raises(ProviderRuntimeError) as auth_error:
        await _driver().discover(_connection())
    _assert_sanitized_error(
        auth_error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )

    harness.transport_error = RuntimeError(
        "PRIVATE_RAW_TOOL PRIVATE_SESSION_SENTINEL X-Private"
    )
    with pytest.raises(ProviderRuntimeError) as unavailable_error:
        await _driver().discover(_connection())
    _assert_sanitized_error(
        unavailable_error.value,
        code="provider_unavailable",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )


@pytest.mark.asyncio
async def test_request_scoped_status_observer_classifies_sdk_swallowed_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.initialize_http_status = 401
    harness.initialize_error = RuntimeError("PRIVATE_SDK_STREAM_SENTINEL")
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver().discover(_connection())

    _assert_sanitized_error(
        error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert len(harness.clients) == 1


@pytest.mark.asyncio
async def test_create_is_single_attempt_and_possible_dispatch_is_outcome_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.call_error = TimeoutError(
        "PRIVATE_RAW_TOOL PRIVATE_SESSION_SENTINEL PRIVATE_HEADER_SENTINEL"
    )
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver().call(
            _connection(),
            _binding(ProviderOperationClass.CREATE),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    assert harness.call_count == 1
    assert len(harness.clients) == 1
    assert len(harness.sessions) == 1
    _assert_sanitized_error(
        error.value,
        code="provider_outcome_unknown",
        dispatch_certainty=DispatchCertainty.UNKNOWN,
    )


@pytest.mark.asyncio
async def test_read_response_schema_and_normalizer_failures_are_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    harness.call_result = SimpleNamespace(
        structuredContent={
            "tool_name": "PRIVATE_RAW_TOOL",
            "session_id": "PRIVATE_SESSION_SENTINEL",
            "headers": {"X-Private": "PRIVATE_HEADER_SENTINEL"},
        },
        isError=False,
    )

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver().call(
            _connection(),
            _binding(),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.DISPATCHED,
    )


@pytest.mark.asyncio
async def test_normalized_reserved_metadata_is_response_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    def unsafe_normalizer(
        _binding: QualifiedCapabilityBinding,
        _structured_content: Mapping[str, Any],
    ) -> object:
        return {"session_id": "PRIVATE_SESSION_SENTINEL"}

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(response_normalizer=unsafe_normalizer).call(
            _connection(),
            _binding(),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.DISPATCHED,
    )


@pytest.mark.asyncio
async def test_tampered_connection_and_binding_fail_before_session_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    connection = _connection()
    object.__setattr__(connection, "tenant_id", UUID(int=0))

    with pytest.raises(ProviderRuntimeError) as connection_error:
        await _driver().discover(connection)
    _assert_sanitized_error(
        connection_error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )

    binding = _binding()
    object.__setattr__(binding, "provider_tool", "PRIVATE_RAW_TOOL\n")
    with pytest.raises(ProviderRuntimeError) as binding_error:
        await _driver().call(
            _connection(),
            binding,
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )
    _assert_sanitized_error(
        binding_error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []
