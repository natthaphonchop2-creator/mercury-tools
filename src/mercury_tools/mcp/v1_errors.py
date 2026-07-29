"""Closed, secret-safe V1 MCP public error codes."""

from __future__ import annotations

from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from mercury_tools.auth.models import MercuryAuthError
from mercury_tools.providers.oauth import ProviderOAuthError
from mercury_tools.providers.peak_setup import PeakSetupError
from mercury_tools.providers.store import ProviderStoreError
from mercury_tools.qualification.provider_mcp import QualificationGateError
from mercury_tools.workspaces.service import WorkspaceAccessError

V1ErrorCode: TypeAlias = Literal[
    "mercury_auth_required",
    "mercury_scope_insufficient",
    "workspace_context_required",
    "workspace_access_denied",
    "provider_connection_required",
    "provider_connection_invalid",
    "provider_authorization_expired",
    "provider_setup_expired",
    "provider_setup_replayed",
    "provider_revocation_required",
    "provider_permission_insufficient",
    "provider_company_mismatch",
    "capability_unavailable",
    "capability_unreviewed",
    "capability_version_changed",
    "validation_failed",
    "preview_expired",
    "preview_binding_mismatch",
    "preview_state_changed",
    "confirmation_required",
    "duplicate_batch_item",
    "operation_in_progress",
    "provider_rejected",
    "outcome_unknown",
    "manual_review_required",
    "insufficient_evidence",
    "rate_limited",
]

V1_ERROR_CODES = frozenset(V1ErrorCode.__args__)


class MercuryV1ErrorDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    code: V1ErrorCode
    guidance: str = Field(min_length=1, max_length=300)


class MercuryV1ErrorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    status: Literal["error"] = "error"
    error: MercuryV1ErrorDetails


class MercuryV1ToolError(RuntimeError):
    """A deterministic V1 public error with no provider exception detail."""

    def __init__(self, code: V1ErrorCode) -> None:
        if code not in V1_ERROR_CODES:
            raise ValueError("mercury_v1_tool_error_invalid")
        self.code = code
        super().__init__(code)


def published_error_output_schema() -> dict[str, object]:
    """Return the closed Section 16 error schema for public MCP output contracts."""

    return TypeAdapter(MercuryV1ErrorOutput).json_schema()


def public_error_code(error: BaseException) -> V1ErrorCode:
    """Map foundation failures to the approved V1 error union."""

    if isinstance(error, MercuryV1ToolError):
        return error.code
    if isinstance(error, MercuryAuthError):
        if error.code == "mercury_scope_insufficient":
            return "mercury_scope_insufficient"
        return "mercury_auth_required"
    if isinstance(error, WorkspaceAccessError):
        return "workspace_access_denied"
    if isinstance(error, QualificationGateError):
        return error.code
    if isinstance(error, ProviderStoreError):
        if error.code == "provider_connection_not_found":
            return "provider_connection_required"
        return "provider_connection_invalid"
    if isinstance(error, ProviderOAuthError):
        return _provider_oauth_error_code(error.code)
    if isinstance(error, PeakSetupError):
        return _peak_setup_error_code(error.code)
    if isinstance(error, ValueError) and error.args and error.args[0] in V1_ERROR_CODES:
        return error.args[0]
    return "validation_failed"


def error_output(error: BaseException) -> MercuryV1ErrorOutput:
    code = public_error_code(error)
    return MercuryV1ErrorOutput(error=MercuryV1ErrorDetails(code=code, guidance=_guidance(code)))


def _guidance(code: V1ErrorCode) -> str:
    messages: dict[V1ErrorCode, str] = {
        "mercury_auth_required": "Sign in to Mercury and try again.",
        "mercury_scope_insufficient": "Request a Mercury session with the required scope.",
        "workspace_context_required": "Call get_mercury_context and select a workspace.",
        "workspace_access_denied": "Use a workspace where you are a member.",
        "provider_connection_required": "Connect the provider for this workspace first.",
        "provider_connection_invalid": "Review the provider connection and try again.",
        "provider_authorization_expired": "Reconnect the provider authorization.",
        "provider_setup_expired": "Start a new provider setup session.",
        "provider_setup_replayed": "Start a new provider setup session.",
        "provider_revocation_required": "Complete revocation in the provider account.",
        "provider_permission_insufficient": "Grant the required provider permissions.",
        "provider_company_mismatch": "Use the provider company bound to this connection.",
        "capability_unavailable": "Use an enabled capability for this connection.",
        "capability_unreviewed": "The capability is not reviewed for publication.",
        "capability_version_changed": "Retrieve the current capability schema and retry.",
        "validation_failed": "Review the request fields and try again.",
        "preview_expired": "Prepare a new preview.",
        "preview_binding_mismatch": "Render the current preview before confirming.",
        "preview_state_changed": "Render the current preview before confirming.",
        "confirmation_required": "Provide the required confirmation value.",
        "duplicate_batch_item": "Use unique client item identifiers.",
        "operation_in_progress": "Wait for the existing operation result.",
        "provider_rejected": "Review the provider validation result.",
        "outcome_unknown": "Do not retry; reconcile the existing operation.",
        "manual_review_required": "Ask an accountant to review this operation.",
        "insufficient_evidence": "Provide reviewed qualification evidence first.",
        "rate_limited": "Wait before retrying this request.",
    }
    return messages[code]


def _provider_oauth_error_code(code: str) -> V1ErrorCode:
    if "permission" in code:
        return "provider_permission_insufficient"
    if "expired" in code:
        return "provider_authorization_expired"
    if "replay" in code:
        return "provider_setup_replayed"
    if "company" in code:
        return "provider_company_mismatch"
    return "provider_connection_invalid"


def _peak_setup_error_code(code: str) -> V1ErrorCode:
    if "expired" in code:
        return "provider_setup_expired"
    if "replay" in code or "consumed" in code:
        return "provider_setup_replayed"
    if "revocation" in code:
        return "provider_revocation_required"
    return "provider_connection_invalid"


__all__ = [
    "MercuryV1ToolError",
    "MercuryV1ErrorDetails",
    "MercuryV1ErrorOutput",
    "V1ErrorCode",
    "V1_ERROR_CODES",
    "error_output",
    "published_error_output_schema",
    "public_error_code",
]
