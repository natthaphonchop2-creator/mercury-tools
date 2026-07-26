"""Request-scoped Streamable HTTP MCP provider runtime."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from mercury_tools.config import Settings
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderAuthRequired,
    ProviderCallResult,
    ProviderDiscovery,
    ProviderOperationClass,
    ProviderOutcomeUnknown,
    ProviderResponseInvalid,
    ProviderRuntimeError,
    ProviderSchemaChanged,
    ProviderStatusClass,
    ProviderTimeoutPreDispatch,
    ProviderUnavailable,
    ProviderValidation,
    QualifiedCapabilityBinding,
)
from mercury_tools.providers.manifest import (
    ProviderDriverManifest,
    ProviderManifestError,
    ResolvedProviderResource,
    TimeoutClass,
    resolve_provider_resource,
)
from mercury_tools.providers.models import ProviderConnection

HeaderFactory = Callable[
    [ProviderConnection],
    Mapping[str, str] | Awaitable[Mapping[str, str]],
]
ResponseNormalizer = Callable[
    [QualifiedCapabilityBinding, Mapping[str, Any]],
    object,
]
_BLOCKED_HEADER_NAMES = frozenset(
    {
        "content-length",
        "host",
        "mcp-session-id",
        "transfer-encoding",
    }
)
_AUTH_HTTP_STATUSES = frozenset({401, 403})


async def _empty_headers(_connection: ProviderConnection) -> Mapping[str, str]:
    return {}


def _unconfigured_normalizer(
    _binding: QualifiedCapabilityBinding,
    _structured_content: Mapping[str, Any],
) -> object:
    raise ValueError("provider_response_invalid")


class StreamableMCPDriver:
    """One hosted provider driver with no reusable client or session state."""

    def __init__(
        self,
        *,
        settings: Settings,
        manifest: ProviderDriverManifest,
        header_factory: HeaderFactory | None = None,
        response_normalizer: ResponseNormalizer | None = None,
    ) -> None:
        self.provider = manifest.provider
        self._settings = settings
        self._manifest = manifest
        self._header_factory = header_factory or _empty_headers
        self._response_normalizer = response_normalizer or _unconfigured_normalizer

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery:
        connection = self._validate_connection_binding(connection)
        resource = self._resolve_resource(connection)
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        try:
            async with self._session(
                connection,
                resource,
                TimeoutClass.DISCOVERY,
                observed_auth_statuses,
            ) as session:
                initialized = await session.initialize()
                self._validate_protocol(initialized)
                discovered = await session.list_tools()
                capabilities = self._normalize_discovery(discovered)
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(exc, observed_auth_statuses)
        if failure is not None:
            raise failure
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
        connection = self._validate_connection_binding(connection)
        resource = self._resolve_resource(connection)
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        try:
            async with self._session(
                connection,
                resource,
                TimeoutClass.READ,
                observed_auth_statuses,
            ) as session:
                initialized = await session.initialize()
                self._validate_protocol(initialized)
        except ProviderRuntimeError as error:
            failure = _closed_runtime_error(error)
        except Exception as exc:
            failure = self._predispatch_error(exc, observed_auth_statuses)
        if failure is not None:
            raise failure
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
        connection, binding = self._validate_call_binding(
            connection,
            binding,
            arguments,
            operation_id,
        )
        try:
            serialized_arguments = arguments.model_dump(mode="json")
        except Exception:
            failure = ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            )
        else:
            failure = None
        if failure is not None:
            raise failure
        resource = self._resolve_resource(connection)
        timeout_class = (
            TimeoutClass.CREATE
            if binding.operation_class is ProviderOperationClass.CREATE
            else TimeoutClass.READ
        )
        possible_dispatch = False
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        result: ProviderCallResult | None = None
        try:
            async with self._session(
                connection,
                resource,
                timeout_class,
                observed_auth_statuses,
            ) as session:
                initialized = await session.initialize()
                self._validate_protocol(initialized)
                possible_dispatch = True
                raw_result = await session.call_tool(
                    binding.provider_tool,
                    serialized_arguments,
                    read_timeout_seconds=timedelta(
                        seconds=self._operation_seconds(timeout_class)
                    ),
                )
                normalized_data = self._normalize_call(binding, raw_result)
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
                binding.operation_class is ProviderOperationClass.CREATE
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
                binding.operation_class is ProviderOperationClass.CREATE
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
            raise failure
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
    ):
        headers = await self._request_headers(connection)
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
                ) as (read_stream, write_stream, _get_session_id),
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=operation_seconds),
                ) as session,
            ):
                yield session
        finally:
            for name in tuple(headers):
                headers[name] = ""
            headers.clear()

    async def _request_headers(
        self,
        connection: ProviderConnection,
    ) -> dict[str, str]:
        failure: ProviderRuntimeError | None = None
        headers: dict[str, str] | None = None
        try:
            value = self._header_factory(connection)
            if inspect.isawaitable(value):
                value = await value
            if not isinstance(value, Mapping):
                raise TypeError
            headers = {}
            for name, header_value in value.items():
                if (
                    not isinstance(name, str)
                    or not isinstance(header_value, str)
                    or not name
                    or name.casefold() in _BLOCKED_HEADER_NAMES
                    or "\r" in name
                    or "\n" in name
                    or "\r" in header_value
                    or "\n" in header_value
                ):
                    raise ValueError
                headers[name] = header_value
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

    def _normalize_call(
        self,
        binding: QualifiedCapabilityBinding,
        result: object,
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
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            ) from None
        if not isinstance(normalized, dict):
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.DISPATCHED,
            )
        return normalized

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
        if _contains_exception(error, (TimeoutError, httpx.TimeoutException)):
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


def _closed_runtime_error(error: ProviderRuntimeError) -> ProviderRuntimeError:
    return type(error)(
        error.provider,
        dispatch_certainty=error.dispatch_certainty,
    )


__all__ = [
    "HeaderFactory",
    "ResponseNormalizer",
    "StreamableMCPDriver",
]
