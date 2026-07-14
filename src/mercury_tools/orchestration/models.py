"""Secret-safe data contracts for host-orchestrated Cross-MCP work."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from mercury_tools.qualification.models import StrictSafeModel

_MAX_DATA_DEPTH = 8
_MAX_DATA_ITEMS = 512
_MAX_DATA_STRING_LENGTH = 2_048
_URL_LIKE = re.compile(
    r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\b(?:file|data|javascript|mailto):|\bwww\.|(?<!:)//)"
)
_INSTRUCTION_LIKE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above)\s+instructions?"
    r"|\bsystem\s+prompt\b|\byou\s+must\b|<\s*(?:system|assistant|tool)[^>]*>)"
)
_EXECUTABLE_LIKE = re.compile(
    r"(?i)(?:```|#!\s*/|<\s*script\b|\b(?:eval|exec)\s*\("
    r"|\b(?:os\.system|subprocess\.)|\b(?:powershell|cmd\.exe)\b)"
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


def _normalized_field_name(value: str) -> str:
    snake_case = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value.strip())
    return re.sub(r"[^a-z0-9]+", "_", snake_case.casefold()).strip("_")


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


def validate_cross_mcp_data(value: Any, *, reject_tool_names: bool = True) -> None:
    """Reject non-data content before it can cross an MCP boundary."""

    remaining = [_MAX_DATA_ITEMS]

    def visit(item: Any, *, depth: int) -> None:
        if depth > _MAX_DATA_DEPTH:
            raise ValueError("cross_mcp_data_too_deep")
        if isinstance(item, str):
            if len(item) > _MAX_DATA_STRING_LENGTH:
                raise ValueError("cross_mcp_data_too_large")
            if _URL_LIKE.search(item):
                raise ValueError("cross_mcp_url_forbidden")
            if _INSTRUCTION_LIKE.search(item):
                raise ValueError("cross_mcp_instruction_forbidden")
            if _EXECUTABLE_LIKE.search(item):
                raise ValueError("cross_mcp_executable_forbidden")
            if reject_tool_names and _TOOL_NAMES.search(item):
                raise ValueError("cross_mcp_tool_name_forbidden")
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


def _payload_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _canonical_value(payload),
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

    @model_validator(mode="after")
    def validate_data_contract(self) -> HandoffContract:
        _validate_allowed_fields(self.allowed_fields)
        _validate_nonempty_unique(
            self.required_capabilities,
            code="handoff_required_capabilities_invalid",
        )
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
        validate_cross_mcp_data(
            (
                self.source,
                self.purpose,
                self.data_classification,
                self.required_capabilities,
                self.optional_capabilities,
                self.allowed_fields,
                self.redaction_policy,
                self.retention_limit,
                self.fallbacks,
                self.approval_points,
                self.evidence_requirements,
                self.blocked_actions,
            )
        )
        validate_cross_mcp_data(self.destination)
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

    @model_validator(mode="after")
    def validate_workflow_contract(self) -> WorkflowContract:
        if not self.handoffs:
            raise ValueError("workflow_handoffs_required")
        _validate_nonempty_unique(
            self.required_capabilities,
            code="workflow_required_capabilities_invalid",
        )
        for values, code in (
            (self.optional_capabilities, "workflow_optional_capabilities_invalid"),
            (self.fallbacks, "workflow_fallbacks_invalid"),
            (self.approval_points, "workflow_approval_points_invalid"),
            (self.evidence_requirements, "workflow_evidence_requirements_invalid"),
            (self.blocked_actions, "workflow_blocked_actions_invalid"),
        ):
            if values:
                _validate_nonempty_unique(values, code=code)
        validate_cross_mcp_data(
            (
                self.workflow_id,
                self.purpose,
                self.required_capabilities,
                self.optional_capabilities,
                self.fallbacks,
                self.approval_points,
                self.evidence_requirements,
                self.blocked_actions,
            )
        )
        return self


class ApprovalBinding(StrictSafeModel):
    action_version: str = Field(min_length=1, max_length=128)
    destination: str = Field(min_length=1, max_length=128)
    side_effect: str = Field(min_length=1, max_length=128)
    allowed_fields: tuple[str, ...]
    payload: dict[str, Any]
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: str = Field(min_length=1, max_length=512)
    issued_at: datetime
    expires_at: datetime
    single_use: Literal[True] = True

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
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("approval_ttl_invalid")
        issued_at = now or datetime.now(UTC)
        if issued_at.tzinfo is None or issued_at.utcoffset() is None:
            raise ValueError("approval_time_timezone_required")
        field_tuple = tuple(allowed_fields)
        payload_copy = dict(payload)
        _validate_approval_payload(field_tuple, payload_copy)
        validate_cross_mcp_data(payload_copy)
        return cls(
            action_version=action_version,
            destination=destination,
            side_effect=side_effect,
            allowed_fields=field_tuple,
            payload=payload_copy,
            payload_digest=_payload_digest(payload_copy),
            purpose=purpose or side_effect,
            issued_at=issued_at.astimezone(UTC),
            expires_at=issued_at.astimezone(UTC) + timedelta(seconds=ttl_seconds),
        )

    @model_validator(mode="after")
    def validate_binding(self) -> ApprovalBinding:
        _validate_approval_payload(self.allowed_fields, self.payload)
        validate_cross_mcp_data(self.payload)
        validate_cross_mcp_data(
            (
                self.action_version,
                self.destination,
                self.side_effect,
                self.purpose,
            )
        )
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("approval_time_timezone_required")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("approval_time_timezone_required")
        if self.expires_at <= self.issued_at:
            raise ValueError("approval_expiry_invalid")
        if self.payload_digest != _payload_digest(self.payload):
            raise ValueError("approval_payload_digest_mismatch")
        return self

    def accepts(
        self,
        *,
        destination: str,
        payload: Mapping[str, Any],
        action_version: str | None = None,
        side_effect: str | None = None,
        allowed_fields: tuple[str, ...] | None = None,
        purpose: str | None = None,
        at: datetime | None = None,
    ) -> bool:
        checked_at = at or datetime.now(UTC)
        if checked_at.tzinfo is None or checked_at.utcoffset() is None:
            return False
        if checked_at < self.issued_at or checked_at >= self.expires_at:
            return False
        if destination != self.destination:
            return False
        if action_version is not None and action_version != self.action_version:
            return False
        if side_effect is not None and side_effect != self.side_effect:
            return False
        if allowed_fields is not None and tuple(allowed_fields) != self.allowed_fields:
            return False
        if purpose is not None and purpose != self.purpose:
            return False
        try:
            candidate = dict(payload)
            _validate_approval_payload(self.allowed_fields, candidate)
            validate_cross_mcp_data(candidate)
            return (
                self.payload_digest == _payload_digest(self.payload)
                and self.payload_digest == _payload_digest(candidate)
            )
        except (TypeError, ValueError):
            return False


def _validate_approval_payload(
    allowed_fields: tuple[str, ...],
    payload: Mapping[str, Any],
) -> None:
    _validate_allowed_fields(allowed_fields)
    if set(payload) != set(allowed_fields):
        raise ValueError("approval_payload_schema_mismatch")
