from datetime import UTC, datetime, timedelta, timezone

import pytest

from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.selection import (
    EvidenceOutcome,
    EvidenceRequest,
    select_evidence,
)
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
ACTION_ID = f"act_{1:024x}"
VERSION_ID = f"av_{1:064x}"


def _record(**overrides) -> ValidationKnowledge:
    status = ValidationStatus(overrides.get("validation_status", ValidationStatus.LIVE_SUCCESS))
    run_number = int(overrides.pop("run_number", 1))
    values = {
        "opaque_evidence_id": f"ev_{run_number:026d}",
        "run_id": f"run_{run_number:026d}",
        "action_id": ACTION_ID,
        "version_id": VERSION_ID,
        "connector_id": "flowaccount",
        "environment": "sandbox",
        "validation_status": status,
        "evidence_level": EvidenceLevel.SANDBOX_OBSERVED,
        "execution_eligibility": ExecutionEligibility.SANDBOX_READ,
        "approved_public": True,
        "summary_th": SUMMARY_TH[status],
        "summary_en": SUMMARY_EN[status],
        "prerequisites": (),
        "limitations": (),
        "recommended_next_step": "review_accounting_result",
        "response_shape": {
            "counterparty_tax_id": "string",
            "document_id": "string",
            "email": "string",
        },
        "status_class": "2xx",
        "latency_ms": 25,
        "semantic_contract": SemanticContract(
            business_object="invoice",
            operation="list",
            accounting_uses=("revenue_review",),
        ),
        "evidence_sha256": f"{run_number:064x}",
        "reviewed_by": "release_reviewer",
        "runner_version": "v0.2.1",
        "run_state": QualificationRunState.COMPLETED,
        "evaluated_at": NOW - timedelta(hours=1),
        "expires_at": NOW + timedelta(days=1),
    }
    values.update(overrides)
    values["validation_status"] = status
    values.setdefault("summary_th", SUMMARY_TH[status])
    values.setdefault("summary_en", SUMMARY_EN[status])
    return ValidationKnowledge.model_validate(values)


def _request(**overrides) -> EvidenceRequest:
    values = {
        "connector_id": "flowaccount",
        "action_id": ACTION_ID,
        "version_id": VERSION_ID,
        "environment": "sandbox",
    }
    values.update(overrides)
    return EvidenceRequest.model_validate(values)


def test_no_current_public_evidence_is_distinct() -> None:
    selection = select_evidence([], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.NO_EVIDENCE
    assert selection.selected is None
    assert selection.blocking_conditions == ()
    assert selection.records == ()


def test_live_success_is_selected() -> None:
    success = _record()

    selection = select_evidence([success], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.LIVE_SUCCESS
    assert selection.selected == success
    assert selection.blocking_conditions == ()


def test_contract_only_fallback_is_distinct() -> None:
    contract = _record(
        validation_status=ValidationStatus.CONTRACT_VALIDATED,
        evidence_level=EvidenceLevel.CONTRACT_VALIDATED,
        execution_eligibility=ExecutionEligibility.DISCOVERY_ONLY,
        status_class="not_attempted",
        latency_ms=None,
    )

    selection = select_evidence([contract], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.CONTRACT_ONLY
    assert selection.selected == contract
    assert selection.blocking_conditions == ()


def test_newer_failure_blocks_older_success() -> None:
    success = _record(run_number=1, evaluated_at=NOW - timedelta(days=1))
    failed = _record(
        run_number=2,
        validation_status=ValidationStatus.LIVE_FAILED,
        evaluated_at=NOW,
        run_state=QualificationRunState.FAILED,
        status_class="4xx",
    )

    selection = select_evidence([success, failed], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.BLOCKER
    assert selection.selected is None
    assert selection.blocking_conditions == ("live_failed",)
    assert selection.records == (failed, success)


def test_quarantined_success_is_distinct_and_never_selected() -> None:
    quarantined = _record(run_state=QualificationRunState.QUARANTINED)

    selection = select_evidence([quarantined], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.QUARANTINE
    assert selection.selected is None
    assert selection.blocking_conditions == ("quarantine",)


def test_unapproved_and_expired_blockers_do_not_hide_current_success() -> None:
    success = _record(run_number=1)
    unapproved = _record(
        run_number=2,
        validation_status=ValidationStatus.LIVE_FAILED,
        approved_public=False,
        evaluated_at=NOW,
        run_state=QualificationRunState.FAILED,
        status_class="4xx",
    )
    expired = _record(
        run_number=3,
        validation_status=ValidationStatus.OUTCOME_UNKNOWN,
        evaluated_at=NOW - timedelta(days=2),
        expires_at=NOW - timedelta(seconds=1),
        run_state=QualificationRunState.FAILED,
        status_class="timeout",
    )

    selection = select_evidence([expired, unapproved, success], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.LIVE_SUCCESS
    assert selection.selected == success
    assert selection.records == (success,)


def test_exact_expiry_boundary_is_expired() -> None:
    record = _record(expires_at=NOW)

    selection = select_evidence([record], request=_request(), now=NOW)

    assert selection.outcome is EvidenceOutcome.NO_EVIDENCE
    assert selection.records == ()


def test_aware_offsets_are_normalized_to_utc_before_ordering() -> None:
    offset = timezone(timedelta(hours=7))
    same_instant = NOW.astimezone(offset)
    first = _record(run_number=1, evaluated_at=same_instant)
    second = _record(run_number=2, evaluated_at=NOW)

    selection = select_evidence([first, second], request=_request(), now=NOW)

    assert selection.records == (
        second,
        first.model_copy(update={"evaluated_at": NOW}),
    )
    assert all(record.evaluated_at.tzinfo is UTC for record in selection.records)


def test_equal_timestamps_use_run_id_descending_as_stable_tie_breaker() -> None:
    first = _record(run_number=1, evaluated_at=NOW)
    second = _record(run_number=2, evaluated_at=NOW)

    forward = select_evidence([first, second], request=_request(), now=NOW)
    reversed_input = select_evidence([second, first], request=_request(), now=NOW)

    assert tuple(item.run_id for item in forward.records) == (second.run_id, first.run_id)
    assert reversed_input.records == forward.records
    assert forward.selected == second


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("connector_id", "peak"),
        ("action_id", f"act_{2:024x}"),
        ("version_id", f"av_{2:064x}"),
        ("environment", "uat"),
    ],
)
def test_exact_connector_action_version_and_environment_scope_is_required(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="^evidence_scope_mismatch$"):
        select_evidence([_record(**{field: value})], request=_request(), now=NOW)


@pytest.mark.parametrize("field", ["evaluated_at", "expires_at"])
def test_naive_record_timestamps_are_rejected_before_selection(field: str) -> None:
    naive = datetime(2026, 7, 13, 12)

    with pytest.raises(ValueError, match="^evidence_timestamp_naive$"):
        select_evidence([_record(**{field: naive})], request=_request(), now=NOW)


def test_naive_now_is_rejected_before_selection() -> None:
    with pytest.raises(ValueError, match="^evidence_timestamp_naive$"):
        select_evidence(
            [_record()],
            request=_request(),
            now=datetime(2026, 7, 13, 12),
        )
