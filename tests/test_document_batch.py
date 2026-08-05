from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_document_operations import RecordingAudit, RecordingProvider
from test_document_preview import (
    CONNECTION_ID,
    NOW,
    WORKSPACE_ID,
    _draft,
    _membership,
    _principal,
    _service,
)

from mercury_tools.execution.hosted.models import BatchDocumentCreate
from mercury_tools.execution.hosted.store import HostedPreviewError
from mercury_tools.providers.base import DispatchCertainty, ProviderOutcomeUnknown
from mercury_tools.providers.models import ProviderId


@dataclass
class RecordingNativeBatchProvider:
    result: object = field(default_factory=lambda: ("native-document-1", "native-document-2"))
    calls: list[dict[str, object]] = field(default_factory=list)

    async def dispatch_batch(
        self,
        connection: object,
        qualification: object,
        provider_arguments: tuple[dict[str, object], ...],
        *,
        operation_id: UUID,
    ) -> object:
        self.calls.append(
            {
                "connection": connection,
                "qualification": qualification,
                "provider_arguments": provider_arguments,
                "operation_id": operation_id,
            }
        )
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


async def _prepared_batch(*, documents=None):
    selected_documents = documents or (
        _draft(client_item_id="first", reference="INV-BATCH-001"),
        _draft(client_item_id="second", reference="INV-BATCH-002"),
    )
    preview_service, store, connection, qualification, _ = _service(
        ids=tuple(uuid4() for _ in range(len(selected_documents) + 1)),
    )
    prepared = await preview_service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        BatchDocumentCreate(
            mode="batch",
            documents=selected_documents,
        ),
    )
    return prepared, store, connection, qualification


def _batch_service(
    *,
    store: object,
    connection: object,
    qualification: object,
    sequential_provider: RecordingProvider,
    native_provider: RecordingNativeBatchProvider | None,
    native_batch_qualification: object | None,
):
    from mercury_tools.execution.hosted.batch_service import BatchOperationService

    def resolve_connection(
        _resolved_membership: object,
        _resolved_principal: object,
        _connection_id: UUID,
    ) -> object:
        return connection

    def resolve_qualification(
        _current_connection: object,
        _capability_id: str,
        _capability_version: str,
    ) -> object:
        return qualification

    return BatchOperationService(
        store=store,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=resolve_qualification,
        sequential_dispatcher=sequential_provider,
        native_batch_dispatcher=native_provider,
        native_batch_qualification=native_batch_qualification,
        audit_recorder=RecordingAudit().record,
        clock=lambda: NOW,
        uuid_factory=uuid4,
    )


def _confirmation(prepared):
    from mercury_tools.execution.hosted.operation_service import DocumentCreateConfirmation

    return DocumentCreateConfirmation(
        preview_id=prepared.preview_id,
        expected_state_version=prepared.state_version,
        confirmation="CONFIRM_CREATE",
    )


def _exact_native_qualification(qualification):
    from mercury_tools.execution.hosted.batch_service import NativeBatchQualification

    return NativeBatchQualification(
        provider=qualification.provider,
        environment=qualification.environment,
        create_capability_id=qualification.normalized_capability,
        create_capability_version=qualification.capability_version_sha256,
        batch_capability_id="documents.invoice.batch_create",
        batch_capability_version="e" * 64,
        provider_tool_name="batch_create_invoice",
        max_batch_size=25,
        response_correlation="request_order",
        duplicate_behavior="idempotent_by_operation_id",
        timeout_semantics="ambiguous_after_possible_dispatch",
        atomicity="atomic",
    )


@pytest.mark.asyncio
async def test_duplicate_client_item_ids_and_payload_hashes_are_rejected_before_preview() -> None:
    with pytest.raises(ValidationError, match="duplicate_client_item_id"):
        BatchDocumentCreate(
            mode="batch",
            documents=(
                _draft(client_item_id="duplicate", reference="INV-BATCH-001"),
                _draft(client_item_id="duplicate", reference="INV-BATCH-002"),
            ),
        )

    preview_service, _, _, qualification, _ = _service(ids=tuple(uuid4() for _ in range(3)))
    duplicate_payloads = BatchDocumentCreate(
        mode="batch",
        documents=(
            _draft(client_item_id="first", reference="INV-SAME"),
            _draft(client_item_id="second", reference="INV-SAME"),
        ),
    )
    with pytest.raises(HostedPreviewError, match="^duplicate_provider_call$"):
        await preview_service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            duplicate_payloads,
        )


@pytest.mark.asyncio
async def test_two_document_success_uses_native_batch_only_with_full_exact_qualification() -> None:
    prepared, store, connection, qualification = await _prepared_batch()
    sequential = RecordingProvider()
    native = RecordingNativeBatchProvider()
    service = _batch_service(
        store=store,
        connection=connection,
        qualification=qualification,
        sequential_provider=sequential,
        native_provider=native,
        native_batch_qualification=_exact_native_qualification(qualification),
    )

    operation = await service.confirm_and_dispatch(
        _principal(), WORKSPACE_ID, _confirmation(prepared)
    )

    assert operation.state == "succeeded"
    assert [item.state for item in operation.items] == ["succeeded", "succeeded"]
    assert [item.provider_result_identifier for item in operation.items] == [
        "native-document-1",
        "native-document-2",
    ]
    assert len(native.calls) == 1
    assert sequential.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "qualification_update",
    [
        {"create_capability_version": "f" * 64},
        {"provider": ProviderId.PEAK},
        {"environment": "production"},
        {"batch_capability_version": "0" * 64},
        {"max_batch_size": 1},
        {"response_correlation": "unknown"},
        {"duplicate_behavior": "unknown"},
        {"timeout_semantics": "unknown"},
        {"atomicity": "non_atomic"},
    ],
)
async def test_incomplete_or_nonexact_native_batch_qualification_uses_deterministic_sequence(
    qualification_update: dict[str, object],
) -> None:
    prepared, store, connection, qualification = await _prepared_batch()
    sequential = RecordingProvider(outcomes=["sequential-first", "sequential-second"])
    native = RecordingNativeBatchProvider()
    native_qualification = _exact_native_qualification(qualification).model_copy(
        update=qualification_update
    )
    service = _batch_service(
        store=store,
        connection=connection,
        qualification=qualification,
        sequential_provider=sequential,
        native_provider=native,
        native_batch_qualification=native_qualification,
    )

    operation = await service.confirm_and_dispatch(
        _principal(), WORKSPACE_ID, _confirmation(prepared)
    )

    assert operation.state == "succeeded"
    assert [call["provider_arguments"]["reference"] for call in sequential.calls] == [
        "INV-BATCH-001",
        "INV-BATCH-002",
    ]
    assert native.calls == []


@pytest.mark.asyncio
async def test_deterministic_rejection_stops_later_children_without_rollback_claim() -> None:
    from mercury_tools.execution.hosted.operation_service import ProviderCreateRejected

    prepared, store, connection, qualification = await _prepared_batch(
        documents=(
            _draft(client_item_id="first", reference="INV-BATCH-001"),
            _draft(client_item_id="second", reference="INV-BATCH-002"),
            _draft(client_item_id="third", reference="INV-BATCH-003"),
        )
    )
    sequential = RecordingProvider(
        outcomes=["already-created-first", ProviderCreateRejected("provider_rejected")]
    )
    service = _batch_service(
        store=store,
        connection=connection,
        qualification=qualification,
        sequential_provider=sequential,
        native_provider=None,
        native_batch_qualification=None,
    )

    operation = await service.confirm_and_dispatch(
        _principal(), WORKSPACE_ID, _confirmation(prepared)
    )

    assert operation.state == "provider_rejected"
    assert [item.state for item in operation.items] == [
        "succeeded",
        "provider_rejected",
        "not_dispatched",
    ]
    assert len(sequential.calls) == 2
    assert all("rollback" not in event.sanitized_reason for event in operation.events)


@pytest.mark.asyncio
async def test_ambiguous_child_stops_later_children_and_is_never_replayed() -> None:
    prepared, store, connection, qualification = await _prepared_batch(
        documents=(
            _draft(client_item_id="first", reference="INV-BATCH-001"),
            _draft(client_item_id="second", reference="INV-BATCH-002"),
            _draft(client_item_id="third", reference="INV-BATCH-003"),
        )
    )
    sequential = RecordingProvider(
        outcomes=[
            "already-created-first",
            ProviderOutcomeUnknown(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.UNKNOWN,
            ),
        ]
    )
    service = _batch_service(
        store=store,
        connection=connection,
        qualification=qualification,
        sequential_provider=sequential,
        native_provider=None,
        native_batch_qualification=None,
    )
    confirmation = _confirmation(prepared)

    unknown = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    repeated = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)

    assert unknown.state == "outcome_unknown"
    assert [item.state for item in unknown.items] == [
        "succeeded",
        "outcome_unknown",
        "not_dispatched",
    ]
    assert repeated.operation_id == unknown.operation_id
    assert len(sequential.calls) == 2
