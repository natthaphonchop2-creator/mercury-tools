"""Wiki ingestion pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from mercury_tools.rag.chunking import chunk_document, document_from_markdown
from mercury_tools.rag.embeddings import EmbeddingProvider
from mercury_tools.rag.models import KnowledgeChunk, KnowledgeDocument


class RagStore(Protocol):
    def get_document_by_uri(self, document_uri: str) -> dict | None:
        ...

    def upsert_document_with_chunks(
        self,
        document: KnowledgeDocument,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        ...


@dataclass(frozen=True)
class IngestStats:
    scanned: int = 0
    inserted_or_updated: int = 0
    skipped_unchanged: int = 0
    chunks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "inserted_or_updated": self.inserted_or_updated,
            "skipped_unchanged": self.skipped_unchanged,
            "chunks": self.chunks,
        }


def iter_markdown_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.md")
        if ".git" not in path.parts and not path.name.startswith(".")
    )


def ingest_wiki(root: Path, *, store: RagStore, embedder: EmbeddingProvider) -> IngestStats:
    root = root.expanduser().resolve()
    scanned = inserted_or_updated = skipped = chunk_count = 0
    for path in iter_markdown_files(root):
        scanned += 1
        document = document_from_markdown(path, root=root)
        existing = store.get_document_by_uri(document.document_uri)
        if existing and existing.get("sha256") == document.sha256:
            skipped += 1
            continue
        chunks = chunk_document(document)
        embeddings = embedder.embed_texts([chunk.text for chunk in chunks])
        store.upsert_document_with_chunks(document, chunks, embeddings)
        inserted_or_updated += 1
        chunk_count += len(chunks)
    return IngestStats(
        scanned=scanned,
        inserted_or_updated=inserted_or_updated,
        skipped_unchanged=skipped,
        chunks=chunk_count,
    )

