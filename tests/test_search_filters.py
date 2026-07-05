from mercury_tools.rag.models import SearchFilters


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

