from mercury_tools.rag.models import SearchFilters
from mercury_tools.rag.routing import apply_connector_routing, infer_connector_id


def test_search_filters_map_to_rpc_payload() -> None:
    filters = SearchFilters(
        jurisdiction="TH",
        connector="flowaccount",
        doc_type="tax",
        review_status="reviewed",
        effective_date="2026-07-05",
    )

    assert filters.to_rpc_payload() == {
        "filter_jurisdiction": "TH",
        "filter_connector": "flowaccount",
        "filter_doc_type": "tax",
        "filter_review_status": "reviewed",
        "filter_effective_date": "2026-07-05",
    }


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
