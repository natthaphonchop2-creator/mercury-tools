from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from test_document_preview import (
    AUTH_USER_ID,
    CONNECTION_ID,
    NOW,
    PREVIEW_ID,
    SECRET_COUNTERPARTY,
    TENANT_ID,
    WORKSPACE_ID,
    _draft,
    _membership,
    _payload_vault,
    _projector_registry,
    _qualification,
    _service,
)

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260726105000_mercury_v1_operations_previews.sql"
OPERATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
OPERATION_ITEM_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
EVENT_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _tracking_payload_vault():
    from mercury_tools.execution.hosted.store import HostedPayloadVault

    class TrackingHostedPayloadVault(HostedPayloadVault):
        def __init__(self, delegate: object) -> None:
            self.delegate = delegate
            self.open_calls = 0

        def open(self, binding, envelope):
            self.open_calls += 1
            return self.delegate.open(binding, envelope)

    return TrackingHostedPayloadVault(_payload_vault())


async def _prepared():
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    qualification = _qualification()
    service, store, connection, qualification, _ = _service()
    result = await service.prepare_document_create(
        service_test_principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )
    preview = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=result.preview_id,
    )
    return preview, store, connection, qualification


def service_test_principal():
    from mercury_tools.auth.models import MercuryPrincipal

    return MercuryPrincipal(
        subject=AUTH_USER_ID,
        client_id="mercury-test-client",
        scopes=frozenset({"mcp:tools"}),
    )


@pytest.mark.asyncio
async def test_in_memory_store_is_tenant_bound_immutable_and_returns_defensive_models() -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, _, _ = await _prepared()

    with pytest.raises(HostedPreviewError, match="^preview_not_found$"):
        store.get_preview(
            tenant_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=preview.preview_id,
        )
    assert store.create_preview(preview) == preview

    loaded = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
    )
    assert loaded == preview
    assert SECRET_COUNTERPARTY not in loaded.model_dump_json()
    assert SECRET_COUNTERPARTY not in repr(store)


@pytest.mark.asyncio
async def test_confirmable_load_rechecks_state_expiry_connection_catalog_and_payload_hash() -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, connection, qualification = await _prepared()
    confirmed = store.load_confirmable(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
        expected_state_version=1,
        connection=connection,
        qualification=qualification,
        now=NOW,
    )
    assert confirmed.provider_payload_for("client-item-1")["reference"] == "INV-DRAFT-001"
    assert SECRET_COUNTERPARTY not in repr(confirmed)
    assert SECRET_COUNTERPARTY not in confirmed.model_dump_json()

    cases = (
        ({"expected_state_version": 2}, "preview_state_stale"),
        ({"now": preview.expires_at}, "preview_expired"),
        (
            {"connection": connection.model_copy(update={"revision": connection.revision + 1})},
            "preview_binding_changed",
        ),
        (
            {
                "connection": connection.model_copy(
                    update={"provider_account_id": "changed-provider-company"}
                )
            },
            "preview_binding_changed",
        ),
        (
            {
                "qualification": qualification.model_copy(
                    update={"capability_version_sha256": "f" * 64}
                )
            },
            "preview_binding_changed",
        ),
    )
    base = {
        "tenant_id": TENANT_ID,
        "auth_user_id": AUTH_USER_ID,
        "workspace_id": WORKSPACE_ID,
        "preview_id": preview.preview_id,
        "expected_state_version": 1,
        "connection": connection,
        "qualification": qualification,
        "now": NOW,
    }
    for updates, code in cases:
        with pytest.raises(HostedPreviewError, match=f"^{code}$"):
            store.load_confirmable(**{**base, **updates})

    tampered_envelope = preview.items[0].payload_envelope.model_copy(
        update={
            "ciphertext": preview.items[0].payload_envelope.ciphertext[:-1]
            + bytes([preview.items[0].payload_envelope.ciphertext[-1] ^ 1])
        }
    )
    tampered_item = preview.items[0].model_copy(update={"payload_envelope": tampered_envelope})
    tampered = preview.model_copy(update={"items": (tampered_item,)})
    from mercury_tools.execution.hosted.store import InMemoryHostedPreviewStore

    tampered_store = InMemoryHostedPreviewStore(
        payload_vault=_payload_vault(),
        projector_registry=_projector_registry(qualification),
        clock=lambda: NOW,
    )
    tampered_store.create_preview(tampered)
    with pytest.raises(HostedPreviewError, match="^preview_payload_changed$"):
        tampered_store.load_confirmable(
            **{
                **base,
                "preview_id": tampered.preview_id,
            }
        )


@pytest.mark.asyncio
async def test_confirmable_load_rejects_a_preview_with_rebound_expiry() -> None:
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        InMemoryHostedPreviewStore,
    )

    preview, _, connection, qualification = await _prepared()
    shifted_by = timedelta(minutes=1)
    shifted_purge_after = preview.payload_purge_after + shifted_by
    rebound = preview.model_copy(
        update={
            "created_at": preview.created_at + shifted_by,
            "expires_at": preview.expires_at + shifted_by,
            "payload_purge_after": shifted_purge_after,
            "items": tuple(
                item.model_copy(
                    update={
                        "created_at": item.created_at + shifted_by,
                        "payload_purge_after": shifted_purge_after,
                    }
                )
                for item in preview.items
            ),
        }
    )
    rebound_store = InMemoryHostedPreviewStore(
        payload_vault=_payload_vault(),
        projector_registry=_projector_registry(qualification),
        clock=lambda: NOW,
    )
    rebound_store.create_preview(rebound)

    with pytest.raises(HostedPreviewError, match="^preview_payload_changed$"):
        rebound_store.load_confirmable(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=rebound.preview_id,
            expected_state_version=1,
            connection=connection,
            qualification=qualification,
            now=NOW,
        )


def _projector_registry_variant(
    qualification,
    *,
    projector_id: str = "mercury.test.invoice",
    projector_version: str = "c" * 64,
    mismatch_projection: bool = False,
):
    from mercury_tools.execution.hosted.projectors import (
        DocumentProjectorRegistry,
        ProjectedDocument,
        ReviewedInvoiceProjector,
    )

    projector = ReviewedInvoiceProjector(
        projector_id=projector_id,
        projector_version=projector_version,
        provider=qualification.provider,
        environment=qualification.environment,
        provider_tool_name=qualification.provider_tool_name,
        capability_id=qualification.normalized_capability,
        capability_version=qualification.capability_version_sha256,
        schema_hash=qualification.schema_hash,
        currency_minor_units={"THB": 2, "USD": 2},
    )
    if not mismatch_projection:
        return DocumentProjectorRegistry((projector,))

    class MismatchedProjector:
        def __init__(self) -> None:
            self.projector_id = projector.projector_id
            self.projector_version = projector.projector_version
            self.provider = projector.provider
            self.environment = projector.environment
            self.provider_tool_name = projector.provider_tool_name
            self.capability_id = projector.capability_id
            self.capability_version = projector.capability_version
            self.schema_hash = projector.schema_hash

        def matches(self, candidate) -> bool:
            return projector.matches(candidate)

        def project(self, provider_arguments):
            projected = projector.project(provider_arguments)
            return ProjectedDocument(
                document_type=projected.document_type,
                counterparty_display="[REDACTED_COUNTERPARTY_CHANGED]",
                issue_date=projected.issue_date,
                due_date=projected.due_date,
                financials=projected.financials,
            )

    return DocumentProjectorRegistry((MismatchedProjector(),))


def _confirmable_store(
    kind: str,
    *,
    preview,
    projector_registry,
    payload_vault,
):
    from mercury_tools.execution.hosted.store import (
        InMemoryHostedPreviewStore,
        SupabaseHostedPreviewStore,
    )

    if kind == "memory":
        store = InMemoryHostedPreviewStore(
            payload_vault=payload_vault,
            projector_registry=projector_registry,
            clock=lambda: NOW,
        )
        store.create_preview(preview)
        return store, None

    from mercury_tools.config import Settings

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/rpc/load_mercury_document_preview")
        return httpx.Response(
            200,
            json=[
                {
                    "preview": preview.storage_record(),
                    "items": [item.storage_record() for item in preview.items],
                }
            ],
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = SupabaseHostedPreviewStore(
        settings=Settings(
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="test-service-role-key",
            supabase_auth_issuer="https://example.supabase.co/auth/v1",
            openai_api_key="",
        ),
        payload_vault=payload_vault,
        http_client=client,
        projector_registry=projector_registry,
    )
    return store, client


@pytest.mark.parametrize("store_kind", ("memory", "supabase"))
@pytest.mark.parametrize(
    ("projector_id", "projector_version"),
    (
        ("mercury.drifted.invoice", "c" * 64),
        ("mercury.test.invoice", "d" * 64),
    ),
)
@pytest.mark.asyncio
async def test_confirmable_load_rejects_projector_identity_drift_before_payload_open(
    store_kind: str,
    projector_id: str,
    projector_version: str,
) -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, _, connection, qualification = await _prepared()
    tracking_vault = _tracking_payload_vault()
    store, client = _confirmable_store(
        store_kind,
        preview=preview,
        projector_registry=_projector_registry_variant(
            qualification,
            projector_id=projector_id,
            projector_version=projector_version,
        ),
        payload_vault=tracking_vault,
    )
    try:
        with pytest.raises(HostedPreviewError, match="^capability_unavailable$") as raised:
            store.load_confirmable(
                tenant_id=TENANT_ID,
                auth_user_id=AUTH_USER_ID,
                workspace_id=WORKSPACE_ID,
                preview_id=preview.preview_id,
                expected_state_version=preview.state_version,
                connection=connection,
                qualification=qualification,
                now=NOW,
            )
    finally:
        if client is not None:
            client.close()

    assert tracking_vault.open_calls == 0
    assert SECRET_COUNTERPARTY not in str(raised.value)


@pytest.mark.parametrize("store_kind", ("memory", "supabase"))
@pytest.mark.asyncio
async def test_confirmable_load_rejects_same_identity_reprojection_mismatch(
    store_kind: str,
) -> None:
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, _, connection, qualification = await _prepared()
    tracking_vault = _tracking_payload_vault()
    store, client = _confirmable_store(
        store_kind,
        preview=preview,
        projector_registry=_projector_registry_variant(
            qualification,
            mismatch_projection=True,
        ),
        payload_vault=tracking_vault,
    )
    try:
        with pytest.raises(HostedPreviewError, match="^preview_payload_changed$") as raised:
            store.load_confirmable(
                tenant_id=TENANT_ID,
                auth_user_id=AUTH_USER_ID,
                workspace_id=WORKSPACE_ID,
                preview_id=preview.preview_id,
                expected_state_version=preview.state_version,
                connection=connection,
                qualification=qualification,
                now=NOW,
            )
    finally:
        if client is not None:
            client.close()

    assert tracking_vault.open_calls == 1
    assert "INV-DRAFT-001" not in str(raised.value)
    assert SECRET_COUNTERPARTY not in str(raised.value)


@pytest.mark.asyncio
async def test_prepare_normalizes_connection_resolver_errors_without_leaking_values() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.preview_service import HostedPreviewService
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        InMemoryHostedPreviewStore,
    )

    qualification = _qualification()
    payload_vault = _payload_vault()
    store = InMemoryHostedPreviewStore(payload_vault=payload_vault, clock=lambda: NOW)

    def resolve_connection(*_args: object) -> None:
        raise RuntimeError(SECRET_COUNTERPARTY)

    service = HostedPreviewService(
        store=store,
        payload_vault=payload_vault,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=lambda *_args: qualification,
        clock=lambda: NOW,
    )

    with pytest.raises(HostedPreviewError, match="^preview_binding_changed$") as raised:
        await service.prepare_document_create(
            service_test_principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )

    assert SECRET_COUNTERPARTY not in str(raised.value)


@pytest.mark.asyncio
async def test_operation_store_persists_versioned_transitions_and_retention_metadata() -> None:
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationItemState,
        ParentOperationState,
        PreviewState,
    )
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, _, _ = await _prepared()
    operation = HostedOperation.from_preview(
        preview,
        operation_id=OPERATION_ID,
        operation_item_ids=(OPERATION_ITEM_ID,),
        event_id=EVENT_ID,
        now=NOW,
    )
    stored = store.create_operation(operation)
    repeated = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            operation_item_ids=(UUID("14141414-1414-4414-8414-141414141414"),),
            event_id=UUID("15151515-1515-4515-8515-151515151515"),
            now=NOW,
        )
    )

    assert stored.preview_id == preview.preview_id
    assert repeated.operation_id == stored.operation_id
    assert stored.state is ParentOperationState.AWAITING_CONFIRMATION
    assert stored.state_version == 1
    assert stored.payload_purge_after == NOW + timedelta(days=30)
    assert len(stored.items) == 1
    assert len(stored.events) == 1
    confirmed_preview = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=preview.preview_id,
    )
    assert confirmed_preview.state is PreviewState.CONFIRMED
    assert confirmed_preview.state_version == 2

    dispatching = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=OPERATION_ID,
        expected_state_version=1,
        target_state=ParentOperationState.DISPATCHING,
        event_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
        occurred_at=NOW + timedelta(milliseconds=250),
        sanitized_reason="explicit_confirmation",
    )
    assert dispatching.state is ParentOperationState.DISPATCHING
    assert dispatching.state_version == 2
    assert dispatching.events[-1].from_state == ParentOperationState.AWAITING_CONFIRMATION.value
    assert dispatching.events[-1].to_state == ParentOperationState.DISPATCHING.value

    item_transition = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=OPERATION_ID,
        operation_item_id=OPERATION_ITEM_ID,
        expected_state_version=1,
        target_state=OperationItemState.DISPATCHING,
        event_id=UUID("12121212-1212-4212-8212-121212121212"),
        occurred_at=NOW + timedelta(milliseconds=500),
        sanitized_reason="provider_create_started",
    )
    assert item_transition.items[0].state is OperationItemState.DISPATCHING
    assert item_transition.items[0].state_version == 2
    assert item_transition.events[-1].operation_item_id == OPERATION_ITEM_ID

    with pytest.raises(HostedPreviewError, match="^operation_state_stale$"):
        store.transition_operation_item(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            operation_item_id=OPERATION_ITEM_ID,
            expected_state_version=1,
            target_state=OperationItemState.SUCCEEDED,
            event_id=UUID("13131313-1313-4313-8313-131313131313"),
            occurred_at=NOW + timedelta(milliseconds=750),
            sanitized_reason="provider_succeeded",
        )

    with pytest.raises(HostedPreviewError, match="^operation_state_stale$"):
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=OPERATION_ID,
            expected_state_version=1,
            target_state=ParentOperationState.SUCCEEDED,
            event_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            occurred_at=NOW + timedelta(seconds=2),
            sanitized_reason="provider_succeeded",
        )


@pytest.mark.parametrize(
    ("item_target", "parent_target", "provider_result_identifier"),
    (
        ("succeeded", "succeeded", "document-1"),
        ("needs_manual_review", "needs_manual_review", None),
    ),
)
@pytest.mark.asyncio
async def test_unknown_outcome_recovery_transitions_item_before_parent(
    item_target: str,
    parent_target: str,
    provider_result_identifier: str | None,
) -> None:
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationItemState,
        ParentOperationState,
    )

    preview, store, _, _ = await _prepared()
    operation = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=OPERATION_ID,
            operation_item_ids=(OPERATION_ITEM_ID,),
            event_id=EVENT_ID,
            now=NOW,
        )
    )
    dispatching = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        expected_state_version=1,
        target_state=ParentOperationState.DISPATCHING,
        event_id=UUID("21212121-2121-4212-8212-212121212121"),
        occurred_at=NOW + timedelta(seconds=1),
        sanitized_reason="explicit_confirmation",
    )
    item_dispatching = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=1,
        target_state=OperationItemState.DISPATCHING,
        event_id=UUID("22222222-2222-4222-8222-222222222223"),
        occurred_at=NOW + timedelta(seconds=2),
        sanitized_reason="provider_create_started",
    )
    item_unknown = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=item_dispatching.items[0].state_version,
        target_state=OperationItemState.OUTCOME_UNKNOWN,
        event_id=UUID("23232323-2323-4232-8232-232323232323"),
        occurred_at=NOW + timedelta(seconds=3),
        sanitized_reason="provider_outcome_unknown",
    )
    parent_unknown = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        expected_state_version=dispatching.state_version,
        target_state=ParentOperationState.OUTCOME_UNKNOWN,
        event_id=UUID("24242424-2424-4242-8242-242424242424"),
        occurred_at=NOW + timedelta(seconds=4),
        sanitized_reason="provider_outcome_unknown",
    )
    recovered_item = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=item_unknown.items[0].state_version,
        target_state=OperationItemState(item_target),
        event_id=UUID("25252525-2525-4252-8252-252525252525"),
        occurred_at=NOW + timedelta(seconds=5),
        sanitized_reason="provider_reconciled",
        provider_result_identifier=provider_result_identifier,
    )
    recovered_parent = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        expected_state_version=parent_unknown.state_version,
        target_state=ParentOperationState(parent_target),
        event_id=UUID("26262626-2626-4262-8262-262626262626"),
        occurred_at=NOW + timedelta(seconds=6),
        sanitized_reason="provider_reconciled",
    )

    assert recovered_item.state is ParentOperationState.OUTCOME_UNKNOWN
    assert recovered_item.items[0].state is OperationItemState(item_target)
    assert recovered_parent.state is ParentOperationState(parent_target)


@pytest.mark.parametrize(
    ("terminal_state", "item_state"),
    (
        ("cancelled", "cancelled"),
        ("expired", "expired"),
    ),
)
@pytest.mark.asyncio
async def test_parent_terminal_transition_requires_children_to_close_first(
    terminal_state: str,
    item_state: str,
) -> None:
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationItemState,
        ParentOperationState,
    )
    from mercury_tools.execution.hosted.store import HostedPreviewError

    preview, store, _, _ = await _prepared()
    operation = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=OPERATION_ID,
            operation_item_ids=(OPERATION_ITEM_ID,),
            event_id=EVENT_ID,
            now=NOW,
        )
    )

    with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$"):
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=operation.operation_id,
            expected_state_version=operation.state_version,
            target_state=ParentOperationState(terminal_state),
            event_id=UUID("27272727-2727-4272-8272-272727272727"),
            occurred_at=NOW + timedelta(seconds=1),
            sanitized_reason="operation_closed",
        )

    item_closed = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=operation.items[0].state_version,
        target_state=OperationItemState(item_state),
        event_id=UUID("28282828-2828-4282-8282-282828282828"),
        occurred_at=NOW + timedelta(seconds=2),
        sanitized_reason="operation_closed",
    )
    parent_closed = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        expected_state_version=operation.state_version,
        target_state=ParentOperationState(terminal_state),
        event_id=UUID("29292929-2929-4292-8292-292929292929"),
        occurred_at=NOW + timedelta(seconds=3),
        sanitized_reason="operation_closed",
    )

    assert item_closed.items[0].state is OperationItemState(item_state)
    assert parent_closed.state is ParentOperationState(terminal_state)


@pytest.mark.parametrize("child_state", ("cancelled", "expired"))
def test_prepared_parent_accepts_child_closure_before_terminal_transition(
    child_state: str,
) -> None:
    from mercury_tools.execution.hosted.models import (
        OperationItemState,
        ParentOperationState,
        parent_operation_children_compatible,
    )

    assert parent_operation_children_compatible(
        ParentOperationState.PREPARED,
        (OperationItemState(child_state),),
    )


def test_failed_pre_dispatch_closes_undispatched_children_before_parent() -> None:
    from mercury_tools.execution.hosted.models import (
        OperationItemState,
        ParentOperationState,
        item_operation_transition_allowed,
        parent_operation_children_compatible,
        parent_operation_transition_allowed,
    )

    child_states = (
        OperationItemState.FAILED_PRE_DISPATCH,
        OperationItemState.NOT_DISPATCHED,
    )
    assert item_operation_transition_allowed(
        OperationItemState.AWAITING_CONFIRMATION,
        OperationItemState.NOT_DISPATCHED,
        parent_state=ParentOperationState.AWAITING_CONFIRMATION,
    )
    assert parent_operation_children_compatible(
        ParentOperationState.AWAITING_CONFIRMATION,
        child_states,
    )
    assert parent_operation_transition_allowed(
        ParentOperationState.AWAITING_CONFIRMATION,
        ParentOperationState.FAILED_PRE_DISPATCH,
        child_states=child_states,
    )


@pytest.mark.asyncio
async def test_operation_public_surfaces_sanitize_provider_result_identifiers() -> None:
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationItemState,
        ParentOperationState,
    )
    from mercury_tools.execution.hosted.store import operation_rpc_payload

    raw_email = "jane@example.com"
    raw_tax_id = "1234567890123"
    raw_phone = "081-234-5678"
    raw_token = "".join(("sk", "-", "qrstuvwxyz", "123456"))
    raw_bearer = "Bearer provider-secret-value"
    raw_identifier = f"Result {raw_email} {raw_tax_id} {raw_phone} {raw_token} {raw_bearer}"
    preview, store, _, _ = await _prepared()
    operation = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=OPERATION_ID,
            operation_item_ids=(OPERATION_ITEM_ID,),
            event_id=EVENT_ID,
            now=NOW,
        )
    )
    store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        expected_state_version=operation.state_version,
        target_state=ParentOperationState.DISPATCHING,
        event_id=UUID("30303030-3030-4030-8030-303030303030"),
        occurred_at=NOW + timedelta(seconds=1),
        sanitized_reason="explicit_confirmation",
    )
    item_dispatching = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=operation.items[0].state_version,
        target_state=OperationItemState.DISPATCHING,
        event_id=UUID("31313131-3131-4131-8131-313131313131"),
        occurred_at=NOW + timedelta(seconds=2),
        sanitized_reason="provider_create_started",
    )
    succeeded = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=operation.operation_id,
        operation_item_id=operation.items[0].operation_item_id,
        expected_state_version=item_dispatching.items[0].state_version,
        target_state=OperationItemState.SUCCEEDED,
        event_id=UUID("32323232-3232-4232-8232-323232323232"),
        occurred_at=NOW + timedelta(seconds=3),
        sanitized_reason="provider_succeeded",
        provider_result_identifier=raw_identifier,
    )
    public_surfaces = "\n".join(
        (
            repr(succeeded),
            succeeded.model_dump_json(),
            json.dumps(operation_rpc_payload(succeeded), sort_keys=True),
        )
    )

    for raw in (raw_email, raw_tax_id, raw_phone, raw_token, "provider-secret-value"):
        assert raw not in public_surfaces
    assert "[REDACTED_" in public_surfaces


@pytest.mark.asyncio
async def test_operation_reason_rejects_secret_like_identifiers_without_error_echo() -> None:
    from pydantic import ValidationError

    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationEvent,
        ParentOperationState,
    )
    from mercury_tools.execution.hosted.store import HostedPreviewError

    raw_reason = "".join(("sk", "-", "qrstuvwxyz", "123456"))
    preview, store, _, _ = await _prepared()
    operation = store.create_operation(
        HostedOperation.from_preview(
            preview,
            operation_id=OPERATION_ID,
            operation_item_ids=(OPERATION_ITEM_ID,),
            event_id=EVENT_ID,
            now=NOW,
        )
    )

    with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$") as raised:
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=operation.operation_id,
            expected_state_version=operation.state_version,
            target_state=ParentOperationState.DISPATCHING,
            event_id=UUID("33333333-3333-4333-8333-333333333334"),
            occurred_at=NOW + timedelta(seconds=1),
            sanitized_reason=raw_reason,
        )
    assert raw_reason not in str(raised.value)

    with pytest.raises(ValidationError) as validation:
        OperationEvent(
            event_id=UUID("34343434-3434-4434-8434-343434343434"),
            operation_id=operation.operation_id,
            from_state="awaiting_confirmation",
            to_state="dispatching",
            state_version=2,
            sanitized_reason=raw_reason,
            occurred_at=NOW + timedelta(seconds=1),
        )
    assert raw_reason not in str(validation.value)


def test_supabase_operation_requests_sanitize_before_postgrest() -> None:
    from mercury_tools.config import Settings
    from mercury_tools.execution.hosted.models import (
        OperationItemState,
        ParentOperationState,
    )
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        SupabaseHostedPreviewStore,
    )

    raw_email = "jane@example.com"
    raw_tax_id = "1234567890123"
    raw_phone = "081-234-5678"
    raw_token = "".join(("sk", "-", "qrstuvwxyz", "123456"))
    raw_identifier = f"{raw_email} {raw_tax_id} {raw_phone} {raw_token}"
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(400, json={"message": "operation_transition_invalid"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseHostedPreviewStore(
            settings=Settings(
                supabase_url="https://example.supabase.co",
                supabase_service_role_key="test-service-role-key",
                supabase_auth_issuer="https://example.supabase.co/auth/v1",
                openai_api_key="",
            ),
            payload_vault=_payload_vault(),
            http_client=client,
        )
        with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$"):
            store.transition_operation_item(
                tenant_id=TENANT_ID,
                auth_user_id=AUTH_USER_ID,
                workspace_id=WORKSPACE_ID,
                operation_id=OPERATION_ID,
                operation_item_id=OPERATION_ITEM_ID,
                expected_state_version=3,
                target_state=OperationItemState.SUCCEEDED,
                event_id=EVENT_ID,
                occurred_at=NOW,
                sanitized_reason="provider_succeeded",
                provider_result_identifier=raw_identifier,
            )
        item_payload = json.loads(requests[-1].content)
        serialized_item_payload = json.dumps(item_payload, sort_keys=True)
        for raw in (raw_email, raw_tax_id, raw_phone, raw_token):
            assert raw not in serialized_item_payload
        assert "[REDACTED_" in serialized_item_payload

        request_count = len(requests)
        with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$"):
            store.transition_operation(
                tenant_id=TENANT_ID,
                auth_user_id=AUTH_USER_ID,
                workspace_id=WORKSPACE_ID,
                operation_id=OPERATION_ID,
                expected_state_version=1,
                target_state=ParentOperationState.DISPATCHING,
                event_id=EVENT_ID,
                occurred_at=NOW,
                sanitized_reason=raw_token,
            )
        assert len(requests) == request_count


def _rpc_preview_response(payload: dict[str, object]) -> list[dict[str, object]]:
    preview = dict(payload["p_preview"])
    items = list(payload["p_items"])
    return [{"preview": preview, "items": items}]


@pytest.mark.asyncio
async def test_supabase_store_uses_narrow_rpc_and_never_sends_plaintext_payload() -> None:
    from mercury_tools.config import Settings
    from mercury_tools.execution.hosted.store import SupabaseHostedPreviewStore

    preview, _, _, _ = await _prepared()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        if request.url.path.endswith("/rpc/save_mercury_document_preview"):
            return httpx.Response(200, json=_rpc_preview_response(payload))
        if request.url.path.endswith("/rpc/load_mercury_document_preview"):
            return httpx.Response(
                200,
                json=[
                    {
                        "preview": preview.storage_record(),
                        "items": [item.storage_record() for item in preview.items],
                    }
                ],
            )
        return httpx.Response(404, json={"message": "not_found"})

    settings = Settings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key="test-service-role-key",
        supabase_auth_issuer="https://example.supabase.co/auth/v1",
        openai_api_key="",
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        store = SupabaseHostedPreviewStore(
            settings=settings,
            payload_vault=_payload_vault(),
            http_client=client,
        )
        saved = store.create_preview(preview)
        loaded = store.get_preview(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            preview_id=PREVIEW_ID,
        )

    assert saved == preview
    assert loaded == preview
    assert [request.url.path for request in requests] == [
        "/rest/v1/rpc/save_mercury_document_preview",
        "/rest/v1/rpc/load_mercury_document_preview",
    ]
    serialized_requests = b"\n".join(request.content for request in requests).decode("utf-8")
    assert SECRET_COUNTERPARTY not in serialized_requests
    assert "payload_ciphertext" in serialized_requests
    assert requests[0].headers["authorization"] == "Bearer test-service-role-key"


def test_migration_is_expand_only_rls_bound_and_serializes_state_transitions() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    compact = " ".join(sql.lower().split())

    assert not re.search(r"\b(drop|truncate)\s+(table|column)\b", compact)
    for table in (
        "mercury_document_previews",
        "mercury_preview_items",
        "mercury_operations",
        "mercury_operation_items",
        "mercury_operation_events",
    ):
        assert f"create table if not exists public.{table}" in compact
        assert f"alter table public.{table} enable row level security" in compact
        assert f"grant all on table public.{table} to service_role" in compact

    assert "unique (workspace_id, connection_id, provider_call_hash)" in compact
    assert "unique (preview_id, client_item_id)" in compact
    assert "unique (preview_id, provider_call_hash)" in compact
    assert "preview_integrity_hash" in compact
    assert "projector_id" in compact
    assert "projector_version" in compact
    assert "payload_ciphertext" in compact
    assert "payload_envelope_created_at" in compact
    assert "sanitized_summary" in compact
    assert "payload_purge_after" in compact
    assert "mercury_preview_payload_purge_idx" in compact
    assert "mercury_operation_payload_purge_idx" in compact
    assert "for update" in compact
    assert "state_version = state_version + 1" in compact
    assert "p_expected_state_version" in compact
    assert "p_expected_preview_state_version" in compact
    assert "transition_mercury_operation_item" in compact
    assert "mercury_parent_operation_transition_is_allowed" in compact
    assert "mercury_preview_authority_is_current" in compact
    assert "mercury_create_schema_is_closed" in compact
    assert "mercury_assert_provider_backend_workspace_access" in compact
    assert "failed_pre_dispatch" in compact
    assert "provider_rejected" in compact
    assert "public.digest" in compact
    assert "pg_catalog.digest" not in compact
    assert "member.tenant_id = tenant_id" not in compact
    assert "jsonb_object_keys" in compact
    assert "update public.mercury_preview_items" in compact
    assert "expires_at = created_at + pg_catalog.make_interval(secs => 1800)" in compact
    assert "payload_purge_after = expires_at + pg_catalog.make_interval(hours => 24)" in compact
    assert "payload_purge_after <= created_at + pg_catalog.make_interval(days => 30)" in compact


def test_hosted_modules_do_not_import_local_repository_sqlite_or_local_ttl_state() -> None:
    hosted = ROOT / "src/mercury_tools/execution/hosted"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(hosted.glob("*.py"))
        if path.name != "read_service.py"
    )
    for forbidden in (
        "RepositoryContext",
        "LocalRequestStore",
        "sqlite3",
        "mercury_tools.local",
    ):
        assert forbidden not in text

    assert "from mercury_tools.execution.models import PREVIEW_TTL" not in text

    from mercury_tools.execution.models import PREVIEW_TTL

    assert timedelta(minutes=15) == PREVIEW_TTL
