"""Qualified exact-lookup reconciliation for unknown create outcomes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, TypeAlias
from uuid import UUID, uuid4

from pydantic import Field

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.models import ProviderConnection, ProviderId
from mercury_tools.workspaces.models import WorkspaceMembership

from .models import DocumentPreview, HostedOperation, OperationItemState, ParentOperationState
from .operation_service import (
    AuditRecorder,
    ConnectionResolver,
    MembershipResolver,
    QualificationResolver,
    UUIDFactory,
    _authority_matches_preview,
    _await_value,
    _identifier_sha256,
    _next_uuid,
    _now,
    _offload,
    _OperationModel,
    _valid_provider_identifier,
)
from .store import HostedPreviewError, HostedPreviewStore

_SHA256 = r"^[0-9a-f]{64}$"
_CAPABILITY = r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$"
_ENVIRONMENT = r"^[a-z][a-z0-9_-]{0,63}$"
_TOOL_NAME = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$"


class RecordedLookupBinding(_OperationModel):
    provider: ProviderId
    environment: str = Field(pattern=_ENVIRONMENT)
    create_capability_id: str = Field(pattern=_CAPABILITY)
    create_capability_version: str = Field(pattern=_SHA256)
    lookup_capability_id: str = Field(pattern=_CAPABILITY)
    lookup_capability_version: str = Field(pattern=_SHA256)
    provider_tool_name: str = Field(pattern=_TOOL_NAME)


class ReconciliationError(RuntimeError):
    _CODES = frozenset(
        {
            "audit_write_failed",
            "lookup_binding_changed",
            "lookup_response_invalid",
            "operation_not_reconcilable",
        }
    )

    def __init__(self, code: str) -> None:
        if code not in self._CODES:
            raise ValueError("reconciliation_error_invalid")
        self.code = code
        super().__init__(code)


RecordedLookupBindingResolver: TypeAlias = Callable[
    [HostedOperation], RecordedLookupBinding | Awaitable[RecordedLookupBinding]
]
LookupQualificationResolver: TypeAlias = Callable[
    [ProviderConnection, RecordedLookupBinding],
    ProviderMCPQualification | Awaitable[ProviderMCPQualification],
]


class ExactLookupProvider(Protocol):
    async def lookup_exact(
        self,
        connection: ProviderConnection,
        lookup_qualification: ProviderMCPQualification,
        *,
        provider_call_hash: str,
    ) -> tuple[str, ...]: ...


class ReconciliationService:
    """Reconcile an unknown outcome without replaying the create action."""

    def __init__(
        self,
        *,
        store: HostedPreviewStore,
        membership_resolver: MembershipResolver,
        connection_resolver: ConnectionResolver,
        qualification_resolver: QualificationResolver,
        recorded_lookup_binding_resolver: RecordedLookupBindingResolver,
        lookup_qualification_resolver: LookupQualificationResolver,
        lookup_provider: ExactLookupProvider,
        audit_recorder: AuditRecorder,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: UUIDFactory | None = None,
    ) -> None:
        if not callable(audit_recorder):
            raise TypeError("reconciliation_service_invalid")
        self._store = store
        self._membership_resolver = membership_resolver
        self._connection_resolver = connection_resolver
        self._qualification_resolver = qualification_resolver
        self._recorded_lookup_binding_resolver = recorded_lookup_binding_resolver
        self._lookup_qualification_resolver = lookup_qualification_resolver
        self._lookup_provider = lookup_provider
        self._audit_recorder = audit_recorder
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    async def reconcile_outcome(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> HostedOperation:
        membership = await self._membership(principal, workspace_id)
        operation = await _offload(
            self._store.get_operation,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            operation_id=operation_id,
        )
        if operation.state is not ParentOperationState.OUTCOME_UNKNOWN:
            raise ReconciliationError("operation_not_reconcilable")
        preview = await _offload(
            self._store.get_preview,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            preview_id=operation.preview_id,
        )
        connection, create_qualification = await self._authority(
            membership=membership,
            principal=principal,
            preview=preview,
        )
        try:
            binding = RecordedLookupBinding.model_validate(
                await _await_value(self._recorded_lookup_binding_resolver(operation))
            )
            lookup_qualification = await _await_value(
                self._lookup_qualification_resolver(connection, binding)
            )
        except Exception:
            raise ReconciliationError("lookup_binding_changed") from None
        if not self._binding_is_exact(
            operation,
            connection,
            create_qualification,
            binding,
            lookup_qualification,
            now=_now(self._clock),
        ):
            raise ReconciliationError("lookup_binding_changed")

        try:
            await _await_value(self._audit_recorder(self._audit(operation, binding)))
        except Exception:
            raise ReconciliationError("audit_write_failed") from None

        current = operation
        for operation_item in operation.items:
            if operation_item.state is not OperationItemState.OUTCOME_UNKNOWN:
                continue
            try:
                raw_matches = await self._lookup_provider.lookup_exact(
                    connection,
                    lookup_qualification,
                    provider_call_hash=operation_item.provider_call_hash,
                )
            except Exception:
                raise ReconciliationError("lookup_response_invalid") from None
            matches = self._matches(raw_matches)
            if len(matches) == 1:
                current = await self._transition_item(
                    current,
                    operation_item.operation_item_id,
                    OperationItemState.SUCCEEDED,
                    reason="provider_reconciled",
                    provider_result_identifier=matches[0],
                )
            elif len(matches) > 1:
                current = await self._transition_item(
                    current,
                    operation_item.operation_item_id,
                    OperationItemState.NEEDS_MANUAL_REVIEW,
                    reason="multiple_exact_matches",
                )

        states = tuple(item.state for item in current.items)
        if any(state is OperationItemState.NEEDS_MANUAL_REVIEW for state in states):
            return await self._transition_parent(
                current,
                ParentOperationState.NEEDS_MANUAL_REVIEW,
                reason="multiple_exact_matches",
            )
        if any(state is OperationItemState.OUTCOME_UNKNOWN for state in states):
            return current
        if all(state is OperationItemState.SUCCEEDED for state in states):
            return await self._transition_parent(
                current,
                ParentOperationState.SUCCEEDED,
                reason="provider_reconciled",
            )
        return current

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
        except Exception:
            raise ReconciliationError("lookup_binding_changed") from None
        if not _authority_matches_preview(
            preview,
            connection,
            qualification,
            now=_now(self._clock),
        ):
            raise ReconciliationError("lookup_binding_changed")
        return connection, qualification

    @staticmethod
    def _binding_is_exact(
        operation: HostedOperation,
        connection: ProviderConnection,
        create_qualification: ProviderMCPQualification,
        binding: RecordedLookupBinding,
        lookup_qualification: object,
        *,
        now: datetime,
    ) -> bool:
        return (
            isinstance(lookup_qualification, ProviderMCPQualification)
            and binding.provider is operation.provider
            and binding.provider is connection.provider
            and binding.environment == operation.environment
            and binding.environment == connection.environment
            and binding.create_capability_id == operation.capability_id
            and binding.create_capability_id == create_qualification.normalized_capability
            and binding.create_capability_version == operation.capability_version
            and binding.create_capability_version == create_qualification.capability_version_sha256
            and binding.lookup_capability_version != "0" * 64
            and getattr(lookup_qualification, "provider", None) == binding.provider.value
            and getattr(lookup_qualification, "environment", None) == binding.environment
            and getattr(lookup_qualification, "normalized_capability", None)
            == binding.lookup_capability_id
            and getattr(lookup_qualification, "capability_version_sha256", None)
            == binding.lookup_capability_version
            and getattr(lookup_qualification, "provider_tool_name", None)
            == binding.provider_tool_name
            and lookup_qualification.qualification_state is QualificationState.ENABLED
            and lookup_qualification.evidence_evaluated_at is not None
            and lookup_qualification.evidence_evaluated_at <= now
            and lookup_qualification.evidence_expires_at is not None
            and lookup_qualification.evidence_expires_at > now
            and set(lookup_qualification.required_permissions).issubset(
                connection.granted_permissions
            )
        )

    @staticmethod
    def _matches(raw_matches: object) -> tuple[str, ...]:
        if not isinstance(raw_matches, (tuple, list)):
            raise ReconciliationError("lookup_response_invalid")
        matches = tuple(_valid_provider_identifier(value) for value in raw_matches)
        if any(match is None for match in matches) or len(matches) != len(set(matches)):
            raise ReconciliationError("lookup_response_invalid")
        return tuple(match for match in matches if match is not None)

    async def _transition_item(
        self,
        operation: HostedOperation,
        operation_item_id: UUID,
        target: OperationItemState,
        *,
        reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        current = next(
            item for item in operation.items if item.operation_item_id == operation_item_id
        )
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

    @staticmethod
    def _audit(operation: HostedOperation, binding: RecordedLookupBinding) -> dict[str, object]:
        return {
            "tool_name": "reconcile_document_create",
            "input": {
                "workspace_id_sha256": _identifier_sha256(operation.workspace_id),
                "operation_id": str(operation.operation_id),
                "lookup_capability_id": binding.lookup_capability_id,
                "lookup_capability_version": binding.lookup_capability_version,
            },
            "output_summary": {"status": "lookup_authorized"},
            "status": "ok",
            "metadata": {"runtime": "mcp", "surface": "v1"},
        }


__all__ = ["RecordedLookupBinding", "ReconciliationError", "ReconciliationService"]
