from pathlib import Path

from mercury_tools.db.memory import InMemoryRagStore
from mercury_tools.rag import ingest as rag_ingest
from mercury_tools.rag.embeddings import HashEmbeddingProvider
from mercury_tools.rag.ingest import ingest_wiki
from mercury_tools.rag.models import KnowledgeDocument


def test_ingest_skips_unchanged_documents(tmp_path: Path) -> None:
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "index.md").write_text("# Index\n\nVAT input tax.", encoding="utf-8")
    store = InMemoryRagStore()
    embedder = HashEmbeddingProvider()

    first = ingest_wiki(wiki, store=store, embedder=embedder)
    second = ingest_wiki(wiki, store=store, embedder=embedder)

    assert first.inserted_or_updated == 1
    assert first.chunks == 1
    assert second.skipped_unchanged == 1
    assert second.inserted_or_updated == 0


def test_ingest_documents_publishes_generated_validation_documents() -> None:
    document = KnowledgeDocument(
        document_uri="mercury://wiki/validation/flowaccount/action/version/run",
        title="Validation",
        body="# Validation\n\nQualified endpoint evidence.",
        sha256="1" * 64,
        source_uri="mercury://wiki/validation/flowaccount/action/version/run",
        source_title="Validation",
    )
    store = InMemoryRagStore()

    stats = rag_ingest.ingest_documents(
        (document,),
        store=store,
        embedder=HashEmbeddingProvider(),
    )

    assert stats.as_dict() == {
        "scanned": 1,
        "inserted_or_updated": 1,
        "skipped_unchanged": 0,
        "chunks": 1,
    }
    assert store.documents[document.document_uri]["sha256"] == document.sha256
