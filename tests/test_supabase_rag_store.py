from __future__ import annotations

from pathlib import Path

import pytest

from mercury_tools.db.supabase import CHUNK_UPLOAD_BATCH_SIZE, SupabaseRagStore
from mercury_tools.rag.models import KnowledgeChunk, KnowledgeDocument


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
