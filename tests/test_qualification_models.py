from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationReport,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)


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


def test_validation_knowledge_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        valid_record(raw_response={"status": "ok"})


@pytest.mark.parametrize("field", ["summary_th", "summary_en", "recommended_next_step"])
def test_validation_knowledge_rejects_secret_literals(field):
    with pytest.raises((ValueError, ValidationError), match="catalog_credentials_unsafe"):
        valid_record(**{field: "Bearer secret-token-value-that-must-never-publish"})


def test_validation_knowledge_rejects_credential_bearing_paths():
    with pytest.raises((ValueError, ValidationError), match="catalog_credential_path_unsafe"):
        valid_record(response_shape={"endpoint": "/v1/bearer synthetic-token"})


@pytest.mark.parametrize(
    "field, summary",
    [
        ("summary_th", "ข้อความสรุป public ที่กำหนดเอง"),
        ("summary_en", "Arbitrary public summary"),
    ],
)
def test_public_approval_requires_controlled_status_summaries(field, summary):
    with pytest.raises(ValidationError, match="approved_public_summary_not_controlled"):
        valid_record(approved_public=True, **{field: summary})


@pytest.mark.parametrize(
    "response_shape, error",
    [
        ({"email": "person@example.com"}, "approved_public_response_shape_unsafe"),
        ({"document_id": "provider-document-123"}, "approved_public_response_shape_unsafe"),
        ({"payload": {"document_id": "string"}}, "approved_public_response_shape_unsafe"),
        ({"source_path": "string"}, "approved_public_response_shape_unsafe"),
        ({"access_token": "string"}, "catalog_credentials_unsafe"),
    ],
)
def test_public_approval_rejects_arbitrary_response_shape_content(response_shape, error):
    with pytest.raises(ValidationError, match=error):
        valid_record(approved_public=True, response_shape=response_shape)


def test_public_approval_keeps_business_field_names_and_type_descriptors():
    record = valid_record(
        approved_public=True,
        response_shape={
            "data": {
                "type": "array",
                "items": {"document_id": "string", "email": "string"},
            }
        },
    )

    assert record.response_shape["data"]["items"] == {
        "document_id": "string",
        "email": "string",
    }


def test_validation_knowledge_rejects_invalid_enum_values():
    with pytest.raises(ValidationError):
        valid_record(validation_status="provider_says_maybe")


def test_validation_knowledge_rejects_invalid_latency_and_digest():
    with pytest.raises(ValidationError):
        valid_record(latency_ms=-1)
    with pytest.raises(ValidationError):
        valid_record(evidence_sha256="not-a-sha256")


def test_qualification_report_exposes_record_count():
    report = QualificationReport(
        connector_id="flowaccount",
        environment="sandbox",
        run_id="run_01j00000000000000000000000",
        run_state=QualificationRunState.COMPLETED,
        records=(valid_record(),),
    )

    assert report.total == 1
