"""Mercury V1 MCP tool registration and request-bound handlers."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request

from mercury_tools.auth.models import MercuryAuthError, MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.execution.hosted.read_service import HostedReadService
from mercury_tools.mcp.contracts import LEGACY_HOSTED_TOOL_NAMES, V1_HOSTED_TOOL_NAMES
from mercury_tools.mcp.generated_tools import GeneratedProviderToolPublisher
from mercury_tools.mcp.v1_errors import (
    MercuryV1ToolError,
    public_error_code,
    published_error_output_schema,
)
from mercury_tools.mcp.v1_schemas import (
    CAPABILITY_ID_PATTERN,
    SHA256_PATTERN,
    ConnectorStatusData,
    ConnectorStatusOutput,
    DisconnectProviderData,
    DisconnectProviderOutput,
    FlowAccountConnectionOutput,
    FlowAccountConnectionStartData,
    FlowAccountDisconnectedData,
    FlowAccountProviderOutput,
    FlowAccountRevocationRequiredData,
    GetCapabilitySchemaOutput,
    GetMercuryContextOutput,
    ListAccountingProvidersOutput,
    ListProviderCapabilitiesOutput,
    ListProviderConnectionsOutput,
    PeakConnectionOutput,
    PeakConnectionStartData,
    PeakDisconnectData,
    PeakProviderOutput,
    ProviderCapabilityOutput,
    ProviderConnectionOutput,
    ReviewedCapabilitySchemaOutput,
    StartProviderConnectionArguments,
    StartProviderConnectionOutput,
    start_provider_connection_input_schema,
)
from mercury_tools.providers.finalization import await_cleanup
from mercury_tools.providers.models import (
    ConnectionReadiness,
    ProviderConnection,
    ProviderConnectionSummary,
    ProviderId,
)
from mercury_tools.providers.production import build_provider_oauth_production_composition
from mercury_tools.providers.streamable_mcp import (
    ProviderOperationDeadline,
    provider_operation_deadline,
)
from mercury_tools.qualification.provider_mcp import (
    CapabilityResolution,
    CapabilitySelection,
)
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole
from mercury_tools.workspaces.service import WorkspaceService

GET_MERCURY_CONTEXT_TOOL = "get_mercury_context"
LIST_ACCOUNTING_PROVIDERS_TOOL = "list_accounting_providers"
START_PROVIDER_CONNECTION_TOOL = "start_provider_connection"
LIST_PROVIDER_CONNECTIONS_TOOL = "list_provider_connections"
CONNECTOR_STATUS_TOOL = "connector_status"
LIST_PROVIDER_CAPABILITIES_TOOL = "list_provider_capabilities"
GET_CAPABILITY_SCHEMA_TOOL = "get_capability_schema"
DISCONNECT_PROVIDER_TOOL = "disconnect_provider"

WorkspaceServiceFactory = Callable[[], WorkspaceService]
ProviderRuntimeFactory: TypeAlias = Callable[[], Any | Awaitable[Any]]
AuditRecorder: TypeAlias = Callable[[dict[str, object]], object | Awaitable[object]]

_V1_SEED_CAPABILITIES = frozenset(
    {
        "provider_profile.get",
        "documents.invoice.list",
        "documents.invoice.get",
        "documents.invoice.create",
    }
)

_IDEMPOTENT_MERCURY_STATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_CLOSED_READ = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    openWorldHint=False,
)
_START_PROVIDER_CONNECTION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
_AUDIT_ONLY = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
_DISCONNECT_PROVIDER = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)
_V1_TOOL_META = {
    "mercury/surface": "v1",
    "mercury/error-schema": "mercury.v1.error.v1",
}
_V1_CONFIGURATION_LOCK = threading.RLock()


def _workspace_service() -> WorkspaceService:
    return WorkspaceService.from_settings(load_settings())


async def _provider_runtime() -> Any:
    return await asyncio.to_thread(
        build_provider_oauth_production_composition,
        settings=load_settings(),
    )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, separator, token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not token
        or token != token.strip()
        or any(character.isspace() for character in token)
    ):
        return None
    return token


def _authenticated_request(context: Context) -> tuple[MercuryPrincipal, str]:
    try:
        request = context.request_context.request
    except (AttributeError, ValueError):
        raise MercuryAuthError("mercury_auth_required") from None
    if not isinstance(request, Request):
        raise MercuryAuthError("mercury_auth_required")

    principal = getattr(request.state, "mercury_principal", None)
    if not isinstance(principal, MercuryPrincipal):
        raise MercuryAuthError("mercury_auth_required")

    access_token = _bearer_token(request.headers.get("authorization"))
    if access_token is None:
        raise MercuryAuthError("mercury_auth_required")
    return principal, access_token


async def _await_value(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _require_workspace(
    context: Context,
    *,
    workspace_id: UUID,
    service_factory: WorkspaceServiceFactory,
) -> tuple[MercuryPrincipal, WorkspaceMembership]:
    principal, access_token = _authenticated_request(context)
    service = service_factory()
    membership = await _await_value(
        service.require_workspace(
            principal,
            access_token,
            workspace_id,
            WorkspaceRole.MEMBER,
        )
    )
    return principal, WorkspaceMembership.model_validate(membership)


async def _runtime_from(factory: ProviderRuntimeFactory | None) -> Any:
    return await _await_value((factory or _provider_runtime)())


async def _close_runtime(runtime: Any) -> None:
    close = getattr(runtime, "aclose", None)
    if callable(close):
        await await_cleanup(_await_value(close()))


async def _record_connector_status_audit(event: dict[str, object]) -> None:
    """Persist a non-critical status audit without exposing connection identity."""

    try:
        store = SupabaseRagStore(load_settings())
        await asyncio.to_thread(store.record_audit_event, event)
    except Exception:
        return


async def _write_audit(
    recorder: AuditRecorder,
    event: dict[str, object],
) -> None:
    try:
        await _await_value(recorder(event))
    except Exception:
        return


async def _store_list_connections(
    runtime: Any,
    *,
    membership: WorkspaceMembership,
    workspace_id: UUID,
    principal: MercuryPrincipal,
) -> tuple[ProviderConnectionSummary, ...]:
    method = runtime.connection_store.list_for_workspace
    result = await asyncio.to_thread(
        method,
        tenant_id=membership.tenant_id,
        workspace_id=workspace_id,
        auth_user_id=principal.subject,
    )
    resolved = await _await_value(result)
    return tuple(ProviderConnectionSummary.model_validate(item) for item in resolved)


async def _store_load_connection(
    runtime: Any,
    *,
    membership: WorkspaceMembership,
    workspace_id: UUID,
    principal: MercuryPrincipal,
    connection_id: UUID,
) -> ProviderConnection:
    method = runtime.connection_store.load_connection
    result = await asyncio.to_thread(
        method,
        tenant_id=membership.tenant_id,
        workspace_id=workspace_id,
        auth_user_id=principal.subject,
        connection_id=connection_id,
    )
    resolved = await _await_value(result)
    return ProviderConnection.model_validate(resolved)


async def _catalog_qualifications(runtime: Any) -> tuple[ProviderMCPQualification, ...]:
    result = await asyncio.to_thread(runtime.qualification_catalog.list_provider_mcp_qualifications)
    resolved = await _await_value(result)
    return tuple(ProviderMCPQualification.model_validate(item) for item in resolved)


class GeneratedProviderToolProjection:
    """Bind dynamic V1 wrappers to the composition selected by the HTTP lifespan."""

    def __init__(
        self,
        server: FastMCP,
        *,
        runtime_factory: ProviderRuntimeFactory | None,
        service_factory: WorkspaceServiceFactory,
        close_runtime: bool,
    ) -> None:
        self._server = server
        self._runtime_factory = runtime_factory
        self._service_factory = service_factory
        self._close_runtime = close_runtime
        self._publisher = GeneratedProviderToolPublisher(
            server,
            execute=self._execute,
            persist_schema_change=self._persist_schema_change,
            schema_drift_alert=self._record_schema_drift_alert,
        )

    def reconfigure(
        self,
        *,
        runtime_factory: ProviderRuntimeFactory | None,
        service_factory: WorkspaceServiceFactory,
        close_runtime: bool,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._service_factory = service_factory
        self._close_runtime = close_runtime

    async def refresh(self, context: Context | object | None = None) -> bool:
        runtime: Any | None = None
        try:
            runtime = await _runtime_from(self._runtime_factory)
            return await self._publisher.reconcile(
                await _catalog_qualifications(runtime),
                context=context,
            )
        finally:
            if runtime is not None and self._close_runtime:
                await _close_runtime(runtime)

    async def _execute(
        self,
        tool_context: Context,
        *,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        inputs: BaseModel,
    ) -> Any:
        principal, membership = await _require_workspace(
            tool_context,
            workspace_id=workspace_id,
            service_factory=self._service_factory,
        )

        async def bound_membership(
            requested_principal: MercuryPrincipal,
            requested_workspace_id: UUID,
        ) -> WorkspaceMembership:
            if requested_principal != principal or requested_workspace_id != workspace_id:
                raise ValueError("workspace_access_denied")
            return membership

        service = HostedReadService(
            runtime_factory=self._runtime_factory or _provider_runtime,
            membership_resolver=bound_membership,
            audit_recorder=_record_connector_status_audit,
            close_runtime=self._close_runtime,
        )
        return await service.execute(
            principal,
            workspace_id,
            connection_id,
            capability_id,
            capability_version,
            inputs,
        )

    async def _record_schema_drift_alert(
        self,
        event: dict[str, object],
    ) -> None:
        await _record_connector_status_audit(event)

    async def _persist_schema_change(
        self,
        qualification: ProviderMCPQualification,
        _context: Context | object,
    ) -> tuple[ProviderMCPQualification, ...]:
        runtime: Any | None = None
        try:
            runtime = await _runtime_from(self._runtime_factory)
            transition = getattr(
                runtime.qualification_catalog,
                "disable_provider_mcp_capability_version",
                None,
            )
            if not callable(transition):
                raise ValueError("catalog_schema_transition_unavailable")
            result = await asyncio.to_thread(transition, qualification)
            await _await_value(result)
            # Reload only after the authority persistence call has completed.
            return await _catalog_qualifications(runtime)
        finally:
            if runtime is not None and self._close_runtime:
                await _close_runtime(runtime)


async def refresh_generated_provider_tools(
    server: FastMCP,
    *,
    runtime_factory: ProviderRuntimeFactory | None = None,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    context: Context | object | None = None,
    close_runtime: bool = True,
) -> bool:
    """Refresh the server-scoped projection from the exact catalog authority."""

    projection = getattr(server, "_mercury_v1_generated_provider_projection", None)
    if not isinstance(projection, GeneratedProviderToolProjection):
        projection = GeneratedProviderToolProjection(
            server,
            runtime_factory=runtime_factory,
            service_factory=service_factory,
            close_runtime=close_runtime,
        )
        server._mercury_v1_generated_provider_projection = projection
        server._mercury_v1_generated_provider_tools = projection._publisher
    else:
        projection.reconfigure(
            runtime_factory=runtime_factory,
            service_factory=service_factory,
            close_runtime=close_runtime,
        )
    return await projection.refresh(context)


async def refresh_generated_provider_tools_until_stopped(
    server: FastMCP,
    *,
    runtime_factory: ProviderRuntimeFactory | None = None,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    close_runtime: bool = True,
    stop_event: asyncio.Event,
    interval_seconds: float,
) -> None:
    """Poll catalog authority for the HTTP lifespan without a request context."""

    if interval_seconds <= 0:
        raise ValueError("generated_refresh_interval_invalid")
    while not stop_event.is_set():
        try:
            await refresh_generated_provider_tools(
                server,
                runtime_factory=runtime_factory,
                service_factory=service_factory,
                close_runtime=close_runtime,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # Initial projection gates readiness. Later authority outages must not
            # take down an already-serving MCP session; the next bounded poll
            # reconciles the projection.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            continue


def _connection_output(summary: ProviderConnectionSummary) -> ProviderConnectionOutput:
    values = {
        "connection_id": summary.connection_id,
        "provider": summary.provider.value,
        "environment": summary.environment,
        "account_display_name": summary.account_display_name,
        "authorization_method": summary.authorization_method.value,
        "granted_permissions": list(summary.granted_permissions),
        "readiness": summary.readiness.value,
        "revision": summary.revision,
        "provider_revocation_required": summary.provider_revocation_required,
    }
    if summary.provider is ProviderId.FLOWACCOUNT:
        return FlowAccountConnectionOutput.model_validate(values)
    return PeakConnectionOutput.model_validate(values)


def _selected_connection(
    connections: Iterable[ProviderConnectionSummary],
    connection_id: UUID,
) -> ProviderConnectionSummary:
    selected = next(
        (item for item in connections if item.connection_id == connection_id),
        None,
    )
    if selected is None:
        raise ValueError("provider_connection_required")
    return selected


def _qualification_matches(
    qualification: ProviderMCPQualification,
    connection: ProviderConnectionSummary,
) -> bool:
    return (
        qualification.provider == connection.provider.value
        and qualification.environment == connection.environment
    )


def _qualification_selection(
    qualification: ProviderMCPQualification,
) -> CapabilitySelection:
    return CapabilitySelection(
        provider=qualification.provider,
        environment=qualification.environment,
        normalized_capability=qualification.normalized_capability,
        provider_tool_name=qualification.provider_tool_name,
        capability_version_sha256=qualification.capability_version_sha256,
    )


async def _resolve_qualification(
    runtime: Any,
    *,
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
) -> CapabilityResolution:
    deadline = ProviderOperationDeadline.start(5)
    with provider_operation_deadline(deadline):
        resolved = await _await_value(
            runtime.qualification_resolver.resolve_for_connection(
                connection,
                selection=_qualification_selection(qualification),
            )
        )
    if not isinstance(resolved, CapabilityResolution):
        raise ValueError("capability_unavailable")
    return resolved


async def _missing_qualification_capabilities(
    runtime: Any,
    qualifications: Iterable[ProviderMCPQualification],
    connection: ProviderConnection,
) -> list[str]:
    enabled: set[str] = set()
    for qualification in qualifications:
        if not _qualification_matches(qualification, connection):
            continue
        resolution = await _resolve_qualification(
            runtime,
            connection=connection,
            qualification=qualification,
        )
        if resolution.status == "enabled":
            enabled.add(qualification.normalized_capability)
    return sorted(_V1_SEED_CAPABILITIES - enabled)


async def _capability_status_detail(
    runtime: Any,
    qualification: ProviderMCPQualification,
    connection: ProviderConnection,
) -> tuple[
    Literal["enabled", "unavailable"],
    Literal[
        "enabled",
        "connection_not_ready",
        "capability_unavailable",
        "capability_unreviewed",
        "insufficient_evidence",
    ],
]:
    if connection.readiness is not ConnectionReadiness.READY:
        return "unavailable", "connection_not_ready"
    resolution = await _resolve_qualification(
        runtime,
        connection=connection,
        qualification=qualification,
    )
    if resolution.status == "enabled":
        return "enabled", "enabled"
    if qualification.qualification_state in {
        QualificationState.DISCOVERED_UNREVIEWED,
        QualificationState.SCHEMA_VALIDATED,
    }:
        return "unavailable", "capability_unreviewed"
    if resolution.status == "insufficient_evidence":
        return "unavailable", "insufficient_evidence"
    return "unavailable", "capability_unavailable"


def _sha256_identifier(value: UUID) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _connector_status_audit_event(
    *,
    workspace_id: UUID,
    connection_id: UUID,
    connection: ProviderConnectionSummary,
    missing_qualification_capabilities: list[str],
) -> dict[str, object]:
    return {
        "tool_name": CONNECTOR_STATUS_TOOL,
        "input": {
            "workspace_id_sha256": _sha256_identifier(workspace_id),
            "connection_id_sha256": _sha256_identifier(connection_id),
        },
        "output_summary": {
            "provider": connection.provider.value,
            "environment": connection.environment,
            "readiness": connection.readiness.value,
            "missing_qualification_count": len(missing_qualification_capabilities),
        },
        "status": "ok",
        "metadata": {"runtime": "mcp", "surface": "v1"},
    }


async def get_mercury_context(
    context: Context,
    *,
    service_factory: WorkspaceServiceFactory = _workspace_service,
) -> GetMercuryContextOutput:
    """Bootstrap and return the caller's sanitized Mercury workspace context."""

    principal, access_token = _authenticated_request(context)
    service = service_factory()
    mercury_context = await asyncio.to_thread(
        service.bootstrap,
        principal,
        access_token,
    )
    return GetMercuryContextOutput.model_validate(mercury_context.model_dump(mode="python"))


async def list_accounting_providers(
    context: Context,
) -> ListAccountingProvidersOutput:
    """List the supported, secretless accounting provider connection methods."""

    try:
        _authenticated_request(context)
        return ListAccountingProvidersOutput(
            data=[
                FlowAccountProviderOutput(
                    provider="flowaccount",
                    connection_method="oauth2_pkce",
                    environments=["sandbox", "production"],
                ),
                PeakProviderOutput(
                    provider="peak",
                    connection_method="provider_credentials",
                    environments=["uat", "production"],
                ),
            ],
            next_allowed_actions=["start_provider_connection"],
        )
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None


async def start_provider_connection(
    context: Context,
    *,
    workspace_id: UUID,
    provider: Literal["flowaccount", "peak"],
    environment: Literal["sandbox", "uat", "production"],
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> StartProviderConnectionOutput:
    """Start one workspace-bound provider authorization or secure setup handoff."""

    runtime: Any | None = None
    try:
        request = StartProviderConnectionArguments.model_validate(
            {
                "workspace_id": workspace_id,
                "provider": provider,
                "environment": environment,
            }
        )
        principal, _membership = await _require_workspace(
            context,
            workspace_id=request.workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        if request.provider == "flowaccount":
            started = await _await_value(
                runtime.provider_oauth_service.start(
                    principal,
                    request.workspace_id,
                    ProviderId.FLOWACCOUNT,
                    request.environment,
                )
            )
            return StartProviderConnectionOutput(
                workspace_id=request.workspace_id,
                provider="flowaccount",
                environment=request.environment,
                data=FlowAccountConnectionStartData(
                    provider="flowaccount",
                    environment=request.environment,
                    authorization_url=str(started.authorization_url),
                    expires_at=started.expires_at,
                ),
                next_allowed_actions=["list_provider_connections"],
            )
        started = await _await_value(
            runtime.peak_setup_service.start(
                principal,
                request.workspace_id,
                ProviderId.PEAK,
                request.environment,
            )
        )
        return StartProviderConnectionOutput(
            workspace_id=request.workspace_id,
            provider="peak",
            environment=request.environment,
            data=PeakConnectionStartData(
                provider="peak",
                environment=request.environment,
                setup_url=str(started.setup_url),
                expires_at=started.expires_at,
            ),
            next_allowed_actions=["list_provider_connections"],
        )
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def list_provider_connections(
    context: Context,
    *,
    workspace_id: UUID,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> ListProviderConnectionsOutput:
    """List sanitized provider connections visible to the selected workspace."""

    runtime: Any | None = None
    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        connections = await _store_list_connections(
            runtime,
            membership=membership,
            workspace_id=workspace_id,
            principal=principal,
        )
        return ListProviderConnectionsOutput(
            workspace_id=workspace_id,
            data=[_connection_output(item) for item in connections],
            next_allowed_actions=["connector_status", "list_provider_capabilities"],
        )
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def connector_status(
    context: Context,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
    audit_recorder: AuditRecorder = _record_connector_status_audit,
) -> ConnectorStatusOutput:
    """Return local readiness and qualification status without provider-wide calls."""

    runtime: Any | None = None
    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        connection = _selected_connection(
            await _store_list_connections(
                runtime,
                membership=membership,
                workspace_id=workspace_id,
                principal=principal,
            ),
            connection_id,
        )
        bound_connection = await _store_load_connection(
            runtime,
            membership=membership,
            workspace_id=workspace_id,
            principal=principal,
            connection_id=connection_id,
        )
        qualifications = await _catalog_qualifications(runtime)
        missing_qualification_capabilities = await _missing_qualification_capabilities(
            runtime,
            qualifications,
            bound_connection,
        )
        response = ConnectorStatusOutput(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=connection.provider,
            environment=connection.environment,
            data=ConnectorStatusData(
                connection=_connection_output(connection),
                missing_qualification_capabilities=missing_qualification_capabilities,
            ),
            next_allowed_actions=["list_provider_capabilities"],
        )
        await _write_audit(
            audit_recorder,
            _connector_status_audit_event(
                workspace_id=workspace_id,
                connection_id=connection_id,
                connection=connection,
                missing_qualification_capabilities=missing_qualification_capabilities,
            ),
        )
        return response
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def list_provider_capabilities(
    context: Context,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> ListProviderCapabilitiesOutput:
    """List catalog capability states for one selected provider connection."""

    runtime: Any | None = None
    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        connection = _selected_connection(
            await _store_list_connections(
                runtime,
                membership=membership,
                workspace_id=workspace_id,
                principal=principal,
            ),
            connection_id,
        )
        bound_connection = await _store_load_connection(
            runtime,
            membership=membership,
            workspace_id=workspace_id,
            principal=principal,
            connection_id=connection_id,
        )
        qualifications = sorted(
            (
                item
                for item in await _catalog_qualifications(runtime)
                if _qualification_matches(item, connection)
            ),
            key=lambda item: (item.normalized_capability, item.capability_version_sha256),
        )
        data: list[ProviderCapabilityOutput] = []
        for qualification in qualifications:
            availability, detail = await _capability_status_detail(
                runtime,
                qualification,
                bound_connection,
            )
            data.append(
                ProviderCapabilityOutput(
                    capability_id=qualification.normalized_capability,
                    capability_version=qualification.capability_version_sha256,
                    qualification_state=qualification.qualification_state,
                    availability=availability,
                    status_detail=detail,
                )
            )
        return ListProviderCapabilitiesOutput(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=connection.provider,
            environment=connection.environment,
            data=data,
            next_allowed_actions=["get_capability_schema"],
        )
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def get_capability_schema(
    context: Context,
    *,
    workspace_id: UUID,
    capability_id: str,
    capability_version: str,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> GetCapabilitySchemaOutput:
    """Return one exact reviewed catalog schema as canonical JSON text."""

    runtime: Any | None = None
    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        qualifications = tuple(
            item
            for item in await _catalog_qualifications(runtime)
            if item.normalized_capability == capability_id
            and item.capability_version_sha256 == capability_version
        )
        if not qualifications:
            raise ValueError("capability_unavailable")
        connections = await _store_list_connections(
            runtime,
            membership=membership,
            workspace_id=workspace_id,
            principal=principal,
        )
        had_insufficient_evidence = False
        for summary in connections:
            for qualification in qualifications:
                if not _qualification_matches(qualification, summary):
                    continue
                connection = await _store_load_connection(
                    runtime,
                    membership=membership,
                    workspace_id=workspace_id,
                    principal=principal,
                    connection_id=summary.connection_id,
                )
                resolution = await _resolve_qualification(
                    runtime,
                    connection=connection,
                    qualification=qualification,
                )
                if resolution.status == "enabled" and resolution.qualification is not None:
                    qualification = resolution.qualification
                    return GetCapabilitySchemaOutput(
                        workspace_id=workspace_id,
                        capability_id=capability_id,
                        capability_version=capability_version,
                        data=ReviewedCapabilitySchemaOutput(
                            capability_id=qualification.normalized_capability,
                            capability_version=qualification.capability_version_sha256,
                            qualification_state=qualification.qualification_state,
                            schema_sha256=qualification.schema_hash,
                            response_shape_sha256=qualification.response_shape_hash,
                            input_schema_json=json.dumps(
                                qualification.model_dump(mode="json")["input_schema"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            output_schema_json=json.dumps(
                                qualification.model_dump(mode="json")["output_schema"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                        ),
                    )
                had_insufficient_evidence = (
                    had_insufficient_evidence or resolution.status == "insufficient_evidence"
                )
        if all(
            qualification.qualification_state
            in {QualificationState.DISCOVERED_UNREVIEWED, QualificationState.SCHEMA_VALIDATED}
            for qualification in qualifications
        ):
            raise ValueError("capability_unreviewed")
        if had_insufficient_evidence:
            raise ValueError("insufficient_evidence")
        raise ValueError("capability_unavailable")
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def disconnect_provider(
    context: Context,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    confirmation: Literal["DISCONNECT"],
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> DisconnectProviderOutput:
    """Delete usable local provider authorization after explicit confirmation."""

    runtime: Any | None = None
    try:
        if confirmation != "DISCONNECT":
            raise ValueError("confirmation_required")
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _runtime_from(runtime_factory)
        connection = _selected_connection(
            await _store_list_connections(
                runtime,
                membership=membership,
                workspace_id=workspace_id,
                principal=principal,
            ),
            connection_id,
        )
        if connection.provider is ProviderId.FLOWACCOUNT:
            disconnected = await _await_value(
                runtime.provider_oauth_service.disconnect(
                    principal,
                    workspace_id,
                    connection_id,
                )
            )
            if disconnected.provider_revocation_required:
                data: DisconnectProviderData = FlowAccountRevocationRequiredData(
                    provider="flowaccount",
                    status=disconnected.status,
                    local_credentials_deleted=disconnected.local_credentials_deleted,
                    remote_revocation_status=disconnected.remote_revocation_status,
                    deleted_envelope_count=disconnected.deleted_envelope_count,
                    provider_revocation_required=disconnected.provider_revocation_required,
                    revision=disconnected.revision,
                )
            else:
                data = FlowAccountDisconnectedData(
                    provider="flowaccount",
                    status=disconnected.status,
                    local_credentials_deleted=disconnected.local_credentials_deleted,
                    remote_revocation_status=disconnected.remote_revocation_status,
                    deleted_envelope_count=disconnected.deleted_envelope_count,
                    provider_revocation_required=disconnected.provider_revocation_required,
                    revision=disconnected.revision,
                )
        else:
            disconnected = await _await_value(
                runtime.peak_setup_service.disconnect(
                    principal,
                    workspace_id,
                    connection_id,
                )
            )
            data = PeakDisconnectData(
                provider="peak",
                status=disconnected.status,
                local_credentials_deleted=disconnected.local_credentials_deleted,
                instruction=disconnected.instruction,
            )
        return DisconnectProviderOutput(
            workspace_id=workspace_id,
            connection_id=connection_id,
            provider=connection.provider,
            environment=connection.environment,
            data=data,
            next_allowed_actions=["list_provider_connections"],
        )
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


def _register_tool(
    server: FastMCP,
    function: Callable[..., Any],
    *,
    name: str,
    description: str,
    annotations: ToolAnnotations,
) -> None:
    if server._tool_manager.get_tool(name) is None:
        server.add_tool(
            function,
            name=name,
            description=description,
            annotations=annotations,
            meta=_V1_TOOL_META,
            structured_output=True,
        )


def _published_output_contract(success_schema: Mapping[str, Any]) -> dict[str, object]:
    """Publish one closed success-or-error output union for every V1 tool."""

    success = dict(success_schema)
    error = published_error_output_schema()
    success_definitions = success.pop("$defs", {})
    error_definitions = error.pop("$defs", {})
    if not isinstance(success_definitions, Mapping) or not isinstance(error_definitions, Mapping):
        raise RuntimeError("mercury_v1_output_schema_invalid")
    duplicate_definitions = set(success_definitions) & set(error_definitions)
    if duplicate_definitions:
        raise RuntimeError("mercury_v1_output_schema_invalid")
    return {
        "$defs": {
            **success_definitions,
            **error_definitions,
            "Success": success,
            "MercuryV1ErrorOutput": error,
        },
        "oneOf": [
            {"$ref": "#/$defs/Success"},
            {"$ref": "#/$defs/MercuryV1ErrorOutput"},
        ],
    }


def _success_output_schema(registered: Any) -> dict[str, Any]:
    output_model = registered.fn_metadata.output_model
    if not isinstance(output_model, type) or not issubclass(output_model, BaseModel):
        raise RuntimeError("mercury_v1_output_schema_invalid")
    return output_model.model_json_schema(by_alias=True)


def _is_v1_registered_tool(registered: Any) -> bool:
    metadata = getattr(registered, "meta", None)
    return isinstance(metadata, Mapping) and metadata.get("mercury/surface") == "v1"


def configure_v1_tools(
    server: FastMCP,
    *,
    enabled: bool,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    runtime_factory: ProviderRuntimeFactory | None = None,
) -> None:
    """Register the stable V1 surface and remove legacy tools when V1 is enabled."""

    with _V1_CONFIGURATION_LOCK:
        _configure_v1_tools(
            server,
            enabled=enabled,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )


def _configure_v1_tools(
    server: FastMCP,
    *,
    enabled: bool,
    service_factory: WorkspaceServiceFactory,
    runtime_factory: ProviderRuntimeFactory | None,
) -> None:
    if not enabled:
        publisher = getattr(server, "_mercury_v1_generated_provider_tools", None)
        if isinstance(publisher, GeneratedProviderToolPublisher):
            publisher.clear()
        for name in V1_HOSTED_TOOL_NAMES:
            registered = server._tool_manager.get_tool(name)
            if registered is not None and _is_v1_registered_tool(registered):
                server.remove_tool(name)
        legacy_tools = getattr(server, "_mercury_v1_legacy_tools", ())
        for name, registered in legacy_tools:
            if server._tool_manager.get_tool(name) is None:
                server._tool_manager._tools[name] = registered
        return

    if not hasattr(server, "_mercury_v1_legacy_tools"):
        legacy_tools = tuple(
            (name, registered)
            for name in LEGACY_HOSTED_TOOL_NAMES
            if (registered := server._tool_manager.get_tool(name)) is not None
            and not _is_v1_registered_tool(registered)
        )
        server._mercury_v1_legacy_tools = legacy_tools
    for name in LEGACY_HOSTED_TOOL_NAMES:
        registered = server._tool_manager.get_tool(name)
        if registered is not None and not _is_v1_registered_tool(registered):
            server.remove_tool(name)

    async def get_mercury_context_tool(context: Context) -> GetMercuryContextOutput:
        try:
            return await get_mercury_context(context, service_factory=service_factory)
        except Exception as error:
            raise MercuryV1ToolError(public_error_code(error)) from None

    async def list_accounting_providers_tool(
        context: Context,
    ) -> ListAccountingProvidersOutput:
        return await list_accounting_providers(context)

    async def start_provider_connection_tool(
        context: Context,
        workspace_id: UUID,
        provider: Literal["flowaccount", "peak"],
        environment: Literal["sandbox", "uat", "production"],
    ) -> StartProviderConnectionOutput:
        return await start_provider_connection(
            context,
            workspace_id=workspace_id,
            provider=provider,
            environment=environment,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    async def list_provider_connections_tool(
        context: Context,
        workspace_id: UUID,
    ) -> ListProviderConnectionsOutput:
        return await list_provider_connections(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    async def connector_status_tool(
        context: Context,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> ConnectorStatusOutput:
        return await connector_status(
            context,
            workspace_id=workspace_id,
            connection_id=connection_id,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    async def list_provider_capabilities_tool(
        context: Context,
        workspace_id: UUID,
        connection_id: UUID,
    ) -> ListProviderCapabilitiesOutput:
        return await list_provider_capabilities(
            context,
            workspace_id=workspace_id,
            connection_id=connection_id,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    async def get_capability_schema_tool(
        context: Context,
        workspace_id: UUID,
        capability_id: Annotated[
            str,
            Field(min_length=3, max_length=200, pattern=CAPABILITY_ID_PATTERN),
        ],
        capability_version: Annotated[str, Field(pattern=SHA256_PATTERN)],
    ) -> GetCapabilitySchemaOutput:
        return await get_capability_schema(
            context,
            workspace_id=workspace_id,
            capability_id=capability_id,
            capability_version=capability_version,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    async def disconnect_provider_tool(
        context: Context,
        workspace_id: UUID,
        connection_id: UUID,
        confirmation: Literal["DISCONNECT"],
    ) -> DisconnectProviderOutput:
        return await disconnect_provider(
            context,
            workspace_id=workspace_id,
            connection_id=connection_id,
            confirmation=confirmation,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
        )

    registrations = (
        (
            get_mercury_context_tool,
            GET_MERCURY_CONTEXT_TOOL,
            "Changes: idempotently bootstraps Mercury workspace state. "
            "External contact: none. Omitted options: workspace selection is "
            "explicit in later calls.",
            _IDEMPOTENT_MERCURY_STATE,
        ),
        (
            list_accounting_providers_tool,
            LIST_ACCOUNTING_PROVIDERS_TOOL,
            "Changes: none. External contact: none. "
            "Omitted options: provider credentials and provider endpoints are not accepted.",
            _CLOSED_READ,
        ),
        (
            start_provider_connection_tool,
            START_PROVIDER_CONNECTION_TOOL,
            "Changes: creates a workspace-bound provider setup attempt. "
            "External contact: starts the server-configured provider authorization handoff. "
            "Omitted options: credentials, provider URLs, and company overrides are not accepted.",
            _START_PROVIDER_CONNECTION,
        ),
        (
            list_provider_connections_tool,
            LIST_PROVIDER_CONNECTIONS_TOOL,
            "Changes: none. External contact: none. "
            "Omitted options: provider account identifiers and credential material "
            "are not returned.",
            _CLOSED_READ,
        ),
        (
            connector_status_tool,
            CONNECTOR_STATUS_TOOL,
            "Changes: records a sanitized status audit event. External contact: none. "
            "Omitted options: provider-wide validation and raw provider responses "
            "are not requested.",
            _AUDIT_ONLY,
        ),
        (
            list_provider_capabilities_tool,
            LIST_PROVIDER_CAPABILITIES_TOOL,
            "Changes: none. External contact: none. "
            "Omitted options: unqualified provider actions are not executable.",
            _CLOSED_READ,
        ),
        (
            get_capability_schema_tool,
            GET_CAPABILITY_SCHEMA_TOOL,
            "Changes: none. External contact: none. "
            "Omitted options: discovered but unreviewed schemas are not returned.",
            _CLOSED_READ,
        ),
        (
            disconnect_provider_tool,
            DISCONNECT_PROVIDER_TOOL,
            "Changes: removes usable local provider authorization. External contact: "
            "revokes supported FlowAccount OAuth authorization. Omitted options: "
            "provider credential values and replacement authorization are not accepted.",
            _DISCONNECT_PROVIDER,
        ),
    )
    for function, name, description, tool_annotations in registrations:
        function.__name__ = name
        _register_tool(
            server,
            function,
            name=name,
            description=description,
            annotations=tool_annotations,
        )

    server.set_tool_input_contract(
        START_PROVIDER_CONNECTION_TOOL,
        argument_model=StartProviderConnectionArguments,
        schema=start_provider_connection_input_schema(),
    )
    for name in V1_HOSTED_TOOL_NAMES:
        registered = server._tool_manager.get_tool(name)
        if registered is None:
            raise RuntimeError("mercury_v1_output_schema_invalid")
        server.set_tool_output_contract(
            name,
            schema=_published_output_contract(_success_output_schema(registered)),
        )


__all__ = [
    "CONNECTOR_STATUS_TOOL",
    "AuditRecorder",
    "DISCONNECT_PROVIDER_TOOL",
    "GET_CAPABILITY_SCHEMA_TOOL",
    "GET_MERCURY_CONTEXT_TOOL",
    "LIST_ACCOUNTING_PROVIDERS_TOOL",
    "LIST_PROVIDER_CAPABILITIES_TOOL",
    "LIST_PROVIDER_CONNECTIONS_TOOL",
    "ProviderRuntimeFactory",
    "START_PROVIDER_CONNECTION_TOOL",
    "WorkspaceServiceFactory",
    "configure_v1_tools",
    "connector_status",
    "disconnect_provider",
    "get_capability_schema",
    "get_mercury_context",
    "list_accounting_providers",
    "list_provider_capabilities",
    "list_provider_connections",
    "refresh_generated_provider_tools",
    "refresh_generated_provider_tools_until_stopped",
    "start_provider_connection",
]
