import pytest

from mercury_tools.db.memory import InMemoryRagStore
from mercury_tools.rag.models import SearchFilters
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
