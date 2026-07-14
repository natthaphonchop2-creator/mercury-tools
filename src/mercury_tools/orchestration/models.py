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
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from mercury_tools.qualification.models import StrictSafeModel

_MAX_DATA_DEPTH = 8
_MAX_DATA_ITEMS = 512
_MAX_DATA_STRING_LENGTH = 2_048
_MAX_ACCOUNTING_TEXT_LENGTH = 512
_ACCOUNTING_TEXT_PUNCTUATION = frozenset(" ./:-#()+,%&'_")
_DISALLOWED_TEXT_CATEGORIES = frozenset({"Cc", "Cf", "Cn", "Co", "Cs", "Zl", "Zp"})
_HOST_PORT = re.compile(
    r"^(?:\[[0-9a-f:.]+\]|[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?):\d{1,5}(?:[/?#].*)?$",
    re.IGNORECASE,
)
_DNS_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$", re.IGNORECASE)
_CREDENTIAL_LIKE = re.compile(
    r"(?ix)(?:"
    r"\b(?:api[ _-]?key|access[ _-]?token|authorization|client[ _-]?secret|"
    r"password|passwd|private[ _-]?key|refresh[ _-]?token)\b\s*(?:[:=]|\b)"
    r"|\bbearer\s+[a-z0-9._~+/=-]{6,}\b"
    r"|\b(?:sk|pk|rk)_[a-z0-9_-]{8,}\b"
    r"|\bAKIA[0-9A-Z]{16}\b"
    r")"
)
_DISALLOWED_URI_SCHEMES = frozenset(
    {
        "data",
        "file",
        "ftp",
        "ftps",
        "gs",
        "http",
        "https",
        "javascript",
        "mailto",
        "s3",
        "sftp",
        "ssh",
        "tel",
        "ws",
        "wss",
    }
)
_INSTRUCTION_LIKE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?"
    r"|\bsystem\s+prompt\b|\byou\s+must\b|<\s*(?:system|assistant|tool)[^>]*>"
    r"|\b(?:call|delete|execute|invoke|open|post|run|send|share|upload|write)\s+"
    r"(?:(?:this|the|a|an)\s+)?(?:command|email|file|message|report|request|tool)\b)"
)
_EXECUTABLE_LIKE = re.compile(
    r"(?ix)(?:"
    r"```|\#!\s*/|<\s*script\b|\b(?:eval|exec)\s*\("
    r"|\b(?:os\.system|subprocess\.)"
    r"|(?:^|\s)(?:bash|cmd(?:\.exe)?|curl|env|node|perl|powershell|pwsh|python\d*|"
    r"rm|ruby|sh|sudo|wget|zsh)\s+(?:-[^\s]+\s+)?"
    r"|(?:^|\s)(?:~[/\\]|/(?!/)[^\s]*|[a-z]:[/\\]|\\\\[^\s\\]+\\)"
    r"|`|\$\(|\|\||&&|[;|<>]|\r|\n"
    r")"
)
_EXTERNAL_MCP_TOOL = re.compile(
    r"(?i)(?:\bmcp(?:__|[-.:/])[a-z0-9_-]+(?:(?:__|[-.:/])[a-z0-9_-]+)*"
    r"|\b(?:call|invoke|execute|run)\s+[a-z][a-z0-9_-]*(?:__|\.)"
    r"[a-z][a-z0-9_-]*)"
)
_TOOL_NAMES = re.compile(
    r"(?i)\b(?:connector_status|credential_status|retrieve_context_pack|search_erp_actions"
    r"|get_erp_action_schema|run_erp_read|preview_erp_write|confirm_erp_write"
    r"|execute_erp_write|get_erp_request_status|run_accounting_skill"
    r"|run_mercury_flow|list_workspace_flows|save_workspace_flow"
    r"|run_workspace_flow|import_erp_spec|list_connector_drivers|search_knowledge"
    r"|get_document)\b"
)
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


def _is_dns_host(host: str) -> bool:
    normalized = host.rstrip(".")
    if "." not in normalized:
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


def _is_endpoint_token(value: str) -> bool:
    if value.startswith("//") or _is_file_path(value):
        return True

    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    if parsed.scheme:
        scheme = parsed.scheme.casefold()
        if scheme in _DISALLOWED_URI_SCHEMES or value[len(parsed.scheme) + 1 :].startswith("//"):
            return True

    authority = re.split(r"[/?#]", value, maxsplit=1)[0]
    if _is_ip_host(authority):
        return True
    try:
        host = urlsplit(f"//{value}").hostname
    except ValueError:
        return value.startswith("[")
    if host is not None and (
        host.casefold() == "localhost" or _is_ip_host(host) or _is_dns_host(host)
    ):
        return True
    return bool(_HOST_PORT.fullmatch(value) and any(marker in value for marker in "/?#"))


def _is_endpoint_like(value: str) -> bool:
    token_boundary = "\"'(){}<>,;!?`"
    return any(
        _is_endpoint_token(token.strip(token_boundary))
        for token in re.findall(r"\S+", value)
        if token.strip(token_boundary)
    )


def _is_bounded_accounting_text(value: str, *, max_length: int) -> bool:
    return bool(
        value
        and len(value) <= max_length
        and value == value.strip()
        and ".." not in value
        and "//" not in value
        and all(
            character == " "
            or character in _ACCOUNTING_TEXT_PUNCTUATION
            or unicodedata.category(character)[0] in {"L", "M", "N"}
            for character in value
        )
    )


def validate_accounting_text(
    value: Any,
    *,
    code: str,
    max_length: int = _MAX_ACCOUNTING_TEXT_LENGTH,
) -> str:
    """Validate bounded accounting labels without permitting executable endpoint text."""

    if not isinstance(value, str):
        raise ValueError(code)
    validate_cross_mcp_data(value)
    if not _is_bounded_accounting_text(value, max_length=max_length):
        raise ValueError(code)
    return value


def _validate_capability(value: str, *, code: str) -> None:
    if _EXTERNAL_MCP_TOOL.search(value):
        raise ValueError("cross_mcp_tool_name_forbidden")
    if (
        not _CAPABILITY_IDENTIFIER.fullmatch(value)
        or value.split(".", maxsplit=1)[0].startswith("mcp")
        or value.rsplit(".", maxsplit=1)[-1] not in _CAPABILITY_OPERATIONS
    ):
        raise ValueError(code)


def _validate_side_effect(value: str) -> None:
    if _EXTERNAL_MCP_TOOL.search(value):
        raise ValueError("cross_mcp_tool_name_forbidden")
    if (
        not _SIDE_EFFECT_IDENTIFIER.fullmatch(value)
        or value.split(".", maxsplit=1)[0].startswith("mcp")
        or value.rsplit(".", maxsplit=1)[-1] not in _SIDE_EFFECT_OPERATIONS
    ):
        raise ValueError("approval_side_effect_invalid")


def _validate_purpose(value: str) -> None:
    if _EXTERNAL_MCP_TOOL.search(value) or _TOOL_NAMES.search(value):
        raise ValueError("cross_mcp_tool_name_forbidden")
    if _SIDE_EFFECT_IDENTIFIER.fullmatch(value):
        _validate_side_effect(value)
        return
    validate_cross_mcp_data(value)
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


def validate_cross_mcp_data(value: Any) -> None:
    """Reject non-data content before it can cross an MCP boundary."""

    remaining = [_MAX_DATA_ITEMS]

    def visit(item: Any, *, depth: int) -> None:
        if depth > _MAX_DATA_DEPTH:
            raise ValueError("cross_mcp_data_too_deep")
        if isinstance(item, str):
            if len(item) > _MAX_DATA_STRING_LENGTH:
                raise ValueError("cross_mcp_data_too_large")
            if _CREDENTIAL_LIKE.search(item):
                raise ValueError("cross_mcp_credential_forbidden")
            if _INSTRUCTION_LIKE.search(item):
                raise ValueError("cross_mcp_instruction_forbidden")
            if _EXECUTABLE_LIKE.search(item):
                raise ValueError("cross_mcp_executable_forbidden")
            if _has_disallowed_text_character(item):
                raise ValueError("cross_mcp_control_character_forbidden")
            if _TOOL_NAMES.search(item) or _EXTERNAL_MCP_TOOL.search(item):
                raise ValueError("cross_mcp_tool_name_forbidden")
            if _is_endpoint_like(item):
                raise ValueError("cross_mcp_url_forbidden")
            return
        if item is None or isinstance(item, (bool, int, Decimal, date, datetime)):
            return
        if isinstance(item, float):
            if not math.isfinite(item):
                raise ValueError("cross_mcp_non_finite_number")
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise ValueError("cross_mcp_field_name_invalid")
                if _field_is_forbidden(key):
                    raise ValueError("cross_mcp_payload_field_forbidden")
                consume()
                visit(key, depth=depth + 1)
                visit(nested, depth=depth + 1)
            return
        if isinstance(item, Sequence) and not isinstance(item, (bytes, bytearray)):
            for nested in item:
                consume()
                visit(nested, depth=depth + 1)
            return
        raise ValueError("cross_mcp_data_type_forbidden")

    def consume() -> None:
        remaining[0] -= 1
        if remaining[0] < 0:
            raise ValueError("cross_mcp_data_too_large")

    visit(value, depth=0)


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
        for fallback in self.fallbacks:
            validate_cross_mcp_data(fallback)
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
        for fallback in self.fallbacks:
            validate_cross_mcp_data(fallback)
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
        validate_cross_mcp_data(payload_copy)
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
        validate_cross_mcp_data(self.payload)
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
            validate_cross_mcp_data(candidate)
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
    for value in payload.values():
        if value is None or isinstance(value, (bool, int, Decimal, date, datetime)):
            continue
        if isinstance(value, float):
            if not math.isfinite(value):
                raise ValueError("cross_mcp_non_finite_number")
            continue
        if isinstance(value, str):
            validate_accounting_text(value, code="approval_payload_value_invalid")
            continue
        raise ValueError("approval_payload_value_invalid")


def _new_issuance_id() -> str:
    return f"apr_{secrets.token_hex(16)}"
