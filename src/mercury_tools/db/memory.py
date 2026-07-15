"""In-memory RAG store for tests."""

from __future__ import annotations

from mercury_tools.rag.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    SearchFilters,
    SearchResult,
    project_public_knowledge_metadata,
)


class InMemoryRagStore:
    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.chunks: list[dict] = []
        self.audit_events: list[dict] = []

    def get_document_by_uri(self, document_uri: str) -> dict | None:
        return self.documents.get(document_uri)

    def upsert_document_with_chunks(
        self,
        document: KnowledgeDocument,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        self.documents[document.document_uri] = {
            "document_uri": document.document_uri,
            "title": document.title,
            "sha256": document.sha256,
            "body": document.body,
            "metadata": project_public_knowledge_metadata(
                document.metadata,
                document_uri=document.document_uri,
                source_uri=document.source_uri,
                doc_type=document.doc_type,
            ),
        }
        self.chunks = [
            chunk for chunk in self.chunks if chunk["document_uri"] != document.document_uri
        ]
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.chunks.append(
                {
                    "id": chunk.chunk_uri,
                    "document_id": document.document_uri,
                    "document_uri": document.document_uri,
                    "chunk_uri": chunk.chunk_uri,
                    "chunk_text": chunk.text,
                    "source_title": chunk.source_title,
                    "source_uri": chunk.source_uri,
                    "source_url": chunk.source_url,
                    "source_path": chunk.source_path,
                    "citation": chunk.citation,
                    "metadata": chunk.metadata,
                    "embedding": embedding,
                }
            )

    def search_knowledge(
        self,
        *,
        query: str,
        query_embedding: list[float] | None,
        filters: SearchFilters,
        top_k: int,
        mode: str,
    ) -> list[SearchResult]:
        del query_embedding, mode
        query_lower = query.lower()
        rows = []
        for row in self.chunks:
            metadata = row.get("metadata") or {}
            if filters.jurisdiction and metadata.get("jurisdiction") != filters.jurisdiction:
                continue
            if filters.connector and metadata.get("connector") != filters.connector:
                continue
            if filters.doc_type and metadata.get("doc_type") != filters.doc_type:
                continue
            if filters.review_status and metadata.get("review_status") != filters.review_status:
                continue
            if (
                filters.effective_date
                and metadata.get("effective_date") is not None
                and metadata.get("effective_date") > filters.effective_date
            ):
                continue
            if filters.action_id and metadata.get("action_id") != filters.action_id:
                continue
            if filters.version_id and metadata.get("version_id") != filters.version_id:
                continue
            if filters.environment and metadata.get("environment") != filters.environment:
                continue
            if filters.capability and metadata.get("capability") != filters.capability:
                continue
            if filters.accounting_use:
                accounting_uses = metadata.get("accounting_use")
                if isinstance(accounting_uses, str):
                    accounting_use_matches = accounting_uses == filters.accounting_use
                elif isinstance(accounting_uses, (list, tuple)) and all(
                    isinstance(item, str) for item in accounting_uses
                ):
                    accounting_use_matches = filters.accounting_use in accounting_uses
                else:
                    accounting_use_matches = False
                if not accounting_use_matches:
                    continue
            score = 1.0 if query_lower in row["chunk_text"].lower() else 0.1
            rows.append((score, row))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [
            SearchResult(
                chunk_id=row["id"],
                document_id=row["document_id"],
                document_uri=row["document_uri"],
                chunk_uri=row["chunk_uri"],
                text=row["chunk_text"],
                score=score,
                source_title=row["source_title"],
                source_uri=row["source_uri"],
                source_url=row["source_url"],
                source_path=row["source_path"],
                citation=row["citation"],
                metadata=row["metadata"],
            )
            for score, row in rows[:top_k]
        ]

    def record_audit_event(self, event: dict) -> dict:
        event = {"id": f"memory-{len(self.audit_events) + 1}", **event}
        self.audit_events.append(event)
        return event
