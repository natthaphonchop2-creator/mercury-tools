"""Closed contracts for tenant-bound hosted previews and operation state."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Annotated, Any, Literal, TypeAlias
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)

from mercury_tools.catalog.identity import deep_freeze
from mercury_tools.credentials.models import CredentialEnvelope
from mercury_tools.execution.models import canonical_payload_bytes, canonical_payload_hash
from mercury_tools.providers.models import ConnectionReadiness, ProviderId
from mercury_tools.v1.constants import MAX_BATCH_DOCUMENTS, PREVIEW_TTL_SECONDS

UNCONFIRMED_PAYLOAD_RETENTION = timedelta(hours=24)
CONFIRMED_PAYLOAD_RETENTION = timedelta(days=30)
HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE = "hosted_preview_payload"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_ENVIRONMENT = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_DOCUMENT_TYPE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?$")
_MAX_DECIMAL = Decimal("999999999999999.9999")


class _HostedModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )


def _parse_decimal_string(value: object) -> Decimal:
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, str) and _DECIMAL.fullmatch(value) is not None:
        try:
            candidate = Decimal(value)
        except InvalidOperation:
            raise ValueError("decimal_string_required") from None
    else:
        raise ValueError("decimal_string_required")
    if not candidate.is_finite() or abs(candidate) > _MAX_DECIMAL:
        raise ValueError("decimal_string_required")
    exponent = candidate.as_tuple().exponent
    if exponent < -4:
        raise ValueError("decimal_string_required")
    return candidate


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


DecimalString = Annotated[
    Decimal,
    BeforeValidator(_parse_decimal_string),
    PlainSerializer(_decimal_text, return_type=str, when_used="json"),
]
NonnegativeDecimalString = Annotated[DecimalString, Field(ge=Decimal("0"))]
PositiveDecimalString = Annotated[DecimalString, Field(gt=Decimal("0"))]
RateDecimalString = Annotated[
    DecimalString,
    Field(ge=Decimal("0"), le=Decimal("100")),
]


def _aware_utc(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(code)
    return value.astimezone(UTC)


def _non_nil(value: UUID, code: str) -> UUID:
    if value.int == 0:
        raise ValueError(code)
    return value


def _safe_text(value: str, code: str) -> str:
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise ValueError(code)
    return value


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        )
    except (TypeError, ValueError):
        raise ValueError("document_payload_invalid") from None


class DocumentLineAmounts(_HostedModel):
    """Provider-neutral line arithmetic used only for review and cross-checks."""

    currency: str = Field(pattern=_CURRENCY.pattern)
    quantity: PositiveDecimalString
    unit_price: NonnegativeDecimalString
    discount_amount: NonnegativeDecimalString = Decimal("0")
    vat_rate: RateDecimalString = Decimal("0")
    vat_amount: NonnegativeDecimalString = Decimal("0")
    withholding_rate: RateDecimalString = Decimal("0")
    withholding_amount: NonnegativeDecimalString = Decimal("0")
    line_total: DecimalString

    @model_validator(mode="after")
    def cross_check_line(self) -> DocumentLineAmounts:
        subtotal = self.quantity * self.unit_price
        if self.discount_amount > subtotal:
            raise ValueError("line_discount_mismatch")
        taxable = subtotal - self.discount_amount
        if self.vat_amount != taxable * self.vat_rate / Decimal("100"):
            raise ValueError("line_vat_mismatch")
        if self.withholding_amount != taxable * self.withholding_rate / Decimal("100"):
            raise ValueError("line_withholding_mismatch")
        expected_total = taxable + self.vat_amount - self.withholding_amount
        if self.line_total != expected_total:
            raise ValueError("line_total_mismatch")
        return self


class DocumentFinancials(_HostedModel):
    """Deterministic decimal totals for one document preview."""

    currency: str = Field(pattern=_CURRENCY.pattern)
    lines: tuple[DocumentLineAmounts, ...] = Field(min_length=1, max_length=2500)
    subtotal: NonnegativeDecimalString
    discount_total: NonnegativeDecimalString = Decimal("0")
    vat_total: NonnegativeDecimalString = Decimal("0")
    withholding_tax_total: NonnegativeDecimalString = Decimal("0")
    grand_total: DecimalString

    @model_validator(mode="after")
    def cross_check_document(self) -> DocumentFinancials:
        if any(line.currency != self.currency for line in self.lines):
            raise ValueError("document_currency_mismatch")
        if self.subtotal != sum(
            (line.quantity * line.unit_price for line in self.lines),
            start=Decimal("0"),
        ):
            raise ValueError("document_subtotal_mismatch")
        if self.discount_total != sum(
            (line.discount_amount for line in self.lines),
            start=Decimal("0"),
        ):
            raise ValueError("document_discount_mismatch")
        if self.vat_total != sum(
            (line.vat_amount for line in self.lines),
            start=Decimal("0"),
        ):
            raise ValueError("document_vat_mismatch")
        if self.withholding_tax_total != sum(
            (line.withholding_amount for line in self.lines),
            start=Decimal("0"),
        ):
            raise ValueError("document_withholding_mismatch")
        expected_total = (
            self.subtotal - self.discount_total + self.vat_total - self.withholding_tax_total
        )
        if self.grand_total != expected_total or self.grand_total != sum(
            (line.line_total for line in self.lines),
            start=Decimal("0"),
        ):
            raise ValueError("document_grand_total_mismatch")
        return self


class DocumentCreateDraft(_HostedModel):
    """Internal exact provider payload plus separately safe review fields."""

    client_item_id: str = Field(min_length=1, max_length=200, pattern=_IDENTIFIER.pattern)
    document_type: str = Field(min_length=1, max_length=64, pattern=_DOCUMENT_TYPE.pattern)
    counterparty_display: str | None = Field(default=None, min_length=1, max_length=200)
    issue_date: date | None = None
    due_date: date | None = None
    provider_payload: dict[str, Any] = Field(repr=False, exclude=True)
    financials: DocumentFinancials
    warnings: tuple[str, ...] = Field(default=(), max_length=100)
    accountant_review_points: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("counterparty_display")
    @classmethod
    def validate_display(cls, value: str | None) -> str | None:
        return None if value is None else _safe_text(value, "document_preview_summary_invalid")

    @field_validator("warnings", "accountant_review_points")
    @classmethod
    def validate_review_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)) or any(
            _IDENTIFIER.fullmatch(item) is None for item in value
        ):
            raise ValueError("document_review_metadata_invalid")
        return value

    @model_validator(mode="after")
    def freeze_payload(self) -> DocumentCreateDraft:
        copied = _json_copy(self.provider_payload)
        if not isinstance(copied, dict):
            raise ValueError("document_payload_invalid")
        canonical_payload_hash(copied)
        object.__setattr__(self, "provider_payload", deep_freeze(copied))
        if (
            self.due_date is not None
            and self.issue_date is not None
            and self.due_date < self.issue_date
        ):
            raise ValueError("document_due_date_invalid")
        return self

    def provider_payload_copy(self) -> dict[str, Any]:
        copied = _json_copy(self.provider_payload)
        if not isinstance(copied, dict):
            raise ValueError("document_payload_invalid")
        return copied

    def authoritative_payload(self) -> dict[str, Any]:
        return {
            "accountant_review_points": list(self.accountant_review_points),
            "counterparty_display": self.counterparty_display,
            "document_type": self.document_type,
            "issue_date": self.issue_date.isoformat() if self.issue_date is not None else None,
            "due_date": self.due_date.isoformat() if self.due_date is not None else None,
            "financials": self.financials.model_dump(mode="json"),
            "provider_payload": self.provider_payload_copy(),
            "warnings": list(self.warnings),
        }

    @property
    def payload_hash(self) -> str:
        return canonical_payload_hash(self.authoritative_payload())


class SingleDocumentCreate(_HostedModel):
    mode: Literal["single"]
    document: DocumentCreateDraft

    @property
    def documents(self) -> tuple[DocumentCreateDraft, ...]:
        return (self.document,)


class BatchDocumentCreate(_HostedModel):
    mode: Literal["batch"]
    documents: tuple[DocumentCreateDraft, ...]

    @model_validator(mode="after")
    def validate_batch(self) -> BatchDocumentCreate:
        if not 1 <= len(self.documents) <= MAX_BATCH_DOCUMENTS:
            raise ValueError("batch_size_invalid")
        client_ids = tuple(item.client_item_id for item in self.documents)
        if len(client_ids) != len(set(client_ids)):
            raise ValueError("duplicate_client_item_id")
        return self


PrepareDocumentCreate: TypeAlias = SingleDocumentCreate | BatchDocumentCreate


class PreviewState(StrEnum):
    PREPARED = "prepared"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


_PREVIEW_TRANSITIONS: Mapping[PreviewState, frozenset[PreviewState]] = {
    PreviewState.PREPARED: frozenset(
        {
            PreviewState.AWAITING_CONFIRMATION,
            PreviewState.CONFIRMED,
            PreviewState.EXPIRED,
            PreviewState.CANCELLED,
        }
    ),
    PreviewState.AWAITING_CONFIRMATION: frozenset(
        {PreviewState.CONFIRMED, PreviewState.EXPIRED, PreviewState.CANCELLED}
    ),
    PreviewState.CONFIRMED: frozenset(),
    PreviewState.EXPIRED: frozenset(),
    PreviewState.CANCELLED: frozenset(),
}


class PreviewPayloadBinding(_HostedModel):
    """Complete identity compressed into the credential-vault AAD binding."""

    preview_id: UUID
    preview_item_id: UUID
    tenant_id: UUID
    auth_user_id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    provider_account_sha256: str = Field(pattern=_SHA256.pattern)
    environment: str = Field(pattern=_ENVIRONMENT.pattern)
    qualification_id: UUID | None
    provider_tool_name: str = Field(pattern=_IDENTIFIER.pattern)
    capability_id: str = Field(pattern=_CAPABILITY.pattern)
    capability_version: str = Field(pattern=_SHA256.pattern)
    schema_hash: str = Field(pattern=_SHA256.pattern)
    evidence_revision_sha256: str = Field(pattern=_SHA256.pattern)
    connection_revision: int = Field(ge=1)
    connection_readiness: ConnectionReadiness
    preview_state_version: int = Field(ge=1)
    client_item_id: str = Field(pattern=_IDENTIFIER.pattern)
    payload_hash: str = Field(pattern=_SHA256.pattern)
    created_at: datetime
    expires_at: datetime

    @field_validator("created_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value, "preview_payload_binding_invalid")

    @model_validator(mode="after")
    def validate_ids(self) -> PreviewPayloadBinding:
        for value in (
            self.preview_id,
            self.preview_item_id,
            self.tenant_id,
            self.auth_user_id,
            self.workspace_id,
            self.connection_id,
        ):
            _non_nil(value, "preview_payload_binding_invalid")
        if self.qualification_id is not None:
            _non_nil(self.qualification_id, "preview_payload_binding_invalid")
        if self.expires_at != self.created_at + timedelta(seconds=PREVIEW_TTL_SECONDS):
            raise ValueError("preview_payload_binding_invalid")
        return self

    @property
    def vault_company_binding(self) -> str:
        return canonical_payload_hash(self.model_dump(mode="json"))


class StoredPreviewItem(_HostedModel):
    preview_item_id: UUID
    preview_id: UUID
    tenant_id: UUID
    auth_user_id: UUID
    workspace_id: UUID
    connection_id: UUID
    item_index: int = Field(ge=0, lt=MAX_BATCH_DOCUMENTS)
    client_item_id: str = Field(pattern=_IDENTIFIER.pattern)
    payload_hash: str = Field(pattern=_SHA256.pattern)
    document_type: str = Field(pattern=_DOCUMENT_TYPE.pattern)
    counterparty_display: str | None = Field(default=None, min_length=1, max_length=200)
    issue_date: date | None
    due_date: date | None
    financials: DocumentFinancials
    warnings: tuple[str, ...] = Field(default=(), max_length=100)
    accountant_review_points: tuple[str, ...] = Field(default=(), max_length=100)
    payload_envelope: CredentialEnvelope = Field(repr=False, exclude=True)
    created_at: datetime
    payload_purge_after: datetime

    @field_validator("created_at", "payload_purge_after")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value, "preview_item_timestamp_invalid")

    @model_validator(mode="after")
    def validate_item(self) -> StoredPreviewItem:
        for value in (
            self.preview_item_id,
            self.preview_id,
            self.tenant_id,
            self.auth_user_id,
            self.workspace_id,
            self.connection_id,
        ):
            _non_nil(value, "preview_item_binding_invalid")
        unconfirmed_purge = (
            self.created_at + timedelta(seconds=PREVIEW_TTL_SECONDS) + UNCONFIRMED_PAYLOAD_RETENTION
        )
        confirmed_purge_limit = (
            self.created_at + timedelta(seconds=PREVIEW_TTL_SECONDS) + CONFIRMED_PAYLOAD_RETENTION
        )
        if not unconfirmed_purge <= self.payload_purge_after <= confirmed_purge_limit:
            raise ValueError("preview_item_retention_invalid")
        envelope = CredentialEnvelope.model_validate(self.payload_envelope)
        if (
            envelope.id.int == 0
            or envelope.tenant_id != self.tenant_id
            or envelope.auth_user_id != self.auth_user_id
            or envelope.workspace_id != self.workspace_id
            or envelope.connection_id != self.connection_id
            or envelope.credential_type != HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE
            or envelope.revoked_at is not None
        ):
            raise ValueError("preview_item_envelope_invalid")
        return self

    def storage_record(self) -> dict[str, Any]:
        envelope = self.payload_envelope
        return {
            "id": str(self.preview_item_id),
            "preview_id": str(self.preview_id),
            "tenant_id": str(self.tenant_id),
            "auth_user_id": str(self.auth_user_id),
            "workspace_id": str(self.workspace_id),
            "connection_id": str(self.connection_id),
            "item_index": self.item_index,
            "client_item_id": self.client_item_id,
            "payload_hash": self.payload_hash,
            "document_type": self.document_type,
            "sanitized_summary": self.public_record(),
            "payload_envelope_id": str(envelope.id),
            "payload_key_version": envelope.key_version,
            "payload_nonce": envelope.nonce.hex(),
            "payload_ciphertext": envelope.ciphertext.hex(),
            "payload_aad_hash": envelope.aad_hash.hex(),
            "payload_envelope_created_at": envelope.created_at.isoformat(),
            "created_at": self.created_at.isoformat(),
            "payload_purge_after": self.payload_purge_after.isoformat(),
        }

    def public_record(self) -> dict[str, Any]:
        return {
            "client_item_id": self.client_item_id,
            "document_type": self.document_type,
            "counterparty_display": self.counterparty_display,
            "issue_date": self.issue_date.isoformat() if self.issue_date is not None else None,
            "due_date": self.due_date.isoformat() if self.due_date is not None else None,
            "financials": self.financials.model_dump(mode="json"),
            "warnings": list(self.warnings),
            "accountant_review_points": list(self.accountant_review_points),
        }


def preview_payload_hash(
    *,
    preview_id: UUID,
    tenant_id: UUID,
    auth_user_id: UUID,
    workspace_id: UUID,
    connection_id: UUID,
    provider: ProviderId,
    provider_account_sha256: str,
    environment: str,
    capability_id: str,
    capability_version: str,
    connection_revision: int,
    connection_readiness: ConnectionReadiness,
    items: Sequence[StoredPreviewItem],
) -> str:
    return canonical_payload_hash(
        {
            "preview_id": str(preview_id),
            "tenant_id": str(tenant_id),
            "auth_user_id": str(auth_user_id),
            "workspace_id": str(workspace_id),
            "connection_id": str(connection_id),
            "provider": provider.value,
            "provider_account_sha256": provider_account_sha256,
            "environment": environment,
            "capability_id": capability_id,
            "capability_version": capability_version,
            "connection_revision": connection_revision,
            "connection_readiness": connection_readiness.value,
            "items": [
                {
                    "client_item_id": item.client_item_id,
                    "payload_hash": item.payload_hash,
                }
                for item in items
            ],
        }
    )


class DocumentPreview(_HostedModel):
    preview_id: UUID
    tenant_id: UUID
    auth_user_id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    provider_account_sha256: str = Field(pattern=_SHA256.pattern)
    account_display_name: str = Field(min_length=1, max_length=200)
    environment: str = Field(pattern=_ENVIRONMENT.pattern)
    qualification_id: UUID | None
    provider_tool_name: str = Field(pattern=_IDENTIFIER.pattern)
    capability_id: str = Field(pattern=_CAPABILITY.pattern)
    capability_version: str = Field(pattern=_SHA256.pattern)
    schema_hash: str = Field(pattern=_SHA256.pattern)
    response_shape_hash: str = Field(pattern=_SHA256.pattern)
    evidence_revision_sha256: str = Field(pattern=_SHA256.pattern)
    connection_revision: int = Field(ge=1)
    connection_readiness: ConnectionReadiness
    payload_hash: str = Field(pattern=_SHA256.pattern)
    state: PreviewState
    state_version: int = Field(ge=1)
    currency: str = Field(pattern=_CURRENCY.pattern)
    subtotal: NonnegativeDecimalString
    discount_total: NonnegativeDecimalString
    vat_total: NonnegativeDecimalString
    withholding_tax_total: NonnegativeDecimalString
    grand_total: DecimalString
    warnings: tuple[str, ...] = Field(default=(), max_length=2500)
    accountant_review_points: tuple[str, ...] = Field(default=(), max_length=2500)
    items: tuple[StoredPreviewItem, ...] = Field(min_length=1, max_length=MAX_BATCH_DOCUMENTS)
    supersedes_preview_id: UUID | None = None
    created_at: datetime
    expires_at: datetime
    payload_purge_after: datetime
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None

    @field_validator(
        "created_at", "expires_at", "payload_purge_after", "confirmed_at", "cancelled_at"
    )
    @classmethod
    def validate_timestamp(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _aware_utc(value, "preview_timestamp_invalid")

    @field_validator("account_display_name")
    @classmethod
    def validate_account_display(cls, value: str) -> str:
        return _safe_text(value, "preview_binding_invalid")

    @model_validator(mode="after")
    def validate_preview(self) -> DocumentPreview:
        for value in (
            self.preview_id,
            self.tenant_id,
            self.auth_user_id,
            self.workspace_id,
            self.connection_id,
        ):
            _non_nil(value, "preview_binding_invalid")
        for value in (self.qualification_id, self.supersedes_preview_id):
            if value is not None:
                _non_nil(value, "preview_binding_invalid")
        if self.expires_at != self.created_at + timedelta(seconds=PREVIEW_TTL_SECONDS):
            raise ValueError("preview_ttl_invalid")
        if self.connection_readiness is not ConnectionReadiness.READY:
            raise ValueError("preview_binding_invalid")
        if (
            self.state in {PreviewState.PREPARED, PreviewState.AWAITING_CONFIRMATION}
            and self.state_version != 1
        ) or (
            self.state in {PreviewState.CONFIRMED, PreviewState.EXPIRED, PreviewState.CANCELLED}
            and self.state_version != 2
        ):
            raise ValueError("preview_state_invalid")
        unconfirmed_purge = self.expires_at + UNCONFIRMED_PAYLOAD_RETENTION
        if self.state in {PreviewState.PREPARED, PreviewState.AWAITING_CONFIRMATION}:
            if (
                self.confirmed_at is not None
                or self.cancelled_at is not None
                or self.payload_purge_after != unconfirmed_purge
            ):
                raise ValueError("preview_state_invalid")
        elif self.state is PreviewState.CONFIRMED:
            if (
                self.confirmed_at is None
                or self.cancelled_at is not None
                or not unconfirmed_purge
                <= self.payload_purge_after
                <= self.confirmed_at + CONFIRMED_PAYLOAD_RETENTION
            ):
                raise ValueError("preview_state_invalid")
        elif self.state is PreviewState.CANCELLED:
            if (
                self.cancelled_at is None
                or self.confirmed_at is not None
                or self.payload_purge_after != unconfirmed_purge
            ):
                raise ValueError("preview_state_invalid")
        elif (
            self.confirmed_at is not None
            or self.cancelled_at is not None
            or self.payload_purge_after != unconfirmed_purge
        ):
            raise ValueError("preview_state_invalid")

        client_ids = tuple(item.client_item_id for item in self.items)
        item_ids = tuple(item.preview_item_id for item in self.items)
        item_hashes = tuple(item.payload_hash for item in self.items)
        if (
            len(client_ids) != len(set(client_ids))
            or len(item_ids) != len(set(item_ids))
            or len(item_hashes) != len(set(item_hashes))
        ):
            raise ValueError("preview_items_not_unique")
        if tuple(item.item_index for item in self.items) != tuple(range(len(self.items))):
            raise ValueError("preview_item_order_invalid")
        for item in self.items:
            envelope = item.payload_envelope
            if (
                item.preview_id != self.preview_id
                or item.tenant_id != self.tenant_id
                or item.auth_user_id != self.auth_user_id
                or item.workspace_id != self.workspace_id
                or item.connection_id != self.connection_id
                or item.created_at != self.created_at
                or item.payload_purge_after != self.payload_purge_after
                or envelope.provider != self.provider.value
                or envelope.environment != self.environment
            ):
                raise ValueError("preview_item_binding_invalid")
        if any(item.financials.currency != self.currency for item in self.items):
            raise ValueError("document_currency_mismatch")
        totals = {
            "subtotal": sum((item.financials.subtotal for item in self.items), Decimal("0")),
            "discount_total": sum(
                (item.financials.discount_total for item in self.items), Decimal("0")
            ),
            "vat_total": sum((item.financials.vat_total for item in self.items), Decimal("0")),
            "withholding_tax_total": sum(
                (item.financials.withholding_tax_total for item in self.items), Decimal("0")
            ),
            "grand_total": sum((item.financials.grand_total for item in self.items), Decimal("0")),
        }
        if any(getattr(self, name) != value for name, value in totals.items()):
            raise ValueError("preview_total_mismatch")
        expected_hash = preview_payload_hash(
            preview_id=self.preview_id,
            tenant_id=self.tenant_id,
            auth_user_id=self.auth_user_id,
            workspace_id=self.workspace_id,
            connection_id=self.connection_id,
            provider=self.provider,
            provider_account_sha256=self.provider_account_sha256,
            environment=self.environment,
            capability_id=self.capability_id,
            capability_version=self.capability_version,
            connection_revision=self.connection_revision,
            connection_readiness=self.connection_readiness,
            items=self.items,
        )
        if self.payload_hash != expected_hash:
            raise ValueError("preview_payload_hash_invalid")
        return self

    def payload_binding(self, item: StoredPreviewItem) -> PreviewPayloadBinding:
        if item not in self.items:
            raise ValueError("preview_item_binding_invalid")
        return PreviewPayloadBinding(
            preview_id=self.preview_id,
            preview_item_id=item.preview_item_id,
            tenant_id=self.tenant_id,
            auth_user_id=self.auth_user_id,
            workspace_id=self.workspace_id,
            connection_id=self.connection_id,
            provider=self.provider,
            provider_account_sha256=self.provider_account_sha256,
            environment=self.environment,
            qualification_id=self.qualification_id,
            provider_tool_name=self.provider_tool_name,
            capability_id=self.capability_id,
            capability_version=self.capability_version,
            schema_hash=self.schema_hash,
            evidence_revision_sha256=self.evidence_revision_sha256,
            connection_revision=self.connection_revision,
            connection_readiness=self.connection_readiness,
            preview_state_version=1,
            client_item_id=item.client_item_id,
            payload_hash=item.payload_hash,
            created_at=self.created_at,
            expires_at=self.expires_at,
        )

    def transition(
        self,
        *,
        target_state: PreviewState,
        occurred_at: datetime,
        confirmed_payload_purge_after: datetime | None = None,
    ) -> DocumentPreview:
        target = PreviewState(target_state)
        if target not in _PREVIEW_TRANSITIONS[self.state]:
            raise ValueError("preview_state_invalid")
        timestamp = _aware_utc(occurred_at, "preview_timestamp_invalid")
        if timestamp < self.created_at:
            raise ValueError("preview_timestamp_invalid")
        if target is PreviewState.CONFIRMED and timestamp >= self.expires_at:
            raise ValueError("preview_expired")
        if target is PreviewState.EXPIRED and timestamp < self.expires_at:
            raise ValueError("preview_state_invalid")
        if target is PreviewState.CONFIRMED:
            purge_after = (
                self.payload_purge_after
                if confirmed_payload_purge_after is None
                else _aware_utc(
                    confirmed_payload_purge_after,
                    "preview_timestamp_invalid",
                )
            )
        elif confirmed_payload_purge_after is not None:
            raise ValueError("preview_state_invalid")
        else:
            purge_after = self.payload_purge_after
        transitioned = self.model_copy(
            update={
                "state": target,
                "state_version": self.state_version + 1,
                "confirmed_at": timestamp if target is PreviewState.CONFIRMED else None,
                "cancelled_at": timestamp if target is PreviewState.CANCELLED else None,
                "payload_purge_after": purge_after,
                "items": tuple(
                    item.model_copy(update={"payload_purge_after": purge_after})
                    for item in self.items
                ),
            }
        )
        return DocumentPreview.model_validate(transitioned)

    def storage_record(self) -> dict[str, Any]:
        return {
            "id": str(self.preview_id),
            "tenant_id": str(self.tenant_id),
            "auth_user_id": str(self.auth_user_id),
            "workspace_id": str(self.workspace_id),
            "connection_id": str(self.connection_id),
            "provider": self.provider.value,
            "provider_account_sha256": self.provider_account_sha256,
            "account_display_name": self.account_display_name,
            "environment": self.environment,
            "qualification_id": str(self.qualification_id) if self.qualification_id else None,
            "provider_tool_name": self.provider_tool_name,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "schema_hash": self.schema_hash,
            "response_shape_hash": self.response_shape_hash,
            "evidence_revision_sha256": self.evidence_revision_sha256,
            "connection_revision": self.connection_revision,
            "connection_readiness": self.connection_readiness.value,
            "payload_hash": self.payload_hash,
            "status": self.state.value,
            "state_version": self.state_version,
            "document_count": len(self.items),
            "currency": self.currency,
            "subtotal": _decimal_text(self.subtotal),
            "discount_total": _decimal_text(self.discount_total),
            "vat_total": _decimal_text(self.vat_total),
            "withholding_tax_total": _decimal_text(self.withholding_tax_total),
            "grand_total": _decimal_text(self.grand_total),
            "warning_count": len(self.warnings),
            "sanitized_summary": self.public_summary_record(),
            "warnings": list(self.warnings),
            "accountant_review_points": list(self.accountant_review_points),
            "supersedes_preview_id": (
                str(self.supersedes_preview_id) if self.supersedes_preview_id else None
            ),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "payload_purge_after": self.payload_purge_after.isoformat(),
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "cancelled_at": self.cancelled_at.isoformat() if self.cancelled_at else None,
        }

    def public_summary_record(self) -> dict[str, Any]:
        return {
            "workspace_id": str(self.workspace_id),
            "preview_id": str(self.preview_id),
            "state_version": self.state_version,
            "status": self.state.value,
            "provider": self.provider.value,
            "company_display_name": self.account_display_name,
            "environment": self.environment,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "document_count": len(self.items),
            "currency": self.currency,
            "subtotal": _decimal_text(self.subtotal),
            "discount_total": _decimal_text(self.discount_total),
            "vat_total": _decimal_text(self.vat_total),
            "withholding_tax_total": _decimal_text(self.withholding_tax_total),
            "grand_total": _decimal_text(self.grand_total),
            "warning_count": len(self.warnings),
            "expires_at": self.expires_at.isoformat(),
        }


class PreparedPreviewItem(_HostedModel):
    client_item_id: str
    payload_hash: str = Field(pattern=_SHA256.pattern)
    document_type: str
    counterparty_display: str | None
    issue_date: date | None
    due_date: date | None
    financials: DocumentFinancials
    warnings: tuple[str, ...]
    accountant_review_points: tuple[str, ...]


class PreparedDocumentPreview(_HostedModel):
    status: Literal["awaiting_confirmation"]
    workspace_id: UUID
    preview_id: UUID
    state_version: int = Field(ge=1)
    connection_id: UUID
    provider: ProviderId
    company_display_name: str
    environment: str
    capability_id: str
    capability_version: str
    document_count: int = Field(ge=1, le=MAX_BATCH_DOCUMENTS)
    currency: str = Field(pattern=_CURRENCY.pattern)
    subtotal: NonnegativeDecimalString
    discount_total: NonnegativeDecimalString
    vat_total: NonnegativeDecimalString
    withholding_tax_total: NonnegativeDecimalString
    grand_total: DecimalString
    warning_count: int = Field(ge=0)
    warnings: tuple[str, ...]
    accountant_review_points: tuple[str, ...]
    items: tuple[PreparedPreviewItem, ...]
    expires_at: datetime
    next_allowed_actions: tuple[Literal["render_document_preview"], ...] = (
        "render_document_preview",
    )

    @classmethod
    def from_preview(cls, preview: DocumentPreview) -> PreparedDocumentPreview:
        checked = DocumentPreview.model_validate(preview)
        return cls(
            status="awaiting_confirmation",
            workspace_id=checked.workspace_id,
            preview_id=checked.preview_id,
            state_version=checked.state_version,
            connection_id=checked.connection_id,
            provider=checked.provider,
            company_display_name=checked.account_display_name,
            environment=checked.environment,
            capability_id=checked.capability_id,
            capability_version=checked.capability_version,
            document_count=len(checked.items),
            currency=checked.currency,
            subtotal=checked.subtotal,
            discount_total=checked.discount_total,
            vat_total=checked.vat_total,
            withholding_tax_total=checked.withholding_tax_total,
            grand_total=checked.grand_total,
            warning_count=len(checked.warnings),
            warnings=checked.warnings,
            accountant_review_points=checked.accountant_review_points,
            items=tuple(
                PreparedPreviewItem(
                    client_item_id=item.client_item_id,
                    payload_hash=item.payload_hash,
                    document_type=item.document_type,
                    counterparty_display=item.counterparty_display,
                    issue_date=item.issue_date,
                    due_date=item.due_date,
                    financials=item.financials,
                    warnings=item.warnings,
                    accountant_review_points=item.accountant_review_points,
                )
                for item in checked.items
            ),
            expires_at=checked.expires_at,
        )


class OpenedPreviewItem(_HostedModel):
    preview_item_id: UUID
    client_item_id: str
    provider_payload: dict[str, Any] = Field(repr=False, exclude=True)

    @model_validator(mode="after")
    def freeze_payload(self) -> OpenedPreviewItem:
        copied = _json_copy(self.provider_payload)
        if not isinstance(copied, dict):
            raise ValueError("preview_payload_changed")
        object.__setattr__(self, "provider_payload", deep_freeze(copied))
        return self

    def provider_payload_copy(self) -> dict[str, Any]:
        copied = _json_copy(self.provider_payload)
        if not isinstance(copied, dict):
            raise ValueError("preview_payload_changed")
        return copied


class ConfirmableDocumentPreview(_HostedModel):
    preview: DocumentPreview
    opened_items: tuple[OpenedPreviewItem, ...] = Field(repr=False)

    def provider_payload_for(self, client_item_id: str) -> dict[str, Any]:
        matches = [
            item.provider_payload_copy()
            for item in self.opened_items
            if item.client_item_id == client_item_id
        ]
        if len(matches) != 1:
            raise ValueError("preview_item_not_found")
        return matches[0]


class OperationState(StrEnum):
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    DISPATCHING = "dispatching"
    SUCCEEDED = "succeeded"
    FAILED_PRE_DISPATCH = "failed_pre_dispatch"
    PROVIDER_REJECTED = "provider_rejected"
    OUTCOME_UNKNOWN = "outcome_unknown"
    NEEDS_MANUAL_REVIEW = "needs_manual_review"
    NOT_DISPATCHED = "not_dispatched"
    CANCELLED = "cancelled"


_OPERATION_TRANSITIONS: Mapping[OperationState, frozenset[OperationState]] = {
    OperationState.AWAITING_CONFIRMATION: frozenset(
        {
            OperationState.DISPATCHING,
            OperationState.FAILED_PRE_DISPATCH,
            OperationState.NOT_DISPATCHED,
            OperationState.CANCELLED,
        }
    ),
    OperationState.FAILED_PRE_DISPATCH: frozenset(
        {OperationState.DISPATCHING, OperationState.CANCELLED}
    ),
    OperationState.DISPATCHING: frozenset(
        {
            OperationState.SUCCEEDED,
            OperationState.PROVIDER_REJECTED,
            OperationState.OUTCOME_UNKNOWN,
        }
    ),
    OperationState.OUTCOME_UNKNOWN: frozenset(
        {OperationState.SUCCEEDED, OperationState.NEEDS_MANUAL_REVIEW}
    ),
    OperationState.SUCCEEDED: frozenset(),
    OperationState.PROVIDER_REJECTED: frozenset(),
    OperationState.NEEDS_MANUAL_REVIEW: frozenset(),
    OperationState.NOT_DISPATCHED: frozenset(),
    OperationState.CANCELLED: frozenset(),
}


class OperationItem(_HostedModel):
    operation_item_id: UUID
    preview_item_id: UUID
    item_index: int = Field(ge=0, lt=MAX_BATCH_DOCUMENTS)
    client_item_id: str = Field(pattern=_IDENTIFIER.pattern)
    payload_hash: str = Field(pattern=_SHA256.pattern)
    state: OperationState
    state_version: int = Field(ge=1)
    provider_result_identifier: str | None = Field(default=None, min_length=1, max_length=200)
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value, "operation_timestamp_invalid")

    @model_validator(mode="after")
    def validate_item(self) -> OperationItem:
        _non_nil(self.operation_item_id, "operation_items_invalid")
        _non_nil(self.preview_item_id, "operation_items_invalid")
        if self.updated_at < self.created_at:
            raise ValueError("operation_timestamp_invalid")
        if self.provider_result_identifier is not None:
            _safe_text(self.provider_result_identifier, "operation_transition_invalid")
        return self

    def transition(
        self,
        *,
        target_state: OperationState,
        occurred_at: datetime,
        provider_result_identifier: str | None = None,
    ) -> OperationItem:
        target = OperationState(target_state)
        if target not in _OPERATION_TRANSITIONS[self.state]:
            raise ValueError("operation_transition_invalid")
        timestamp = _aware_utc(occurred_at, "operation_timestamp_invalid")
        if timestamp < self.updated_at:
            raise ValueError("operation_timestamp_invalid")
        if (target is OperationState.SUCCEEDED) != (provider_result_identifier is not None):
            raise ValueError("operation_transition_invalid")
        return self.model_copy(
            update={
                "state": target,
                "state_version": self.state_version + 1,
                "provider_result_identifier": provider_result_identifier,
                "updated_at": timestamp,
            }
        )


class OperationEvent(_HostedModel):
    event_id: UUID
    operation_id: UUID
    operation_item_id: UUID | None = None
    from_state: OperationState | None
    to_state: OperationState
    state_version: int = Field(ge=1)
    sanitized_reason: str = Field(min_length=1, max_length=200, pattern=_IDENTIFIER.pattern)
    occurred_at: datetime

    @field_validator("occurred_at")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value, "operation_event_timestamp_invalid")


class HostedOperation(_HostedModel):
    operation_id: UUID
    preview_id: UUID
    tenant_id: UUID
    auth_user_id: UUID
    workspace_id: UUID
    connection_id: UUID
    provider: ProviderId
    environment: str = Field(pattern=_ENVIRONMENT.pattern)
    capability_id: str = Field(pattern=_CAPABILITY.pattern)
    capability_version: str = Field(pattern=_SHA256.pattern)
    connection_revision: int = Field(ge=1)
    payload_hash: str = Field(pattern=_SHA256.pattern)
    state: OperationState
    state_version: int = Field(ge=1)
    items: tuple[OperationItem, ...] = Field(min_length=1, max_length=MAX_BATCH_DOCUMENTS)
    events: tuple[OperationEvent, ...] = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    payload_purge_after: datetime

    @field_validator("created_at", "updated_at", "payload_purge_after")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        return _aware_utc(value, "operation_timestamp_invalid")

    @model_validator(mode="after")
    def validate_operation(self) -> HostedOperation:
        for value in (
            self.operation_id,
            self.preview_id,
            self.tenant_id,
            self.auth_user_id,
            self.workspace_id,
            self.connection_id,
        ):
            _non_nil(value, "operation_binding_invalid")
        if self.updated_at < self.created_at:
            raise ValueError("operation_timestamp_invalid")
        if not (
            self.created_at
            < self.payload_purge_after
            <= self.created_at + CONFIRMED_PAYLOAD_RETENTION
        ):
            raise ValueError("operation_retention_invalid")
        if len({item.operation_item_id for item in self.items}) != len(self.items) or tuple(
            item.item_index for item in self.items
        ) != tuple(range(len(self.items))):
            raise ValueError("operation_items_invalid")
        if any(
            event.operation_id != self.operation_id or event.occurred_at < self.created_at
            for event in self.events
        ):
            raise ValueError("operation_event_invalid")
        parent_events = tuple(event for event in self.events if event.operation_item_id is None)
        if (
            not parent_events
            or parent_events[-1].state_version != self.state_version
            or parent_events[-1].to_state is not self.state
        ):
            raise ValueError("operation_event_invalid")
        item_by_id = {item.operation_item_id: item for item in self.items}
        for event in self.events:
            if event.operation_item_id is not None and event.operation_item_id not in item_by_id:
                raise ValueError("operation_event_invalid")
        for item_id, item in item_by_id.items():
            item_events = tuple(
                event for event in self.events if event.operation_item_id == item_id
            )
            if item_events and (
                item_events[-1].state_version != item.state_version
                or item_events[-1].to_state is not item.state
            ):
                raise ValueError("operation_event_invalid")
        return self

    @classmethod
    def from_preview(
        cls,
        preview: DocumentPreview,
        *,
        operation_id: UUID,
        operation_item_ids: Sequence[UUID],
        event_id: UUID,
        now: datetime,
    ) -> HostedOperation:
        checked = DocumentPreview.model_validate(preview)
        timestamp = _aware_utc(now, "operation_timestamp_invalid")
        if len(operation_item_ids) != len(checked.items):
            raise ValueError("operation_items_invalid")
        items = tuple(
            OperationItem(
                operation_item_id=operation_item_id,
                preview_item_id=preview_item.preview_item_id,
                item_index=item_index,
                client_item_id=preview_item.client_item_id,
                payload_hash=preview_item.payload_hash,
                state=OperationState.AWAITING_CONFIRMATION,
                state_version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            for item_index, (operation_item_id, preview_item) in enumerate(
                zip(operation_item_ids, checked.items, strict=True)
            )
        )
        event = OperationEvent(
            event_id=event_id,
            operation_id=operation_id,
            from_state=None,
            to_state=OperationState.AWAITING_CONFIRMATION,
            state_version=1,
            sanitized_reason="explicit_confirmation",
            occurred_at=timestamp,
        )
        return cls(
            operation_id=operation_id,
            preview_id=checked.preview_id,
            tenant_id=checked.tenant_id,
            auth_user_id=checked.auth_user_id,
            workspace_id=checked.workspace_id,
            connection_id=checked.connection_id,
            provider=checked.provider,
            environment=checked.environment,
            capability_id=checked.capability_id,
            capability_version=checked.capability_version,
            connection_revision=checked.connection_revision,
            payload_hash=checked.payload_hash,
            state=OperationState.AWAITING_CONFIRMATION,
            state_version=1,
            items=items,
            events=(event,),
            created_at=timestamp,
            updated_at=timestamp,
            payload_purge_after=timestamp + CONFIRMED_PAYLOAD_RETENTION,
        )

    def transition(
        self,
        *,
        target_state: OperationState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
    ) -> HostedOperation:
        if target_state not in _OPERATION_TRANSITIONS[self.state]:
            raise ValueError("operation_transition_invalid")
        timestamp = _aware_utc(occurred_at, "operation_timestamp_invalid")
        if timestamp < self.updated_at:
            raise ValueError("operation_timestamp_invalid")
        next_version = self.state_version + 1
        event = OperationEvent(
            event_id=event_id,
            operation_id=self.operation_id,
            from_state=self.state,
            to_state=target_state,
            state_version=next_version,
            sanitized_reason=sanitized_reason,
            occurred_at=timestamp,
        )
        return self.model_copy(
            update={
                "state": target_state,
                "state_version": next_version,
                "events": (*self.events, event),
                "updated_at": timestamp,
            }
        )

    def transition_item(
        self,
        *,
        operation_item_id: UUID,
        target_state: OperationState,
        event_id: UUID,
        occurred_at: datetime,
        sanitized_reason: str,
        provider_result_identifier: str | None = None,
    ) -> HostedOperation:
        matches = tuple(
            (index, item)
            for index, item in enumerate(self.items)
            if item.operation_item_id == operation_item_id
        )
        if len(matches) != 1:
            raise ValueError("operation_transition_invalid")
        index, current = matches[0]
        transitioned = current.transition(
            target_state=target_state,
            occurred_at=occurred_at,
            provider_result_identifier=provider_result_identifier,
        )
        event = OperationEvent(
            event_id=event_id,
            operation_id=self.operation_id,
            operation_item_id=operation_item_id,
            from_state=current.state,
            to_state=transitioned.state,
            state_version=transitioned.state_version,
            sanitized_reason=sanitized_reason,
            occurred_at=transitioned.updated_at,
        )
        items = list(self.items)
        items[index] = transitioned
        return self.model_copy(
            update={
                "items": tuple(items),
                "events": (*self.events, event),
                "updated_at": max(self.updated_at, transitioned.updated_at),
            }
        )


def authoritative_payload_bytes(draft: DocumentCreateDraft) -> bytes:
    return canonical_payload_bytes(draft.authoritative_payload())


__all__ = [
    "BatchDocumentCreate",
    "CONFIRMED_PAYLOAD_RETENTION",
    "ConfirmableDocumentPreview",
    "DocumentCreateDraft",
    "DocumentFinancials",
    "DocumentLineAmounts",
    "DocumentPreview",
    "HOSTED_PREVIEW_PAYLOAD_CREDENTIAL_TYPE",
    "HostedOperation",
    "OpenedPreviewItem",
    "OperationEvent",
    "OperationItem",
    "OperationState",
    "PrepareDocumentCreate",
    "PreparedDocumentPreview",
    "PreparedPreviewItem",
    "PreviewPayloadBinding",
    "PreviewState",
    "SingleDocumentCreate",
    "StoredPreviewItem",
    "UNCONFIRMED_PAYLOAD_RETENTION",
    "authoritative_payload_bytes",
    "preview_payload_hash",
]
