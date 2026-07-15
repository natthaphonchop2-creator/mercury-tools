"""Strict canonical transaction and reconciliation evidence models."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mercury_tools.orchestration.models import (
    validate_accounting_reference,
    validate_accounting_source,
    validate_counterparty_key,
    validate_document_state,
    validate_evidence_locator,
    validate_record_identifier,
)
from mercury_tools.qualification.models import StrictSafeModel

MONEY_QUANTUM = Decimal("0.01")


def _normalized_money(value: Any, *, code: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(code) from exc
    if not amount.is_finite():
        raise ValueError(f"{code}_must_be_finite")
    try:
        return amount.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise ValueError(code) from exc


def _clean_text(value: str, *, casefold: bool = False) -> str:
    cleaned = re.sub(r" +", " ", value.strip(" "))
    return cleaned.casefold() if casefold else cleaned


def _normalized_text(
    value: Any,
    *,
    code: str,
    casefold: bool = False,
    allow_empty: bool = False,
) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    cleaned = _clean_text(value, casefold=casefold)
    if not cleaned and not allow_empty:
        raise ValueError(code)
    return cleaned


def _normalized_text_tuple(value: Any, *, code: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping))
        or not isinstance(value, Sequence)
    ):
        raise ValueError(code)
    return tuple(_normalized_text(item, code=code) for item in value)


def _normalized_record_identifier(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    cleaned = _clean_text(value)
    if not cleaned:
        raise ValueError(code)
    return validate_record_identifier(cleaned, code=code)


def _normalized_reference(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    return validate_accounting_reference(_clean_text(value), code=code)


def _normalized_counterparty_key(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    return validate_counterparty_key(_clean_text(value), code=code)


def _normalized_source(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    return validate_accounting_source(_clean_text(value), code=code)


def _normalized_document_state(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    return validate_document_state(_clean_text(value), code=code)


def _normalized_evidence_locator(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    return validate_evidence_locator(_clean_text(value), code=code)


def _normalized_evidence_locators(value: Any, *, code: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping))
        or not isinstance(value, Sequence)
    ):
        raise ValueError(code)
    return tuple(_normalized_evidence_locator(item, code=code) for item in value)


class CanonicalTransaction(StrictSafeModel):
    transaction_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=128)
    amount: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    transaction_date: date
    reference: str | None = Field(default=None, max_length=512)
    counterparty_key: str | None = Field(default=None, max_length=512)
    document_state: str = Field(min_length=1, max_length=128)
    evidence_refs: tuple[str, ...] = Field(min_length=1)

    @field_validator("amount", mode="before")
    @classmethod
    def normalize_amount(cls, value: Any) -> Decimal:
        return _normalized_money(value, code="transaction_amount_invalid")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: Any) -> str:
        return _normalized_text(value, code="transaction_text_invalid").upper()

    @field_validator("transaction_id", mode="before")
    @classmethod
    def normalize_transaction_id(cls, value: Any) -> str:
        return _normalized_record_identifier(value, code="transaction_text_invalid")

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: Any) -> str:
        return _normalized_source(value, code="transaction_source_invalid")

    @field_validator("document_state", mode="before")
    @classmethod
    def normalize_document_state(cls, value: Any) -> str:
        return _normalized_document_state(value, code="transaction_document_state_invalid")

    @field_validator("reference", mode="before")
    @classmethod
    def normalize_optional_reference(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip(" "):
            return None
        cleaned = _normalized_reference(value, code="transaction_reference_invalid")
        return cleaned or None

    @field_validator("counterparty_key", mode="before")
    @classmethod
    def normalize_optional_counterparty_key(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip(" "):
            return None
        cleaned = _normalized_counterparty_key(
            value,
            code="transaction_counterparty_key_invalid",
        )
        return cleaned or None

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def normalize_evidence_refs(cls, value: Any) -> tuple[str, ...]:
        return _normalized_evidence_locators(
            value,
            code="transaction_evidence_refs_invalid",
        )

    @model_validator(mode="after")
    def validate_canonical_data(self) -> CanonicalTransaction:
        if any(not item for item in self.evidence_refs):
            raise ValueError("transaction_evidence_refs_invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("transaction_evidence_refs_invalid")
        return self


class ReconciliationPolicy(StrictSafeModel):
    amount_tolerance: Decimal = Field(default=Decimal("0.00"), ge=0)
    difference_tolerance: Decimal = Field(default=Decimal("0.00"), ge=0)
    date_tolerance_days: int = Field(default=0, ge=0, le=31)
    require_document_state_match: bool = True
    require_identity_match: bool = True

    @field_validator("amount_tolerance", "difference_tolerance", mode="before")
    @classmethod
    def normalize_tolerance(cls, value: Any) -> Decimal:
        return _normalized_money(value, code="reconciliation_tolerance_invalid")

    @model_validator(mode="after")
    def validate_tolerance_order(self) -> ReconciliationPolicy:
        if self.amount_tolerance > self.difference_tolerance:
            raise ValueError("reconciliation_difference_tolerance_invalid")
        return self


class PairEvidence(StrictSafeModel):
    classification: Literal["matched", "difference"]
    left_transaction_id: str
    right_transaction_id: str
    amount_difference: Decimal = Field(ge=0)
    date_difference_days: int = Field(ge=0)
    matched_fields: tuple[str, ...]
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    candidate_count: int = Field(ge=1)
    tie_breaker: Literal["none", "stable_transaction_id"] = "none"

    @field_validator("left_transaction_id", "right_transaction_id", mode="before")
    @classmethod
    def validate_transaction_ids(cls, value: Any) -> str:
        return _normalized_record_identifier(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @field_validator("matched_fields", mode="before")
    @classmethod
    def validate_matched_fields(cls, value: Any) -> tuple[str, ...]:
        return _normalized_text_tuple(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: Any) -> tuple[str, ...]:
        return _normalized_evidence_locators(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @model_validator(mode="after")
    def validate_evidence_data(self) -> PairEvidence:
        allowed_match_fields = {
            "amount",
            "counterparty_key",
            "currency",
            "document_state",
            "reference",
            "transaction_date",
        }
        if not self.matched_fields or any(
            field not in allowed_match_fields for field in self.matched_fields
        ):
            raise ValueError("reconciliation_matched_fields_invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("reconciliation_evidence_refs_invalid")
        return self


class DuplicateEvidence(StrictSafeModel):
    side: Literal["left", "right"]
    canonical_transaction_id: str
    transaction_ids: tuple[str, ...] = Field(min_length=2)
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: Literal["exact_canonical_duplicate"] = "exact_canonical_duplicate"

    @field_validator("canonical_transaction_id", mode="before")
    @classmethod
    def validate_canonical_id(cls, value: Any) -> str:
        return _normalized_record_identifier(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @field_validator("transaction_ids", mode="before")
    @classmethod
    def validate_transaction_ids(cls, value: Any) -> tuple[str, ...]:
        if (
            isinstance(value, (str, bytes, bytearray, Mapping))
            or not isinstance(value, Sequence)
        ):
            raise ValueError("reconciliation_evidence_text_invalid")
        return tuple(
            _normalized_record_identifier(
                item,
                code="reconciliation_evidence_text_invalid",
            )
            for item in value
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: Any) -> tuple[str, ...]:
        return _normalized_evidence_locators(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @model_validator(mode="after")
    def validate_evidence_data(self) -> DuplicateEvidence:
        if len(set(self.transaction_ids)) != len(self.transaction_ids):
            raise ValueError("reconciliation_duplicate_ids_invalid")
        if self.canonical_transaction_id not in self.transaction_ids:
            raise ValueError("reconciliation_duplicate_ids_invalid")
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("reconciliation_evidence_refs_invalid")
        return self


class UnmatchedEvidence(StrictSafeModel):
    side: Literal["left", "right"]
    transaction_id: str
    evidence_refs: tuple[str, ...] = Field(min_length=1)
    reason: Literal["candidate_contention", "no_eligible_candidate"] = (
        "no_eligible_candidate"
    )

    @field_validator("transaction_id", mode="before")
    @classmethod
    def validate_transaction_id(cls, value: Any) -> str:
        return _normalized_record_identifier(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def validate_evidence_refs(cls, value: Any) -> tuple[str, ...]:
        return _normalized_evidence_locators(
            value,
            code="reconciliation_evidence_text_invalid",
        )

    @model_validator(mode="after")
    def validate_evidence_data(self) -> UnmatchedEvidence:
        if len(set(self.evidence_refs)) != len(self.evidence_refs):
            raise ValueError("reconciliation_evidence_refs_invalid")
        return self


class ReconciliationResult(StrictSafeModel):
    matched: tuple[PairEvidence, ...] = ()
    differences: tuple[PairEvidence, ...] = ()
    duplicates: tuple[DuplicateEvidence, ...] = ()
    unmatched: tuple[UnmatchedEvidence, ...] = ()
