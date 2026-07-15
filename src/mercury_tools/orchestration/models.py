"""Secret-safe data contracts for host-orchestrated Cross-MCP work."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from mercury_tools.qualification.models import StrictSafeModel

_MAX_ACCOUNTING_TEXT_LENGTH = 512
_DISALLOWED_TEXT_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"})
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_REFERENCE_TOKEN_SEPARATORS = frozenset("-/.#")
_IDENTIFIER_SEPARATORS = frozenset("-")
_ENDPOINT_ATOM_BOUNDARY = re.compile(r"[\s(){}\[\]<>\"'`,;]+")
_AMOUNT_TEXT = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,6})?$")
_IPV4_DECIMAL_COMPONENT = re.compile(r"^(?:0|[1-9][0-9]*)$")
_IPV4_OCTAL_COMPONENT = re.compile(r"^0[0-7]+$")
_IPV4_HEX_COMPONENT = re.compile(r"^0[xX][0-9A-Fa-f]+$")
_ACCOUNTING_DATE_DMY = re.compile(r"^([0-9]{2})([./])([0-9]{2})\2([0-9]{4})$")
_ACCOUNTING_DATE_ISO = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_FORBIDDEN_HANDOFF_FIELDS = frozenset(
    {
        "approval",
        "approval_state",
        "authorization",
        "command",
        "credential",
        "credentials",
        "destination",
        "destination_override",
        "endpoint",
        "function",
        "function_name",
        "instruction",
        "instructions",
        "password",
        "prompt",
        "script",
        "secret",
        "system_prompt",
        "tool",
        "tool_name",
        "token",
        "url",
    }
)
_FORBIDDEN_PAYLOAD_KEYS = _FORBIDDEN_HANDOFF_FIELDS | {
    "api_key",
    "client_secret",
    "executable",
    "oauth_token",
    "raw_provider_response",
    "refresh_token",
}
_FORBIDDEN_FIELD_TOKENS = frozenset(
    {
        "approval",
        "authorization",
        "command",
        "credential",
        "credentials",
        "destination",
        "endpoint",
        "executable",
        "function",
        "instruction",
        "instructions",
        "password",
        "prompt",
        "script",
        "secret",
        "token",
        "tool",
        "url",
    }
)
_BOUNDARY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
_WORKFLOW_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_CAPABILITY_IDENTIFIER = re.compile(
    r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*)+$"
)
_FIELD_IDENTIFIER = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
_ACTION_VERSION_IDENTIFIER = re.compile(r"^av_[A-Za-z0-9][A-Za-z0-9_-]{0,123}$")
_SIDE_EFFECT_IDENTIFIER = re.compile(
    r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*(?:\.[a-z][a-z0-9]*(?:_[a-z0-9]+)*)+$"
)
_ISSUANCE_IDENTIFIER = re.compile(r"^apr_[0-9a-f]{32}$")
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_SAFE_PURPOSE_TEXT = re.compile(r"^[\w\s.,()&+%'-]+$")
_PURPOSE_PREFIXES = frozenset(
    {
        "archive",
        "classify",
        "compare",
        "export",
        "gather",
        "prepare",
        "publish",
        "reconcile",
        "record",
        "report",
        "review",
        "summarize",
    }
)
_PURPOSE_WORDS = _PURPOSE_PREFIXES | {
    "a",
    "account",
    "accounts",
    "accounting",
    "adjustment",
    "adjustments",
    "an",
    "and",
    "approval",
    "bank",
    "cash",
    "customer",
    "customers",
    "data",
    "destination",
    "difference",
    "differences",
    "document",
    "documents",
    "end",
    "erp",
    "evidence",
    "external",
    "file",
    "files",
    "findings",
    "for",
    "from",
    "in",
    "into",
    "invoice",
    "invoices",
    "marketplace",
    "month",
    "of",
    "on",
    "one",
    "payable",
    "payables",
    "payment",
    "payments",
    "payout",
    "payouts",
    "period",
    "receipt",
    "receipts",
    "receivable",
    "receivables",
    "reconciliation",
    "record",
    "records",
    "refund",
    "refunds",
    "report",
    "reports",
    "result",
    "results",
    "settlement",
    "settlements",
    "sheet",
    "source",
    "sources",
    "supplier",
    "suppliers",
    "the",
    "to",
    "transaction",
    "transactions",
    "with",
}
_CAPABILITY_OPERATIONS = frozenset(
    {
        "export",
        "import",
        "list",
        "read",
        "reconcile",
        "review",
        "search",
        "send",
        "share",
        "upload",
        "write",
    }
)
_SIDE_EFFECT_OPERATIONS = frozenset(
    {
        "append",
        "archive",
        "create",
        "delete",
        "export",
        "publish",
        "send",
        "share",
        "update",
        "upload",
        "write",
    }
)
_REDACTION_POLICIES = frozenset(
    {
        "exclude_credentials",
        "exclude_personal_identifiers",
        "minimum_required_fields",
    }
)
_RETENTION_LIMITS = frozenset(
    {
        "no_retention",
        "one_workflow_run",
        "until_approval_expiry",
    }
)
_FALLBACKS = frozenset(
    {
        "request_connect_or_upload",
        "request_upload",
        "stop_without_source",
    }
)
_APPROVAL_POINTS = frozenset(
    {
        "before_destination_write",
        "before_erp_write",
    }
)
_EVIDENCE_REQUIREMENTS = frozenset(
    {
        "immutable_evidence_reference",
        "period_and_source_reference",
        "source_record_reference",
    }
)
_BLOCKED_ACTIONS = frozenset(
    {
        "delete_record",
        "execute_external_tool",
        "infer_missing_bank_feed",
        "infer_missing_source",
        "send_email",
        "share_file",
        "store_credentials",
    }
)
_ACCOUNTING_COMPACT_REFERENCE_PREFIXES = (
    "purchase-order",
    "bank-payment",
    "bank-transfer",
    "credit-note",
    "debit-note",
    "sales-order",
    "ใบกำกับภาษี",
    "ใบแจ้งหนี้",
    "invoice",
    "payment",
    "receipt",
    "transfer",
    "ใบเสร็จ",
    "เลขที่",
    "order",
    "inv",
)
_ACCOUNTING_IDENTIFIER_LABELS = (
    "bank payment",
    "bank transfer",
    "credit note",
    "debit note",
    "invoice",
    "order",
    "payment",
    "purchase order",
    "receipt",
    "sales order",
    "transfer",
    "ใบกำกับภาษี",
    "ใบแจ้งหนี้",
    "ใบเสร็จ",
    "เลขที่",
)
_ACCOUNTING_DATE_LABELS = (
    "document date",
    "วันที่เอกสาร",
    "เอกสารวันที่",
)
_ACCOUNTING_DECIMAL_LABELS = ("vat",)
_LEGACY_IPV4_COMPONENT_LIMITS = (
    (0xFFFFFFFF,),
    (0xFF, 0xFFFFFF),
    (0xFF, 0xFF, 0xFFFF),
    (0xFF, 0xFF, 0xFF, 0xFF),
)
_COUNTERPARTY_PREFIXES = frozenset(
    {
        "counterparty",
        "cust",
        "customer",
        "supplier",
        "vendor",
        "คู่ค้า",
        "ผู้ขาย",
        "ลูกค้า",
        "เลขที่",
    }
)
_SOURCE_ROOTS = frozenset(
    {
        "bank",
        "csv",
        "erp",
        "express",
        "flowaccount",
        "manual",
        "marketplace",
        "peak",
        "settlement",
        "sheets",
        "statement",
        "upload",
    }
)
_DOCUMENT_STATES = frozenset(
    {
        "cancelled",
        "closed",
        "draft",
        "issued",
        "open",
        "overdue",
        "paid",
        "partially_paid",
        "pending",
        "refunded",
        "sent",
        "settled",
        "void",
    }
)
_APPROVAL_STATUSES = frozenset(
    {
        "approved",
        "blocked",
        "difference",
        "duplicate",
        "matched",
        "pending_review",
        "ready",
        "rejected",
        "unmatched",
    }
)
_EVIDENCE_NAMESPACES = frozenset(
    {
        "bank",
        "erp",
        "evidence",
        "left",
        "marketplace",
        "right",
        "settlement",
        "statement",
        "upload",
    }
)
_EVIDENCE_RECORD_KINDS = frozenset(
    {
        "document",
        "invoice",
        "payment",
        "payout",
        "receipt",
        "record",
        "row",
        "shared",
        "statement",
        "transaction",
    }
)
_APPROVAL_FIELD_SCHEMAS = {
    "amount": "amount",
    "counterparty_key": "counterparty_key",
    "currency": "currency",
    "date": "date",
    "document_date": "date",
    "document_state": "document_state",
    "evidence_ref": "evidence_ref",
    "reference": "reference",
    "source": "source",
    "state": "document_state",
    "status": "status",
    "transaction_date": "date",
    "transaction_id": "transaction_id",
}


def _normalized_field_name(value: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", snake_case.casefold()).strip("_")


def _clean_required_string(value: Any, *, code: str) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    cleaned = re.sub(r"\s+", " ", value.strip())
    if not cleaned:
        raise ValueError(code)
    return cleaned


def _clean_string_tuple(value: Any, *, code: str) -> tuple[str, ...]:
    if (
        isinstance(value, (str, bytes, bytearray, Mapping))
        or not isinstance(value, Sequence)
    ):
        raise ValueError(code)
    return tuple(_clean_required_string(item, code=code) for item in value)


def _validate_identifier(value: str, *, pattern: re.Pattern[str], code: str) -> None:
    if not pattern.fullmatch(value):
        raise ValueError(code)


def _validate_controlled_values(
    values: tuple[str, ...],
    *,
    allowed: frozenset[str],
    code: str,
) -> None:
    if any(value not in allowed for value in values):
        raise ValueError(code)


def _has_disallowed_text_character(value: str) -> bool:
    return any(
        unicodedata.category(character) in _DISALLOWED_TEXT_CATEGORIES
        or (unicodedata.category(character) == "Zs" and character != " ")
        for character in value
    )


def _is_ip_host(host: str) -> bool:
    try:
        ipaddress.ip_address(host.rstrip("."))
    except ValueError:
        return False
    return True


def _parse_ipv4_component(value: str) -> int | None:
    if _IPV4_HEX_COMPONENT.fullmatch(value):
        return int(value[2:], 16)
    if _IPV4_OCTAL_COMPONENT.fullmatch(value):
        return int(value, 8)
    if _IPV4_DECIMAL_COMPONENT.fullmatch(value):
        return int(value, 10)
    return None


def _is_legacy_ipv4_host(host: str) -> bool:
    parts = host.split(".")
    if not 1 <= len(parts) <= len(_LEGACY_IPV4_COMPONENT_LIMITS):
        return False
    limits = _LEGACY_IPV4_COMPONENT_LIMITS[len(parts) - 1]
    values = tuple(_parse_ipv4_component(part) for part in parts)
    return all(
        value is not None and value <= limit
        for value, limit in zip(values, limits, strict=True)
    )


def _is_dns_host(host: str) -> bool:
    normalized = host.rstrip(".")
    if "." not in normalized:
        return False
    final_label = normalized.rsplit(".", maxsplit=1)[-1]
    if not any(unicodedata.category(character).startswith("L") for character in final_label):
        return False
    try:
        ascii_host = normalized.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    labels = ascii_host.split(".")
    return all(_DNS_LABEL.fullmatch(label) for label in labels)


def _is_file_path(value: str) -> bool:
    return (
        value.startswith(("/", "\\", "./", ".\\", "../", "..\\", "~/", "~\\"))
        or bool(re.match(r"^[a-z]:[\\/]", value, flags=re.IGNORECASE))
        or "\\" in value
    )


def _is_endpoint_atom(value: str) -> bool:
    candidate = value.strip(".!?")
    if not candidate:
        return False
    if (
        candidate.startswith("//")
        or "://" in candidate
        or _is_file_path(candidate)
        or bool(re.match(r"^[a-z][a-z0-9+.-]*:", candidate, flags=re.IGNORECASE))
    ):
        return True

    route_match = re.search(r"[/?#\\]", candidate)
    authority = candidate if route_match is None else candidate[: route_match.start()]
    if not authority:
        return False
    if _is_ip_host(authority):
        return True
    if authority.startswith("[") and "]" in authority:
        host = authority[1 : authority.index("]")]
        if _is_ip_host(host):
            return True

    host = authority.rsplit("@", maxsplit=1)[-1]
    if host.count(":") == 1:
        possible_host, possible_port = host.rsplit(":", maxsplit=1)
        if possible_host and possible_port.isdecimal():
            return True
    if _is_ip_host(host):
        return True
    if host.casefold() == "localhost":
        return True
    if _is_legacy_ipv4_host(host):
        return True
    return _is_dns_host(host)


def _is_endpoint_like(value: str) -> bool:
    return any(_is_endpoint_atom(atom) for atom in _ENDPOINT_ATOM_BOUNDARY.split(value) if atom)


def _require_plain_text(value: Any, *, code: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(code)
    if (
        not value
        or len(value) > max_length
        or value != value.strip()
        or re.sub(r" +", " ", value) != value
    ):
        raise ValueError(code)
    if _has_disallowed_text_character(value):
        raise ValueError("cross_mcp_control_character_forbidden")
    return value


def _is_segmented_unicode_token(value: str, *, separators: frozenset[str]) -> bool:
    if not value or value[0] in separators or value[-1] in separators:
        return False
    previous_was_separator = False
    for character in value:
        if character in separators:
            if previous_was_separator:
                return False
            previous_was_separator = True
            continue
        if unicodedata.category(character)[0] not in {"L", "M", "N"}:
            return False
        previous_was_separator = False
    return True


def _has_cased_upper(value: str) -> bool:
    return any(character.isupper() and not character.islower() for character in value)


def _is_compact_reference_suffix(value: str) -> bool:
    return all(component.isdecimal() for component in value.split("-"))


def _is_compact_accounting_reference(value: str) -> bool:
    folded = value.casefold()
    for prefix in _ACCOUNTING_COMPACT_REFERENCE_PREFIXES:
        prefix_length = len(prefix)
        if (
            folded.startswith(prefix)
            and len(value) > prefix_length
            and value[prefix_length] in _REFERENCE_TOKEN_SEPARATORS
            and _is_compact_reference_suffix(value[prefix_length + 1 :])
        ):
            return True
    return False


def _is_labeled_identifier_suffix(value: str) -> bool:
    return value.isdecimal() or _is_compact_accounting_reference(value)


def _is_accounting_date_suffix(value: str) -> bool:
    if _ACCOUNTING_DATE_ISO.fullmatch(value):
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
        return True
    match = _ACCOUNTING_DATE_DMY.fullmatch(value)
    if match is None:
        return False
    day, _, month, year = match.groups()
    try:
        date(int(year), int(month), int(day))
    except ValueError:
        return False
    return True


def _is_accounting_decimal_suffix(value: str) -> bool:
    return _AMOUNT_TEXT.fullmatch(value) is not None


def _has_labeled_suffix(
    value: str,
    *,
    labels: tuple[str, ...],
    suffix_validator: Callable[[str], bool],
) -> bool:
    folded = value.casefold()
    for label in labels:
        marker = f"{label} "
        if folded.startswith(marker):
            suffix = value[len(marker) :]
            return " " not in suffix and suffix_validator(suffix)
    return False


def _is_labeled_accounting_reference(value: str) -> bool:
    return (
        _has_labeled_suffix(
            value,
            labels=_ACCOUNTING_IDENTIFIER_LABELS,
            suffix_validator=_is_labeled_identifier_suffix,
        )
        or _has_labeled_suffix(
            value,
            labels=_ACCOUNTING_DATE_LABELS,
            suffix_validator=_is_accounting_date_suffix,
        )
        or _has_labeled_suffix(
            value,
            labels=_ACCOUNTING_DECIMAL_LABELS,
            suffix_validator=_is_accounting_decimal_suffix,
        )
    )


def validate_accounting_reference(
    value: Any,
    *,
    code: str,
    max_length: int = _MAX_ACCOUNTING_TEXT_LENGTH,
) -> str:
    """Parse one explicit compact or labelled accounting reference."""

    reference = _require_plain_text(value, code=code, max_length=max_length)
    if _is_compact_accounting_reference(reference) or _is_labeled_accounting_reference(
        reference
    ):
        return reference
    if reference.isascii() and reference.isdecimal():
        raise ValueError(code)
    if _is_endpoint_like(reference):
        raise ValueError("cross_mcp_url_forbidden")
    raise ValueError(code)


def validate_record_identifier(
    value: Any,
    *,
    code: str,
    max_length: int = 256,
) -> str:
    identifier = _require_plain_text(value, code=code, max_length=max_length)
    if " " in identifier or not _is_segmented_unicode_token(
        identifier,
        separators=_IDENTIFIER_SEPARATORS,
    ):
        raise ValueError(code)
    return identifier


def validate_counterparty_key(value: Any, *, code: str) -> str:
    key = _require_plain_text(value, code=code, max_length=256)
    if _is_endpoint_like(key) or " " in key:
        if _is_endpoint_like(key):
            raise ValueError("cross_mcp_url_forbidden")
        raise ValueError(code)
    if not _is_segmented_unicode_token(key, separators=frozenset("-.")):
        raise ValueError(code)
    if any(character.isdecimal() for character in key) or _has_cased_upper(key):
        return key
    prefix, separator, suffix = key.partition("-")
    if separator and prefix.casefold() in _COUNTERPARTY_PREFIXES and suffix:
        return key
    raise ValueError(code)


def validate_accounting_source(value: Any, *, code: str) -> str:
    source = _require_plain_text(value, code=code, max_length=128).casefold()
    root, separator, suffix = source.partition("-")
    if root not in _SOURCE_ROOTS:
        raise ValueError(code)
    if separator and not _is_segmented_unicode_token(
        suffix,
        separators=_IDENTIFIER_SEPARATORS,
    ):
        raise ValueError(code)
    return source


def validate_document_state(value: Any, *, code: str) -> str:
    state = _require_plain_text(value, code=code, max_length=128).casefold()
    if state not in _DOCUMENT_STATES:
        raise ValueError(code)
    return state


def validate_approval_status(value: Any, *, code: str) -> str:
    status = _require_plain_text(value, code=code, max_length=128).casefold()
    if status not in _APPROVAL_STATUSES:
        raise ValueError(code)
    return status


def validate_currency(value: Any, *, code: str) -> str:
    currency = _require_plain_text(value, code=code, max_length=3)
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError(code)
    return currency


def _is_evidence_locator_id(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,126}[A-Za-z0-9])?", value))


def validate_evidence_locator(value: Any, *, code: str) -> str:
    locator = _require_plain_text(value, code=code, max_length=256)
    if " " in locator:
        raise ValueError(code)
    parts = locator.split(":")
    if len(parts) == 2:
        namespace, locator_id = parts
        if namespace in _EVIDENCE_NAMESPACES and _is_evidence_locator_id(locator_id):
            return locator
    elif len(parts) == 3:
        namespace, record_kind, locator_id = parts
        if (
            namespace in _EVIDENCE_NAMESPACES
            and record_kind in _EVIDENCE_RECORD_KINDS
            and _is_evidence_locator_id(locator_id)
        ):
            return locator
    raise ValueError(code)


def _validate_capability(value: str, *, code: str) -> None:
    if (
        not _CAPABILITY_IDENTIFIER.fullmatch(value)
        or value.split(".", maxsplit=1)[0].startswith("mcp")
        or value.rsplit(".", maxsplit=1)[-1] not in _CAPABILITY_OPERATIONS
    ):
        raise ValueError(code)


def _validate_side_effect(value: str) -> None:
    if (
        not _SIDE_EFFECT_IDENTIFIER.fullmatch(value)
        or value.split(".", maxsplit=1)[0].startswith("mcp")
        or value.rsplit(".", maxsplit=1)[-1] not in _SIDE_EFFECT_OPERATIONS
    ):
        raise ValueError("approval_side_effect_invalid")


def _validate_purpose(value: str) -> None:
    if _SIDE_EFFECT_IDENTIFIER.fullmatch(value):
        _validate_side_effect(value)
        return
    first_word = value.split(maxsplit=1)[0].casefold()
    words = re.findall(r"[a-z0-9]+", value.casefold())
    if (
        not _SAFE_PURPOSE_TEXT.fullmatch(value)
        or first_word not in _PURPOSE_PREFIXES
        or not words
        or any(word not in _PURPOSE_WORDS and not word.isdigit() for word in words)
    ):
        raise ValueError("cross_mcp_purpose_invalid")


def _field_is_forbidden(value: str) -> bool:
    normalized = _normalized_field_name(value)
    tokens = set(normalized.split("_"))
    return bool(
        normalized in _FORBIDDEN_PAYLOAD_KEYS
        or tokens.intersection(_FORBIDDEN_FIELD_TOKENS)
    )


def _validate_nonempty_unique(values: tuple[str, ...], *, code: str) -> None:
    if not values or any(not value.strip() for value in values):
        raise ValueError(code)
    normalized = tuple(_normalized_field_name(value) for value in values)
    if len(set(normalized)) != len(normalized):
        raise ValueError(code)


def _validate_allowed_fields(values: tuple[str, ...]) -> None:
    _validate_nonempty_unique(values, code="handoff_allowed_fields_invalid")
    if any(_field_is_forbidden(value) for value in values):
        raise ValueError("handoff_instruction_field_forbidden")
    if any(not _FIELD_IDENTIFIER.fullmatch(value) for value in values):
        raise ValueError("handoff_allowed_fields_invalid")
    if any(value not in _APPROVAL_FIELD_SCHEMAS for value in values):
        raise ValueError("approval_payload_field_unknown")


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("cross_mcp_non_finite_number")
        return format(value.normalize(), "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cross_mcp_non_finite_number")
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("cross_mcp_datetime_timezone_required")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("cross_mcp_field_name_invalid")
            result[key] = _canonical_value(item)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_value(item) for item in value]
    raise ValueError("cross_mcp_data_type_forbidden")


def _authorization_digest(
    *,
    issuance_id: str,
    action_version: str,
    destination: str,
    side_effect: str,
    allowed_fields: tuple[str, ...],
    payload: Mapping[str, Any],
    purpose: str,
    issued_at: datetime,
    expires_at: datetime,
) -> str:
    authorization = {
        "contract_version": "approval-binding-v1",
        "issuance_id": issuance_id,
        "action_version": action_version,
        "destination": destination,
        "side_effect": side_effect,
        "allowed_fields": allowed_fields,
        "payload": payload,
        "purpose": purpose,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "content_is_untrusted": True,
        "atomic_consumption_required": True,
        "local_consumption_enforced": False,
    }
    canonical = json.dumps(
        _canonical_value(authorization),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class HandoffContract(StrictSafeModel):
    source: str = Field(min_length=1, max_length=128)
    destination: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=512)
    data_classification: Literal["public", "internal", "confidential"]
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...] = ()
    allowed_fields: tuple[str, ...]
    redaction_policy: tuple[str, ...]
    retention_limit: str = Field(min_length=1, max_length=128)
    fallbacks: tuple[str, ...]
    approval_points: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    content_is_untrusted: Literal[True] = True

    @field_validator("source", "destination", mode="before")
    @classmethod
    def validate_boundary_identifier(cls, value: Any) -> str:
        cleaned = _clean_required_string(value, code="handoff_boundary_invalid")
        _validate_identifier(
            cleaned,
            pattern=_BOUNDARY_IDENTIFIER,
            code="handoff_boundary_invalid",
        )
        return cleaned

    @field_validator("purpose", mode="before")
    @classmethod
    def validate_purpose_text(cls, value: Any) -> str:
        cleaned = _clean_required_string(value, code="cross_mcp_purpose_invalid")
        _validate_purpose(cleaned)
        return cleaned

    @field_validator(
        "required_capabilities",
        "optional_capabilities",
        "allowed_fields",
        "redaction_policy",
        "fallbacks",
        "approval_points",
        "evidence_requirements",
        "blocked_actions",
        mode="before",
    )
    @classmethod
    def validate_string_tuple_fields(cls, value: Any) -> tuple[str, ...]:
        return _clean_string_tuple(value, code="handoff_string_list_invalid")

    @field_validator("retention_limit", mode="before")
    @classmethod
    def validate_retention_limit(cls, value: Any) -> str:
        cleaned = _clean_required_string(value, code="handoff_retention_limit_invalid")
        if cleaned not in _RETENTION_LIMITS:
            raise ValueError("handoff_retention_limit_invalid")
        return cleaned

    @model_validator(mode="after")
    def validate_data_contract(self) -> HandoffContract:
        _validate_allowed_fields(self.allowed_fields)
        _validate_nonempty_unique(
            self.required_capabilities,
            code="handoff_required_capabilities_invalid",
        )
        for capability in (*self.required_capabilities, *self.optional_capabilities):
            _validate_capability(capability, code="handoff_capabilities_invalid")
        for values, code in (
            (self.optional_capabilities, "handoff_optional_capabilities_invalid"),
            (self.redaction_policy, "handoff_redaction_policy_invalid"),
            (self.fallbacks, "handoff_fallbacks_invalid"),
            (self.approval_points, "handoff_approval_points_invalid"),
            (self.evidence_requirements, "handoff_evidence_requirements_invalid"),
            (self.blocked_actions, "handoff_blocked_actions_invalid"),
        ):
            if values:
                _validate_nonempty_unique(values, code=code)
        _validate_controlled_values(
            self.redaction_policy,
            allowed=_REDACTION_POLICIES,
            code="handoff_redaction_policy_invalid",
        )
        _validate_controlled_values(
            self.fallbacks,
            allowed=_FALLBACKS,
            code="handoff_fallbacks_invalid",
        )
        _validate_controlled_values(
            self.approval_points,
            allowed=_APPROVAL_POINTS,
            code="handoff_approval_points_invalid",
        )
        _validate_controlled_values(
            self.evidence_requirements,
            allowed=_EVIDENCE_REQUIREMENTS,
            code="handoff_evidence_requirements_invalid",
        )
        _validate_controlled_values(
            self.blocked_actions,
            allowed=_BLOCKED_ACTIONS,
            code="handoff_blocked_actions_invalid",
        )
        return self


class WorkflowContract(StrictSafeModel):
    workflow_id: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=512)
    required_capabilities: tuple[str, ...]
    optional_capabilities: tuple[str, ...] = ()
    handoffs: tuple[HandoffContract, ...]
    fallbacks: tuple[str, ...]
    approval_points: tuple[str, ...]
    evidence_requirements: tuple[str, ...]
    blocked_actions: tuple[str, ...]
    content_is_untrusted: Literal[True] = True

    @field_validator("workflow_id", mode="before")
    @classmethod
    def validate_workflow_identifier(cls, value: Any) -> str:
        cleaned = _clean_required_string(value, code="workflow_id_invalid")
        _validate_identifier(
            cleaned,
            pattern=_WORKFLOW_IDENTIFIER,
            code="workflow_id_invalid",
        )
        return cleaned

    @field_validator("purpose", mode="before")
    @classmethod
    def validate_purpose_text(cls, value: Any) -> str:
        cleaned = _clean_required_string(value, code="cross_mcp_purpose_invalid")
        _validate_purpose(cleaned)
        return cleaned

    @field_validator(
        "required_capabilities",
        "optional_capabilities",
        "fallbacks",
        "approval_points",
        "evidence_requirements",
        "blocked_actions",
        mode="before",
    )
    @classmethod
    def validate_string_tuple_fields(cls, value: Any) -> tuple[str, ...]:
        return _clean_string_tuple(value, code="workflow_string_list_invalid")

    @model_validator(mode="after")
    def validate_workflow_contract(self) -> WorkflowContract:
        if not self.handoffs:
            raise ValueError("workflow_handoffs_required")
        _validate_nonempty_unique(
            self.required_capabilities,
            code="workflow_required_capabilities_invalid",
        )
        for capability in (*self.required_capabilities, *self.optional_capabilities):
            _validate_capability(capability, code="workflow_capabilities_invalid")
        for values, code in (
            (self.optional_capabilities, "workflow_optional_capabilities_invalid"),
            (self.fallbacks, "workflow_fallbacks_invalid"),
            (self.approval_points, "workflow_approval_points_invalid"),
            (self.evidence_requirements, "workflow_evidence_requirements_invalid"),
            (self.blocked_actions, "workflow_blocked_actions_invalid"),
        ):
            if values:
                _validate_nonempty_unique(values, code=code)
        _validate_controlled_values(
            self.fallbacks,
            allowed=_FALLBACKS,
            code="workflow_fallbacks_invalid",
        )
        _validate_controlled_values(
            self.approval_points,
            allowed=_APPROVAL_POINTS,
            code="workflow_approval_points_invalid",
        )
        _validate_controlled_values(
            self.evidence_requirements,
            allowed=_EVIDENCE_REQUIREMENTS,
            code="workflow_evidence_requirements_invalid",
        )
        _validate_controlled_values(
            self.blocked_actions,
            allowed=_BLOCKED_ACTIONS,
            code="workflow_blocked_actions_invalid",
        )
        return self


class ApprovalBinding(StrictSafeModel):
    issuance_id: str = Field(pattern=r"^apr_[0-9a-f]{32}$")
    action_version: str = Field(min_length=1, max_length=128)
    destination: str = Field(min_length=1, max_length=128)
    side_effect: str = Field(min_length=1, max_length=128)
    allowed_fields: tuple[str, ...]
    payload: dict[str, Any]
    authorization_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: str = Field(min_length=1, max_length=512)
    issued_at: datetime
    expires_at: datetime
    content_is_untrusted: Literal[True] = True
    atomic_consumption_required: Literal[True] = True
    local_consumption_enforced: Literal[False] = False

    @field_validator(
        "issuance_id",
        "action_version",
        "destination",
        "side_effect",
        "purpose",
        mode="before",
    )
    @classmethod
    def validate_required_text(cls, value: Any) -> str:
        return _clean_required_string(value, code="approval_metadata_invalid")

    @field_validator("allowed_fields", mode="before")
    @classmethod
    def validate_allowed_field_list(cls, value: Any) -> tuple[str, ...]:
        return _clean_string_tuple(value, code="handoff_allowed_fields_invalid")

    @classmethod
    def issue(
        cls,
        *,
        action_version: str,
        destination: str,
        side_effect: str,
        allowed_fields: tuple[str, ...],
        payload: Mapping[str, Any],
        ttl_seconds: int,
        purpose: str | None = None,
        now: datetime | None = None,
    ) -> Self:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("approval_ttl_invalid")
        issued_at = datetime.now(UTC) if now is None else now
        if (
            not isinstance(issued_at, datetime)
            or issued_at.tzinfo is None
            or issued_at.utcoffset() is None
        ):
            raise ValueError("approval_time_timezone_required")
        normalized_issued_at = issued_at.astimezone(UTC)
        expires_at = normalized_issued_at + timedelta(seconds=ttl_seconds)
        field_tuple = _clean_string_tuple(
            allowed_fields,
            code="handoff_allowed_fields_invalid",
        )
        if not isinstance(payload, Mapping):
            raise ValueError("approval_payload_invalid")
        payload_copy = dict(payload)
        approval_purpose = side_effect if purpose is None else purpose
        approval_issuance_id = _new_issuance_id()
        _validate_approval_payload(field_tuple, payload_copy)
        digest = _authorization_digest(
            issuance_id=approval_issuance_id,
            action_version=action_version,
            destination=destination,
            side_effect=side_effect,
            allowed_fields=field_tuple,
            payload=payload_copy,
            purpose=approval_purpose,
            issued_at=normalized_issued_at,
            expires_at=expires_at,
        )
        return cls(
            issuance_id=approval_issuance_id,
            action_version=action_version,
            destination=destination,
            side_effect=side_effect,
            allowed_fields=field_tuple,
            payload=payload_copy,
            authorization_digest=digest,
            purpose=approval_purpose,
            issued_at=normalized_issued_at,
            expires_at=expires_at,
        )

    @model_validator(mode="after")
    def validate_binding(self) -> ApprovalBinding:
        _validate_identifier(
            self.issuance_id,
            pattern=_ISSUANCE_IDENTIFIER,
            code="approval_issuance_id_invalid",
        )
        _validate_identifier(
            self.action_version,
            pattern=_ACTION_VERSION_IDENTIFIER,
            code="approval_action_version_invalid",
        )
        _validate_identifier(
            self.destination,
            pattern=_BOUNDARY_IDENTIFIER,
            code="approval_destination_invalid",
        )
        _validate_side_effect(self.side_effect)
        _validate_purpose(self.purpose)
        _validate_approval_payload(self.allowed_fields, self.payload)
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("approval_time_timezone_required")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("approval_time_timezone_required")
        if self.expires_at <= self.issued_at:
            raise ValueError("approval_expiry_invalid")
        expected_digest = _authorization_digest(
            issuance_id=self.issuance_id,
            action_version=self.action_version,
            destination=self.destination,
            side_effect=self.side_effect,
            allowed_fields=self.allowed_fields,
            payload=self.payload,
            purpose=self.purpose,
            issued_at=self.issued_at,
            expires_at=self.expires_at,
        )
        if not hmac.compare_digest(self.authorization_digest, expected_digest):
            raise ValueError("approval_authorization_digest_mismatch")
        return self

    def accepts(
        self,
        *,
        action_version: str,
        destination: str,
        side_effect: str,
        allowed_fields: tuple[str, ...],
        purpose: str,
        payload: Mapping[str, Any],
        at: datetime,
        trusted_issuance_id: str,
        trusted_authorization_digest: str,
    ) -> bool:
        if not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None:
            return False
        if at < self.issued_at or at >= self.expires_at:
            return False
        if not isinstance(trusted_issuance_id, str) or not isinstance(
            trusted_authorization_digest, str
        ):
            return False
        if not _ISSUANCE_IDENTIFIER.fullmatch(
            trusted_issuance_id
        ) or not _SHA256_HEX.fullmatch(trusted_authorization_digest):
            return False
        if not hmac.compare_digest(trusted_issuance_id, self.issuance_id):
            return False
        try:
            candidate_fields = _clean_string_tuple(
                allowed_fields,
                code="handoff_allowed_fields_invalid",
            )
            if action_version != self.action_version:
                return False
            if destination != self.destination:
                return False
            if side_effect != self.side_effect:
                return False
            if candidate_fields != self.allowed_fields:
                return False
            if purpose != self.purpose:
                return False
            candidate = dict(payload)
            _validate_approval_payload(candidate_fields, candidate)
            candidate_digest = _authorization_digest(
                issuance_id=trusted_issuance_id,
                action_version=action_version,
                destination=destination,
                side_effect=side_effect,
                allowed_fields=candidate_fields,
                payload=candidate,
                purpose=purpose,
                issued_at=self.issued_at,
                expires_at=self.expires_at,
            )
            return hmac.compare_digest(
                trusted_authorization_digest,
                self.authorization_digest,
            ) and hmac.compare_digest(trusted_authorization_digest, candidate_digest)
        except (TypeError, ValueError):
            return False


def _validate_approval_payload(
    allowed_fields: tuple[str, ...],
    payload: Mapping[str, Any],
) -> None:
    _validate_allowed_fields(allowed_fields)
    if set(payload) != set(allowed_fields):
        raise ValueError("approval_payload_schema_mismatch")
    for field_name, value in payload.items():
        _validate_approval_value(field_name, value)


def _validate_approval_value(field_name: str, value: Any) -> None:
    schema = _APPROVAL_FIELD_SCHEMAS.get(field_name)
    if schema is None:
        raise ValueError("approval_payload_field_unknown")
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        raise ValueError("approval_formula_forbidden")

    code = "approval_payload_value_invalid"
    if schema == "reference":
        validate_accounting_reference(value, code=code)
        return
    if schema == "counterparty_key":
        validate_counterparty_key(value, code=code)
        return
    if schema == "evidence_ref":
        validate_evidence_locator(value, code=code)
        return
    if schema == "transaction_id":
        validate_record_identifier(value, code=code)
        return
    if schema == "source":
        validate_accounting_source(value, code=code)
        return
    if schema == "document_state":
        validate_document_state(value, code=code)
        return
    if schema == "status":
        validate_approval_status(value, code=code)
        return
    if schema == "currency":
        validate_currency(value, code=code)
        return
    if schema == "amount":
        _validate_approval_amount(value)
        return
    if schema == "date":
        _validate_approval_date(value)
        return
    raise ValueError("approval_payload_field_unknown")


def _validate_approval_amount(value: Any) -> None:
    if isinstance(value, bool):
        raise ValueError("approval_payload_value_invalid")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("cross_mcp_non_finite_number")
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("cross_mcp_non_finite_number")
        return
    if isinstance(value, str) and _AMOUNT_TEXT.fullmatch(value):
        return
    raise ValueError("approval_payload_value_invalid")


def _validate_approval_date(value: Any) -> None:
    if isinstance(value, datetime):
        raise ValueError("approval_payload_value_invalid")
    if isinstance(value, date):
        return
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("approval_payload_value_invalid") from exc
        return
    raise ValueError("approval_payload_value_invalid")


def _new_issuance_id() -> str:
    return f"apr_{secrets.token_hex(16)}"
