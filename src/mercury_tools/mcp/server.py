"""Mercury Tools MCP server."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.mercury_runtime import connector_status as read_connector_status
from mercury_tools.mercury_runtime import skill_markdown
from mercury_tools.prompts import get_prompt
from mercury_tools.rag.embeddings import OpenAIEmbeddingProvider
from mercury_tools.rag.models import SearchFilters
from mercury_tools.rag.service import RagService
from mercury_tools.safety.redaction import redact_json

mcp = FastMCP("Mercury Tools")


def _service() -> RagService:
    settings = load_settings()
    return RagService(store=SupabaseRagStore(settings), embedder=OpenAIEmbeddingProvider(settings))


def _audit(tool_name: str, input_payload: dict[str, Any], output_summary: dict[str, Any]) -> None:
    try:
        settings = load_settings()
        SupabaseRagStore(settings).record_audit_event(
            {
                "tool_name": tool_name,
                "input": input_payload,
                "output_summary": output_summary,
                "status": "ok",
                "metadata": {"runtime": "mcp"},
            }
        )
    except Exception:
        pass


def _filters(filters: dict[str, Any] | None) -> SearchFilters:
    filters = filters or {}
    return SearchFilters(
        jurisdiction=filters.get("jurisdiction"),
        connector=filters.get("connector"),
        doc_type=filters.get("doc_type"),
        review_status=filters.get("review_status"),
        effective_date=filters.get("effective_date"),
    )


@mcp.tool()
def search_knowledge(
    query: str,
    filters: dict[str, Any] | None = None,
    top_k: int = 8,
    mode: str = "hybrid",
) -> dict[str, Any]:
    """Search Mercury accounting knowledge and return citation-bearing chunks."""
    results = _service().search(query, filters=_filters(filters), top_k=top_k, mode=mode)
    payload = {
        "query": query,
        "results": [
            {
                "chunk_id": result.chunk_id,
                "document_uri": result.document_uri,
                "score": result.score,
                "text": result.text,
                "citation": result.citation,
                "source_title": result.source_title,
                "source_uri": result.source_uri,
                "source_url": result.source_url,
                "source_path": result.source_path,
            }
            for result in results
        ],
    }
    payload = redact_json(payload)
    _audit(
        "search_knowledge",
        {"query": query, "filters": filters, "top_k": top_k},
        {"count": len(results)},
    )
    return payload


@mcp.tool()
def retrieve_context_pack(
    query: str,
    task: str | None = None,
    filters: dict[str, Any] | None = None,
    max_chunks: int = 12,
) -> dict[str, Any]:
    """Return a context pack with citations for the host agent to answer with."""
    pack = _service().context_pack(
        query,
        task=task,
        filters=_filters(filters),
        max_chunks=max_chunks,
    )
    payload = redact_json(pack.as_dict())
    _audit("retrieve_context_pack", {"query": query, "task": task}, {"count": len(pack.results)})
    return payload


@mcp.tool()
def get_document(document_id: str) -> dict[str, Any]:
    """Fetch one indexed knowledge document by UUID or document URI."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    payload = redact_json({"status": "ok" if document else "not_found", "document": document})
    _audit("get_document", {"document_id": document_id}, {"found": bool(document)})
    return payload


@mcp.tool()
def connector_status() -> dict[str, Any]:
    """Read sanitized Mercury connector status from the local Mercury runtime."""
    payload = read_connector_status()
    _audit("connector_status", {}, {"status": payload.get("status")})
    return payload


@mcp.tool()
def run_accounting_skill(
    skill_id: str,
    inputs: dict[str, Any],
    evidence_mode: bool = False,
) -> dict[str, Any]:
    """Return a read-only accounting skill execution package for the host agent."""
    markdown = skill_markdown(skill_id)
    payload = redact_json(
        {
            "status": "ok" if markdown else "not_found",
            "skill_id": skill_id,
            "inputs": inputs,
            "evidence_mode": evidence_mode,
            "skill_markdown": markdown,
            "note": "v1 returns a read-only skill package; production writes are blocked.",
        }
    )
    _audit(
        "run_accounting_skill",
        {"skill_id": skill_id, "inputs": inputs},
        {"status": payload["status"]},
    )
    return payload


@mcp.resource("mercury://wiki/index")
def wiki_index() -> str:
    """Return the Mercury LLM Wiki index prompt resource."""
    return (
        "Use search_knowledge or retrieve_context_pack to fetch indexed wiki content "
        "with citations."
    )


@mcp.resource("mercury://wiki/doc/{document_id}")
def wiki_document(document_id: str) -> str:
    """Return one knowledge document body."""
    document = SupabaseRagStore(load_settings()).get_document(document_id)
    if not document:
        return "Document not found."
    return str(document.get("body") or "")


@mcp.resource("mercury://skills/{skill_id}")
def skill_resource(skill_id: str) -> str:
    """Return bundled Mercury skill markdown if available locally."""
    return skill_markdown(skill_id) or "Skill not found."


@mcp.resource("mercury://connectors")
def connectors_resource() -> str:
    """Return sanitized connector status."""
    return str(connector_status())


@mcp.resource("mercury://audit/{event_id}")
def audit_resource(event_id: str) -> str:
    """Return one sanitized MCP audit event from Supabase."""
    event = SupabaseRagStore(load_settings()).get_audit_event(event_id)
    if not event:
        return "Audit event not found."
    return str(redact_json(event))


@mcp.prompt()
def company_health_check_th() -> str:
    return get_prompt("company_health_check_th")


@mcp.prompt()
def vat_summary_th() -> str:
    return get_prompt("vat_summary_th")


@mcp.prompt()
def invoice_review_th() -> str:
    return get_prompt("invoice_review_th")


@mcp.prompt()
def management_report_th() -> str:
    return get_prompt("management_report_th")


@mcp.prompt()
def connector_setup_guide_th() -> str:
    return get_prompt("connector_setup_guide_th")


def serve(*, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000) -> None:
    if transport == "stdio":
        mcp.run(transport="stdio")
        return
    if transport in {"http", "streamable-http"}:
        mcp.settings.host = host
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
        return
    raise ValueError(f"Unsupported transport: {transport}")


if __name__ == "__main__":
    serve()
