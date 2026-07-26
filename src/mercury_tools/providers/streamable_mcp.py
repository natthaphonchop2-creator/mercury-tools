"""Request-scoped Streamable HTTP MCP provider runtime."""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
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
from mercury_tools.providers.models import ProviderConnection

BindingVerifier = Callable[
    [ProviderConnection, QualifiedCapabilityBinding, str],
    VerifiedRuntimeBinding | Awaitable[VerifiedRuntimeBinding],
]
ResponseNormalizer = Callable[
    [VerifiedRuntimeBinding, Mapping[str, Any]],
    BaseModel,
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


class _SuppressRawMCPTransportLogs(logging.Filter):
    """Drop the SDK transport's raw request/session/error records process-wide."""

    def filter(self, _record: logging.LogRecord) -> bool:
        return False


_MCP_TRANSPORT_LOG_FILTER = _SuppressRawMCPTransportLogs()
_MCP_TRANSPORT_LOGGER = logging.getLogger("mcp.client.streamable_http")
if not any(
    isinstance(item, _SuppressRawMCPTransportLogs)
    for item in _MCP_TRANSPORT_LOGGER.filters
):
    _MCP_TRANSPORT_LOGGER.addFilter(_MCP_TRANSPORT_LOG_FILTER)


_PROVIDER_LOG_SCOPE = ContextVar("mercury_provider_log_scope", default=False)


class _SuppressProviderScopeLogs(logging.Filter):
    def filter(self, _record: logging.LogRecord) -> bool:
        return not _PROVIDER_LOG_SCOPE.get()


_PROVIDER_SCOPE_LOG_FILTER = _SuppressProviderScopeLogs()
for _logger_name in (
    "",
    "client",
    "httpx",
    "httpcore.connection",
    "httpcore.http11",
    "httpcore.http2",
    "httpcore.proxy",
    "httpcore.socks",
):
    _logger = logging.getLogger(_logger_name)
    if not any(
        isinstance(item, _SuppressProviderScopeLogs)
        for item in _logger.filters
    ):
        _logger.addFilter(_PROVIDER_SCOPE_LOG_FILTER)


@contextmanager
def _provider_log_suppression():
    token = _PROVIDER_LOG_SCOPE.set(True)
    try:
        yield
    finally:
        _PROVIDER_LOG_SCOPE.reset(token)


class _MCPProtocolFailure(Exception):
    pass


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
            return any(
                self.rejects(key) or self.rejects(item)
                for key, item in value.items()
            )
        if isinstance(value, list | tuple):
            return any(self.rejects(item) for item in value)
        if not isinstance(value, str):
            return False
        checked = value.casefold()
        return any(
            checked == boundary
            or (len(boundary) >= 8 and boundary in checked)
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
    headers: tuple[ProviderAuthHeader, ...] = Field(
        min_length=1,
        max_length=32,
        exclude=True,
        repr=False,
    )

    @model_validator(mode="after")
    def validate_closed_header_set(self) -> ProviderAuthHeaders:
        names = tuple(header.name.casefold() for header in self.headers)
        if (
            len(names) != len(set(names))
            or any(
                name.startswith("mcp-") or name in _BLOCKED_HEADER_NAMES
                for name in names
            )
        ):
            raise ValueError("provider_auth_headers_invalid")
        return self


HeaderFactory = Callable[
    [ProviderConnection],
    ProviderAuthHeaders | Awaitable[ProviderAuthHeaders],
]


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
        response_model_resolver: ResponseModelResolver | None = None,
    ) -> None:
        checked_manifest = ProviderDriverManifest.model_validate(
            manifest.model_dump(mode="json")
        )
        self.provider = checked_manifest.provider
        self._settings = settings
        self._manifest = checked_manifest
        self._header_factory = header_factory
        self._binding_verifier = binding_verifier
        self._response_normalizer = response_normalizer or _unconfigured_normalizer
        self._response_model_resolver = response_model_resolver

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery:
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        capabilities: list[str] | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression():
                async with asyncio.timeout(
                    self._operation_seconds(TimeoutClass.DISCOVERY)
                ):
                    connection = self._validate_connection_binding(connection)
                    resource = self._resolve_resource(connection)
                    boundary = _RequestBoundaryValues(resource.uri)
                    async with self._session(
                        connection,
                        resource,
                        TimeoutClass.DISCOVERY,
                        observed_auth_statuses,
                        boundary,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        self._capture_session_id(get_session_id, boundary)
                        self._validate_protocol(initialized)
                        discovered = await session.list_tools()
                        capabilities = self._normalize_discovery(discovered)
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(exc, observed_auth_statuses)
        if failure is not None:
            raise failure from None
        if resource is None or capabilities is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return ProviderDiscovery(
            provider=self.provider,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "capabilities": capabilities,
                "resource_uri_sha256": resource.uri_sha256,
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )

    async def validate_connection(
        self,
        connection: ProviderConnection,
    ) -> ProviderValidation:
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression():
                async with asyncio.timeout(
                    self._operation_seconds(TimeoutClass.READ)
                ):
                    connection = self._validate_connection_binding(connection)
                    resource = self._resolve_resource(connection)
                    boundary = _RequestBoundaryValues(resource.uri)
                    async with self._session(
                        connection,
                        resource,
                        TimeoutClass.READ,
                        observed_auth_statuses,
                        boundary,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        self._capture_session_id(get_session_id, boundary)
                        self._validate_protocol(initialized)
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(exc, observed_auth_statuses)
        if failure is not None:
            raise failure from None
        if resource is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        return ProviderValidation(
            provider=self.provider,
            status_class=ProviderStatusClass.SUCCESS,
            normalized_data={
                "protocol_version": self._manifest.protocol_version,
                "resource_uri_sha256": resource.uri_sha256,
            },
            dispatch_certainty=DispatchCertainty.NOT_APPLICABLE,
        )

    async def call(
        self,
        connection: ProviderConnection,
        binding: QualifiedCapabilityBinding,
        arguments: BaseModel,
        operation_id: UUID,
    ) -> ProviderCallResult:
        possible_dispatch = False
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        result: ProviderCallResult | None = None
        verified_binding: VerifiedRuntimeBinding | None = None
        boundary: _RequestBoundaryValues | None = None
        timeout_class = TimeoutClass.READ
        loop = asyncio.get_running_loop()
        started = loop.time()
        try:
            with _provider_log_suppression():
                async with asyncio.timeout_at(
                    started + self._operation_seconds(TimeoutClass.READ)
                ) as operation_timeout:
                    connection, binding = self._validate_call_binding(
                        connection,
                        binding,
                        arguments,
                        operation_id,
                    )
                    try:
                        serialized_arguments = arguments.model_dump(mode="json")
                    except Exception:
                        raise ProviderResponseInvalid(
                            self.provider,
                            dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
                        ) from None
                    resource = self._resolve_resource(connection)
                    boundary = _RequestBoundaryValues(resource.uri)
                    verified_binding = await self._verify_runtime_binding(
                        connection,
                        binding,
                        resource,
                    )
                    timeout_class = (
                        TimeoutClass.CREATE
                        if verified_binding.operation_class
                        is ProviderOperationClass.CREATE
                        else TimeoutClass.READ
                    )
                    boundary.add(verified_binding.provider_tool)
                    operation_timeout.reschedule(
                        started + self._operation_seconds(timeout_class)
                    )
                    async with self._session(
                        connection,
                        resource,
                        timeout_class,
                        observed_auth_statuses,
                        boundary,
                    ) as (session, get_session_id):
                        initialized = await self._initialize_session(session)
                        self._capture_session_id(get_session_id, boundary)
                        self._validate_protocol(initialized)
                        possible_dispatch = True
                        raw_result = await session.call_tool(
                            verified_binding.provider_tool,
                            serialized_arguments,
                            read_timeout_seconds=timedelta(
                                seconds=self._operation_seconds(timeout_class)
                            ),
                        )
                        normalized_data = await self._normalize_call(
                            verified_binding,
                            raw_result,
                            boundary,
                        )
                        try:
                            result = ProviderCallResult(
                                provider=self.provider,
                                status_class=ProviderStatusClass.SUCCESS,
                                normalized_data=normalized_data,
                                dispatch_certainty=DispatchCertainty.DISPATCHED,
                            )
                        except Exception:
                            raise ProviderResponseInvalid(
                                self.provider,
                                dispatch_certainty=DispatchCertainty.DISPATCHED,
                            ) from None
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
                )
            else:
                failure = self._predispatch_error(exc, observed_auth_statuses)
        if failure is not None:
            raise failure from None
        if result is None:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
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
    ):
        headers = await self._request_headers(connection, boundary)
        operation_seconds = self._operation_seconds(timeout_class)
        timeout = httpx.Timeout(
            operation_seconds,
            connect=self._manifest.timeout_classes[timeout_class].connect_seconds,
        )

        async def observe_auth_status(response: httpx.Response) -> None:
            if response.status_code in _AUTH_HTTP_STATUSES:
                observed_auth_statuses.add(response.status_code)

        try:
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
                yield session, get_session_id
        finally:
            for name in tuple(headers):
                headers[name] = ""
            headers.clear()

    async def _request_headers(
        self,
        connection: ProviderConnection,
        boundary: _RequestBoundaryValues,
    ) -> dict[str, str]:
        failure: ProviderRuntimeError | None = None
        headers: dict[str, str] | None = None
        try:
            if self._header_factory is None:
                raise TypeError
            value = self._header_factory(connection)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, ProviderAuthHeaders):
                raise TypeError
            value = ProviderAuthHeaders.model_validate(value)
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
        if (
            checked is not None
            and checked.authorization_method is not self._manifest.auth_adapter
        ):
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
            if not isinstance(value, VerifiedRuntimeBinding):
                raise TypeError
            verified = VerifiedRuntimeBinding.model_validate(value)
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

    async def _initialize_session(self, session: object) -> object:
        try:
            return await session.initialize()
        except Exception as error:
            if _is_sdk_schema_failure(error, include_runtime_error=True):
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
        if (
            getattr(initialized, "protocolVersion", None)
            != self._manifest.protocol_version
        ):
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
        result: object,
        boundary: _RequestBoundaryValues,
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
            if not isinstance(normalized, BaseModel):
                raise TypeError
            if self._response_model_resolver is None:
                raise TypeError
            response_model = self._response_model_resolver(binding)
            if inspect.isawaitable(response_model):
                response_model = await response_model
            if (
                not isinstance(response_model, type)
                or not issubclass(response_model, BaseModel)
                or type(normalized) is not response_model
                or response_model.model_config.get("extra") != "forbid"
                or response_model.model_config.get("frozen") is not True
                or response_model.model_config.get("revalidate_instances") != "always"
            ):
                raise TypeError
            revalidated = response_model.model_validate(normalized)
            if type(revalidated) is not response_model:
                raise TypeError
            serialized = revalidated.model_dump(mode="json")
            if boundary.rejects(serialized):
                raise ValueError
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
    ) -> ProviderRuntimeError:
        if observed_auth_statuses or _contains_http_status(
            error,
            _AUTH_HTTP_STATUSES,
        ):
            return ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        if _is_sdk_schema_failure(error):
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
    ) -> ProviderRuntimeError:
        if observed_auth_statuses or _contains_http_status(
            error,
            _AUTH_HTTP_STATUSES,
        ):
            return ProviderAuthRequired(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        if _is_sdk_schema_failure(error):
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
        if (
            isinstance(item, httpx.HTTPStatusError)
            and item.response.status_code in statuses
        ):
            return True
    return False


def _contains_mcp_error_code(
    error: BaseException,
    codes: set[int] | frozenset[int],
) -> bool:
    return any(
        isinstance(item, McpError) and item.error.code in codes
        for item in _exception_chain(error)
    )


def _is_sdk_schema_failure(
    error: BaseException,
    *,
    include_runtime_error: bool = False,
) -> bool:
    if _contains_exception(error, (_MCPProtocolFailure, ValidationError)):
        return True
    if _contains_mcp_error_code(error, _MCP_SCHEMA_ERROR_CODES):
        return True
    return include_runtime_error and _contains_exception(error, (RuntimeError,))


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
    "ResponseNormalizer",
    "ResponseModelResolver",
    "StreamableMCPDriver",
]
