"""High-level RAG service."""

from __future__ import annotations

from mercury_tools.rag.embeddings import EmbeddingProvider
from mercury_tools.rag.models import ContextPack, SearchFilters, SearchResult

MIN_RELEVANCE_SCORE = 0.20


class RagService:
    def __init__(self, *, store, embedder: EmbeddingProvider):
        self.store = store
        self.embedder = embedder

    def search(
        self,
        query: str,
        *,
        filters: SearchFilters | None = None,
        top_k: int = 8,
        mode: str = "hybrid",
        minimum_score: float = MIN_RELEVANCE_SCORE,
    ) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise ValueError("query is required")
        embedding = self.embedder.embed_query(query) if mode in {"hybrid", "vector"} else None
        results = self.store.search_knowledge(
            query=query,
            query_embedding=embedding,
            filters=filters or SearchFilters(),
            top_k=top_k,
            mode=mode,
        )
        return [result for result in results if result.score >= minimum_score]

    def context_pack(
        self,
        query: str,
        *,
        task: str | None = None,
        filters: SearchFilters | None = None,
        max_chunks: int = 12,
    ) -> ContextPack:
        return ContextPack(
            query=query,
            task=task,
            results=self.search(query, filters=filters, top_k=max_chunks, mode="hybrid"),
        )
