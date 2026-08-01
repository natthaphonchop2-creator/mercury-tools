from __future__ import annotations

import hashlib
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.credentials.vault import CredentialVault
from mercury_tools.providers.models import (
    AuthorizationMethod,
    ConnectionReadiness,
    ProviderConnection,
    ProviderId,
)
from mercury_tools.workspaces.models import WorkspaceMembership, WorkspaceRole

TENANT_ID = UUID("11111111-1111-4111-8111-111111111111")
AUTH_USER_ID = UUID("22222222-2222-4222-8222-222222222222")
WORKSPACE_ID = UUID("33333333-3333-4333-8333-333333333333")
CONNECTION_ID = UUID("44444444-4444-4444-8444-444444444444")
QUALIFICATION_ID = UUID("55555555-5555-4555-8555-555555555555")
PREVIEW_ID = UUID("66666666-6666-4666-8666-666666666666")
ITEM_ID = UUID("77777777-7777-4777-8777-777777777777")
EDITED_PREVIEW_ID = UUID("88888888-8888-4888-8888-888888888888")
EDITED_ITEM_ID = UUID("99999999-9999-4999-8999-999999999999")
NOW = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
COMPANY_ID = "provider-company-sensitive-42"
COMPANY_SHA256 = hashlib.sha256(COMPANY_ID.encode("utf-8")).hexdigest()
KEY = bytes(range(32))
SECRET_COUNTERPARTY = "Sensitive Customer 991"


def _input_schema() -> dict[str, object]:
    money = {"type": "string", "pattern": r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?$"}
    nonnegative = {"type": "string", "pattern": r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?$"}
    line = {
        "type": "object",
        "properties": {
            "description": {"type": "string", "minLength": 1},
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            "quantity": nonnegative,
            "unit_price": nonnegative,
            "discount_amount": nonnegative,
            "vat_rate": nonnegative,
            "vat_amount": nonnegative,
            "withholding_rate": nonnegative,
            "withholding_amount": nonnegative,
            "line_total": money,
        },
        "required": [
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
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "minLength": 1},
            "counterparty_name": {"type": "string", "minLength": 1},
            "issue_date": {"type": "string", "format": "date"},
            "due_date": {"type": "string", "format": "date"},
            "currency": {"type": "string", "pattern": "^[A-Z]{3}$"},
            "lines": {"type": "array", "items": line, "minItems": 1, "maxItems": 100},
            "subtotal": nonnegative,
            "discount_total": nonnegative,
            "vat_total": nonnegative,
            "withholding_tax_total": nonnegative,
            "grand_total": money,
        },
        "required": [
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
        ],
        "additionalProperties": False,
    }


def _payload(*, reference: str = "INV-DRAFT-001", currency: str = "THB") -> dict[str, object]:
    return {
        "reference": reference,
        "counterparty_name": SECRET_COUNTERPARTY,
        "issue_date": "2026-07-31",
        "due_date": "2026-08-30",
        "currency": currency,
        "lines": [
            {
                "description": "Implementation service",
                "currency": currency,
                "quantity": "2",
                "unit_price": "100.00",
                "discount_amount": "10.00",
                "vat_rate": "7",
                "vat_amount": "13.30",
                "withholding_rate": "3",
                "withholding_amount": "5.70",
                "line_total": "197.60",
            }
        ],
        "subtotal": "200.00",
        "discount_total": "10.00",
        "vat_total": "13.30",
        "withholding_tax_total": "5.70",
        "grand_total": "197.60",
    }


def _financials(
    *,
    currency: str = "THB",
    line_currency: str | None = None,
    **updates: object,
):
    from mercury_tools.execution.hosted.models import (
        DocumentFinancials,
        DocumentLineAmounts,
    )

    line_values: dict[str, object] = {
        "currency": line_currency or currency,
        "quantity": "2",
        "unit_price": "100.00",
        "discount_amount": "10.00",
        "vat_rate": "7",
        "vat_amount": "13.30",
        "withholding_rate": "3",
        "withholding_amount": "5.70",
        "line_total": "197.60",
    }
    financial_values: dict[str, object] = {
        "currency": currency,
        "lines": (DocumentLineAmounts(**line_values),),
        "subtotal": "200.00",
        "discount_total": "10.00",
        "vat_total": "13.30",
        "withholding_tax_total": "5.70",
        "grand_total": "197.60",
    }
    financial_values.update(updates)
    return DocumentFinancials(**financial_values)


def _draft(
    *,
    client_item_id: str = "client-item-1",
    reference: str = "INV-DRAFT-001",
    currency: str = "THB",
    provider_updates: dict[str, object] | None = None,
    warnings: tuple[str, ...] = ("withholding_tax_requires_review",),
    accountant_review_points: tuple[str, ...] = ("confirm_counterparty_tax_treatment",),
):
    from mercury_tools.execution.hosted.models import DocumentCreateDraft

    provider_arguments = _payload(reference=reference, currency=currency)
    if provider_updates:
        provider_arguments.update(provider_updates)
    return DocumentCreateDraft(
        client_item_id=client_item_id,
        provider_arguments=provider_arguments,
        warnings=warnings,
        accountant_review_points=accountant_review_points,
    )


def _connection(**updates: object) -> ProviderConnection:
    values: dict[str, object] = {
        "id": CONNECTION_ID,
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "auth_user_id": AUTH_USER_ID,
        "provider": ProviderId.FLOWACCOUNT,
        "environment": "sandbox",
        "provider_account_id": COMPANY_ID,
        "account_display_name": "Mercury Test Company",
        "authorization_method": AuthorizationMethod.OAUTH2_PKCE,
        "granted_permissions": ("documents.create",),
        "readiness": ConnectionReadiness.READY,
        "revision": 7,
        "last_validated_at": NOW,
        "credential_envelope_ids": (UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),),
        "created_at": NOW - timedelta(days=1),
        "updated_at": NOW,
    }
    values.update(updates)
    return ProviderConnection(**values)


def _qualification(**updates: object) -> ProviderMCPQualification:
    input_schema = updates.pop("input_schema", _input_schema())
    definition = ProviderMCPQualification.discovered(
        provider="flowaccount",
        environment="sandbox",
        provider_tool_name="create_invoice",
        normalized_capability="documents.invoice.create",
        input_schema=input_schema,
        output_schema={
            "type": "object",
            "properties": {"document_id": {"type": "string"}},
            "required": ["document_id"],
            "additionalProperties": False,
        },
        response_shape_hash="a" * 64,
        required_permissions=("documents.create",),
    )
    values = definition.model_dump(mode="python")
    values.update(
        {
            "id": QUALIFICATION_ID,
            "qualification_state": QualificationState.ENABLED,
            "company_sha256": COMPANY_SHA256,
            "evidence_revision_sha256": "b" * 64,
            "qualification_evidence_uri": (
                "catalog://global/flowaccount/qualifications/"
                f"{definition.capability_version_sha256}-{'b' * 64}.json"
            ),
            "evidence_evaluated_at": NOW - timedelta(hours=1),
            "evidence_expires_at": NOW + timedelta(days=1),
        }
    )
    values.update(updates)
    return ProviderMCPQualification.model_validate(values)


def _principal() -> MercuryPrincipal:
    return MercuryPrincipal(
        subject=AUTH_USER_ID,
        client_id="mercury-test-client",
        scopes=frozenset({"mcp:tools"}),
    )


def _membership(_principal: MercuryPrincipal, workspace_id: UUID) -> WorkspaceMembership:
    assert workspace_id == WORKSPACE_ID
    return WorkspaceMembership(
        tenant_id=TENANT_ID,
        tenant_display_name="Personal",
        workspace_id=WORKSPACE_ID,
        workspace_display_name="Mercury Workspace",
        role=WorkspaceRole.OWNER,
    )


def _payload_vault(*, clock=None):
    from mercury_tools.execution.hosted.store import HostedPayloadVault

    return HostedPayloadVault(
        CredentialVault(
            active_key_version="v1",
            keys={"v1": KEY},
            clock=clock or (lambda: NOW),
        )
    )


def _projector_registry(qualification: ProviderMCPQualification):
    from mercury_tools.execution.hosted.projectors import (
        DocumentProjectorRegistry,
        ReviewedInvoiceProjector,
    )

    projector = ReviewedInvoiceProjector(
        projector_id="mercury.test.invoice",
        projector_version="c" * 64,
        provider=qualification.provider,
        environment=qualification.environment,
        provider_tool_name=qualification.provider_tool_name,
        capability_id=qualification.normalized_capability,
        capability_version=qualification.capability_version_sha256,
        schema_hash=qualification.schema_hash,
        currency_minor_units={"THB": 2, "USD": 2},
    )
    return DocumentProjectorRegistry((projector,))


def _service(
    *,
    connection=None,
    qualification=None,
    ids=None,
    authority_state: dict[str, object] | None = None,
    projector_registry=None,
    store_factory=None,
    clock=None,
):
    from mercury_tools.execution.hosted.preview_service import HostedPreviewService
    from mercury_tools.execution.hosted.store import InMemoryHostedPreviewStore

    checked_connection = connection or _connection()
    checked_qualification = qualification or _qualification()
    current_authority = authority_state or {
        "connection": checked_connection,
        "qualification": checked_qualification,
    }
    registry = projector_registry or _projector_registry(checked_qualification)
    selected_clock = clock or (lambda: NOW)
    payload_vault = _payload_vault(clock=selected_clock)
    store = (
        store_factory(payload_vault, registry, current_authority)
        if store_factory is not None
        else InMemoryHostedPreviewStore(
            payload_vault=payload_vault,
            projector_registry=registry,
            authority_resolver=lambda _preview: (
                current_authority["connection"],
                current_authority["qualification"],
            ),
            clock=selected_clock,
        )
    )
    id_values = iter(ids or (PREVIEW_ID, ITEM_ID))
    provider_calls: list[object] = []

    def resolve_connection(
        membership: WorkspaceMembership,
        principal: MercuryPrincipal,
        connection_id: UUID,
    ) -> ProviderConnection:
        assert membership.tenant_id == TENANT_ID
        assert principal.subject == AUTH_USER_ID
        assert connection_id == CONNECTION_ID
        return checked_connection

    def resolve_qualification(
        supplied_connection: ProviderConnection,
        capability_id: str,
        capability_version: str,
    ) -> ProviderMCPQualification:
        assert supplied_connection.id == CONNECTION_ID
        assert capability_id == checked_qualification.normalized_capability
        assert capability_version == checked_qualification.capability_version_sha256
        return checked_qualification

    service = HostedPreviewService(
        store=store,
        payload_vault=payload_vault,
        membership_resolver=_membership,
        connection_resolver=resolve_connection,
        qualification_resolver=resolve_qualification,
        projector_registry=registry,
        clock=selected_clock,
        uuid_factory=lambda: next(id_values),
    )
    return service, store, checked_connection, checked_qualification, provider_calls


def test_hosted_preview_contracts_are_closed_frozen_and_secret_safe() -> None:
    from mercury_tools.execution.hosted.models import DocumentCreateDraft

    draft = _draft()

    with pytest.raises(ValidationError):
        DocumentCreateDraft(**{**draft.model_dump(mode="python"), "unknown": "value"})
    with pytest.raises(ValidationError):
        draft.client_item_id = "changed"

    serialized = draft.model_dump_json()
    rendered = repr(draft)
    assert SECRET_COUNTERPARTY not in serialized
    assert SECRET_COUNTERPARTY not in rendered
    assert "provider_arguments" not in serialized
    assert "financials" not in type(draft).model_fields
    assert draft.provider_arguments_copy()["grand_total"] == "197.60"


@pytest.mark.parametrize(
    ("updates", "error"),
    [
        ({"subtotal": "199.99"}, "document_subtotal_mismatch"),
        ({"discount_total": "9.99"}, "document_discount_mismatch"),
        ({"vat_total": "13.29"}, "document_vat_mismatch"),
        ({"withholding_tax_total": "5.69"}, "document_withholding_mismatch"),
        ({"grand_total": "197.61"}, "document_grand_total_mismatch"),
        ({"currency": "USD", "line_currency": "THB"}, "document_currency_mismatch"),
    ],
)
def test_financial_cross_checks_use_deterministic_decimal_arithmetic(
    updates: dict[str, object],
    error: str,
) -> None:
    with pytest.raises(ValidationError, match=error):
        _financials(**updates)

    with pytest.raises(ValidationError, match="decimal_string_required"):
        _financials(subtotal=200.0)

    assert isinstance(_financials().grand_total, Decimal)


@pytest.mark.asyncio
async def test_prepare_binds_every_identity_and_performs_no_provider_call() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    service, store, connection, qualification, provider_calls = _service()
    result = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )

    stored = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=result.preview_id,
    )
    assert stored.preview_id == PREVIEW_ID
    assert stored.tenant_id == TENANT_ID
    assert stored.auth_user_id == AUTH_USER_ID
    assert stored.workspace_id == WORKSPACE_ID
    assert stored.connection_id == CONNECTION_ID
    assert stored.provider is ProviderId.FLOWACCOUNT
    assert stored.provider_account_sha256 == COMPANY_SHA256
    assert stored.environment == "sandbox"
    assert stored.connection_revision == connection.revision
    assert stored.connection_readiness is ConnectionReadiness.READY
    assert stored.qualification_id == QUALIFICATION_ID
    assert stored.provider_tool_name == qualification.provider_tool_name
    assert stored.capability_id == qualification.normalized_capability
    assert stored.capability_version == qualification.capability_version_sha256
    assert stored.schema_hash == qualification.schema_hash
    assert stored.evidence_revision_sha256 == qualification.evidence_revision_sha256
    assert stored.state_version == 1
    assert len(stored.items) == 1
    assert stored.items[0].provider_call_hash == result.items[0].provider_call_hash
    assert stored.items[0].preview_integrity_hash
    assert stored.provider_call_hash
    assert stored.preview_integrity_hash
    assert stored.projector_id == "mercury.test.invoice"
    assert stored.projector_version == "c" * 64
    assert stored.expires_at == NOW + timedelta(seconds=1800)
    assert stored.payload_purge_after == stored.expires_at + timedelta(hours=24)
    assert provider_calls == []
    assert SECRET_COUNTERPARTY not in result.model_dump_json()
    assert SECRET_COUNTERPARTY not in repr(stored)


@pytest.mark.asyncio
async def test_preview_payload_aad_binds_connection_readiness() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    service, store, _, qualification, _ = _service()
    result = await service.prepare_document_create(
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
        preview_id=result.preview_id,
    )

    binding = preview.payload_binding(preview.items[0])
    assert binding.connection_readiness is ConnectionReadiness.READY
    rebound = binding.model_copy(
        update={"connection_readiness": ConnectionReadiness.REQUIRES_VALIDATION}
    )

    with pytest.raises(HostedPreviewError, match="^preview_payload_changed$"):
        _payload_vault().open(rebound, preview.items[0].payload_envelope)


@pytest.mark.asyncio
async def test_preview_state_version_requires_a_real_state_transition() -> None:
    from mercury_tools.execution.hosted.models import DocumentPreview, SingleDocumentCreate

    service, store, _, qualification, _ = _service()
    result = await service.prepare_document_create(
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
        preview_id=result.preview_id,
    )

    with pytest.raises(ValidationError, match="preview_state_invalid"):
        DocumentPreview.model_validate(preview.model_copy(update={"state_version": 2}))


@pytest.mark.parametrize(
    ("qualification_updates", "payload_updates", "error"),
    [
        (
            {"qualification_state": QualificationState.DISABLED, "disable_reason": "reviewed"},
            {},
            "capability_unavailable",
        ),
        ({"normalized_capability": "documents.invoice.get"}, {}, "capability_unavailable"),
        ({"provider": "peak"}, {}, "capability_unavailable"),
        ({}, {"unexpected": "value"}, "document_schema_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_prepare_accepts_only_exact_enabled_document_create_schema(
    qualification_updates: dict[str, object],
    payload_updates: dict[str, object],
    error: str,
) -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    if qualification_updates.get("qualification_state") is QualificationState.DISABLED:
        enabled = _qualification()
        values = enabled.model_dump(mode="python")
        values.update(qualification_updates)
        qualification = ProviderMCPQualification.model_validate(values)
    elif qualification_updates:
        definition = _qualification()
        qualification = definition.model_copy(update=qualification_updates)
    else:
        qualification = _qualification()

    service, _, _, qualification, _ = _service(qualification=qualification)
    draft = _draft()
    if payload_updates:
        draft = draft.model_copy(
            update={
                "provider_arguments": {
                    **draft.provider_arguments_copy(),
                    **payload_updates,
                }
            }
        )
    with pytest.raises(HostedPreviewError, match=f"^{error}$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            SingleDocumentCreate(mode="single", document=draft),
        )


@pytest.mark.asyncio
async def test_prepare_rejects_an_enabled_but_open_document_schema() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    open_schema = _input_schema()
    open_schema["additionalProperties"] = True
    qualification = _qualification(input_schema=open_schema)
    service, _, _, qualification, _ = _service(qualification=qualification)

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
async def test_batch_rejects_size_duplicate_client_ids_and_duplicate_provider_calls() -> None:
    from mercury_tools.execution.hosted.models import BatchDocumentCreate
    from mercury_tools.execution.hosted.store import HostedPreviewError

    service, _, _, qualification, _ = _service(
        ids=[PREVIEW_ID, *[UUID(int=i + 1) for i in range(25)]]
    )

    with pytest.raises(ValidationError, match="batch_size_invalid"):
        BatchDocumentCreate(mode="batch", documents=())
    with pytest.raises(ValidationError, match="batch_size_invalid"):
        BatchDocumentCreate(
            mode="batch",
            documents=tuple(
                _draft(client_item_id=f"item-{index}", reference=f"INV-{index}")
                for index in range(26)
            ),
        )
    with pytest.raises(ValidationError, match="duplicate_client_item_id"):
        BatchDocumentCreate(
            mode="batch",
            documents=(
                _draft(client_item_id="same", reference="INV-1"),
                _draft(client_item_id="same", reference="INV-2"),
            ),
        )

    duplicate_payloads = BatchDocumentCreate(
        mode="batch",
        documents=(
            _draft(client_item_id="first"),
            _draft(client_item_id="second"),
        ),
    )
    with pytest.raises(HostedPreviewError, match="^duplicate_provider_call$"):
        await service.prepare_document_create(
            _principal(),
            WORKSPACE_ID,
            CONNECTION_ID,
            qualification.normalized_capability,
            qualification.capability_version_sha256,
            duplicate_payloads,
        )


@pytest.mark.asyncio
async def test_edit_creates_a_new_immutable_preview_without_mutating_the_original() -> None:
    from mercury_tools.execution.hosted.models import SingleDocumentCreate

    service, store, _, qualification, _ = _service(
        ids=(PREVIEW_ID, ITEM_ID, EDITED_PREVIEW_ID, EDITED_ITEM_ID)
    )
    first = await service.prepare_document_create(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        SingleDocumentCreate(mode="single", document=_draft()),
    )
    edited = await service.edit_document_preview(
        _principal(),
        WORKSPACE_ID,
        CONNECTION_ID,
        qualification.normalized_capability,
        qualification.capability_version_sha256,
        preview_id=first.preview_id,
        request=SingleDocumentCreate(
            mode="single",
            document=_draft(reference="INV-DRAFT-EDITED"),
        ),
    )

    original = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=first.preview_id,
    )
    replacement = store.get_preview(
        tenant_id=TENANT_ID,
        auth_user_id=AUTH_USER_ID,
        workspace_id=WORKSPACE_ID,
        preview_id=edited.preview_id,
    )
    assert edited.preview_id == EDITED_PREVIEW_ID
    assert edited.preview_id != first.preview_id
    assert replacement.supersedes_preview_id == original.preview_id
    assert original.supersedes_preview_id is None
    assert original.items[0].provider_call_hash != replacement.items[0].provider_call_hash
    assert original.state_version == 1


def test_prepare_service_has_no_provider_runtime_or_dispatch_dependency() -> None:
    from mercury_tools.execution.hosted.preview_service import HostedPreviewService

    signature = inspect.signature(HostedPreviewService)
    source = inspect.getsource(inspect.getmodule(HostedPreviewService))
    assert "runtime_factory" not in signature.parameters
    assert "ProviderRuntime" not in source
    assert ".call(" not in source
    assert "dispatch" not in source.casefold()
    for forbidden in ("RepositoryContext", "LocalRequestStore", "sqlite3"):
        assert forbidden not in source
