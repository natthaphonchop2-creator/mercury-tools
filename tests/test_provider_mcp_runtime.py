from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import warnings
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_type_hints
from uuid import UUID

import anyio
import httpx
import pytest
from mcp import ClientSession
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage, JSONRPCResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
)

import mercury_tools.providers.streamable_mcp as streamable_module
from mercury_tools.catalog.identity import canonical_json
from mercury_tools.config import Settings
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderDriver,
    ProviderOperationClass,
    ProviderQualificationState,
    ProviderRuntimeError,
    ProviderStatusClass,
    ProviderValidation,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.manifest import load_provider_manifest
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.providers.registry import build_provider_registry
from mercury_tools.providers.streamable_mcp import (
    ProviderAuthHeader,
    ProviderAuthHeaders,
    StreamableMCPDriver,
)

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
OTHER_TENANT_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
AUTH_USER_ID = UUID("44444444-4444-4444-8444-444444444444")
CONNECTION_ID = UUID("55555555-5555-4555-8555-555555555555")
OTHER_CONNECTION_ID = UUID("66666666-6666-4666-8666-666666666666")
OPERATION_ID = UUID("77777777-7777-4777-8777-777777777777")
NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
_DEFAULT = object()


class InvoiceArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str


class InvoiceResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str


class AlternateInvoiceArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str


class AlternateInvoiceResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str


class OpenInvoiceModel(BaseModel):
    invoice_id: str


class AliasedInvoiceArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str = Field(alias="invoiceId")


class AliasedInvoiceResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str = Field(alias="invoiceId")


class OpenMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    note: str


class NestedOpenArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    metadata: OpenMetadata


class NestedOpenResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    metadata: OpenMetadata


class ArbitraryMappingArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    payload: dict[str, object]


class UnsafeSerializedArguments(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str

    @field_serializer("invoice_id", return_type=str)
    def serialize_invoice_id(self, _value: str) -> object:
        return {"PRIVATE_UNBOUND_REQUEST_FIELD": True}


class UnsafeSerializedResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str

    @field_serializer("invoice_id", return_type=str)
    def serialize_invoice_id(self, _value: str) -> object:
        return {"PRIVATE_UNBOUND_RESPONSE_FIELD": True}


def _wire_schema_sha256(model: type[BaseModel]) -> str:
    return hashlib.sha256(
        canonical_json(
            model.model_json_schema(
                by_alias=True,
                mode="serialization",
            )
        ).encode("utf-8")
    ).hexdigest()


INVOICE_ARGUMENTS_SCHEMA_SHA256 = _wire_schema_sha256(InvoiceArguments)
INVOICE_RESPONSE_SCHEMA_SHA256 = _wire_schema_sha256(InvoiceResponse)


class BoundaryMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    alias: str


class BoundaryResponse(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        revalidate_instances="always",
    )

    invoice_id: str
    metadata: BoundaryMetadata


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
        credential_envelope_ids=(UUID("88888888-8888-4888-8888-888888888888"),),
        created_at=NOW,
        updated_at=NOW,
    )


def _binding(
    operation_class: ProviderOperationClass = ProviderOperationClass.READ,
    *,
    provider_tool: str = "catalog_qualified_tool",
    normalized_capability: str = "documents.invoice.get",
    qualification_hash: str = "a" * 64,
) -> QualifiedCapabilityBinding:
    return QualifiedCapabilityBinding(
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        normalized_capability=normalized_capability,
        provider_tool=provider_tool,
        operation_class=operation_class,
        qualification_hash=qualification_hash,
    )


def _verified_binding(
    operation_class: ProviderOperationClass = ProviderOperationClass.READ,
    *,
    environment: str = "sandbox",
    provider_tool: str = "catalog_qualified_tool",
    normalized_capability: str = "documents.invoice.get",
    qualification_hash: str = "a" * 64,
    resource_uri_sha256: str = ("a938c191dfa72244698c04a394f2021d7f9a7c7bce71591815696b137e9f349d"),
    qualification_state: ProviderQualificationState = ProviderQualificationState.ENABLED,
    request_schema_sha256: str = INVOICE_ARGUMENTS_SCHEMA_SHA256,
    response_schema_sha256: str = INVOICE_RESPONSE_SCHEMA_SHA256,
) -> VerifiedRuntimeBinding:
    return VerifiedRuntimeBinding(
        qualification_state=qualification_state,
        provider=ProviderId.FLOWACCOUNT,
        environment=environment,
        resource_uri_sha256=resource_uri_sha256,
        normalized_capability=normalized_capability,
        capability_version="1.0.0",
        provider_tool=provider_tool,
        operation_class=operation_class,
        request_schema_sha256=request_schema_sha256,
        response_schema_sha256=response_schema_sha256,
        qualification_hash=qualification_hash,
    )


def _auth_headers(
    *,
    provider: ProviderId = ProviderId.FLOWACCOUNT,
    authorization_method: AuthorizationMethod = AuthorizationMethod.OAUTH2_PKCE,
    name: str = "X-Mercury-Test-Auth",
    value: str = "PRIVATE_AUTH_HEADER_VALUE",
) -> ProviderAuthHeaders:
    return ProviderAuthHeaders(
        provider=provider,
        authorization_method=authorization_method,
        headers=(ProviderAuthHeader(name=name, value=value),),
    )


def _verify_binding(
    _connection: ProviderConnection,
    _binding: QualifiedCapabilityBinding,
    _resource_uri_sha256: str,
) -> VerifiedRuntimeBinding:
    return _verified_binding(
        operation_class=_binding.operation_class,
        environment=_binding.environment,
        provider_tool=_binding.provider_tool,
        normalized_capability=_binding.normalized_capability,
        qualification_hash=_binding.qualification_hash,
        resource_uri_sha256=_resource_uri_sha256,
    )


def _resolve_invoice_request_model(
    binding: VerifiedRuntimeBinding,
) -> type[BaseModel]:
    assert binding.request_schema_sha256 == INVOICE_ARGUMENTS_SCHEMA_SHA256
    return InvoiceArguments


def _resolve_invoice_response_model(
    binding: VerifiedRuntimeBinding,
) -> type[BaseModel]:
    assert binding.response_schema_sha256 == INVOICE_RESPONSE_SCHEMA_SHA256
    return InvoiceResponse


def _normalize_invoice(
    binding: VerifiedRuntimeBinding,
    structured_content: Mapping[str, Any],
) -> BaseModel:
    assert binding.normalized_capability == "documents.invoice.get"
    invoice = structured_content["invoice"]
    assert isinstance(invoice, Mapping)
    return InvoiceResponse(invoice_id=invoice["id"])


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
        self.initialize_delay = 0.0
        self.list_delay = 0.0
        self.call_delay = 0.0
        self.session_exit_callback: Callable[[], None] | None = None
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
                if harness.session_exit_callback is not None:
                    harness.session_exit_callback()
                return None

            async def initialize(self) -> object:
                harness.events.append(("initialize", self.index, None))
                await asyncio.sleep(harness.initialize_delay)
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
                await asyncio.sleep(harness.list_delay)
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
                await asyncio.sleep(harness.call_delay)
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


class FakeOperationDeadline:
    def __init__(self) -> None:
        self.elapsed = 0.0
        self.limit = 0.0
        self.expires_at = 0.0
        self.expired = False
        self.initial_seconds: list[float] = []
        self.rescheduled_seconds: list[float] = []

    def start(self, seconds: float) -> FakeOperationDeadline:
        self.limit = seconds
        self.expires_at = asyncio.get_running_loop().time() + 3600
        self.initial_seconds.append(seconds)
        return self

    def reschedule(self, seconds: float) -> None:
        self.limit = seconds
        self.rescheduled_seconds.append(seconds)
        self.check()

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds

    def expire(self) -> None:
        self.expired = True

    def check(self) -> None:
        self.remaining()

    def remaining(self) -> float:
        if self.expired or self.elapsed >= self.limit:
            raise streamable_module._OperationDeadlineExpired
        return self.limit - self.elapsed


def _install_fake_operation_deadline(
    monkeypatch: pytest.MonkeyPatch,
    deadline: FakeOperationDeadline,
) -> None:
    monkeypatch.setattr(
        streamable_module._OperationDeadline,
        "start",
        classmethod(lambda _cls, seconds: deadline.start(seconds)),
    )


def _driver(
    *,
    header_factory: Callable[[ProviderConnection], object] | None | object = _DEFAULT,
    binding_verifier: Callable[[ProviderConnection, QualifiedCapabilityBinding, str], object]
    | None = _verify_binding,
    response_normalizer: Callable[
        [VerifiedRuntimeBinding, Mapping[str, Any]], object
    ] = _normalize_invoice,
    request_model_resolver: Callable[[VerifiedRuntimeBinding], object]
    | None = _resolve_invoice_request_model,
    response_model_resolver: Callable[[VerifiedRuntimeBinding], object]
    | None = _resolve_invoice_response_model,
) -> StreamableMCPDriver:
    manifest = load_provider_manifest(
        Path(__file__).resolve().parents[1] / "catalog/global/flowaccount/driver.json"
    )
    return StreamableMCPDriver(
        settings=_settings(),
        manifest=manifest,
        header_factory=(
            (lambda _connection: _auth_headers()) if header_factory is _DEFAULT else header_factory
        ),
        binding_verifier=binding_verifier,
        response_normalizer=response_normalizer,
        request_model_resolver=request_model_resolver,
        response_model_resolver=response_model_resolver,
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


def test_wire_schema_digest_is_deterministic_canonical_sha256() -> None:
    assert INVOICE_ARGUMENTS_SCHEMA_SHA256 == (
        "e84a9c87a57519c5cd9319b40d521bf191220ecac0f77bad5f2c2c4c962953e4"
    )
    assert INVOICE_RESPONSE_SCHEMA_SHA256 == (
        "bbcfbd66bf01a44d3bd842e0dd9fee3e0b76fd39f77681aa5486bd3b5ce5c85a"
    )
    assert streamable_module.wire_schema_sha256(InvoiceArguments) == INVOICE_ARGUMENTS_SCHEMA_SHA256
    assert streamable_module.wire_schema_sha256(InvoiceResponse) == INVOICE_RESPONSE_SCHEMA_SHA256
    assert (
        streamable_module.wire_schema_sha256(AlternateInvoiceArguments)
        != INVOICE_ARGUMENTS_SCHEMA_SHA256
    )
    with pytest.raises(TypeError):
        streamable_module.wire_schema_sha256(OpenInvoiceModel)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "arguments",
        "verified",
        "request_model_resolver",
        "response_model_resolver",
    ),
    [
        (
            AlternateInvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(),
            lambda _binding: InvoiceArguments,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments.model_construct(invoice_id=123),
            _verified_binding(),
            _resolve_invoice_request_model,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(request_schema_sha256="d" * 64),
            lambda _binding: InvoiceArguments,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(response_schema_sha256="e" * 64),
            _resolve_invoice_request_model,
            lambda _binding: InvoiceResponse,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(),
            None,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(),
            _resolve_invoice_request_model,
            None,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(),
            lambda _binding: OpenInvoiceModel,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(),
            _resolve_invoice_request_model,
            lambda _binding: OpenInvoiceModel,
        ),
        (
            NestedOpenArguments(
                metadata=OpenMetadata(
                    note="safe",
                    PRIVATE_UNBOUND_NESTED_FIELD=True,
                )
            ),
            _verified_binding(request_schema_sha256=_wire_schema_sha256(NestedOpenArguments)),
            lambda _binding: NestedOpenArguments,
            _resolve_invoice_response_model,
        ),
        (
            ArbitraryMappingArguments(payload={"PRIVATE_UNBOUND_MAPPING_FIELD": True}),
            _verified_binding(request_schema_sha256=_wire_schema_sha256(ArbitraryMappingArguments)),
            lambda _binding: ArbitraryMappingArguments,
            _resolve_invoice_response_model,
        ),
        (
            UnsafeSerializedArguments(invoice_id="invoice-123"),
            _verified_binding(request_schema_sha256=_wire_schema_sha256(UnsafeSerializedArguments)),
            lambda _binding: UnsafeSerializedArguments,
            _resolve_invoice_response_model,
        ),
        (
            InvoiceArguments(invoice_id="invoice-123"),
            _verified_binding(response_schema_sha256=_wire_schema_sha256(NestedOpenResponse)),
            _resolve_invoice_request_model,
            lambda _binding: NestedOpenResponse,
        ),
    ],
    ids=[
        "alternate-request-model",
        "request-model-bypassed-validation",
        "request-schema-hash-mismatch",
        "response-schema-hash-mismatch",
        "missing-request-model-resolver",
        "missing-response-model-resolver",
        "open-request-model",
        "open-response-model",
        "nested-open-request-model",
        "arbitrary-mapping-request-model",
        "custom-request-serializer-outside-schema",
        "nested-open-response-model",
    ],
)
async def test_catalog_schema_models_and_hashes_fail_closed_before_auth_or_session(
    monkeypatch: pytest.MonkeyPatch,
    arguments: BaseModel,
    verified: VerifiedRuntimeBinding,
    request_model_resolver: Callable[[VerifiedRuntimeBinding], object] | None,
    response_model_resolver: Callable[[VerifiedRuntimeBinding], object] | None,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    header_calls = 0

    def headers(_connection: ProviderConnection) -> ProviderAuthHeaders:
        nonlocal header_calls
        header_calls += 1
        return _auth_headers()

    def verifier(
        _connection: ProviderConnection,
        _binding: QualifiedCapabilityBinding,
        _resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        return verified

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            header_factory=headers,
            binding_verifier=verifier,
            request_model_resolver=request_model_resolver,
            response_model_resolver=response_model_resolver,
        ).call(
            _connection(),
            _binding(),
            arguments,
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert header_calls == 0
    assert harness.clients == []
    assert harness.sessions == []
    assert harness.call_count == 0


@pytest.mark.asyncio
async def test_alias_policy_is_identical_for_wire_schema_request_and_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    def verifier(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        return _verify_binding(
            connection,
            binding,
            resource_uri_sha256,
        ).model_copy(
            update={
                "request_schema_sha256": _wire_schema_sha256(AliasedInvoiceArguments),
                "response_schema_sha256": _wire_schema_sha256(AliasedInvoiceResponse),
            }
        )

    def normalizer(
        _binding: VerifiedRuntimeBinding,
        _structured_content: Mapping[str, Any],
    ) -> BaseModel:
        return AliasedInvoiceResponse(invoiceId="invoice-123")

    result = await _driver(
        binding_verifier=verifier,
        request_model_resolver=lambda _binding: AliasedInvoiceArguments,
        response_model_resolver=lambda _binding: AliasedInvoiceResponse,
        response_normalizer=normalizer,
    ).call(
        _connection(),
        _binding(),
        AliasedInvoiceArguments(invoiceId="invoice-123"),
        OPERATION_ID,
    )

    call_event = next(event for event in harness.events if event[0] == "call_tool")
    _tool, serialized_arguments, _timeout = call_event[2]
    assert serialized_arguments == {"invoiceId": "invoice-123"}
    assert result.normalized_data == {"invoiceId": "invoice-123"}


@pytest.mark.asyncio
async def test_custom_response_serializer_cannot_escape_bound_wire_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    def verifier(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        return _verify_binding(
            connection,
            binding,
            resource_uri_sha256,
        ).model_copy(
            update={"response_schema_sha256": _wire_schema_sha256(UnsafeSerializedResponse)}
        )

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            binding_verifier=verifier,
            response_model_resolver=lambda _binding: UnsafeSerializedResponse,
            response_normalizer=lambda _binding, _content: UnsafeSerializedResponse(
                invoice_id="invoice-123"
            ),
        ).call(
            _connection(),
            _binding(),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    assert harness.call_count == 1
    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.DISPATCHED,
    )


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
@pytest.mark.parametrize(
    ("untrusted", "verified"),
    [
        (
            _binding(provider_tool="delete_everything"),
            _verified_binding(provider_tool="catalog_qualified_tool"),
        ),
        (
            _binding(qualification_hash="d" * 64),
            _verified_binding(qualification_hash="a" * 64),
        ),
        (
            _binding(
                ProviderOperationClass.READ,
                provider_tool="create_invoice",
                normalized_capability="documents.invoice.create",
                qualification_hash="e" * 64,
            ),
            _verified_binding(
                ProviderOperationClass.CREATE,
                provider_tool="create_invoice",
                normalized_capability="documents.invoice.create",
                qualification_hash="e" * 64,
            ),
        ),
        (
            _binding(),
            _verified_binding(
                qualification_state=ProviderQualificationState.DISABLED,
            ),
        ),
        (
            _binding(),
            _verified_binding(
                qualification_state=ProviderQualificationState.SUPERSEDED,
            ),
        ),
        (
            _binding(),
            _verified_binding(resource_uri_sha256="f" * 64),
        ),
    ],
    ids=[
        "forged-tool",
        "forged-qualification-hash",
        "create-labelled-read",
        "disabled",
        "superseded",
        "resource-uri-changed",
    ],
)
async def test_untrusted_binding_must_match_exact_enabled_verified_runtime_binding(
    monkeypatch: pytest.MonkeyPatch,
    untrusted: QualifiedCapabilityBinding,
    verified: VerifiedRuntimeBinding,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    verifier_inputs: list[tuple[UUID, str, str]] = []
    header_calls = 0

    def verifier(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        verifier_inputs.append((connection.id, binding.qualification_hash, resource_uri_sha256))
        return verified

    def headers(_connection: ProviderConnection) -> ProviderAuthHeaders:
        nonlocal header_calls
        header_calls += 1
        return _auth_headers()

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            header_factory=headers,
            binding_verifier=verifier,
        ).call(
            _connection(),
            untrusted,
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert verifier_inputs == [
        (
            CONNECTION_ID,
            untrusted.qualification_hash,
            "a938c191dfa72244698c04a394f2021d7f9a7c7bce71591815696b137e9f349d",
        )
    ]
    assert header_calls == 0
    assert harness.clients == []
    assert harness.sessions == []
    assert harness.call_count == 0


@pytest.mark.asyncio
async def test_missing_trusted_binding_verifier_fails_before_auth_or_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    header_calls = 0

    def headers(_connection: ProviderConnection) -> ProviderAuthHeaders:
        nonlocal header_calls
        header_calls += 1
        return _auth_headers()

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            header_factory=headers,
            binding_verifier=None,
        ).call(
            _connection(),
            _binding(),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert header_calls == 0
    assert harness.clients == []
    assert harness.sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header_factory",
    [
        None,
        lambda _connection: {"Authorization": "PRIVATE_RAW_MAPPING"},
    ],
    ids=["missing", "untyped-mapping"],
)
async def test_missing_or_untyped_auth_adapter_result_fails_before_transport(
    monkeypatch: pytest.MonkeyPatch,
    header_factory: object,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(header_factory=header_factory).discover(_connection())

    _assert_sanitized_error(
        error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []


@pytest.mark.asyncio
async def test_connection_auth_method_must_match_manifest_before_adapter_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    adapter_calls = 0

    def headers(_connection: ProviderConnection) -> ProviderAuthHeaders:
        nonlocal adapter_calls
        adapter_calls += 1
        return _auth_headers()

    connection = _connection().model_copy(
        update={"authorization_method": AuthorizationMethod.PROVIDER_CREDENTIALS}
    )
    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(header_factory=headers).discover(connection)

    _assert_sanitized_error(
        error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert adapter_calls == 0
    assert harness.clients == []
    assert harness.sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "auth_headers",
    [
        _auth_headers(provider=ProviderId.PEAK),
        _auth_headers(authorization_method=AuthorizationMethod.PROVIDER_CREDENTIALS),
    ],
    ids=["wrong-provider", "wrong-authorization-method"],
)
async def test_auth_header_result_identity_must_match_connection_and_manifest(
    monkeypatch: pytest.MonkeyPatch,
    auth_headers: ProviderAuthHeaders,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(header_factory=lambda _connection: auth_headers).discover(_connection())

    _assert_sanitized_error(
        error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        (
            ProviderAuthHeader.model_construct(
                name="X-Mercury-Auth",
                value="PRIVATE_ONE",
            ),
            ProviderAuthHeader.model_construct(
                name="x-mercury-auth",
                value="PRIVATE_TWO",
            ),
        ),
        (
            ProviderAuthHeader.model_construct(
                name="Mcp-Protocol-Version",
                value="PRIVATE_PROTOCOL_CONTROL",
            ),
        ),
        (
            ProviderAuthHeader.model_construct(
                name="Last-Event-ID",
                value="PRIVATE_RESUMPTION_CONTROL",
            ),
        ),
        (
            ProviderAuthHeader.model_construct(
                name="Connection",
                value="PRIVATE_HOP_BY_HOP",
            ),
        ),
        (
            ProviderAuthHeader.model_construct(
                name="Proxy-Authorization",
                value="PRIVATE_PROXY_AUTH",
            ),
        ),
    ],
    ids=[
        "case-insensitive-duplicate",
        "mcp-protocol-version",
        "resumption",
        "connection",
        "proxy-authorization",
    ],
)
async def test_auth_header_result_rejects_duplicates_and_protocol_controls(
    monkeypatch: pytest.MonkeyPatch,
    headers: tuple[ProviderAuthHeader, ...],
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    malformed = ProviderAuthHeaders.model_construct(headers=headers)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(header_factory=lambda _connection: malformed).discover(_connection())

    _assert_sanitized_error(
        error.value,
        code="provider_auth_required",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []


async def _exercise_real_transport_log_scenario(scenario: str) -> tuple[str, ...]:
    endpoint = "https://PRIVATE_ENDPOINT_SENTINEL.example/mcp"
    observed_requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            observed_requests.append("DELETE")
            raise httpx.ConnectError(
                "PRIVATE_TEARDOWN_SENTINEL",
                request=request,
            )
        if request.method == "GET":
            observed_requests.append("GET")
            return httpx.Response(405, request=request)

        payload = json.loads(request.content)
        method = payload.get("method")
        observed_requests.append(f"POST:{method}")
        if method == "initialize":
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": "PRIVATE_SESSION_SENTINEL",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "serverInfo": {
                            "name": "PRIVATE_SERVER_SENTINEL",
                            "version": "1.0.0",
                        },
                    },
                },
                request=request,
            )
        if method == "notifications/initialized":
            return httpx.Response(202, request=request)
        if method == "tools/call":
            if scenario == "malformed":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b"PRIVATE_MALFORMED_RESPONSE_SENTINEL",
                    request=request,
                )
            raise httpx.ReadTimeout(
                "PRIVATE_TIMEOUT_SENTINEL",
                request=request,
            )
        return httpx.Response(202, request=request)

    try:
        async with (
            httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                timeout=httpx.Timeout(1.0),
            ) as client,
            streamable_module.streamable_http_client(
                endpoint,
                http_client=client,
                terminate_on_close=True,
            ) as (read_stream, write_stream, _get_session_id),
            ClientSession(
                read_stream,
                write_stream,
                read_timeout_seconds=timedelta(seconds=0.25),
            ) as session,
        ):
            await session.initialize()
            await session.call_tool(
                "PRIVATE_PROVIDER_TOOL_SENTINEL",
                {"invoice_id": "PRIVATE_ARGUMENT_SENTINEL"},
                read_timeout_seconds=timedelta(seconds=0.25),
            )
    except Exception:
        pass
    return tuple(observed_requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_real_mcp_transport_logs_are_suppressed_for_process_lifetime(
    caplog: pytest.LogCaptureFixture,
    level: int,
) -> None:
    caplog.set_level(level)

    malformed_requests = await _exercise_real_transport_log_scenario("malformed")
    timeout_requests = await _exercise_real_transport_log_scenario("timeout")

    for requests in (malformed_requests, timeout_requests):
        assert "POST:initialize" in requests
        assert "POST:tools/call" in requests
        assert "DELETE" in requests

    formatter = logging.Formatter()
    rendered = "\n".join(
        formatter.format(record)
        for record in caplog.records
        if record.name == "mcp.client.streamable_http"
    )
    for sentinel in (
        "PRIVATE_ENDPOINT_SENTINEL",
        "PRIVATE_SESSION_SENTINEL",
        "PRIVATE_SERVER_SENTINEL",
        "PRIVATE_PROVIDER_TOOL_SENTINEL",
        "PRIVATE_ARGUMENT_SENTINEL",
        "PRIVATE_MALFORMED_RESPONSE_SENTINEL",
        "PRIVATE_TIMEOUT_SENTINEL",
        "PRIVATE_TEARDOWN_SENTINEL",
    ):
        assert sentinel not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_provider_log_boundary_covers_descendants_and_is_concurrency_safe(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    level: int,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    caplog.set_level(level)

    async def logging_headers(
        _connection: ProviderConnection,
    ) -> ProviderAuthHeaders:
        for logger_name in (
            "",
            "client.child",
            "mcp.client.session",
            "httpx.child",
            "httpcore.connection.child",
            "httpcore.http11.child",
        ):
            raw_error = RuntimeError("PRIVATE_LOG_EXCEPTION_SENTINEL")
            logging.getLogger(logger_name).log(
                level,
                "PRIVATE_PROVIDER_LOG_SENTINEL %s",
                "PRIVATE_LOG_ARGUMENT_SENTINEL",
                exc_info=(RuntimeError, raw_error, raw_error.__traceback__),
                stack_info=True,
            )
        await asyncio.sleep(0)
        return _auth_headers()

    async def unrelated_log() -> None:
        await asyncio.sleep(0)
        logging.getLogger("httpx.child").log(
            level,
            "PUBLIC_CONCURRENT_LOG_SENTINEL",
        )

    await asyncio.gather(
        _driver(header_factory=logging_headers).discover(_connection()),
        unrelated_log(),
    )

    rendered = caplog.text
    assert "PRIVATE_PROVIDER_LOG_SENTINEL" not in rendered
    assert "PRIVATE_LOG_ARGUMENT_SENTINEL" not in rendered
    assert "PRIVATE_LOG_EXCEPTION_SENTINEL" not in rendered
    assert "provider_runtime_log_redacted" in rendered
    assert "PUBLIC_CONCURRENT_LOG_SENTINEL" in rendered
    redacted_records = [
        record
        for record in caplog.records
        if record.getMessage() == "provider_runtime_log_redacted"
    ]
    assert redacted_records
    assert all(record.msg == "provider_runtime_log_redacted" for record in redacted_records)
    assert all(record.args == () for record in redacted_records)
    assert all(record.exc_info is None for record in redacted_records)
    assert all(record.stack_info is None for record in redacted_records)


class _StructuredLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            record.__dict__,
            default=str,
            sort_keys=True,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_provider_log_boundary_redacts_chained_and_late_extra_attributes(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    level: int,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    caplog.set_level(level)
    installed_factory = logging.getLogRecordFactory()

    def preexisting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = logging.LogRecord(*args, **kwargs)
        record.chained_copy = record.msg
        record.pathname = record.msg
        record.processName = record.msg
        return record

    logging.setLogRecordFactory(preexisting_factory)
    streamable_module._install_provider_log_record_boundary()

    async def logging_headers(
        _connection: ProviderConnection,
    ) -> ProviderAuthHeaders:
        logging.getLogger("mcp.client.session.descendant").log(
            level,
            "PRIVATE_CHAINED_LOG_SENTINEL",
            extra={
                "provider_secret": "PRIVATE_EXTRA_LOG_SENTINEL",
                "request_id": "request-safe-123",
            },
        )
        return _auth_headers()

    async def unrelated_log() -> None:
        await asyncio.sleep(0)
        logging.getLogger("httpx.public").log(
            level,
            "PUBLIC_STRUCTURED_LOG_SENTINEL",
            extra={
                "public_detail": "PUBLIC_EXTRA_LOG_SENTINEL",
                "request_id": "request-public-456",
            },
        )

    try:
        await asyncio.gather(
            _driver(header_factory=logging_headers).discover(_connection()),
            unrelated_log(),
        )
    finally:
        logging.setLogRecordFactory(installed_factory)

    formatter = _StructuredLogFormatter()
    rendered = "\n".join(formatter.format(record) for record in caplog.records)
    assert "PRIVATE_CHAINED_LOG_SENTINEL" not in rendered
    assert "PRIVATE_EXTRA_LOG_SENTINEL" not in rendered
    assert "provider_runtime_log_redacted" in rendered
    provider_record = next(
        record for record in caplog.records if record.name == "mcp.client.session.descendant"
    )
    assert provider_record.request_id == "request-safe-123"
    assert provider_record.chained_copy == "provider_runtime_log_redacted"
    assert provider_record.provider_secret == "provider_runtime_log_redacted"
    assert provider_record.pathname == "provider_runtime_log_redacted"
    assert provider_record.processName != "PRIVATE_CHAINED_LOG_SENTINEL"
    assert "PUBLIC_STRUCTURED_LOG_SENTINEL" in rendered
    assert "PUBLIC_EXTRA_LOG_SENTINEL" in rendered
    public_record = next(record for record in caplog.records if record.name == "httpx.public")
    assert public_record.request_id == "request-public-456"
    assert public_record.chained_copy == "PUBLIC_STRUCTURED_LOG_SENTINEL"


async def _exercise_actual_client_call_tool_warning() -> None:
    server_send, client_receive = anyio.create_memory_object_stream(4)
    client_send, server_receive = anyio.create_memory_object_stream(4)

    async def respond() -> None:
        async with server_send, server_receive:
            async for request in server_receive:
                root = request.message.root
                method = getattr(root, "method", None)
                request_id = getattr(root, "id", None)
                if request_id is None:
                    continue
                if method == "initialize":
                    result = {
                        "protocolVersion": "2025-11-25",
                        "capabilities": {},
                        "serverInfo": {
                            "name": "PRIVATE_SERVER_SENTINEL",
                            "version": "1",
                        },
                    }
                elif method == "tools/call":
                    result = {
                        "content": [],
                        "structuredContent": {
                            "invoice_id": "invoice-123",
                        },
                        "isError": False,
                    }
                elif method == "tools/list":
                    result = {"tools": []}
                else:
                    result = {}
                await server_send.send(
                    SessionMessage(
                        JSONRPCMessage(
                            JSONRPCResponse(
                                jsonrpc="2.0",
                                id=request_id,
                                result=result,
                            )
                        )
                    )
                )

    async with anyio.create_task_group() as task_group:
        task_group.start_soon(respond)
        with streamable_module._provider_log_suppression():
            async with ClientSession(
                client_receive,
                client_send,
                read_timeout_seconds=timedelta(seconds=0.1),
            ) as session:
                await session.initialize()
                result = await session.call_tool(
                    "PRIVATE_PROVIDER_TOOL_SENTINEL",
                    {"invoice_id": "PRIVATE_ARGUMENT_SENTINEL"},
                    read_timeout_seconds=timedelta(seconds=0.1),
                )
                assert result.isError is False
        task_group.cancel_scope.cancel()


@pytest.mark.asyncio
@pytest.mark.parametrize("level", [logging.INFO, logging.DEBUG])
async def test_actual_successful_client_session_warning_is_redacted_before_handlers(
    caplog: pytest.LogCaptureFixture,
    level: int,
) -> None:
    caplog.set_level(level)

    await _exercise_actual_client_call_tool_warning()

    rendered = caplog.text
    assert "PRIVATE_PROVIDER_TOOL_SENTINEL" not in rendered
    assert "PRIVATE_ARGUMENT_SENTINEL" not in rendered
    assert "PRIVATE_SERVER_SENTINEL" not in rendered
    assert "provider_runtime_log_redacted" in rendered


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
async def test_registry_injects_trusted_runtime_and_response_schema_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    registry = build_provider_registry(
        settings=_settings(),
        manifest_root=Path(__file__).resolve().parents[1] / "catalog/global",
        header_factories={
            AuthorizationMethod.OAUTH2_PKCE: lambda _connection: _auth_headers(),
        },
        binding_verifier=_verify_binding,
        response_normalizer=_normalize_invoice,
        request_model_resolver=_resolve_invoice_request_model,
        response_model_resolver=_resolve_invoice_response_model,
    )

    result = await registry.get(ProviderId.FLOWACCOUNT).call(
        _connection(),
        _binding(),
        InvoiceArguments(invoice_id="invoice-123"),
        OPERATION_ID,
    )

    assert result.normalized_data == {"invoice_id": "invoice-123"}
    assert [event[0] for event in harness.events] == [
        "transport",
        "initialize",
        "call_tool",
    ]


@pytest.mark.asyncio
async def test_every_operation_uses_fresh_scoped_client_session_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    async def request_headers(connection: ProviderConnection) -> ProviderAuthHeaders:
        return _auth_headers(
            name="X-Mercury-Test-Scope",
            value=f"{connection.tenant_id}:{connection.id}:{connection.environment}",
        )

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
        _binding(ProviderOperationClass.CREATE).model_copy(update={"environment": "production"}),
        InvoiceArguments(invoice_id="invoice-456"),
        UUID("99999999-9999-4999-8999-999999999999"),
    )

    assert len({id(client) for client in harness.clients}) == 4
    assert len({id(session) for session in harness.sessions}) == 4
    assert all(client.closed for client in harness.clients)
    assert all(client.follow_redirects is False for client in harness.clients)
    assert [client.timeout.connect for client in harness.clients] == [5, 5, 5, 5]
    assert [client.timeout.read for client in harness.clients] == [30, 30, 30, 60]
    assert [session.read_timeout_seconds.total_seconds() for session in harness.sessions] == [
        30,
        30,
        30,
        60,
    ]
    assert harness.clients[0].headers != harness.clients[1].headers
    assert harness.clients[0].headers == harness.clients[2].headers
    assert harness.clients[1].headers == harness.clients[3].headers
    assert harness.clients[0].headers is not harness.clients[2].headers


@pytest.mark.asyncio
async def test_driver_retains_its_own_revalidated_manifest_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    manifest = load_provider_manifest(
        Path(__file__).resolve().parents[1] / "catalog/global/flowaccount/driver.json"
    )
    driver = StreamableMCPDriver(
        settings=_settings(),
        manifest=manifest,
        header_factory=lambda _connection: _auth_headers(),
        binding_verifier=_verify_binding,
        response_normalizer=_normalize_invoice,
        response_model_resolver=_resolve_invoice_response_model,
    )
    object.__setattr__(manifest, "protocol_version", "PRIVATE_FORGED_PROTOCOL")

    result = await driver.validate_connection(_connection())

    assert result.normalized_data["protocol_version"] == "2025-11-25"
    assert [event[0] for event in harness.events] == ["transport", "initialize"]


@pytest.mark.asyncio
async def test_operation_deadline_includes_header_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    async def slow_headers(
        _connection: ProviderConnection,
    ) -> ProviderAuthHeaders:
        await asyncio.sleep(0.08)
        return _auth_headers()

    driver = _driver(header_factory=slow_headers)
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.05)
    started = time.monotonic()

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.discover(_connection())

    assert time.monotonic() - started < 0.15
    _assert_sanitized_error(
        error.value,
        code="provider_timeout_pre_dispatch",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "blocked_seam",
    [
        "header-factory",
        "binding-verifier",
        "request-model-resolver",
        "response-model-resolver",
        "request-serialization",
    ],
)
async def test_blocking_synchronous_predispatch_seams_cannot_dispatch_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
    blocked_seam: str,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    arguments: BaseModel = InvoiceArguments(invoice_id="invoice-123")
    verified = _verified_binding()

    def header_factory(_connection: ProviderConnection) -> ProviderAuthHeaders:
        if blocked_seam == "header-factory":
            time.sleep(0.08)
        return _auth_headers()

    def verifier(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        if blocked_seam == "binding-verifier":
            time.sleep(0.08)
        return verified.model_copy(
            update={
                "provider": connection.provider,
                "environment": binding.environment,
                "resource_uri_sha256": resource_uri_sha256,
                "normalized_capability": binding.normalized_capability,
                "provider_tool": binding.provider_tool,
                "operation_class": binding.operation_class,
                "qualification_hash": binding.qualification_hash,
            }
        )

    def request_resolver(
        _binding: VerifiedRuntimeBinding,
    ) -> type[BaseModel]:
        if blocked_seam == "request-model-resolver":
            time.sleep(0.08)
        return type(arguments)

    def response_resolver(
        _binding: VerifiedRuntimeBinding,
    ) -> type[BaseModel]:
        if blocked_seam == "response-model-resolver":
            time.sleep(0.08)
        return InvoiceResponse

    if blocked_seam == "request-serialization":

        class BlockingInvoiceArguments(BaseModel):
            model_config = ConfigDict(
                extra="forbid",
                frozen=True,
                revalidate_instances="always",
            )

            invoice_id: str

            def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                time.sleep(0.08)
                return super().model_dump(*args, **kwargs)

        arguments = BlockingInvoiceArguments(invoice_id="invoice-123")
        verified = verified.model_copy(
            update={"request_schema_sha256": _wire_schema_sha256(BlockingInvoiceArguments)}
        )

    driver = _driver(
        header_factory=header_factory,
        binding_verifier=verifier,
        request_model_resolver=request_resolver,
        response_model_resolver=response_resolver,
    )
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.05)

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.call(
            _connection(),
            _binding(),
            arguments,
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_timeout_pre_dispatch",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert harness.clients == []
    assert harness.sessions == []
    assert harness.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_class", "expected_code"),
    [
        (ProviderOperationClass.READ, "provider_timeout_pre_dispatch"),
        (ProviderOperationClass.CREATE, None),
    ],
)
async def test_requested_operation_class_selects_initial_verifier_budget(
    monkeypatch: pytest.MonkeyPatch,
    operation_class: ProviderOperationClass,
    expected_code: str | None,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    deadline = FakeOperationDeadline()
    _install_fake_operation_deadline(monkeypatch, deadline)

    def verifier(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        deadline.advance(40)
        return _verify_binding(
            connection,
            binding,
            resource_uri_sha256,
        )

    driver = _driver(binding_verifier=verifier)
    if expected_code is None:
        result = await driver.call(
            _connection(),
            _binding(operation_class),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )
        assert result.status_class is ProviderStatusClass.SUCCESS
        assert deadline.initial_seconds == [60]
        assert deadline.rescheduled_seconds == [60]
        assert harness.call_count == 1
        return

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.call(
            _connection(),
            _binding(operation_class),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert deadline.initial_seconds == [30]
    assert deadline.rescheduled_seconds == []
    assert harness.call_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked_seam", "operation_class", "expected_code", "expected_certainty"),
    [
        (
            "normalizer",
            ProviderOperationClass.READ,
            "provider_unavailable",
            DispatchCertainty.DISPATCHED,
        ),
        (
            "normalizer",
            ProviderOperationClass.CREATE,
            "provider_outcome_unknown",
            DispatchCertainty.UNKNOWN,
        ),
        (
            "teardown",
            ProviderOperationClass.READ,
            "provider_unavailable",
            DispatchCertainty.DISPATCHED,
        ),
        (
            "teardown",
            ProviderOperationClass.CREATE,
            "provider_outcome_unknown",
            DispatchCertainty.UNKNOWN,
        ),
    ],
)
async def test_blocking_synchronous_postdispatch_work_cannot_return_success(
    monkeypatch: pytest.MonkeyPatch,
    blocked_seam: str,
    operation_class: ProviderOperationClass,
    expected_code: str,
    expected_certainty: DispatchCertainty,
) -> None:
    harness = FakeMCPHarness()
    deadline = FakeOperationDeadline()
    _install_fake_operation_deadline(monkeypatch, deadline)
    if blocked_seam == "teardown":
        harness.session_exit_callback = deadline.expire
    harness.install(monkeypatch)

    def normalizer(
        binding: VerifiedRuntimeBinding,
        structured_content: Mapping[str, Any],
    ) -> BaseModel:
        if blocked_seam == "normalizer":
            deadline.expire()
        return _normalize_invoice(binding, structured_content)

    driver = _driver(response_normalizer=normalizer)

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.call(
            _connection(),
            _binding(operation_class),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    assert harness.call_count == 1
    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=expected_certainty,
    )


@pytest.mark.asyncio
async def test_discovery_has_one_cumulative_initialize_and_list_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.initialize_delay = 0.04
    harness.list_delay = 0.04
    harness.install(monkeypatch)
    driver = _driver()
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.05)
    started = time.monotonic()

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.discover(_connection())

    assert time.monotonic() - started < 0.15
    _assert_sanitized_error(
        error.value,
        code="provider_timeout_pre_dispatch",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation_class", "expected_code", "expected_certainty"),
    [
        (
            ProviderOperationClass.READ,
            "provider_unavailable",
            DispatchCertainty.DISPATCHED,
        ),
        (
            ProviderOperationClass.CREATE,
            "provider_outcome_unknown",
            DispatchCertainty.UNKNOWN,
        ),
    ],
)
async def test_call_has_one_cumulative_initialize_and_dispatch_deadline(
    monkeypatch: pytest.MonkeyPatch,
    operation_class: ProviderOperationClass,
    expected_code: str,
    expected_certainty: DispatchCertainty,
) -> None:
    harness = FakeMCPHarness()
    harness.initialize_delay = 0.04
    harness.call_delay = 0.04
    harness.install(monkeypatch)
    driver = _driver()
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.05)
    started = time.monotonic()

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.call(
            _connection(),
            _binding(operation_class),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    assert time.monotonic() - started < 0.15
    assert harness.call_count == 1
    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=expected_certainty,
    )


async def _actual_client_session_error(
    result: Mapping[str, Any] | None,
) -> Exception:
    server_send, client_receive = anyio.create_memory_object_stream(1)
    client_send, server_receive = anyio.create_memory_object_stream(1)

    async def respond() -> None:
        request = await server_receive.receive()
        request_id = request.message.root.id
        assert result is not None
        await server_send.send(
            SessionMessage(
                JSONRPCMessage(
                    JSONRPCResponse(
                        jsonrpc="2.0",
                        id=request_id,
                        result=dict(result),
                    )
                )
            )
        )

    captured: Exception | None = None
    async with server_send, server_receive:
        try:
            if result is None:
                async with ClientSession(
                    client_receive,
                    client_send,
                    read_timeout_seconds=timedelta(seconds=0.01),
                ) as session:
                    await session.initialize()
            else:
                async with anyio.create_task_group() as task_group:
                    task_group.start_soon(respond)
                    async with ClientSession(
                        client_receive,
                        client_send,
                        read_timeout_seconds=timedelta(seconds=0.1),
                    ) as session:
                        await session.initialize()
        except Exception as error:
            captured = error
    assert captured is not None
    return captured


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sdk_result", "expected_code"),
    [
        (None, "provider_timeout_pre_dispatch"),
        ({}, "provider_schema_changed"),
        (
            {
                "protocolVersion": "2099-01-01",
                "capabilities": {},
                "serverInfo": {"name": "PRIVATE_SERVER_SENTINEL", "version": "1"},
            },
            "provider_schema_changed",
        ),
    ],
    ids=["sdk-408", "parser-validation", "unsupported-protocol"],
)
async def test_actual_client_session_timeout_and_protocol_errors_are_closed(
    monkeypatch: pytest.MonkeyPatch,
    sdk_result: Mapping[str, Any] | None,
    expected_code: str,
) -> None:
    sdk_error = await _actual_client_session_error(sdk_result)
    harness = FakeMCPHarness()
    harness.initialize_error = sdk_error
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver().discover(_connection())

    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )


def _install_real_mock_transport(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    observed_requests: list[str],
) -> None:
    real_async_client = httpx.AsyncClient

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            observed_requests.append("DELETE")
            if scenario == "teardown":
                raise httpx.ConnectError(
                    "PRIVATE_TEARDOWN_SENTINEL",
                    request=request,
                )
            return httpx.Response(200, request=request)
        if request.method == "GET":
            observed_requests.append("GET")
            return httpx.Response(405, request=request)

        payload = json.loads(request.content)
        method = payload.get("method")
        observed_requests.append(f"POST:{method}")
        if method == "notifications/initialized":
            return httpx.Response(202, request=request)
        if method == "initialize":
            if scenario == "network-close":
                raise httpx.ConnectError(
                    "PRIVATE_NETWORK_CLOSE_SENTINEL",
                    request=request,
                )
            if scenario == "malformed-initialize":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b"PRIVATE_MALFORMED_INITIALIZE_SENTINEL",
                    request=request,
                )
            if scenario in {
                "wrong-content-type-initialize",
                "missing-content-type-initialize",
            }:
                headers = (
                    {"Content-Type": "text/plain"}
                    if scenario == "wrong-content-type-initialize"
                    else {}
                )
                return httpx.Response(
                    200,
                    headers=headers,
                    content=json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": payload["id"],
                            "result": {
                                "protocolVersion": "2025-11-25",
                                "capabilities": {},
                                "serverInfo": {
                                    "name": "PRIVATE_CONTENT_TYPE_SENTINEL",
                                    "version": "1",
                                },
                            },
                        }
                    ).encode(),
                    request=request,
                )
            if scenario == "mcp-408":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    json={
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "error": {
                            "code": 408,
                            "message": "PRIVATE_MCP_408_SENTINEL",
                        },
                    },
                    request=request,
                )
            protocol_version = "2099-01-01" if scenario == "unsupported-protocol" else "2025-11-25"
            return httpx.Response(
                200,
                headers={
                    "Content-Type": "application/json",
                    "Mcp-Session-Id": "PRIVATE_SESSION_SENTINEL",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "protocolVersion": protocol_version,
                        "capabilities": {},
                        "serverInfo": {
                            "name": "PRIVATE_SERVER_SENTINEL",
                            "version": "1",
                        },
                    },
                },
                request=request,
            )
        if method == "tools/list":
            if scenario == "malformed-list":
                return httpx.Response(
                    200,
                    headers={"Content-Type": "application/json"},
                    content=b"PRIVATE_MALFORMED_LIST_SENTINEL",
                    request=request,
                )
            result: Mapping[str, Any] = {} if scenario == "invalid-parsed-list" else {"tools": []}
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json"},
                json={
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": result,
                },
                request=request,
            )
        if method == "tools/call" and scenario in {
            "wrong-content-type-call",
            "missing-content-type-call",
        }:
            headers = (
                {"Content-Type": "text/plain"} if scenario == "wrong-content-type-call" else {}
            )
            return httpx.Response(
                200,
                headers=headers,
                content=json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "structuredContent": {
                                "invoice": {"id": "PRIVATE_CONTENT_TYPE_CALL_SENTINEL"}
                            },
                            "isError": False,
                        },
                    }
                ).encode(),
                request=request,
            )
        return httpx.Response(202, request=request)

    def build_client(
        *,
        headers: Mapping[str, str],
        timeout: httpx.Timeout,
        follow_redirects: bool,
        event_hooks: Mapping[str, list[Callable[[httpx.Response], object]]],
    ) -> httpx.AsyncClient:
        return real_async_client(
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
            event_hooks=event_hooks,
            transport=httpx.MockTransport(handler),
        )

    monkeypatch.setattr(streamable_module.httpx, "AsyncClient", build_client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "expected_code"),
    [
        ("malformed-initialize", "provider_schema_changed"),
        ("malformed-list", "provider_schema_changed"),
        ("invalid-parsed-list", "provider_schema_changed"),
        ("unsupported-protocol", "provider_schema_changed"),
        ("wrong-content-type-initialize", "provider_schema_changed"),
        ("missing-content-type-initialize", "provider_schema_changed"),
        ("mcp-408", "provider_timeout_pre_dispatch"),
        ("network-close", "provider_unavailable"),
    ],
)
async def test_real_driver_transport_failures_are_precisely_classified(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    expected_code: str,
) -> None:
    observed_requests: list[str] = []
    _install_real_mock_transport(monkeypatch, scenario, observed_requests)
    caplog.set_level(logging.DEBUG)
    driver = _driver()
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.08)

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.discover(_connection())

    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )
    assert "POST:initialize" in observed_requests
    if scenario in {"malformed-list", "invalid-parsed-list"}:
        assert "POST:tools/list" in observed_requests
    rendered = caplog.text
    for sentinel in (
        "PRIVATE_MALFORMED_INITIALIZE_SENTINEL",
        "PRIVATE_MALFORMED_LIST_SENTINEL",
        "PRIVATE_MCP_408_SENTINEL",
        "PRIVATE_NETWORK_CLOSE_SENTINEL",
        "PRIVATE_SESSION_SENTINEL",
        "PRIVATE_SERVER_SENTINEL",
        "PRIVATE_CONTENT_TYPE_SENTINEL",
    ):
        assert sentinel not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scenario", "operation_class", "expected_code", "expected_certainty"),
    [
        (
            "wrong-content-type-call",
            ProviderOperationClass.READ,
            "provider_schema_changed",
            DispatchCertainty.DISPATCHED,
        ),
        (
            "missing-content-type-call",
            ProviderOperationClass.READ,
            "provider_schema_changed",
            DispatchCertainty.DISPATCHED,
        ),
        (
            "wrong-content-type-call",
            ProviderOperationClass.CREATE,
            "provider_outcome_unknown",
            DispatchCertainty.UNKNOWN,
        ),
        (
            "missing-content-type-call",
            ProviderOperationClass.CREATE,
            "provider_outcome_unknown",
            DispatchCertainty.UNKNOWN,
        ),
    ],
)
async def test_real_content_type_failure_respects_dispatch_certainty(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: str,
    operation_class: ProviderOperationClass,
    expected_code: str,
    expected_certainty: DispatchCertainty,
) -> None:
    observed_requests: list[str] = []
    _install_real_mock_transport(monkeypatch, scenario, observed_requests)
    caplog.set_level(logging.DEBUG)
    driver = _driver()
    monkeypatch.setattr(driver, "_operation_seconds", lambda _timeout_class: 0.2)

    with pytest.raises(ProviderRuntimeError) as error:
        await driver.call(
            _connection(),
            _binding(operation_class),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code=expected_code,
        dispatch_certainty=expected_certainty,
    )
    assert observed_requests.count("POST:tools/call") == 1
    assert "PRIVATE_CONTENT_TYPE_CALL_SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_real_driver_teardown_error_is_suppressed_and_does_not_change_success(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    observed_requests: list[str] = []
    _install_real_mock_transport(monkeypatch, "teardown", observed_requests)
    caplog.set_level(logging.DEBUG)

    result = await _driver().discover(_connection())

    assert result.status_class is ProviderStatusClass.SUCCESS
    assert "DELETE" in observed_requests
    assert "PRIVATE_TEARDOWN_SENTINEL" not in caplog.text


@pytest.mark.asyncio
async def test_ordinary_initialize_runtime_error_is_unavailable_not_schema_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.initialize_error = RuntimeError("PRIVATE_NETWORK_CLOSE_SENTINEL")
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver().discover(_connection())

    _assert_sanitized_error(
        error.value,
        code="provider_unavailable",
        dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
    )


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

    harness.transport_error = RuntimeError("PRIVATE_RAW_TOOL PRIVATE_SESSION_SENTINEL X-Private")
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
@pytest.mark.parametrize(
    ("normalizer", "response_model_resolver", "expected_certainty"),
    [
        (
            lambda _binding, _content: {"invoice_id": "invoice-123"},
            _resolve_invoice_response_model,
            DispatchCertainty.DISPATCHED,
        ),
        (
            lambda _binding, _content: BoundaryResponse(
                invoice_id="invoice-123",
                metadata=BoundaryMetadata(alias="safe"),
            ),
            _resolve_invoice_response_model,
            DispatchCertainty.DISPATCHED,
        ),
        (
            lambda _binding, _content: InvoiceResponse.model_construct(invoice_id=123),
            _resolve_invoice_response_model,
            DispatchCertainty.DISPATCHED,
        ),
        (
            _normalize_invoice,
            None,
            DispatchCertainty.NOT_DISPATCHED,
        ),
    ],
    ids=[
        "arbitrary-dict",
        "wrong-exact-model",
        "model-bypassed-validation",
        "missing-response-schema-resolver",
    ],
)
async def test_response_normalizer_requires_revalidated_exact_catalog_response_model(
    monkeypatch: pytest.MonkeyPatch,
    normalizer: Callable[[VerifiedRuntimeBinding, Mapping[str, Any]], object],
    response_model_resolver: Callable[[VerifiedRuntimeBinding], object] | None,
    expected_certainty: DispatchCertainty,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            response_normalizer=normalizer,
            response_model_resolver=response_model_resolver,
        ).call(
            _connection(),
            _binding(),
            InvoiceArguments(invoice_id="invoice-123"),
            OPERATION_ID,
        )

    _assert_sanitized_error(
        error.value,
        code="provider_response_invalid",
        dispatch_certainty=expected_certainty,
    )


@pytest.mark.asyncio
async def test_response_schema_resolver_receives_verified_catalog_schema_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)
    observed: list[tuple[str, str, str]] = []

    def resolver(binding: VerifiedRuntimeBinding) -> type[BaseModel]:
        observed.append(
            (
                binding.normalized_capability,
                binding.capability_version,
                binding.response_schema_sha256,
            )
        )
        return InvoiceResponse

    result = await _driver(response_model_resolver=resolver).call(
        _connection(),
        _binding(),
        InvoiceArguments(invoice_id="invoice-123"),
        OPERATION_ID,
    )

    assert result.normalized_data == {"invoice_id": "invoice-123"}
    assert observed == [
        (
            "documents.invoice.get",
            "1.0.0",
            INVOICE_RESPONSE_SCHEMA_SHA256,
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "boundary_value",
    [
        "https://flowaccount-sandbox.example/mcp",
        "catalog_qualified_tool",
        "PRIVATE_SESSION_SENTINEL",
        "X-Mercury-Test-Auth",
        "PRIVATE_AUTH_HEADER_VALUE",
    ],
    ids=["endpoint", "provider-tool", "session-id", "header-name", "header-value"],
)
async def test_normalized_response_rejects_request_boundary_values_under_nested_aliases(
    monkeypatch: pytest.MonkeyPatch,
    boundary_value: str,
) -> None:
    harness = FakeMCPHarness()
    harness.install(monkeypatch)

    def unsafe_normalizer(
        _binding: VerifiedRuntimeBinding,
        _structured_content: Mapping[str, Any],
    ) -> BaseModel:
        return BoundaryResponse(
            invoice_id="invoice-123",
            metadata=BoundaryMetadata(alias=boundary_value),
        )

    def verify_boundary_response(
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        resource_uri_sha256: str,
    ) -> VerifiedRuntimeBinding:
        return _verify_binding(
            connection,
            binding,
            resource_uri_sha256,
        ).model_copy(update={"response_schema_sha256": _wire_schema_sha256(BoundaryResponse)})

    with pytest.raises(ProviderRuntimeError) as error:
        await _driver(
            binding_verifier=verify_boundary_response,
            response_normalizer=unsafe_normalizer,
            response_model_resolver=lambda _binding: BoundaryResponse,
        ).call(
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
        _binding: VerifiedRuntimeBinding,
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
