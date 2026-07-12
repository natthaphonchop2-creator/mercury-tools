"""Immutable local previews for ERP mutations.

These models deliberately retain bound request inputs only for the local SQLite
store. Callers that need model-visible data must use ``public_dict``.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import deep_freeze
from mercury_tools.catalog.models import CatalogAction, RiskTier, revalidate_catalog_action
from mercury_tools.drivers.models import AuthContext
from mercury_tools.execution.policy import RiskDecision, effective_risk
from mercury_tools.local.repository import RepositoryContext
from mercury_tools.safety.redaction import redact_json

PREVIEW_TTL = timedelta(minutes=15)
_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_PAYLOAD_HASH = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^req_[0-9a-z_]{8,128}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,127}$")
_PUBLIC_STRUCTURAL_KEY = re.compile(r"^[a-z][A-Za-z0-9_-]{0,127}$")
_DYNAMIC_PROVIDER_KEY = re.compile(r"^[A-Za-z]{2,12}[_-]([A-Za-z0-9]{6,})$")
_UUID_KEY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_OPAQUE_HEX_KEY = re.compile(r"^[0-9a-f]{16,}$", re.IGNORECASE)
_OPAQUE_ALNUM_KEY = re.compile(r"^[A-Za-z0-9]{12,}$")
_FORBIDDEN_HEADER_NAMES = frozenset(
    {"authorization", "cookie", "host", "proxy-authorization", "set-cookie"}
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
    AWAITING_FINAL_CONFIRMATION = "awaiting_final_confirmation"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTING = "executing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """Return the SHA-256 hash of canonical, JSON-safe bound request data."""

    try:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("payload_not_canonicalizable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
    required_confirmations: int,
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
        "required_confirmations": required_confirmations,
    }


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
    final_path: str
    sanitized_summary: dict[str, Any]
    request_inputs: dict[str, Any]
    payload_hash: str
    risk_tier: RiskTier
    required_confirmations: int
    confirmation_count: int = 0
    state: RequestState
    failure_reason: str | None = None
    response_summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    expires_at: datetime

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
        if _PAYLOAD_HASH.fullmatch(self.payload_hash) is None:
            raise ValueError("invalid_payload_hash")
        if self.required_confirmations not in (1, 2):
            raise ValueError("invalid_required_confirmations")
        if self.required_confirmations != int(self.risk_tier):
            raise ValueError("invalid_required_confirmations")
        if self.confirmation_count < 0 or self.confirmation_count > self.required_confirmations:
            raise ValueError("invalid_confirmation_count")
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

        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "expires_at", expires_at)
        object.__setattr__(
            self,
            "sanitized_summary",
            deep_freeze(_sanitize_public_data(self.sanitized_summary)),
        )
        object.__setattr__(self, "request_inputs", deep_freeze(self.request_inputs))
        object.__setattr__(
            self,
            "response_summary",
            deep_freeze(_sanitize_public_data(self.response_summary)),
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
            required_confirmations=self.required_confirmations,
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
    ) -> PreparedRequest:
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
        if risk.required_confirmations != int(risk.tier):
            raise ValueError("invalid_risk_decision")
        risk_floor = effective_risk(action)
        if (
            risk.tier < risk_floor.tier
            or risk.required_confirmations < risk_floor.required_confirmations
        ):
            raise ValueError("risk_below_runtime_floor")

        method = _request_field(request, "method", action.method.value)
        if method != action.method.value:
            raise ValueError("request_method_mismatch")
        final_path = _request_field(request, "final_path", None)
        if final_path is None:
            final_path = _request_field(request, "path", None)
        summary = _request_field(request, "sanitized_summary", {})
        inputs = _request_field(request, "request_inputs", {})
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
            required_confirmations=risk.required_confirmations,
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
            final_path=final_path,
            sanitized_summary=summary,
            request_inputs=inputs,
            payload_hash=payload_hash,
            risk_tier=risk.tier,
            required_confirmations=risk.required_confirmations,
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
            "sanitized_summary": _public_summary(self.sanitized_summary),
            "payload_hash": self.payload_hash,
            "risk_tier": int(self.risk_tier),
            "required_confirmations": self.required_confirmations,
            "confirmation_count": self.confirmation_count,
            "state": self.state.value,
            "failure_reason": self.failure_reason,
            "response_summary": _public_summary(self.response_summary),
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
            f"required_confirmations={self.required_confirmations!r}, "
            f"confirmation_count={self.confirmation_count!r}, state={self.state.value!r}, "
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


def _is_reason(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value))


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
        valid = request.confirmation_count == 0 and request.failure_reason is None
    elif request.state is RequestState.AWAITING_FINAL_CONFIRMATION:
        valid = (
            request.required_confirmations == 2
            and request.confirmation_count == 1
            and request.failure_reason is None
        )
    elif request.state in {RequestState.READY_TO_EXECUTE, RequestState.EXECUTING}:
        valid = (
            request.confirmation_count == request.required_confirmations
            and request.failure_reason is None
        )
    elif request.state in {
        RequestState.SUCCEEDED,
        RequestState.FAILED,
        RequestState.OUTCOME_UNKNOWN,
    }:
        valid = request.confirmation_count <= request.required_confirmations
    else:
        valid = False
    if not valid:
        raise ValueError("invalid_request_state")


def _sanitize_public_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid_public_summary")
    sanitized = _redact_public_value(redact_json(dict(value)))
    if not isinstance(sanitized, dict):
        raise ValueError("invalid_public_summary")
    return sanitized


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if not _is_safe_public_key(name):
                continue
            compact = re.sub(r"[^a-z0-9]", "", name.casefold())
            if any(part in compact for part in _SENSITIVE_PUBLIC_KEY_PARTS):
                result[name] = "[REDACTED]"
            else:
                result[name] = _redact_public_value(item)
        return result
    if isinstance(value, (tuple, list)):
        return [_redact_public_value(item) for item in value]
    return value


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


def _public_summary(value: Mapping[str, Any]) -> dict[str, Any]:
    """Expose summary shape without copying business values out of local state."""

    return {
        str(key): _summary_shape(item)
        for key, item in value.items()
        if _is_safe_public_key(str(key))
    }


def _summary_shape(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _summary_shape(item)
            for key, item in value.items()
            if _is_safe_public_key(str(key))
        }
    if isinstance(value, (tuple, list)):
        return [_summary_shape(item) for item in value]
    return "[REDACTED]"


def _is_safe_public_key(key: str) -> bool:
    if _PUBLIC_STRUCTURAL_KEY.fullmatch(key) is None:
        return False
    if _UUID_KEY.fullmatch(key) is not None or _OPAQUE_HEX_KEY.fullmatch(key) is not None:
        return False
    if _OPAQUE_ALNUM_KEY.fullmatch(key) is not None and any(
        character.isdigit() for character in key
    ):
        return False
    provider_match = _DYNAMIC_PROVIDER_KEY.fullmatch(key)
    if provider_match is not None:
        suffix = provider_match.group(1)
        if any(character.isdigit() for character in suffix) or len(suffix) >= 12:
            return False
    compact = re.sub(r"[^a-z0-9]", "", key.casefold())
    return not compact.startswith(("bearer", "githubpat", "skproj", "xoxb", "xoxp"))
