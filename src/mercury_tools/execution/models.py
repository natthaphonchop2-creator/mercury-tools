"""Immutable local previews for ERP mutations.

These models deliberately retain bound request inputs only for the local SQLite
store. Callers that need model-visible data must use ``public_dict``.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, unquote

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from mercury_tools.canonical import (
    canonical_payload_hash,
)
from mercury_tools.catalog.identity import deep_freeze
from mercury_tools.catalog.models import CatalogAction, RiskTier, revalidate_catalog_action
from mercury_tools.drivers.models import AuthContext
from mercury_tools.execution.policy import (
    ApprovalLevel,
    MutationClass,
    RiskDecision,
    effective_risk,
)
from mercury_tools.safety.redaction import redact_json

if TYPE_CHECKING:
    from mercury_tools.local.repository import RepositoryContext

PREVIEW_TTL = timedelta(minutes=15)
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PAYLOAD_HASH = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_REVISION = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^req_[0-9a-z_]{8,128}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_PATH_PLACEHOLDER = re.compile(r"^\{([A-Za-z][A-Za-z0-9_]*)\}$")
_FORBIDDEN_HEADER_NAMES = frozenset(
    {"authorization", "cookie", "host", "proxy-authorization", "set-cookie"}
)
# Public previews expose only these documented structural names. Values are
# always replaced by shape markers before leaving the local state boundary.
_PREVIEW_SUMMARY_KEYS = frozenset(
    {
        "amount",
        "body",
        "content_type",
        "count",
        "currency",
        "customer",
        "document",
        "document_type",
        "fields",
        "files",
        "has_body",
        "headers",
        "idempotency",
        "items",
        "line_items",
        "name",
        "operation",
        "path",
        "query",
        "record",
        "records",
        "request",
        "resource",
        "resources",
        "total",
    }
)
_RESPONSE_SUMMARY_KEYS = frozenset(
    {
        "count",
        "customer",
        "data",
        "document_number",
        "error",
        "error_code",
        "http_status",
        "invoice_number",
        "items",
        "latency_ms",
        "outcome",
        "provider_code",
        "provider_status",
        "result",
        "status",
        "status_class",
        "success",
    }
)
_SENSITIVE_PUBLIC_KEY_PARTS = frozenset(
    {
        "address",
        "authorization",
        "cookie",
        "email",
        "firstname",
        "fullname",
        "lastname",
        "mobile",
        "name",
        "nationalid",
        "passport",
        "phone",
        "taxid",
    }
)


class RequestState(StrEnum):
    PREVIEWED = "previewed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


def _binding_payload(
    *,
    repository_id: str,
    connector_id: str,
    environment: str,
    action_id: str,
    version_id: str,
    method: str,
    final_path: str,
    request_inputs: Any,
    risk_tier: RiskTier | int,
    approval_level: ApprovalLevel | str,
    mutation_class: MutationClass | str,
    credential_revision: str,
    preflight_actions: Sequence[PreflightActionBinding],
) -> dict[str, Any]:
    return {
        "repository_id": repository_id,
        "connector_id": connector_id,
        "environment": environment,
        "action_id": action_id,
        "version_id": version_id,
        "method": method,
        "final_path": final_path,
        "request_inputs": request_inputs,
        "risk_tier": int(risk_tier),
        "approval_level": str(approval_level),
        "mutation_class": str(mutation_class),
        "credential_revision": credential_revision,
        "preflight_actions": [item.binding_payload for item in preflight_actions],
    }


class PreflightActionBinding(BaseModel):
    """Internal immutable identity for one approval-bound preflight action."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    action_id: str
    version_id: str
    connector_id: str
    method: Literal["GET"]
    path_template: str

    @model_validator(mode="after")
    def validate_binding(self) -> PreflightActionBinding:
        if (
            not self.action_id
            or not self.version_id
            or _IDENTIFIER.fullmatch(self.connector_id) is None
            or not _valid_final_path(self.path_template)
        ):
            raise ValueError("invalid_preflight_binding")
        return self

    @property
    def binding_payload(self) -> dict[str, str]:
        return {
            "action_id": self.action_id,
            "version_id": self.version_id,
            "connector_id": self.connector_id,
            "method": self.method,
            "path_template": self.path_template,
        }

    @classmethod
    def from_action(cls, action: CatalogAction) -> PreflightActionBinding:
        try:
            action = revalidate_catalog_action(action)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid_preflight_binding") from None
        if action.method.value != "GET":
            raise ValueError("invalid_preflight_binding")
        return cls(
            action_id=action.action_id,
            version_id=action.version_id,
            connector_id=action.connector_id,
            method="GET",
            path_template=action.path_template,
        )


class PreparedRequest(BaseModel):
    """A short-lived, confirmation-bound ERP mutation preview."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    request_id: str
    repository_id: str
    connector_id: str
    environment: str
    action_id: str
    version_id: str
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    path_template: str
    final_path: str
    sanitized_summary: dict[str, Any]
    request_inputs: dict[str, Any]
    payload_hash: str
    risk_tier: RiskTier
    approval_level: ApprovalLevel
    mutation_class: MutationClass
    credential_revision: str = Field(repr=False)
    preflight_actions: tuple[PreflightActionBinding, ...] = ()
    approval_count: Literal[0, 1] = 0
    state: RequestState
    failure_reason: str | None = None
    response_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime

    @field_validator("approval_count", mode="before")
    @classmethod
    def validate_approval_count(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
            raise ValueError("invalid_approval_count")
        return value

    @model_validator(mode="after")
    def validate_preview(self) -> PreparedRequest:
        if _REQUEST_ID.fullmatch(self.request_id) is None:
            raise ValueError("invalid_request_id")
        for value in (self.repository_id, self.connector_id, self.environment):
            if _IDENTIFIER.fullmatch(value) is None:
                raise ValueError("invalid_request_binding")
        if not self.action_id or not self.version_id:
            raise ValueError("invalid_request_binding")
        if self.method not in _MUTATING_METHODS or not _valid_final_path(self.final_path):
            raise ValueError("invalid_request_binding")
        try:
            rendered_path = render_action_path(
                self.path_template,
                self.request_inputs.get("path", {}),
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid_action_path") from None
        if not secrets.compare_digest(self.final_path, rendered_path):
            raise ValueError("request_path_mismatch")
        if _PAYLOAD_HASH.fullmatch(self.payload_hash) is None:
            raise ValueError("invalid_payload_hash")
        if _CREDENTIAL_REVISION.fullmatch(self.credential_revision) is None:
            raise ValueError("invalid_credential_revision")
        preflight_ids = tuple(item.action_id for item in self.preflight_actions)
        if len(preflight_ids) != len(set(preflight_ids)):
            raise ValueError("invalid_preflight_binding")
        if not _valid_approval_binding(self.approval_level, self.mutation_class):
            raise ValueError("invalid_approval_binding")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("preview_created_at_naive")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("preview_expiry_naive")
        created_at = self.created_at.astimezone(UTC)
        expires_at = self.expires_at.astimezone(UTC)
        if expires_at - created_at != PREVIEW_TTL:
            raise ValueError("preview_ttl_invalid")
        if self.failure_reason is not None and not _is_reason(self.failure_reason):
            raise ValueError("invalid_failure_reason")
        _validate_static_headers(self.request_inputs)
        _validate_state_fields(self)
        expected_hash = canonical_payload_hash(self.binding_payload)
        if not secrets.compare_digest(self.payload_hash, expected_hash):
            raise ValueError("payload_hash_mismatch")
        if (
            (self.method == "DELETE" and self.mutation_class is not MutationClass.SENSITIVE)
            or (self.method == "POST" and self.mutation_class is MutationClass.UPDATE)
            or (self.method in {"PUT", "PATCH"} and self.mutation_class is MutationClass.CREATE)
        ):
            raise ValueError("invalid_mutation_class")

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(
            self,
            "sanitized_summary",
            deep_freeze(
                _sanitize_public_data(
                    self.sanitized_summary,
                    _PREVIEW_SUMMARY_KEYS,
                )
            ),
        )
        object.__setattr__(self, "request_inputs", deep_freeze(self.request_inputs))
        object.__setattr__(
            self,
            "response_summary",
            deep_freeze(
                _sanitize_public_data(
                    self.response_summary,
                    _RESPONSE_SUMMARY_KEYS,
                )
            ),
        )
        return self

    @property
    def binding_payload(self) -> dict[str, Any]:
        """Return the complete canonical request binding used by ``payload_hash``."""

        return _binding_payload(
            repository_id=self.repository_id,
            connector_id=self.connector_id,
            environment=self.environment,
            action_id=self.action_id,
            version_id=self.version_id,
            method=self.method,
            final_path=self.final_path,
            request_inputs=self.request_inputs,
            risk_tier=self.risk_tier,
            approval_level=self.approval_level,
            mutation_class=self.mutation_class,
            credential_revision=self.credential_revision,
            preflight_actions=self.preflight_actions,
        )

    @classmethod
    def from_template(
        cls,
        repository: RepositoryContext,
        action: CatalogAction,
        environment: str,
        request: Any,
        risk: RiskDecision,
        payload_hash: str,
        credential_revision: str,
        preflight_actions: Sequence[PreflightActionBinding],
    ) -> PreparedRequest:
        from mercury_tools.local.repository import RepositoryContext

        if not isinstance(repository, RepositoryContext):
            raise ValueError("invalid_repository_context")
        try:
            if not isinstance(action, CatalogAction):
                raise TypeError
            action = revalidate_catalog_action(action)
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid_action_binding") from None
        if environment not in action.environments:
            raise ValueError("action_environment_not_supported")
        if action.method.value not in _MUTATING_METHODS:
            raise ValueError("read_action_cannot_be_previewed")
        if not isinstance(risk, RiskDecision):
            raise ValueError("invalid_risk_decision")
        if not _valid_approval_binding(risk.approval_level, risk.mutation_class):
            raise ValueError("invalid_risk_decision")
        risk_floor = effective_risk(action)
        if not _risk_covers_floor(risk, risk_floor):
            raise ValueError("risk_below_runtime_floor")

        method = _request_field(request, "method", action.method.value)
        if method != action.method.value:
            raise ValueError("request_method_mismatch")
        final_path = _request_field(request, "final_path", None)
        if final_path is None:
            final_path = _request_field(request, "path", None)
        summary = _request_field(request, "sanitized_summary", {})
        inputs = _request_field(request, "request_inputs", {})
        try:
            rendered_path = render_action_path(
                action.path_template,
                inputs.get("path", {}) if isinstance(inputs, Mapping) else None,
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("invalid_action_path") from None
        if not isinstance(final_path, str) or not secrets.compare_digest(
            final_path,
            rendered_path,
        ):
            raise ValueError("request_path_mismatch")
        binding_payload = _binding_payload(
            repository_id=repository.repository_id,
            connector_id=action.connector_id,
            environment=environment,
            action_id=action.action_id,
            version_id=action.version_id,
            method=method,
            final_path=final_path,
            request_inputs=inputs,
            risk_tier=risk.tier,
            approval_level=risk.approval_level,
            mutation_class=risk.mutation_class,
            credential_revision=credential_revision,
            preflight_actions=preflight_actions,
        )
        expected_hash = canonical_payload_hash(binding_payload)
        if not isinstance(payload_hash, str) or not secrets.compare_digest(
            payload_hash,
            expected_hash,
        ):
            raise ValueError("payload_hash_mismatch")
        now = datetime.now(UTC)
        return cls(
            request_id="req_" + secrets.token_hex(16),
            repository_id=repository.repository_id,
            connector_id=action.connector_id,
            environment=environment,
            action_id=action.action_id,
            version_id=action.version_id,
            method=method,
            path_template=action.path_template,
            final_path=final_path,
            sanitized_summary=summary,
            request_inputs=inputs,
            payload_hash=payload_hash,
            risk_tier=risk.tier,
            approval_level=risk.approval_level,
            mutation_class=risk.mutation_class,
            credential_revision=credential_revision,
            preflight_actions=tuple(preflight_actions),
            state=RequestState.PREVIEWED,
            created_at=now,
            expires_at=now + PREVIEW_TTL,
        )

    def to_httpx_request(self, auth: AuthContext) -> httpx.Request:
        """Render the locally bound request with short-lived driver auth attached."""

        if not isinstance(auth, AuthContext):
            raise ValueError("invalid_auth_context")
        inputs = self.request_inputs
        query = _string_mapping(inputs.get("query", {}), "invalid_request_inputs")
        headers = _string_mapping(inputs.get("headers", {}), "invalid_request_inputs")
        headers.update(auth.headers)
        query.update(auth.query)
        body = inputs.get("body", inputs.get("json"))
        if body is None:
            return httpx.Request(self.method, self.final_path, params=query, headers=headers)
        return httpx.Request(
            self.method,
            self.final_path,
            params=query,
            headers=headers,
            json=_thaw(body),
        )

    def public_dict(self) -> dict[str, Any]:
        """Return the non-payload state that may be shown to a model or user."""

        return {
            "request_id": self.request_id,
            "repository_id": self.repository_id,
            "connector_id": self.connector_id,
            "environment": self.environment,
            "action_id": self.action_id,
            "version_id": self.version_id,
            "method": self.method,
            "target": self.path_template,
            "sanitized_summary": _public_summary(
                self.sanitized_summary,
                _PREVIEW_SUMMARY_KEYS,
            ),
            "payload_hash": self.payload_hash,
            "risk_tier": int(self.risk_tier),
            "approval_level": self.approval_level.value,
            "mutation_class": self.mutation_class.value,
            "approval_count": self.approval_count,
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "response_summary": _public_summary(
                self.response_summary,
                _RESPONSE_SUMMARY_KEYS,
            ),
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            "PreparedRequest("
            f"request_id={self.request_id!r}, repository_id={self.repository_id!r}, "
            f"connector_id={self.connector_id!r}, environment={self.environment!r}, "
            f"action_id={self.action_id!r}, version_id={self.version_id!r}, "
            f"method={self.method!r}, payload_hash={self.payload_hash!r}, "
            f"risk_tier={int(self.risk_tier)!r}, "
            f"approval_level={self.approval_level.value!r}, "
            f"mutation_class={self.mutation_class.value!r}, "
            f"approval_count={self.approval_count!r}, state={self.state.value!r}, "
            f"failure_reason={self.failure_reason!r}, expires_at={self.expires_at!r})"
        )


def _request_field(request: Any, name: str, default: Any) -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _valid_final_path(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("/")
        and "//" not in value
        and "?" not in value
        and "#" not in value
        and "\\" not in value
        and "://" not in value
    )


def render_action_path(path_template: str, path_parameters: Any) -> str:
    """Render one catalog path template from an exact set of segment values."""

    if not isinstance(path_template, str) or not _valid_final_path(path_template):
        raise ValueError("invalid_action_path")
    if not isinstance(path_parameters, Mapping):
        raise ValueError("invalid_action_path")
    if any(not isinstance(key, str) for key in path_parameters):
        raise ValueError("invalid_action_path")

    placeholders: dict[str, int] = {}
    rendered_segments: list[str] = []
    for segment in path_template.split("/"):
        match = _PATH_PLACEHOLDER.fullmatch(segment)
        if match is not None:
            name = match.group(1)
            if name in placeholders:
                raise ValueError("invalid_action_path")
            placeholders[name] = len(rendered_segments)
            rendered_segments.append("")
            continue
        if "{" in segment or "}" in segment:
            raise ValueError("invalid_action_path")
        _validate_path_segment(segment, allow_empty=not segment)
        rendered_segments.append(segment)

    if set(path_parameters) != set(placeholders):
        raise ValueError("invalid_action_path")
    for name, index in placeholders.items():
        raw_value = path_parameters[name]
        if isinstance(raw_value, bool) or not isinstance(raw_value, (str, int)):
            raise ValueError("invalid_action_path")
        raw_segment = str(raw_value)
        _validate_path_segment(raw_segment)
        rendered_segments[index] = quote(raw_segment, safe="-._~", encoding="utf-8")

    rendered = "/".join(rendered_segments)
    if not _valid_final_path(rendered):
        raise ValueError("invalid_action_path")
    return rendered


def _validate_path_segment(value: str, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError("invalid_action_path")
    decoded = value
    for _ in range(len(value) + 2):
        if (
            decoded in {".", ".."}
            or "/" in decoded
            or "\\" in decoded
            or "?" in decoded
            or "#" in decoded
            or any(unicodedata.category(character) == "Cc" for character in decoded)
        ):
            raise ValueError("invalid_action_path")
        next_value = unquote(decoded, encoding="utf-8", errors="strict")
        if next_value == decoded:
            return
        decoded = next_value
    raise ValueError("invalid_action_path")


def _is_reason(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value))


def _valid_approval_binding(
    approval_level: ApprovalLevel,
    mutation_class: MutationClass,
) -> bool:
    expected = (
        ApprovalLevel.ELEVATED
        if mutation_class is MutationClass.SENSITIVE
        else ApprovalLevel.STANDARD
    )
    return approval_level is expected


def _risk_covers_floor(candidate: RiskDecision, floor: RiskDecision) -> bool:
    if candidate.tier < floor.tier:
        return False
    if floor.mutation_class is MutationClass.SENSITIVE:
        return candidate.mutation_class is MutationClass.SENSITIVE
    return candidate.mutation_class in {
        floor.mutation_class,
        MutationClass.SENSITIVE,
    }


def _validate_static_headers(inputs: Mapping[str, Any]) -> None:
    if not isinstance(inputs, Mapping):
        raise ValueError("invalid_request_inputs")
    headers = inputs.get("headers", {})
    if not isinstance(headers, Mapping):
        raise ValueError("invalid_request_inputs")
    for header, value in headers.items():
        if not isinstance(header, str) or not isinstance(value, str):
            raise ValueError("invalid_request_inputs")
        if header.casefold() in _FORBIDDEN_HEADER_NAMES:
            raise ValueError("forbidden_request_header")


def _validate_state_fields(request: PreparedRequest) -> None:
    if request.state in {RequestState.PREVIEWED, RequestState.AWAITING_CONFIRMATION}:
        valid = request.approval_count == 0 and request.failure_reason is None
    elif request.state in {RequestState.READY_TO_EXECUTE, RequestState.EXECUTING}:
        valid = request.approval_count == 1 and request.failure_reason is None
    elif request.state in {
        RequestState.SUCCEEDED,
        RequestState.FAILED,
        RequestState.OUTCOME_UNKNOWN,
    }:
        valid = request.approval_count in (0, 1)
    else:
        valid = False
    if not valid:
        raise ValueError("invalid_request_state")


def _sanitize_public_data(value: Any, allowlist: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_public_summary")
    sanitized = _redact_public_value(value, allowlist)
    if not isinstance(sanitized, dict):
        raise ValueError("invalid_public_summary")
    return sanitized


def _redact_public_value(value: Any, allowlist: frozenset[str]) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if name not in allowlist:
                continue
            compact = re.sub(r"[^a-z0-9]", "", name.casefold())
            if any(part in compact for part in _SENSITIVE_PUBLIC_KEY_PARTS):
                result[name] = "[REDACTED]"
            else:
                result[name] = _redact_public_value(item, allowlist)
        return result
    if isinstance(value, (tuple, list)):
        return [_redact_public_value(item, allowlist) for item in value]
    return redact_json(value)


def _string_mapping(value: Any, error: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise ValueError(error)
    return dict(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, list):
        return [_thaw(item) for item in value]
    return value


def _public_summary(
    value: Mapping[str, Any],
    allowlist: frozenset[str],
) -> dict[str, Any]:
    """Expose summary shape without copying business values out of local state."""

    return {
        str(key): _summary_shape(item, allowlist)
        for key, item in value.items()
        if str(key) in allowlist
    }


def _summary_shape(value: Any, allowlist: frozenset[str] | None = None) -> Any:
    selected = allowlist or _PREVIEW_SUMMARY_KEYS
    if isinstance(value, Mapping):
        return {
            str(key): _summary_shape(item, selected)
            for key, item in value.items()
            if str(key) in selected
        }
    if isinstance(value, (tuple, list)):
        return [_summary_shape(item, selected) for item in value]
    return "[REDACTED]"
