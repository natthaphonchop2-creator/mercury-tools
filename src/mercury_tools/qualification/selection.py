"""Fail-closed selection of exact-scope endpoint validation evidence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field

from mercury_tools.qualification.models import (
    QualificationRunState,
    StrictSafeModel,
    ValidationKnowledge,
    ValidationStatus,
)

_BLOCKING_STATUSES = frozenset(
    {
        ValidationStatus.LIVE_FAILED,
        ValidationStatus.BLOCKED_MISSING_CREDENTIALS,
        ValidationStatus.BLOCKED_MISSING_PREREQUISITE,
        ValidationStatus.BLOCKED_EXTERNAL_EFFECT,
        ValidationStatus.UNSUPPORTED_BY_SANDBOX,
        ValidationStatus.OUTCOME_UNKNOWN,
    }
)
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9._:-]+$"


class EvidenceOutcome(StrEnum):
    NO_EVIDENCE = "no_evidence"
    QUARANTINE = "quarantine"
    BLOCKER = "blocker"
    CONTRACT_ONLY = "contract_only"
    LIVE_SUCCESS = "live_success"


class EvidenceRequest(StrictSafeModel):
    connector_id: str = Field(min_length=1, max_length=200, pattern=_IDENTIFIER_PATTERN)
    action_id: str = Field(min_length=1, max_length=200, pattern=_IDENTIFIER_PATTERN)
    version_id: str = Field(min_length=1, max_length=200, pattern=_IDENTIFIER_PATTERN)
    environment: Literal["sandbox", "test", "uat", "production"]

    @property
    def scope_key(self) -> tuple[str, str, str, str]:
        return (
            self.connector_id,
            self.action_id,
            self.version_id,
            self.environment,
        )

    def matches(self, record: ValidationKnowledge) -> bool:
        return self.scope_key == (
            record.connector_id,
            record.action_id,
            record.version_id,
            record.environment,
        )


class EvidenceSelection(StrictSafeModel):
    outcome: EvidenceOutcome
    selected: ValidationKnowledge | None
    blocking_conditions: tuple[str, ...]
    records: tuple[ValidationKnowledge, ...]


def select_evidence(
    records: Sequence[ValidationKnowledge],
    *,
    request: EvidenceRequest,
    now: datetime,
) -> EvidenceSelection:
    """Select public evidence ordered by evaluated_at desc, then run_id desc."""

    validated_request = _validated_request(request)
    normalized_now = normalize_evidence_time(now)
    normalized_records = tuple(_validated_record(record) for record in records)
    if any(not validated_request.matches(record) for record in normalized_records):
        raise ValueError("evidence_scope_mismatch")

    current = [
        record
        for record in normalized_records
        if record.approved_public
        and (record.expires_at is None or record.expires_at > normalized_now)
    ]
    current.sort(key=lambda item: (item.evaluated_at, item.run_id), reverse=True)
    ordered = tuple(current)

    if any(record.run_state is QualificationRunState.QUARANTINED for record in ordered):
        return EvidenceSelection(
            outcome=EvidenceOutcome.QUARANTINE,
            selected=None,
            blocking_conditions=("quarantine",),
            records=ordered,
        )

    blockers = _blocking_conditions(ordered)
    if blockers:
        return EvidenceSelection(
            outcome=EvidenceOutcome.BLOCKER,
            selected=None,
            blocking_conditions=blockers,
            records=ordered,
        )

    live = next(
        (record for record in ordered if record.validation_status is ValidationStatus.LIVE_SUCCESS),
        None,
    )
    if live is not None:
        return EvidenceSelection(
            outcome=EvidenceOutcome.LIVE_SUCCESS,
            selected=live,
            blocking_conditions=(),
            records=ordered,
        )

    contract = next(
        (
            record
            for record in ordered
            if record.validation_status is ValidationStatus.CONTRACT_VALIDATED
        ),
        None,
    )
    if contract is not None:
        return EvidenceSelection(
            outcome=EvidenceOutcome.CONTRACT_ONLY,
            selected=contract,
            blocking_conditions=(),
            records=ordered,
        )

    return EvidenceSelection(
        outcome=EvidenceOutcome.NO_EVIDENCE,
        selected=None,
        blocking_conditions=(),
        records=ordered,
    )


def normalize_evidence_time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evidence_timestamp_naive")
    return value.astimezone(UTC)


def _validated_request(request: Any) -> EvidenceRequest:
    try:
        return EvidenceRequest.model_validate(request)
    except (TypeError, ValueError):
        raise ValueError("evidence_request_invalid") from None


def _validated_record(record: Any) -> ValidationKnowledge:
    try:
        validated = ValidationKnowledge.model_validate(record)
    except (TypeError, ValueError):
        raise ValueError("validation_evidence_invalid") from None

    evaluated_at = normalize_evidence_time(validated.evaluated_at)
    expires_at = (
        normalize_evidence_time(validated.expires_at) if validated.expires_at is not None else None
    )
    if expires_at is not None and expires_at <= evaluated_at:
        raise ValueError("validation_evidence_invalid")
    return validated.model_copy(update={"evaluated_at": evaluated_at, "expires_at": expires_at})


def _blocking_conditions(records: Sequence[ValidationKnowledge]) -> tuple[str, ...]:
    conditions: dict[str, None] = {}
    for record in records:
        if record.validation_status in _BLOCKING_STATUSES:
            conditions.setdefault(record.validation_status.value, None)
        elif record.run_state is QualificationRunState.FAILED:
            conditions.setdefault("run_state_failed", None)
    return tuple(conditions)
