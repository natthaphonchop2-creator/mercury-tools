"""Confirm and dispatch immutable hosted document-create previews."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, TypeAlias
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.base import DispatchCertainty, ProviderRuntimeError
from mercury_tools.providers.models import ConnectionReadiness, ProviderConnection
from mercury_tools.workspaces.models import WorkspaceMembership

from .models import (
    ConfirmableDocumentPreview,
    DocumentPreview,
    HostedOperation,
    OperationItem,
    OperationItemState,
    ParentOperationState,
    PreviewState,
)
from .sanitization import sanitize_public_text
from .store import HostedPreviewError, HostedPreviewStore

MembershipResolver: TypeAlias = Callable[
    [MercuryPrincipal, UUID], WorkspaceMembership | Awaitable[WorkspaceMembership]
]
ConnectionResolver: TypeAlias = Callable[
    [WorkspaceMembership, MercuryPrincipal, UUID],
    ProviderConnection | Awaitable[ProviderConnection],
]
QualificationResolver: TypeAlias = Callable[
    [ProviderConnection, str, str],
    ProviderMCPQualification | Awaitable[ProviderMCPQualification],
]
AuditRecorder: TypeAlias = Callable[[dict[str, object]], object | Awaitable[object]]
UUIDFactory: TypeAlias = Callable[[], UUID]


class _OperationModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


class DocumentCreateConfirmation(_OperationModel):
    """Closed confirmation input; the reviewed provider payload stays server-side."""

    preview_id: UUID
    expected_state_version: int = Field(ge=1)
    confirmation: Literal["CONFIRM_CREATE"]


class DocumentOperationError(RuntimeError):
    """Stable operation error without downstream exception text."""

    _CODES = frozenset(
        {
            "audit_write_failed",
            "operation_invalid",
            "retry_payload_unavailable",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("document_operation_error_invalid")
        self.code = code
        super().__init__(code)


class ProviderCreateRejected(RuntimeError):
    """A deterministic provider rejection that proves no unknown outcome."""

    def __init__(self, code: str = "provider_rejected") -> None:
        if code != "provider_rejected":
            raise ValueError("provider_create_rejection_invalid")
        self.code = code
        super().__init__(code)


class ProviderCreateDispatcher(Protocol):
    async def dispatch_create(
        self,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        provider_arguments: dict[str, object],
        *,
        operation_id: UUID,
    ) -> object: ...


async def _await_value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _offload(callback: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(callback, *args, **kwargs))

    def consume(completed: asyncio.Task[Any]) -> None:
        try:
            completed.exception()
        except asyncio.CancelledError:
            return

    task.add_done_callback(consume)
    return await asyncio.shield(task)


def _now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise DocumentOperationError("operation_invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise DocumentOperationError("operation_invalid")
    return value.astimezone(UTC)


def _next_uuid(factory: UUIDFactory) -> UUID:
    try:
        value = factory()
    except Exception:
        raise DocumentOperationError("operation_invalid") from None
    if not isinstance(value, UUID) or value.int == 0:
        raise DocumentOperationError("operation_invalid")
    return value


def _identifier_sha256(value: UUID) -> str:
    return hashlib.sha256(str(value).encode("ascii")).hexdigest()


def _provider_account_sha256(connection: ProviderConnection) -> str:
    return hashlib.sha256(connection.provider_account_id.encode("utf-8")).hexdigest()


def _authority_matches_preview(
    preview: DocumentPreview,
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
    *,
    now: datetime,
) -> bool:
    return (
        connection.id == preview.connection_id
        and connection.tenant_id == preview.tenant_id
        and connection.auth_user_id == preview.auth_user_id
        and connection.workspace_id == preview.workspace_id
        and connection.provider is preview.provider
        and connection.environment == preview.environment
        and connection.revision == preview.connection_revision
        and connection.readiness is ConnectionReadiness.READY
        and _provider_account_sha256(connection) == preview.provider_account_sha256
        and qualification.id == preview.qualification_id
        and qualification.provider == preview.provider.value
        and qualification.environment == preview.environment
        and qualification.provider_tool_name == preview.provider_tool_name
        and qualification.normalized_capability == preview.capability_id
        and qualification.capability_version_sha256 == preview.capability_version
        and qualification.schema_hash == preview.schema_hash
        and qualification.response_shape_hash == preview.response_shape_hash
        and qualification.evidence_revision_sha256 == preview.evidence_revision_sha256
        and qualification.company_sha256 == preview.provider_account_sha256
        and qualification.qualification_state is QualificationState.ENABLED
        and qualification.evidence_evaluated_at is not None
        and qualification.evidence_evaluated_at <= now
        and qualification.evidence_expires_at is not None
        and qualification.evidence_expires_at > now
        and set(qualification.required_permissions).issubset(connection.granted_permissions)
    )


def _item(operation: HostedOperation, operation_item_id: UUID) -> OperationItem:
    matches = tuple(item for item in operation.items if item.operation_item_id == operation_item_id)
    if len(matches) != 1:
        raise DocumentOperationError("operation_invalid")
    return matches[0]


def _valid_provider_identifier(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        sanitized = sanitize_public_text(value, code="operation_invalid")
    except (TypeError, ValueError):
        return None
    return sanitized if len(sanitized) <= 200 else None


def _proven_not_dispatched(error: ProviderRuntimeError) -> bool:
    return error.dispatch_certainty is DispatchCertainty.NOT_DISPATCHED


class OperationService:
    """Serialize confirmation and execute one immutable create operation."""

    def __init__(
        self,
        *,
        store: HostedPreviewStore,
        membership_resolver: MembershipResolver,
        connection_resolver: ConnectionResolver,
        qualification_resolver: QualificationResolver,
        provider_dispatcher: ProviderCreateDispatcher,
        audit_recorder: AuditRecorder,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: UUIDFactory | None = None,
    ) -> None:
        if not callable(audit_recorder):
            raise TypeError("document_operation_service_invalid")
        self._store = store
        self._membership_resolver = membership_resolver
        self._connection_resolver = connection_resolver
        self._qualification_resolver = qualification_resolver
        self._provider_dispatcher = provider_dispatcher
        self._audit_recorder = audit_recorder
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4
        self._locks: dict[tuple[UUID, UUID, str], asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()
        self._retry_payloads: dict[UUID, ConfirmableDocumentPreview] = {}

    async def confirm_and_dispatch(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        confirmation: DocumentCreateConfirmation,
    ) -> HostedOperation:
        """Confirm exactly once; terminal and unknown operations are replay-only."""

        try:
            checked_confirmation = DocumentCreateConfirmation.model_validate(confirmation)
        except (TypeError, ValueError, ValidationError):
            raise DocumentOperationError("operation_invalid") from None
        membership = await self._membership(principal, workspace_id)
        preview = await self._get_preview(
            membership=membership,
            principal=principal,
            workspace_id=workspace_id,
            preview_id=checked_confirmation.preview_id,
        )
        key = (preview.workspace_id, preview.connection_id, preview.provider_call_hash)
        lock = await self._lock_for(key)
        async with lock:
            return await self._confirm_locked(
                principal=principal,
                membership=membership,
                workspace_id=workspace_id,
                confirmation=checked_confirmation,
            )

    async def _confirm_locked(
        self,
        *,
        principal: MercuryPrincipal,
        membership: WorkspaceMembership,
        workspace_id: UUID,
        confirmation: DocumentCreateConfirmation,
    ) -> HostedOperation:
        preview = await self._get_preview(
            membership=membership,
            principal=principal,
            workspace_id=workspace_id,
            preview_id=confirmation.preview_id,
        )
        confirmable: ConfirmableDocumentPreview | None = None
        connection: ProviderConnection | None = None
        qualification: ProviderMCPQualification | None = None
        if preview.state is not PreviewState.CONFIRMED:
            connection, qualification = await self._authority(
                membership=membership,
                principal=principal,
                preview=preview,
            )
            confirmable = await _offload(
                self._store.load_confirmable,
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=workspace_id,
                preview_id=preview.preview_id,
                expected_state_version=confirmation.expected_state_version,
                connection=connection,
                qualification=qualification,
                now=_now(self._clock),
            )

        candidate = self._candidate(preview)
        persisted = await _offload(
            self._store.create_operation,
            candidate,
            expected_preview_state_version=confirmation.expected_state_version,
        )
        created = persisted.operation_id == candidate.operation_id
        if created:
            if confirmable is None:
                raise DocumentOperationError("operation_invalid")
            self._retry_payloads[persisted.operation_id] = confirmable
        elif persisted.state not in {
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.FAILED_PRE_DISPATCH,
        }:
            return persisted

        if persisted.state not in {
            ParentOperationState.AWAITING_CONFIRMATION,
            ParentOperationState.FAILED_PRE_DISPATCH,
        }:
            return persisted
        if connection is None or qualification is None:
            connection, qualification = await self._authority(
                membership=membership,
                principal=principal,
                preview=preview,
            )
        confirmable = confirmable or self._retry_payloads.get(persisted.operation_id)
        if confirmable is None:
            raise DocumentOperationError("retry_payload_unavailable")

        try:
            await _await_value(self._audit_recorder(self._dispatch_audit(persisted)))
        except Exception:
            if persisted.state is ParentOperationState.AWAITING_CONFIRMATION:
                await self._mark_failed_before_dispatch(persisted)
            raise DocumentOperationError("audit_write_failed") from None

        result = await self._dispatch_confirmed(
            operation=persisted,
            confirmable=confirmable,
            connection=connection,
            qualification=qualification,
        )
        if result.state is not ParentOperationState.FAILED_PRE_DISPATCH:
            self._retry_payloads.pop(result.operation_id, None)
        return result

    async def _dispatch_confirmed(
        self,
        *,
        operation: HostedOperation,
        confirmable: ConfirmableDocumentPreview,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
    ) -> HostedOperation:
        if len(operation.items) != 1:
            raise DocumentOperationError("operation_invalid")
        dispatching = await self._begin_dispatch(operation)
        operation_item = dispatching.items[0]
        payload = confirmable.provider_arguments_for(operation_item.client_item_id)
        try:
            raw_result = await self._provider_dispatcher.dispatch_create(
                connection,
                qualification,
                payload,
                operation_id=operation.operation_id,
            )
        except ProviderCreateRejected:
            return await self._finish_single(
                dispatching,
                item_state=OperationItemState.PROVIDER_REJECTED,
                parent_state=ParentOperationState.PROVIDER_REJECTED,
                reason="provider_rejected",
            )
        except ProviderRuntimeError as error:
            if _proven_not_dispatched(error):
                return await self._finish_single(
                    dispatching,
                    item_state=OperationItemState.FAILED_PRE_DISPATCH,
                    parent_state=ParentOperationState.FAILED_PRE_DISPATCH,
                    reason="provider_not_dispatched",
                )
            return await self._finish_single(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_outcome_unknown",
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_single(
                    dispatching,
                    item_state=OperationItemState.OUTCOME_UNKNOWN,
                    parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                    reason="provider_outcome_unknown",
                )
            )
            raise
        except Exception:
            return await self._finish_single(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_outcome_unknown",
            )

        identifier = _valid_provider_identifier(raw_result)
        if identifier is None:
            return await self._finish_single(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_response_malformed",
            )
        return await self._finish_single(
            dispatching,
            item_state=OperationItemState.SUCCEEDED,
            parent_state=ParentOperationState.SUCCEEDED,
            reason="provider_create_succeeded",
            provider_result_identifier=identifier,
        )

    async def _begin_dispatch(self, operation: HostedOperation) -> HostedOperation:
        parent = await self._transition_parent(
            operation,
            ParentOperationState.DISPATCHING,
            reason=(
                "provider_create_retried"
                if operation.state is ParentOperationState.FAILED_PRE_DISPATCH
                else "provider_create_started"
            ),
        )
        current = parent
        for operation_item in parent.items:
            if operation_item.state not in {
                OperationItemState.AWAITING_CONFIRMATION,
                OperationItemState.FAILED_PRE_DISPATCH,
            }:
                continue
            current = await self._transition_item(
                current,
                operation_item.operation_item_id,
                OperationItemState.DISPATCHING,
                reason="provider_create_started",
            )
        return current

    async def _finish_single(
        self,
        operation: HostedOperation,
        *,
        item_state: OperationItemState,
        parent_state: ParentOperationState,
        reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        transitioned = await self._transition_item(
            operation,
            operation.items[0].operation_item_id,
            item_state,
            reason=reason,
            provider_result_identifier=provider_result_identifier,
        )
        return await self._transition_parent(transitioned, parent_state, reason=reason)

    async def _mark_failed_before_dispatch(self, operation: HostedOperation) -> HostedOperation:
        current = operation
        for operation_item in operation.items:
            current = await self._transition_item(
                current,
                operation_item.operation_item_id,
                OperationItemState.FAILED_PRE_DISPATCH,
                reason="audit_write_failed",
            )
        return await self._transition_parent(
            current,
            ParentOperationState.FAILED_PRE_DISPATCH,
            reason="audit_write_failed",
        )

    async def _transition_parent(
        self,
        operation: HostedOperation,
        target: ParentOperationState,
        *,
        reason: str,
    ) -> HostedOperation:
        return await _offload(
            self._store.transition_operation,
            tenant_id=operation.tenant_id,
            auth_user_id=operation.auth_user_id,
            workspace_id=operation.workspace_id,
            operation_id=operation.operation_id,
            expected_state_version=operation.state_version,
            target_state=target,
            event_id=_next_uuid(self._uuid_factory),
            occurred_at=_now(self._clock),
            sanitized_reason=reason,
        )

    async def _transition_item(
        self,
        operation: HostedOperation,
        operation_item_id: UUID,
        target: OperationItemState,
        *,
        reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        current = _item(operation, operation_item_id)
        return await _offload(
            self._store.transition_operation_item,
            tenant_id=operation.tenant_id,
            auth_user_id=operation.auth_user_id,
            workspace_id=operation.workspace_id,
            operation_id=operation.operation_id,
            operation_item_id=operation_item_id,
            expected_state_version=current.state_version,
            target_state=target,
            event_id=_next_uuid(self._uuid_factory),
            occurred_at=_now(self._clock),
            sanitized_reason=reason,
            provider_result_identifier=provider_result_identifier,
        )

    async def _membership(
        self, principal: MercuryPrincipal, workspace_id: UUID
    ) -> WorkspaceMembership:
        if not isinstance(principal, MercuryPrincipal):
            raise HostedPreviewError("workspace_access_denied")
        try:
            membership = WorkspaceMembership.model_validate(
                await _await_value(self._membership_resolver(principal, workspace_id))
            )
        except Exception:
            raise HostedPreviewError("workspace_access_denied") from None
        if membership.workspace_id != workspace_id:
            raise HostedPreviewError("workspace_access_denied")
        return membership

    async def _get_preview(
        self,
        *,
        membership: WorkspaceMembership,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> DocumentPreview:
        return await _offload(
            self._store.get_preview,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            preview_id=preview_id,
        )

    async def _authority(
        self,
        *,
        membership: WorkspaceMembership,
        principal: MercuryPrincipal,
        preview: DocumentPreview,
    ) -> tuple[ProviderConnection, ProviderMCPQualification]:
        try:
            connection = ProviderConnection.model_validate(
                await _await_value(
                    self._connection_resolver(membership, principal, preview.connection_id)
                )
            )
            qualification = ProviderMCPQualification.model_validate(
                await _await_value(
                    self._qualification_resolver(
                        connection,
                        preview.capability_id,
                        preview.capability_version,
                    )
                )
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_binding_changed") from None
        if not _authority_matches_preview(
            preview,
            connection,
            qualification,
            now=_now(self._clock),
        ):
            raise HostedPreviewError("preview_binding_changed")
        return connection, qualification

    def _candidate(self, preview: DocumentPreview) -> HostedOperation:
        return HostedOperation.from_preview(
            preview,
            operation_id=_next_uuid(self._uuid_factory),
            operation_item_ids=tuple(_next_uuid(self._uuid_factory) for _ in preview.items),
            event_id=_next_uuid(self._uuid_factory),
            now=_now(self._clock),
        )

    async def _lock_for(self, key: tuple[UUID, UUID, str]) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(key, asyncio.Lock())

    @staticmethod
    def _dispatch_audit(operation: HostedOperation) -> dict[str, object]:
        return {
            "tool_name": "confirm_document_create",
            "input": {
                "workspace_id_sha256": _identifier_sha256(operation.workspace_id),
                "connection_id_sha256": _identifier_sha256(operation.connection_id),
                "preview_id_sha256": _identifier_sha256(operation.preview_id),
                "operation_id": str(operation.operation_id),
                "capability_id": operation.capability_id,
                "capability_version": operation.capability_version,
            },
            "output_summary": {
                "status": "dispatch_authorized",
                "provider": operation.provider.value,
                "environment": operation.environment,
                "item_count": len(operation.items),
            },
            "status": "ok",
            "metadata": {"runtime": "mcp", "surface": "v1"},
        }


__all__ = [
    "DocumentCreateConfirmation",
    "DocumentOperationError",
    "OperationService",
    "ProviderCreateDispatcher",
    "ProviderCreateRejected",
]
