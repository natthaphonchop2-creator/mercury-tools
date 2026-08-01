from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from test_document_preview import (
    AUTH_USER_ID,
    CONNECTION_ID,
    NOW,
    PREVIEW_ID,
    TENANT_ID,
    WORKSPACE_ID,
    _connection,
    _draft,
    _principal,
    _qualification,
    _service,
)

ROOT = Path(__file__).resolve().parents[1]


def test_clean_hosted_imports_do_not_load_the_local_execution_stack() -> None:
    probe = r"""
import importlib
import json
import sys

for module in (
    "mercury_tools.execution.hosted.models",
    "mercury_tools.execution.hosted.projectors",
    "mercury_tools.execution.hosted.store",
    "mercury_tools.execution.hosted.preview_service",
):
    importlib.import_module(module)

forbidden = (
    "sqlite3",
    "mercury_tools.execution.executor",
    "mercury_tools.execution.store",
    "mercury_tools.local.repository",
    "mercury_tools.local.credentials",
    "mercury_tools.local.audit",
    "mercury_tools.local.operation_lock",
)
print(json.dumps([name for name in forbidden if name in sys.modules]))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == []


def test_draft_accepts_only_provider_arguments_and_safe_review_codes() -> None:
    from mercury_tools.execution.hosted.models import DocumentCreateDraft

    with pytest.raises(ValidationError) as raised:
        DocumentCreateDraft(
            client_item_id="client-item-1",
            provider_arguments=_draft().provider_arguments_copy(),
            document_type="invoice",
            counterparty_display="caller controlled",
            issue_date="2026-07-31",
            due_date="2026-08-30",
            financials={"grand_total": "1.00"},
        )

    assert "caller controlled" not in str(raised.value)


@pytest.mark.asyncio
async def test_exact_projector_derives_every_displayed_business_field() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    qualification = _qualification()
    service, _, _, _, _ = _service(qualification=qualification)
    result = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )

    projected = result.items[0]
    assert projected.document_type == "invoice"
    assert projected.counterparty_display == "[REDACTED_COUNTERPARTY]"
    assert projected.issue_date.isoformat() == "2026-07-31"
    assert projected.due_date.isoformat() == "2026-08-30"
    assert projected.financials.currency == "THB"
    assert projected.financials.lines[0].quantity == 2
    assert projected.financials.lines[0].unit_price == 100
    assert projected.financials.lines[0].discount_amount == 10
    assert projected.financials.lines[0].vat_amount == Decimal("13.30")
    assert projected.financials.lines[0].withholding_amount == Decimal("5.70")
    assert projected.financials.grand_total == Decimal("197.60")


@pytest.mark.asyncio
async def test_prepare_fails_closed_without_an_exact_reviewed_projector() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.projectors import DocumentProjectorRegistry
    from mercury_tools.execution.hosted.store import HostedPreviewError

    qualification = _qualification()
    service, _, _, _, _ = _service(
        qualification=qualification,
        projector_registry=DocumentProjectorRegistry(()),
    )

    with pytest.raises(HostedPreviewError, match="^capability_unreviewed$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )


@pytest.mark.parametrize(
    "schema",
    [
        {},
        {
            "type": "object",
            "properties": {"payload": {}},
            "required": ["payload"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"payload": True},
            "required": ["payload"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"payload": {"$ref": "#/$defs/payload"}},
            "required": ["payload"],
            "additionalProperties": False,
        },
    ],
)
@pytest.mark.asyncio
async def test_prepare_rejects_unconstrained_or_unresolved_create_schemas(
    schema: dict[str, object],
) -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    qualification = _qualification(input_schema=schema)
    service, _, _, _, _ = _service(qualification=qualification)

    with pytest.raises(HostedPreviewError, match="^capability_unavailable$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )


@pytest.mark.asyncio
async def test_prepare_requires_a_durable_qualification_identity() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    qualification = _qualification().model_copy(update={"id": None})
    service, _, _, _, _ = _service(qualification=qualification)

    with pytest.raises(HostedPreviewError, match="^capability_unavailable$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )


@pytest.mark.asyncio
async def test_provider_arguments_reject_json_floats_and_apply_explicit_rounding() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    qualification = _qualification()
    service, _, _, _, _ = _service(qualification=qualification)
    float_lines = _draft().provider_arguments_copy()["lines"]
    float_lines[0]["quantity"] = 2.0

    with pytest.raises(HostedPreviewError, match="^document_schema_invalid$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(
                mode="single",
                document=_draft(provider_updates={"lines": float_lines}),
            ),
        )

    rounded_lines = _draft().provider_arguments_copy()["lines"]
    rounded_lines[0].update(
        {
            "quantity": "1",
            "unit_price": "0.05",
            "discount_amount": "0",
            "vat_rate": "7",
            "vat_amount": "0.00",
            "withholding_rate": "0",
            "withholding_amount": "0.00",
            "line_total": "0.05",
        }
    )
    rounded = _draft(
        provider_updates={
            "lines": rounded_lines,
            "subtotal": "0.05",
            "discount_total": "0",
            "vat_total": "0.00",
            "withholding_tax_total": "0.00",
            "grand_total": "0.05",
        }
    )
    result = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=rounded),
    )
    assert result.items[0].financials.vat_total == 0
    assert result.items[0].financials.grand_total == Decimal("0.05")


@pytest.mark.asyncio
async def test_provider_call_and_preview_integrity_have_separate_identities() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    qualification = _qualification()
    first_service, first_store, _, _, _ = _service(qualification=qualification)
    second_service, second_store, _, _, _ = _service(qualification=qualification)
    first = await first_service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft(client_item_id="first")),
    )
    second = await second_service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(
            mode="single",
            document=_draft(
                client_item_id="second",
                warnings=("different_warning",),
                accountant_review_points=("different_review",),
            ),
        ),
    )
    first_stored = first_store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=first.preview_id,
    )
    second_stored = second_store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=second.preview_id,
    )

    assert first_stored.items[0].provider_call_hash == second_stored.items[0].provider_call_hash
    assert (
        first_stored.items[0].preview_integrity_hash
        != second_stored.items[0].preview_integrity_hash
    )


@pytest.mark.asyncio
async def test_public_preview_surfaces_are_server_sanitized() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    raw_email = "jane@example.com"
    raw_tax_id = "1234567890123"
    raw_phone = "081-234-5678"
    raw_token = "sk-abcdefghijklmnop"
    adversarial = f"Jane Doe {raw_email} {raw_tax_id} {raw_phone} {raw_token}"
    connection = _connection(account_display_name=adversarial)
    qualification = _qualification()
    service, store, connection, qualification, _ = _service(
        connection=connection,
        qualification=qualification,
    )
    lines = _draft().provider_arguments_copy()["lines"]
    lines[0]["description"] = adversarial
    result = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(
            mode="single",
            document=_draft(
                provider_updates={
                    "counterparty_name": adversarial,
                    "lines": lines,
                }
            ),
        ),
    )
    stored = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=result.preview_id,
    )
    confirmable = store.load_confirmable(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=result.preview_id,
        expected_state_version=1,
        connection=connection,
        qualification=qualification,
        now=NOW,
    )
    public_surfaces = "\n".join(
        (
            result.model_dump_json(),
            repr(result),
            repr(stored),
            json.dumps(stored.storage_record(), sort_keys=True),
            json.dumps(stored.items[0].public_record(), sort_keys=True),
            confirmable.model_dump_json(),
            repr(confirmable),
        )
    )

    for raw in (raw_email, raw_tax_id, raw_phone, raw_token):
        assert raw not in public_surfaces
    assert "[REDACTED_" in public_surfaces

    with pytest.raises(ValidationError) as raised:
        _draft(client_item_id=raw_phone)
    assert raw_phone not in str(raised.value)


class _StoreProxy:
    def __init__(self, delegate: object) -> None:
        self.delegate = delegate

    def __getattr__(self, name: str):
        return getattr(self.delegate, name)


@pytest.mark.asyncio
async def test_synchronous_persistence_is_offloaded_from_the_event_loop() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import InMemoryHostedPreviewStore

    class SlowStore(_StoreProxy):
        def create_preview(self, preview):
            time.sleep(0.15)
            return self.delegate.create_preview(preview)

    def store_factory(payload_vault, registry, authority):
        delegate = InMemoryHostedPreviewStore(
            payload_vault=payload_vault,
            projector_registry=registry,
            authority_resolver=lambda _preview: (
                authority["connection"],
                authority["qualification"],
            ),
            clock=lambda: NOW,
        )
        return SlowStore(delegate)

    qualification = _qualification()
    service, _, _, _, _ = _service(
        qualification=qualification,
        store_factory=store_factory,
    )
    started = asyncio.get_running_loop().time()
    prepare = asyncio.create_task(
        service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=_draft()),
        )
    )
    await asyncio.sleep(0.02)
    heartbeat_elapsed = asyncio.get_running_loop().time() - started

    assert heartbeat_elapsed < 0.1
    assert not prepare.done()
    await prepare


@pytest.mark.asyncio
async def test_ambiguous_save_recovers_the_identical_committed_preview() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import (
        HostedPreviewError,
        InMemoryHostedPreviewStore,
    )

    class AmbiguousStore(_StoreProxy):
        create_calls = 0

        def create_preview(self, preview):
            self.create_calls += 1
            self.delegate.create_preview(preview)
            raise HostedPreviewError("preview_store_unavailable")

    proxy: AmbiguousStore | None = None

    def store_factory(payload_vault, registry, authority):
        nonlocal proxy
        delegate = InMemoryHostedPreviewStore(
            payload_vault=payload_vault,
            projector_registry=registry,
            authority_resolver=lambda _preview: (
                authority["connection"],
                authority["qualification"],
            ),
            clock=lambda: NOW,
        )
        proxy = AmbiguousStore(delegate)
        return proxy

    qualification = _qualification()
    service, _, _, _, _ = _service(
        qualification=qualification,
        ids=(
            PREVIEW_ID,
            UUID("77777777-7777-4777-8777-777777777777"),
            UUID("88888888-8888-4888-8888-888888888888"),
            UUID("99999999-9999-4999-8999-999999999999"),
        ),
        store_factory=store_factory,
    )
    request = SingleDocumentCreate(mode="single", document=_draft())
    first = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        request,
    )
    repeated = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        request,
    )

    assert first.preview_id == PREVIEW_ID
    assert repeated == first
    assert proxy is not None
    assert proxy.create_calls == 1


@pytest.mark.asyncio
async def test_operation_boundaries_recheck_authority_and_enforce_parent_item_order() -> None:
    from mercury_tools.catalog.models import QualificationState
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationItemState,
        ParentOperationState,
        SingleDocumentCreate,
    )
    from mercury_tools.execution.hosted.store import HostedPreviewError

    qualification = _qualification()
    connection = _connection()
    authority: dict[str, object] = {
        "connection": connection,
        "qualification": qualification,
    }
    service, store, _, _, _ = _service(
        connection=connection,
        qualification=qualification,
        authority_state=authority,
    )
    prepared = await service.prepare_document_create(
        _principal(),
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
        preview_id=prepared.preview_id,
    )
    operation = HostedOperation.from_preview(
        preview,
        operation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        operation_item_ids=(UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),),
        event_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        now=NOW,
    )

    authority["connection"] = connection.model_copy(update={"revision": 8})
    with pytest.raises(HostedPreviewError, match="^preview_binding_changed$"):
        store.create_operation(operation)
    authority["connection"] = connection
    authority["qualification"] = qualification.model_copy(
        update={
            "qualification_state": QualificationState.DISABLED,
            "disable_reason": "reviewed",
        }
    )
    with pytest.raises(HostedPreviewError, match="^capability_unavailable$"):
        store.create_operation(operation)
    authority["qualification"] = qualification
    stored = store.create_operation(operation)

    with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$"):
        store.transition_operation_item(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=stored.operation_id,
            operation_item_id=stored.items[0].operation_item_id,
            expected_state_version=1,
            target_state=OperationItemState.DISPATCHING,
            event_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            occurred_at=NOW + timedelta(seconds=1),
            sanitized_reason="provider_create_started",
        )
    with pytest.raises(HostedPreviewError, match="^operation_transition_invalid$"):
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=stored.operation_id,
            expected_state_version=1,
            target_state=ParentOperationState.SUCCEEDED,
            event_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            occurred_at=NOW + timedelta(seconds=1),
            sanitized_reason="provider_succeeded",
        )

    authority["qualification"] = qualification.model_copy(
        update={
            "qualification_state": QualificationState.DISABLED,
            "disable_reason": "reviewed",
        }
    )
    with pytest.raises(HostedPreviewError, match="^capability_unavailable$"):
        store.transition_operation(
            tenant_id=TENANT_ID,
            auth_user_id=AUTH_USER_ID,
            workspace_id=WORKSPACE_ID,
            operation_id=stored.operation_id,
            expected_state_version=1,
            target_state=ParentOperationState.DISPATCHING,
            event_id=UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
            occurred_at=NOW + timedelta(seconds=1),
            sanitized_reason="explicit_confirmation",
        )

    authority["qualification"] = qualification
    dispatching = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=stored.operation_id,
        expected_state_version=1,
        target_state=ParentOperationState.DISPATCHING,
        event_id=UUID("11111111-2222-4333-8444-555555555555"),
        occurred_at=NOW + timedelta(seconds=1),
        sanitized_reason="explicit_confirmation",
    )
    item_dispatching = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=stored.operation_id,
        operation_item_id=stored.items[0].operation_item_id,
        expected_state_version=1,
        target_state=OperationItemState.DISPATCHING,
        event_id=UUID("22222222-3333-4444-8555-666666666666"),
        occurred_at=NOW + timedelta(seconds=2),
        sanitized_reason="provider_create_started",
    )
    item_succeeded = store.transition_operation_item(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=stored.operation_id,
        operation_item_id=stored.items[0].operation_item_id,
        expected_state_version=2,
        target_state=OperationItemState.SUCCEEDED,
        event_id=UUID("33333333-4444-4555-8666-777777777777"),
        occurred_at=NOW + timedelta(seconds=3),
        sanitized_reason="provider_succeeded",
        provider_result_identifier="document-1",
    )
    succeeded = store.transition_operation(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        operation_id=stored.operation_id,
        expected_state_version=dispatching.state_version,
        target_state=ParentOperationState.SUCCEEDED,
        event_id=UUID("44444444-5555-4666-8777-888888888888"),
        occurred_at=NOW + timedelta(seconds=4),
        sanitized_reason="provider_succeeded",
    )

    assert item_dispatching.items[0].state is OperationItemState.DISPATCHING
    assert item_succeeded.items[0].state is OperationItemState.SUCCEEDED
    assert succeeded.state is ParentOperationState.SUCCEEDED


def test_parent_and_item_state_contracts_are_distinct() -> None:
    from mercury_tools.execution.hosted.models import (
        OperationItemState,
        ParentOperationState,
    )

    assert "prepared" in {state.value for state in ParentOperationState}
    assert "expired" in {state.value for state in ParentOperationState}
    assert "not_dispatched" not in {state.value for state in ParentOperationState}
    assert "not_dispatched" in {state.value for state in OperationItemState}
