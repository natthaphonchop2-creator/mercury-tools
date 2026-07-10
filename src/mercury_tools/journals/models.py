"""Validation and FlowAccount payload mapping for General Journal Vouchers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MONEY_QUANTUM = Decimal("0.01")
SIDE_CODES = {"debit": 1, "credit": 3}


class JournalValidationError(ValueError):
    """Structured validation failure safe to return through MCP."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class PreparedJournal:
    document_date: str
    reference: str
    description: str
    total_debit: str
    total_credit: str
    preview_lines: list[dict[str, Any]]
    flowaccount_payload: dict[str, Any]

    def input_hash(
        self,
        *,
        workspace_id: str,
        connector_profile_id: str,
        environment: str,
    ) -> str:
        canonical = json.dumps(
            {
                "workspace_id": workspace_id,
                "connector_profile_id": connector_profile_id,
                "environment": environment,
                "payload": self.flowaccount_payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).casefold()


def _money(value: Any) -> Decimal:
    try:
        amount = Decimal(str(value)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise JournalValidationError(
            "invalid_amount",
            "Journal amounts must be decimal values.",
        ) from exc
    if not amount.is_finite() or amount <= 0:
        raise JournalValidationError(
            "invalid_amount",
            "Journal amounts must be greater than zero.",
        )
    return amount


def _json_amount(amount: Decimal) -> int | float:
    integral = amount.to_integral_value()
    return int(integral) if amount == integral else float(amount)


def _candidate_summary(row: dict[str, Any]) -> dict[str, str]:
    return {
        "code": str(row.get("code") or ""),
        "name": str(row.get("nameLocal") or row.get("nameForeign") or ""),
    }


def _resolve_account(
    line: dict[str, Any],
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    requested_code = _normalize_text(line.get("account_code"))
    requested_name = _normalize_text(line.get("account_name"))
    if not requested_code and not requested_name:
        raise JournalValidationError(
            "account_required",
            "Each journal line requires account_code or account_name.",
        )

    if requested_code:
        matches = [
            row for row in accounts if _normalize_text(row.get("code")) == requested_code
        ]
    else:
        matches = [
            row
            for row in accounts
            if requested_name
            in {
                _normalize_text(row.get("nameLocal")),
                _normalize_text(row.get("nameForeign")),
            }
        ]
    if not matches:
        raise JournalValidationError(
            "account_resolution_required",
            "No exact chart-of-account match was found.",
            details={
                "account_code": line.get("account_code"),
                "account_name": line.get("account_name"),
            },
        )
    if len(matches) != 1:
        raise JournalValidationError(
            "ambiguous_account",
            "More than one chart-of-account match was found.",
            details={"candidates": [_candidate_summary(row) for row in matches]},
        )
    return matches[0]


def prepare_general_journal(
    *,
    document_date: str,
    reference: str,
    description: str,
    lines: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    environment: str,
    note: str | None = None,
    remarks: str | None = None,
) -> PreparedJournal:
    """Resolve and validate one FlowAccount General Journal Voucher."""

    try:
        normalized_date = date.fromisoformat(str(document_date)).isoformat()
    except ValueError as exc:
        raise JournalValidationError(
            "invalid_document_date",
            "document_date must use YYYY-MM-DD.",
        ) from exc

    clean_reference = str(reference or "").strip()
    clean_description = str(description or "").strip()
    if environment == "production" and not clean_reference:
        raise JournalValidationError(
            "reference_required",
            "reference is required for production journals.",
        )
    if not clean_description:
        raise JournalValidationError(
            "description_required",
            "description is required.",
        )
    if len(lines) < 2:
        raise JournalValidationError(
            "insufficient_lines",
            "A journal requires at least two lines.",
        )

    debit = Decimal("0.00")
    credit = Decimal("0.00")
    body_lines: list[dict[str, Any]] = []
    preview_lines: list[dict[str, Any]] = []
    for line in lines:
        side = str(line.get("side") or "").strip().lower()
        if side not in SIDE_CODES:
            raise JournalValidationError(
                "invalid_side",
                "side must be debit or credit.",
            )
        amount = _money(line.get("amount"))
        account = _resolve_account(line, accounts)
        if side == "debit":
            debit += amount
        else:
            credit += amount
        line_description = str(line.get("description") or "").strip() or None
        body_lines.append(
            {
                "debitCredit": SIDE_CODES[side],
                "chartOfAccountId": int(account["id"]),
                "value": _json_amount(amount),
                "description": line_description,
            }
        )
        preview_lines.append(
            {
                "side": side,
                "account_code": str(account.get("code") or ""),
                "account_name": str(
                    account.get("nameLocal") or account.get("nameForeign") or ""
                ),
                "amount": f"{amount:.2f}",
                "description": line_description,
            }
        )

    if not debit or not credit or debit != credit:
        raise JournalValidationError(
            "unbalanced_journal",
            "Total debit must equal total credit.",
            details={
                "total_debit": f"{debit:.2f}",
                "total_credit": f"{credit:.2f}",
            },
        )

    payload = {
        "documentType": 51,
        "documentDate": normalized_date,
        "contactId": None,
        "contactName": "",
        "description": clean_description,
        "note": str(note).strip() if note else None,
        "remarks": str(remarks).strip() if remarks else None,
        "reference": clean_reference or None,
        "bookOfAccounts": body_lines,
    }
    return PreparedJournal(
        document_date=normalized_date,
        reference=clean_reference,
        description=clean_description,
        total_debit=f"{debit:.2f}",
        total_credit=f"{credit:.2f}",
        preview_lines=preview_lines,
        flowaccount_payload=payload,
    )
