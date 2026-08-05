from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request

WORKSPACE_ID = UUID("11111111-1111-1111-1111-111111111111")
TENANT_ID = UUID("22222222-2222-2222-2222-222222222222")
USER_ID = UUID("33333333-3333-3333-3333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-4444-444444444444")
PREVIEW_ID = UUID("55555555-5555-5555-5555-555555555555")
OPERATION_ID = UUID("66666666-6666-6666-6666-666666666666")
ITEM_ID = UUID("77777777-7777-7777-7777-777777777777")
EVENT_ID = UUID("88888888-8888-8888-8888-888888888888")
VERSION = "a" * 64
NOW = datetime(2026, 8, 6, 8, 0, tzinfo=UTC)


def _context():
    from mercury_tools.auth.models import MercuryPrincipal

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/mcp",
            "headers": [(b"authorization", b"Bearer test.token.value")],
        }
    )
    request.state.mercury_principal = MercuryPrincipal(
        subject=USER_ID,
        client_id="test-client",
        scopes=frozenset({"openid"}),
        token_id="test-token",
    )
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


class WorkspaceService:
    checked = False

    def require_workspace(self, *_args: object):
        from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

        type(self).checked = True
        return WorkspaceMembership(
            tenant_id=TENANT_ID,
            tenant_display_name="Mercury",
            workspace_id=WORKSPACE_ID,
            workspace_display_name="Demo",
            role=WorkspaceRole.OWNER,
        )


def _prepared_preview():
    from mercury_tools.execution.hosted.models import (
        DocumentFinancials,
        DocumentLineAmounts,
        PreparedDocumentPreview,
        PreparedPreviewItem,
        PreviewState,
    )
    from mercury_tools.providers.models import ProviderId

    line = DocumentLineAmounts(
        currency="THB",
        quantity=Decimal("1.00"),
        unit_price=Decimal("100.00"),
        discount_amount=Decimal("0.00"),
        vat_rate=Decimal("7.00"),
        vat_amount=Decimal("7.00"),
        withholding_rate=Decimal("0.00"),
        withholding_amount=Decimal("0.00"),
        line_total=Decimal("107.00"),
    )
    financials = DocumentFinancials(
        currency="THB",
        lines=(line,),
        subtotal=Decimal("100.00"),
        discount_total=Decimal("0.00"),
        vat_total=Decimal("7.00"),
        withholding_tax_total=Decimal("0.00"),
        grand_total=Decimal("107.00"),
    )
    return PreparedDocumentPreview(
        status=PreviewState.AWAITING_CONFIRMATION,
        workspace_id=WORKSPACE_ID,
        preview_id=PREVIEW_ID,
        state_version=1,
        connection_id=CONNECTION_ID,
        provider=ProviderId.FLOWACCOUNT,
        company_display_name="Demo Company",
        environment="sandbox",
        capability_id="documents.invoice.create",
        capability_version=VERSION,
        document_count=1,
        currency="THB",
        subtotal=Decimal("100.00"),
        discount_total=Decimal("0.00"),
        vat_total=Decimal("7.00"),
        withholding_tax_total=Decimal("0.00"),
        grand_total=Decimal("107.00"),
        warning_count=0,
        warnings=(),
        accountant_review_points=("review_invoice",),
        items=(
            PreparedPreviewItem(
                client_item_id="invoice-1",
                provider_call_hash="b" * 64,
                preview_integrity_hash="c" * 64,
                document_type="invoice",
                counterparty_display="[REDACTED_COUNTERPARTY]",
                issue_date=date(2026, 8, 6),
                due_date=date(2026, 8, 13),
                financials=financials,
                warnings=(),
                accountant_review_points=("review_invoice",),
            ),
        ),
        expires_at=NOW + timedelta(minutes=30),
        next_allowed_actions=("render_document_preview",),
    )


def _operation():
    from mercury_tools.execution.hosted.models import (
        HostedOperation,
        OperationEvent,
        OperationItem,
        OperationItemState,
        ParentOperationState,
    )
    from mercury_tools.providers.models import ProviderId

    item = OperationItem(
        operation_item_id=ITEM_ID,
        preview_item_id=ITEM_ID,
        item_index=0,
        client_item_id="invoice-1",
        provider_call_hash="b" * 64,
        preview_integrity_hash="c" * 64,
        state=OperationItemState.DISPATCHING,
        state_version=2,
        created_at=NOW,
        updated_at=NOW,
    )
    event = OperationEvent(
        event_id=EVENT_ID,
        operation_id=OPERATION_ID,
        from_state="awaiting_confirmation",
        to_state="dispatching",
        state_version=2,
        sanitized_reason="dispatch_started",
        occurred_at=NOW,
    )
    return HostedOperation(
        operation_id=OPERATION_ID,
        preview_id=PREVIEW_ID,
        tenant_id=TENANT_ID,
        auth_user_id=USER_ID,
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        provider=ProviderId.FLOWACCOUNT,
        environment="sandbox",
        capability_id="documents.invoice.create",
        capability_version=VERSION,
        connection_revision=1,
        provider_call_hash="d" * 64,
        preview_integrity_hash="e" * 64,
        state=ParentOperationState.DISPATCHING,
        state_version=2,
        items=(item,),
        events=(event,),
        created_at=NOW,
        updated_at=NOW,
        payload_purge_after=NOW + timedelta(hours=1),
    )


class DocumentRuntime:
    def __init__(self) -> None:
        self.closed = False
        self.prepared_request = None

    async def prepare_document_create(self, *_args: object):
        assert WorkspaceService.checked
        self.prepared_request = _args[-1]
        return _prepared_preview()

    async def render_document_preview(self, *_args: object):
        assert WorkspaceService.checked
        return _prepared_preview()

    async def confirm_document_create(self, *_args: object):
        assert WorkspaceService.checked
        return _operation()

    async def get_operation_status(self, *_args: object):
        assert WorkspaceService.checked
        return _operation()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_prepare_validates_json_and_returns_only_sanitized_preview_summary() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.mcp.v1_schemas import DocumentCreateItemInput
    from mercury_tools.mcp.v1_tools import prepare_document_create

    WorkspaceService.checked = False
    runtime = DocumentRuntime()
    result = await prepare_document_create(
        _context(),
        workspace_id=WORKSPACE_ID,
        connection_id=CONNECTION_ID,
        capability_id="documents.invoice.create",
        capability_version=VERSION,
        mode="single",
        documents=[
            DocumentCreateItemInput(
                client_item_id="invoice-1",
                provider_arguments_json='{"reference":"INV-1"}',
                accountant_review_points=["review_invoice"],
            )
        ],
        service_factory=WorkspaceService,
        document_runtime_factory=lambda: runtime,
    )

    assert result.status == "ok"
    assert result.preview_id == PREVIEW_ID
    assert result.data.grand_total == "107.00"
    assert result.next_allowed_actions == ["render_document_preview"]
    assert isinstance(runtime.prepared_request, SingleDocumentCreate)
    assert runtime.prepared_request.document.provider_arguments_copy() == {"reference": "INV-1"}
    assert "provider_arguments" not in result.model_dump_json()
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_prepare_rejects_non_object_json_before_runtime_dispatch() -> None:
    from mercury_tools.mcp.v1_errors import MercuryV1ToolError
    from mercury_tools.mcp.v1_schemas import DocumentCreateItemInput
    from mercury_tools.mcp.v1_tools import prepare_document_create

    runtime = DocumentRuntime()
    with pytest.raises(MercuryV1ToolError) as raised:
        await prepare_document_create(
            _context(),
            workspace_id=WORKSPACE_ID,
            connection_id=CONNECTION_ID,
            capability_id="documents.invoice.create",
            capability_version=VERSION,
            mode="single",
            documents=[
                DocumentCreateItemInput(
                    client_item_id="invoice-1",
                    provider_arguments_json="[]",
                )
            ],
            service_factory=WorkspaceService,
            document_runtime_factory=lambda: runtime,
        )

    assert raised.value.code == "validation_failed"
    assert runtime.prepared_request is None
    assert runtime.closed is False


@pytest.mark.asyncio
async def test_render_confirm_and_status_project_lower_state_without_payloads() -> None:
    from mercury_tools.mcp.v1_tools import (
        confirm_document_create,
        get_operation_status,
        render_document_preview,
    )

    runtime = DocumentRuntime()
    kwargs = {
        "service_factory": WorkspaceService,
        "document_runtime_factory": lambda: runtime,
    }
    rendered = await render_document_preview(
        _context(), workspace_id=WORKSPACE_ID, preview_id=PREVIEW_ID, **kwargs
    )
    confirmed = await confirm_document_create(
        _context(),
        workspace_id=WORKSPACE_ID,
        preview_id=PREVIEW_ID,
        state_version=1,
        confirmation="CONFIRM_CREATE",
        **kwargs,
    )
    status = await get_operation_status(
        _context(), workspace_id=WORKSPACE_ID, operation_id=OPERATION_ID, **kwargs
    )

    assert rendered.next_allowed_actions == ["confirm_document_create"]
    assert confirmed.operation_id == OPERATION_ID
    assert confirmed.data.operation_state == "dispatching"
    assert status.data.items[0].client_item_id == "invoice-1"
    for output in (rendered, confirmed, status):
        serialized = output.model_dump_json()
        assert "auth_user_id" not in serialized
        assert "provider_call_hash" not in serialized
        assert "preview_integrity_hash" not in serialized
