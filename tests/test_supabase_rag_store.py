from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mercury_tools.db.supabase import CHUNK_UPLOAD_BATCH_SIZE, SupabaseRagStore
from mercury_tools.rag.models import KnowledgeChunk, KnowledgeDocument, SearchFilters

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260713101000_validation_knowledge_rag_filters.sql"


def _document() -> KnowledgeDocument:
    return KnowledgeDocument(
        document_uri="mercury://wiki/test",
        title="Test",
        body="body",
        sha256="a" * 64,
        source_uri="mercury://wiki/test",
        source_title="Test",
        path=Path("wiki/test.md"),
    )


def _chunks(count: int) -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            document_uri="mercury://wiki/test",
            chunk_uri=f"mercury://wiki/test#chunk-{index}",
            chunk_index=index,
            text=f"chunk {index}",
            source_title="Test",
            source_uri="mercury://wiki/test",
            source_url=None,
            source_path="wiki/test.md",
            heading=None,
            citation={"source_uri": "mercury://wiki/test"},
        )
        for index in range(count)
    ]


def test_chunk_uploads_are_bounded_and_document_sha_commits_last(monkeypatch) -> None:
    store = object.__new__(SupabaseRagStore)
    calls: list[tuple[str, object]] = []
    document = _document()
    chunk_count = CHUNK_UPLOAD_BATCH_SIZE * 2 + 3

    monkeypatch.setattr(store, "_upsert_source", lambda _: {"id": "source-1"})
    monkeypatch.setattr(
        store,
        "get_document_by_uri",
        lambda _: {"id": "document-1", "sha256": "previous"},
    )

    def upsert_document(*args, **kwargs):
        calls.append(("document", kwargs.get("sha256")))
        return {"id": "document-1"}

    def request(method, path, **kwargs):
        if path == "knowledge_chunks" and method == "POST":
            calls.append(("chunks", len(kwargs["json"])))
        return None

    monkeypatch.setattr(store, "_upsert_document", upsert_document)
    monkeypatch.setattr(store, "_request", request)

    store.upsert_document_with_chunks(
        document,
        _chunks(chunk_count),
        [[0.0, 1.0] for _ in range(chunk_count)],
    )

    assert calls == [
        ("chunks", CHUNK_UPLOAD_BATCH_SIZE),
        ("chunks", CHUNK_UPLOAD_BATCH_SIZE),
        ("chunks", 3),
        ("document", None),
    ]


def test_new_document_keeps_incomplete_sha_when_chunk_upload_fails(monkeypatch) -> None:
    store = object.__new__(SupabaseRagStore)
    document = _document()
    committed_hashes: list[str | None] = []

    monkeypatch.setattr(store, "_upsert_source", lambda _: {"id": "source-1"})
    monkeypatch.setattr(store, "get_document_by_uri", lambda _: None)

    def upsert_document(*args, **kwargs):
        committed_hashes.append(kwargs.get("sha256"))
        return {"id": "document-1"}

    def request(method, path, **kwargs):
        if method == "POST" and path == "knowledge_chunks":
            raise RuntimeError("write timeout")
        return None

    monkeypatch.setattr(store, "_upsert_document", upsert_document)
    monkeypatch.setattr(store, "_request", request)

    with pytest.raises(RuntimeError, match="write timeout"):
        store.upsert_document_with_chunks(document, _chunks(1), [[0.0, 1.0]])

    assert committed_hashes == [f"incomplete:{document.sha256}"]


def test_search_rpc_receives_every_exact_validation_filter(monkeypatch) -> None:
    store = object.__new__(SupabaseRagStore)
    captured = {}

    def request(method, path, **kwargs):
        captured.update(method=method, path=path, payload=kwargs["json"])
        return []

    monkeypatch.setattr(store, "_request", request)

    store.search_knowledge(
        query="qualified evidence",
        query_embedding=None,
        filters=SearchFilters(
            action_id="act_1234567890abcdef12345678",
            version_id="av_" + "1" * 64,
            environment="sandbox",
            capability="documents.invoice.list",
            accounting_use="revenue_review",
        ),
        top_k=8,
        mode="keyword",
    )

    assert captured["path"] == "rpc/match_knowledge_chunks"
    assert captured["payload"] == {
        "query_text": "qualified evidence",
        "query_embedding": None,
        "match_count": 8,
        "search_mode": "keyword",
        "filter_jurisdiction": None,
        "filter_connector": None,
        "filter_doc_type": None,
        "filter_review_status": None,
        "filter_effective_date": None,
        "filter_action_id": "act_1234567890abcdef12345678",
        "filter_version_id": "av_" + "1" * 64,
        "filter_environment": "sandbox",
        "filter_capability": "documents.invoice.list",
        "filter_accounting_use": "revenue_review",
    }


def test_chunk_upload_passes_only_explicit_chunk_metadata(monkeypatch) -> None:
    store = object.__new__(SupabaseRagStore)
    uploaded = []
    document = _document()
    chunk = replace(
        _chunks(1)[0],
        metadata={
            "connector": "flowaccount",
            "action_id": "act_1234567890abcdef12345678",
            "approval_state": "approved_public",
        },
    )

    monkeypatch.setattr(store, "_upsert_source", lambda _: {"id": "source-1"})
    monkeypatch.setattr(
        store,
        "get_document_by_uri",
        lambda _: {"id": "document-1", "sha256": "previous"},
    )
    monkeypatch.setattr(store, "_upsert_document", lambda *args, **kwargs: {"id": "document-1"})

    def request(method, path, **kwargs):
        if method == "POST" and path == "knowledge_chunks":
            uploaded.extend(kwargs["json"])
        return None

    monkeypatch.setattr(store, "_request", request)

    store.upsert_document_with_chunks(document, [chunk], [[0.0, 1.0]])

    assert uploaded[0]["metadata"] == chunk.metadata
    assert "body" not in uploaded[0]["metadata"]


def test_supabase_request_failure_is_constant_and_does_not_echo_body(monkeypatch) -> None:
    store = object.__new__(SupabaseRagStore)
    store.base_url = "https://example.test/rest/v1"
    store.headers = {}
    secret_body = "provider body with private-value"

    class FailedResponse:
        status_code = 500
        text = secret_body

    monkeypatch.setattr(
        "mercury_tools.db.supabase.httpx.request",
        lambda *args, **kwargs: FailedResponse(),
    )

    with pytest.raises(RuntimeError, match="^supabase_rag_request_failed$") as raised:
        store._request("GET", "knowledge_documents")

    assert secret_body not in str(raised.value)


def test_rag_filter_migration_replaces_old_signature_without_overload() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    old_signature = (
        "public.match_knowledge_chunks(\n"
        "  text,\n"
        "  vector(1536),\n"
        "  integer,\n"
        "  text,\n"
        "  text,\n"
        "  text,\n"
        "  text,\n"
        "  text,\n"
        "  date\n"
        ")"
    )

    assert f"drop function if exists {old_signature};" in sql
    assert sql.index("drop function if exists") < sql.index("create function")
    assert sql.count("create function public.match_knowledge_chunks") == 1
    for parameter in (
        "filter_action_id",
        "filter_version_id",
        "filter_environment",
        "filter_capability",
        "filter_accounting_use",
    ):
        assert parameter in sql
    assert "c.metadata ->> 'action_id' = filter_action_id" in sql
    assert "c.metadata -> 'accounting_use' ? filter_accounting_use" in sql
    assert "set search_path = pg_catalog, public, pg_temp" in sql
    assert "from public, anon, authenticated" in sql
    assert "to service_role" in sql
