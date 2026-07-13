from datetime import UTC, datetime

from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.response_shape import extract_response_shape
from mercury_tools.qualification.templates import render_summary_en, render_summary_th


def valid_record(**overrides):
    values = {
        "opaque_evidence_id": "ev_01j00000000000000000000000",
        "run_id": "run_01j00000000000000000000000",
        "action_id": "act_abc123",
        "version_id": "av_def456",
        "connector_id": "flowaccount",
        "environment": "sandbox",
        "validation_status": ValidationStatus.CONTRACT_VALIDATED,
        "evidence_level": EvidenceLevel.CONTRACT_VALIDATED,
        "execution_eligibility": ExecutionEligibility.DISCOVERY_ONLY,
        "approved_public": False,
        "summary_th": "ตรวจสอบสัญญา endpoint แล้วโดยยังไม่ได้เรียก provider",
        "summary_en": "Endpoint contract validated without a provider call.",
        "prerequisites": (),
        "limitations": ("provider_call_not_observed",),
        "recommended_next_step": "complete_sandbox_validation",
        "response_shape": {},
        "status_class": "not_attempted",
        "latency_ms": None,
        "semantic_contract": SemanticContract(
            business_object="invoice",
            operation="list",
            accounting_uses=("revenue_review",),
        ),
        "evidence_sha256": "0" * 64,
        "reviewed_by": "release_reviewer",
        "runner_version": "0.2.1",
        "run_state": QualificationRunState.COMPLETED,
        "evaluated_at": datetime(2026, 7, 13, tzinfo=UTC),
        "expires_at": None,
    }
    values.update(overrides)
    return ValidationKnowledge.model_validate(values)


def test_response_shape_keeps_names_and_types_but_drops_values():
    source = {"data": [{"recordId": 9912, "customerEmail": "person@example.com"}]}
    shape = extract_response_shape(source)
    serialized = str(shape)

    assert shape == {
        "data": {
            "type": "array",
            "items": {"customerEmail": "string", "recordId": "integer"},
        }
    }
    assert "9912" not in serialized
    assert "person@example.com" not in serialized


def test_response_shape_is_bounded_by_depth_and_field_count():
    nested = current = {}
    for _index in range(8):
        current["nested"] = {}
        current = current["nested"]
    wide = {f"field_{index:03d}": index for index in range(130)}

    truncated = extract_response_shape(nested)
    for _ in range(5):
        truncated = truncated["nested"]
    assert truncated["nested"] == "truncated"
    shape = extract_response_shape(wide)
    assert len(shape) == 128
    assert "field_000" in shape
    assert "field_127" in shape
    assert "field_128" not in shape


def test_response_shape_classifies_json_scalars_and_empty_arrays():
    assert extract_response_shape(
        {"null": None, "bool": True, "integer": 1, "number": 1.5, "text": "x"}
    ) == {
        "bool": "boolean",
        "integer": "integer",
        "null": "null",
        "number": "number",
        "text": "string",
    }
    assert extract_response_shape({"items": []}) == {
        "items": {"type": "array", "items": "unknown"}
    }


def test_every_terminal_status_has_thai_and_english_copy():
    for status in ValidationStatus:
        record = valid_record(validation_status=status)
        assert render_summary_th(record)
        assert render_summary_en(record)


def test_status_templates_are_deterministic_and_do_not_echo_record_copy():
    first = valid_record(
        validation_status=ValidationStatus.LIVE_SUCCESS,
        summary_th="internal Thai wording",
        summary_en="internal English wording",
    )
    second = valid_record(
        validation_status=ValidationStatus.LIVE_SUCCESS,
        summary_th="different internal wording",
        summary_en="different internal wording",
    )

    assert render_summary_th(first) == render_summary_th(second)
    assert render_summary_en(first) == render_summary_en(second)
    assert "internal" not in render_summary_th(first)
    assert "internal" not in render_summary_en(first)
