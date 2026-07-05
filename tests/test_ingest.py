from pathlib import Path

from mercury_tools.db.memory import InMemoryRagStore
from mercury_tools.rag.embeddings import HashEmbeddingProvider
from mercury_tools.rag.ingest import ingest_wiki


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

