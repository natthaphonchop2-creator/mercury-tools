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


def semantic_contract(**overrides):
    values = {
        "business_object": "invoice",
        "operation": "list",
        "accounting_uses": ("revenue_review",),
    }
    values.update(overrides)
    return SemanticContract.model_validate(values)


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


@pytest.mark.parametrize(
    "field_name",
    [
        "schema@example.invalid",
        "schema@internal",
        "991234567",
        "provider_991234567",
        "providerRecordId",
        "provider_record_id",
        "provider_recordid",
        "providerDocumentId",
        "tax_id_0123456789012",
        "fixture/records/document",
        "authHeader",
        "auth_header",
        "authheader",
        "localPath",
        "local_path",
        "rawPayloadItems",
        "raw_payload_items",
        "payloadItems",
        "payload_items",
        "sourceDocument",
        "source_document",
        "credentialHint",
        "credential_hint",
    ],
)
def test_public_approval_rejects_data_embedded_in_response_shape_field_names(field_name):
    with pytest.raises(ValidationError, match="approved_public_response_shape_unsafe"):
        valid_record(approved_public=True, response_shape={field_name: "string"})


def test_public_approval_bounds_response_shape_field_names():
    with pytest.raises(ValidationError, match="approved_public_response_shape_unsafe"):
        valid_record(approved_public=True, response_shape={"a" * 65: "string"})


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
    serialized = record.model_dump_json()
    assert record.model_dump_json() == serialized
    assert ValidationKnowledge.model_validate_json(serialized).model_dump_json() == serialized


def test_equivalent_mapping_orders_serialize_to_identical_json():
    first = valid_record(
        approved_public=True,
        response_shape={
            "zeta": "string",
            "alpha": {"zulu": "integer", "bravo": "boolean"},
        },
        semantic_contract=semantic_contract(
            output_semantics={"zeta": "last field", "alpha": "first field"}
        ),
    )
    second = valid_record(
        approved_public=True,
        response_shape={
            "alpha": {"bravo": "boolean", "zulu": "integer"},
            "zeta": "string",
        },
        semantic_contract=semantic_contract(
            output_semantics={"alpha": "first field", "zeta": "last field"}
        ),
    )

    assert list(first.response_shape) == ["alpha", "zeta"]
    assert list(first.response_shape["alpha"]) == ["bravo", "zulu"]
    assert list(first.semantic_contract.output_semantics) == ["alpha", "zeta"]
    assert first.model_dump_json() == second.model_dump_json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"prerequisites": ("contact schema@example.invalid",)},
        {"limitations": ("inspect file:///fixture.invalid/record",)},
        {"recommended_next_step": "x" * 513},
        {"status_class": "unsafe\x00status"},
        {"reviewed_by": "reviewer@example.invalid"},
        {"reviewed_by": "reviewer@internal"},
        {"limitations": ("provider document 9912345678901",)},
        {"limitations": ("tax reference 0-1234-56789-01-2",)},
        {"prerequisites": ("raw_payload_label",)},
        {"status_class": "source_record_label"},
        {"recommended_next_step": "https://fixture.invalid/next"},
        {"recommended_next_step": "//fixture.invalid/next"},
        {"limitations": ("inspect /opt/mercury-fixture/provider-record",)},
    ],
)
def test_public_approval_rejects_unsafe_non_summary_content(overrides):
    with pytest.raises(ValidationError, match="approved_public_content_unsafe"):
        valid_record(approved_public=True, **overrides)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "client_credential opaque_fixture_material",
        "../private/provider_response",
        "file:/private/provider_response",
        "prefixA1234567890123Zsuffix",
        "tax reference 0\u20131234\u201356789\u201301\u20132",
        "tax reference 0\u22121234\u221256789\u221201\u22122",
    ],
)
def test_public_approval_rejects_third_review_string_bypasses_without_echo(
    unsafe_value,
):
    with pytest.raises(ValidationError, match="approved_public_content_unsafe") as raised:
        valid_record(approved_public=True, limitations=(unsafe_value,))

    assert unsafe_value not in str(raised.value)


@pytest.mark.parametrize(
    "unsafe_value",
    [
        "reference 12\u00b7345\u00b7678\u00b79",
        "reference 12/345/678/9",
        "reference 12\uff0f345\uff0f678\uff0f9",
        "reference 1_2(3)[4]{5}!6?7\u00b78/9",
        "private/provider_response",
        "private\\provider_response",
        "C:private\\provider_response",
        "inspect private/provider_response",
        "inspect private\\provider_response",
        "inspect C:private\\provider_response",
    ],
)
def test_public_approval_rejects_fourth_review_string_bypasses_without_echo(
    unsafe_value,
):
    with pytest.raises(ValidationError, match="approved_public_content_unsafe") as raised:
        valid_record(approved_public=True, limitations=(unsafe_value,))

    assert unsafe_value not in str(raised.value)


def test_public_approval_avoids_fourth_review_false_positives():
    record = valid_record(
        approved_public=True,
        opaque_evidence_id="ev_" + "1" * 26,
        run_id="run_" + "2" * 26,
        action_id="act_" + "3" * 24,
        version_id="av_" + "4" * 64,
        evidence_sha256="5" * 64,
        prerequisites=(
            "review phase 1/2 when ready",
            "compare input/output totals",
        ),
        limitations=(
            "provider_call_not_observed",
            "reference 12\u00b734\u00b756\u00b778",
            "C: status complete",
        ),
        semantic_contract=semantic_contract(
            required_external_capabilities=("google_sheets.values.read",),
            optional_external_capabilities=("workspace.sheets.read",),
            next_action_ids=("act_" + "6" * 24,),
        ),
    )

    assert record.prerequisites == (
        "review phase 1/2 when ready",
        "compare input/output totals",
    )
    assert record.limitations[0] == "provider_call_not_observed"
    assert record.semantic_contract.required_external_capabilities == (
        "google_sheets.values.read",
    )


@pytest.mark.parametrize("container", ["{}", "inspect {}"])
@pytest.mark.parametrize(
    "path_token",
    [
        "private/provider",
        "private/provider-data",
        "private/2026",
        "private\\provider",
        "private\\provider-data",
        "private\\2026",
        "C:private\\provider",
        "C:private\\provider-data",
        "C:private\\2026",
        "input\\output",
        "debit\\credit",
        "1\\2",
    ],
)
def test_public_approval_rejects_fifth_review_path_tokens_without_echo(
    container,
    path_token,
):
    unsafe_value = container.format(path_token)

    with pytest.raises(ValidationError, match="approved_public_content_unsafe") as raised:
        valid_record(approved_public=True, limitations=(unsafe_value,))

    assert unsafe_value not in str(raised.value)


@pytest.mark.parametrize(
    "allowed_value",
    [
        "input/output",
        "compare input/output totals",
        "Input/Output",
        "debit/credit",
        "review debit/credit mapping",
        "1/2",
        "review phase 1/2 when ready",
        "2026/07/13",
        "review period 2026/07/13",
        "review (input/output).",
        "review (debit/credit).",
        "review (1/2).",
        "period (2026/07/13).",
        "review [input/output]:.",
    ],
)
def test_public_approval_allows_controlled_slash_terms_and_numeric_ratios(
    allowed_value,
):
    record = valid_record(approved_public=True, limitations=(allowed_value,))

    assert record.limitations == (allowed_value,)


def test_public_approval_bounds_non_shape_collections():
    with pytest.raises(ValidationError, match="approved_public_content_unsafe"):
        valid_record(
            approved_public=True,
            prerequisites=tuple(f"requirement_{index}" for index in range(129)),
        )


@pytest.mark.parametrize(
    "contract",
    [
        semantic_contract(business_object="schema@example.invalid"),
        semantic_contract(operation="inspect file:///fixture.invalid/record"),
        semantic_contract(accounting_uses=("schema@example.invalid",)),
        semantic_contract(output_semantics={"schema@example.invalid": "document number"}),
        semantic_contract(output_semantics={"document_id": "schema@example.invalid"}),
        semantic_contract(join_keys=("schema@example.invalid",)),
        semantic_contract(next_action_ids=("schema@example.invalid",)),
        semantic_contract(required_external_capabilities=("schema@example.invalid",)),
        semantic_contract(optional_external_capabilities=("schema@example.invalid",)),
        semantic_contract(fallbacks=("schema@example.invalid",)),
        semantic_contract(accounting_uses=("team@internal",)),
        semantic_contract(join_keys=("provider document 9912345678901",)),
        semantic_contract(fallbacks=("raw_payload_label",)),
        semantic_contract(required_external_capabilities=("source_record_label",)),
        semantic_contract(
            output_semantics={"document_id": "https://fixture.invalid/record"}
        ),
        semantic_contract(operation="inspect /opt/mercury-fixture/record"),
    ],
)
def test_public_approval_rejects_unsafe_nested_semantic_contract_strings(contract):
    with pytest.raises(ValidationError, match="approved_public_content_unsafe"):
        valid_record(approved_public=True, semantic_contract=contract)


def test_qualification_models_deep_freeze_nested_content_after_validation():
    source_shape = {
        "data": {
            "items": [
                {
                    "labels": {"alpha", "beta"},
                }
            ]
        }
    }
    source_semantics = {"document_id": "provider document number"}
    record = valid_record(
        response_shape=source_shape,
        semantic_contract=semantic_contract(output_semantics=source_semantics),
    )

    source_shape["data"]["items"][0]["labels"].add("gamma")
    source_semantics["document_id"] = "changed"

    assert record.response_shape["data"]["items"][0]["labels"] == frozenset(
        {"alpha", "beta"}
    )
    assert record.semantic_contract.output_semantics["document_id"] == (
        "provider document number"
    )
    with pytest.raises(TypeError, match="immutable_mapping"):
        record.response_shape["data"]["new"] = "string"
    with pytest.raises(AttributeError):
        record.response_shape["data"]["items"].append("string")
    with pytest.raises(AttributeError):
        record.response_shape["data"]["items"][0]["labels"].add("gamma")
    with pytest.raises(TypeError, match="immutable_mapping"):
        record.semantic_contract.output_semantics["document_id"] = "changed"


def test_public_approval_keeps_internal_ids_and_controlled_semantic_descriptions():
    record = valid_record(
        approved_public=True,
        opaque_evidence_id="ev_" + "1" * 26,
        run_id="run_" + "2" * 26,
        action_id="act_" + "3" * 24,
        version_id="av_" + "4" * 64,
        limitations=("provider_call_not_observed",),
        response_shape={"document_id": "string", "email": "string"},
        semantic_contract=semantic_contract(
            business_object="provider_document",
            output_semantics={"document_id": "provider document number"},
            next_action_ids=("act_" + "5" * 24,),
        ),
        evidence_sha256="6" * 64,
    )

    assert record.limitations == ("provider_call_not_observed",)
    assert record.semantic_contract.output_semantics["document_id"] == (
        "provider document number"
    )


def test_public_approval_allows_strict_dotted_external_capabilities():
    capabilities = (
        "google_sheets.values.read",
        "google.drive.files.read",
        "workspace.sheets.read",
    )
    record = valid_record(
        approved_public=True,
        semantic_contract=semantic_contract(
            required_external_capabilities=capabilities[:2],
            optional_external_capabilities=capabilities[2:],
        ),
    )

    assert record.semantic_contract.required_external_capabilities == capabilities[:2]
    assert record.semantic_contract.optional_external_capabilities == capabilities[2:]


@pytest.mark.parametrize(
    "capability",
    [
        "workspace_sheets_read",
        "google_sheets.values",
        "google_sheets..values.read",
        "Google_sheets.values.read",
        "google_sheets.values.read/extra",
        "providera123456789z.values.read",
        "https://fixture.invalid/capability",
    ],
)
def test_public_approval_rejects_malformed_external_capabilities(capability):
    with pytest.raises(ValidationError, match="approved_public_content_unsafe"):
        valid_record(
            approved_public=True,
            semantic_contract=semantic_contract(
                required_external_capabilities=(capability,)
            ),
        )


def test_dotted_capability_is_rejected_outside_external_capability_fields():
    with pytest.raises(ValidationError, match="approved_public_content_unsafe"):
        valid_record(
            approved_public=True,
            semantic_contract=semantic_contract(operation="workspace.sheets.read"),
        )


def test_model_copy_revalidates_public_updates_without_echoing_content():
    internal = valid_record(summary_en="Internal qualification wording")
    with pytest.raises(ValidationError, match="approved_public_summary_not_controlled"):
        internal.model_copy(update={"approved_public": True})

    unsafe_value = "client_credential opaque_copy_material"
    public = valid_record(approved_public=True)
    with pytest.raises(ValidationError, match="approved_public_content_unsafe") as raised:
        public.model_copy(update={"limitations": (unsafe_value,)})
    assert unsafe_value not in str(raised.value)

    with pytest.raises(ValidationError):
        public.model_copy(update={"raw_response": {"status": "string"}})


def test_model_copy_deep_freezes_mutable_update_aliases():
    mutable_shape = {"outer": {"items": ["string"]}}
    copied = valid_record().model_copy(update={"response_shape": mutable_shape})

    mutable_shape["outer"]["items"].append("integer")

    assert copied.response_shape == {"outer": {"items": ("string",)}}
    with pytest.raises(TypeError, match="immutable_mapping"):
        copied.response_shape["outer"]["new"] = "string"
    with pytest.raises(AttributeError):
        copied.response_shape["outer"]["items"].append("integer")


def test_public_model_copy_detaches_and_freezes_nested_mapping_alias():
    mutable_shape = {"data": {"email": "string", "document_id": "string"}}
    copied = valid_record(approved_public=True).model_copy(
        update={"response_shape": mutable_shape}
    )

    mutable_shape["data"]["email"] = "integer"

    assert copied.response_shape["data"]["email"] == "string"
    with pytest.raises(TypeError, match="immutable_mapping"):
        copied.response_shape["data"]["email"] = "integer"


def test_safe_model_copy_preserves_value_and_fields_set_for_every_copy_mode():
    contract = SemanticContract(business_object="invoice", operation="list")
    mutable_semantics = {"zeta": "last field", "alpha": "first field"}

    shallow = contract.model_copy()
    deep = contract.model_copy(deep=True)
    updated = contract.model_copy(
        update={
            "accounting_uses": ("revenue_review",),
            "output_semantics": mutable_semantics,
        }
    )
    mutable_semantics["alpha"] = "changed"

    assert shallow == contract
    assert deep == contract
    assert shallow is not contract
    assert deep is not contract
    assert shallow.model_fields_set == contract.model_fields_set
    assert deep.model_fields_set == contract.model_fields_set
    assert updated.accounting_uses == ("revenue_review",)
    assert list(updated.output_semantics) == ["alpha", "zeta"]
    assert updated.output_semantics["alpha"] == "first field"
    assert updated.model_fields_set == contract.model_fields_set | {
        "accounting_uses",
        "output_semantics",
    }
    with pytest.raises(TypeError, match="immutable_mapping"):
        updated.output_semantics["alpha"] = "changed"


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
