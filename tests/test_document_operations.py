from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_document_preview import (
    CONNECTION_ID,
    NOW,
    WORKSPACE_ID,
    _draft,
    _membership,
    _principal,
    _service,
)

from mercury_tools.execution.hosted.models import SingleDocumentCreate
from mercury_tools.execution.hosted.store import HostedPreviewError
from mercury_tools.providers.base import (
    DispatchCertainty,
    ProviderOutcomeUnknown,
    ProviderResponseInvalid,
    ProviderTimeoutPreDispatch,
    ProviderUnavailable,
)
from mercury_tools.providers.models import ConnectionReadiness, ProviderId


@dataclass
class RecordingProvider:
    outcomes: list[object] = field(default_factory=lambda: ["provider-document-1"])
    calls: list[dict[str, object]] = field(default_factory=list)
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None

    async def dispatch_create(
        self,
        connection: object,
        qualification: object,
        provider_arguments: dict[str, object],
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
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@dataclass
class RecordingAudit:
    fail: bool = False
    calls: list[object] = field(default_factory=list)

    async def record(self, event: object) -> None:
        self.calls.append(event)
        if self.fail:
            raise RuntimeError("audit insert unavailable")


async def _prepared_preview(*, authority_state: dict[str, object] | None = None):
    preview_service, store, connection, qualification, _ = _service(
        authority_state=authority_state,
        ids=(uuid4(), uuid4()),
    )
    prepared = await preview_service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )
    return prepared, store, connection, qualification


def _operation_service(
    *,
    store: object,
    authority_state: dict[str, object],
    provider: RecordingProvider,
    audit: RecordingAudit,
):
    """The Task 13 service must consume Task 12's real preview/store boundary."""

    from mercury_tools.execution.hosted.operation_service import OperationService

    def resolve_connection(
        _resolved_membership: object,
        _resolved_principal: object,
        _connection_id: UUID,
    ) -> object:
        return authority_state["connection"]

    def resolve_qualification(
        _current_connection: object,
        _capability_id: str,
        _capability_version: str,
    ) -> object:
        return authority_state["qualification"]

    return OperationService(
        store=store,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=resolve_qualification,
        provider_dispatcher=provider,
        audit_recorder=audit.record,
        clock=lambda: NOW,
        uuid_factory=uuid4,
    )


def _confirmation(prepared, *, state_version: int | None = None):
    from mercury_tools.execution.hosted.operation_service import DocumentCreateConfirmation

    return DocumentCreateConfirmation(
        preview_id=prepared.preview_id,
        expected_state_version=state_version or prepared.state_version,
        confirmation="CONFIRM_CREATE",
    )


@pytest.mark.asyncio
async def test_confirmation_is_literal_closed_and_cannot_supply_provider_payload() -> None:
    from mercury_tools.execution.hosted.operation_service import DocumentCreateConfirmation

    prepared, _, _, _ = await _prepared_preview()
    for confirmation in ("confirm_create", "CONFIRM", "CONFIRM_CREATE "):
        with pytest.raises(ValidationError):
            DocumentCreateConfirmation(
                preview_id=prepared.preview_id,
                expected_state_version=prepared.state_version,
                confirmation=confirmation,
            )
    with pytest.raises(ValidationError):
        DocumentCreateConfirmation.model_validate(
            {
                "preview_id": str(prepared.preview_id),
                "expected_state_version": prepared.state_version,
                "confirmation": "CONFIRM_CREATE",
                "provider_arguments": {"reference": "caller-must-not-replace-reviewed-payload"},
            }
        )


@pytest.mark.asyncio
async def test_confirmation_dispatches_immutable_payload_with_operation_idempotency_key() -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    authority_state = {"connection": connection, "qualification": qualification}
    provider = RecordingProvider()
    audit = RecordingAudit()
    service = _operation_service(
        store=store,
        authority_state=authority_state,
        provider=provider,
        audit=audit,
    )

    operation = await service.confirm_and_dispatch(
        _principal(), WORKSPACE_ID, _confirmation(prepared)
    )

    assert operation.state == "succeeded"
    assert operation.items[0].state == "succeeded"
    assert operation.items[0].provider_result_identifier == "provider-document-1"
    assert len(audit.calls) == 1
    assert len(provider.calls) == 1
    assert provider.calls[0]["operation_id"] == operation.operation_id
    assert provider.calls[0]["provider_arguments"] == _draft().provider_arguments_copy()
    assert provider.calls[0]["connection"] == connection
    assert provider.calls[0]["qualification"] == qualification


@pytest.mark.asyncio
async def test_confirmation_requires_current_version_and_task12_bindings() -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    binding_variants = (
        {"connection": connection.model_copy(update={"id": uuid4()})},
        {"connection": connection.model_copy(update={"provider": ProviderId.PEAK})},
        {"connection": connection.model_copy(update={"environment": "production"})},
        {"connection": connection.model_copy(update={"revision": connection.revision + 1})},
        {"connection": connection.model_copy(update={"provider_account_id": "different-company"})},
        {
            "connection": connection.model_copy(
                update={"readiness": ConnectionReadiness.REQUIRES_VALIDATION}
            )
        },
        {"connection": connection.model_copy(update={"granted_permissions": ()})},
        {"qualification": qualification.model_copy(update={"capability_version_sha256": "f" * 64})},
        {"qualification": qualification.model_copy(update={"id": uuid4()})},
        {"qualification": qualification.model_copy(update={"evidence_revision_sha256": "e" * 64})},
    )

    for changed in binding_variants:
        authority_state = {
            "connection": changed.get("connection", connection),
            "qualification": changed.get("qualification", qualification),
        }
        provider = RecordingProvider()
        service = _operation_service(
            store=store,
            authority_state=authority_state,
            provider=provider,
            audit=RecordingAudit(),
        )
        with pytest.raises(HostedPreviewError, match="^preview_binding_changed$"):
            await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, _confirmation(prepared))
        assert provider.calls == []

    authority_state = {"connection": connection, "qualification": qualification}
    provider = RecordingProvider()
    service = _operation_service(
        store=store,
        authority_state=authority_state,
        provider=provider,
        audit=RecordingAudit(),
    )
    with pytest.raises(HostedPreviewError, match="^preview_state_stale$"):
        await service.confirm_and_dispatch(
            _principal(),
            WORKSPACE_ID,
            _confirmation(prepared, state_version=prepared.state_version + 1),
        )
    assert provider.calls == []


@pytest.mark.asyncio
async def test_concurrent_and_repeated_confirmation_create_one_operation_and_one_dispatch() -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    provider = RecordingProvider(started=asyncio.Event(), release=asyncio.Event())
    service = _operation_service(
        store=store,
        authority_state={"connection": connection, "qualification": qualification},
        provider=provider,
        audit=RecordingAudit(),
    )
    confirmation = _confirmation(prepared)

    first = asyncio.create_task(
        service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    )
    await provider.started.wait()
    second = asyncio.create_task(
        service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    )
    provider.release.set()
    first_result, second_result = await asyncio.gather(first, second)
    repeated = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)

    assert first_result.operation_id == second_result.operation_id == repeated.operation_id
    assert repeated.state == "succeeded"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_terminal_replay_returns_existing_result_without_reauthorizing_dispatch() -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    authority_state = {"connection": connection, "qualification": qualification}
    provider = RecordingProvider()
    service = _operation_service(
        store=store,
        authority_state=authority_state,
        provider=provider,
        audit=RecordingAudit(),
    )
    confirmation = _confirmation(prepared)

    succeeded = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    authority_state["connection"] = connection.model_copy(
        update={"revision": connection.revision + 1}
    )
    replayed = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)

    assert replayed == succeeded
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_pre_dispatch_failure_retries_same_operation_and_payload() -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    provider = RecordingProvider(
        outcomes=[
            ProviderTimeoutPreDispatch(
                ProviderId.FLOWACCOUNT,
                dispatch_certainty=DispatchCertainty.NOT_DISPATCHED,
            ),
            "provider-document-after-retry",
        ]
    )
    service = _operation_service(
        store=store,
        authority_state={"connection": connection, "qualification": qualification},
        provider=provider,
        audit=RecordingAudit(),
    )
    confirmation = _confirmation(prepared)

    failed = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    retried = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)

    assert failed.state == "failed_pre_dispatch"
    assert retried.operation_id == failed.operation_id
    assert retried.state == "succeeded"
    assert len(provider.calls) == 2
    assert provider.calls[0]["operation_id"] == provider.calls[1]["operation_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "possible_dispatch_outcome",
    [
        ProviderOutcomeUnknown(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.UNKNOWN,
        ),
        ProviderUnavailable(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.UNKNOWN,
        ),
        ProviderResponseInvalid(
            ProviderId.FLOWACCOUNT,
            dispatch_certainty=DispatchCertainty.UNKNOWN,
        ),
        ConnectionError("transport lost after request write"),
        {"malformed": "provider create response"},
        "x" * 201,
    ],
)
async def test_possible_dispatch_outcomes_are_unknown_and_never_automatically_replayed(
    possible_dispatch_outcome: object,
) -> None:
    prepared, store, connection, qualification = await _prepared_preview()
    provider = RecordingProvider(outcomes=[possible_dispatch_outcome])
    service = _operation_service(
        store=store,
        authority_state={"connection": connection, "qualification": qualification},
        provider=provider,
        audit=RecordingAudit(),
    )
    confirmation = _confirmation(prepared)

    unknown = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)
    repeated = await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, confirmation)

    assert unknown.state == "outcome_unknown"
    assert unknown.items[0].state == "outcome_unknown"
    assert repeated.operation_id == unknown.operation_id
    assert repeated.state == "outcome_unknown"
    assert len(provider.calls) == 1


@pytest.mark.asyncio
async def test_audit_insert_failure_blocks_dispatch_before_provider_boundary() -> None:
    from mercury_tools.execution.hosted.operation_service import DocumentOperationError

    prepared, store, connection, qualification = await _prepared_preview()
    provider = RecordingProvider()
    audit = RecordingAudit(fail=True)
    service = _operation_service(
        store=store,
        authority_state={"connection": connection, "qualification": qualification},
        provider=provider,
        audit=audit,
    )

    with pytest.raises(DocumentOperationError, match="^audit_write_failed$"):
        await service.confirm_and_dispatch(_principal(), WORKSPACE_ID, _confirmation(prepared))

    assert len(audit.calls) == 1
    assert provider.calls == []
