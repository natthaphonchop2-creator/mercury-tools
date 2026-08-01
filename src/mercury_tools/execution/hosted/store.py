"""Immutable hosted preview storage and encrypted payload boundaries."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import UTC, date, datetime
from typing import Any, Protocol
from uuid import UUID

import httpx
from pydantic import ValidationError

from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.config import Settings, v1_supabase_rest_url
from mercury_tools.credentials.models import CredentialBinding, CredentialEnvelope
from mercury_tools.credentials.vault import CredentialVault, CredentialVaultError
from mercury_tools.providers.models import ConnectionReadiness, ProviderConnection, ProviderId

from .models import (
    HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE,
    ConfirmableDocumentPreview,
    DocumentFinancials,
    DocumentPreview,
    HostedOperation,
    OpenedPreviewItem,
    OperationEvent,
    OperationItem,
    OperationItemState,
    ParentOperationState,
    PreviewPayloadBinding,
    PreviewState,
    StoredPreviewItem,
    preview_item_integrity_hash,
)
from .projectors import DocumentProjectorRegistry, ProjectorError, provider_call_hash

_PREVIEW_ERROR_CODES = frozenset(
    {
        "capability_unavailable",
        "capability_unreviewed",
        "document_payload_invalid",
        "document_schema_invalid",
        "duplicate_provider_call",
        "operation_conflict",
        "operation_not_found",
        "operation_state_stale",
        "operation_transition_invalid",
        "preview_binding_changed",
        "preview_conflict",
        "preview_expired",
        "preview_not_found",
        "preview_payload_changed",
        "preview_state_invalid",
        "preview_state_stale",
        "preview_store_unavailable",
        "workspace_access_denied",
    }
)

AuthorityResolver = Callable[[DocumentPreview], tuple[ProviderConnection, ProviderMCPQualification]]


class HostedPreviewError(RuntimeError):
    """Stable hosted state error that never includes business payload values."""

    def __init__(self, code: str) -> None:
        if code not in _PREVIEW_ERROR_CODES:
            raise ValueError("hosted_preview_error_invalid")
        self.code = code
        super().__init__(code)


class HostedPayloadVault:
    """Adapt the credential vault to complete preview/item AAD identities."""

    def __init__(self, vault: CredentialVault) -> None:
        if not isinstance(vault, CredentialVault):
            raise TypeError("hosted_payload_vault_invalid")
        self._vault = vault

    def __repr__(self) -> str:
        return "HostedPayloadVault()"

    @staticmethod
    def _credential_binding(binding: PreviewPayloadBinding) -> CredentialBinding:
        checked = PreviewPayloadBinding.model_validate(binding)
        return CredentialBinding(
            tenant_id=checked.tenant_id,
            workspace_id=checked.workspace_id,
            auth_user_id=checked.auth_user_id,
            connection_id=checked.connection_id,
            provider=checked.provider.value,
            company_or_merchant_id=checked.vault_company_binding,
            environment=checked.environment,
            credential_type=HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE,
        )

    def seal(
        self, binding: PreviewPayloadBinding, plaintext: bytes | bytearray
    ) -> CredentialEnvelope:
        try:
            return self._vault.seal(self._credential_binding(binding), plaintext)
        except (CredentialVaultError, TypeError, ValueError, ValidationError):
            raise HostedPreviewError("document_payload_invalid") from None

    def open(self, binding: PreviewPayloadBinding, envelope: CredentialEnvelope) -> bytearray:
        try:
            return self._vault.open(self._credential_binding(binding), envelope)
        except (CredentialVaultError, TypeError, ValueError, ValidationError):
            raise HostedPreviewError("preview_payload_changed") from None


class HostedPreviewStore(Protocol):
    def create_preview(self, preview: DocumentPreview) -> DocumentPreview: ...

    def find_preview_by_provider_call(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        connection_id: UUID,
        provider_call_hash: str,
    ) -> DocumentPreview | None: ...

    def get_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> DocumentPreview: ...

    def load_confirmable(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        now: datetime,
    ) -> ConfirmableDocumentPreview: ...

    def transition_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        target_state: PreviewState,
        occurred_at: datetime,
    ) -> DocumentPreview: ...

    def create_operation(
        self,
        operation: HostedOperation,
        *,
        expected_preview_state_version: int = 1,
    ) -> HostedOperation: ...

    def get_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> HostedOperation: ...

    def transition_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        expected_state_version: int,
        target_state: ParentOperationState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
    ) -> HostedOperation: ...

    def transition_operation_item(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        operation_item_id: UUID,
        expected_state_version: int,
        target_state: OperationItemState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation: ...


def _timestamp(value: datetime, code: str = "preview_store_unavailable") -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HostedPreviewError(code)
    return value.astimezone(UTC)


def _provider_account_sha256(connection: ProviderConnection) -> str:
    return hashlib.sha256(connection.provider_account_id.encode("utf-8")).hexdigest()


def _check_scope(
    preview: DocumentPreview,
    *,
    tenant_id: UUID,
    auth_user_id: UUID,
    workspace_id: UUID,
) -> None:
    if (
        preview.tenant_id != tenant_id
        or preview.auth_user_id != auth_user_id
        or preview.workspace_id != workspace_id
    ):
        raise HostedPreviewError("preview_not_found")


def _connection_matches(preview: DocumentPreview, connection: ProviderConnection) -> bool:
    return (
        connection.id == preview.connection_id
        and connection.tenant_id == preview.tenant_id
        and connection.auth_user_id == preview.auth_user_id
        and connection.workspace_id == preview.workspace_id
        and connection.provider is preview.provider
        and connection.environment == preview.environment
        and connection.revision == preview.connection_revision
        and connection.readiness is ConnectionReadiness.READY
        and connection.readiness is preview.connection_readiness
        and _provider_account_sha256(connection) == preview.provider_account_sha256
    )


def _qualification_identity_matches(
    preview: DocumentPreview,
    qualification: ProviderMCPQualification,
) -> bool:
    return (
        qualification.id == preview.qualification_id
        and qualification.provider == preview.provider.value
        and qualification.environment == preview.environment
        and qualification.provider_tool_name == preview.provider_tool_name
        and qualification.normalized_capability == preview.capability_id
        and qualification.capability_version_sha256 == preview.capability_version
        and qualification.schema_hash == preview.schema_hash
        and qualification.response_shape_hash == preview.response_shape_hash
        and qualification.evidence_revision_sha256 == preview.evidence_revision_sha256
        and qualification.company_sha256 == preview.provider_account_sha256
    )


def _qualification_is_current(
    qualification: ProviderMCPQualification,
    connection: ProviderConnection,
    *,
    now: datetime,
) -> bool:
    return (
        qualification.qualification_state is QualificationState.ENABLED
        and qualification.evidence_evaluated_at is not None
        and qualification.evidence_evaluated_at <= now
        and qualification.evidence_expires_at is not None
        and qualification.evidence_expires_at > now
        and set(qualification.required_permissions).issubset(connection.granted_permissions)
    )


def _validate_authority(
    preview: DocumentPreview,
    *,
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
    projector_registry: DocumentProjectorRegistry,
    now: datetime,
    require_projector: bool = True,
) -> ProviderMCPQualification:
    try:
        checked_connection = ProviderConnection.model_validate(connection)
        checked_qualification = ProviderMCPQualification.model_validate(qualification)
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("preview_binding_changed") from None
    if not _connection_matches(preview, checked_connection):
        raise HostedPreviewError("preview_binding_changed")
    if not _qualification_identity_matches(preview, checked_qualification):
        raise HostedPreviewError("preview_binding_changed")
    if not _qualification_is_current(checked_qualification, checked_connection, now=now):
        raise HostedPreviewError("capability_unavailable")
    if require_projector:
        projector = projector_registry.resolve(checked_qualification)
        if (
            projector is None
            or projector.projector_id != preview.projector_id
            or projector.projector_version != preview.projector_version
        ):
            raise HostedPreviewError("capability_unavailable")
    return checked_qualification


def _operation_matches_preview(operation: HostedOperation, preview: DocumentPreview) -> bool:
    if (
        operation.preview_id != preview.preview_id
        or operation.tenant_id != preview.tenant_id
        or operation.auth_user_id != preview.auth_user_id
        or operation.workspace_id != preview.workspace_id
        or operation.connection_id != preview.connection_id
        or operation.provider is not preview.provider
        or operation.environment != preview.environment
        or operation.capability_id != preview.capability_id
        or operation.capability_version != preview.capability_version
        or operation.connection_revision != preview.connection_revision
        or operation.provider_call_hash != preview.provider_call_hash
        or operation.preview_integrity_hash != preview.preview_integrity_hash
        or operation.state is not ParentOperationState.AWAITING_CONFIRMATION
        or operation.state_version != 1
        or operation.created_at < preview.created_at
        or operation.created_at >= preview.expires_at
        or operation.updated_at != operation.created_at
        or operation.payload_purge_after < preview.payload_purge_after
        or len(operation.items) != len(preview.items)
        or len(operation.events) != 1
    ):
        return False
    event = operation.events[0]
    if (
        event.operation_item_id is not None
        or event.from_state is not None
        or event.to_state != ParentOperationState.AWAITING_CONFIRMATION.value
        or event.state_version != 1
        or event.occurred_at != operation.created_at
    ):
        return False
    return all(
        operation_item.preview_item_id == preview_item.preview_item_id
        and operation_item.item_index == preview_item.item_index
        and operation_item.client_item_id == preview_item.client_item_id
        and operation_item.provider_call_hash == preview_item.provider_call_hash
        and operation_item.preview_integrity_hash == preview_item.preview_integrity_hash
        and operation_item.state is OperationItemState.AWAITING_CONFIRMATION
        and operation_item.state_version == 1
        and operation_item.provider_result_identifier is None
        and operation_item.created_at == operation.created_at
        and operation_item.updated_at == operation.created_at
        for operation_item, preview_item in zip(operation.items, preview.items, strict=True)
    )


def _same_confirmation_identity(left: HostedOperation, right: HostedOperation) -> bool:
    return (
        left.preview_id == right.preview_id
        and left.tenant_id == right.tenant_id
        and left.auth_user_id == right.auth_user_id
        and left.workspace_id == right.workspace_id
        and left.connection_id == right.connection_id
        and left.provider is right.provider
        and left.environment == right.environment
        and left.capability_id == right.capability_id
        and left.capability_version == right.capability_version
        and left.connection_revision == right.connection_revision
        and left.provider_call_hash == right.provider_call_hash
        and left.preview_integrity_hash == right.preview_integrity_hash
        and tuple(
            (
                item.item_index,
                item.preview_item_id,
                item.client_item_id,
                item.provider_call_hash,
                item.preview_integrity_hash,
            )
            for item in left.items
        )
        == tuple(
            (
                item.item_index,
                item.preview_item_id,
                item.client_item_id,
                item.provider_call_hash,
                item.preview_integrity_hash,
            )
            for item in right.items
        )
    )


def _load_confirmable(
    preview: DocumentPreview,
    *,
    payload_vault: HostedPayloadVault,
    projector_registry: DocumentProjectorRegistry,
    tenant_id: UUID,
    auth_user_id: UUID,
    workspace_id: UUID,
    expected_state_version: int,
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
    now: datetime,
) -> ConfirmableDocumentPreview:
    _check_scope(
        preview,
        tenant_id=tenant_id,
        auth_user_id=auth_user_id,
        workspace_id=workspace_id,
    )
    checked_now = _timestamp(now, "preview_state_invalid")
    if (
        isinstance(expected_state_version, bool)
        or not isinstance(expected_state_version, int)
        or expected_state_version != preview.state_version
    ):
        raise HostedPreviewError("preview_state_stale")
    if preview.state not in {PreviewState.PREPARED, PreviewState.AWAITING_CONFIRMATION}:
        raise HostedPreviewError("preview_state_invalid")
    if preview.expires_at <= checked_now:
        raise HostedPreviewError("preview_expired")
    checked_qualification = _validate_authority(
        preview,
        connection=connection,
        qualification=qualification,
        projector_registry=projector_registry,
        now=checked_now,
        require_projector=False,
    )
    projector = projector_registry.resolve(checked_qualification)
    if projector is None:
        raise HostedPreviewError("capability_unavailable")

    opened_items: list[OpenedPreviewItem] = []
    for item in preview.items:
        opened: bytearray | None = None
        try:
            opened = payload_vault.open(preview.payload_binding(item), item.payload_envelope)
            provider_arguments = json.loads(bytes(opened).decode("utf-8"))
            if not isinstance(provider_arguments, dict):
                raise ValueError
            projection = projector.project(provider_arguments)
            expected_provider_hash = provider_call_hash(
                provider=preview.provider.value,
                environment=preview.environment,
                provider_tool_name=preview.provider_tool_name,
                capability_id=preview.capability_id,
                capability_version=preview.capability_version,
                schema_hash=preview.schema_hash,
                provider_arguments=provider_arguments,
            )
            expected_integrity_hash = preview_item_integrity_hash(
                client_item_id=item.client_item_id,
                provider_call_hash=expected_provider_hash,
                projector_id=preview.projector_id,
                projector_version=preview.projector_version,
                document_type=projection.document_type,
                counterparty_display=projection.counterparty_display,
                issue_date=projection.issue_date,
                due_date=projection.due_date,
                financials=projection.financials,
                warnings=item.warnings,
                accountant_review_points=item.accountant_review_points,
            )
            if (
                expected_provider_hash != item.provider_call_hash
                or expected_integrity_hash != item.preview_integrity_hash
                or projection.document_type != item.document_type
                or projection.counterparty_display != item.counterparty_display
                or projection.issue_date != item.issue_date
                or projection.due_date != item.due_date
                or projection.financials != item.financials
            ):
                raise ValueError
            opened_items.append(
                OpenedPreviewItem(
                    preview_item_id=item.preview_item_id,
                    client_item_id=item.client_item_id,
                    provider_arguments=provider_arguments,
                )
            )
        except HostedPreviewError:
            raise
        except (ProjectorError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise HostedPreviewError("preview_payload_changed") from None
        finally:
            if opened is not None:
                opened[:] = b"\x00" * len(opened)
    return ConfirmableDocumentPreview(preview=preview, opened_items=tuple(opened_items))


class InMemoryHostedPreviewStore:
    """Production-equivalent unit store with immutable payload and CAS semantics."""

    def __init__(
        self,
        *,
        payload_vault: HostedPayloadVault,
        projector_registry: DocumentProjectorRegistry | None = None,
        authority_resolver: AuthorityResolver | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(payload_vault, HostedPayloadVault):
            raise TypeError("hosted_preview_store_invalid")
        self._payload_vault = payload_vault
        self._projector_registry = projector_registry or DocumentProjectorRegistry(())
        self._authority_resolver = authority_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.RLock()
        self._previews: dict[UUID, DocumentPreview] = {}
        self._preview_provider_calls: dict[tuple[UUID, UUID, str], UUID] = {}
        self._item_provider_calls: set[tuple[UUID, UUID, str]] = set()
        self._operations: dict[UUID, HostedOperation] = {}
        self._operation_by_preview: dict[UUID, UUID] = {}

    def __repr__(self) -> str:
        with self._lock:
            return (
                "InMemoryHostedPreviewStore("
                f"preview_count={len(self._previews)}, operation_count={len(self._operations)})"
            )

    def create_preview(self, preview: DocumentPreview) -> DocumentPreview:
        try:
            checked = DocumentPreview.model_validate(preview)
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("preview_conflict") from None
        with self._lock:
            key = (checked.workspace_id, checked.connection_id, checked.provider_call_hash)
            existing_id = self._preview_provider_calls.get(key)
            if existing_id is not None:
                existing = self._previews[existing_id]
                if existing.preview_integrity_hash == checked.preview_integrity_hash:
                    return DocumentPreview.model_validate(existing)
                raise HostedPreviewError("duplicate_provider_call")
            item_keys = {
                (checked.workspace_id, checked.connection_id, item.provider_call_hash)
                for item in checked.items
            }
            if checked.preview_id in self._previews:
                raise HostedPreviewError("preview_conflict")
            if item_keys & self._item_provider_calls:
                raise HostedPreviewError("duplicate_provider_call")
            self._previews[checked.preview_id] = checked
            self._preview_provider_calls[key] = checked.preview_id
            self._item_provider_calls.update(item_keys)
            return DocumentPreview.model_validate(checked)

    def find_preview_by_provider_call(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        connection_id: UUID,
        provider_call_hash: str,
    ) -> DocumentPreview | None:
        with self._lock:
            preview_id = self._preview_provider_calls.get(
                (workspace_id, connection_id, provider_call_hash)
            )
            if preview_id is None:
                return None
            preview = self._previews[preview_id]
            _check_scope(
                preview,
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
            )
            return DocumentPreview.model_validate(preview)

    def get_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> DocumentPreview:
        with self._lock:
            preview = self._previews.get(preview_id)
            if preview is None:
                raise HostedPreviewError("preview_not_found")
            _check_scope(
                preview,
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
            )
            return DocumentPreview.model_validate(preview)

    def load_confirmable(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        now: datetime,
    ) -> ConfirmableDocumentPreview:
        with self._lock:
            preview = self.get_preview(
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
                preview_id=preview_id,
            )
            return _load_confirmable(
                preview,
                payload_vault=self._payload_vault,
                projector_registry=self._projector_registry,
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
                expected_state_version=expected_state_version,
                connection=connection,
                qualification=qualification,
                now=now,
            )

    def transition_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        target_state: PreviewState,
        occurred_at: datetime,
    ) -> DocumentPreview:
        with self._lock:
            preview = self.get_preview(
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
                preview_id=preview_id,
            )
            if (
                isinstance(expected_state_version, bool)
                or not isinstance(expected_state_version, int)
                or preview.state_version != expected_state_version
            ):
                raise HostedPreviewError("preview_state_stale")
            if PreviewState(target_state) is PreviewState.CONFIRMED:
                raise HostedPreviewError("preview_state_invalid")
            try:
                transitioned = preview.transition(
                    target_state=PreviewState(target_state),
                    occurred_at=occurred_at,
                )
            except (TypeError, ValueError, ValidationError) as exc:
                code = (
                    "preview_expired" if str(exc) == "preview_expired" else "preview_state_invalid"
                )
                raise HostedPreviewError(code) from None
            self._previews[preview_id] = transitioned
            return DocumentPreview.model_validate(transitioned)

    def create_operation(
        self,
        operation: HostedOperation,
        *,
        expected_preview_state_version: int = 1,
    ) -> HostedOperation:
        try:
            checked = HostedOperation.model_validate(operation)
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("operation_conflict") from None
        with self._lock:
            preview = self._previews.get(checked.preview_id)
            if preview is None:
                raise HostedPreviewError("operation_conflict")
            existing_id = self._operation_by_preview.get(checked.preview_id)
            if existing_id is not None:
                existing = self._operations[existing_id]
                if not _same_confirmation_identity(existing, checked):
                    raise HostedPreviewError("operation_conflict")
                return HostedOperation.model_validate(existing)
            self._revalidate_operation_authority(preview)
            if not _operation_matches_preview(checked, preview):
                raise HostedPreviewError("operation_conflict")
            if checked.operation_id in self._operations:
                raise HostedPreviewError("operation_conflict")
            if (
                isinstance(expected_preview_state_version, bool)
                or not isinstance(expected_preview_state_version, int)
                or preview.state_version != expected_preview_state_version
            ):
                raise HostedPreviewError("preview_state_stale")
            now = _timestamp(self._clock(), "preview_state_invalid")
            if preview.expires_at <= now:
                raise HostedPreviewError("preview_expired")
            try:
                confirmed = preview.transition(
                    target_state=PreviewState.CONFIRMED,
                    occurred_at=checked.created_at,
                    confirmed_payload_purge_after=checked.payload_purge_after,
                )
            except (TypeError, ValueError, ValidationError) as exc:
                code = "preview_expired" if str(exc) == "preview_expired" else "operation_conflict"
                raise HostedPreviewError(code) from None
            self._previews[preview.preview_id] = confirmed
            self._operations[checked.operation_id] = checked
            self._operation_by_preview[checked.preview_id] = checked.operation_id
            return HostedOperation.model_validate(checked)

    def _revalidate_operation_authority(self, preview: DocumentPreview) -> None:
        if self._authority_resolver is None:
            raise HostedPreviewError("preview_binding_changed")
        try:
            connection, qualification = self._authority_resolver(preview)
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_binding_changed") from None
        _validate_authority(
            preview,
            connection=connection,
            qualification=qualification,
            projector_registry=self._projector_registry,
            now=_timestamp(self._clock(), "preview_state_invalid"),
        )

    def get_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> HostedOperation:
        with self._lock:
            operation = self._operations.get(operation_id)
            if (
                operation is None
                or operation.tenant_id != tenant_id
                or operation.auth_user_id != auth_user_id
                or operation.workspace_id != workspace_id
            ):
                raise HostedPreviewError("operation_not_found")
            return HostedOperation.model_validate(operation)

    def transition_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        expected_state_version: int,
        target_state: ParentOperationState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
    ) -> HostedOperation:
        with self._lock:
            operation = self.get_operation(
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
            if operation.state_version != expected_state_version:
                raise HostedPreviewError("operation_state_stale")
            target = ParentOperationState(target_state)
            if target is ParentOperationState.DISPATCHING:
                preview = self._previews.get(operation.preview_id)
                if preview is None:
                    raise HostedPreviewError("operation_not_found")
                self._revalidate_operation_authority(preview)
            try:
                transitioned = operation.transition(
                    target_state=target,
                    event_id=event_id,
                    occurred_at=occurred_at,
                    sanitized_reason=sanitized_reason,
                )
            except (TypeError, ValueError, ValidationError):
                raise HostedPreviewError("operation_transition_invalid") from None
            self._operations[operation_id] = transitioned
            return HostedOperation.model_validate(transitioned)

    def transition_operation_item(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        operation_item_id: UUID,
        expected_state_version: int,
        target_state: OperationItemState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        with self._lock:
            operation = self.get_operation(
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
                operation_id=operation_id,
            )
            matches = tuple(
                item for item in operation.items if item.operation_item_id == operation_item_id
            )
            if len(matches) != 1:
                raise HostedPreviewError("operation_not_found")
            if matches[0].state_version != expected_state_version:
                raise HostedPreviewError("operation_state_stale")
            try:
                transitioned = operation.transition_item(
                    operation_item_id=operation_item_id,
                    target_state=OperationItemState(target_state),
                    event_id=event_id,
                    occurred_at=occurred_at,
                    sanitized_reason=sanitized_reason,
                    provider_result_identifier=provider_result_identifier,
                )
            except (TypeError, ValueError, ValidationError):
                raise HostedPreviewError("operation_transition_invalid") from None
            self._operations[operation_id] = transitioned
            return HostedOperation.model_validate(transitioned)


class SupabaseHostedPreviewStore:
    """Service-role PostgREST adapter over tenant-checking preview/operation RPCs."""

    def __init__(
        self,
        *,
        settings: Settings,
        payload_vault: HostedPayloadVault,
        http_client: httpx.Client,
        projector_registry: DocumentProjectorRegistry | None = None,
    ) -> None:
        try:
            self._base_url = v1_supabase_rest_url(
                project_url=settings.supabase_url,
                auth_issuer=settings.supabase_auth_issuer,
            )
            if (
                not settings.supabase_service_role_key
                or not isinstance(payload_vault, HostedPayloadVault)
                or not isinstance(http_client, httpx.Client)
            ):
                raise ValueError
        except Exception:
            raise HostedPreviewError("preview_store_unavailable") from None
        self._service_role_key = settings.supabase_service_role_key
        self._payload_vault = payload_vault
        self._projector_registry = projector_registry or DocumentProjectorRegistry(())
        self._http = http_client

    def __repr__(self) -> str:
        return "SupabaseHostedPreviewStore()"

    def create_preview(self, preview: DocumentPreview) -> DocumentPreview:
        try:
            checked = DocumentPreview.model_validate(preview)
            row = self._rpc_one(
                "save_mercury_document_preview",
                {
                    "p_preview": checked.storage_record(),
                    "p_items": [item.storage_record() for item in checked.items],
                },
            )
            stored = _preview_from_rpc(row)
            if (
                stored.provider_call_hash != checked.provider_call_hash
                or stored.preview_integrity_hash != checked.preview_integrity_hash
            ):
                raise HostedPreviewError("duplicate_provider_call")
            return stored
        except HostedPreviewError:
            raise
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("preview_store_unavailable") from None

    def find_preview_by_provider_call(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        connection_id: UUID,
        provider_call_hash: str,
    ) -> DocumentPreview | None:
        rows = self._rpc_rows(
            "find_mercury_document_preview_by_provider_call",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_connection_id": str(connection_id),
                "p_provider_call_hash": provider_call_hash,
            },
        )
        if not rows:
            return None
        if len(rows) != 1:
            raise HostedPreviewError("preview_store_unavailable")
        preview = _preview_from_rpc(rows[0])
        _check_scope(
            preview,
            tenant_id=tenant_id,
            auth_user_id=auth_user_id,
            workspace_id=workspace_id,
        )
        if (
            preview.connection_id != connection_id
            or preview.provider_call_hash != provider_call_hash
        ):
            raise HostedPreviewError("preview_store_unavailable")
        return preview

    def get_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
    ) -> DocumentPreview:
        row = self._rpc_one(
            "load_mercury_document_preview",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_preview_id": str(preview_id),
            },
        )
        try:
            preview = _preview_from_rpc(row)
            _check_scope(
                preview,
                tenant_id=tenant_id,
                auth_user_id=auth_user_id,
                workspace_id=workspace_id,
            )
            if preview.preview_id != preview_id:
                raise ValueError
            return preview
        except HostedPreviewError:
            raise
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("preview_store_unavailable") from None

    def load_confirmable(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        connection: ProviderConnection,
        qualification: ProviderMCPQualification,
        now: datetime,
    ) -> ConfirmableDocumentPreview:
        preview = self.get_preview(
            tenant_id=tenant_id,
            auth_user_id=auth_user_id,
            workspace_id=workspace_id,
            preview_id=preview_id,
        )
        return _load_confirmable(
            preview,
            payload_vault=self._payload_vault,
            projector_registry=self._projector_registry,
            tenant_id=tenant_id,
            auth_user_id=auth_user_id,
            workspace_id=workspace_id,
            expected_state_version=expected_state_version,
            connection=connection,
            qualification=qualification,
            now=now,
        )

    def transition_preview(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        preview_id: UUID,
        expected_state_version: int,
        target_state: PreviewState,
        occurred_at: datetime,
    ) -> DocumentPreview:
        row = self._rpc_one(
            "transition_mercury_document_preview",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_preview_id": str(preview_id),
                "p_expected_state_version": expected_state_version,
                "p_target_status": PreviewState(target_state).value,
                "p_occurred_at": _timestamp(occurred_at).isoformat(),
            },
        )
        preview = _preview_from_rpc(row)
        _check_scope(
            preview,
            tenant_id=tenant_id,
            auth_user_id=auth_user_id,
            workspace_id=workspace_id,
        )
        if preview.preview_id != preview_id:
            raise HostedPreviewError("preview_store_unavailable")
        return preview

    def create_operation(
        self,
        operation: HostedOperation,
        *,
        expected_preview_state_version: int = 1,
    ) -> HostedOperation:
        try:
            checked = HostedOperation.model_validate(operation)
        except (TypeError, ValueError, ValidationError):
            raise HostedPreviewError("operation_conflict") from None
        row = self._rpc_one(
            "save_mercury_operation",
            {
                **operation_rpc_payload(checked),
                "p_expected_preview_state_version": expected_preview_state_version,
            },
        )
        stored = _operation_from_rpc(row)
        if not _same_confirmation_identity(stored, checked):
            raise HostedPreviewError("preview_store_unavailable")
        return stored

    def get_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
    ) -> HostedOperation:
        row = self._rpc_one(
            "load_mercury_operation",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_operation_id": str(operation_id),
            },
        )
        operation = _operation_from_rpc(row)
        if (
            operation.operation_id != operation_id
            or operation.tenant_id != tenant_id
            or operation.auth_user_id != auth_user_id
            or operation.workspace_id != workspace_id
        ):
            raise HostedPreviewError("preview_store_unavailable")
        return operation

    def transition_operation(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        expected_state_version: int,
        target_state: ParentOperationState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
    ) -> HostedOperation:
        row = self._rpc_one(
            "transition_mercury_operation",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_operation_id": str(operation_id),
                "p_expected_state_version": expected_state_version,
                "p_target_state": ParentOperationState(target_state).value,
                "p_event_id": str(event_id),
                "p_occurred_at": _timestamp(occurred_at).isoformat(),
                "p_sanitized_reason": sanitized_reason,
            },
        )
        operation = _operation_from_rpc(row)
        if (
            operation.operation_id != operation_id
            or operation.tenant_id != tenant_id
            or operation.auth_user_id != auth_user_id
            or operation.workspace_id != workspace_id
        ):
            raise HostedPreviewError("preview_store_unavailable")
        return operation

    def transition_operation_item(
        self,
        *,
        tenant_id: UUID,
        auth_user_id: UUID,
        workspace_id: UUID,
        operation_id: UUID,
        operation_item_id: UUID,
        expected_state_version: int,
        target_state: OperationItemState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        row = self._rpc_one(
            "transition_mercury_operation_item",
            {
                "p_tenant_id": str(tenant_id),
                "p_workspace_id": str(workspace_id),
                "p_auth_user_id": str(auth_user_id),
                "p_operation_id": str(operation_id),
                "p_operation_item_id": str(operation_item_id),
                "p_expected_state_version": expected_state_version,
                "p_target_state": OperationItemState(target_state).value,
                "p_event_id": str(event_id),
                "p_occurred_at": _timestamp(occurred_at).isoformat(),
                "p_sanitized_reason": sanitized_reason,
                "p_provider_result_identifier": provider_result_identifier,
            },
        )
        operation = _operation_from_rpc(row)
        if (
            operation.operation_id != operation_id
            or operation.tenant_id != tenant_id
            or operation.auth_user_id != auth_user_id
            or operation.workspace_id != workspace_id
            or not any(item.operation_item_id == operation_item_id for item in operation.items)
        ):
            raise HostedPreviewError("preview_store_unavailable")
        return operation

    def _rpc_one(self, function: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        rows = self._rpc_rows(function, payload)
        if len(rows) != 1:
            raise HostedPreviewError("preview_store_unavailable")
        return rows[0]

    def _rpc_rows(self, function: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
        try:
            response = self._http.post(
                f"{self._base_url}/rpc/{function}",
                json=dict(payload),
                headers={
                    "apikey": self._service_role_key,
                    "Authorization": f"Bearer {self._service_role_key}",
                    "Content-Type": "application/json",
                },
                timeout=20,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HostedPreviewError("preview_store_unavailable") from None
        if response.status_code < 200 or response.status_code >= 300:
            code: str | None = None
            with suppress(Exception):
                message = response.json().get("message")
                if message in _PREVIEW_ERROR_CODES:
                    code = message
            raise HostedPreviewError(code or "preview_store_unavailable")
        try:
            value = response.json()
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                raise ValueError
            return value
        except (TypeError, ValueError):
            raise HostedPreviewError("preview_store_unavailable") from None


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _parse_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ValueError
    return date.fromisoformat(value)


def _parse_bytes(value: Any) -> bytes:
    if not isinstance(value, str):
        raise ValueError
    encoded = value[2:] if value.startswith("\\x") else value
    return bytes.fromhex(encoded)


def _preview_from_rpc(row: Mapping[str, Any]) -> DocumentPreview:
    preview = row.get("preview")
    item_rows = row.get("items")
    if not isinstance(preview, Mapping) or not isinstance(item_rows, list):
        raise ValueError
    provider = ProviderId(preview["provider"])
    environment = str(preview["environment"])
    items: list[StoredPreviewItem] = []
    for item_row in item_rows:
        if not isinstance(item_row, Mapping):
            raise ValueError
        summary = item_row.get("sanitized_summary")
        if not isinstance(summary, Mapping):
            raise ValueError
        envelope = CredentialEnvelope(
            id=UUID(str(item_row["payload_envelope_id"])),
            tenant_id=UUID(str(item_row["tenant_id"])),
            workspace_id=UUID(str(item_row["workspace_id"])),
            auth_user_id=UUID(str(item_row["auth_user_id"])),
            connection_id=UUID(str(item_row["connection_id"])),
            provider=provider.value,
            environment=environment,
            credential_type=HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE,
            key_version=item_row["payload_key_version"],
            nonce=_parse_bytes(item_row["payload_nonce"]),
            ciphertext=_parse_bytes(item_row["payload_ciphertext"]),
            aad_hash=_parse_bytes(item_row["payload_aad_hash"]),
            created_at=_parse_timestamp(item_row["payload_envelope_created_at"]),
        )
        items.append(
            StoredPreviewItem(
                preview_item_id=UUID(str(item_row["id"])),
                preview_id=UUID(str(item_row["preview_id"])),
                tenant_id=UUID(str(item_row["tenant_id"])),
                auth_user_id=UUID(str(item_row["auth_user_id"])),
                workspace_id=UUID(str(item_row["workspace_id"])),
                connection_id=UUID(str(item_row["connection_id"])),
                item_index=item_row["item_index"],
                client_item_id=item_row["client_item_id"],
                provider_call_hash=item_row["provider_call_hash"],
                preview_integrity_hash=item_row["preview_integrity_hash"],
                document_type=item_row["document_type"],
                counterparty_display=summary["counterparty_display"],
                issue_date=_parse_date(summary["issue_date"]),
                due_date=_parse_date(summary["due_date"]),
                financials=DocumentFinancials.model_validate(summary["financials"]),
                warnings=tuple(summary.get("warnings", ())),
                accountant_review_points=tuple(summary.get("accountant_review_points", ())),
                payload_envelope=envelope,
                created_at=_parse_timestamp(item_row["created_at"]),
                payload_purge_after=_parse_timestamp(item_row["payload_purge_after"]),
            )
        )
    return DocumentPreview(
        preview_id=UUID(str(preview["id"])),
        tenant_id=UUID(str(preview["tenant_id"])),
        auth_user_id=UUID(str(preview["auth_user_id"])),
        workspace_id=UUID(str(preview["workspace_id"])),
        connection_id=UUID(str(preview["connection_id"])),
        provider=provider,
        provider_account_sha256=preview["provider_account_sha256"],
        account_display_name=preview["account_display_name"],
        environment=environment,
        qualification_id=UUID(str(preview["qualification_id"])),
        provider_tool_name=preview["provider_tool_name"],
        capability_id=preview["capability_id"],
        capability_version=preview["capability_version"],
        schema_hash=preview["schema_hash"],
        response_shape_hash=preview["response_shape_hash"],
        evidence_revision_sha256=preview["evidence_revision_sha256"],
        projector_id=preview["projector_id"],
        projector_version=preview["projector_version"],
        connection_revision=preview["connection_revision"],
        connection_readiness=ConnectionReadiness(preview["connection_readiness"]),
        provider_call_hash=preview["provider_call_hash"],
        preview_integrity_hash=preview["preview_integrity_hash"],
        state=PreviewState(preview["status"]),
        state_version=preview["state_version"],
        currency=preview["currency"],
        subtotal=preview["subtotal"],
        discount_total=preview["discount_total"],
        vat_total=preview["vat_total"],
        withholding_tax_total=preview["withholding_tax_total"],
        grand_total=preview["grand_total"],
        warnings=tuple(preview.get("warnings", ())),
        accountant_review_points=tuple(preview.get("accountant_review_points", ())),
        items=tuple(items),
        supersedes_preview_id=(
            UUID(str(preview["supersedes_preview_id"]))
            if preview.get("supersedes_preview_id")
            else None
        ),
        created_at=_parse_timestamp(preview["created_at"]),
        expires_at=_parse_timestamp(preview["expires_at"]),
        payload_purge_after=_parse_timestamp(preview["payload_purge_after"]),
        confirmed_at=(
            _parse_timestamp(preview["confirmed_at"]) if preview.get("confirmed_at") else None
        ),
        cancelled_at=(
            _parse_timestamp(preview["cancelled_at"]) if preview.get("cancelled_at") else None
        ),
    )


def operation_rpc_payload(operation: HostedOperation) -> dict[str, Any]:
    return {
        "p_operation": {
            "id": str(operation.operation_id),
            "preview_id": str(operation.preview_id),
            "tenant_id": str(operation.tenant_id),
            "auth_user_id": str(operation.auth_user_id),
            "workspace_id": str(operation.workspace_id),
            "connection_id": str(operation.connection_id),
            "provider": operation.provider.value,
            "environment": operation.environment,
            "capability_id": operation.capability_id,
            "capability_version": operation.capability_version,
            "connection_revision": operation.connection_revision,
            "provider_call_hash": operation.provider_call_hash,
            "preview_integrity_hash": operation.preview_integrity_hash,
            "status": operation.state.value,
            "state_version": operation.state_version,
            "created_at": operation.created_at.isoformat(),
            "updated_at": operation.updated_at.isoformat(),
            "payload_purge_after": operation.payload_purge_after.isoformat(),
        },
        "p_items": [
            {
                "id": str(item.operation_item_id),
                "operation_id": str(operation.operation_id),
                "preview_item_id": str(item.preview_item_id),
                "preview_id": str(operation.preview_id),
                "tenant_id": str(operation.tenant_id),
                "auth_user_id": str(operation.auth_user_id),
                "workspace_id": str(operation.workspace_id),
                "connection_id": str(operation.connection_id),
                "item_index": item.item_index,
                "client_item_id": item.client_item_id,
                "provider_call_hash": item.provider_call_hash,
                "preview_integrity_hash": item.preview_integrity_hash,
                "status": item.state.value,
                "state_version": item.state_version,
                "provider_result_identifier": item.provider_result_identifier,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in operation.items
        ],
        "p_events": [
            {
                "id": str(event.event_id),
                "operation_id": str(event.operation_id),
                "operation_item_id": (
                    str(event.operation_item_id) if event.operation_item_id else None
                ),
                "tenant_id": str(operation.tenant_id),
                "auth_user_id": str(operation.auth_user_id),
                "workspace_id": str(operation.workspace_id),
                "connection_id": str(operation.connection_id),
                "from_state": event.from_state,
                "to_state": event.to_state,
                "state_version": event.state_version,
                "sanitized_reason": event.sanitized_reason,
                "occurred_at": event.occurred_at.isoformat(),
            }
            for event in operation.events
        ],
    }


def _operation_from_rpc(row: Mapping[str, Any]) -> HostedOperation:
    operation = row.get("operation")
    item_rows = row.get("items")
    event_rows = row.get("events")
    if (
        not isinstance(operation, Mapping)
        or not isinstance(item_rows, list)
        or not isinstance(event_rows, list)
    ):
        raise HostedPreviewError("preview_store_unavailable")
    items = tuple(
        OperationItem(
            operation_item_id=UUID(str(item["id"])),
            preview_item_id=UUID(str(item["preview_item_id"])),
            item_index=item["item_index"],
            client_item_id=item["client_item_id"],
            provider_call_hash=item["provider_call_hash"],
            preview_integrity_hash=item["preview_integrity_hash"],
            state=OperationItemState(item["status"]),
            state_version=item["state_version"],
            provider_result_identifier=item.get("provider_result_identifier"),
            created_at=_parse_timestamp(item["created_at"]),
            updated_at=_parse_timestamp(item["updated_at"]),
        )
        for item in item_rows
    )
    events = tuple(
        OperationEvent(
            event_id=UUID(str(event["id"])),
            operation_id=UUID(str(event["operation_id"])),
            operation_item_id=(
                UUID(str(event["operation_item_id"])) if event.get("operation_item_id") else None
            ),
            from_state=event.get("from_state"),
            to_state=event["to_state"],
            state_version=event["state_version"],
            sanitized_reason=event["sanitized_reason"],
            occurred_at=_parse_timestamp(event["occurred_at"]),
        )
        for event in event_rows
    )
    return HostedOperation(
        operation_id=UUID(str(operation["id"])),
        preview_id=UUID(str(operation["preview_id"])),
        tenant_id=UUID(str(operation["tenant_id"])),
        auth_user_id=UUID(str(operation["auth_user_id"])),
        workspace_id=UUID(str(operation["workspace_id"])),
        connection_id=UUID(str(operation["connection_id"])),
        provider=ProviderId(operation["provider"]),
        environment=operation["environment"],
        capability_id=operation["capability_id"],
        capability_version=operation["capability_version"],
        connection_revision=operation["connection_revision"],
        provider_call_hash=operation["provider_call_hash"],
        preview_integrity_hash=operation["preview_integrity_hash"],
        state=ParentOperationState(operation["status"]),
        state_version=operation["state_version"],
        items=items,
        events=events,
        created_at=_parse_timestamp(operation["created_at"]),
        updated_at=_parse_timestamp(operation["updated_at"]),
        payload_purge_after=_parse_timestamp(operation["payload_purge_after"]),
    )


_operation_rpc_payload = operation_rpc_payload


__all__ = [
    "HostedPayloadVault",
    "HostedPreviewError",
    "HostedPreviewStore",
    "InMemoryHostedPreviewStore",
    "SupabaseHostedPreviewStore",
    "operation_rpc_payload",
]
