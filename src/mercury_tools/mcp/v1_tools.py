"""Mercury V1 MCP tool registration and request-bound handlers."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
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
from mercury_tools.execution.hosted.models import (
    BatchDocumentCreate,
    DocumentCreateDraft,
    HostedOperation,
    PreparedDocumentPreview,
    PrepareDocumentCreate,
    PreviewState,
    SingleDocumentCreate,
)
from mercury_tools.execution.hosted.read_service import HostedReadService
from mercury_tools.mcp.contracts import LEGACY_HOSTED_TOOL_NAMES, V1_HOSTED_TOOL_NAMES
from mercury_tools.mcp.generated_tools import (
    GeneratedProviderToolPublisher,
    catalog_wire_model,
)
from mercury_tools.mcp.v1_errors import (
    MercuryV1ToolError,
    public_error_code,
    published_error_output_schema,
)
from mercury_tools.mcp.v1_schemas import (
    CAPABILITY_ID_PATTERN,
    SHA256_PATTERN,
    ConfirmDocumentCreateArguments,
    ConnectorStatusData,
    ConnectorStatusOutput,
    DisconnectProviderData,
    DisconnectProviderOutput,
    DocumentCreateItemInput,
    DocumentOperationOutput,
    DocumentOperationSummaryOutput,
    DocumentPreviewOutput,
    DocumentPreviewSummaryOutput,
    FlowAccountConnectionOutput,
    FlowAccountConnectionStartData,
    FlowAccountDisconnectedData,
    FlowAccountProviderOutput,
    FlowAccountRevocationRequiredData,
    GetCapabilitySchemaOutput,
    GetMercuryContextOutput,
    GetOperationStatusArguments,
    HostConnectedEvidenceInput,
    KnowledgeCitationOutput,
    KnowledgeFiltersInput,
    KnowledgeResultOutput,
    ListAccountingProvidersOutput,
    ListProviderCapabilitiesOutput,
    ListProviderConnectionsOutput,
    OperationItemSummaryOutput,
    PeakConnectionOutput,
    PeakConnectionStartData,
    PeakDisconnectData,
    PeakProviderOutput,
    PrepareDocumentCreateArguments,
    PreviewItemSummaryOutput,
    ProviderCapabilityOutput,
    ProviderConnectionOutput,
    RenderDocumentPreviewArguments,
    RetrieveContextPackOutput,
    ReviewedCapabilitySchemaOutput,
    RunAccountingSkillArguments,
    RunAccountingSkillData,
    RunAccountingSkillOutput,
    SearchKnowledgeOutput,
    SkillCapabilityBindingOutput,
    StartProviderConnectionArguments,
    StartProviderConnectionOutput,
    non_nullable_public_schema,
    run_accounting_skill_input_schema,
    start_provider_connection_input_schema,
)
from mercury_tools.mcp.widget_tools import preview_widget_tool_meta
from mercury_tools.providers.base import DispatchCertainty, ProviderSchemaChanged
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
    QualificationGateError,
)
from mercury_tools.rag.models import SearchResult
from mercury_tools.rag.routing import normalize_v1_knowledge_filters
from mercury_tools.skills.catalog import (
    AccountingSkillDefinition,
    SkillReadMapping,
    published_accounting_skill,
    v1_skill_read_capabilities,
)
from mercury_tools.skills.routing import (
    build_published_skill_output,
    published_projection_matches,
    resolve_published_skill_route,
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
SEARCH_KNOWLEDGE_TOOL = "search_knowledge"
RETRIEVE_CONTEXT_PACK_TOOL = "retrieve_context_pack"
RUN_ACCOUNTING_SKILL_TOOL = "run_accounting_skill"
PREPARE_DOCUMENT_CREATE_TOOL = "prepare_document_create"
RENDER_DOCUMENT_PREVIEW_TOOL = "render_document_preview"
CONFIRM_DOCUMENT_CREATE_TOOL = "confirm_document_create"
GET_OPERATION_STATUS_TOOL = "get_operation_status"
DISCONNECT_PROVIDER_TOOL = "disconnect_provider"

WorkspaceServiceFactory = Callable[[], WorkspaceService]
ProviderRuntimeFactory: TypeAlias = Callable[[], Any | Awaitable[Any]]
DocumentRuntimeFactory: TypeAlias = Callable[[], Any | Awaitable[Any]]
AuditRecorder: TypeAlias = Callable[[dict[str, object]], object | Awaitable[object]]
RagStoreFactory: TypeAlias = Callable[[], SupabaseRagStore]
SchemaChangeHandler: TypeAlias = Callable[
    [ProviderMCPQualification, Context | object, DispatchCertainty],
    Awaitable[None],
]
SchemaChangeGuard: TypeAlias = Callable[[ProviderMCPQualification], None]

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
_SKILL_RUN = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
_PREPARE_DOCUMENT_CREATE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_CONFIRM_DOCUMENT_CREATE = ToolAnnotations(
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


def _rag_store() -> SupabaseRagStore:
    return SupabaseRagStore(load_settings())


async def _provider_runtime() -> Any:
    return await asyncio.to_thread(
        build_provider_oauth_production_composition,
        settings=load_settings(),
    )


async def _document_runtime() -> Any:
    from mercury_tools.execution.hosted.production import (
        build_hosted_document_production_composition,
    )

    return await asyncio.to_thread(
        build_hosted_document_production_composition,
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
        self._authority_lock = asyncio.Lock()
        self._publisher = GeneratedProviderToolPublisher(
            server,
            execute=self._execute,
            persist_schema_change=self._persist_schema_change,
            schema_change_handler=self.handle_schema_change,
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
        async with self._authority_lock:
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
            dispatch_guard=self.ensure_dispatch_allowed,
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

    async def handle_schema_change(
        self,
        qualification: ProviderMCPQualification,
        context: Context | object,
        dispatch_certainty: DispatchCertainty,
    ) -> None:
        self._publisher.quarantine_schema_change(
            qualification,
            dispatch_certainty,
        )
        async with self._authority_lock:
            await self._publisher.handle_schema_change(
                qualification,
                context,
                dispatch_certainty,
            )

    def ensure_dispatch_allowed(
        self,
        qualification: ProviderMCPQualification,
    ) -> None:
        try:
            self._publisher.ensure_dispatch_allowed(qualification)
        except MercuryV1ToolError as error:
            if error.code != "capability_unavailable":
                raise
            raise QualificationGateError("capability_unavailable") from None

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
                deadline=deadline,
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
        if qualification.public_output_field_paths is None:
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
    if qualification.public_output_field_paths is None:
        return "unavailable", "capability_unreviewed"
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


def _knowledge_filters_payload(
    filters: KnowledgeFiltersInput | Mapping[str, Any] | None,
) -> dict[str, str]:
    if filters is None:
        return {}
    if isinstance(filters, KnowledgeFiltersInput):
        values = filters.model_dump(mode="json", exclude_none=True)
    elif isinstance(filters, Mapping):
        values = dict(filters)
    else:
        raise ValueError("knowledge_filters_invalid")
    return normalize_v1_knowledge_filters(values)


def _knowledge_result_output(result: SearchResult) -> KnowledgeResultOutput:
    metadata = result.metadata
    citation = result.citation
    if (
        not isinstance(metadata, Mapping)
        or metadata.get("review_status") != "reviewed"
        or not isinstance(citation, Mapping)
    ):
        raise ValueError("insufficient_evidence")
    try:
        return KnowledgeResultOutput(
            chunk_id=UUID(result.chunk_id),
            document_id=UUID(result.document_id),
            document_uri=result.document_uri,
            chunk_uri=result.chunk_uri,
            text=result.text,
            score=result.score,
            jurisdiction=metadata.get("jurisdiction"),
            provider=metadata.get("provider"),
            doc_type=metadata.get("doc_type"),
            review_status="reviewed",
            effective_on=metadata.get("effective_on"),
            citation=KnowledgeCitationOutput(
                source_id=UUID(str(metadata["source_id"])),
                source_title=result.source_title,
                source_uri=result.source_uri,
                source_url=result.source_url,
                heading=citation.get("heading"),
            ),
        )
    except (KeyError, TypeError, ValueError):
        raise ValueError("insufficient_evidence") from None


def _knowledge_result_outputs(
    results: Sequence[SearchResult],
) -> list[KnowledgeResultOutput]:
    if not results:
        raise ValueError("insufficient_evidence")
    return [_knowledge_result_output(result) for result in results]


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


def _knowledge_audit_event(
    *,
    tool_name: Literal["search_knowledge", "retrieve_context_pack"],
    workspace_id: UUID,
    query: str,
    filters: Mapping[str, str],
    result_count: int,
    skill: AccountingSkillDefinition | None = None,
) -> dict[str, object]:
    audit_input: dict[str, object] = {
        "workspace_id_sha256": _sha256_identifier(workspace_id),
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "filter_fields": sorted(filters),
    }
    if skill is not None:
        audit_input.update(
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
        )
    return {
        "tool_name": tool_name,
        "input": audit_input,
        "output_summary": {"result_count": result_count},
        "status": "ok",
        "metadata": {"runtime": "mcp", "surface": "v1"},
    }


def _skill_audit_event(
    *,
    workspace_id: UUID,
    connection_id: UUID | None,
    skill: AccountingSkillDefinition,
    query: str,
    read_outcomes: Sequence[Mapping[str, str]],
    knowledge_count: int,
    host_fact_count: int,
    status: Literal["ok", "error", "cancelled"],
    error_code: str | None = None,
) -> dict[str, object]:
    audit_input: dict[str, object] = {
        "workspace_id_sha256": _sha256_identifier(workspace_id),
        "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
        "skill_id": skill.skill_id,
        "skill_version": skill.skill_version,
    }
    if connection_id is not None:
        audit_input["connection_id_sha256"] = _sha256_identifier(connection_id)
    output_summary: dict[str, object] = {
        "read_outcomes": [dict(outcome) for outcome in read_outcomes],
        "knowledge_count": knowledge_count,
        "host_fact_count": host_fact_count,
    }
    if error_code is not None:
        output_summary["error_code"] = error_code
    return {
        "tool_name": RUN_ACCOUNTING_SKILL_TOOL,
        "input": audit_input,
        "output_summary": output_summary,
        "status": status,
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
        bound_connection = await _store_load_connection(
            runtime,
            membership=membership,
            workspace_id=workspace_id,
            principal=principal,
            connection_id=connection_id,
        )
        if (
            bound_connection.id != connection_id
            or bound_connection.tenant_id != membership.tenant_id
            or bound_connection.workspace_id != workspace_id
            or bound_connection.auth_user_id != principal.subject
        ):
            raise ValueError("provider_connection_required")
        connection = bound_connection.summary()
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


async def search_knowledge(
    context: Context,
    *,
    workspace_id: UUID,
    query: str,
    filters: KnowledgeFiltersInput | Mapping[str, Any] | None = None,
    top_k: int = 8,
    mode: Literal["keyword", "hybrid"] = "hybrid",
    service_factory: WorkspaceServiceFactory = _workspace_service,
    store_factory: RagStoreFactory = _rag_store,
    audit_recorder: AuditRecorder = _record_connector_status_audit,
) -> SearchKnowledgeOutput:
    """Search reviewed knowledge through an exact workspace-bound predicate."""

    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        normalized_filters = _knowledge_filters_payload(filters)
        results = await asyncio.to_thread(
            store_factory().search_workspace_knowledge,
            tenant_id=membership.tenant_id,
            workspace_id=workspace_id,
            auth_user_id=principal.subject,
            query=query,
            filters=normalized_filters,
            top_k=top_k,
            mode=mode,
        )
        response = SearchKnowledgeOutput(
            workspace_id=workspace_id,
            query=query,
            data=_knowledge_result_outputs(results),
            next_allowed_actions=["retrieve_context_pack"],
        )
        await _write_audit(
            audit_recorder,
            _knowledge_audit_event(
                tool_name=SEARCH_KNOWLEDGE_TOOL,
                workspace_id=workspace_id,
                query=query,
                filters=normalized_filters,
                result_count=len(response.data),
            ),
        )
        return response
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None


async def retrieve_context_pack(
    context: Context,
    *,
    workspace_id: UUID,
    query: str,
    skill_id: str | None = None,
    skill_version: str | None = None,
    filters: KnowledgeFiltersInput | Mapping[str, Any] | None = None,
    max_chunks: int = 12,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    store_factory: RagStoreFactory = _rag_store,
    audit_recorder: AuditRecorder = _record_connector_status_audit,
) -> RetrieveContextPackOutput:
    """Retrieve cited context without allowing Skill or RAG authority expansion."""

    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        if (skill_id is None) != (skill_version is None):
            raise ValueError("insufficient_evidence")
        store = store_factory()
        normalized_filters = _knowledge_filters_payload(filters)
        skill: AccountingSkillDefinition | None = None
        if skill_id is not None and skill_version is not None:
            skill = published_accounting_skill(skill_id, skill_version)
            if skill is None:
                raise ValueError("insufficient_evidence")
            projection = await asyncio.to_thread(
                store.get_published_skill_projection,
                tenant_id=membership.tenant_id,
                workspace_id=workspace_id,
                auth_user_id=principal.subject,
                skill_id=skill.skill_id,
                skill_version=skill.skill_version,
            )
            if not published_projection_matches(skill, projection):
                raise ValueError("insufficient_evidence")
            normalized_filters.update(dict(skill.knowledge_filters))
        results = await asyncio.to_thread(
            store.search_workspace_knowledge,
            tenant_id=membership.tenant_id,
            workspace_id=workspace_id,
            auth_user_id=principal.subject,
            query=query,
            filters=normalized_filters,
            top_k=max_chunks,
            mode="hybrid",
        )
        response = RetrieveContextPackOutput(
            workspace_id=workspace_id,
            query=query,
            skill_id=skill_id,
            skill_version=skill_version,
            data=_knowledge_result_outputs(results),
            next_allowed_actions=["run_accounting_skill"] if skill_id else [],
        )
        await _write_audit(
            audit_recorder,
            _knowledge_audit_event(
                tool_name=RETRIEVE_CONTEXT_PACK_TOOL,
                workspace_id=workspace_id,
                query=query,
                filters=normalized_filters,
                result_count=len(response.data),
                skill=skill,
            ),
        )
        return response
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None


@dataclass(frozen=True, slots=True)
class _EnabledSkillReadBinding:
    skill_capability: str
    qualification: ProviderMCPQualification


async def _enabled_skill_read_bindings(
    runtime: Any,
    *,
    skill: AccountingSkillDefinition,
    membership: WorkspaceMembership,
    workspace_id: UUID,
    principal: MercuryPrincipal,
    connection_id: UUID,
) -> tuple[tuple[_EnabledSkillReadBinding, ...], tuple[str, ...]]:
    connection = await _store_load_connection(
        runtime,
        membership=membership,
        workspace_id=workspace_id,
        principal=principal,
        connection_id=connection_id,
    )
    if (
        connection.id != connection_id
        or connection.tenant_id != membership.tenant_id
        or connection.workspace_id != workspace_id
        or connection.auth_user_id != principal.subject
    ):
        raise ValueError("provider_connection_required")
    qualifications = tuple(
        item
        for item in await _catalog_qualifications(runtime)
        if item.provider == connection.provider.value and item.environment == connection.environment
    )
    bindings: list[_EnabledSkillReadBinding] = []
    enabled_skill_capabilities: list[str] = []
    declared_capabilities = dict.fromkeys(
        (*skill.required_capabilities, *skill.optional_capabilities)
    )
    for skill_capability in declared_capabilities:
        catalog_capabilities = frozenset(v1_skill_read_capabilities(skill_capability))
        enabled = []
        for qualification in qualifications:
            if qualification.normalized_capability not in catalog_capabilities:
                continue
            resolution = await _resolve_qualification(
                runtime,
                connection=connection,
                qualification=qualification,
            )
            if (
                resolution.status == "enabled"
                and resolution.qualification is not None
                and resolution.qualification.public_output_field_paths is not None
                and resolution.qualification.normalized_capability
                == qualification.normalized_capability
                and resolution.qualification.normalized_capability in catalog_capabilities
            ):
                enabled.append(resolution.qualification)
        if len(enabled) == 1:
            enabled_skill_capabilities.append(skill_capability)
            bindings.append(
                _EnabledSkillReadBinding(
                    skill_capability=skill_capability,
                    qualification=enabled[0],
                )
            )
    return tuple(bindings), tuple(enabled_skill_capabilities)


async def _enabled_skill_capability_bindings(
    runtime: Any,
    *,
    skill: AccountingSkillDefinition,
    membership: WorkspaceMembership,
    workspace_id: UUID,
    principal: MercuryPrincipal,
    connection_id: UUID,
) -> tuple[tuple[SkillCapabilityBindingOutput, ...], tuple[str, ...]]:
    bindings, enabled_capabilities = await _enabled_skill_read_bindings(
        runtime,
        skill=skill,
        membership=membership,
        workspace_id=workspace_id,
        principal=principal,
        connection_id=connection_id,
    )
    return (
        tuple(
            SkillCapabilityBindingOutput(
                skill_capability=binding.skill_capability,
                capability_id=binding.qualification.normalized_capability,
                capability_version=binding.qualification.capability_version_sha256,
            )
            for binding in bindings
        ),
        enabled_capabilities,
    )


def _skill_read_inputs(
    mapping: SkillReadMapping,
    qualification: ProviderMCPQualification,
    arguments: RunAccountingSkillArguments,
) -> BaseModel:
    serialized_arguments = arguments.model_dump(mode="json")
    if mapping.request_kind == "empty":
        payload: dict[str, object] = {}
    elif mapping.request_kind == "invoice_list":
        payload = {
            field: serialized_arguments[field]
            for field in ("period_start", "period_end", "month")
            if serialized_arguments[field] is not None
        }
    elif mapping.request_kind == "invoice_get":
        if not arguments.document_ids:
            raise ValueError("insufficient_evidence")
        payload = {"document_id": arguments.document_ids[0]}
    else:
        raise ValueError("insufficient_evidence")
    try:
        return catalog_wire_model(
            qualification.input_schema,
            kind="input",
        ).model_validate(payload)
    except ValueError:
        raise ValueError("insufficient_evidence") from None


def _skill_host_facts(
    evidence: Sequence[HostConnectedEvidenceInput],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "source": item.source,
            "evidence_type": item.evidence_type,
            "fact": fact.model_dump(mode="json"),
        }
        for item in evidence
        for fact in item.facts
    )


async def run_accounting_skill(
    context: Context,
    *,
    arguments: RunAccountingSkillArguments,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    store_factory: RagStoreFactory = _rag_store,
    runtime_factory: ProviderRuntimeFactory | None = None,
    audit_recorder: AuditRecorder = _record_connector_status_audit,
    schema_change_handler: SchemaChangeHandler | None = None,
    schema_change_guard: SchemaChangeGuard | None = None,
) -> RunAccountingSkillOutput:
    """Execute one exact published Skill from qualified reads and reviewed evidence."""

    runtime: Any | None = None
    skill: AccountingSkillDefinition | None = None
    read_outcomes: list[dict[str, str]] = []
    knowledge_count = 0
    host_facts = _skill_host_facts(arguments.host_evidence)
    audit_eligible = False
    try:
        principal, membership = await _require_workspace(
            context,
            workspace_id=arguments.workspace_id,
            service_factory=service_factory,
        )
        skill = published_accounting_skill(arguments.skill_id, arguments.skill_version)
        if skill is None:
            raise ValueError("insufficient_evidence")
        audit_eligible = True
        store = store_factory()
        projection = await asyncio.to_thread(
            store.get_published_skill_projection,
            tenant_id=membership.tenant_id,
            workspace_id=arguments.workspace_id,
            auth_user_id=principal.subject,
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
        )
        if not published_projection_matches(skill, projection):
            raise ValueError("insufficient_evidence")

        read_bindings: tuple[_EnabledSkillReadBinding, ...] = ()
        enabled_capabilities: tuple[str, ...] = ()
        if arguments.connection_id is not None:
            runtime = await _runtime_from(runtime_factory)
            read_bindings, enabled_capabilities = await _enabled_skill_read_bindings(
                runtime,
                skill=skill,
                membership=membership,
                workspace_id=arguments.workspace_id,
                principal=principal,
                connection_id=arguments.connection_id,
            )

        knowledge_results: Sequence[SearchResult] = ()
        if {"knowledge_source", "citation"}.intersection(skill.evidence_requirements):
            knowledge_results = await asyncio.to_thread(
                store.search_workspace_knowledge,
                tenant_id=membership.tenant_id,
                workspace_id=arguments.workspace_id,
                auth_user_id=principal.subject,
                query=arguments.query,
                filters=dict(skill.knowledge_filters),
                top_k=12,
                mode="hybrid",
            )
        knowledge = _knowledge_result_outputs(knowledge_results) if knowledge_results else []
        knowledge_count = len(knowledge)

        exact_bindings = {binding.skill_capability: binding for binding in read_bindings}
        required_reads: list[tuple[SkillReadMapping, _EnabledSkillReadBinding]] = []
        for mapping in skill.read_mappings:
            binding = exact_bindings.get(mapping.skill_capability)
            if (
                binding is None
                or binding.qualification.normalized_capability != mapping.capability_id
            ):
                read_outcomes.append(
                    {
                        "capability_id": mapping.capability_id,
                        "status": "missing",
                    }
                )
                raise ValueError("insufficient_evidence")
            required_reads.append((mapping, binding))

        preflight = resolve_published_skill_route(
            skill,
            projection=projection,
            enabled_capabilities=enabled_capabilities,
            business_fact_count=len(host_facts) + len(required_reads),
            knowledge_source_count=len({item.citation.source_id for item in knowledge}),
            citation_count=len(knowledge),
        )
        if preflight["status"] != "ready":
            raise ValueError("insufficient_evidence")

        provider_results: list[tuple[str, Mapping[str, object]]] = []
        if required_reads:
            if runtime is None or arguments.connection_id is None:
                raise ValueError("insufficient_evidence")

            async def resolve_membership(
                read_principal: MercuryPrincipal,
                read_workspace_id: UUID,
            ) -> WorkspaceMembership:
                if (
                    read_principal.subject != principal.subject
                    or read_workspace_id != arguments.workspace_id
                ):
                    raise ValueError("workspace_access_denied")
                return membership

            read_service = HostedReadService(
                runtime_factory=lambda: runtime,
                membership_resolver=resolve_membership,
                audit_recorder=audit_recorder,
                dispatch_guard=schema_change_guard,
                close_runtime=False,
            )
            for mapping, binding in required_reads:
                qualification = binding.qualification
                outcome = {
                    "capability_id": qualification.normalized_capability,
                    "capability_version": qualification.capability_version_sha256,
                    "status": "error",
                }
                try:
                    if schema_change_guard is not None:
                        schema_change_guard(qualification)
                    inputs = _skill_read_inputs(mapping, qualification, arguments)
                    result = await read_service.execute(
                        principal,
                        arguments.workspace_id,
                        arguments.connection_id,
                        qualification.normalized_capability,
                        qualification.capability_version_sha256,
                        inputs,
                    )
                except asyncio.CancelledError:
                    outcome["status"] = "cancelled"
                    read_outcomes.append(outcome)
                    raise
                except ProviderSchemaChanged as error:
                    outcome["error_code"] = public_error_code(error)
                    outcome["dispatch_certainty"] = error.dispatch_certainty.value
                    read_outcomes.append(outcome)
                    if schema_change_handler is None:
                        raise MercuryV1ToolError("capability_unavailable") from None
                    await schema_change_handler(
                        qualification,
                        context,
                        error.dispatch_certainty,
                    )
                    raise
                except Exception as error:
                    outcome["error_code"] = public_error_code(error)
                    read_outcomes.append(outcome)
                    raise
                if (
                    result.capability_id != qualification.normalized_capability
                    or result.capability_version != qualification.capability_version_sha256
                ):
                    outcome["error_code"] = "capability_unavailable"
                    read_outcomes.append(outcome)
                    raise ValueError("capability_unavailable")
                outcome["status"] = "ok"
                read_outcomes.append(outcome)
                provider_results.append((mapping.result_fact_name, result.data))

        route = resolve_published_skill_route(
            skill,
            projection=projection,
            enabled_capabilities=enabled_capabilities,
            business_fact_count=len(host_facts) + len(provider_results),
            knowledge_source_count=len({item.citation.source_id for item in knowledge}),
            citation_count=len(knowledge),
        )
        if route["status"] != "ready":
            raise ValueError("insufficient_evidence")
        published_output = build_published_skill_output(
            skill,
            host_facts=host_facts,
            provider_results=provider_results,
            citations=[item.citation.source_uri for item in knowledge],
        )
        response = RunAccountingSkillOutput(
            workspace_id=arguments.workspace_id,
            connection_id=arguments.connection_id,
            skill_id=skill.skill_id,
            skill_version=skill.skill_version,
            data=RunAccountingSkillData.model_validate(published_output),
            accountant_review_points=[],
            next_allowed_actions=[],
        )
        await _write_audit(
            audit_recorder,
            _skill_audit_event(
                workspace_id=arguments.workspace_id,
                connection_id=arguments.connection_id,
                skill=skill,
                query=arguments.query,
                read_outcomes=read_outcomes,
                knowledge_count=knowledge_count,
                host_fact_count=len(host_facts),
                status="ok",
            ),
        )
        return response
    except asyncio.CancelledError:
        if skill is not None and audit_eligible:
            await _write_audit(
                audit_recorder,
                _skill_audit_event(
                    workspace_id=arguments.workspace_id,
                    connection_id=arguments.connection_id,
                    skill=skill,
                    query=arguments.query,
                    read_outcomes=read_outcomes,
                    knowledge_count=knowledge_count,
                    host_fact_count=len(host_facts),
                    status="cancelled",
                ),
            )
        raise
    except Exception as error:
        error_code = public_error_code(error)
        if skill is not None and audit_eligible:
            await _write_audit(
                audit_recorder,
                _skill_audit_event(
                    workspace_id=arguments.workspace_id,
                    connection_id=arguments.connection_id,
                    skill=skill,
                    query=arguments.query,
                    read_outcomes=read_outcomes,
                    knowledge_count=knowledge_count,
                    host_fact_count=len(host_facts),
                    status="error",
                    error_code=error_code,
                ),
            )
        raise MercuryV1ToolError(error_code) from None
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


async def _document_runtime_from(factory: DocumentRuntimeFactory | None) -> Any:
    return await _await_value((factory or _document_runtime)())


def _document_request(
    *,
    mode: Literal["single", "batch"],
    documents: Sequence[DocumentCreateItemInput],
) -> PrepareDocumentCreate:
    drafts: list[DocumentCreateDraft] = []
    for item in documents:
        try:
            provider_arguments = json.loads(item.provider_arguments_json)
        except (TypeError, ValueError):
            raise ValueError("validation_failed") from None
        if not isinstance(provider_arguments, dict):
            raise ValueError("validation_failed")
        drafts.append(
            DocumentCreateDraft(
                client_item_id=item.client_item_id,
                provider_arguments=provider_arguments,
                warnings=tuple(item.warnings),
                accountant_review_points=tuple(item.accountant_review_points),
            )
        )
    if mode == "single":
        if len(drafts) != 1:
            raise ValueError("validation_failed")
        return SingleDocumentCreate(mode="single", document=drafts[0])
    return BatchDocumentCreate(mode="batch", documents=tuple(drafts))


def _preview_output(
    preview: PreparedDocumentPreview,
    *,
    rendered: bool,
) -> DocumentPreviewOutput:
    checked = PreparedDocumentPreview.model_validate(preview)
    next_actions = list(checked.next_allowed_actions)
    if rendered and checked.status is PreviewState.AWAITING_CONFIRMATION:
        next_actions = [CONFIRM_DOCUMENT_CREATE_TOOL]
    return DocumentPreviewOutput(
        workspace_id=checked.workspace_id,
        connection_id=checked.connection_id,
        preview_id=checked.preview_id,
        state_version=checked.state_version,
        data=DocumentPreviewSummaryOutput(
            preview_state=checked.status.value,
            provider=checked.provider,
            environment=checked.environment,
            company_display_name=checked.company_display_name,
            capability_id=checked.capability_id,
            capability_version=checked.capability_version,
            document_count=checked.document_count,
            currency=checked.currency,
            subtotal=str(checked.subtotal),
            discount_total=str(checked.discount_total),
            vat_total=str(checked.vat_total),
            withholding_tax_total=str(checked.withholding_tax_total),
            grand_total=str(checked.grand_total),
            warning_count=checked.warning_count,
            warnings=list(checked.warnings),
            accountant_review_points=list(checked.accountant_review_points),
            items=[
                PreviewItemSummaryOutput(
                    client_item_id=item.client_item_id,
                    document_type=item.document_type,
                    counterparty_display=item.counterparty_display,
                    issue_date=item.issue_date,
                    due_date=item.due_date,
                    currency=item.financials.currency,
                    grand_total=str(item.financials.grand_total),
                    warnings=list(item.warnings),
                    accountant_review_points=list(item.accountant_review_points),
                )
                for item in checked.items
            ],
            expires_at=checked.expires_at,
        ),
        next_allowed_actions=next_actions,
    )


def _operation_output(operation: HostedOperation) -> DocumentOperationOutput:
    checked = HostedOperation.model_validate(operation)
    next_actions = (
        [GET_OPERATION_STATUS_TOOL]
        if checked.state.value in {"dispatching", "outcome_unknown"}
        else []
    )
    return DocumentOperationOutput(
        workspace_id=checked.workspace_id,
        connection_id=checked.connection_id,
        operation_id=checked.operation_id,
        state_version=checked.state_version,
        data=DocumentOperationSummaryOutput(
            preview_id=checked.preview_id,
            provider=checked.provider,
            environment=checked.environment,
            capability_id=checked.capability_id,
            capability_version=checked.capability_version,
            operation_state=checked.state.value,
            items=[
                OperationItemSummaryOutput(
                    client_item_id=item.client_item_id,
                    state=item.state.value,
                )
                for item in checked.items
            ],
            updated_at=checked.updated_at,
        ),
        next_allowed_actions=next_actions,
    )


async def prepare_document_create(
    context: Context,
    *,
    workspace_id: UUID,
    connection_id: UUID,
    capability_id: str,
    capability_version: str,
    mode: Literal["single", "batch"],
    documents: Sequence[DocumentCreateItemInput],
    service_factory: WorkspaceServiceFactory = _workspace_service,
    document_runtime_factory: DocumentRuntimeFactory | None = None,
) -> DocumentPreviewOutput:
    runtime: Any | None = None
    try:
        principal, _membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        request = _document_request(mode=mode, documents=documents)
        runtime = await _document_runtime_from(document_runtime_factory)
        result = await _await_value(
            runtime.prepare_document_create(
                principal,
                workspace_id,
                connection_id,
                capability_id,
                capability_version,
                request,
            )
        )
        return _preview_output(result, rendered=False)
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def render_document_preview(
    context: Context,
    *,
    workspace_id: UUID,
    preview_id: UUID,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    document_runtime_factory: DocumentRuntimeFactory | None = None,
) -> DocumentPreviewOutput:
    runtime: Any | None = None
    try:
        principal, _membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _document_runtime_from(document_runtime_factory)
        result = await _await_value(
            runtime.render_document_preview(principal, workspace_id, preview_id)
        )
        return _preview_output(result, rendered=True)
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def confirm_document_create(
    context: Context,
    *,
    workspace_id: UUID,
    preview_id: UUID,
    state_version: int,
    confirmation: Literal["CONFIRM_CREATE"],
    service_factory: WorkspaceServiceFactory = _workspace_service,
    document_runtime_factory: DocumentRuntimeFactory | None = None,
) -> DocumentOperationOutput:
    if confirmation != "CONFIRM_CREATE":
        raise MercuryV1ToolError("confirmation_required")
    runtime: Any | None = None
    try:
        principal, _membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _document_runtime_from(document_runtime_factory)
        result = await _await_value(
            runtime.confirm_document_create(
                principal,
                workspace_id,
                preview_id,
                state_version,
            )
        )
        return _operation_output(result)
    except Exception as error:
        raise MercuryV1ToolError(public_error_code(error)) from None
    finally:
        if runtime is not None:
            await _close_runtime(runtime)


async def get_operation_status(
    context: Context,
    *,
    workspace_id: UUID,
    operation_id: UUID,
    service_factory: WorkspaceServiceFactory = _workspace_service,
    document_runtime_factory: DocumentRuntimeFactory | None = None,
) -> DocumentOperationOutput:
    runtime: Any | None = None
    try:
        principal, _membership = await _require_workspace(
            context,
            workspace_id=workspace_id,
            service_factory=service_factory,
        )
        runtime = await _document_runtime_from(document_runtime_factory)
        result = await _await_value(
            runtime.get_operation_status(principal, workspace_id, operation_id)
        )
        return _operation_output(result)
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
    meta: Mapping[str, Any] | None = None,
) -> None:
    if server._tool_manager.get_tool(name) is None:
        server.add_tool(
            function,
            name=name,
            description=description,
            annotations=annotations,
            meta={**_V1_TOOL_META, **dict(meta or {})},
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
    return non_nullable_public_schema(
        {
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
    )


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
    store_factory: RagStoreFactory = _rag_store,
    document_runtime_factory: DocumentRuntimeFactory | None = None,
) -> None:
    """Register the stable V1 surface and remove legacy tools when V1 is enabled."""

    with _V1_CONFIGURATION_LOCK:
        _configure_v1_tools(
            server,
            enabled=enabled,
            service_factory=service_factory,
            runtime_factory=runtime_factory,
            store_factory=store_factory,
            document_runtime_factory=document_runtime_factory,
        )


def _configure_v1_tools(
    server: FastMCP,
    *,
    enabled: bool,
    service_factory: WorkspaceServiceFactory,
    runtime_factory: ProviderRuntimeFactory | None,
    store_factory: RagStoreFactory,
    document_runtime_factory: DocumentRuntimeFactory | None,
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

    async def search_knowledge_tool(
        context: Context,
        workspace_id: UUID,
        query: Annotated[str, Field(min_length=1, max_length=2_000)],
        filters: KnowledgeFiltersInput = None,  # type: ignore[assignment]
        top_k: Annotated[int, Field(ge=1, le=20)] = 8,
        mode: Literal["keyword", "hybrid"] = "hybrid",
    ) -> SearchKnowledgeOutput:
        return await search_knowledge(
            context,
            workspace_id=workspace_id,
            query=query,
            filters=filters,
            top_k=top_k,
            mode=mode,
            service_factory=service_factory,
            store_factory=store_factory,
        )

    async def retrieve_context_pack_tool(
        context: Context,
        workspace_id: UUID,
        query: Annotated[str, Field(min_length=1, max_length=2_000)],
        skill_id: Annotated[str | None, Field(max_length=200)] = None,
        skill_version: Annotated[str | None, Field(max_length=64)] = None,
        filters: KnowledgeFiltersInput = None,  # type: ignore[assignment]
        max_chunks: Annotated[int, Field(ge=1, le=20)] = 12,
    ) -> RetrieveContextPackOutput:
        return await retrieve_context_pack(
            context,
            workspace_id=workspace_id,
            query=query,
            skill_id=skill_id,
            skill_version=skill_version,
            filters=filters,
            max_chunks=max_chunks,
            service_factory=service_factory,
            store_factory=store_factory,
        )

    async def run_accounting_skill_tool(
        context: Context,
        workspace_id: UUID,
        skill_id: str,
        skill_version: str,
        query: str,
        connection_id: UUID | None = None,
        connector_id: str | None = None,
        connection_mode: str | None = None,
        environment: str | None = None,
        company_name: str | None = None,
        notes: str | None = None,
        host_evidence: list[HostConnectedEvidenceInput] | None = None,
        period_start: date | None = None,
        period_end: date | None = None,
        month: str | None = None,
        document_ids: list[str] | None = None,
        objective: str | None = None,
        source_reference: str | None = None,
        marketplace_source: str | None = None,
    ) -> RunAccountingSkillOutput:
        values: dict[str, Any] = {
            "workspace_id": workspace_id,
            "skill_id": skill_id,
            "skill_version": skill_version,
            "query": query,
        }
        optional_values = {
            "connection_id": connection_id,
            "connector_id": connector_id,
            "connection_mode": connection_mode,
            "environment": environment,
            "company_name": company_name,
            "notes": notes,
            "period_start": period_start,
            "period_end": period_end,
            "month": month,
            "objective": objective,
            "source_reference": source_reference,
            "marketplace_source": marketplace_source,
        }
        values.update(
            {field: value for field, value in optional_values.items() if value is not None}
        )
        if host_evidence:
            values["host_evidence"] = host_evidence
        if document_ids:
            values["document_ids"] = document_ids
        try:
            projection_server = context.fastmcp
        except (AttributeError, ValueError):
            projection_server = server
        if not isinstance(projection_server, FastMCP):
            projection_server = server
        projection = getattr(
            projection_server,
            "_mercury_v1_generated_provider_projection",
            None,
        )
        return await run_accounting_skill(
            context,
            arguments=RunAccountingSkillArguments.model_validate(values),
            service_factory=service_factory,
            store_factory=store_factory,
            runtime_factory=runtime_factory,
            schema_change_handler=(
                projection.handle_schema_change
                if isinstance(projection, GeneratedProviderToolProjection)
                else None
            ),
            schema_change_guard=(
                projection.ensure_dispatch_allowed
                if isinstance(projection, GeneratedProviderToolProjection)
                else None
            ),
        )

    async def prepare_document_create_tool(
        context: Context,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: Annotated[
            str,
            Field(min_length=3, max_length=200, pattern=CAPABILITY_ID_PATTERN),
        ],
        capability_version: Annotated[str, Field(pattern=SHA256_PATTERN)],
        mode: Literal["single", "batch"],
        documents: Annotated[list[DocumentCreateItemInput], Field(min_length=1, max_length=25)],
    ) -> DocumentPreviewOutput:
        return await prepare_document_create(
            context,
            workspace_id=workspace_id,
            connection_id=connection_id,
            capability_id=capability_id,
            capability_version=capability_version,
            mode=mode,
            documents=documents,
            service_factory=service_factory,
            document_runtime_factory=document_runtime_factory,
        )

    async def render_document_preview_tool(
        context: Context,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> DocumentPreviewOutput:
        return await render_document_preview(
            context,
            workspace_id=workspace_id,
            preview_id=preview_id,
            service_factory=service_factory,
            document_runtime_factory=document_runtime_factory,
        )

    async def confirm_document_create_tool(
        context: Context,
        workspace_id: UUID,
        preview_id: UUID,
        state_version: Annotated[int, Field(ge=1)],
        confirmation: Literal["CONFIRM_CREATE"],
    ) -> DocumentOperationOutput:
        return await confirm_document_create(
            context,
            workspace_id=workspace_id,
            preview_id=preview_id,
            state_version=state_version,
            confirmation=confirmation,
            service_factory=service_factory,
            document_runtime_factory=document_runtime_factory,
        )

    async def get_operation_status_tool(
        context: Context,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> DocumentOperationOutput:
        return await get_operation_status(
            context,
            workspace_id=workspace_id,
            operation_id=operation_id,
            service_factory=service_factory,
            document_runtime_factory=document_runtime_factory,
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
            search_knowledge_tool,
            SEARCH_KNOWLEDGE_TOOL,
            "Changes: records a sanitized knowledge audit. External contact: "
            "Mercury's PostgreSQL knowledge store only. Omitted options: vector-only "
            "retrieval and unknown filters are not accepted.",
            _AUDIT_ONLY,
        ),
        (
            retrieve_context_pack_tool,
            RETRIEVE_CONTEXT_PACK_TOOL,
            "Changes: records a sanitized context audit. External contact: "
            "Mercury's PostgreSQL knowledge store only. Omitted options: unpublished "
            "Skill versions and uncited context are not returned.",
            _AUDIT_ONLY,
        ),
        (
            run_accounting_skill_tool,
            RUN_ACCOUNTING_SKILL_TOOL,
            "Changes: validates evidence and records a sanitized Skill audit. "
            "External contact: qualified provider reads may be used. Omitted options: "
            "provider create, capability discovery, qualification, and enablement.",
            _SKILL_RUN,
        ),
        (
            prepare_document_create_tool,
            PREPARE_DOCUMENT_CREATE_TOOL,
            "Changes: validates and stores one immutable document-create preview. "
            "External contact: none. Omitted options: credentials and provider dispatch.",
            _PREPARE_DOCUMENT_CREATE,
        ),
        (
            render_document_preview_tool,
            RENDER_DOCUMENT_PREVIEW_TOOL,
            "Changes: none. External contact: none. Omitted options: provider payloads and "
            "provider dispatch.",
            _CLOSED_READ,
        ),
        (
            confirm_document_create_tool,
            CONFIRM_DOCUMENT_CREATE_TOOL,
            "Changes: confirms and dispatches the exact immutable preview once. External "
            "contact: the qualified provider create operation. Omitted options: replacement "
            "payloads, arbitrary provider routes, and automatic unknown-outcome retry.",
            _CONFIRM_DOCUMENT_CREATE,
        ),
        (
            get_operation_status_tool,
            GET_OPERATION_STATUS_TOOL,
            "Changes: records a sanitized operation-status audit. External contact: none. "
            "Omitted options: provider payloads and replay controls.",
            _AUDIT_ONLY,
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
            meta=(preview_widget_tool_meta() if name == RENDER_DOCUMENT_PREVIEW_TOOL else None),
        )

    server.set_tool_input_contract(
        START_PROVIDER_CONNECTION_TOOL,
        argument_model=StartProviderConnectionArguments,
        schema=start_provider_connection_input_schema(),
    )
    server.set_tool_input_contract(
        RUN_ACCOUNTING_SKILL_TOOL,
        argument_model=RunAccountingSkillArguments,
        schema=run_accounting_skill_input_schema(),
    )
    for name, argument_model in (
        (PREPARE_DOCUMENT_CREATE_TOOL, PrepareDocumentCreateArguments),
        (RENDER_DOCUMENT_PREVIEW_TOOL, RenderDocumentPreviewArguments),
        (CONFIRM_DOCUMENT_CREATE_TOOL, ConfirmDocumentCreateArguments),
        (GET_OPERATION_STATUS_TOOL, GetOperationStatusArguments),
    ):
        server.set_tool_input_contract(
            name,
            argument_model=argument_model,
            schema=non_nullable_public_schema(argument_model.model_json_schema()),
        )
    for name in V1_HOSTED_TOOL_NAMES:
        registered = server._tool_manager.get_tool(name)
        if registered is None:
            raise RuntimeError("mercury_v1_output_schema_invalid")
        server.set_tool_input_contract(
            name,
            argument_model=registered.fn_metadata.arg_model,
            schema=non_nullable_public_schema(registered.parameters),
        )
        server.set_tool_output_contract(
            name,
            schema=_published_output_contract(_success_output_schema(registered)),
        )


__all__ = [
    "CONNECTOR_STATUS_TOOL",
    "CONFIRM_DOCUMENT_CREATE_TOOL",
    "AuditRecorder",
    "DISCONNECT_PROVIDER_TOOL",
    "GET_CAPABILITY_SCHEMA_TOOL",
    "GET_OPERATION_STATUS_TOOL",
    "GET_MERCURY_CONTEXT_TOOL",
    "LIST_ACCOUNTING_PROVIDERS_TOOL",
    "LIST_PROVIDER_CAPABILITIES_TOOL",
    "LIST_PROVIDER_CONNECTIONS_TOOL",
    "PREPARE_DOCUMENT_CREATE_TOOL",
    "ProviderRuntimeFactory",
    "DocumentRuntimeFactory",
    "RETRIEVE_CONTEXT_PACK_TOOL",
    "RENDER_DOCUMENT_PREVIEW_TOOL",
    "RUN_ACCOUNTING_SKILL_TOOL",
    "RagStoreFactory",
    "SEARCH_KNOWLEDGE_TOOL",
    "START_PROVIDER_CONNECTION_TOOL",
    "WorkspaceServiceFactory",
    "configure_v1_tools",
    "confirm_document_create",
    "connector_status",
    "disconnect_provider",
    "get_capability_schema",
    "get_operation_status",
    "get_mercury_context",
    "list_accounting_providers",
    "list_provider_capabilities",
    "list_provider_connections",
    "prepare_document_create",
    "refresh_generated_provider_tools",
    "refresh_generated_provider_tools_until_stopped",
    "retrieve_context_pack",
    "render_document_preview",
    "run_accounting_skill",
    "search_knowledge",
    "start_provider_connection",
]
