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
from enum import Enum, auto
from typing import Any
from urllib.parse import unquote_to_bytes
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator, FormatChecker
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import McpError
from mcp.types import INVALID_PARAMS, INVALID_REQUEST, PARSE_ERROR, JSONRPCMessage
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
_JSON_POINTER_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")
_INVALID_JSON_POINTER_ESCAPE = re.compile(r"~(?:[^01]|$)")
_INVALID_URI_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_PROVIDER_LOG_REDACTION = "provider_runtime_log_redacted"
_PROVIDER_LOGGER_NAME = "mercury.provider.runtime"
_PROVIDER_LOG_PATHNAME = "provider_runtime"
_PROVIDER_LOG_BOUNDARY_ATTR = "_mercury_provider_make_record_boundary_v1"
_PINNED_CLIENT_INITIALIZE_CODE = ClientSession.initialize.__code__


class _ProviderProtocolFailure(Enum):
    PARSER = auto()
    UNEXPECTED_CONTENT_TYPE = auto()


@dataclass(slots=True)
class _ProviderRuntimeState:
    correlation_values: Mapping[str, str]
    protocol_failure: _ProviderProtocolFailure | None = None


_provider_log_boundary_state = logging.Logger.__dict__.get(_PROVIDER_LOG_BOUNDARY_ATTR)
if not isinstance(_provider_log_boundary_state, dict):
    _provider_log_boundary_state = {
        "scope": ContextVar("mercury_provider_log_scope", default=None),
        "delegate": logging.Logger.makeRecord,
        "wrapper": None,
    }
    setattr(logging.Logger, _PROVIDER_LOG_BOUNDARY_ATTR, _provider_log_boundary_state)
_PROVIDER_LOG_SCOPE: ContextVar[_ProviderRuntimeState | None] = _provider_log_boundary_state[
    "scope"
]


def _safe_provider_log_record(
    state: _ProviderRuntimeState,
    level: object,
) -> logging.LogRecord:
    safe_level = level if isinstance(level, int) and not isinstance(level, bool) else logging.INFO
    record = logging.LogRecord(
        _PROVIDER_LOGGER_NAME,
        safe_level,
        _PROVIDER_LOG_PATHNAME,
        0,
        _PROVIDER_LOG_REDACTION,
        (),
        None,
        _PROVIDER_LOG_PATHNAME,
        None,
    )
    for key, value in state.correlation_values.items():
        record.__dict__[key] = value
    return record


def _install_provider_log_record_boundary() -> None:
    state = logging.Logger.__dict__[_PROVIDER_LOG_BOUNDARY_ATTR]
    wrapper = state["wrapper"]
    if wrapper is None:

        def provider_make_record(
            logger: logging.Logger,
            name: str,
            level: int,
            fn: str,
            lno: int,
            msg: object,
            args: object,
            exc_info: object,
            func: str | None = None,
            extra: Mapping[str, object] | None = None,
            sinfo: str | None = None,
        ) -> logging.LogRecord:
            boundary = logging.Logger.__dict__[_PROVIDER_LOG_BOUNDARY_ATTR]
            provider_state = boundary["scope"].get()
            if provider_state is not None:
                return _safe_provider_log_record(provider_state, level)
            delegate = boundary["delegate"]
            return delegate(
                logger,
                name,
                level,
                fn,
                lno,
                msg,
                args,
                exc_info,
                func,
                extra,
                sinfo,
            )

        wrapper = provider_make_record
        state["wrapper"] = wrapper

    if logging.Logger.makeRecord is not wrapper:
        state["delegate"] = logging.Logger.makeRecord
        logging.Logger.makeRecord = wrapper


def _remove_legacy_transport_log_filters() -> None:
    transport_logger = logging.getLogger("mcp.client.streamable_http")
    for item in tuple(transport_logger.filters):
        item_type = type(item)
        if (
            item_type.__module__ == __name__
            and item_type.__name__ == "_SuppressRawMCPTransportLogs"
        ):
            transport_logger.removeFilter(item)


_remove_legacy_transport_log_filters()
_install_provider_log_record_boundary()


@contextmanager
def _provider_log_suppression(
    *,
    operation_id: UUID | None = None,
):
    correlations: dict[str, str] = {}
    if type(operation_id) is UUID and operation_id.int != 0:
        correlations["operation_id"] = str(operation_id)
    state = _ProviderRuntimeState(correlation_values=correlations)
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
    outer_expires_at: float
    _clock: Callable[[], float]

    @classmethod
    def start(
        cls,
        seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> _OperationDeadline:
        selected_clock = clock or asyncio.get_running_loop().time
        started_at = selected_clock()
        expires_at = started_at + seconds
        return cls(
            started_at=started_at,
            expires_at=expires_at,
            outer_expires_at=expires_at,
            _clock=selected_clock,
        )

    def reschedule(self, seconds: float) -> None:
        self.expires_at = min(self.outer_expires_at, self.started_at + seconds)
        self.check()

    def check(self) -> None:
        self.remaining()

    def remaining(self) -> float:
        remaining = self.expires_at - self._clock()
        if remaining <= 0:
            raise _OperationDeadlineExpired
        return remaining


# Qualification resolution happens before a hosted provider call is dispatched.
# Keep the caller's deadline available to that boundary without exposing the
# runtime's mutable timeout machinery to provider-specific code.
ProviderOperationDeadline = _OperationDeadline
_PROVIDER_OPERATION_DEADLINE: ContextVar[ProviderOperationDeadline | None] = ContextVar(
    "mercury_provider_operation_deadline",
    default=None,
)


def current_provider_operation_deadline() -> ProviderOperationDeadline | None:
    return _PROVIDER_OPERATION_DEADLINE.get()


@contextmanager
def provider_operation_deadline(deadline: ProviderOperationDeadline):
    if not isinstance(deadline, _OperationDeadline):
        raise TypeError("provider_operation_deadline_invalid")
    token = _PROVIDER_OPERATION_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _PROVIDER_OPERATION_DEADLINE.reset(token)


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


_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "$defs",
        "definitions",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
_SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
    }
)
_SCHEMA_LIST_KEYWORDS = frozenset(
    {
        "allOf",
        "anyOf",
        "oneOf",
        "prefixItems",
    }
)
_SCHEMA_OBJECT_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "dependencies",
        "dependentRequired",
        "dependentSchemas",
        "maxProperties",
        "minProperties",
        "patternProperties",
        "properties",
        "propertyNames",
        "required",
        "unevaluatedProperties",
    }
)
_WIRE_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
_SUPPORTED_WIRE_FORMATS = frozenset(
    {
        "date",
        "date-time",
        "duration",
        "email",
        "hostname",
        "idn-email",
        "idn-hostname",
        "ipv4",
        "ipv6",
        "iri",
        "iri-reference",
        "json-pointer",
        "regex",
        "relative-json-pointer",
        "time",
        "uri",
        "uri-reference",
        "uri-template",
        "uuid",
    }
)


@dataclass(frozen=True, slots=True)
class _WireModelContract:
    model: type[BaseModel]
    schema_sha256: str
    validator: Draft202012Validator

    def validate(self, value: object) -> None:
        _assert_closed_local_ref_targets(self.validator.schema)
        self.validator.validate(value)


def _assert_wire_schema_dialect(value: object) -> None:
    if isinstance(value, Mapping):
        if "$schema" in value and value["$schema"] != _WIRE_SCHEMA_DIALECT:
            raise TypeError("provider_schema_model_invalid")
        for child in value.values():
            _assert_wire_schema_dialect(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _assert_wire_schema_dialect(child)


def _decode_local_json_pointer(reference: object) -> tuple[str, ...]:
    if not isinstance(reference, str) or not reference.startswith("#"):
        raise TypeError("provider_schema_model_invalid")
    fragment = reference[1:]
    if _INVALID_URI_PERCENT_ESCAPE.search(fragment):
        raise TypeError("provider_schema_model_invalid")
    try:
        pointer = unquote_to_bytes(fragment).decode("utf-8")
    except UnicodeDecodeError:
        raise TypeError("provider_schema_model_invalid") from None
    if not pointer:
        return ()
    if not pointer.startswith("/"):
        raise TypeError("provider_schema_model_invalid")

    tokens: list[str] = []
    for token in pointer[1:].split("/"):
        if _INVALID_JSON_POINTER_ESCAPE.search(token):
            raise TypeError("provider_schema_model_invalid")
        tokens.append(token.replace("~1", "/").replace("~0", "~"))
    return tuple(tokens)


def _resolve_local_schema_ref(
    root_schema: Mapping[str, Any],
    reference: object,
) -> Mapping[str, Any]:
    target: object = root_schema
    for token in _decode_local_json_pointer(reference):
        if isinstance(target, Mapping):
            if token not in target:
                raise TypeError("provider_schema_model_invalid")
            target = target[token]
        elif isinstance(target, list):
            if _JSON_POINTER_ARRAY_INDEX.fullmatch(token) is None:
                raise TypeError("provider_schema_model_invalid")
            try:
                target = target[int(token)]
            except (IndexError, ValueError):
                raise TypeError("provider_schema_model_invalid") from None
        else:
            raise TypeError("provider_schema_model_invalid")
    if not isinstance(target, Mapping):
        raise TypeError("provider_schema_model_invalid")
    return target


def _wire_schema_children(schema: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    children: list[Mapping[str, Any]] = []
    for key, value in schema.items():
        if key in _SCHEMA_MAP_KEYWORDS:
            if not isinstance(value, Mapping):
                raise TypeError("provider_schema_model_invalid")
            if any(not isinstance(child, Mapping) for child in value.values()):
                raise TypeError("provider_schema_model_invalid")
            children.extend(value.values())
        elif key in _SCHEMA_SINGLE_KEYWORDS:
            if not isinstance(value, Mapping):
                raise TypeError("provider_schema_model_invalid")
            children.append(value)
        elif key in _SCHEMA_LIST_KEYWORDS:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(child, Mapping) for child in value)
            ):
                raise TypeError("provider_schema_model_invalid")
            children.extend(value)
    return children


def _assert_closed_local_ref_targets(root_schema: Mapping[str, Any]) -> None:
    checked_schema_ids: set[int] = set()
    active_schema_ids: set[int] = set()

    def visit(schema: Mapping[str, Any]) -> None:
        if schema is not root_schema and "$id" in schema:
            raise TypeError("provider_schema_model_invalid")
        schema_id = id(schema)
        if schema_id in checked_schema_ids:
            return
        if schema_id in active_schema_ids:
            raise TypeError("provider_schema_model_invalid")
        active_schema_ids.add(schema_id)
        try:
            if "$ref" in schema:
                target = _resolve_local_schema_ref(root_schema, schema["$ref"])
                _assert_closed_wire_schema(target)
                visit(target)
            for child in _wire_schema_children(schema):
                visit(child)
        finally:
            active_schema_ids.discard(schema_id)
        checked_schema_ids.add(schema_id)

    visit(root_schema)


def _assert_closed_wire_schema(schema: Mapping[str, Any]) -> None:
    if not schema:
        raise TypeError("provider_schema_model_invalid")
    schema_format = schema.get("format")
    if schema_format is not None and (
        not isinstance(schema_format, str)
        or schema_format not in _SUPPORTED_WIRE_FORMATS
        or schema_format not in FormatChecker.checkers
    ):
        raise TypeError("provider_schema_model_invalid")

    schema_type = schema.get("type")
    object_schema = (
        schema_type == "object"
        or (isinstance(schema_type, list) and "object" in schema_type)
        or any(key in schema for key in _SCHEMA_OBJECT_KEYWORDS)
        or (
            schema_type is None
            and "$ref" not in schema
            and not any(key in schema for key in _SCHEMA_LIST_KEYWORDS)
        )
    )
    if object_schema and (
        schema.get("additionalProperties") is not False
        or schema.get("unevaluatedProperties", False) not in {False, None}
        or bool(schema.get("patternProperties"))
    ):
        raise TypeError("provider_schema_model_invalid")

    for child in _wire_schema_children(schema):
        _assert_closed_wire_schema(child)


def _wire_model_contract(model: type[BaseModel]) -> _WireModelContract:
    try:
        if (
            not isinstance(model, type)
            or not issubclass(model, BaseModel)
            or model.model_config.get("extra") != "forbid"
            or model.model_config.get("frozen") is not True
            or model.model_config.get("revalidate_instances") != "always"
        ):
            raise TypeError
        schema = model.model_json_schema(
            by_alias=True,
            mode="serialization",
        )
        if not isinstance(schema, Mapping):
            raise TypeError
        _assert_wire_schema_dialect(schema)
        _assert_closed_wire_schema(schema)
        validation_schema = dict(schema)
        validation_schema["$schema"] = _WIRE_SCHEMA_DIALECT
        Draft202012Validator.check_schema(validation_schema)
        format_checker = FormatChecker(formats=sorted(_SUPPORTED_WIRE_FORMATS))
        canonical_schema = canonical_json(schema)
        return _WireModelContract(
            model=model,
            schema_sha256=hashlib.sha256(canonical_schema.encode("utf-8")).hexdigest(),
            validator=Draft202012Validator(
                validation_schema,
                format_checker=format_checker,
            ),
        )
    except Exception:
        raise TypeError("provider_schema_model_invalid") from None


def wire_schema_sha256(model: type[BaseModel]) -> str:
    """Return the canonical Task 5 wire schema identity."""

    return _wire_model_contract(model).schema_sha256


def validation_schema_sha256(model: type[BaseModel]) -> str:
    """Compatibility alias for the Task 5 wire schema identity."""

    return wire_schema_sha256(model)


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
        monotonic_clock: Callable[[], float] | None = None,
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
        self._monotonic_clock = monotonic_clock

    async def discover(
        self,
        connection: ProviderConnection,
    ) -> ProviderDiscovery:
        deadline = _OperationDeadline.start(
            self._operation_seconds(TimeoutClass.DISCOVERY),
            clock=self._monotonic_clock,
        )
        runtime_state: _ProviderRuntimeState | None = None
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        capabilities: list[str] | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression() as runtime_state:
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
                        runtime_state,
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
                runtime_state,
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
                runtime_state,
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
        deadline = _OperationDeadline.start(
            self._operation_seconds(TimeoutClass.READ),
            clock=self._monotonic_clock,
        )
        runtime_state: _ProviderRuntimeState | None = None
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        resource: ResolvedProviderResource | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            with _provider_log_suppression() as runtime_state:
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
                        runtime_state,
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
                runtime_state,
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
                runtime_state,
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
        timeout_class = (
            TimeoutClass.CREATE
            if isinstance(binding, QualifiedCapabilityBinding)
            and binding.operation_class is ProviderOperationClass.CREATE
            else TimeoutClass.READ
        )
        deadline = _OperationDeadline.start(
            self._operation_seconds(timeout_class),
            clock=self._monotonic_clock,
        )
        runtime_state: _ProviderRuntimeState | None = None
        possible_dispatch = False
        observed_auth_statuses: set[int] = set()
        failure: ProviderRuntimeError | None = None
        result: ProviderCallResult | None = None
        verified_binding: VerifiedRuntimeBinding | None = None
        boundary: _RequestBoundaryValues | None = None
        try:
            trusted_operation_id = (
                operation_id if type(operation_id) is UUID and operation_id.int != 0 else None
            )
            with _provider_log_suppression(operation_id=trusted_operation_id) as runtime_state:
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
                    request_contract, response_contract = await self._resolve_schema_models(
                        verified_binding,
                        deadline,
                    )
                    serialized_arguments = self._serialize_arguments(
                        arguments,
                        request_contract,
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
                        runtime_state,
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
                            response_contract,
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
                    runtime_state,
                )
            else:
                failure = self._predispatch_error(
                    exc,
                    observed_auth_statuses,
                    runtime_state,
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
                    runtime_state,
                ) from None
            raise self._predispatch_error(
                exc,
                observed_auth_statuses,
                runtime_state,
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
        runtime_state: _ProviderRuntimeState,
    ):
        headers = await self._request_headers(connection, boundary, deadline)
        deadline.check()
        operation_seconds = self._operation_seconds(timeout_class)
        timeout = httpx.Timeout(
            operation_seconds,
            connect=self._manifest.timeout_classes[timeout_class].connect_seconds,
        )

        async def observe_response(response: httpx.Response) -> None:
            if response.status_code in _AUTH_HTTP_STATUSES:
                observed_auth_statuses.add(response.status_code)
            if (
                response.request.method == "POST"
                and 200 <= response.status_code < 300
                and response.status_code != 202
            ):
                content_type = response.headers.get("content-type", "")
                media_type = content_type.partition(";")[0].strip().casefold()
                if media_type not in {"application/json", "text/event-stream"}:
                    runtime_state.protocol_failure = (
                        _ProviderProtocolFailure.UNEXPECTED_CONTENT_TYPE
                    )
                elif media_type == "application/json":
                    content = await response.aread()
                    try:
                        JSONRPCMessage.model_validate_json(content)
                    except (ValidationError, json.JSONDecodeError):
                        runtime_state.protocol_failure = _ProviderProtocolFailure.PARSER

        try:
            deadline.remaining()
            async with (
                httpx.AsyncClient(
                    headers=headers,
                    timeout=timeout,
                    follow_redirects=False,
                    event_hooks={"response": [observe_response]},
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
                qualification_deadline = current_provider_operation_deadline() or deadline
                with provider_operation_deadline(qualification_deadline):
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
    ) -> tuple[_WireModelContract, _WireModelContract]:
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
            request_contract = _wire_model_contract(request_model)
            deadline.check()
            response_contract = _wire_model_contract(response_model)
            deadline.check()
            if (
                request_contract.schema_sha256 != binding.request_schema_sha256
                or response_contract.schema_sha256 != binding.response_schema_sha256
            ):
                raise TypeError
        except _OperationDeadlineExpired:
            raise
        except Exception:
            raise ProviderResponseInvalid(
                self.provider,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ) from None
        return request_contract, response_contract

    def _serialize_arguments(
        self,
        arguments: BaseModel,
        request_contract: _WireModelContract,
        deadline: _OperationDeadline,
    ) -> dict[str, Any]:
        try:
            if type(arguments) is not request_contract.model:
                raise TypeError
            revalidated = request_contract.model.model_validate(
                arguments,
                by_alias=True,
                by_name=True,
            )
            deadline.check()
            if type(revalidated) is not request_contract.model:
                raise TypeError
            serialized = revalidated.model_dump(
                by_alias=True,
                mode="json",
                warnings="error",
            )
            deadline.check()
            if not isinstance(serialized, dict):
                raise TypeError
            request_contract.validate(serialized)
            deadline.check()
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
        response_contract: _WireModelContract,
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
            if type(normalized) is not response_contract.model:
                raise TypeError
            revalidated = response_contract.model.model_validate(
                normalized,
                by_alias=True,
                by_name=True,
            )
            deadline.check()
            if type(revalidated) is not response_contract.model:
                raise TypeError
            serialized = revalidated.model_dump(
                by_alias=True,
                mode="json",
                warnings="error",
            )
            deadline.check()
            response_contract.validate(serialized)
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
        runtime_state: _ProviderRuntimeState | None,
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
            runtime_state is not None
            and runtime_state.protocol_failure is not None
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
        runtime_state: _ProviderRuntimeState | None,
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
            runtime_state is not None
            and runtime_state.protocol_failure is not None
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
    if _contains_exception(
        error,
        (_MCPProtocolFailure, ValidationError, json.JSONDecodeError),
    ):
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
    "ProviderOperationDeadline",
    "ProviderAuthHeader",
    "ProviderAuthHeaders",
    "RequestModelResolver",
    "ResponseNormalizer",
    "ResponseModelResolver",
    "StreamableMCPDriver",
    "current_provider_operation_deadline",
    "provider_operation_deadline",
    "validation_schema_sha256",
    "wire_schema_sha256",
]
