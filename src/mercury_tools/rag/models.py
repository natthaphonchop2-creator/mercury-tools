"""RAG data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SearchFilters:
    jurisdiction: str | None = None
    connector: str | None = None
    doc_type: str | None = None
    review_status: str | None = None
    effective_date: str | None = None

    def to_rpc_payload(self) -> dict[str, Any]:
        return {
            "filter_jurisdiction": self.jurisdiction,
            "filter_connector": self.connector,
            "filter_doc_type": self.doc_type,
            "filter_review_status": self.review_status,
            "filter_effective_date": self.effective_date,
        }


@dataclass(frozen=True)
class KnowledgeDocument:
    document_uri: str
    title: str
    body: str
    sha256: str
    source_uri: str
    source_title: str
    path: Path | None = None
    source_url: str | None = None
    jurisdiction: str | None = None
    connector: str | None = None
    doc_type: str = "wiki"
    review_status: str = "draft"
    effective_date: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class KnowledgeChunk:
    document_uri: str
    chunk_uri: str
    chunk_index: int
    text: str
    source_title: str
    source_uri: str
    source_url: str | None
    source_path: str | None
    heading: str | None
    citation: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: str
    document_id: str
    document_uri: str
    chunk_uri: str
    text: str
    score: float
    source_title: str
    source_uri: str
    source_url: str | None
    source_path: str | None
    citation: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextPack:
    query: str
    task: str | None
    results: list[SearchResult]

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "task": self.task,
            "context": [
                {
                    "chunk_id": result.chunk_id,
                    "document_id": result.document_id,
                    "document_uri": result.document_uri,
                    "text": result.text,
                    "score": result.score,
                    "citation": result.citation,
                    "source_title": result.source_title,
                    "source_uri": result.source_uri,
                    "source_url": result.source_url,
                    "source_path": result.source_path,
                    "metadata": result.metadata,
                }
                for result in self.results
            ],
        }

