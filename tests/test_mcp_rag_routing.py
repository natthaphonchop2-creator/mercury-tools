from __future__ import annotations

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
        source_path=f"connectors/{connector}.md",
        citation={"chunk_id": "chunk-1", "heading": "Endpoints"},
        metadata={"connector": connector, "doc_type": "endpoint_dictionary"},
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
    assert payload["applied_filters"] == {"connector": "flowaccount"}
    assert payload["inferred_connector"] == "flowaccount"
    assert payload["results"][0]["metadata"] == {
        "connector": "flowaccount",
        "doc_type": "endpoint_dictionary",
    }


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
    }
    assert payload["inferred_connector"] is None
    assert payload["context"][0]["metadata"]["connector"] == "peak"
