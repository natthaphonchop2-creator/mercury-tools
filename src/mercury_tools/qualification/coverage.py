"""Deterministic, secret-safe coverage reports for endpoint qualification."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from pydantic import Field

from mercury_tools.catalog.models import CatalogAction, revalidate_catalog_action
from mercury_tools.qualification.manifest import FLOWACCOUNT_ACTION_COUNT
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationReport,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.response_shape import (
    _validate_approved_public_response_shape,
)
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

_OPAQUE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_NEXT_STEPS = {
    ValidationStatus.LIVE_SUCCESS: "review_local_evidence",
    ValidationStatus.LIVE_FAILED: "review_classified_failure",
    ValidationStatus.CONTRACT_VALIDATED: "complete_sandbox_validation",
    ValidationStatus.BLOCKED_MISSING_CREDENTIALS: "configure_connector",
    ValidationStatus.BLOCKED_MISSING_PREREQUISITE: "prepare_reviewed_fixture",
    ValidationStatus.BLOCKED_EXTERNAL_EFFECT: "keep_action_blocked",
    ValidationStatus.UNSUPPORTED_BY_SANDBOX: "use_supported_action",
    ValidationStatus.OUTCOME_UNKNOWN: "reconcile_manually",
}
_LIMITATIONS = {
    ValidationStatus.LIVE_SUCCESS: (),
    ValidationStatus.LIVE_FAILED: ("classified_failure",),
    ValidationStatus.CONTRACT_VALIDATED: ("provider_call_not_observed",),
    ValidationStatus.BLOCKED_MISSING_CREDENTIALS: ("live_validation_not_attempted",),
    ValidationStatus.BLOCKED_MISSING_PREREQUISITE: ("reviewed_prerequisite_unavailable",),
    ValidationStatus.BLOCKED_EXTERNAL_EFFECT: ("uncontrolled_external_effect",),
    ValidationStatus.UNSUPPORTED_BY_SANDBOX: ("sandbox_unavailable",),
    ValidationStatus.OUTCOME_UNKNOWN: ("manual_reconciliation_required",),
}


class QualificationCoverageReport(QualificationReport):
    """A qualification report with an explicit JSON-safe local output boundary."""

    http_attempts: int = Field(default=0, ge=0, le=40)
    mutation_attempts: int = Field(default=0, ge=0, le=1)

    def public_dict(self) -> dict[str, Any]:
        counts = Counter(record.validation_status.value for record in self.records)
        return {
            "connector_id": self.connector_id,
            "environment": self.environment,
            "run_id": self.run_id,
            "run_state": self.run_state.value,
            "total": self.total,
            "http_attempts": self.http_attempts,
            "mutation_attempts": self.mutation_attempts,
            "counts": {key: counts[key] for key in sorted(counts)},
            "records": [record.model_dump(mode="json") for record in self.records],
        }


def safe_response_shape(value: dict[str, Any] | str) -> dict[str, Any]:
    """Keep only response shapes accepted by the public evidence validator."""
    candidate: dict[str, Any] = value if isinstance(value, dict) else {"type": value}
    try:
        _validate_approved_public_response_shape(candidate)
    except ValueError:
        return {}
    return candidate


def build_terminal_record(
    *,
    action: CatalogAction,
    semantic_contract: SemanticContract,
    run_id: str,
    run_state: QualificationRunState,
    validation_status: ValidationStatus,
    execution_eligibility: ExecutionEligibility,
    evaluated_at: datetime,
    prerequisites: Sequence[str] = (),
    response_shape: Mapping[str, Any] | None = None,
    status_class: str = "not_attempted",
    latency_ms: int | None = None,
) -> ValidationKnowledge:
    """Build one controlled terminal record without retaining provider values."""
    checked_action = revalidate_catalog_action(action)
    checked_contract = SemanticContract.model_validate(
        {name: getattr(semantic_contract, name) for name in SemanticContract.model_fields}
    )
    shape = safe_response_shape(dict(response_shape or {}))
    evidence_payload = {
        "run_id": run_id,
        "action_id": checked_action.action_id,
        "version_id": checked_action.version_id,
        "validation_status": validation_status.value,
        "execution_eligibility": execution_eligibility.value,
        "prerequisites": list(prerequisites),
        "response_shape": shape,
        "status_class": status_class,
        "latency_ms": latency_ms,
        "semantic_contract": checked_contract.model_dump(mode="json"),
        "evaluated_at": evaluated_at.isoformat(),
    }
    encoded = json.dumps(
        evidence_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    evidence_sha256 = hashlib.sha256(encoded).hexdigest()
    evidence_level = (
        EvidenceLevel.SANDBOX_OBSERVED
        if validation_status
        in {
            ValidationStatus.LIVE_SUCCESS,
            ValidationStatus.LIVE_FAILED,
            ValidationStatus.OUTCOME_UNKNOWN,
        }
        else EvidenceLevel.CONTRACT_VALIDATED
    )
    return ValidationKnowledge(
        opaque_evidence_id="ev_" + _opaque_suffix(bytes.fromhex(evidence_sha256)),
        run_id=run_id,
        action_id=checked_action.action_id,
        version_id=checked_action.version_id,
        connector_id="flowaccount",
        environment="sandbox",
        validation_status=validation_status,
        evidence_level=evidence_level,
        execution_eligibility=execution_eligibility,
        approved_public=False,
        summary_th=SUMMARY_TH[validation_status],
        summary_en=SUMMARY_EN[validation_status],
        prerequisites=tuple(prerequisites),
        limitations=_LIMITATIONS[validation_status],
        recommended_next_step=_NEXT_STEPS[validation_status],
        response_shape=shape,
        status_class=status_class,
        latency_ms=latency_ms,
        semantic_contract=checked_contract,
        evidence_sha256=evidence_sha256,
        reviewed_by="local_reviewer",
        runner_version="0.2.1",
        run_state=run_state,
        evaluated_at=evaluated_at,
        expires_at=None,
    )


def build_coverage_report(
    actions: Sequence[CatalogAction],
    records: Sequence[ValidationKnowledge],
    run_state: QualificationRunState,
    *,
    run_id: str,
    http_attempts: int = 0,
    mutation_attempts: int = 0,
) -> QualificationCoverageReport:
    """Require exactly one terminal record for every canonical action identity."""
    checked_actions = tuple(revalidate_catalog_action(action) for action in actions)
    action_identities = tuple((action.action_id, action.version_id) for action in checked_actions)
    if (
        len(checked_actions) != FLOWACCOUNT_ACTION_COUNT
        or len(set(action_identities)) != FLOWACCOUNT_ACTION_COUNT
        or any(action.connector_id != "flowaccount" for action in checked_actions)
    ):
        raise ValueError("qualification_catalog_coverage_invalid")

    checked_records = tuple(ValidationKnowledge.model_validate(record) for record in records)
    record_identities = tuple((record.action_id, record.version_id) for record in checked_records)
    if (
        len(checked_records) != FLOWACCOUNT_ACTION_COUNT
        or len(set(record_identities)) != FLOWACCOUNT_ACTION_COUNT
        or set(record_identities) != set(action_identities)
    ):
        raise ValueError("qualification_record_coverage_invalid")

    finalized: list[ValidationKnowledge] = []
    for record in sorted(
        checked_records,
        key=lambda item: (item.action_id, item.version_id),
    ):
        if (
            record.connector_id != "flowaccount"
            or record.environment != "sandbox"
            or record.run_id != run_id
        ):
            raise ValueError("qualification_record_scope_invalid")
        finalized.append(record.model_copy(update={"run_state": run_state}))

    return QualificationCoverageReport(
        connector_id="flowaccount",
        environment="sandbox",
        run_id=run_id,
        run_state=run_state,
        http_attempts=http_attempts,
        mutation_attempts=mutation_attempts,
        records=tuple(finalized),
    )


def _opaque_suffix(digest: bytes) -> str:
    value = int.from_bytes(digest[:17], "big") >> 6
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        value, remainder = divmod(value, 32)
        characters[index] = _OPAQUE_ALPHABET[remainder]
    return "".join(characters)


__all__ = [
    "QualificationCoverageReport",
    "build_coverage_report",
    "build_terminal_record",
    "safe_response_shape",
]
