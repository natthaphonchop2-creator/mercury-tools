"""Command-line interface for Mercury Tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.rag.embeddings import HashEmbeddingProvider, OpenAIEmbeddingProvider
from mercury_tools.rag.ingest import ingest_wiki
from mercury_tools.rag.models import SearchFilters
from mercury_tools.rag.service import RagService
from mercury_tools.remote import DEFAULT_RENDER_URL, DEFAULT_TOKEN_FILE, read_token, verify_remote


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _embedder(args: argparse.Namespace):
    settings = load_settings()
    if getattr(args, "embedding_provider", "openai") == "hash":
        return HashEmbeddingProvider(settings.embedding_dim)
    return OpenAIEmbeddingProvider(settings)


def cmd_doctor(_args: argparse.Namespace) -> int:
    settings = load_settings()
    _print_json(
        {
            "supabase": settings.supabase_configured,
            "openai": settings.openai_configured,
            "embedding_model": settings.embedding_model,
            "embedding_dim": settings.embedding_dim,
            "mercury_agent_path": (
                str(settings.mercury_agent_path) if settings.mercury_agent_path else None
            ),
            "mercury_home": str(settings.mercury_home) if settings.mercury_home else None,
            "mcp": {
                "transport": settings.mcp_transport,
                "host": settings.mcp_host,
                "port": settings.mcp_port,
                "path": settings.mcp_path,
                "endpoint": settings.mcp_endpoint,
                "http_auth_required": settings.http_require_auth,
                "http_auth_configured": settings.http_auth_configured,
            },
        }
    )
    return 0


def cmd_ingest_wiki(args: argparse.Namespace) -> int:
    settings = load_settings()
    stats = ingest_wiki(
        Path(args.path),
        store=SupabaseRagStore(settings),
        embedder=_embedder(args),
    )
    _print_json({"status": "ok", "ingest": stats.as_dict()})
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    settings = load_settings()
    service = RagService(store=SupabaseRagStore(settings), embedder=_embedder(args))
    filters = SearchFilters(
        jurisdiction=args.jurisdiction,
        connector=args.connector,
        doc_type=args.doc_type,
        review_status=args.review_status,
        effective_date=args.effective_date,
    )
    results = service.search(args.query, filters=filters, top_k=args.top_k, mode=args.mode)
    payload = [
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
    ]
    if args.json:
        _print_json({"query": args.query, "results": payload})
    else:
        for item in payload:
            print(f"- {item['source_title']} ({item['score']:.3f})")
            print(f"  {item['text'][:240].replace(chr(10), ' ')}")
            print(f"  citation: {item['citation']}")
    return 0


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from mercury_tools.mcp.server import serve

    settings = load_settings()
    require_auth = settings.http_require_auth
    if args.require_auth:
        require_auth = True
    if args.allow_unauthenticated:
        require_auth = False
    serve(
        transport=args.transport or settings.mcp_transport,
        host=args.host or settings.mcp_host,
        port=args.port or settings.mcp_port,
        require_auth=require_auth,
    )
    return 0


def cmd_remote_verify(args: argparse.Namespace) -> int:
    token = read_token(token=args.token, token_file=args.token_file)
    result = verify_remote(
        base_url=args.url,
        mcp_path=args.mcp_path,
        token=token,
        timeout=args.timeout,
    )
    payload = result.as_dict()
    if args.json:
        _print_json(payload)
    else:
        print(f"Mercury Tools remote: {payload['base_url']}")
        print(f"healthz: HTTP {payload['health_status_code']} -> {payload['health'].get('status')}")
        print(f"supabase: {payload['health'].get('supabase')}")
        print(f"openai: {payload['health'].get('openai')}")
        print(f"mcp: {payload['mcp_url']}")
        print(f"auth configured: {payload['health'].get('http_auth_configured')}")
        print(f"auth check: {payload['authenticated_mcp_reachable']}")
        if payload["missing"]:
            print("missing:")
            for item in payload["missing"]:
                print(f"- {item}")
        if payload["errors"]:
            print("errors:")
            for item in payload["errors"]:
                print(f"- {item}")
        print(f"ready: {payload['ready']}")
    return 0 if result.ready else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mercury-tools")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=cmd_doctor)

    ingest = sub.add_parser("ingest")
    ingest_sub = ingest.add_subparsers(dest="ingest_command", required=True)
    wiki = ingest_sub.add_parser("wiki")
    wiki.add_argument("--path", required=True)
    wiki.add_argument("--embedding-provider", choices=["openai", "hash"], default="openai")
    wiki.set_defaults(func=cmd_ingest_wiki)

    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--json", action="store_true")
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument("--mode", choices=["hybrid", "keyword", "vector"], default="hybrid")
    search.add_argument("--jurisdiction")
    search.add_argument("--connector")
    search.add_argument("--doc-type")
    search.add_argument("--review-status")
    search.add_argument("--effective-date")
    search.add_argument("--embedding-provider", choices=["openai", "hash"], default="openai")
    search.set_defaults(func=cmd_search)

    mcp = sub.add_parser("mcp")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    serve = mcp_sub.add_parser("serve")
    serve.add_argument("--transport", choices=["stdio", "http", "streamable-http"])
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--require-auth", action="store_true")
    serve.add_argument("--allow-unauthenticated", action="store_true")
    serve.set_defaults(func=cmd_mcp_serve)

    remote = sub.add_parser("remote")
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)
    verify = remote_sub.add_parser("verify")
    verify.add_argument("--url", default=DEFAULT_RENDER_URL)
    verify.add_argument("--mcp-path", default="/mcp")
    verify.add_argument("--token")
    verify.add_argument("--token-file", default=str(DEFAULT_TOKEN_FILE))
    verify.add_argument("--timeout", type=float, default=20)
    verify.add_argument("--json", action="store_true")
    verify.set_defaults(func=cmd_remote_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
