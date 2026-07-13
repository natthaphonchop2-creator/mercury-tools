import pytest

from mercury_tools.db.memory import InMemoryRagStore
from mercury_tools.rag.models import (
    KnowledgeDocument,
    SearchFilters,
    SearchResult,
    public_search_result_payload,
)
from mercury_tools.rag.routing import (
    apply_connector_routing,
    apply_knowledge_routing,
    infer_connector_id,
)


def test_search_filters_map_to_rpc_payload() -> None:
    filters = SearchFilters(
        jurisdiction="TH",
        connector="flowaccount",
        doc_type="tax",
        review_status="reviewed",
        effective_date="2026-07-05",
        action_id="act_1234567890abcdef12345678",
        version_id="av_" + "1" * 64,
        environment="sandbox",
        capability="documents.invoice.list",
        accounting_use="revenue_review",
    )

    assert filters.to_rpc_payload() == {
        "filter_jurisdiction": "TH",
        "filter_connector": "flowaccount",
        "filter_doc_type": "tax",
        "filter_review_status": "reviewed",
        "filter_effective_date": "2026-07-05",
        "filter_action_id": "act_1234567890abcdef12345678",
        "filter_version_id": "av_" + "1" * 64,
        "filter_environment": "sandbox",
        "filter_capability": "documents.invoice.list",
        "filter_accounting_use": "revenue_review",
    }


def _public_result(
    *,
    document_uri: str = "mercury://wiki/tax/vat",
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        chunk_id="chunk-1",
        document_id="document-1",
        document_uri=document_uri,
        chunk_uri=f"{document_uri}#chunk-0",
        text="VAT contact person@example.test tax id 0105559999999",
        score=0.9,
        source_title="VAT",
        source_uri=document_uri,
        source_url="https://example.test/vat",
        source_path="/Users/operator/private/vat.md",
        citation={
            "heading": "VAT",
            "source_path": "/Users/operator/private/vat.md",
            "provider_record_id": "provider-private-value",
        },
        metadata=metadata or {"review_status": "reviewed"},
    )


def _approved_validation_metadata(**overrides) -> dict:
    metadata = {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "endpoint_validation",
        "review_status": "reviewed",
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": ["revenue_review"],
        "validation_status": "contract_validated",
        "evidence_level": "contract_validated",
        "approval_state": "approved_public",
    }
    metadata.update(overrides)
    return metadata


def test_public_result_preserves_only_safe_legacy_general_metadata() -> None:
    metadata = {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "tax",
        "review_status": "reviewed",
        "effective_date": "2026-07-13",
        "action_id": "act_1234567890abcdef12345678",
        "provider_record_id": "provider-private-value",
        "raw_payload": {"tax_id": "0105559999999"},
        "source_path": "/Users/operator/private/vat.md",
        "source_url": "https://provider.example.test/private",
        "email": "person@example.test",
    }

    payload = public_search_result_payload(_public_result(metadata=metadata))

    assert payload["metadata"] == {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "tax",
        "review_status": "reviewed",
        "effective_date": "2026-07-13",
        "action_id": "act_1234567890abcdef12345678",
    }
    assert payload["citation"] == {"heading": "VAT"}
    assert "source_path" not in payload
    serialized = str(payload)
    for forbidden in (
        "provider-private-value",
        "/Users/operator",
        "person@example.test",
        "0105559999999",
        "raw_payload",
    ):
        assert forbidden not in serialized


def test_public_result_requires_complete_validation_metadata_without_echo() -> None:
    unsafe_value = "provider-private-value"
    result = _public_result(
        document_uri="mercury://wiki/validation/flowaccount/action/version/run",
        metadata={
            "review_status": "reviewed",
            "provider_record_id": unsafe_value,
        },
    )

    with pytest.raises(
        ValueError,
        match="^public_knowledge_metadata_invalid$",
    ) as raised:
        public_search_result_payload(result)

    assert unsafe_value not in str(raised.value)


def test_public_validation_result_drops_paths_urls_and_extra_metadata() -> None:
    metadata = _approved_validation_metadata(raw_payload="private-value")
    result = _public_result(
        document_uri="mercury://wiki/validation/flowaccount/action/version/run",
        metadata=metadata,
    )

    payload = public_search_result_payload(result, include_document_id=True)

    assert payload["metadata"] == _approved_validation_metadata()
    assert payload["document_id"] == "document-1"
    assert "source_path" not in payload
    assert "source_url" not in payload
    serialized = str(payload)
    assert "private-value" not in serialized
    assert "https://" not in serialized


def test_public_validation_metadata_rejects_outcome_unknown() -> None:
    result = _public_result(
        document_uri="mercury://wiki/validation/flowaccount/action/version/run",
        metadata=_approved_validation_metadata(validation_status="outcome_unknown"),
    )

    with pytest.raises(ValueError, match="^public_knowledge_metadata_invalid$"):
        public_search_result_payload(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("action_id", "act_wrong"),
        ("version_id", "av_wrong"),
        ("environment", "production@example.test"),
        ("capability", "Bearer private-value"),
        ("accounting_use", "/Users/operator/private"),
    ],
)
def test_search_filters_reject_noncanonical_exact_values_without_echo(
    field: str,
    value: str,
) -> None:
    with pytest.raises(ValueError, match="^search_filters_invalid$") as raised:
        SearchFilters(**{field: value})

    assert value not in str(raised.value)


def test_memory_store_applies_every_exact_validation_filter() -> None:
    store = InMemoryRagStore()
    common = {
        "document_id": "document-1",
        "document_uri": "mercury://wiki/validation/flowaccount/action/version/run",
        "chunk_text": "qualified endpoint evidence",
        "source_title": "Validation",
        "source_uri": "mercury://wiki/validation/flowaccount/action/version/run",
        "source_url": None,
        "source_path": None,
        "citation": {},
        "embedding": [],
    }
    exact_metadata = {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "endpoint_validation",
        "review_status": "reviewed",
        "effective_date": "2026-07-01",
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": ["revenue_review", "vat_output_review"],
    }
    store.chunks = [
        {
            **common,
            "id": "chunk-match",
            "chunk_uri": f"{common['document_uri']}#chunk-0",
            "metadata": exact_metadata,
        },
        {
            **common,
            "id": "chunk-other",
            "chunk_uri": f"{common['document_uri']}#chunk-1",
            "metadata": {**exact_metadata, "accounting_use": ["expense_review"]},
        },
    ]

    results = store.search_knowledge(
        query="qualified",
        query_embedding=None,
        filters=SearchFilters(
            jurisdiction="TH",
            connector="flowaccount",
            doc_type="endpoint_validation",
            review_status="reviewed",
            effective_date="2026-07-13",
            action_id="act_1234567890abcdef12345678",
            version_id="av_" + "1" * 64,
            environment="sandbox",
            capability="documents.invoice.list",
            accounting_use="revenue_review",
        ),
        top_k=8,
        mode="keyword",
    )

    assert [result.chunk_id for result in results] == ["chunk-match"]


@pytest.mark.parametrize(
    ("metadata_value", "matches"),
    [
        ("revenue_review", True),
        (["revenue_review"], True),
        (["vat_output_review", "revenue_review"], True),
        (["revenue_review", "revenue_review"], True),
        (("revenue_review",), True),
        ("revenue_review_extra", False),
        (["revenue_review_extra"], False),
        ([], False),
        ({"revenue_review": True}, False),
        (123, False),
        (1.5, False),
        (True, False),
        (None, False),
        (["revenue_review", 123], False),
        ([123, "revenue_review"], False),
        (["revenue_review", None], False),
        ([["revenue_review"]], False),
    ],
)
def test_memory_accounting_use_matches_strict_supabase_json_shape_matrix(
    metadata_value: object,
    matches: bool,
) -> None:
    store = InMemoryRagStore()
    store.chunks = [
        {
            "id": "chunk-1",
            "document_id": "document-1",
            "document_uri": "mercury://wiki/accounting-use",
            "chunk_uri": "mercury://wiki/accounting-use#chunk-0",
            "chunk_text": "revenue evidence",
            "source_title": "Accounting use",
            "source_uri": "mercury://wiki/accounting-use",
            "source_url": None,
            "source_path": None,
            "citation": {},
            "metadata": {"accounting_use": metadata_value},
            "embedding": [],
        }
    ]

    results = store.search_knowledge(
        query="revenue",
        query_embedding=None,
        filters=SearchFilters(accounting_use="revenue_review"),
        top_k=8,
        mode="keyword",
    )

    assert bool(results) is matches


def test_memory_document_preserves_only_safe_public_general_metadata() -> None:
    store = InMemoryRagStore()
    document = KnowledgeDocument(
        document_uri="mercury://wiki/tax/vat",
        title="VAT",
        body="reviewed VAT guidance",
        sha256="a" * 64,
        source_uri="mercury://wiki/tax/vat",
        source_title="VAT",
        jurisdiction="TH",
        connector="flowaccount",
        doc_type="tax",
        review_status="reviewed",
        effective_date="2026-07-13",
        metadata={
            "jurisdiction": "TH",
            "connector": "flowaccount",
            "doc_type": "tax",
            "review_status": "reviewed",
            "effective_date": "2026-07-13",
            "raw_payload": {"tax_id": "0105559999999"},
            "source_path": "/Users/operator/private/vat.md",
        },
    )

    store.upsert_document_with_chunks(document, [], [])

    assert store.documents[document.document_uri]["metadata"] == {
        "jurisdiction": "TH",
        "connector": "flowaccount",
        "doc_type": "tax",
        "review_status": "reviewed",
        "effective_date": "2026-07-13",
    }


def test_memory_document_rejects_incomplete_validation_metadata() -> None:
    store = InMemoryRagStore()
    document = KnowledgeDocument(
        document_uri="mercury://wiki/validation/flowaccount/action/version/run",
        title="Validation",
        body="validation evidence",
        sha256="a" * 64,
        source_uri="mercury://wiki/validation/flowaccount/action/version/run",
        source_title="Validation",
        doc_type="endpoint_validation",
        review_status="reviewed",
        metadata={"review_status": "reviewed"},
    )

    with pytest.raises(ValueError, match="^public_knowledge_metadata_invalid$"):
        store.upsert_document_with_chunks(document, [], [])


def test_knowledge_routing_preserves_exact_filters_and_input_mapping() -> None:
    original = {
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": "revenue_review",
    }

    applied, inferred_connector, inferred_domain = apply_knowledge_routing(
        "qualified evidence",
        original,
    )

    assert applied == original
    assert applied is not original
    assert inferred_connector is None
    assert inferred_domain == "general"
    assert original["accounting_use"] == "revenue_review"


def test_connector_inference_is_explicit_and_unambiguous() -> None:
    assert infer_connector_id("FlowAccount invoice endpoint") == "flowaccount"
    assert infer_connector_id("ดึงใบแจ้งหนี้จาก PEAK") == "peak"
    assert infer_connector_id("Express Account API") == "express"
    assert infer_connector_id("invoice endpoint") is None
    assert infer_connector_id("Compare FlowAccount with PEAK") is None


def test_explicit_filter_wins_over_query_inference() -> None:
    filters, inferred = apply_connector_routing(
        "FlowAccount invoice endpoint",
        {"connector": "peak", "review_status": "reviewed"},
    )

    assert filters == {"connector": "peak", "review_status": "reviewed"}
    assert inferred is None


def test_inferred_connector_is_added_without_mutating_input() -> None:
    original = {"review_status": "reviewed"}

    filters, inferred = apply_connector_routing("PEAK Accounting API", original)

    assert filters == {"connector": "peak", "review_status": "reviewed"}
    assert inferred == "peak"
    assert original == {"review_status": "reviewed"}
