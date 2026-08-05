from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import pytest
from test_document_operations import (
    RecordingAudit,
    RecordingProvider,
    _confirmation,
    _operation_service,
    _prepared_preview,
)
from test_document_preview import NOW, WORKSPACE_ID, _membership, _principal

from mercury_tools.providers.models import ProviderId


@dataclass
class RecordingLookupProvider:
    matches: tuple[str, ...]
    calls: list[dict[str, object]] = field(default_factory=list)

    async def lookup_exact(
        self,
        connection: object,
        lookup_qualification: object,
        *,
        provider_call_hash: str,
    ) -> tuple[str, ...]:
        self.calls.append(
            {
                "connection": connection,
                "lookup_qualification": lookup_qualification,
                "provider_call_hash": provider_call_hash,
            }
        )
        return self.matches


def _recorded_lookup_binding(qualification):
    from mercury_tools.execution.hosted.reconciliation_service import RecordedLookupBinding

    return RecordedLookupBinding(
        provider=qualification.provider,
        environment=qualification.environment,
        create_capability_id=qualification.normalized_capability,
        create_capability_version=qualification.capability_version_sha256,
        lookup_capability_id="documents.invoice.lookup_by_provider_call_hash",
        lookup_capability_version="d" * 64,
        provider_tool_name="lookup_invoice_by_provider_call_hash",
    )


def _lookup_qualification(qualification):
    return qualification.model_copy(
        update={
            "normalized_capability": "documents.invoice.lookup_by_provider_call_hash",
            "capability_version_sha256": "d" * 64,
            "provider_tool_name": "lookup_invoice_by_provider_call_hash",
        }
    )


def _reconciliation_service(
    *,
    store: object,
    connection: object,
    qualification: object,
    lookup_qualification: object,
    lookup_provider: RecordingLookupProvider,
    recorded_binding: object,
):
    from mercury_tools.execution.hosted.reconciliation_service import ReconciliationService

    def resolve_connection(
        _resolved_membership: object,
        _resolved_principal: object,
        _connection_id: object,
    ) -> object:
        return connection

    def resolve_qualification(
        _current_connection: object,
        _capability_id: str,
        _capability_version: str,
    ) -> object:
        return qualification

    def resolve_recorded_binding(_operation: object) -> object:
        return recorded_binding

    def resolve_lookup_qualification(_connection: object, _binding: object) -> object:
        return lookup_qualification

    return ReconciliationService(
        store=store,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=resolve_qualification,
        recorded_lookup_binding_resolver=resolve_recorded_binding,
        lookup_qualification_resolver=resolve_lookup_qualification,
        lookup_provider=lookup_provider,
        audit_recorder=RecordingAudit().record,
        clock=lambda: NOW,
        uuid_factory=uuid4,
    )


async def _unknown_operation():
    from mercury_tools.providers.base import DispatchCertainty, ProviderOutcomeUnknown

    prepared, store, connection, qualification = await _prepared_preview()
    operation_service = _operation_service(
        store=store,
        authority_state={"connection": connection, "qualification": qualification},
        provider=RecordingProvider(
            outcomes=[
                ProviderOutcomeUnknown(
                    ProviderId.FLOWACCOUNT,
                    dispatch_certainty=DispatchCertainty.UNKNOWN,
                )
            ]
        ),
        audit=RecordingAudit(),
    )
    operation = await operation_service.confirm_and_dispatch(
        _principal(), WORKSPACE_ID, _confirmation(prepared)
    )
    assert operation.state == "outcome_unknown"
    return operation, store, connection, qualification


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("matches", "state", "identifier"),
    [
        (("provider-document-1",), "succeeded", "provider-document-1"),
        ((), "outcome_unknown", None),
        (("provider-document-1", "provider-document-2"), "needs_manual_review", None),
    ],
)
async def test_exact_lookup_reconciliation_closes_only_unambiguous_unknown_outcomes(
    matches: tuple[str, ...],
    state: str,
    identifier: str | None,
) -> None:
    operation, store, connection, qualification = await _unknown_operation()
    lookup_provider = RecordingLookupProvider(matches=matches)
    binding = _recorded_lookup_binding(qualification)
    lookup_qualification = _lookup_qualification(qualification)
    service = _reconciliation_service(
        store=store,
        connection=connection,
        qualification=qualification,
        lookup_qualification=lookup_qualification,
        lookup_provider=lookup_provider,
        recorded_binding=binding,
    )

    reconciled = await service.reconcile_outcome(_principal(), WORKSPACE_ID, operation.operation_id)

    assert reconciled.state == state
    assert reconciled.items[0].state == state
    assert reconciled.items[0].provider_result_identifier == identifier
    assert len(lookup_provider.calls) == 1
    assert lookup_provider.calls[0]["provider_call_hash"] == operation.items[0].provider_call_hash
    assert lookup_provider.calls[0]["connection"] == connection
    assert lookup_provider.calls[0]["lookup_qualification"] == lookup_qualification


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "binding_update",
    [
        {"provider": ProviderId.PEAK},
        {"environment": "production"},
        {"create_capability_id": "documents.receipt.create"},
        {"create_capability_version": "0" * 64},
        {"lookup_capability_version": "0" * 64},
    ],
)
async def test_reconciliation_refuses_any_lookup_binding_other_than_the_recorded_exact_version(
    binding_update: dict[str, object],
) -> None:
    from mercury_tools.execution.hosted.reconciliation_service import ReconciliationError

    operation, store, connection, qualification = await _unknown_operation()
    lookup_provider = RecordingLookupProvider(matches=("provider-document-1",))
    recorded = _recorded_lookup_binding(qualification).model_copy(update=binding_update)
    lookup_qualification = _lookup_qualification(qualification)
    service = _reconciliation_service(
        store=store,
        connection=connection,
        qualification=qualification,
        lookup_qualification=lookup_qualification,
        lookup_provider=lookup_provider,
        recorded_binding=recorded,
    )

    with pytest.raises(ReconciliationError, match="^lookup_binding_changed$"):
        await service.reconcile_outcome(_principal(), WORKSPACE_ID, operation.operation_id)

    assert lookup_provider.calls == []


@pytest.mark.asyncio
async def test_reconciliation_only_allows_unknown_operations_and_never_replays_create() -> None:
    from mercury_tools.execution.hosted.reconciliation_service import ReconciliationError

    operation, store, connection, qualification = await _unknown_operation()
    lookup_provider = RecordingLookupProvider(matches=("provider-document-1",))
    lookup_qualification = _lookup_qualification(qualification)
    service = _reconciliation_service(
        store=store,
        connection=connection,
        qualification=qualification,
        lookup_qualification=lookup_qualification,
        lookup_provider=lookup_provider,
        recorded_binding=_recorded_lookup_binding(qualification),
    )
    succeeded = await service.reconcile_outcome(_principal(), WORKSPACE_ID, operation.operation_id)

    with pytest.raises(ReconciliationError, match="^operation_not_reconcilable$"):
        await service.reconcile_outcome(_principal(), WORKSPACE_ID, succeeded.operation_id)

    assert len(lookup_provider.calls) == 1
