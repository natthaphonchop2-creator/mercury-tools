"""Render/Supabase production composition for Mercury V1 document creates."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeAlias
from uuid import UUID

import httpx
from pydantic import BaseModel

from mercury_tools.auth.middleware import current_mercury_access_token
from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.config import Settings
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.mcp.generated_tools import catalog_wire_model
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderOperationClass,
    ProviderOutcomeUnknown,
    ProviderStatusClass,
    ProviderUnavailable,
)
from mercury_tools.providers.models import ProviderConnection
from mercury_tools.providers.production import (
    ProviderOAuthProductionComposition,
    build_provider_oauth_production_composition,
)
from mercury_tools.providers.streamable_mcp import (
    ProviderOperationDeadline,
    provider_operation_deadline,
)
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole
from mercury_tools.workspaces.service import WorkspaceService

from .batch_service import BatchOperationService
from .models import HostedOperation, PreparedDocumentPreview, PrepareDocumentCreate
from .operation_service import DocumentCreateConfirmation
from .preview_service import HostedPreviewService
from .projectors import DocumentProjectorRegistry, ReviewedInvoiceProjector
from .store import HostedPayloadVault, SupabaseHostedPreviewStore

MembershipResolver: TypeAlias = Callable[
    [MercuryPrincipal, UUID], WorkspaceMembership | Awaitable[WorkspaceMembership]
]

_CREATE_OPERATION_SECONDS = 60
_PROVIDER_IDENTIFIER_KEYS = frozenset(
    {"id", "documentid", "document_id", "recordid", "record_id"}
)
_SAFE_PROVIDER_IDENTIFIER = re.compile(r"^[^\s@]{1,256}$")
_ROOT_FIELDS = frozenset(
    {
        "reference",
        "counterparty_name",
        "issue_date",
        "due_date",
        "currency",
        "lines",
        "subtotal",
        "discount_total",
        "vat_total",
        "withholding_tax_total",
        "grand_total",
    }
)
_LINE_FIELDS = frozenset(
    {
        "description",
        "currency",
        "quantity",
        "unit_price",
        "discount_amount",
        "vat_rate",
        "vat_amount",
        "withholding_rate",
        "withholding_amount",
        "line_total",
    }
)


async def _await_value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _field_set(value: object) -> frozenset[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        return None
    return frozenset(value)


def _closed_object_fields(schema: object) -> tuple[frozenset[str], Mapping[str, Any]] | None:
    if not isinstance(schema, Mapping):
        return None
    properties = schema.get("properties")
    required = _field_set(schema.get("required"))
    if (
        schema.get("type") != "object"
        or schema.get("additionalProperties") is not False
        or not isinstance(properties, Mapping)
        or required is None
        or required != frozenset(properties)
    ):
        return None
    return required, properties


def supports_reviewed_invoice_projection(schema: Mapping[str, Any]) -> bool:
    """Accept only the exact canonical invoice shape reviewed by the projector."""

    root = _closed_object_fields(schema)
    if root is None or root[0] != _ROOT_FIELDS:
        return False
    lines = root[1].get("lines")
    if not isinstance(lines, Mapping) or lines.get("type") != "array":
        return False
    item = _closed_object_fields(lines.get("items"))
    return item is not None and item[0] == _LINE_FIELDS


def _reviewed_projectors(
    qualifications: tuple[ProviderMCPQualification, ...],
) -> DocumentProjectorRegistry:
    projectors = []
    for qualification in qualifications:
        if (
            qualification.qualification_state is not QualificationState.ENABLED
            or qualification.normalized_capability != "documents.invoice.create"
            or not supports_reviewed_invoice_projection(qualification.input_schema)
        ):
            continue
        projectors.append(
            ReviewedInvoiceProjector(
                projector_id=f"{qualification.provider}.invoice.v1",
                projector_version=qualification.capability_version_sha256,
                provider=qualification.provider,
                environment=qualification.environment,
                provider_tool_name=qualification.provider_tool_name,
                capability_id=qualification.normalized_capability,
                capability_version=qualification.capability_version_sha256,
                schema_hash=qualification.schema_hash,
                currency_minor_units={"THB": 2},
            )
        )
    return DocumentProjectorRegistry(tuple(projectors))


def extract_provider_identifier(value: object) -> str | None:
    """Return one unambiguous non-contact provider document identifier."""

    found: list[str] = []

    def visit(current: object) -> None:
        if isinstance(current, Mapping):
            for key, item in current.items():
                normalized = str(key).casefold().replace("-", "_")
                compact = normalized.replace("_", "")
                if normalized in _PROVIDER_IDENTIFIER_KEYS or compact in _PROVIDER_IDENTIFIER_KEYS:
                    if isinstance(item, str) and _SAFE_PROVIDER_IDENTIFIER.fullmatch(item):
                        found.append(item)
                elif isinstance(item, Mapping | list | tuple):
                    visit(item)
        elif isinstance(current, list | tuple):
            for item in current:
                visit(item)

    visit(value)
    unique = tuple(dict.fromkeys(found))
    return unique[0] if len(unique) == 1 else None


class HostedProviderCreateDispatcher:
    """Dispatch exactly one qualified create through the server-owned registry."""

    def __init__(self, runtime: ProviderOAuthProductionComposition) -> None:
        self._runtime = runtime

    async def dispatch_create(
        self,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        provider_arguments: dict[str, object],
        *,
        operation_id: UUID,
    ) -> object:
        deadline = ProviderOperationDeadline.start(_CREATE_OPERATION_SECONDS)
        with provider_operation_deadline(deadline):
            selected, binding = (
                await self._runtime.qualification_resolver.bind_exact_for_connection(
                    connection,
                    capability_id=qualification.normalized_capability,
                    capability_version=qualification.capability_version_sha256,
                    deadline=deadline,
                )
            )
            if (
                selected != qualification
                or binding.operation_class is not ProviderOperationClass.CREATE
            ):
                raise ProviderUnavailable(
                    connection.provider,
                    dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
                )
            arguments: BaseModel = catalog_wire_model(
                qualification.input_schema,
                kind="input",
            ).model_validate(provider_arguments)
            result = await self._runtime.registry.get(connection.provider).call(
                connection,
                binding,
                arguments,
                operation_id,
                deadline=deadline,
            )
        if result.status_class is not ProviderStatusClass.SUCCESS:
            error_type = (
                ProviderUnavailable
                if result.dispatch_certainty is DispatchCertainty.NOT_DISPATCHED
                else ProviderOutcomeUnknown
            )
            raise error_type(
                connection.provider,
                dispatch_certainty=result.dispatch_certainty,
            )
        return extract_provider_identifier(result.normalized_data)


@dataclass(repr=False)
class HostedDocumentRuntime:
    """Request-scoped Mercury V1 document preview and operation runtime."""

    store: Any = field(repr=False)
    preview_service: Any = field(repr=False)
    operation_service: Any = field(repr=False)
    membership_resolver: MembershipResolver = field(repr=False)
    provider_runtime: Any = field(repr=False)
    store_http_client: Any = field(repr=False)
    _closed: bool = field(default=False, init=False, repr=False)

    async def prepare_document_create(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        request: PrepareDocumentCreate,
    ) -> PreparedDocumentPreview:
        return await self.preview_service.prepare_document_create(
            principal,
            workspace_id,
            connection_id,
            capability_id,
            capability_version,
            request,
        )

    async def render_document_preview(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> PreparedDocumentPreview:
        membership = WorkspaceMembership.model_validate(
            await _await_value(self.membership_resolver(principal, workspace_id))
        )
        preview = await asyncio.to_thread(
            self.store.get_preview,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            preview_id=preview_id,
        )
        return PreparedDocumentPreview.from_preview(preview)

    async def confirm_document_create(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        preview_id: UUID,
        state_version: int,
    ) -> HostedOperation:
        return await self.operation_service.confirm_and_dispatch(
            principal,
            workspace_id,
            DocumentCreateConfirmation(
                preview_id=preview_id,
                expected_state_version=state_version,
                confirmation="CONFIRM_CREATE",
            ),
        )

    async def get_operation_status(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> HostedOperation:
        membership = WorkspaceMembership.model_validate(
            await _await_value(self.membership_resolver(principal, workspace_id))
        )
        return await asyncio.to_thread(
            self.store.get_operation,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            operation_id=operation_id,
        )

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await _await_value(self.provider_runtime.aclose())
        finally:
            self.store_http_client.close()


def build_hosted_document_production_composition(
    *,
    settings: Settings,
) -> HostedDocumentRuntime:
    """Build one request-scoped Render/Supabase document runtime."""

    settings.validate_v1()
    provider_runtime = build_provider_oauth_production_composition(settings=settings)
    workspace_service = WorkspaceService.from_settings(settings)
    payload_vault = HostedPayloadVault(CredentialVault.from_settings(settings))
    qualifications = tuple(
        ProviderMCPQualification.model_validate(item)
        for item in provider_runtime.qualification_catalog.list_provider_mcp_qualifications()
    )
    projector_registry = _reviewed_projectors(qualifications)
    store_http = httpx.Client(
        follow_redirects=False,
        trust_env=False,
        timeout=20,
    )
    store = SupabaseHostedPreviewStore(
        settings=settings,
        payload_vault=payload_vault,
        http_client=store_http,
        projector_registry=projector_registry,
    )

    async def membership_resolver(
        principal: MercuryPrincipal,
        workspace_id: UUID,
    ) -> WorkspaceMembership:
        return await asyncio.to_thread(
            workspace_service.require_workspace,
            principal,
            current_mercury_access_token(principal),
            workspace_id,
            WorkspaceRole.MEMBER,
        )

    async def connection_resolver(
        membership: WorkspaceMembership,
        principal: MercuryPrincipal,
        connection_id: UUID,
    ) -> ProviderConnection:
        return await asyncio.to_thread(
            provider_runtime.connection_store.load_connection,
            tenant_id=membership.tenant_id,
            workspace_id=membership.workspace_id,
            auth_user_id=principal.subject,
            connection_id=connection_id,
        )

    async def qualification_resolver(
        connection: ProviderConnection,
        capability_id: str,
        capability_version: str,
    ) -> ProviderMCPQualification:
        deadline = ProviderOperationDeadline.start(_CREATE_OPERATION_SECONDS)
        with provider_operation_deadline(deadline):
            qualification, _binding = (
                await provider_runtime.qualification_resolver.bind_exact_for_connection(
                    connection,
                    capability_id=capability_id,
                    capability_version=capability_version,
                    deadline=deadline,
                )
            )
        return qualification

    async def audit_recorder(event: dict[str, object]) -> None:
        await asyncio.to_thread(SupabaseRagStore(settings).record_audit_event, event)

    preview_service = HostedPreviewService(
        store=store,
        payload_vault=payload_vault,
        membership_resolver=membership_resolver,
        connection_resolver=connection_resolver,
        qualification_resolver=qualification_resolver,
        projector_registry=projector_registry,
    )
    operation_service = BatchOperationService(
        store=store,
        membership_resolver=membership_resolver,
        connection_resolver=connection_resolver,
        qualification_resolver=qualification_resolver,
        sequential_dispatcher=HostedProviderCreateDispatcher(provider_runtime),
        native_batch_dispatcher=None,
        native_batch_qualification=None,
        audit_recorder=audit_recorder,
    )
    return HostedDocumentRuntime(
        store=store,
        preview_service=preview_service,
        operation_service=operation_service,
        membership_resolver=membership_resolver,
        provider_runtime=provider_runtime,
        store_http_client=store_http,
    )


__all__ = [
    "HostedDocumentRuntime",
    "HostedProviderCreateDispatcher",
    "build_hosted_document_production_composition",
    "extract_provider_identifier",
    "supports_reviewed_invoice_projection",
]
