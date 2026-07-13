"""Pydantic contracts for endpoint qualification evidence."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mercury_tools.catalog.identity import validate_credential_safe, validate_credential_safe_paths


class ValidationStatus(StrEnum):
    LIVE_SUCCESS = "live_success"
    LIVE_FAILED = "live_failed"
    CONTRACT_VALIDATED = "contract_validated"
    BLOCKED_MISSING_CREDENTIALS = "blocked_missing_credentials"
    BLOCKED_MISSING_PREREQUISITE = "blocked_missing_prerequisite"
    BLOCKED_EXTERNAL_EFFECT = "blocked_external_effect"
    UNSUPPORTED_BY_SANDBOX = "unsupported_by_sandbox"
    OUTCOME_UNKNOWN = "outcome_unknown"


class EvidenceLevel(StrEnum):
    DOCUMENTED = "documented"
    CONTRACT_VALIDATED = "contract_validated"
    SANDBOX_OBSERVED = "sandbox_observed"
    ACCOUNTANT_REVIEWED = "accountant_reviewed"


class ExecutionEligibility(StrEnum):
    DISCOVERY_ONLY = "discovery_only"
    SANDBOX_READ = "sandbox_read"
    SANDBOX_WRITE_WITH_APPROVAL = "sandbox_write_with_approval"
    PRODUCTION_PENDING_VALIDATION = "production_pending_validation"
    BLOCKED = "blocked"


class QualificationRunState(StrEnum):
    COMPLETED = "completed"
    QUARANTINED = "quarantined"
    FAILED = "failed"


class StrictSafeModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_credentials(cls, value: Any) -> Any:
        validate_credential_safe(value)
        validate_credential_safe_paths(value)
        return value


class SemanticContract(StrictSafeModel):
    business_object: str
    operation: str
    accounting_uses: tuple[str, ...] = ()
    output_semantics: dict[str, str] = Field(default_factory=dict)
    join_keys: tuple[str, ...] = ()
    next_action_ids: tuple[str, ...] = ()
    required_external_capabilities: tuple[str, ...] = ()
    optional_external_capabilities: tuple[str, ...] = ()
    fallbacks: tuple[str, ...] = ()


class ValidationKnowledge(StrictSafeModel):
    opaque_evidence_id: str
    run_id: str
    action_id: str
    version_id: str
    connector_id: str
    environment: str
    validation_status: ValidationStatus
    evidence_level: EvidenceLevel
    execution_eligibility: ExecutionEligibility
    approved_public: bool = False
    summary_th: str
    summary_en: str
    prerequisites: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    recommended_next_step: str
    response_shape: dict[str, Any] = Field(default_factory=dict)
    status_class: str
    latency_ms: int | None = Field(default=None, ge=0)
    semantic_contract: SemanticContract
    evidence_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reviewed_by: str
    runner_version: str
    run_state: QualificationRunState
    evaluated_at: datetime
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_approved_public_content(self) -> ValidationKnowledge:
        if not self.approved_public:
            return self

        from mercury_tools.qualification.response_shape import (
            _validate_approved_public_response_shape,
        )
        from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

        if (
            self.summary_th != SUMMARY_TH[self.validation_status]
            or self.summary_en != SUMMARY_EN[self.validation_status]
        ):
            raise ValueError("approved_public_summary_not_controlled")
        _validate_approved_public_response_shape(self.response_shape)
        return self


class QualificationReport(StrictSafeModel):
    connector_id: str
    environment: str
    run_id: str
    run_state: QualificationRunState
    records: tuple[ValidationKnowledge, ...]

    @property
    def total(self) -> int:
        return len(self.records)
