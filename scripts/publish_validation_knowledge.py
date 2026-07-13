#!/usr/bin/env python3
"""Publish reviewed validation evidence and approved RAG projections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mercury_tools.config import load_settings
from mercury_tools.db.supabase import SupabaseRagStore
from mercury_tools.db.validation import SupabaseValidationStore
from mercury_tools.qualification.publisher import (
    CatalogDefinitions,
    ReviewedValidationReport,
    load_catalog_definitions,
    validation_documents,
)
from mercury_tools.rag.embeddings import EmbeddingProvider, create_embedding_provider
from mercury_tools.rag.ingest import RagStore, ingest_documents

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_CATALOG_ROOT = _REPOSITORY_ROOT / "catalog" / "global"
_SAFE_ERROR = re.compile(r"^[a-z][a-z0-9_]{1,127}$")


class ValidationStore(Protocol):
    def publish(self, records: Sequence[Any]) -> int:
        ...


@dataclass(frozen=True)
class PublicationStats:
    validation_rows_inserted: int
    rag_documents_inserted_or_updated: int
    rag_documents_skipped: int
    rag_chunks: int


def publish_reviewed_report(
    report: ReviewedValidationReport,
    *,
    catalog: CatalogDefinitions,
    validation_store: ValidationStore,
    rag_store: RagStore | None,
    embedder: EmbeddingProvider | None,
    require_approved: bool,
    ingest_rag: bool,
) -> PublicationStats:
    try:
        validated = ReviewedValidationReport.model_validate(report)
    except (TypeError, ValueError):
        raise ValueError("reviewed_validation_report_invalid") from None
    if require_approved and any(
        not record.approved_public for record in validated.records
    ):
        raise ValueError("validation_records_not_approved")
    if ingest_rag and (rag_store is None or embedder is None):
        raise ValueError("validation_rag_dependencies_missing")

    inserted = validation_store.publish(validated.records)
    rag_stats = None
    if ingest_rag:
        documents = validation_documents(validated.records, catalog=catalog)
        rag_stats = ingest_documents(
            documents,
            store=rag_store,
            embedder=embedder,
        )
    return PublicationStats(
        validation_rows_inserted=inserted,
        rag_documents_inserted_or_updated=(
            rag_stats.inserted_or_updated if rag_stats is not None else 0
        ),
        rag_documents_skipped=rag_stats.skipped_unchanged if rag_stats is not None else 0,
        rag_chunks=rag_stats.chunks if rag_stats is not None else 0,
    )


def _read_reviewed_report(path: Path) -> ReviewedValidationReport:
    try:
        return ReviewedValidationReport.model_validate(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("reviewed_validation_report_invalid") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publish endpoint validation knowledge.")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--catalog-root", type=Path, default=_DEFAULT_CATALOG_ROOT)
    parser.add_argument("--require-approved", action="store_true")
    parser.add_argument("--ingest-rag", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _read_reviewed_report(args.input)
        catalog = load_catalog_definitions(args.catalog_root)
        settings = load_settings()
        rag_store = SupabaseRagStore(settings) if args.ingest_rag else None
        embedder = create_embedding_provider(settings) if args.ingest_rag else None
        stats = publish_reviewed_report(
            report,
            catalog=catalog,
            validation_store=SupabaseValidationStore(settings),
            rag_store=rag_store,
            embedder=embedder,
            require_approved=args.require_approved,
            ingest_rag=args.ingest_rag,
        )
    except (RuntimeError, ValueError) as error:
        code = str(error) if _SAFE_ERROR.fullmatch(str(error)) else "validation_publish_failed"
        print(code, file=sys.stderr)
        return 1
    print(
        " ".join(
            (
                f"validation_rows_inserted={stats.validation_rows_inserted}",
                "rag_documents_inserted_or_updated="
                f"{stats.rag_documents_inserted_or_updated}",
                f"rag_documents_skipped={stats.rag_documents_skipped}",
                f"rag_chunks={stats.rag_chunks}",
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
