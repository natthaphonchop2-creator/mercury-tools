"""Deterministic hosted batch document-create dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field

from mercury_tools.catalog.models import ProviderMCPQualification
from mercury_tools.providers.base import ProviderRuntimeError
from mercury_tools.providers.models import ProviderConnection, ProviderId

from .models import (
    ConfirmableDocumentPreview,
    HostedOperation,
    OperationItem,
    OperationItemState,
    ParentOperationState,
)
from .operation_service import (
    AuditRecorder,
    ConnectionResolver,
    DocumentOperationError,
    MembershipResolver,
    OperationService,
    ProviderCreateDispatcher,
    ProviderCreateRejected,
    QualificationResolver,
    UUIDFactory,
    _OperationModel,
    _proven_not_dispatched,
    _valid_provider_identifier,
)
from .store import HostedPreviewStore

_SHA256 = r"^[0-9a-f]{64}$"
_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_TOOL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


class NativeBatchQualification(_OperationModel):
    """Recorded exact native-batch evidence selected outside the model boundary."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    provider: ProviderId
    environment: str = Field(pattern=_ENVIRONMENT)
    create_capability_id: str = Field(pattern=_CAPABILITY)
    create_capability_version: str = Field(pattern=_SHA256)
    batch_capability_id: str = Field(pattern=_CAPABILITY)
    batch_capability_version: str = Field(pattern=_SHA256)
    provider_tool_name: str = Field(pattern=_TOOL_NAME)
    max_batch_size: int = Field(ge=1, le=25)
    response_correlation: Literal["request_order"]
    duplicate_behavior: Literal["idempotent_by_operation_id"]
    timeout_semantics: Literal["ambiguous_after_possible_dispatch"]
    atomicity: Literal["atomic"]


class NativeBatchDispatcher(Protocol):
    async def dispatch_batch(
        self,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        provider_arguments: tuple[dict[str, object], ...],
        *,
        operation_id: UUID,
    ) -> object: ...


class BatchOperationService(OperationService):
    """Use an exact native batch or a deterministic sequential fallback."""

    def __init__(
        self,
        *,
        store: HostedPreviewStore,
        membership_resolver: MembershipResolver,
        connection_resolver: ConnectionResolver,
        qualification_resolver: QualificationResolver,
        sequential_dispatcher: ProviderCreateDispatcher,
        native_batch_dispatcher: NativeBatchDispatcher | None,
        native_batch_qualification: NativeBatchQualification | None,
        audit_recorder: AuditRecorder,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: UUIDFactory | None = None,
    ) -> None:
        super().__init__(
            store=store,
            membership_resolver=membership_resolver,
            connection_resolver=connection_resolver,
            qualification_resolver=qualification_resolver,
            provider_dispatcher=sequential_dispatcher,
            audit_recorder=audit_recorder,
            clock=clock,
            uuid_factory=uuid_factory,
        )
        self._native_batch_dispatcher = native_batch_dispatcher
        self._native_batch_qualification = native_batch_qualification

    async def _dispatch_confirmed(
        self,
        *,
        operation: HostedOperation,
        confirmable: ConfirmableDocumentPreview,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
    ) -> HostedOperation:
        if len(operation.items) < 2:
            return await super()._dispatch_confirmed(
                operation=operation,
                confirmable=confirmable,
                connection=connection,
                qualification=qualification,
            )
        if self._native_is_exact(connection, qualification, item_count=len(operation.items)):
            return await self._dispatch_native(
                operation=operation,
                confirmable=confirmable,
                connection=connection,
                qualification=qualification,
            )
        return await self._dispatch_sequential(
            operation=operation,
            confirmable=confirmable,
            connection=connection,
            qualification=qualification,
        )

    def _native_is_exact(
        self,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        *,
        item_count: int,
    ) -> bool:
        if self._native_batch_dispatcher is None or self._native_batch_qualification is None:
            return False
        try:
            native = NativeBatchQualification.model_validate(self._native_batch_qualification)
        except Exception:
            return False
        return (
            native.provider is connection.provider
            and native.environment == connection.environment
            and native.create_capability_id == qualification.normalized_capability
            and native.create_capability_version == qualification.capability_version_sha256
            and native.batch_capability_version != "0" * 64
            and item_count <= native.max_batch_size
        )

    async def _dispatch_sequential(
        self,
        *,
        operation: HostedOperation,
        confirmable: ConfirmableDocumentPreview,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
    ) -> HostedOperation:
        current = await self._transition_parent(
            operation,
            ParentOperationState.DISPATCHING,
            reason="provider_create_started",
        )
        for index, source_item in enumerate(operation.items):
            current = await self._transition_item(
                current,
                source_item.operation_item_id,
                OperationItemState.DISPATCHING,
                reason="provider_create_started",
            )
            payload = confirmable.provider_arguments_for(source_item.client_item_id)
            try:
                raw_result = await self._provider_dispatcher.dispatch_create(
                    connection,
                    qualification,
                    payload,
                    operation_id=operation.operation_id,
                )
            except ProviderCreateRejected:
                current = await self._transition_item(
                    current,
                    source_item.operation_item_id,
                    OperationItemState.PROVIDER_REJECTED,
                    reason="provider_rejected",
                )
                current = await self._close_remaining(current, operation.items[index + 1 :])
                return await self._transition_parent(
                    current,
                    ParentOperationState.PROVIDER_REJECTED,
                    reason="provider_rejected",
                )
            except ProviderRuntimeError as error:
                if _proven_not_dispatched(error):
                    current = await self._transition_item(
                        current,
                        source_item.operation_item_id,
                        OperationItemState.FAILED_PRE_DISPATCH,
                        reason="provider_not_dispatched",
                    )
                    current = await self._close_remaining(current, operation.items[index + 1 :])
                    return await self._transition_parent(
                        current,
                        ParentOperationState.FAILED_PRE_DISPATCH,
                        reason="provider_not_dispatched",
                    )
                return await self._unknown_and_stop(
                    current,
                    operation.items[index:],
                )
            except asyncio.CancelledError:
                await asyncio.shield(self._unknown_and_stop(current, operation.items[index:]))
                raise
            except Exception:
                return await self._unknown_and_stop(current, operation.items[index:])

            identifier = _valid_provider_identifier(raw_result)
            if identifier is None:
                return await self._unknown_and_stop(current, operation.items[index:])
            current = await self._transition_item(
                current,
                source_item.operation_item_id,
                OperationItemState.SUCCEEDED,
                reason="provider_create_succeeded",
                provider_result_identifier=identifier,
            )
        return await self._transition_parent(
            current,
            ParentOperationState.SUCCEEDED,
            reason="provider_create_succeeded",
        )

    async def _dispatch_native(
        self,
        *,
        operation: HostedOperation,
        confirmable: ConfirmableDocumentPreview,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
    ) -> HostedOperation:
        if self._native_batch_dispatcher is None:
            raise DocumentOperationError("operation_invalid")
        dispatching = await self._begin_dispatch(operation)
        payloads = tuple(
            confirmable.provider_arguments_for(item.client_item_id) for item in operation.items
        )
        try:
            raw_result = await self._native_batch_dispatcher.dispatch_batch(
                connection,
                qualification,
                payloads,
                operation_id=operation.operation_id,
            )
        except ProviderCreateRejected:
            return await self._finish_all(
                dispatching,
                item_state=OperationItemState.PROVIDER_REJECTED,
                parent_state=ParentOperationState.PROVIDER_REJECTED,
                reason="provider_rejected",
            )
        except ProviderRuntimeError as error:
            if _proven_not_dispatched(error):
                return await self._finish_all(
                    dispatching,
                    item_state=OperationItemState.FAILED_PRE_DISPATCH,
                    parent_state=ParentOperationState.FAILED_PRE_DISPATCH,
                    reason="provider_not_dispatched",
                )
            return await self._finish_all(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_outcome_unknown",
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish_all(
                    dispatching,
                    item_state=OperationItemState.OUTCOME_UNKNOWN,
                    parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                    reason="provider_outcome_unknown",
                )
            )
            raise
        except Exception:
            return await self._finish_all(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_outcome_unknown",
            )

        if not isinstance(raw_result, (tuple, list)) or len(raw_result) != len(operation.items):
            return await self._finish_all(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_response_malformed",
            )
        identifiers = tuple(_valid_provider_identifier(value) for value in raw_result)
        if any(identifier is None for identifier in identifiers):
            return await self._finish_all(
                dispatching,
                item_state=OperationItemState.OUTCOME_UNKNOWN,
                parent_state=ParentOperationState.OUTCOME_UNKNOWN,
                reason="provider_response_malformed",
            )
        current = dispatching
        for operation_item, identifier in zip(operation.items, identifiers, strict=True):
            current = await self._transition_item(
                current,
                operation_item.operation_item_id,
                OperationItemState.SUCCEEDED,
                reason="provider_create_succeeded",
                provider_result_identifier=identifier,
            )
        return await self._transition_parent(
            current,
            ParentOperationState.SUCCEEDED,
            reason="provider_create_succeeded",
        )

    async def _unknown_and_stop(
        self,
        operation: HostedOperation,
        remaining_items: Sequence[OperationItem],
    ) -> HostedOperation:
        current = await self._transition_item(
            operation,
            remaining_items[0].operation_item_id,
            OperationItemState.OUTCOME_UNKNOWN,
            reason="provider_outcome_unknown",
        )
        current = await self._close_remaining(current, remaining_items[1:])
        return await self._transition_parent(
            current,
            ParentOperationState.OUTCOME_UNKNOWN,
            reason="provider_outcome_unknown",
        )

    async def _close_remaining(
        self,
        operation: HostedOperation,
        remaining_items: Sequence[OperationItem],
    ) -> HostedOperation:
        current = operation
        for operation_item in remaining_items:
            current = await self._transition_item(
                current,
                operation_item.operation_item_id,
                OperationItemState.NOT_DISPATCHED,
                reason="batch_stopped",
            )
        return current

    async def _finish_all(
        self,
        operation: HostedOperation,
        *,
        item_state: OperationItemState,
        parent_state: ParentOperationState,
        reason: str,
    ) -> HostedOperation:
        current = operation
        for operation_item in operation.items:
            current = await self._transition_item(
                current,
                operation_item.operation_item_id,
                item_state,
                reason=reason,
            )
        return await self._transition_parent(current, parent_state, reason=reason)


__all__ = ["BatchOperationService", "NativeBatchQualification"]
