from __future__ import annotations

import json
import sys
from dataclasses import asdict, replace
from datetime import UTC, datetime
from importlib import util as importlib_util
from pathlib import Path

import pytest

from mercury_tools.db.memory import InMemoryRagStore
from mercury_tools.qualification.models import (
    EvidenceLevel,
    ExecutionEligibility,
    QualificationRunState,
    SemanticContract,
    ValidationKnowledge,
    ValidationStatus,
)
from mercury_tools.qualification.publisher import (
    CatalogDefinitions,
    ReviewedValidationReport,
    validation_documents,
)
from mercury_tools.qualification.templates import SUMMARY_EN, SUMMARY_TH
from mercury_tools.rag.chunking import chunk_document

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    spec = importlib_util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib_util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _record(catalog_action, *, approved_public: bool) -> ValidationKnowledge:
    status = ValidationStatus.CONTRACT_VALIDATED
    return ValidationKnowledge.model_validate(
        {
            "opaque_evidence_id": "ev_" + "1" * 26,
            "run_id": "run_" + "2" * 26,
            "action_id": catalog_action.action_id,
            "version_id": catalog_action.version_id,
            "connector_id": catalog_action.connector_id,
            "environment": "sandbox",
            "validation_status": status,
            "evidence_level": EvidenceLevel.CONTRACT_VALIDATED,
            "execution_eligibility": ExecutionEligibility.DISCOVERY_ONLY,
            "approved_public": approved_public,
            "summary_th": SUMMARY_TH[status],
            "summary_en": SUMMARY_EN[status],
            "prerequisites": ("review_fixture",),
            "limitations": ("limit_marker",),
            "recommended_next_step": "next_marker",
            "response_shape": {},
            "status_class": "status_marker",
            "latency_ms": None,
            "semantic_contract": SemanticContract(
                business_object="invoice",
                operation="list",
                accounting_uses=("revenue_review",),
            ),
            "evidence_sha256": "3" * 64,
            "reviewed_by": "release_reviewer",
            "runner_version": "runner_marker",
            "run_state": QualificationRunState.COMPLETED,
            "evaluated_at": datetime(2026, 7, 13, tzinfo=UTC),
            "expires_at": None,
        }
    )


def test_validation_document_contains_only_allowlisted_reviewed_public_fields(
    catalog_action,
) -> None:
    record = _record(catalog_action, approved_public=True)

    [document] = validation_documents(
        [record],
        catalog=CatalogDefinitions([catalog_action]),
    )

    assert document.document_uri.startswith("mercury://wiki/validation/")
    assert document.review_status == "reviewed"
    assert document.metadata == {
        "jurisdiction": "TH",
        "connector": record.connector_id,
        "doc_type": "endpoint_validation",
        "review_status": "reviewed",
        "action_id": record.action_id,
        "version_id": record.version_id,
        "environment": record.environment,
        "capability": catalog_action.capability,
        "accounting_use": ["revenue_review"],
        "validation_status": "contract_validated",
        "evidence_level": "contract_validated",
        "approval_state": "approved_public",
    }
    serialized = json.dumps(asdict(document), default=str, sort_keys=True)
    assert record.action_id in serialized
    assert record.opaque_evidence_id in serialized
    assert catalog_action.capability in serialized
    for forbidden in (
        "/Users/",
        "recordId",
        "raw_response",
        "client_secret",
        "runner_marker",
        "status_marker",
        "next_marker",
        "limit_marker",
    ):
        assert forbidden not in serialized


def test_unapproved_evidence_is_not_projected_without_catalog_lookup(catalog_action) -> None:
    record = _record(catalog_action, approved_public=False)

    assert validation_documents([record], catalog=CatalogDefinitions(())) == ()


def test_validation_projection_requires_exact_catalog_action_version(catalog_action) -> None:
    record = _record(catalog_action, approved_public=True)

    with pytest.raises(ValueError, match="^validation_catalog_scope_mismatch$"):
        validation_documents([record], catalog=CatalogDefinitions(()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reviewed_by", "untrusted_reviewer"),
        ("run_state", QualificationRunState.FAILED),
    ],
)
def test_validation_projection_requires_reviewed_completed_record(
    catalog_action,
    field: str,
    value: object,
) -> None:
    record = _record(catalog_action, approved_public=True).model_copy(
        update={field: value}
    )

    with pytest.raises(ValueError, match="^validation_evidence_not_reviewed$"):
        validation_documents(
            [record],
            catalog=CatalogDefinitions([catalog_action]),
        )


def test_validation_projection_rejects_noncanonical_approved_metadata(
    catalog_action,
) -> None:
    record = _record(catalog_action, approved_public=True).model_copy(
        update={"environment": "staging"}
    )

    with pytest.raises(ValueError, match="^validation_evidence_invalid$"):
        validation_documents(
            [record],
            catalog=CatalogDefinitions([catalog_action]),
        )


def test_validation_chunk_propagates_exact_allowlisted_metadata(catalog_action) -> None:
    record = _record(catalog_action, approved_public=True)
    [document] = validation_documents(
        [record],
        catalog=CatalogDefinitions([catalog_action]),
    )

    [chunk] = chunk_document(document)

    assert chunk.metadata == document.metadata

    store = InMemoryRagStore()
    store.upsert_document_with_chunks(document, [chunk], [[0.0, 1.0]])
    assert store.documents[document.document_uri]["metadata"] == document.metadata


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("approval_state", "pending_review"),
        ("environment", "staging"),
    ],
)
def test_validation_chunk_revalidates_allowlisted_metadata_values(
    catalog_action,
    field: str,
    value: str,
) -> None:
    record = _record(catalog_action, approved_public=True)
    [document] = validation_documents(
        [record],
        catalog=CatalogDefinitions([catalog_action]),
    )
    malformed = replace(
        document,
        metadata={**document.metadata, field: value},
    )

    with pytest.raises(ValueError, match="^validation_document_metadata_invalid$"):
        chunk_document(malformed)


def test_publish_script_publishes_approved_ledger_and_rag_with_injected_stores(
    catalog_action,
) -> None:
    from mercury_tools.rag.embeddings import HashEmbeddingProvider

    script = _load_script("publish_validation_knowledge")
    record = _record(catalog_action, approved_public=True)
    reviewed = ReviewedValidationReport(
        records=(record,),
        reviewer_role="release_reviewer",
    )
    validation_calls = []

    class ValidationStore:
        def publish(self, records):
            validation_calls.append(tuple(records))
            return len(records)

    rag_store = InMemoryRagStore()
    stats = script.publish_reviewed_report(
        reviewed,
        catalog=CatalogDefinitions([catalog_action]),
        validation_store=ValidationStore(),
        rag_store=rag_store,
        embedder=HashEmbeddingProvider(),
        require_approved=True,
        ingest_rag=True,
    )

    assert validation_calls == [(record,)]
    assert stats.validation_rows_inserted == 1
    assert stats.rag_documents_inserted_or_updated == 1
    assert stats.rag_chunks == 1
    [stored] = rag_store.documents.values()
    assert stored["metadata"]["approval_state"] == "approved_public"


def test_publish_script_require_approved_rejects_unapproved_records(catalog_action) -> None:
    script = _load_script("publish_validation_knowledge")
    reviewed = ReviewedValidationReport(
        records=(_record(catalog_action, approved_public=False),),
        reviewer_role="release_reviewer",
    )

    with pytest.raises(ValueError, match="^validation_records_not_approved$"):
        script.publish_reviewed_report(
            reviewed,
            catalog=CatalogDefinitions([catalog_action]),
            validation_store=object(),
            rag_store=None,
            embedder=None,
            require_approved=True,
            ingest_rag=False,
        )
