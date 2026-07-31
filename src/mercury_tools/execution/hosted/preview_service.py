"""Prepare encrypted immutable document previews without provider access."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

from mercury_tools.auth.models import MercuryPrincipal
from mercury_tools.catalog.models import ProviderMCPQualification, QualificationState
from mercury_tools.providers.models import ConnectionReadiness, ProviderConnection
from mercury_tools.v1.constants import PREVIEW_TTL_SECONDS
from mercury_tools.workspaces.models import WorkspaceMembership

from .models import (
    UNCONFIRMED_PAYLOAD_RETENTION,
    BatchDocumentCreate,
    DocumentCreateDraft,
    DocumentPreview,
    PreparedDocumentPreview,
    PrepareDocumentCreate,
    PreviewPayloadBinding,
    PreviewState,
    SingleDocumentCreate,
    StoredPreviewItem,
    authoritative_payload_bytes,
    preview_payload_hash,
)
from .store import HostedPayloadVault, HostedPreviewError, HostedPreviewStore

_DOCUMENT_CREATE = re.compile(r"^documents\.([a-z][a-z0-9_]*)\.create$")

MembershipResolver = Callable[
    [MercuryPrincipal, UUID],
    WorkspaceMembership | Awaitable[WorkspaceMembership],
]
ConnectionResolver = Callable[
    [WorkspaceMembership, MercuryPrincipal, UUID],
    ProviderConnection | Awaitable[ProviderConnection],
]
QualificationResolver = Callable[
    [ProviderConnection, str, str],
    ProviderMCPQualification | Awaitable[ProviderMCPQualification],
]


class UUIDFactory(Protocol):
    def __call__(self) -> UUID: ...


async def _await_value(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _now(clock: Callable[[], datetime]) -> datetime:
    try:
        value = clock()
    except Exception:
        raise HostedPreviewError("preview_state_invalid") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HostedPreviewError("preview_state_invalid")
    return value.astimezone(UTC)


def _next_uuid(factory: UUIDFactory) -> UUID:
    try:
        value = factory()
    except Exception:
        raise HostedPreviewError("preview_store_unavailable") from None
    if not isinstance(value, UUID) or value.int == 0:
        raise HostedPreviewError("preview_store_unavailable")
    return value


def _company_sha256(connection: ProviderConnection) -> str:
    return hashlib.sha256(connection.provider_account_id.encode("utf-8")).hexdigest()


def _schema_copy(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError):
        raise HostedPreviewError("capability_unavailable") from None
    if not isinstance(copied, dict):
        raise HostedPreviewError("capability_unavailable")
    return copied


def _validate_exact_schema(
    qualification: ProviderMCPQualification,
    documents: tuple[DocumentCreateDraft, ...],
) -> None:
    schema = _schema_copy(qualification.input_schema)
    try:
        Draft202012Validator.check_schema(schema)
        _require_closed_object_schemas(schema)
    except HostedPreviewError:
        raise
    except Exception:
        raise HostedPreviewError("capability_unavailable") from None
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    try:
        for document in documents:
            payload = document.provider_payload_copy()
            validator.validate(payload)
            _cross_check_provider_totals(payload, document)
    except HostedPreviewError:
        raise
    except Exception:
        raise HostedPreviewError("document_schema_invalid") from None


def _require_closed_object_schemas(value: Any) -> None:
    if isinstance(value, Mapping):
        object_type = value.get("type") == "object" or (
            isinstance(value.get("type"), list) and "object" in value["type"]
        )
        if object_type or any(
            keyword in value for keyword in ("properties", "patternProperties", "required")
        ):
            if value.get("additionalProperties") is not False:
                raise HostedPreviewError("capability_unavailable")
            if value.get("unevaluatedProperties", False) is not False:
                raise HostedPreviewError("capability_unavailable")
        for child in value.values():
            _require_closed_object_schemas(child)
    elif isinstance(value, list):
        for child in value:
            _require_closed_object_schemas(child)


def _decimal_field(payload: Mapping[str, Any], aliases: tuple[str, ...]) -> Decimal | None:
    names = [name for name in aliases if name in payload]
    if not names:
        return None
    if len(names) != 1:
        raise HostedPreviewError("document_schema_invalid")
    value = payload[names[0]]
    if not isinstance(value, str):
        raise HostedPreviewError("document_schema_invalid")
    try:
        decimal = Decimal(value)
    except InvalidOperation:
        raise HostedPreviewError("document_schema_invalid") from None
    if not decimal.is_finite():
        raise HostedPreviewError("document_schema_invalid")
    return decimal


def _cross_check_provider_totals(
    payload: Mapping[str, Any],
    document: DocumentCreateDraft,
) -> None:
    financials = document.financials
    currency_names = [
        name for name in ("currency", "currency_code", "currencyCode") if name in payload
    ]
    if len(currency_names) > 1 or (
        currency_names and payload[currency_names[0]] != financials.currency
    ):
        raise HostedPreviewError("document_schema_invalid")
    comparisons = (
        (("subtotal", "sub_total", "subTotal"), financials.subtotal),
        (
            ("discount_total", "discount_amount", "discountAmount"),
            financials.discount_total,
        ),
        (("vat_total", "vat_amount", "vatAmount", "tax_total"), financials.vat_total),
        (
            (
                "withholding_tax_total",
                "withholdingTaxAmount",
                "documentWithholdingTaxAmount",
            ),
            financials.withholding_tax_total,
        ),
        (("grand_total", "grandTotal", "total_amount"), financials.grand_total),
    )
    for aliases, expected in comparisons:
        supplied = _decimal_field(payload, aliases)
        if supplied is not None and supplied != expected:
            raise HostedPreviewError("document_schema_invalid")


def _validate_connection(
    membership: WorkspaceMembership,
    principal: MercuryPrincipal,
    workspace_id: UUID,
    connection_id: UUID,
    connection: ProviderConnection,
) -> ProviderConnection:
    try:
        checked = ProviderConnection.model_validate(connection)
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("preview_binding_changed") from None
    if (
        membership.workspace_id != workspace_id
        or checked.id != connection_id
        or checked.tenant_id != membership.tenant_id
        or checked.workspace_id != workspace_id
        or checked.auth_user_id != principal.subject
        or checked.readiness is not ConnectionReadiness.READY
    ):
        raise HostedPreviewError("preview_binding_changed")
    return checked


def _validate_qualification(
    connection: ProviderConnection,
    qualification: ProviderMCPQualification,
    *,
    capability_id: str,
    capability_version: str,
    now: datetime,
) -> tuple[ProviderMCPQualification, str]:
    try:
        checked = ProviderMCPQualification.model_validate(qualification)
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("capability_unavailable") from None
    capability_match = _DOCUMENT_CREATE.fullmatch(capability_id)
    if (
        capability_match is None
        or checked.qualification_state is not QualificationState.ENABLED
        or checked.normalized_capability != capability_id
        or checked.capability_version_sha256 != capability_version
        or checked.provider != connection.provider.value
        or checked.environment != connection.environment
        or checked.company_sha256 != _company_sha256(connection)
        or checked.evidence_revision_sha256 is None
        or checked.evidence_expires_at is None
        or checked.evidence_expires_at <= now
        or not set(checked.required_permissions).issubset(connection.granted_permissions)
    ):
        raise HostedPreviewError("capability_unavailable")
    return checked, capability_match.group(1)


def _request_documents(request: PrepareDocumentCreate) -> tuple[DocumentCreateDraft, ...]:
    try:
        if isinstance(request, SingleDocumentCreate):
            checked: PrepareDocumentCreate = SingleDocumentCreate.model_validate(request)
        elif isinstance(request, BatchDocumentCreate):
            checked = BatchDocumentCreate.model_validate(request)
        else:
            raise TypeError
    except (TypeError, ValueError, ValidationError):
        raise HostedPreviewError("document_payload_invalid") from None
    return checked.documents


class HostedPreviewService:
    """Validate, encrypt, persist, and return one sanitized immutable preview."""

    def __init__(
        self,
        *,
        store: HostedPreviewStore,
        payload_vault: HostedPayloadVault,
        membership_resolver: MembershipResolver,
        connection_resolver: ConnectionResolver,
        qualification_resolver: QualificationResolver,
        clock: Callable[[], datetime] | None = None,
        uuid_factory: UUIDFactory | None = None,
    ) -> None:
        if not isinstance(payload_vault, HostedPayloadVault):
            raise TypeError("hosted_preview_service_invalid")
        self._store = store
        self._payload_vault = payload_vault
        self._membership_resolver = membership_resolver
        self._connection_resolver = connection_resolver
        self._qualification_resolver = qualification_resolver
        self._clock = clock or (lambda: datetime.now(UTC))
        self._uuid_factory = uuid_factory or uuid4

    async def prepare_document_create(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        request: PrepareDocumentCreate,
        *,
        supersedes_preview_id: UUID | None = None,
    ) -> PreparedDocumentPreview:
        if not isinstance(principal, MercuryPrincipal):
            raise HostedPreviewError("workspace_access_denied")
        now = _now(self._clock)
        try:
            membership = WorkspaceMembership.model_validate(
                await _await_value(self._membership_resolver(principal, workspace_id))
            )
        except Exception:
            raise HostedPreviewError("workspace_access_denied") from None
        if membership.workspace_id != workspace_id:
            raise HostedPreviewError("workspace_access_denied")
        try:
            connection = _validate_connection(
                membership,
                principal,
                workspace_id,
                connection_id,
                await _await_value(self._connection_resolver(membership, principal, connection_id)),
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_binding_changed") from None
        try:
            qualification, document_type = _validate_qualification(
                connection,
                await _await_value(
                    self._qualification_resolver(
                        connection,
                        capability_id,
                        capability_version,
                    )
                ),
                capability_id=capability_id,
                capability_version=capability_version,
                now=now,
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("capability_unavailable") from None
        documents = _request_documents(request)
        if any(document.document_type != document_type for document in documents):
            raise HostedPreviewError("document_schema_invalid")
        if len({document.client_item_id for document in documents}) != len(documents):
            raise HostedPreviewError("document_payload_invalid")
        payload_hashes = tuple(document.payload_hash for document in documents)
        if len(set(payload_hashes)) != len(payload_hashes):
            raise HostedPreviewError("duplicate_payload_hash")
        currencies = {document.financials.currency for document in documents}
        if len(currencies) != 1:
            raise HostedPreviewError("document_schema_invalid")
        _validate_exact_schema(qualification, documents)

        preview_id = _next_uuid(self._uuid_factory)
        provider_account_sha256 = _company_sha256(connection)
        expires_at = now + timedelta(seconds=PREVIEW_TTL_SECONDS)
        payload_purge_after = expires_at + UNCONFIRMED_PAYLOAD_RETENTION
        stored_items: list[StoredPreviewItem] = []
        for index, document in enumerate(documents):
            preview_item_id = _next_uuid(self._uuid_factory)
            binding = PreviewPayloadBinding(
                preview_id=preview_id,
                preview_item_id=preview_item_id,
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=workspace_id,
                connection_id=connection.id,
                provider=connection.provider,
                provider_account_sha256=provider_account_sha256,
                environment=connection.environment,
                qualification_id=qualification.id,
                provider_tool_name=qualification.provider_tool_name,
                capability_id=qualification.normalized_capability,
                capability_version=qualification.capability_version_sha256,
                schema_hash=qualification.schema_hash,
                evidence_revision_sha256=qualification.evidence_revision_sha256,
                connection_revision=connection.revision,
                connection_readiness=connection.readiness,
                preview_state_version=1,
                client_item_id=document.client_item_id,
                payload_hash=document.payload_hash,
                created_at=now,
                expires_at=expires_at,
            )
            plaintext = bytearray(authoritative_payload_bytes(document))
            try:
                envelope = self._payload_vault.seal(binding, plaintext)
            finally:
                plaintext[:] = b"\x00" * len(plaintext)
            stored_items.append(
                StoredPreviewItem(
                    preview_item_id=preview_item_id,
                    preview_id=preview_id,
                    tenant_id=membership.tenant_id,
                    auth_user_id=principal.subject,
                    workspace_id=workspace_id,
                    connection_id=connection.id,
                    item_index=index,
                    client_item_id=document.client_item_id,
                    payload_hash=document.payload_hash,
                    document_type=document.document_type,
                    counterparty_display=document.counterparty_display,
                    issue_date=document.issue_date,
                    due_date=document.due_date,
                    financials=document.financials,
                    warnings=document.warnings,
                    accountant_review_points=document.accountant_review_points,
                    payload_envelope=envelope,
                    created_at=now,
                    payload_purge_after=payload_purge_after,
                )
            )

        items = tuple(stored_items)
        currency = next(iter(currencies))
        warnings = tuple(code for item in items for code in item.warnings)
        review_points = tuple(code for item in items for code in item.accountant_review_points)
        payload_hash = preview_payload_hash(
            preview_id=preview_id,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            provider_account_sha256=provider_account_sha256,
            environment=connection.environment,
            capability_id=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            connection_revision=connection.revision,
            connection_readiness=connection.readiness,
            items=items,
        )
        preview = DocumentPreview(
            preview_id=preview_id,
            tenant_id=membership.tenant_id,
            auth_user_id=principal.subject,
            workspace_id=workspace_id,
            connection_id=connection.id,
            provider=connection.provider,
            provider_account_sha256=provider_account_sha256,
            account_display_name=connection.account_display_name,
            environment=connection.environment,
            qualification_id=qualification.id,
            provider_tool_name=qualification.provider_tool_name,
            capability_id=qualification.normalized_capability,
            capability_version=qualification.capability_version_sha256,
            schema_hash=qualification.schema_hash,
            response_shape_hash=qualification.response_shape_hash,
            evidence_revision_sha256=qualification.evidence_revision_sha256,
            connection_revision=connection.revision,
            connection_readiness=connection.readiness,
            payload_hash=payload_hash,
            state=PreviewState.AWAITING_CONFIRMATION,
            state_version=1,
            currency=currency,
            subtotal=sum((item.financials.subtotal for item in items), Decimal("0")),
            discount_total=sum((item.financials.discount_total for item in items), Decimal("0")),
            vat_total=sum((item.financials.vat_total for item in items), Decimal("0")),
            withholding_tax_total=sum(
                (item.financials.withholding_tax_total for item in items), Decimal("0")
            ),
            grand_total=sum((item.financials.grand_total for item in items), Decimal("0")),
            warnings=warnings,
            accountant_review_points=review_points,
            items=items,
            supersedes_preview_id=supersedes_preview_id,
            created_at=now,
            expires_at=expires_at,
            payload_purge_after=payload_purge_after,
        )
        try:
            persisted = self._store.create_preview(preview)
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_store_unavailable") from None
        return PreparedDocumentPreview.from_preview(persisted)

    async def edit_document_preview(
        self,
        principal: MercuryPrincipal,
        workspace_id: UUID,
        connection_id: UUID,
        capability_id: str,
        capability_version: str,
        *,
        preview_id: UUID,
        request: PrepareDocumentCreate,
    ) -> PreparedDocumentPreview:
        if not isinstance(principal, MercuryPrincipal):
            raise HostedPreviewError("workspace_access_denied")
        try:
            membership = WorkspaceMembership.model_validate(
                await _await_value(self._membership_resolver(principal, workspace_id))
            )
        except Exception:
            raise HostedPreviewError("workspace_access_denied") from None
        try:
            original = self._store.get_preview(
                tenant_id=membership.tenant_id,
                auth_user_id=principal.subject,
                workspace_id=workspace_id,
                preview_id=preview_id,
            )
        except HostedPreviewError:
            raise
        except Exception:
            raise HostedPreviewError("preview_store_unavailable") from None
        if (
            original.connection_id != connection_id
            or original.capability_id != capability_id
            or original.capability_version != capability_version
        ):
            raise HostedPreviewError("preview_binding_changed")
        return await self.prepare_document_create(
            principal,
            workspace_id,
            connection_id,
            capability_id,
            capability_version,
            request,
            supersedes_preview_id=original.preview_id,
        )


__all__ = ["HostedPreviewService"]
