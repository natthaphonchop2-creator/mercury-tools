from __future__ import annotations

from uuid import UUID

import pytest

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.execution.hosted.models import SingleDocumentCreate
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

TENANT_ID = UUID("10000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-4000-8000-000000000002")
USER_ID = UUID("30000000-0000-4000-8000-000000000003")
CONNECTION_ID = UUID("40000000-0000-4000-8000-000000000004")
PREVIEW_ID = UUID("50000000-0000-4000-8000-000000000005")
OPERATION_ID = UUID("60000000-0000-4000-8000-000000000006")


def _principal() -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=USER_ID,
        client_id="mercury-test",
        scopes=frozenset({"openid", "email", "profile"}),
    )


def _membership() -> WorkspaceMembership:
    return WorkspaceMembership(
        tenant_id=TENANT_ID,
        tenant_display_name="Mercury Test",
        workspace_id=WORKSPACE_ID,
        workspace_display_name="Mercury Test Company",
        role=WorkspaceRole.MEMBER,
    )


class PreviewService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def prepare_document_create(self, *args):
        self.calls.append(args)
        return "prepared-preview"


class OperationService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def confirm_and_dispatch(self, *args):
        self.calls.append(args)
        return "confirmed-operation"


class Store:
    def __init__(self) -> None:
        self.preview_calls: list[dict[str, object]] = []
        self.operation_calls: list[dict[str, object]] = []

    def get_preview(self, **kwargs):
        self.preview_calls.append(kwargs)
        return "stored-preview"

    def get_operation(self, **kwargs):
        self.operation_calls.append(kwargs)
        return "stored-operation"


class Closeable:
    def __init__(self) -> None:
        self.closed = 0

    async def aclose(self) -> None:
        self.closed += 1


class SyncCloseable:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


@pytest.mark.asyncio
async def test_hosted_document_runtime_keeps_preview_payload_server_side(monkeypatch) -> None:
    from mercury_tools.execution.hosted.models import PreparedDocumentPreview
    from mercury_tools.execution.hosted.production import HostedDocumentRuntime

    monkeypatch.setattr(
        PreparedDocumentPreview,
        "from_preview",
        classmethod(lambda _cls, preview: preview),
    )

    preview_service = PreviewService()
    operation_service = OperationService()
    store = Store()
    provider_runtime = Closeable()
    store_http = SyncCloseable()

    async def membership_resolver(principal, workspace_id):
        assert principal == _principal()
        assert workspace_id == WORKSPACE_ID
        return _membership()

    runtime = HostedDocumentRuntime(
        store=store,
        preview_service=preview_service,
        operation_service=operation_service,
        membership_resolver=membership_resolver,
        provider_runtime=provider_runtime,
        store_http_client=store_http,
    )
    request = SingleDocumentCreate(
        mode="single",
        document={
            "client_item_id": "invoice-001",
            "provider_arguments": {"reference": "INV-001"},
        },
    )

    assert (
        await runtime.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            "documents.invoice.create",
            "a" * 64,
            request,
        )
        == "prepared-preview"
    )
    assert preview_service.calls == [
        (
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            "documents.invoice.create",
            "a" * 64,
            request,
        )
    ]

    assert (
        await runtime.render_document_preview(_principal(), WORKSPACE_ID, PREVIEW_ID)
        == "stored-preview"
    )
    assert store.preview_calls == [
        {
            "tenant_id": TENANT_ID,
            "auth_user_id": USER_ID,
            "workspace_id": WORKSPACE_ID,
            "preview_id": PREVIEW_ID,
        }
    ]

    assert (
        await runtime.confirm_document_create(
            _principal(), WORKSPACE_ID, PREVIEW_ID, 3
        )
        == "confirmed-operation"
    )
    confirmation = operation_service.calls[0][2]
    assert confirmation.preview_id == PREVIEW_ID
    assert confirmation.expected_state_version == 3
    assert confirmation.confirmation == "CONFIRM_CREATE"

    assert (
        await runtime.get_operation_status(_principal(), WORKSPACE_ID, OPERATION_ID)
        == "stored-operation"
    )
    assert store.operation_calls == [
        {
            "tenant_id": TENANT_ID,
            "auth_user_id": USER_ID,
            "workspace_id": WORKSPACE_ID,
            "operation_id": OPERATION_ID,
        }
    ]

    await runtime.aclose()
    await runtime.aclose()
    assert provider_runtime.closed == 1
    assert store_http.closed == 1


def test_provider_identifier_is_closed_and_unambiguous() -> None:
    from mercury_tools.execution.hosted.production import extract_provider_identifier

    assert extract_provider_identifier({"data": {"documentId": "INV-42"}}) == "INV-42"
    assert extract_provider_identifier({"id": "A", "data": {"recordId": "B"}}) is None
    assert extract_provider_identifier({"data": {"email": "private@example.com"}}) is None
    assert extract_provider_identifier({"id": ""}) is None


def test_reviewed_invoice_projector_accepts_only_the_closed_canonical_schema() -> None:
    from mercury_tools.execution.hosted.production import supports_reviewed_invoice_projection

    root_fields = {
        "reference",
        "counterparty_name",
        "issue_date",
        "due_date",
        "currency",
        "lines",
        "subtotal",
        "discount_total",
        "vat_total",
        "withholding_tax_total",
        "grand_total",
    }
    line_fields = {
        "description",
        "currency",
        "quantity",
        "unit_price",
        "discount_amount",
        "vat_rate",
        "vat_amount",
        "withholding_rate",
        "withholding_amount",
        "line_total",
    }
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": sorted(root_fields),
        "properties": {
            **{field: {"type": "string"} for field in root_fields - {"lines"}},
            "lines": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": sorted(line_fields),
                    "properties": {
                        field: {"type": "string"} for field in line_fields
                    },
                },
            },
        },
    }

    assert supports_reviewed_invoice_projection(schema) is True
    assert supports_reviewed_invoice_projection({**schema, "additionalProperties": True}) is False
    assert (
        supports_reviewed_invoice_projection(
            {**schema, "required": [field for field in schema["required"] if field != "vat_total"]}
        )
        is False
    )


@pytest.mark.asyncio
async def test_v1_default_document_runtime_uses_render_supabase_composition(
    monkeypatch,
) -> None:
    from mercury_tools.execution.hosted import production
    from mercury_tools.mcp import v1_tools

    settings = object()
    runtime = object()
    monkeypatch.setattr(v1_tools, "load_settings", lambda: settings)
    monkeypatch.setattr(
        production,
        "build_hosted_document_production_composition",
        lambda *, settings: runtime,
    )

    assert await v1_tools._document_runtime() is runtime
