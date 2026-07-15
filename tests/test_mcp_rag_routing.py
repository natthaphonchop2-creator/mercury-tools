from __future__ import annotations

import pytest

from mercury_tools.rag.models import ContextPack, SearchResult


def _result(connector: str) -> SearchResult:
    return SearchResult(
        chunk_id="chunk-1",
        document_id="document-1",
        document_uri=f"mercury://wiki/connectors/{connector}",
        chunk_uri=f"mercury://wiki/connectors/{connector}#chunk-1",
        text="Endpoint context",
        score=0.98,
        source_title=f"{connector} endpoint dictionary",
        source_uri=f"mercury://wiki/connectors/{connector}",
        source_url=None,
        source_path=f"/Users/operator/private/{connector}.md",
        citation={
            "chunk_id": "chunk-1",
            "heading": "Endpoints",
            "provider_record_id": "provider-private-value",
        },
        metadata={
            "connector": connector,
            "doc_type": "endpoint_dictionary",
            "provider_record_id": "provider-private-value",
            "raw_payload": {"email": "person@example.test"},
        },
    )


def test_search_knowledge_applies_inferred_connector_and_returns_metadata(monkeypatch) -> None:
    from mercury_tools.mcp import server

    captured = {}

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            captured.update(
                query=query,
                filters=filters,
                top_k=top_k,
                mode=mode,
            )
            return [_result("flowaccount")]

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.search_knowledge("FlowAccount invoice endpoint")

    assert captured["filters"].connector == "flowaccount"
    assert captured["filters"].doc_type == "endpoint_dictionary"
    assert payload["applied_filters"] == {
        "connector": "flowaccount",
        "doc_type": "endpoint_dictionary",
    }
    assert payload["inferred_connector"] == "flowaccount"
    assert payload["inferred_domain"] == "connector_endpoint"
    assert payload["results"][0]["metadata"] == {
        "connector": "flowaccount",
        "doc_type": "endpoint_dictionary",
    }
    assert "source_path" not in payload["results"][0]
    assert "provider-private-value" not in str(payload)


def test_retrieve_context_pack_preserves_explicit_connector_filter(monkeypatch) -> None:
    from mercury_tools.mcp import server

    captured = {}

    class FakeService:
        def context_pack(self, query, *, task, filters, max_chunks):
            captured.update(query=query, task=task, filters=filters, max_chunks=max_chunks)
            return ContextPack(query=query, task=task, results=[_result("peak")])

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.retrieve_context_pack(
        "FlowAccount invoice endpoint",
        filters={"connector": "peak", "review_status": "reviewed"},
    )

    assert captured["filters"].connector == "peak"
    assert payload["applied_filters"] == {
        "connector": "peak",
        "review_status": "reviewed",
        "doc_type": "endpoint_dictionary",
    }
    assert payload["inferred_connector"] is None
    assert payload["inferred_domain"] == "connector_endpoint"
    assert payload["context"][0]["metadata"] == {
        "connector": "peak",
        "doc_type": "endpoint_dictionary",
    }
    assert "source_path" not in payload["context"][0]
    assert "provider-private-value" not in str(payload)


def test_search_knowledge_routes_standard_without_connector_filter(monkeypatch) -> None:
    from mercury_tools.mcp import server

    captured = {}

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            captured.update(query=query, filters=filters, top_k=top_k, mode=mode)
            return []

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.search_knowledge("FlowAccount ใช้ TFRS 15 อย่างไร")

    assert captured["filters"].connector is None
    assert captured["filters"].doc_type == "accounting_standard"
    assert payload["applied_filters"] == {"doc_type": "accounting_standard"}
    assert payload["inferred_connector"] == "flowaccount"
    assert payload["inferred_domain"] == "accounting_standard"
    assert payload["status"] == "no_relevant_knowledge"
    assert payload["minimum_score"] == 0.20
    assert payload["results"] == []


def test_retrieve_context_pack_reports_no_relevant_knowledge(monkeypatch) -> None:
    from mercury_tools.mcp import server

    class FakeService:
        def context_pack(self, query, *, task, filters, max_chunks):
            return ContextPack(query=query, task=task, results=[])

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    payload = server.retrieve_context_pack("มาตรฐานที่ไม่มีใน Mercury")

    assert payload["status"] == "no_relevant_knowledge"
    assert payload["minimum_score"] == 0.20
    assert payload["context"] == []


def test_search_knowledge_routes_exact_filters_without_mutating_input(monkeypatch) -> None:
    from mercury_tools.mcp import server

    captured = {}

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            captured.update(filters=filters)
            return []

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)
    original = {
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": "revenue_review",
    }

    payload = server.search_knowledge("qualified evidence", filters=original)

    filters = captured["filters"]
    assert filters.action_id == original["action_id"]
    assert filters.version_id == original["version_id"]
    assert filters.environment == "sandbox"
    assert filters.capability == "documents.invoice.list"
    assert filters.accounting_use == "revenue_review"
    assert payload["applied_filters"] == original
    assert original == {
        "action_id": "act_1234567890abcdef12345678",
        "version_id": "av_" + "1" * 64,
        "environment": "sandbox",
        "capability": "documents.invoice.list",
        "accounting_use": "revenue_review",
    }


def test_search_knowledge_rejects_unknown_filter_without_echo(monkeypatch) -> None:
    from mercury_tools.mcp import server

    calls = []

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            calls.append(filters)
            return []

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    unsafe_value = "private-value-must-not-echo"

    with pytest.raises(ValueError, match="^search_filters_invalid$") as raised:
        server.search_knowledge(
            "qualified evidence",
            filters={"raw_response": unsafe_value},
        )

    assert calls == []
    assert unsafe_value not in str(raised.value)


def test_search_knowledge_rejects_partial_validation_metadata_without_echo(
    monkeypatch,
) -> None:
    from mercury_tools.mcp import server

    unsafe_value = "provider-private-value"
    validation_uri = "mercury://wiki/validation/flowaccount/action/version/run"

    class FakeService:
        def search(self, query, *, filters, top_k, mode):
            return [
                SearchResult(
                    chunk_id="chunk-validation",
                    document_id="document-validation",
                    document_uri=validation_uri,
                    chunk_uri=f"{validation_uri}#chunk-0",
                    text="Unapproved validation",
                    score=1.0,
                    source_title="Validation",
                    source_uri=validation_uri,
                    source_url=None,
                    source_path=None,
                    citation={},
                    metadata={
                        "review_status": "reviewed",
                        "provider_record_id": unsafe_value,
                    },
                )
            ]

    monkeypatch.setattr(server, "_service", lambda: FakeService())
    monkeypatch.setattr(server, "_audit", lambda *args, **kwargs: None)

    with pytest.raises(
        ValueError,
        match="^public_knowledge_metadata_invalid$",
    ) as raised:
        server.search_knowledge("qualified evidence")

    assert unsafe_value not in str(raised.value)
