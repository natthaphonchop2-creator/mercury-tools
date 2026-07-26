"""Request-scoped Streamable HTTP MCP provider runtime."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from mercury_tools.catalog.identity import canonical_json
from mercury_tools.config import Settings
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderAuthRequired,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderOutcomeUnknown,
    ProviderQualificationState,
    ProviderResponseInvalid,
    ProviderRuntimeError,
    ProviderSchemaChanged,
    ProviderStatusClass,
    ProviderTimeoutPreDispatch,
    ProviderUnavailable,
    ProviderValidation,
    QualifiedCapabilityBinding,
    VerifiedRuntimeBinding,
)
from mercury_tools.providers.manifest import (
    ProviderDriverManifest,
    ProviderManifestError,
    ResolvedProviderResource,
    TimeoutClass,
    resolve_provider_resource,
)
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ProviderConnection,
    ProviderId,
)

BindingVerifier = Callable[
    [ProviderConnection, QualifiedCapabilityBinding, str],
    VerifiedRuntimeBinding | Awaitable[VerifiedRuntimeBinding],
]
ResponseNormalizer = Callable[
    [VerifiedRuntimeBinding, Mapping[str, Any]],
    BaseModel | Awaitable[BaseModel],
]
RequestModelResolver = Callable[
    [VerifiedRuntimeBinding],
    type[BaseModel] | Awaitable[type[BaseModel]],
]
ResponseModelResolver = Callable[
    [VerifiedRuntimeBinding],
    type[BaseModel] | Awaitable[type[BaseModel]],
]
_BLOCKED_HEADER_NAMES = frozenset(
    {
        "connection",
        "content-length",
        "host",
        "http2-settings",
        "keep-alive",
        "last-event-id",
        "mcp-session-id",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_AUTH_HTTP_STATUSES = frozenset({401, 403})
_MCP_SCHEMA_ERROR_CODES = frozenset({PARSE_ERROR, INVALID_REQUEST, INVALID_PARAMS})
_HTTP_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_PROVIDER_LOG_REDACTION = "provider_runtime_log_redacted"
_PINNED_CLIENT_INITIALIZE_CODE = ClientSession.initialize.__code__


class _SuppressRawMCPTransportLogs(logging.Filter):
    """Drop the SDK transport's raw request/session/error records process-wide."""

    def filter(self, _record: logging.LogRecord) -> bool:
        return False


_MCP_TRANSPORT_LOG_FILTER = _SuppressRawMCPTransportLogs()
_MCP_TRANSPORT_LOGGER = logging.getLogger("mcp.client.streamable_http")
if not any(
    isinstance(item, _SuppressRawMCPTransportLogs) for item in _MCP_TRANSPORT_LOGGER.filters
):
    _MCP_TRANSPORT_LOGGER.addFilter(_MCP_TRANSPORT_LOG_FILTER)


@dataclass(slots=True)
class _ProviderLogState:
    parser_failure: bool = False


_PROVIDER_LOG_SCOPE: ContextVar[_ProviderLogState | None] = ContextVar(
    "mercury_provider_log_scope",
    default=None,
)


def _install_provider_log_record_boundary() -> None:
    previous_factory = logging.getLogRecordFactory()
    if getattr(previous_factory, "_mercury_provider_boundary", False):
        return

    def provider_log_record_factory(
        *args: Any,
        **kwargs: Any,
    ) -> logging.LogRecord:
        record = previous_factory(*args, **kwargs)
        state = _PROVIDER_LOG_SCOPE.get()
        if state is None:
            return record
        if (
            record.name == "mcp.client.streamable_http"
            and record.exc_info is not None
            and isinstance(record.exc_info[1], BaseException)
            and _contains_exception(
                record.exc_info[1],
                (ValidationError, json.JSONDecodeError),
            )
        ):
            state.parser_failure = True
        record.msg = _PROVIDER_LOG_REDACTION
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return record

    provider_log_record_factory._mercury_provider_boundary = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(provider_log_record_factory)


_install_provider_log_record_boundary()


@contextmanager
def _provider_log_suppression():
    state = _ProviderLogState()
    token = _PROVIDER_LOG_SCOPE.set(state)
    try:
        yield state
    finally:
        _PROVIDER_LOG_SCOPE.reset(token)


class _MCPProtocolFailure(Exception):
    pass


class _OperationDeadlineExpired(TimeoutError):
    pass


@dataclass(slots=True)
class _OperationDeadline:
    started_at: float
    expires_at: float

    @classmethod
    def start(cls, seconds: float) -> _OperationDeadline:
        started_at = asyncio.get_running_loop().time()
        return cls(started_at=started_at, expires_at=started_at + seconds)

    def reschedule(self, seconds: float) -> None:
        self.expires_at = self.started_at + seconds
        self.check()

    def check(self) -> None:
        self.remaining()

    def remaining(self) -> float:
        remaining = self.expires_at - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _OperationDeadlineExpired
        return remaining


class _RequestBoundaryValues:
    __slots__ = ("_values",)

    def __init__(self, *values: str) -> None:
        self._values: set[str] = set()
        for value in values:
            self.add(value)

    def add(self, value: str) -> None:
        if value:
            self._values.add(value.casefold())

    def rejects(self, value: object) -> bool:
        if isinstance(value, Mapping):
            return any(self.rejects(key) or self.rejects(item) for key, item in value.items())
        if isinstance(value, list | tuple):
            return any(self.rejects(item) for item in value)
        if not isinstance(value, str):
            return False
        checked = value.casefold()
        return any(
            checked == boundary or (len(boundary) >= 8 and boundary in checked)
            for boundary in self._values
        )


class _AuthModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class ProviderAuthHeader(_AuthModel):
    name: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=8192, exclude=True, repr=False)

    @field_validator("name", "value")
    @classmethod
    def reject_line_controls(cls, value: str) -> str:
        if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
            raise ValueError("provider_auth_headers_invalid")
        return value

    @field_validator("name")
    @classmethod
    def validate_header_name(cls, value: str) -> str:
        if _HTTP_HEADER_NAME.fullmatch(value) is None:
            raise ValueError("provider_auth_headers_invalid")
        return value


class ProviderAuthHeaders(_AuthModel):
    provider: ProviderId
    authorization_method: AuthorizationMethod
    headers: tuple[ProviderAuthHeader, ...] = Field(
        min_length=1,
        max_length=32,
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_closed_header_set(self) -> ProviderAuthHeaders:
        names = tuple(header.name.casefold() for header in self.headers)
        if len(names) != len(set(names)) or any(
            name.startswith("mcp-") or name in _BLOCKED_HEADER_NAMES for name in names
        ):
            raise ValueError("provider_auth_headers_invalid")
        return self


HeaderFactory = Callable[
    [ProviderConnection],
    ProviderAuthHeaders | Awaitable[ProviderAuthHeaders],
]


def validation_schema_sha256(model: type[BaseModel]) -> str:
    """Return the canonical Task 5 identity for a closed validation model."""

    if (
        not isinstance(model, type)
        or not issubclass(model, BaseModel)
        or model.model_config.get("extra") != "forbid"
        or model.model_config.get("frozen") is not True
        or model.model_config.get("revalidate_instances") != "always"
    ):
        raise TypeError("provider_schema_model_invalid")
    schema = model.model_json_schema(mode="validation")
    return hashlib.sha256(canonical_json(schema).encode("utf-8")).hexdigest()


def _unconfigured_normalizer(
    _binding: VerifiedRuntimeBinding,
    _structured_content: Mapping[str, Any],
) -> BaseModel:
    raise ValueError("provider_response_invalid")


class StreamableMCPDriver:
    """One hosted provider driver with no reusable client or session state."""

    def __init__(
        self,
        *,
        settings: Settings,
        manifest: ProviderDriverManifest,
        header_factory: HeaderFactory | None = None,
        binding_verifier: BindingVerifier | None = None,
        response_normalizer: ResponseNormalizer | None = None,
        request_model_resolver: RequestModelResolver | None = None,
        response_model_resolver: ResponseModelResolver | None = None,
    ) -> None:
        checked_manifest = ProviderDriverManifest.model_validate(manifest.model_dump(mode="json"))
        self.provider = checked_manifest.provider
        self._settings = settings
        self._manifest = checked_manifest
        self._header_factory = header_factory
        self._binding_verifier = binding_verifier
        self._response_normalizer = response_normalizer or _unconfigured_normalizer
        self._request_model_resolver = request_model_resolver
        self._response_model_resolver = response_model_resolver

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery:
        deadline = _OperationDeadline.start(self._operation_seconds(TimeoutClass.DISCOVERY))
        log_state: _ProviderLogState | None = None
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        capabilities: list[str] | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression() as log_state:
                async with asyncio.timeout_at(deadline.expires_at):
                    connection = self._validate_connection_binding(connection)
                    deadline.check()
                    resource = self._resolve_resource(connection)
                    deadline.check()
                    boundary = _RequestBoundaryValues(resource.uri)
                    async with self._session(
                        connection,
                        resource,
                        TimeoutClass.DISCOVERY,
                        observed_auth_statuses,
                        boundary,
                        deadline,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        deadline.check()
                        self._capture_session_id(get_session_id, boundary)
                        deadline.check()
                        self._validate_protocol(initialized)
                        deadline.remaining()
                        discovered = await session.list_tools()
                        deadline.check()
                        capabilities = self._normalize_discovery(discovered)
                        deadline.check()
                    deadline.check()
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(
                exc,
                observed_auth_statuses,
                log_state,
            )
        if failure is not None:
            raise failure from None
        if resource is None or capabilities is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        try:
            result = ProviderDiscovery(
                provider=self.provider,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={
                    "capabilities": capabilities,
                    "resource_uri_sha256": resource.uri_sha256,
                },
                dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
            )
            deadline.check()
        except _OperationDeadlineExpired as exc:
            raise self._predispatch_error(
                exc,
                observed_auth_statuses,
                log_state,
            ) from None
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        return result

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation:
        deadline = _OperationDeadline.start(self._operation_seconds(TimeoutClass.READ))
        log_state: _ProviderLogState | None = None
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression() as log_state:
                async with asyncio.timeout_at(deadline.expires_at):
                    connection = self._validate_connection_binding(connection)
                    deadline.check()
                    resource = self._resolve_resource(connection)
                    deadline.check()
                    boundary = _RequestBoundaryValues(resource.uri)
                    async with self._session(
                        connection,
                        resource,
                        TimeoutClass.READ,
                        observed_auth_statuses,
                        boundary,
                        deadline,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        deadline.check()
                        self._capture_session_id(get_session_id, boundary)
                        deadline.check()
                        self._validate_protocol(initialized)
                        deadline.check()
                    deadline.check()
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(
                exc,
                observed_auth_statuses,
                log_state,
            )
        if failure is not None:
            raise failure from None
        if resource is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        try:
            result = ProviderValidation(
                provider=self.provider,
                status_class=ProviderStatusClass.SUCCESS,
                normalized_data={
                    "protocol_version": self._manifest.protocol_version,
                    "resource_uri_sha256": resource.uri_sha256,
                },
                dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
            )
            deadline.check()
        except _OperationDeadlineExpired as exc:
            raise self._predispatch_error(
                exc,
                observed_auth_statuses,
                log_state,
            ) from None
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        return result

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult:
        deadline = _OperationDeadline.start(self._operation_seconds(TimeoutClass.READ))
        log_state: _ProviderLogState | None = None
        possible_dispatch = False
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        result: ProviderCallResult | None = None
        verified_binding: VerifiedRuntimeBinding | None = None
        boundary: _RequestBoundaryValues | None = None
        timeout_class = TimeoutClass.READ
        try:
            with _provider_log_suppression() as log_state:
                async with asyncio.timeout_at(deadline.expires_at) as operation_timeout:
                    connection, binding = self._validate_call_binding(
                        connection,
                        binding,
                        arguments,
                        operation_id,
                    )
                    deadline.check()
                    resource = self._resolve_resource(connection)
                    deadline.check()
                    boundary = _RequestBoundaryValues(resource.uri)
                    verified_binding = await self._verify_runtime_binding(
                        connection,
                        binding,
                        resource,
                        deadline,
                    )
                    deadline.check()
                    timeout_class = (
                        TimeoutClass.CREATE
                        if verified_binding.operation_class is ProviderOperationClass.CREATE
                        else TimeoutClass.READ
                    )
                    boundary.add(verified_binding.provider_tool)
                    deadline.reschedule(self._operation_seconds(timeout_class))
                    operation_timeout.reschedule(deadline.expires_at)
                    request_model, response_model = await self._resolve_schema_models(
                        verified_binding,
                        deadline,
                    )
                    serialized_arguments = self._serialize_arguments(
                        arguments,
                        request_model,
                        deadline,
                    )
                    deadline.check()
                    async with self._session(
                        connection,
                        resource,
                        timeout_class,
                        observed_auth_statuses,
                        boundary,
                        deadline,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        deadline.check()
                        self._capture_session_id(get_session_id, boundary)
                        deadline.check()
                        self._validate_protocol(initialized)
                        deadline.remaining()
                        possible_dispatch = True
                        raw_result = await session.call_tool(
                            verified_binding.provider_tool,
                            serialized_arguments,
                            read_timeout_seconds=timedelta(
                                seconds=self._operation_seconds(timeout_class)
                            ),
                        )
                        deadline.check()
                        normalized_data = await self._normalize_call(
                            verified_binding,
                            response_model,
                            raw_result,
                            boundary,
                            deadline,
                        )
                        deadline.check()
                        try:
                            result = ProviderCallResult(
                                provider=self.provider,
                                status_class=ProviderStatusClass.SUCCESS,
                                normalized_data=normalized_data,
                                dispatch_certainty=DispatchCertainty.DISPATCHED,
                            )
                            deadline.check()
                        except _OperationDeadlineExpired:
                            raise
                        except Exception:
                            raise ProviderResponseInvalid(
                                self.provider,
                                dispatch_certainty=DispatchCertainty.DISPATCHED,
                            ) from None
                    deadline.check()
        except ProviderRuntimeError as exc:
            if (
                verified_binding is not None
                and verified_binding.operation_class is ProviderOperationClass.CREATE
                and possible_dispatch
            ):
                failure = ProviderOutcomeUnknown(
                    self.provider,
                    dispatch_certainty=DispatchCertainty.UNKNOWN,
                )
            else:
                failure = _closed_runtime_error(exc)
        except Exception as exc:
            if (
                verified_binding is not None
                and verified_binding.operation_class is ProviderOperationClass.CREATE
                and possible_dispatch
            ):
                failure = ProviderOutcomeUnknown(
                    self.provider,
                    dispatch_certainty=DispatchCertainty.UNKNOWN,
                )
            elif possible_dispatch:
                failure = self._dispatched_read_error(
                    exc,
                    observed_auth_statuses,
                    log_state,
                )
            else:
                failure = self._predispatch_error(
                    exc,
                    observed_auth_statuses,
                    log_state,
                )
        if failure is not None:
            raise failure from None
        if result is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        try:
            deadline.check()
        except _OperationDeadlineExpired as exc:
            if (
                verified_binding is not None
                and verified_binding.operation_class is ProviderOperationClass.CREATE
                and possible_dispatch
            ):
                raise ProviderOutcomeUnknown(
                    self.provider,
                    dispatch_certainty=DispatchCertainty.UNKNOWN,
                ) from None
            if possible_dispatch:
                raise self._dispatched_read_error(
                    exc,
                    observed_auth_statuses,
                    log_state,
                ) from None
            raise self._predispatch_error(
                exc,
                observed_auth_statuses,
                log_state,
            ) from None
        return result

    def _resolve_resource(
        self,
        connection: ProviderConnection,
    ) -> ResolvedProviderResource:
        failure: ProviderRuntimeError | None = None
        try:
            resource = resolve_provider_resource(
                settings=self._settings,
                manifest=self._manifest,
                environment=connection.environment,
            )
        except ProviderManifestError:
            failure = ProviderUnavailable(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if failure is not None:
            raise failure
        return resource

    @asynccontextmanager
    async def _session(
        self,
        connection: ProviderConnection,
        resource: ResolvedProviderResource,
        timeout_class: TimeoutClass,
        observed_auth_statuses: set[int],
        boundary: _RequestBoundaryValues,
        deadline: _OperationDeadline,
    ):
        headers = await self._request_headers(connection, boundary, deadline)
        deadline.check()
        operation_seconds = self._operation_seconds(timeout_class)
        timeout = httpx.Timeout(
            operation_seconds,
            connect=self._manifest.timeout_classes[timeout_class].connect_seconds,
        )

        async def observe_auth_status(response: httpx.Response) -> None:
            if response.status_code in _AUTH_HTTP_STATUSES:
                observed_auth_statuses.add(response.status_code)

        try:
            deadline.remaining()
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                    event_hooks={"response": [observe_auth_status]},
                ) as http_client,
                streamable_http_client(
                    resource.uri,
                    http_client=http_client,
                    terminate_on_close=True,
                ) as (read_stream, write_stream, get_session_id),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=operation_seconds),
                ) as session,
            ):
                deadline.check()
                yield session, get_session_id
        finally:
            for name in tuple(headers):
                headers[name] = ""
            headers.clear()
            deadline.check()

    async def _request_headers(
        self,
        connection: ProviderConnection,
        boundary: _RequestBoundaryValues,
        deadline: _OperationDeadline,
    ) -> dict[str, str]:
        failure: ProviderRuntimeError | None = None
        headers: dict[str, str] | None = None
        try:
            if self._header_factory is None:
                raise TypeError
            value = self._header_factory(connection)
            if inspect.isawaitable(value):
                value = await value
            deadline.check()
            if not isinstance(value, ProviderAuthHeaders):
                raise TypeError
            value = ProviderAuthHeaders.model_validate(value)
            deadline.check()
            if (
                value.provider is not self.provider
                or value.provider is not connection.provider
                or value.authorization_method is not self._manifest.auth_adapter
                or value.authorization_method is not connection.authorization_method
            ):
                raise TypeError
            headers = {}
            for header in value.headers:
                name = header.name
                header_value = header.value
                if (
                    not name
                    or name.casefold() in _BLOCKED_HEADER_NAMES
                    or "\r" in name
                    or "\n" in name
                    or "\r" in header_value
                    or "\n" in header_value
                ):
                    raise ValueError
                headers[name] = header_value
                boundary.add(name)
                boundary.add(header_value)
            deadline.check()
        except _OperationDeadlineExpired:
            raise
        except Exception:
            failure = ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if failure is not None:
            raise failure
        if headers is None:
            raise ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return headers

    def _validate_connection_binding(
        self,
        connection: ProviderConnection,
    ) -> ProviderConnection:
        failure: ProviderRuntimeError | None = None
        checked: ProviderConnection | None = None
        try:
            if not isinstance(connection, ProviderConnection):
                raise TypeError
            checked = ProviderConnection.model_validate(connection)
        except Exception:
            failure = ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if failure is not None:
            raise failure
        if checked is not None and checked.authorization_method is not self._manifest.auth_adapter:
            raise ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if (
            checked is None
            or checked.provider is not self.provider
            or checked.environment not in self._manifest.environments
        ):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return checked

    def _validate_call_binding(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> tuple[ProviderConnection, QualifiedCapabilityBinding]:
        checked_connection = self._validate_connection_binding(connection)
        failure: ProviderRuntimeError | None = None
        checked_binding: QualifiedCapabilityBinding | None = None
        try:
            if not isinstance(binding, QualifiedCapabilityBinding):
                raise TypeError
            checked_binding = QualifiedCapabilityBinding.model_validate(binding)
        except Exception:
            failure = ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if failure is not None:
            raise failure
        if (
            checked_binding is None
            or checked_binding.provider is not self.provider
            or checked_binding.environment != checked_connection.environment
            or not isinstance(arguments, BaseModel)
            or not isinstance(operation_id, UUID)
            or operation_id.int == 0
        ):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return checked_connection, checked_binding

    async def _verify_runtime_binding(
        self,
        connection: ProviderConnection,
        untrusted: QualifiedCapabilityBinding,
        resource: ResolvedProviderResource,
        deadline: _OperationDeadline,
    ) -> VerifiedRuntimeBinding:
        failure: ProviderRuntimeError | None = None
        verified: VerifiedRuntimeBinding | None = None
        try:
            if self._binding_verifier is None:
                raise TypeError
            value = self._binding_verifier(
                connection,
                untrusted,
                resource.uri_sha256,
            )
            if inspect.isawaitable(value):
                value = await value
            deadline.check()
            if not isinstance(value, VerifiedRuntimeBinding):
                raise TypeError
            verified = VerifiedRuntimeBinding.model_validate(value)
            deadline.check()
        except _OperationDeadlineExpired:
            raise
        except Exception:
            failure = ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if failure is not None:
            raise failure from None
        if (
            verified is None
            or verified.qualification_state is not ProviderQualificationState.ENABLED
            or verified.provider is not self.provider
            or verified.provider is not connection.provider
            or verified.environment != connection.environment
            or verified.resource_uri_sha256 != resource.uri_sha256
            or verified.normalized_capability != untrusted.normalized_capability
            or verified.provider_tool != untrusted.provider_tool
            or verified.operation_class is not untrusted.operation_class
            or verified.qualification_hash != untrusted.qualification_hash
        ):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return verified

    async def _resolve_schema_models(
        self,
        binding: VerifiedRuntimeBinding,
        deadline: _OperationDeadline,
    ) -> tuple[type[BaseModel], type[BaseModel]]:
        try:
            if self._request_model_resolver is None or self._response_model_resolver is None:
                raise TypeError
            request_model = self._request_model_resolver(binding)
            if inspect.isawaitable(request_model):
                request_model = await request_model
            deadline.check()
            response_model = self._response_model_resolver(binding)
            if inspect.isawaitable(response_model):
                response_model = await response_model
            deadline.check()
            request_digest = validation_schema_sha256(request_model)
            deadline.check()
            response_digest = validation_schema_sha256(response_model)
            deadline.check()
            if (
                request_digest != binding.request_schema_sha256
                or response_digest != binding.response_schema_sha256
            ):
                raise TypeError
        except _OperationDeadlineExpired:
            raise
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        return request_model, response_model

    def _serialize_arguments(
        self,
        arguments: BaseModel,
        request_model: type[BaseModel],
        deadline: _OperationDeadline,
    ) -> dict[str, Any]:
        try:
            if type(arguments) is not request_model:
                raise TypeError
            revalidated = request_model.model_validate(arguments)
            deadline.check()
            if type(revalidated) is not request_model:
                raise TypeError
            serialized = revalidated.model_dump(mode="json")
            deadline.check()
            if not isinstance(serialized, dict):
                raise TypeError
        except _OperationDeadlineExpired:
            raise
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        return serialized

    async def _initialize_session(self, session: object) -> object:
        try:
            return await session.initialize()
        except Exception as error:
            if _is_sdk_schema_failure(error) or _is_unsupported_protocol_error(error):
                raise _MCPProtocolFailure from None
            raise

    def _capture_session_id(
        self,
        get_session_id: Callable[[], str | None],
        boundary: _RequestBoundaryValues,
    ) -> None:
        try:
            session_id = get_session_id()
            if session_id is not None and not isinstance(session_id, str):
                raise TypeError
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        if session_id is not None:
            boundary.add(session_id)

    def _validate_protocol(self, initialized: object) -> None:
        if getattr(initialized, "protocolVersion", None) != self._manifest.protocol_version:
            raise ProviderSchemaChanged(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )

    def _normalize_discovery(self, result: object) -> list[str]:
        tools = getattr(result, "tools", None)
        if not isinstance(tools, list | tuple):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        mapping = {
            item.provider_tool: item.normalized_capability
            for item in self._manifest.discovery_mappings
        }
        capabilities: set[str] = set()
        for tool in tools:
            name = getattr(tool, "name", None)
            if not isinstance(name, str):
                raise ProviderResponseInvalid(
                    self.provider,
                    dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
                )
            capability = mapping.get(name)
            if capability is not None:
                capabilities.add(capability)
        return sorted(capabilities)

    async def _normalize_call(
        self,
        binding: VerifiedRuntimeBinding,
        response_model: type[BaseModel],
        result: object,
        boundary: _RequestBoundaryValues,
        deadline: _OperationDeadline,
    ) -> dict[str, Any]:
        if getattr(result, "isError", None) is not False:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        structured_content = getattr(result, "structuredContent", None)
        if not isinstance(structured_content, Mapping):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        try:
            normalized = self._response_normalizer(binding, structured_content)
            if inspect.isawaitable(normalized):
                normalized = await normalized
            deadline.check()
            if not isinstance(normalized, BaseModel):
                raise TypeError
            response_digest = validation_schema_sha256(response_model)
            deadline.check()
            if (
                type(normalized) is not response_model
                or response_digest != binding.response_schema_sha256
            ):
                raise TypeError
            revalidated = response_model.model_validate(normalized)
            deadline.check()
            if type(revalidated) is not response_model:
                raise TypeError
            serialized = revalidated.model_dump(mode="json")
            deadline.check()
            if boundary.rejects(serialized):
                raise ValueError
            deadline.check()
        except _OperationDeadlineExpired:
            raise
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            ) from None
        if not isinstance(serialized, dict):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        return serialized

    def _operation_seconds(self, timeout_class: TimeoutClass) -> int:
        return self._manifest.timeout_classes[timeout_class].operation_seconds

    def _predispatch_error(
        self,
        error: Exception,
        observed_auth_statuses: set[int],
        log_state: _ProviderLogState | None,
    ) -> ProviderRuntimeError:
        if observed_auth_statuses or _contains_http_status(
            error,
            _AUTH_HTTP_STATUSES,
        ):
            return ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if (
            log_state is not None
            and log_state.parser_failure
            or _is_sdk_schema_failure(error)
            or _is_unsupported_protocol_error(error)
        ):
            return ProviderSchemaChanged(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if _contains_mcp_error_code(error, {408}) or _contains_exception(
            error,
            (TimeoutError, httpx.TimeoutException),
        ):
            return ProviderTimeoutPreDispatch(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return ProviderUnavailable(
            self.provider,
            dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
        )

    def _dispatched_read_error(
        self,
        error: Exception,
        observed_auth_statuses: set[int],
        log_state: _ProviderLogState | None,
    ) -> ProviderRuntimeError:
        if observed_auth_statuses or _contains_http_status(
            error,
            _AUTH_HTTP_STATUSES,
        ):
            return ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        if (
            log_state is not None
            and log_state.parser_failure
            or _is_sdk_schema_failure(error)
            or _is_unsupported_protocol_error(error)
        ):
            return ProviderSchemaChanged(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        return ProviderUnavailable(
            self.provider,
            dispatch_certainty=DispatchCertainty.DISPATCHED,
        )


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    found: list[BaseException] = []
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        found.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
    return tuple(found)


def _contains_exception(
    error: BaseException,
    classes: tuple[type[BaseException], ...],
) -> bool:
    return any(isinstance(item, classes) for item in _exception_chain(error))


def _contains_http_status(
    error: BaseException,
    statuses: set[int] | frozenset[int],
) -> bool:
    for item in _exception_chain(error):
        if isinstance(item, httpx.HTTPStatusError) and item.response.status_code in statuses:
            return True
    return False


def _contains_mcp_error_code(
    error: BaseException,
    codes: set[int] | frozenset[int],
) -> bool:
    return any(
        isinstance(item, McpError) and item.error.code in codes for item in _exception_chain(error)
    )


def _is_sdk_schema_failure(
    error: BaseException,
) -> bool:
    if _contains_exception(error, (_MCPProtocolFailure, ValidationError)):
        return True
    return _contains_mcp_error_code(error, _MCP_SCHEMA_ERROR_CODES)


def _is_unsupported_protocol_error(error: BaseException) -> bool:
    for item in _exception_chain(error):
        if type(item) is not RuntimeError:
            continue
        traceback = item.__traceback__
        if traceback is None:
            continue
        while traceback.tb_next is not None:
            traceback = traceback.tb_next
        if traceback.tb_frame.f_code is _PINNED_CLIENT_INITIALIZE_CODE:
            return True
    return False


def _closed_runtime_error(error: ProviderRuntimeError) -> ProviderRuntimeError:
    return type(error)(
        error.provider,
        dispatch_certainty=error.dispatch_certainty,
    )


__all__ = [
    "BindingVerifier",
    "HeaderFactory",
    "ProviderAuthHeader",
    "ProviderAuthHeaders",
    "RequestModelResolver",
    "ResponseNormalizer",
    "ResponseModelResolver",
    "StreamableMCPDriver",
    "validation_schema_sha256",
]
