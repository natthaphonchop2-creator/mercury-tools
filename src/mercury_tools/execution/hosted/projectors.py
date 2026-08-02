"""Reviewed projections from exact provider arguments to hosted previews."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from mercury_tools.canonical import canonical_payload_hash
from mercury_tools.catalog.models import ProviderMCPQualification

from .models import DocumentFinancials, DocumentLineAmounts
from .sanitization import sanitize_public_text

_DECIMAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,4})?$")
_CAPABILITY = re.compile(r"^documents\.([a-z][a-z0-9_]*)\.create$")


class ProjectorError(ValueError):
    """The exact reviewed projection cannot be derived from the provider call."""


@dataclass(frozen=True)
class ProjectedDocument:
    document_type: str
    counterparty_display: str
    issue_date: date
    due_date: date
    financials: DocumentFinancials


@dataclass(frozen=True)
class ReviewedInvoiceProjector:
    """Reviewed invoice mapping used only for the exact qualified schema."""

    projector_id: str
    projector_version: str
    provider: str
    environment: str
    provider_tool_name: str
    capability_id: str
    capability_version: str
    schema_hash: str
    currency_minor_units: Mapping[str, int]

    def matches(self, qualification: ProviderMCPQualification) -> bool:
        return (
            self.provider == qualification.provider
            and self.environment == qualification.environment
            and self.provider_tool_name == qualification.provider_tool_name
            and self.capability_id == qualification.normalized_capability
            and self.capability_version == qualification.capability_version_sha256
            and self.schema_hash == qualification.schema_hash
        )

    def project(self, provider_arguments: Mapping[str, Any]) -> ProjectedDocument:
        expected_root = {
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
        if not isinstance(provider_arguments, Mapping) or set(provider_arguments) != expected_root:
            raise ProjectorError("projector_payload_invalid")
        capability_match = _CAPABILITY.fullmatch(self.capability_id)
        if capability_match is None or capability_match.group(1) != "invoice":
            raise ProjectorError("projector_identity_invalid")

        currency = _currency(provider_arguments["currency"])
        minor_units = self.currency_minor_units.get(currency)
        if (
            not isinstance(minor_units, int)
            or isinstance(minor_units, bool)
            or not 0 <= minor_units <= 4
        ):
            raise ProjectorError("projector_currency_invalid")
        issue_date = _date(provider_arguments["issue_date"])
        due_date = _date(provider_arguments["due_date"])
        if due_date < issue_date:
            raise ProjectorError("projector_date_invalid")
        counterparty = _counterparty(provider_arguments["counterparty_name"])
        _nonempty_text(provider_arguments["reference"])

        raw_lines = provider_arguments["lines"]
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ProjectorError("projector_lines_invalid")
        lines = tuple(
            _project_line(raw_line, currency=currency, minor_units=minor_units)
            for raw_line in raw_lines
        )
        financials = DocumentFinancials(
            currency=currency,
            minor_units=minor_units,
            lines=lines,
            subtotal=_decimal(provider_arguments["subtotal"]),
            discount_total=_decimal(provider_arguments["discount_total"]),
            vat_total=_decimal(provider_arguments["vat_total"]),
            withholding_tax_total=_decimal(provider_arguments["withholding_tax_total"]),
            grand_total=_decimal(provider_arguments["grand_total"]),
        )
        return ProjectedDocument(
            document_type="invoice",
            counterparty_display=counterparty,
            issue_date=issue_date,
            due_date=due_date,
            financials=financials,
        )


class DocumentProjectorRegistry:
    """Closed reviewed mapping registry keyed by exact qualification identity."""

    def __init__(self, projectors: tuple[ReviewedInvoiceProjector, ...]) -> None:
        self._projectors = tuple(projectors)
        identities = tuple(
            (
                projector.provider,
                projector.environment,
                projector.provider_tool_name,
                projector.capability_id,
                projector.capability_version,
                projector.schema_hash,
            )
            for projector in self._projectors
        )
        if len(identities) != len(set(identities)):
            raise ValueError("projector_identity_duplicate")

    def resolve(self, qualification: ProviderMCPQualification) -> ReviewedInvoiceProjector | None:
        matches = tuple(
            projector for projector in self._projectors if projector.matches(qualification)
        )
        return matches[0] if len(matches) == 1 else None


def provider_call_hash(
    *,
    provider: str,
    environment: str,
    provider_tool_name: str,
    capability_id: str,
    capability_version: str,
    schema_hash: str,
    provider_arguments: Mapping[str, Any],
) -> str:
    """Hash the exact provider call independently from review presentation."""

    return canonical_payload_hash(
        {
            "provider": provider,
            "environment": environment,
            "provider_tool_name": provider_tool_name,
            "capability_id": capability_id,
            "capability_version": capability_version,
            "schema_hash": schema_hash,
            "provider_arguments": provider_arguments,
        }
    )


def _project_line(raw: Any, *, currency: str, minor_units: int) -> DocumentLineAmounts:
    expected_line = {
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
    if not isinstance(raw, Mapping) or set(raw) != expected_line:
        raise ProjectorError("projector_line_invalid")
    _display(raw["description"])
    if _currency(raw["currency"]) != currency:
        raise ProjectorError("projector_currency_invalid")
    return DocumentLineAmounts(
        currency=currency,
        minor_units=minor_units,
        quantity=_decimal(raw["quantity"]),
        unit_price=_decimal(raw["unit_price"]),
        discount_amount=_decimal(raw["discount_amount"]),
        vat_rate=_decimal(raw["vat_rate"]),
        vat_amount=_decimal(raw["vat_amount"]),
        withholding_rate=_decimal(raw["withholding_rate"]),
        withholding_amount=_decimal(raw["withholding_amount"]),
        line_total=_decimal(raw["line_total"]),
    )


def _currency(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z]{3}", value) is None:
        raise ProjectorError("projector_currency_invalid")
    return value


def _date(value: Any) -> date:
    if not isinstance(value, str):
        raise ProjectorError("projector_date_invalid")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ProjectorError("projector_date_invalid") from None


def _decimal(value: Any) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ProjectorError("projector_decimal_invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ProjectorError("projector_decimal_invalid") from None
    if not result.is_finite():
        raise ProjectorError("projector_decimal_invalid")
    return result


def _nonempty_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ProjectorError("projector_text_invalid")
    return value


def _display(value: Any) -> str:
    try:
        return sanitize_public_text(_nonempty_text(value), code="projector_text_invalid")
    except ValueError:
        raise ProjectorError("projector_text_invalid") from None


def _counterparty(value: Any) -> str:
    _display(value)
    return "[REDACTED_COUNTERPARTY]"


__all__ = [
    "DocumentProjectorRegistry",
    "ProjectedDocument",
    "ProjectorError",
    "ReviewedInvoiceProjector",
    "provider_call_hash",
]
