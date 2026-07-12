"""Supabase PostgREST adapter for Mercury Tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx

from mercury_tools.config import Settings, require_supabase
from mercury_tools.rag.models import KnowledgeChunk, KnowledgeDocument, SearchFilters, SearchResult
from mercury_tools.safety.redaction import redact_json

CHUNK_UPLOAD_BATCH_SIZE = 10


class SupabaseRagStore:
    def __init__(self, settings: Settings):
        require_supabase(settings)
        self.settings = settings
        self.base_url = f"{settings.supabase_url}/rest/v1"
        self.headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        extra_headers = kwargs.pop("headers", {})
        headers = {**self.headers, **extra_headers}
        response = httpx.request(method, url, headers=headers, timeout=60, **kwargs)
        if response.status_code >= 300:
            raise RuntimeError(
                f"Supabase request failed: HTTP {response.status_code} {response.text[:300]}"
            )
        if not response.text:
            return None
        return response.json()

    def get_document_by_uri(self, document_uri: str) -> dict | None:
        rows = self._request(
            "GET",
            "knowledge_documents",
            params={
                "document_uri": f"eq.{document_uri}",
                "select": "id,document_uri,sha256,title",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def _upsert_source(self, document: KnowledgeDocument) -> dict:
        payload = {
            "source_uri": document.source_uri,
            "title": document.source_title,
            "source_url": document.source_url,
            "source_path": str(document.path) if document.path else None,
            "jurisdiction": document.jurisdiction,
            "connector": document.connector,
            "doc_type": document.doc_type,
            "review_status": document.review_status,
            "metadata": document.metadata,
        }
        rows = self._request(
            "POST",
            "knowledge_sources",
            params={"on_conflict": "source_uri"},
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            json=[payload],
        )
        return rows[0]

    def _upsert_document(
        self,
        document: KnowledgeDocument,
        source_id: str,
        *,
        sha256: str | None = None,
    ) -> dict:
        payload = {
            "source_id": source_id,
            "document_uri": document.document_uri,
            "title": document.title,
            "body": document.body,
            "sha256": sha256 or document.sha256,
            "effective_date": document.effective_date,
            "metadata": document.metadata,
        }
        rows = self._request(
            "POST",
            "knowledge_documents",
            params={"on_conflict": "document_uri"},
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=representation"},
            json=[payload],
        )
        return rows[0]

    def upsert_document_with_chunks(
        self,
        document: KnowledgeDocument,
        chunks: list[KnowledgeChunk],
        embeddings: list[list[float]],
    ) -> None:
        source = self._upsert_source(document)
        existing = self.get_document_by_uri(document.document_uri)
        if existing is None:
            doc = self._upsert_document(
                document,
                source["id"],
                sha256=f"incomplete:{document.sha256}",
            )
        else:
            doc = existing
        self._request(
            "DELETE",
            "knowledge_chunks",
            params={"document_id": f"eq.{doc['id']}"},
        )
        payload = []
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            payload.append(
                {
                    "document_id": doc["id"],
                    "chunk_uri": chunk.chunk_uri,
                    "chunk_index": chunk.chunk_index,
                    "chunk_text": chunk.text,
                    "embedding": embedding,
                    "citation": chunk.citation,
                    "metadata": chunk.metadata,
                }
            )
        for offset in range(0, len(payload), CHUNK_UPLOAD_BATCH_SIZE):
            self._request(
                "POST",
                "knowledge_chunks",
                json=payload[offset : offset + CHUNK_UPLOAD_BATCH_SIZE],
            )
        self._upsert_document(document, source["id"])

    def search_knowledge(
        self,
        *,
        query: str,
        query_embedding: list[float] | None,
        filters: SearchFilters,
        top_k: int,
        mode: str,
    ) -> list[SearchResult]:
        payload = {
            "query_text": query,
            "query_embedding": query_embedding,
            "match_count": top_k,
            "search_mode": mode,
            **filters.to_rpc_payload(),
        }
        rows = self._request("POST", "rpc/match_knowledge_chunks", json=payload) or []
        return [
            SearchResult(
                chunk_id=str(row["chunk_id"]),
                document_id=str(row["document_id"]),
                document_uri=str(row["document_uri"]),
                chunk_uri=str(row["chunk_uri"]),
                text=str(row["chunk_text"]),
                score=float(row.get("score") or 0),
                source_title=str(row.get("source_title") or ""),
                source_uri=str(row.get("source_uri") or ""),
                source_url=row.get("source_url"),
                source_path=row.get("source_path"),
                citation=row.get("citation") or {},
                metadata=row.get("metadata") or {},
            )
            for row in rows
        ]

    def get_document(self, document_id: str) -> dict | None:
        rows = self._request(
            "GET",
            "knowledge_documents",
            params={
                "or": f"(id.eq.{document_id},document_uri.eq.{document_id})",
                "select": "id,document_uri,title,body,sha256,metadata,knowledge_sources(*)",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    def record_audit_event(self, event: dict[str, Any]) -> dict:
        sanitized = redact_json(event)
        payload = {
            "tool_name": str(sanitized.get("tool_name") or ""),
            "input_hash": hashlib.sha256(
                json.dumps(sanitized.get("input") or {}, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "output_summary": sanitized.get("output_summary") or {},
            "status": str(sanitized.get("status") or "ok"),
            "metadata": sanitized.get("metadata") or {},
        }
        rows = self._request(
            "POST",
            "mcp_audit_events",
            headers={**self.headers, "Prefer": "return=representation"},
            json=[payload],
        )
        return rows[0]

    def get_audit_event(self, event_id: str) -> dict | None:
        rows = self._request(
            "GET",
            "mcp_audit_events",
            params={
                "id": f"eq.{event_id}",
                "select": "id,created_at,tool_name,input_hash,output_summary,status,metadata",
                "limit": "1",
            },
        )
        return rows[0] if rows else None
