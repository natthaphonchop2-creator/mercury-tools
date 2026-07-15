"""Markdown parsing and citation-aware chunking."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from mercury_tools.rag.models import (
    VALIDATION_METADATA_FIELDS,
    KnowledgeChunk,
    KnowledgeDocument,
    project_approved_validation_metadata,
)

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
ACTION_ID_RE = re.compile(r"^action_id: (act_[0-9a-f]{24})$", re.MULTILINE)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    loaded = yaml.safe_load(match.group(1)) or {}
    metadata = loaded if isinstance(loaded, dict) else {}
    return metadata, text[match.end() :]


def first_heading(body: str, fallback: str) -> str:
    match = HEADING_RE.search(body)
    return match.group(2).strip() if match else fallback


def document_from_markdown(path: Path, *, root: Path | None = None) -> KnowledgeDocument:
    raw = path.read_text(encoding="utf-8")
    metadata, body = parse_frontmatter(raw)
    doc_type = str(metadata.get("doc_type") or metadata.get("type") or "wiki")
    review_status = str(metadata.get("review_status") or "draft")
    if doc_type in {"accounting_standard", "tax"} and review_status == "reviewed":
        if not metadata.get("source_url"):
            raise ValueError("reviewed regulated document requires source_url")
        if not metadata.get("source_verified_at"):
            raise ValueError("reviewed regulated document requires source_verified_at")
    relative = path.relative_to(root) if root else path.name
    slug = str(relative.with_suffix("")).replace("\\", "/")
    document_uri = str(metadata.get("document_uri") or f"mercury://wiki/{slug}")
    source_uri = str(metadata.get("source_uri") or document_uri)
    title = str(metadata.get("title") or first_heading(body, path.stem.replace("-", " ").title()))
    source_title = str(metadata.get("source_title") or title)
    return KnowledgeDocument(
        document_uri=document_uri,
        title=title,
        body=body.strip(),
        sha256=sha256_text(body.strip()),
        source_uri=source_uri,
        source_title=source_title,
        path=path,
        source_url=metadata.get("source_url"),
        jurisdiction=metadata.get("jurisdiction"),
        connector=metadata.get("connector"),
        doc_type=doc_type,
        review_status=review_status,
        effective_date=metadata.get("effective_date"),
        metadata=metadata,
    )


def split_markdown_sections(body: str) -> list[tuple[str | None, str]]:
    matches = list(HEADING_RE.finditer(body))
    if not matches:
        return [(None, body.strip())] if body.strip() else []

    sections: list[tuple[str | None, str]] = []
    prefix = body[: matches[0].start()].strip()
    if prefix:
        sections.append((None, prefix))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        heading = match.group(2).strip()
        text = body[match.start() : end].strip()
        if text:
            sections.append((heading, text))
    return sections


def _window_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    windows: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if not current:
            current = paragraph
            continue
        if len(current) + len(paragraph) + 2 <= max_chars:
            current = f"{current}\n\n{paragraph}"
        else:
            windows.append(current)
            current = paragraph
    if current:
        windows.append(current)
    return windows


def chunk_document(document: KnowledgeDocument, *, max_chars: int = 1800) -> list[KnowledgeChunk]:
    chunks: list[KnowledgeChunk] = []
    declared_source_path = document.metadata.get("source_path")
    source_path = (
        str(declared_source_path)
        if declared_source_path
        else (str(document.path) if document.path else None)
    )
    for heading, section in split_markdown_sections(document.body):
        for text in _window_text(section, max_chars=max_chars):
            index = len(chunks)
            chunk_uri = f"{document.document_uri}#chunk-{index}"
            action_ids = ACTION_ID_RE.findall(text)
            action_metadata = {"action_id": action_ids[0]} if len(action_ids) == 1 else {}
            citation = {
                "source_title": document.source_title,
                "source_uri": document.source_uri,
                "source_url": document.source_url,
                "source_path": source_path,
                "heading": heading,
                "chunk_index": index,
            }
            if document.doc_type == "endpoint_validation":
                try:
                    projected_metadata = project_approved_validation_metadata(
                        document.metadata
                    )
                except ValueError:
                    raise ValueError("validation_document_metadata_invalid") from None
                if (
                    projected_metadata is None
                    or set(document.metadata) != set(VALIDATION_METADATA_FIELDS)
                    or document.metadata.get("jurisdiction") != document.jurisdiction
                    or document.metadata.get("connector") != document.connector
                    or document.metadata.get("doc_type") != document.doc_type
                    or document.metadata.get("review_status") != document.review_status
                ):
                    raise ValueError("validation_document_metadata_invalid")
                chunk_metadata = projected_metadata
            else:
                chunk_metadata = {
                    "jurisdiction": document.jurisdiction,
                    "connector": document.connector,
                    "doc_type": document.doc_type,
                    "review_status": document.review_status,
                    "effective_date": document.effective_date,
                    **action_metadata,
                }
            chunks.append(
                KnowledgeChunk(
                    document_uri=document.document_uri,
                    chunk_uri=chunk_uri,
                    chunk_index=index,
                    text=text,
                    source_title=document.source_title,
                    source_uri=document.source_uri,
                    source_url=document.source_url,
                    source_path=source_path,
                    heading=heading,
                    citation=citation,
                    metadata=chunk_metadata,
                )
            )
    return chunks
